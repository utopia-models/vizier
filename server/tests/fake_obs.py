"""A fake obs-websocket v5 server for the loop's tests and acceptance: ws on / (no auth), control on the same port:
GET /_calls -> {"calls": [requestType, ...]}, POST /_reset clears. Run: python3 fake_obs.py --port 14455"""
import argparse
from aiohttp import web


async def ws(req):
    w = web.WebSocketResponse(protocols=("obswebsocket.json",)); await w.prepare(req)
    await w.send_json({"op": 0, "d": {"obsWebSocketVersion": "5.5.0", "rpcVersion": 1}})
    async for m in w:
        j = m.json() if m.type == web.WSMsgType.TEXT else {}
        if j.get("op") == 1:
            await w.send_json({"op": 2, "d": {"negotiatedRpcVersion": 1}})
        elif j.get("op") == 6:
            d = j["d"]; req.app["calls"].append(d["requestType"])
            await w.send_json({"op": 7, "d": {"requestType": d["requestType"], "requestId": d["requestId"], "requestStatus": {"result": True, "code": 100}}})
    return w


async def calls(req):
    return web.json_response({"calls": req.app["calls"]})


async def reset(req):
    req.app["calls"].clear(); return web.json_response({"ok": True})


def make_app():
    app = web.Application(); app["calls"] = []
    app.router.add_get("/", ws); app.router.add_get("/_calls", calls); app.router.add_post("/_reset", reset)
    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=14455)
    web.run_app(make_app(), host="127.0.0.1", port=ap.parse_args().port, print=None)
