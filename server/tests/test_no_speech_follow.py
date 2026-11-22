"""Tyler 2026-11-22 7:34 AM: "It shouldn't be listening to me and doing the arrows for me. I will talk and I will press Down."
The prompter position (GET /state current_line_index) moves ONLY on the reader's own key reports (POST /prompter/position, which
the app sends from its Up / Down / Left / Right / Home / wheel handlers). Speech events, me and them, whose text equals the
current and the later script lines never move it, over every transcript path the server has."""
import asyncio, importlib, os, sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
LINES = ["Hello there.", "Second line.", "Third line.", "Fourth line."]


@pytest.fixture
def vz(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("VIZIER_"):
            monkeypatch.delenv(k)
    (tmp_path / "speaker-script.md").write_text("# T\n\n## 1 · open\n" + "\n".join(LINES) + "\n")
    monkeypatch.setenv("VIZIER_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("VIZIER_SPEAKER_SCRIPT", str(tmp_path / "speaker-script.md"))
    import vizier
    return importlib.reload(vizier)


def run(v, fn):
    async def go():
        async with TestClient(TestServer(v.make_app())) as c:
            return await fn(c)
    return asyncio.run(go())


def key(line):   # what the app reports after one of the reader's key presses: the 1-based line now current
    return {"part": 1, "parts": 1, "title": "speaker script", "line": line, "lines": len(LINES), "text": LINES[line - 1]}


async def cur(c):
    return (await (await c.get("/state")).json())["current_line_index"]


def test_speech_events_never_move_the_prompter(vz):
    async def f(c):
        await c.post("/prompter/position", json=key(2))
        before = await (await c.get("/state")).json()
        await c.post("/call/start", json={"id": "t1", "name": "take", "mode": "call"})
        for text in LINES + [" ".join(LINES), LINES[3].lower(), LINES[1]]:   # the current line, later lines, all of them, again
            for ch in ("me", "them"):
                await c.post("/lines", json={"ch": ch, "text": text, "final": False})   # an interim
                await c.post("/lines", json={"ch": ch, "text": text, "final": True})
        await c.post("/lines", json={"lines": [{"ch": "me", "text": t, "final": True} for t in LINES]})
        for ch in ("me", "agent"):
            await c.post("/talk/transcript", json={"rows": [{"ch": ch, "text": t, "final": True} for t in LINES]})
        await c.post("/talk/log", json={"rows": [{"ch": "me", "text": t} for t in LINES]})
        after = await (await c.get("/state")).json()
        return before, after
    before, after = run(vz, f)
    assert before["current_line_index"] == 1
    assert after["current_line_index"] == 1 and after["current_line_text"] == LINES[1]
    assert after["seq"] == before["seq"], "a speech event published a cursor move"


def test_speech_never_sets_a_position_when_there_is_none(vz):
    async def f(c):
        for t in LINES:
            await c.post("/lines", json={"ch": "me", "text": t, "final": True})
        return await cur(c)
    assert run(vz, f) is None


def test_down_and_up_key_reports_still_move_it(vz):
    async def f(c):
        out = [await cur(c)]
        for line in (1, 2, 3, 2, 1):   # Down, Down, Down, Up, Up
            await c.post("/prompter/position", json=key(line))
            out.append(await cur(c))
        return out
    assert run(vz, f) == [None, 0, 1, 2, 1, 0]


def test_no_follow_surface_is_left():
    """The server has no follow switch and the page has no recognizer, tracker or follow toggle."""
    srv = (ROOT / "server" / "vizier.py").read_text()
    html = (ROOT / "app" / "ui" / "index.html").read_text()
    main = (ROOT / "app" / "src" / "main.rs").read_text()
    assert "prompter/follow" not in srv and "FOLLOW" not in srv
    for needle in ("vosk", "follow.js", "VoiceFollow", "vizierFollow", "prompter/follow", "kfollow", "byHand"):
        assert needle not in html, needle
    assert "KeyF" not in main and "alt+f" not in main
    assert not (ROOT / "app" / "ui" / "follow.js").exists()


def test_every_prompter_move_in_the_page_comes_from_a_key_wheel_or_the_take_reset():
    """goLine / goTake are called only from key handlers, the wheel, a part switch, a script rebuild and the /prompter/goto reset."""
    import re
    html = (ROOT / "app" / "ui" / "index.html").read_text()
    for m in re.finditer(r"\b(goLine|goTake)\(", html):
        line = html[html.rfind("\n", 0, m.start()) + 1: html.index("\n", m.start())]
        assert not re.search(r"transcript|interim|\.final|heard|\bspeech\b|\bsaid\b", line, re.I), line
