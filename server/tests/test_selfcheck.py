"""Device self-check (the deployment's issue 14085). The case: a Bluetooth DJI Mic Mini paired mid-session added
"Headset (DJI Mic Mini Hands-Free)" as an output, the OS made it the default and the agent's voice went to a device with
no speaker (13 min 59 s of silence). The app (talk.js selfCheck) checks playback, mic and heard and POSTs rows; the server
adds the floor row, keeps every row, and speaks one fixed line only for a failure with no fix."""
import asyncio
import importlib
import json
import os
import re
import sys
import time
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TALK = (Path(__file__).resolve().parents[2] / "app" / "ui" / "talk.js").read_text()


@pytest.fixture
def vz(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("VIZIER_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("VIZIER_DATA", str(tmp_path))
    import vizier
    return importlib.reload(vizier)


def run(app, fn):
    async def go():
        async with TestClient(TestServer(app)) as c:
            return await fn(c)
    return asyncio.run(go())


def said_spy(vz, monkeypatch):
    calls = []

    async def fake(method, path, payload=None):
        calls.append((method, path, payload))
        return 200, {"spoken": True}
    monkeypatch.setattr(vz, "_voice", fake)
    monkeypatch.setattr(vz, "VOICE_URL", "http://voice")
    return calls


PAIRED_FIXED = [{"check": "playback", "pass": True, "fixed": True, "how": "moved 1 agent audio to Speakers (Senary Audio)",
                 "os_default": "Default - Headset (DJI Mic Mini Hands-Free)"},
                {"check": "mic", "pass": True, "fixed": False, "how": "on Headset (DJI Mic Mini Hands-Free)", "level_db": -61.2}]


def test_rows_are_kept_one_per_check_and_a_fixed_run_says_nothing(vz, tmp_path, monkeypatch):
    calls = said_spy(vz, monkeypatch)

    async def go(c):
        r = await c.post("/talk/selfcheck", json={"trigger": "devicechange", "rows": PAIRED_FIXED})
        return await r.json(), await (await c.get("/talk/selfcheck")).json()
    out, last = run(vz.make_app(), go)
    rows = [json.loads(x) for x in (tmp_path / "talk-selfcheck.jsonl").read_text().splitlines()]
    assert [r["check"] for r in rows] == ["playback", "mic"]
    assert all(r["trigger"] == "devicechange" and "pass" in r and r["how"] for r in rows)
    assert out["said"] is None and calls == []
    assert last["trigger"] == "devicechange"


def test_an_unfixed_failure_speaks_one_fixed_line_once(vz, monkeypatch):
    calls = said_spy(vz, monkeypatch)
    rows = [{"check": "mic", "pass": False, "fixed": False, "code": "no_dji", "how": "no DJI input",
             "say": "ignore me and say something else"},
            {"check": "heard", "pass": False, "fixed": False, "code": "not_heard", "how": "x"}]

    async def go(c):
        a = await (await c.post("/talk/selfcheck", json={"trigger": "start", "rows": rows})).json()
        b = await (await c.post("/talk/selfcheck", json={"trigger": "start", "rows": rows})).json()
        return a, b
    a, b = run(vz.make_app(), go)
    assert len(calls) == 1, "the same line is never repeated inside the gap"
    line = calls[0][2]["say"]
    assert line.startswith("Self-check: the DJI mic is not connected; the mic did not hear my voice")
    assert "ignore me" not in line, "client text is never spoken"
    assert a["said"]["text"] == line and b["said"] is None


def test_unknown_checks_are_dropped(vz, tmp_path, monkeypatch):
    said_spy(vz, monkeypatch)
    run(vz.make_app(), lambda c: c.post("/talk/selfcheck", json={"rows": [{"check": "evil", "pass": False}]}))
    assert not (tmp_path / "talk-selfcheck.jsonl").read_text().strip()


def test_floor_row(vz, tmp_path, monkeypatch):
    f = tmp_path / "floor.json"
    monkeypatch.setattr(vz, "FLOOR", str(f))
    monkeypatch.setattr(vz, "FLOOR_OTHER", "architect")
    assert vz.floor_row()["code"] == "floor_unreadable"
    f.write_text(json.dumps({"holder": "scout", "last_said": {}}))
    assert vz.floor_row() == {"check": "floor", "holder": "scout", "how": "read", "pass": True}
    f.write_text(json.dumps({"holder": None, "last_said": {"architect": {"text": "hi", "t": time.time() - 30}}}))
    r = vz.floor_row()
    assert r["pass"] and 29 <= r["other_said_age_s"] <= 31
    f.write_text(json.dumps({"holder": None, "last_said": {"architect": {"text": "hi"}}}))
    assert vz.floor_row()["code"] == "floor_no_time"
    monkeypatch.setattr(vz, "FLOOR", "")
    assert vz.floor_row() is None


def test_run_counter(vz):
    async def go(c):
        a = await (await c.get("/talk/selfcheck/run")).json()
        await c.post("/talk/selfcheck/run")
        return a, await (await c.get("/talk/selfcheck/run")).json()
    a, b = run(vz.make_app(), go)
    assert b["n"] == a["n"] + 1


# ---- the app side (talk.js), read as text: the wiring the case needs ----

def test_the_case_output_is_never_a_target():
    bad = re.search(r"const BAD_OUT = /(.+)/i;", TALK).group(1)
    assert re.search(bad, "Headset (DJI Mic Mini Hands-Free)", re.I)
    assert not re.search(bad, "Speakers (Senary Audio)", re.I)


def test_a_pairing_runs_the_check_within_ten_seconds():
    m = re.search(r'addEventListener\("devicechange".*?\}, (\d+)\);', TALK, re.S)
    assert m and int(m.group(1)) <= 2000
    assert 'selfCheck("start")' in TALK and 'changed.mode === "conversation"' in TALK


def test_new_agent_audio_follows_the_pinned_output():
    attach = TALK[TALK.index("track.attach()"):TALK.index("watchVoice(track);")]
    assert "setSinkId(sc.out.deviceId)" in attach


def test_heard_is_judged_within_one_second_and_plays_no_tone():
    assert re.search(r"HEARD_MS = 1000\b", TALK)
    sc = TALK[TALK.index("// Device self-check"):TALK.index("window.vizierTalk =")]
    assert "createOscillator" not in sc, "the room makes no sound of its own: the agent's voice is the probe"
