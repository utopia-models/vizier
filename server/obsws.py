"""One obs-websocket v5 request from the Vizier server (the take's start/stop; nothing else talks to OBS from here).

`request(url, type, password)` opens the socket, identifies (with the auth challenge when OBS asks), sends ONE request,
returns its response data and closes. A refused / unreachable OBS raises ObsError: a take that did not start says so.
"""
import base64
import hashlib
import uuid

import aiohttp


class ObsError(Exception):
    pass


def _auth(password: str, salt: str, challenge: str) -> str:
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


async def request(url: str, req_type: str, password: str = "", timeout: float = 5.0) -> dict:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
            async with s.ws_connect(url, protocols=("obswebsocket.json",)) as ws:
                hello = await ws.receive_json()
                ident = {"rpcVersion": 1}
                a = (hello.get("d") or {}).get("authentication")
                if a:
                    if not password:
                        raise ObsError("OBS asks for a password and none is set (VIZIER_OBS_PASSWORD)")
                    ident["authentication"] = _auth(password, a["salt"], a["challenge"])
                await ws.send_json({"op": 1, "d": ident})
                ok = await ws.receive_json()
                if ok.get("op") != 2:
                    raise ObsError(f"OBS refused identify: {ok}")
                rid = uuid.uuid4().hex
                await ws.send_json({"op": 6, "d": {"requestType": req_type, "requestId": rid}})
                while True:
                    m = await ws.receive_json()
                    if m.get("op") == 7 and (m.get("d") or {}).get("requestId") == rid:
                        st = m["d"].get("requestStatus") or {}
                        if not st.get("result"):
                            raise ObsError(f"OBS {req_type}: {st.get('code')} {st.get('comment', '')}")
                        return m["d"].get("responseData") or {}
    except ObsError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ObsError(f"OBS unreachable at {url}: {type(e).__name__}: {e}") from e
