"""Unit tests for the Vizier server: config loading, the display API (lines, transcript, bullets, script, feed),
the prompter endpoints, bearer auth, CORS."""
import asyncio
import importlib
import os
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


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


def test_load_config_sets_without_overriding(vz, tmp_path, monkeypatch):
    f = tmp_path / "vizier.env"
    f.write_text("# comment\n\nVIZIER_A=1\nexport VIZIER_B='two words'\nVIZIER_C=\"q\"\nVIZIER_KEEP=file\nnot a pair\nVIZIER_D=~/x\nVIZIER_E=$VIZIER_A/y\n")
    monkeypatch.setenv("VIZIER_KEEP", "env")
    assert vz.load_config(f) == f
    assert os.environ["VIZIER_A"] == "1"
    assert os.environ["VIZIER_B"] == "two words"
    assert os.environ["VIZIER_C"] == "q"
    assert os.environ["VIZIER_KEEP"] == "env"
    assert os.environ["VIZIER_D"] == os.path.expanduser("~/x")
    assert os.environ["VIZIER_E"] == "1/y"
    assert vz.load_config(tmp_path / "missing.env") is None
    assert vz.load_config(None) is None


def test_defaults_live_under_data_dir(vz, tmp_path):
    assert vz.LOG == tmp_path / "events.jsonl"
    assert vz.SCRIPTS == tmp_path / "scripts.md"
    assert vz.TAKES == tmp_path / "takes"
    assert vz.BRIEFS == tmp_path / "briefs"


def test_health(vz):
    async def f(c):
        r = await c.get("/health")
        return r.status, await r.json()
    status, j = run(vz.make_app(), f)
    assert status == 200 and j["ok"] is True and j["call"] is None


def test_prompter_scripts_keeps_numbered_parts(vz, tmp_path):
    (tmp_path / "scripts.md").write_text("# T\n\n## 1 · Hello\nline one\n**line two**\n\n## Do not say\nsecret\n\n## 2 · Bye\nend\n")

    async def f(c):
        return await (await c.get("/prompter/scripts")).json()
    takes = run(vz.make_app(), f)
    assert [t["title"] for t in takes] == ["1 · Hello", "2 · Bye"]
    assert takes[0]["lines"] == ["line one", "line two"]


def test_prompter_scripts_rejects_bad_names(vz):
    async def f(c):
        return (await c.get("/prompter/scripts", params={"file": "../etc"})).status, \
               (await c.get("/prompter/scripts", params={"brief": "a/b"})).status
    assert run(vz.make_app(), f) == (400, 400)


def test_prompter_context_reads_sibling(vz, tmp_path, monkeypatch):
    s = tmp_path / "intro-script.md"
    s.write_text("# Intro\n## 1 · a\nx\n## Notes\nkeep calm\n")
    (tmp_path / "intro-context.md").write_text("background")
    monkeypatch.setattr(vz, "SCRIPTS", s)

    async def f(c):
        return await (await c.get("/prompter/context")).json()
    j = run(vz.make_app(), f)
    assert j["title"] == "Intro" and j["context"] == "background" and j["notes"][0]["title"] == "Notes"


def test_cors_preflight_for_app_origin(vz):
    async def f(c):
        r = await c.options("/call/start", headers={"Origin": "https://tauri.localhost"})
        r2 = await c.get("/health", headers={"Origin": "https://evil.example"})
        return r.status, r.headers.get("Access-Control-Allow-Origin"), r2.headers.get("Access-Control-Allow-Origin")
    assert run(vz.make_app(), f) == (204, "https://tauri.localhost", None)


def test_buildlog_missing_file_is_empty(vz):
    async def f(c):
        return await (await c.get("/buildlog")).json()
    j = run(vz.make_app(), f)
    assert j["lines"] == [] and "error" in j


def test_lines_start_a_call_and_land_in_the_transcript(vz):
    async def f(c):
        r = await c.post("/lines", json={"ch": "them", "text": "What is your rate?"})
        j = await r.json()
        await c.post("/lines", json={"lines": [{"ch": "me", "text": "interim", "final": False},
                                               {"ch": "me", "text": "Ninety an hour.", "t_audio_end": 1.0}]})
        t = await (await c.get("/transcript")).json()
        txt = await (await c.get("/transcript", params={"format": "text", "since": "1"})).text()
        h = await (await c.get("/health")).json()
        return r.status, j, t, txt, h
    status, j, t, txt, h = run(vz.make_app(), f)
    assert status == 200 and j["call_id"].startswith("call-") and j["seq"] == [1]
    assert [(l["seq"], l["ch"], l["text"]) for l in t["lines"]] == [(1, "them", "What is your rate?"), (2, "me", "Ninety an hour.")]
    assert txt == "ME: Ninety an hour.\n"
    assert h["call"] == j["call_id"] and h["lines"] == 2


def test_lines_reject_a_bad_channel(vz):
    async def f(c):
        return (await c.post("/lines", json={"ch": "both", "text": "x"})).status, \
               (await c.post("/lines", data="not json")).status
    assert run(vz.make_app(), f) == (400, 400)


def test_feed_carries_lines_bullets_status_and_script(vz):
    async def f(c):
        ws = await c.ws_connect("/feed")
        first = await ws.receive_json()
        await c.post("/call/start", json={"call_id": "demo"})
        await c.post("/lines", json={"ch": "them", "text": "Tell me about Rust."})
        await c.post("/bullets", json={"bullets": ["Two years of Rust in production", "Ask what they build"], "for": "Rust"})
        await c.post("/status", json={"text": "thinking"})
        await c.post("/script", json={"markdown": "# S\n## 1 · Hook\nhello there\n## 2 · End\nbye\n"})
        frames = [await ws.receive_json() for _ in range(7)]
        takes = await (await c.get("/prompter/scripts")).json()
        end = await (await c.post("/call/end", json={})).json()
        await ws.close()
        return first, frames, takes, end
    first, frames, takes, end = run(vz.make_app(), f)
    assert first["type"] == "status" and first["text"] == "no call"
    assert [x["type"] for x in frames] == ["status", "line", "suggestion", "delta", "done", "status", "script"]
    assert frames[0]["text"] == "call demo live"
    assert frames[1]["ch"] == "them" and frames[1]["text"] == "Tell me about Rust."
    assert frames[2]["type"] == "suggestion" and frames[2]["sid"] == "demo-b1"
    assert frames[3]["text"] == "- Two years of Rust in production\n- Ask what they build"
    assert frames[4]["type"] == "done"
    assert frames[5] == {**frames[5], "type": "status", "text": "thinking"}
    assert frames[6]["type"] == "script" and frames[6]["takes"] == 2
    assert [k["title"] for k in takes] == ["1 · Hook", "2 · End"]
    assert end["call_id"] == "demo" and end["lines"] == 1


def test_bullets_accept_markdown_text(vz):
    async def f(c):
        r = await c.post("/bullets", json={"bullets": "- one\n- two\n"})
        bad = await c.post("/bullets", json={"bullets": []})
        return r.status, bad.status
    assert run(vz.make_app(), f) == (200, 400)


def test_token_guards_every_post(vz, monkeypatch):
    monkeypatch.setattr(vz, "TOKEN", "s3")
    async def f(c):
        no = (await c.post("/lines", json={"ch": "me", "text": "x"})).status
        yes = (await c.post("/lines", json={"ch": "me", "text": "x"}, headers={"Authorization": "Bearer s3"})).status
        read = (await c.get("/transcript")).status
        return no, yes, read
    assert run(vz.make_app(), f) == (401, 200, 200)


def test_record_is_off_without_a_camera(vz):
    async def f(c):
        return (await c.post("/prompter/record", json={"take": "A"})).status
    assert run(vz.make_app(), f) == 409


def test_note_round_trip(vz):
    async def f(c):
        await c.post("/prompter/note", json={"text": "slower on the hook", "take": "A-1"})
        return await (await c.get("/prompter/note")).json()
    j = run(vz.make_app(), f)
    assert j["text"] == "slower on the hook" and j["take"] == "A-1" and j["recording"] is None


def test_openapi_is_served(vz):
    async def f(c):
        r = await c.get("/openapi.yaml")
        return r.status, await r.text()
    status, text = run(vz.make_app(), f)
    assert status == 200 and text.startswith("openapi: 3.1")


def test_errors_carry_cors_for_the_app(vz, monkeypatch):
    monkeypatch.setattr(vz, "TOKEN", "s3")
    async def f(c):
        r = await c.post("/lines", json={"ch": "me", "text": "x"}, headers={"Origin": "https://tauri.localhost"})
        return r.status, r.headers.get("Access-Control-Allow-Origin")
    assert run(vz.make_app(), f) == (401, "https://tauri.localhost")


def test_script_path_stays_inside_the_script_folders(vz, tmp_path):
    (tmp_path / "talk.md").write_text("## 1 · A\nhello\n")
    async def f(c):
        ok = await c.post("/script", json={"path": str(tmp_path / "talk.md")})
        out = await c.post("/script", json={"path": "/etc/passwd"})
        md_out = await c.post("/script", json={"path": str(tmp_path.parent / "x.md")})
        return ok.status, out.status, md_out.status
    assert run(vz.make_app(), f) == (200, 403, 403)
