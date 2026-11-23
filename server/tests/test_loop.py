"""issue 17374: Vizier owns the loop. Keys, clips, one state, and OBS start/stop as one request each (fake obs-websocket)."""
import asyncio, importlib, os, sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestServer, TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_obs


@pytest.fixture
def vz(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("VIZIER_"):
            monkeypatch.delenv(k)
    (tmp_path / "speaker-script.md").write_text("# T\n\n## 1 · open\n" + "\n".join(f"l{i}" for i in range(10)) + "\n")
    monkeypatch.setenv("VIZIER_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("VIZIER_SPEAKER_SCRIPT", str(tmp_path / "speaker-script.md"))
    monkeypatch.setenv("VIZIER_CLIPS", str(tmp_path / "clips"))
    monkeypatch.setenv("VIZIER_CLIP_SLUG", "t")
    d = tmp_path / "clips" / "t"; d.mkdir(parents=True)
    for i in range(10):
        (d / f"row-{i}.webm").write_bytes(b"x")
    import player, vizier
    importlib.reload(player)
    return importlib.reload(vizier), tmp_path


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_keys_clips_state_and_refusal(vz):
    v, tmp = vz
    async def go():
        async with TestClient(TestServer(v.make_app())) as c:
            async def key(k): return await (await c.post("/prompter/key", json={"key": k})).json()
            async def trace():
                await key("AltT"); out = []
                for k in ("Down", "Down", "Down", "Up", "Down", "AltT", "Down", "Down"):
                    r = await key(k); out.append((r["row"], r["clip"]))
                return out
            t1 = await trace()
            assert [r for r, _ in t1] == [1, 2, 3, 2, 3, 0, 1, 2]
            assert await trace() == t1                      # rehearsal pass == recording pass
            st = await (await c.get("/state")).json()
            assert st["state"] == "staged" and st["row"] == 2 and st["current_line_index"] == 2
            # no clip anywhere: Down must still advance (brute-force reading, no clip-or-refuse)
            import shutil; shutil.rmtree(tmp / "clips")
            await key("AltT")
            for n in range(1, 10):
                r = await key("Down")
                assert r["row"] == n and r["refused"] is None
            assert (await c.post("/prompter/key", json={"key": "Left"})).status == 400
    run(go())


def test_take_is_one_obs_call_each(vz):
    v, _ = vz
    async def go():
        obs = TestServer(fake_obs.make_app()); await obs.start_server()
        v.OBS_URL = f"ws://127.0.0.1:{obs.port}/"
        try:
            async with TestClient(TestServer(v.make_app())) as c:
                assert (await c.post("/take/start", json={})).status == 200
                assert (await c.post("/take/stop", json={})).status == 200
                assert obs.app["calls"] == ["StartRecord", "StopRecord"]
                assert (await (await c.get("/state")).json())["state"] == "staged"
        finally:
            await obs.close()
        v.OBS_URL = "ws://127.0.0.1:1/"
        async with TestClient(TestServer(v.make_app())) as c:
            assert (await c.post("/take/start", json={})).status == 502   # OBS down says so
    run(go())
