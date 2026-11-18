"""Replay test (the deployment's issue 15077, 2026-11-17): a restart of the display server took 29 s and 32 s, because
aiohttp's graceful shutdown waited on every open /feed WebSocket (the app holds one per window, and its handler never ends
on its own). The laptop's tabs showed "failed to fetch" for the whole gap, mid-take. SIGTERM with two feed clients
attached must exit in well under the unit's 3 s stop timeout, and the clients must be told to go (1001), not cut."""
import asyncio
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import aiohttp

VIZIER = Path(__file__).resolve().parents[1] / "vizier.py"


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_sigterm_with_open_feeds_exits_fast(tmp_path):
    port = free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("VIZIER_")}
    env["VIZIER_DATA"] = str(tmp_path)
    p = subprocess.Popen([sys.executable, str(VIZIER), "--host", "127.0.0.1", "--port", str(port)], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    async def go():
        async with aiohttp.ClientSession() as s:
            for _ in range(100):
                try:
                    async with s.get(f"http://127.0.0.1:{port}/health") as r:
                        if r.status == 200:
                            break
                except aiohttp.ClientError:
                    await asyncio.sleep(0.1)
            wss = [await s.ws_connect(f"http://127.0.0.1:{port}/feed") for _ in range(2)]
            for ws in wss:
                await ws.receive_json(timeout=5)   # the hello status frame
            t0 = time.monotonic()
            p.send_signal(signal.SIGTERM)
            codes = []
            for ws in wss:
                await ws.receive(timeout=5)
                codes.append(ws.close_code)
            while p.poll() is None and time.monotonic() - t0 < 10:
                await asyncio.sleep(0.05)
            return time.monotonic() - t0, codes

    try:
        dt, codes = asyncio.run(go())
    finally:
        if p.poll() is None:
            p.kill()
    assert p.returncode == 0, p.returncode
    assert dt < 2.0, f"stop took {dt:.1f} s"
    assert codes == [1001, 1001], codes


def test_beacon_shows_in_health_with_its_age(tmp_path, monkeypatch):
    """studiod on the recording machine posts its state every 2 s; /health carries it as `studio` with `age_s`."""
    import importlib
    from aiohttp.test_utils import TestClient, TestServer
    for k in list(os.environ):
        if k.startswith("VIZIER_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("VIZIER_DATA", str(tmp_path))
    sys.path.insert(0, str(VIZIER.parent))
    import vizier
    vz = importlib.reload(vizier)

    async def go():
        async with TestClient(TestServer(vz.make_app())) as c:
            assert (await (await c.get("/health")).json())["studio"] is None
            r = await c.post("/beacon", json={"recording": True, "take_id": 3, "name": "demo", "beat": 2, "obs": {"up": True}})
            assert r.status == 200
            return (await (await c.get("/health")).json())["studio"]
    s = asyncio.run(go())
    assert s["recording"] is True and s["take_id"] == 3 and s["obs_up"] is True and 0 <= s["age_s"] < 2, s
