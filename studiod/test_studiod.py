"""studiod against a fake obs-websocket v5 server (the deployment's issue 15077).

Run: STUDIOD=target/release/studiod python -m pytest -q test_studiod.py   (needs aiohttp + pytest)
Checks: SHA-256 auth from OBS's own config.json, `rec start` answers after RecordStateChanged (and how fast), an `id`
replays instead of starting a second take, a stream request is never sent and a start is refused while streaming, the
state file is written, and the held connection comes back by itself after OBS restarts."""
import asyncio
import base64
import hashlib
import json
import os
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

import pytest
from aiohttp import web

BIN = os.environ.get("STUDIOD", str(Path(__file__).parent / "target/release/studiod"))
PW = "fake-obs-pw"


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class FakeOBS:
    def __init__(self, port):
        self.port, self.requests, self.streaming, self.recording, self.salt, self.challenge = port, [], False, False, "s4lt", "ch4l"
        self.loop = asyncio.new_event_loop(); self.runner = None; self.conns = set()
        self.items, self.enabled = {("VSL", "Face"): 7, ("VSL", "Orb"): 9}, {7: True, 9: False}   # the hand-off sources
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

    def expected_auth(self):
        sec = base64.b64encode(hashlib.sha256((PW + self.salt).encode()).digest()).decode()
        return base64.b64encode(hashlib.sha256((sec + self.challenge).encode()).digest()).decode()

    async def ws(self, req):
        ws = web.WebSocketResponse(protocols=("obswebsocket.json",)); await ws.prepare(req); self.conns.add(ws)
        await ws.send_json({"op": 0, "d": {"obsWebSocketVersion": "5.5.6", "rpcVersion": 1,
                                           "authentication": {"salt": self.salt, "challenge": self.challenge}}})
        ident = json.loads((await ws.receive()).data)
        if ident["d"].get("authentication") != self.expected_auth():
            await ws.close(code=4009); return ws
        assert ident["d"]["eventSubscriptions"] == 64
        await ws.send_json({"op": 2, "d": {"negotiatedRpcVersion": 1}})
        async for m in ws:
            d = json.loads(m.data)["d"]; t = d["requestType"]; self.requests.append(t)
            resp = {"requestType": t, "requestId": d["requestId"], "requestStatus": {"result": True, "code": 100}}
            if t == "GetStreamStatus": resp["responseData"] = {"outputActive": self.streaming}
            elif t == "GetRecordStatus": resp["responseData"] = {"outputActive": self.recording}
            elif t == "StopRecord": resp["responseData"] = {"outputPath": "C:/VSL/fake.mkv"}
            elif t == "GetCurrentProgramScene": resp["responseData"] = {"currentProgramSceneName": "VSL", "sceneName": "VSL"}
            elif t == "GetSceneItemId":
                rd = d.get("requestData", {}); iid = self.items.get((rd.get("sceneName"), rd.get("sourceName")))
                if iid is None: resp["requestStatus"] = {"result": False, "code": 600, "comment": "No scene items were found"}
                else: resp["responseData"] = {"sceneItemId": iid}
            elif t == "SetSceneItemEnabled":
                rd = d.get("requestData", {}); self.enabled[rd["sceneItemId"]] = rd["sceneItemEnabled"]
            await ws.send_json({"op": 7, "d": resp})
            if t in ("StartRecord", "StopRecord"):
                await asyncio.sleep(0.03)   # OBS emits STARTING/STOPPING then STARTED/STOPPED
                on = t == "StartRecord"; self.recording = on
                for st in (("OBS_WEBSOCKET_OUTPUT_STARTING", "OBS_WEBSOCKET_OUTPUT_STARTED") if on else ("OBS_WEBSOCKET_OUTPUT_STOPPING", "OBS_WEBSOCKET_OUTPUT_STOPPED")):
                    data = {"outputActive": on and st.endswith("STARTED"), "outputState": st}
                    if st.endswith("STOPPED"): data["outputPath"] = "C:/VSL/fake.mkv"
                    await ws.send_json({"op": 5, "d": {"eventType": "RecordStateChanged", "eventIntent": 64, "eventData": data}})
        self.conns.discard(ws)
        return ws

    def start(self):
        async def go():
            app = web.Application(); app.router.add_get("/", self.ws)
            self.runner = web.AppRunner(app); await self.runner.setup()
            await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        asyncio.run_coroutine_threadsafe(go(), self.loop).result(5)

    def stop(self):
        async def go():
            for w in list(self.conns): await w.close()
            await self.runner.cleanup()
        asyncio.run_coroutine_threadsafe(go(), self.loop).result(5)


def call(port, verb=None, **kw):
    if verb is None:
        return json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=10))
    body = json.dumps({"verb": verb, **kw}).encode()
    try:
        return json.load(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/cmd", body, method="POST"), timeout=10))
    except urllib.error.HTTPError as e:
        return {"http": e.code, **json.load(e)}


def wait(pred, s=8):
    t = time.time()
    while time.time() - t < s:
        if pred(): return True
        time.sleep(0.05)
    return False


@pytest.fixture
def rig(tmp_path):
    obs_port, port = free_port(), free_port()
    cfg = tmp_path / "config.json"; cfg.write_text(json.dumps({"server_password": PW, "server_port": obs_port}))
    obs = FakeOBS(obs_port); obs.start()
    state = tmp_path / "state.json"
    p = subprocess.Popen([BIN, "--port", str(port), "--obs", f"ws://127.0.0.1:{obs_port}", "--obs-config", str(cfg),
                          "--state", str(state)], stderr=subprocess.PIPE, env={k: v for k, v in os.environ.items() if k != "STUDIOD_OBS_PASSWORD"})
    assert wait(lambda: _up(port) and call(port)["obs"]["up"]), "studiod never connected to the fake OBS"
    yield obs, port, state, p
    p.kill(); p.wait()
    try: obs.stop()
    except Exception: pass


def _up(port):
    try: socket.create_connection(("127.0.0.1", port), 0.2).close(); return True
    except OSError: return False


def test_rec_start_stop_waits_for_record_state_changed(rig):
    obs, port, state, _ = rig
    r = call(port, "rec start", name="demo")
    assert r["ok"] and r["state"]["recording"] and r["state"]["take_id"] == 1 and r["state"]["name"] == "demo", r
    assert 25 <= r["event_ms"] < 300, r          # the fake emits STARTED 30 ms after its ack
    assert json.loads(state.read_text())["recording"] is True
    r = call(port, "rec stop")
    assert r["ok"] and not r["state"]["recording"] and r["state"]["output_path"] == "C:/VSL/fake.mkv", r
    assert json.loads(state.read_text())["recording"] is False


def test_same_id_replays_and_never_starts_two_takes(rig):
    obs, port, _, _ = rig
    a = call(port, "rec start", id="voice-1"); b = call(port, "rec start", id="voice-1")
    assert a["ok"] and b.get("replayed") and obs.requests.count("StartRecord") == 1
    c = call(port, "rec start")    # no id, already recording: answered, nothing sent
    assert c["already"] and obs.requests.count("StartRecord") == 1


def test_refused_while_streaming_and_never_sends_a_stream_request(rig):
    obs, port, _, _ = rig
    obs.streaming = True
    r = call(port, "rec start")
    assert r["http"] == 409 and "streaming" in r["error"] and "StartRecord" not in obs.requests
    assert not any("Stream" in t and t != "GetStreamStatus" for t in obs.requests)


def test_reconnects_by_itself_after_obs_restarts(rig):
    obs, port, _, _ = rig
    obs.stop()
    assert wait(lambda: not call(port)["obs"]["up"], 5)
    obs.start()
    t = time.time()
    assert wait(lambda: call(port)["obs"]["up"], 5), "no reconnect"
    assert time.time() - t < 3.0            # Full Jitter capped at 2 s
    assert call(port, "rec start")["ok"]


def test_cue_verbs_and_unknown_verb(rig):
    _, port, state, _ = rig
    assert call(port, "cue next")["state"]["beat"] == 1
    assert call(port, "cue goto 5")["state"]["beat"] == 5
    assert call(port, "cue back")["state"]["beat"] == 4
    assert call(port, "dance")["http"] == 400
    assert json.loads(state.read_text())["beat"] == 4


def test_face_orb_swaps_the_face_card_for_the_orb_and_back(rig):
    """The hand-off (Down into [AI voice]): face card hidden, orb shown, in the program scene; "face cam" reverses it."""
    obs, port, state, _ = rig
    r = call(port, "face orb")
    assert r["ok"] and r["scene"] == "VSL" and obs.enabled == {7: False, 9: True}, r
    assert r["state"]["face"] == "orb" and json.loads(state.read_text())["face"] == "orb"
    assert "StartStream" not in obs.requests
    r = call(port, "face cam")
    assert r["ok"] and obs.enabled == {7: True, 9: False} and r["state"]["face"] == "cam", r


def test_face_orb_reports_a_missing_source(rig):
    obs, port, _, _ = rig
    del obs.items[("VSL", "Orb")]
    r = call(port, "face orb")
    assert r.get("http") == 502 and not r["ok"] and any("Orb" in e for e in r["errors"]), r
    assert obs.enabled[7] is False          # the face card still went off: the take never shows both
