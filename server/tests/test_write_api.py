"""POST /speaker-script, POST /horus-script, GET /state: the agent writes the live files the tabs and vsl-follow read."""
import asyncio, importlib, json, os, sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
SECRET = "s3cret-for-tests"
H = {"X-Vizier-Write": SECRET, "X-Vizier-Who": "horus"}
CUES = "| id | on | do |\n|---|---|---|\n| C0 | hello | Right |\n| C1 | world | Left |\n"


@pytest.fixture
def vz(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("VIZIER_"):
            monkeypatch.delenv(k)
    (tmp_path / "secret").write_text(SECRET + "\n")
    (tmp_path / "speaker-script.md").write_text("# T\n\n## 1 · open\nHello there.\n")
    (tmp_path / "horus-script.md").write_text(CUES)
    (tmp_path / "state.json").write_text('{"beat": "b1", "at": 5}')
    monkeypatch.setenv("VIZIER_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("VIZIER_WRITE_SECRET_FILE", str(tmp_path / "secret"))
    monkeypatch.setenv("VIZIER_SPEAKER_SCRIPT", str(tmp_path / "speaker-script.md"))
    monkeypatch.setenv("VIZIER_HORUS_CUES", f"{tmp_path/'missing.md'}{os.pathsep}{tmp_path/'horus-script.md'}")
    monkeypatch.setenv("VIZIER_HORUS_STATE", str(tmp_path / "state.json"))
    import vizier
    return importlib.reload(vizier), tmp_path


def run(v, fn):
    async def go():
        async with TestClient(TestServer(v.make_app())) as c:
            return await fn(c)
    return asyncio.run(go())


def test_write_needs_the_secret(vz):
    v, d = vz
    async def f(c):
        assert (await c.post("/speaker-script", data="x")).status == 403
        assert (await c.post("/speaker-script", data="x", headers={"X-Vizier-Write": "nope"})).status == 403
        return (await c.get("/speaker-script")).status
    assert run(v, f) == 200
    assert "Hello there" in (d / "speaker-script.md").read_text()


def test_write_is_off_without_a_secret_file(vz, monkeypatch):
    v, d = vz
    monkeypatch.setattr(v, "WRITE_SECRET_FILE", "")
    async def f(c):
        return (await c.post("/speaker-script", data="x", headers=H)).status
    assert run(v, f) == 403


def test_post_json_and_raw_then_get_shows_it(vz):
    v, d = vz
    async def f(c):
        r = await c.post("/speaker-script", json={"text": "# New\n\n## 1 · a\nFirst line.\nSecond line."}, headers=H)
        j = await r.json()
        assert r.status == 200 and j["ok"] and j["bytes"] == len("# New\n\n## 1 · a\nFirst line.\nSecond line.\n".encode()) and len(j["sha"]) == 12 and j["at"]
        g = await (await c.get("/speaker-script")).json()
        assert g["title"] == "New" and [l["text"] for l in g["sections"][0]["lines"]] == ["First line.", "Second line."]
        r2 = await c.post("/speaker-script", data="# Raw\n\n## 1 · b\nRaw line.\n", headers={**H, "Content-Type": "text/plain"})
        assert (await r2.json())["sha"] != j["sha"]
        return (await (await c.get("/speaker-script")).json())["title"]
    assert run(v, f) == "Raw"
    assert not list(d.glob(".*.tmp")), "temp file left behind"


def test_horus_write_reaches_the_file_the_tab_reads(vz):
    v, d = vz
    new = "| id | on | do |\n|---|---|---|\n| C0 | a | Right |\n| C1 | b | Right |\n| C2 | c | Left |\n"
    async def f(c):
        assert (await c.post("/horus-script", json={"text": new}, headers=H)).status == 200
        return await (await c.get("/horus-script")).json()
    j = run(v, f)
    assert [c["id"] for c in j["cues"]] == ["C0", "C1", "C2"] and j["current"] == "b1"
    assert (d / "horus-script.md").read_text() == new
    assert not (d / "missing.md").exists()


def test_empty_and_bad_bodies_refused_file_untouched(vz):
    v, d = vz
    before = (d / "speaker-script.md").read_text()
    async def f(c):
        assert (await c.post("/speaker-script", data="  \n", headers=H)).status == 400
        assert (await c.post("/speaker-script", json={"text": 5}, headers=H)).status == 400
        assert (await c.post("/speaker-script", data=b"\xff\xfe", headers=H)).status == 400
        assert (await c.post("/speaker-script", data="x" * (513 * 1024), headers=H)).status == 413
    run(v, f)
    assert (d / "speaker-script.md").read_text() == before


def test_log_history_keeps_20_and_undo(vz):
    v, d = vz
    async def f(c):
        for i in range(25):
            assert (await c.post("/speaker-script", data=f"# v{i}\n", headers=H)).status == 200
        assert (await c.post("/speaker-script/undo", headers=H)).status == 200
    run(v, f)
    assert (d / "speaker-script.md").read_text() == "# v23\n"
    log = [json.loads(l) for l in (d / "data" / "script-writes.jsonl").read_text().splitlines()]
    assert len(log) == 25 and log[0]["who"] == "horus" and log[0]["kind"] == "speaker-script" and log[0]["sha"] and log[0]["at"]
    assert len(list((d / "data" / "script-history" / "speaker-script").glob("*.md"))) == 20


def test_undo_without_history_404(vz):
    v, d = vz
    async def f(c):
        return (await c.post("/horus-script/undo", headers=H)).status
    assert run(v, f) == 404


def test_state_is_one_json(vz):
    v, d = vz
    async def f(c):
        await c.post("/state/front", json={"tab": "tscript"})
        return await (await c.get("/state")).json()
    j = run(v, f)
    assert j["speaker_script"]["text"].startswith("# T") and j["speaker_script"]["sha"] and j["speaker_script"]["mtime"]
    assert j["speaker_script"]["sections"] == 1
    h = j["horus_script"]
    assert h["cue_count"] == 2 and h["current_beat"] == "b1" and h["current_cue"]["id"] == "C1"
    assert "text" not in h and j["front_tab"] == "tscript" and j["write_enabled"] is True
    assert "recording" in j["take"] and j["take"]["name"]


def test_state_unconfigured_is_honest(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("VIZIER_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("VIZIER_DATA", str(tmp_path))
    import vizier
    v = importlib.reload(vizier)
    async def f(c):
        return await (await c.get("/state")).json()
    j = run(v, f)
    assert j["speaker_script"]["text"] is None and j["horus_script"]["cue_count"] == 0 and j["write_enabled"] is False
