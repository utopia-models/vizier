"""The live call copilot: GET /calls/state, POST /calls/transcript | card | start | reset (write secret), kept in DATA/calls.json."""
import asyncio, importlib, json, os, sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
SECRET = "s3cret-for-tests"
H = {"X-Vizier-Write": SECRET}


def load(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("VIZIER_"):
            monkeypatch.delenv(k)
    (tmp_path / "secret").write_text(SECRET + "\n")
    monkeypatch.setenv("VIZIER_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("VIZIER_WRITE_SECRET_FILE", str(tmp_path / "secret"))
    import vizier
    return importlib.reload(vizier)


@pytest.fixture
def vz(tmp_path, monkeypatch):
    return load(tmp_path, monkeypatch), tmp_path, monkeypatch


def run(v, fn):
    async def go():
        async with TestClient(TestServer(v.make_app())) as c:
            return await fn(c)
    return asyncio.run(go())


def test_state_shape_when_empty(vz):
    v, *_ = vz
    async def f(c):
        r = await c.get("/calls/state"); return r.status, await r.json()
    st, j = run(v, f)
    assert st == 200 and j["ok"] is True and j["active"] is False and j["title"] == ""
    assert j["transcript"] == [] and j["cards"] == [] and "updated_at" in j


def test_append_then_interim_replaced_by_id(vz):
    v, *_ = vz
    async def f(c):
        a = await (await c.post("/calls/transcript", json={"speaker": "THEM", "text": "how much", "final": False}, headers=H)).json()
        b = await (await c.post("/calls/transcript", json={"speaker": "ME", "text": "hello", "final": True}, headers=H)).json()
        a2 = await (await c.post("/calls/transcript", json={"speaker": "THEM", "text": "how much does it cost", "final": True, "id": a["id"]}, headers=H)).json()
        return a, b, a2, await (await c.get("/calls/state")).json()
    a, b, a2, st = run(v, f)
    assert a["final"] is False and a["speaker"] == "THEM" and a2["id"] == a["id"] and b["id"] != a["id"]
    assert [x["text"] for x in st["transcript"]] == ["how much does it cost", "hello"]
    assert st["transcript"][0]["final"] is True and st["transcript"][1]["speaker"] == "ME"


def test_cards_append_and_caps(vz):
    v, *_ = vz
    async def f(c):
        for i in range(55):
            r = await c.post("/calls/card", json={"asked": f"q{i}", "say": "s", "next": "n"}, headers=H)
            assert r.status == 200
        for i in range(310):
            await c.post("/calls/transcript", json={"speaker": "ME", "text": f"l{i}", "final": True}, headers=H)
        return await (await c.get("/calls/state")).json()
    st = run(v, f)
    assert len(st["cards"]) == 50 and st["cards"][-1]["asked"] == "q54" and set(st["cards"][0]) == {"id", "t", "asked", "say", "next"}
    assert len(st["transcript"]) == 300 and st["transcript"][-1]["text"] == "l309"


def test_start_and_reset(vz):
    v, *_ = vz
    async def f(c):
        await c.post("/calls/transcript", json={"speaker": "ME", "text": "old", "final": True}, headers=H)
        await c.post("/calls/start", json={"title": "Demo call"}, headers=H)
        s1 = await (await c.get("/calls/state")).json()
        await c.post("/calls/card", json={"asked": "a", "say": "b", "next": "c"}, headers=H)
        await c.post("/calls/reset", headers=H)
        return s1, await (await c.get("/calls/state")).json()
    s1, s2 = run(v, f)
    assert s1["title"] == "Demo call" and s1["active"] is True and s1["transcript"] == [] and s1["cards"] == []
    assert s2["active"] is False and s2["cards"] == [] and s2["transcript"] == []


def test_persists_across_a_module_reload(vz):
    v, tmp, mp = vz
    async def f(c):
        await c.post("/calls/start", json={"title": "Kept"}, headers=H)
        await c.post("/calls/transcript", json={"speaker": "THEM", "text": "hi", "final": True}, headers=H)
        await c.post("/calls/card", json={"asked": "a", "say": "b", "next": "c"}, headers=H)
    run(v, f)
    assert json.loads((tmp / "data" / "calls.json").read_text())["title"] == "Kept"
    assert sorted(p.name for p in (tmp / "data").iterdir()) == ["calls.json"]   # nothing else is written
    v2 = importlib.reload(v)
    async def g(c):
        return await (await c.get("/calls/state")).json()
    st = run(v2, g)
    assert st["title"] == "Kept" and st["active"] is True and st["transcript"][0]["text"] == "hi" and st["cards"][0]["say"] == "b"


@pytest.mark.parametrize("path", ["/calls/transcript", "/calls/card", "/calls/start", "/calls/reset"])
def test_posts_need_the_secret_and_get_is_open(vz, path):
    v, *_ = vz
    async def f(c):
        r1 = await c.post(path, json={"text": "x", "speaker": "ME"})
        r2 = await c.post(path, json={"text": "x"}, headers={"X-Vizier-Write": "wrong"})
        r3 = await c.get("/calls/state")
        return r1.status, r2.status, r3.status, await r3.json()
    s1, s2, s3, j = run(v, f)
    assert (s1, s2, s3) == (403, 403, 200) and j["transcript"] == [] and j["cards"] == []
