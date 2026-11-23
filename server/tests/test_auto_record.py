"""Opt-in AUTO-RECORD (issue 17409): unarmed never touches OBS; armed starts OBS once on the first move off the staged row
(keys AND /prompter/position), stops on /take/stop or the auto-stop, writes row_times.json; OBS down -> banner, stepping goes on."""
import asyncio, importlib, json, os, sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_obs


@pytest.fixture
def vz(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("VIZIER_"):
            monkeypatch.delenv(k)
    (tmp_path / "clips" / "s").mkdir(parents=True)
    for i in range(4):
        (tmp_path / "clips" / "s" / f"row-{i:02d}.webm").write_bytes(b"x")
    (tmp_path / "speaker-script.md").write_text("# T\n\n## 1 · open\n" + "\n".join(f"line {i}" for i in range(4)) + "\n")
    monkeypatch.setenv("VIZIER_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("VIZIER_SPEAKER_SCRIPT", str(tmp_path / "speaker-script.md"))
    monkeypatch.setenv("VIZIER_CLIPS", str(tmp_path / "clips"))
    monkeypatch.setenv("VIZIER_CLIP_SLUG", "s")
    monkeypatch.setenv("VIZIER_AUTOSTOP_S", "0.2")
    import vizier
    return importlib.reload(vizier), tmp_path


def run(v, tmp, fn, obs_up=True):
    async def go():
        obs = TestServer(fake_obs.make_app()); await obs.start_server()
        port = obs.port if obs_up else 1
        v.OBS_URL = f"ws://127.0.0.1:{port}"
        try:
            async with TestClient(TestServer(v.make_app())) as c:
                return await fn(c, obs.app["calls"])
        finally:
            await obs.close()
    return asyncio.run(go())


def test_unarmed_never_touches_obs(vz):
    v, tmp = vz
    async def f(c, calls):
        for _ in range(3):
            await c.post("/prompter/key", json={"key": "Down"})
        await c.post("/prompter/position", json={"line": 2, "lines": 4, "text": "x"})
        assert calls == [] and (await (await c.get("/state")).json())["armed"] is False
    run(v, tmp, f)


def test_armed_keys_start_once_then_autostop_and_row_times(vz):
    v, tmp = vz
    async def f(c, calls):
        d = tmp / "take"
        assert (await (await c.post("/take/arm", json={"slug": "s", "dir": str(d)})).json())["armed"] is True
        for _ in range(5):
            await c.post("/prompter/key", json={"key": "Down"})
        assert calls == ["StartRecord"]
        await c.post("/prompter/key", json={"key": "Up"})          # leaving the last row cancels the auto-stop
        await asyncio.sleep(0.4)
        assert calls == ["StartRecord"]
        await c.post("/prompter/key", json={"key": "Down"})
        await asyncio.sleep(0.5)
        assert calls == ["StartRecord", "StopRecord"]
        assert (await (await c.get("/state")).json())["armed"] is False
        rt = json.loads((d / "row_times.json").read_text())
        assert [r["i"] for r in rt["rows"]] == [1, 2, 3]
    run(v, tmp, f)


def test_armed_position_path_starts_obs_and_up_never_stops(vz):
    v, tmp = vz
    async def f(c, calls):
        await c.post("/take/arm", json={"slug": "s"})
        await c.post("/prompter/position", json={"line": 1, "lines": 4, "text": "x"})   # the staged row: no start
        assert calls == []
        await c.post("/prompter/position", json={"line": 2, "lines": 4, "text": "x"})
        await c.post("/prompter/position", json={"line": 1, "lines": 4, "text": "x"})   # Up
        await c.post("/prompter/key", json={"key": "AltT"})
        assert calls == ["StartRecord"]
        await c.post("/take/stop", json={})
        assert calls == ["StartRecord", "StopRecord"]
    run(v, tmp, f)


def test_obs_down_sets_banner_and_keeps_stepping(vz):
    v, tmp = vz
    async def f(c, calls):
        await c.post("/take/arm", json={"slug": "s"})
        r = await (await c.post("/prompter/key", json={"key": "Down"})).json()
        assert r["row"] == 1
        st = await (await c.get("/state")).json()
        assert st["banner"]["text"] == "recording did not start"
        r = await (await c.post("/prompter/key", json={"key": "Down"})).json()
        assert r["row"] == 2
    run(v, tmp, f, obs_up=False)
