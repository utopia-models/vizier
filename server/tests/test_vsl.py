"""POST /vsl: the one switch behind Alt+R, the SETTINGS "Recording" row and the agent's "record" command. It answers
at once (the key acts in milliseconds) and runs the configured start/stop command in the background."""
import asyncio
import importlib
import os
import sys
import time
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def vz(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("VIZIER_"):
            monkeypatch.delenv(k)
    state = tmp_path / "current.json"
    monkeypatch.setenv("VIZIER_DATA", str(tmp_path))
    monkeypatch.setenv("VIZIER_VSL_STATE", str(state))
    # a slow start (0.5 s) that writes the state file, like `vsl start` once OBS records; stop removes it
    monkeypatch.setenv("VIZIER_VSL_START_CMD", f"sleep 0.5; echo {{name}} > {state}; echo RECORDING {{name}}")
    monkeypatch.setenv("VIZIER_VSL_STOP_CMD", f"sleep 0.3; rm -f {state}; echo DONE")
    import vizier
    return importlib.reload(vizier), state


def run(app, fn):
    async def go():
        async with TestClient(TestServer(app)) as c:
            return await fn(c)
    return asyncio.run(go())


def test_toggle_answers_at_once_and_the_take_follows(vz):
    v, state = vz

    async def go(c):
        t0 = time.monotonic()
        j = await (await c.post("/vsl", json={"toggle": True, "src": "hotkey"})).json()
        took = time.monotonic() - t0
        assert took < 0.2 and j["on"] is True and j["pending"] is True and j["actual"] is False
        h = await (await c.get("/health")).json()
        assert h["vsl"]["on"] is True                      # the app's 1 s health poll shows REC before OBS does
        await asyncio.sleep(0.9)
        j = await (await c.get("/vsl")).json()
        assert j["actual"] is True and j["pending"] is False and state.read_text().strip() == j["name"]
        j = await (await c.post("/vsl", json={"toggle": True})).json()
        assert j["on"] is False
        await asyncio.sleep(0.6)
        j = await (await c.get("/vsl")).json()
        assert j["actual"] is False and not state.exists()
    run(v.make_app(), go)


def test_start_during_a_stop_waits_for_it(vz):
    v, state = vz

    async def go(c):
        await c.post("/vsl", json={"on": True}); await asyncio.sleep(0.8)
        await c.post("/vsl", json={"on": False}); await asyncio.sleep(0.05)
        j = await (await c.post("/vsl", json={"on": True})).json()
        assert j["on"] is True
        await asyncio.sleep(1.2)
        j = await (await c.get("/vsl")).json()
        assert j["actual"] is True and state.exists()
    run(v.make_app(), go)


def test_unconfigured_is_409(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("VIZIER_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("VIZIER_DATA", str(tmp_path))
    import vizier
    v = importlib.reload(vizier)

    async def go(c):
        r = await c.post("/vsl", json={"toggle": True})
        assert r.status == 409
        assert (await (await c.get("/vsl")).json())["configured"] is False
    run(v.make_app(), go)
