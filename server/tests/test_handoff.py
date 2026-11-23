"""The hand-off (the user 2026-11-22): Down into [AI voice] -> POST /handoff runs VIZIER_HANDOFF_CMD once per take;
POST /handoff {"done": true} (the voice agent's last line) stops the take through the /vsl switch."""
import asyncio, importlib, os, sys, time
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def vz(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("VIZIER_"):
            monkeypatch.delenv(k)
    (tmp_path / "speaker-script.md").write_text("# T\n\n## [Host] Intro\nHi.\n\n## [AI voice] Architecture diagram\nHere it is.\n")
    monkeypatch.setenv("VIZIER_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("VIZIER_SPEAKER_SCRIPT", str(tmp_path / "speaker-script.md"))
    monkeypatch.setenv("VIZIER_HANDOFF_CMD", f"echo {{section}} {{line}} >> {tmp_path / 'ran.txt'}")
    state = tmp_path / "rec.on"
    monkeypatch.setenv("VIZIER_VSL_STATE", str(state))
    monkeypatch.setenv("VIZIER_VSL_START_CMD", f"touch {state}")
    monkeypatch.setenv("VIZIER_VSL_STOP_CMD", f"rm -f {state}")
    import vizier
    return importlib.reload(vizier), tmp_path


def run(v, fn):
    async def go():
        async with TestClient(TestServer(v.make_app())) as c:
            return await fn(c)
    return asyncio.run(go())


async def settle(cond, s=3.0):
    t = time.time()
    while time.time() - t < s and not cond():
        await asyncio.sleep(0.05)


def test_handoff_runs_the_command_once_per_take(vz):
    v, d = vz
    async def f(c):
        r0 = await (await c.get("/handoff")).json()
        a = await (await c.post("/handoff", json={"section": "[AI voice] Architecture diagram", "line": 2})).json()
        await settle(lambda: (d / "ran.txt").is_file())
        b = await (await c.post("/handoff", json={"section": "[AI voice] Architecture diagram", "line": 2})).json()
        st = await (await c.get("/state")).json()
        return r0, a, b, st
    r0, a, b, st = run(v, f)
    assert r0["configured"] and not r0["fired"]
    assert a["fired"] and a["already"] is False and a["line"] == 2
    assert b["already"] is True                    # Up/Down across the boundary never starts a second voice agent
    assert (d / "ran.txt").read_text().strip() == "[AI voice] Architecture diagram 2"   # section shell-quoted, ran once
    assert st["handoff"]["fired"] and st["handoff"]["section"].startswith("[AI voice]")


def test_a_new_take_can_hand_off_again(vz):
    v, d = vz
    async def f(c):
        await c.post("/handoff", json={"section": "s", "line": 2, "take": "t1"})
        await c.post("/handoff", json={"section": "s", "line": 2, "take": "t2"})
        await settle(lambda: (d / "ran.txt").is_file() and len((d / "ran.txt").read_text().splitlines()) >= 2)
    run(v, f)
    assert len((d / "ran.txt").read_text().splitlines()) == 2


def test_done_stops_the_take(vz):
    v, d = vz
    async def f(c):
        await c.post("/vsl", json={"on": True})
        await settle(lambda: (d / "rec.on").is_file())
        assert (d / "rec.on").is_file()
        r = await (await c.post("/handoff", json={"done": True, "src": "voice-agent"})).json()
        await settle(lambda: not (d / "rec.on").is_file())
        return r
    r = run(v, f)
    assert r["stopping"] is True and r["done_at"]
    assert not (d / "rec.on").is_file()           # the take ended by itself after the voice agent's last line


def test_unconfigured_handoff_still_records_the_moment(vz, monkeypatch):
    monkeypatch.delenv("VIZIER_HANDOFF_CMD")
    import vizier
    v = importlib.reload(vizier)
    async def f(c):
        return await (await c.post("/handoff", json={"section": "s", "line": 3})).json()
    r = run(v, f)
    assert r["configured"] is False and r["fired"] is True


def test_state_reports_the_app_build(vz):
    """The app posts its build (build.rs: git sha) with the front tab; GET /state shows it, so a detector can compare the
    exe on the laptop with main without touching the laptop."""
    v, d = vz
    async def f(c):
        s0 = await (await c.get("/state")).json()
        await c.post("/state/front", json={"tab": "tscript", "build": {"sha": "ea3cb5239bfd", "version": "0.3.0", "built_at": 1795340000}})
        return s0, await (await c.get("/state")).json()
    s0, s1 = run(v, f)
    assert s0["app_build"] is None
    assert s1["app_build"]["sha"] == "ea3cb5239bfd" and s1["app_build"]["version"] == "0.3.0" and s1["front_tab"] == "tscript"
