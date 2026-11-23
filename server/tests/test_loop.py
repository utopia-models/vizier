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
            assert [r for r, _ in t1] == [1, 2, 3, 2, 3, 0, 1, 2] and all(r == c for r, c in t1)
            assert await trace() == t1                      # rehearsal pass == recording pass
            st = await (await c.get("/state")).json()
            assert st["state"] == "staged" and st["row"] == 2 and st["current_line_index"] == 2
            starts = [r["row"] for r in (await (await c.get("/player/log?n=500")).json())["rows"] if r["event"] == "clip_start"]
            assert starts[:9] == [0, 1, 2, 3, 2, 3, 0, 1, 2]
            # dead screen impossible
            clip = tmp / "clips" / "t" / "row-5.webm"
            await key("AltT")
            for _ in range(4): await key("Down")
            clip.rename(clip.with_suffix(".hid"))
            r = await key("Down")
            assert r["row"] == 4 and "no clip for row 5" in r["refused"] and r["banner"]
            assert (await (await c.get("/state")).json())["banner"]
            clip.with_suffix(".hid").rename(clip)
            r = await key("Down")
            assert r["row"] == 5 and r["refused"] is None and r["banner"] is None
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


def test_zero_padded_clip_names_are_the_same_row(vz):
    """The renderer writes row-00.webm .. row-NN.webm; the key handler and the page ask for row-0 .. row-N: both must find the clip."""
    v, tmp = vz
    d = tmp / "clips" / "t"
    for f in list(d.iterdir()): f.unlink()
    for i in range(10): (d / f"row-{i:02d}.webm").write_bytes(b"x")
    import player
    assert all(player.clip_path("t", i) is not None for i in range(10))
    async def go():
        async with TestClient(TestServer(v.make_app())) as c:
            assert (await c.get("/clips/t/row-3.webm")).status == 200     # asked unpadded, on disk padded
            assert (await c.get("/clips/t/row-03.webm")).status == 200
            assert (await c.get("/clips/t/row-99.webm")).status == 404
            r = await (await c.post("/prompter/key", json={"key": "Down"})).json()
            assert r["row"] == 1 and not r.get("refused")
    run(go())
