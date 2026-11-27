"""The script queue: scripts are loaded ahead of time, "next" shows the next one at once, and the item on screen is what a
recorder reads when a recording starts."""
import asyncio, importlib, os, sys, time
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

W = {"X-Vizier-Write": "s3cret"}


@pytest.fixture
def vz(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("VIZIER_"):
            monkeypatch.delenv(k)
    (tmp_path / "secret").write_text("s3cret\n")
    monkeypatch.setenv("VIZIER_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("VIZIER_SPEAKER_SCRIPT", str(tmp_path / "speaker-script.md"))
    monkeypatch.setenv("VIZIER_WRITE_SECRET_FILE", str(tmp_path / "secret"))
    import vizier
    return importlib.reload(vizier), tmp_path


def script(n, extra=""):
    return f"# Speaker script: job {n}\n## [Reader] Intro\nhello {n}\nsecond line {n}{extra}\n"


def items(*ns):
    return [{"id": f"job-{n}", "title": f"Job {n}", "text": script(n), "meta": {"frame": "face_only"}} for n in ns]


def run(v, fn):
    async def go():
        async with TestClient(TestServer(v.make_app())) as c:
            return await fn(c)
    return asyncio.run(go())


async def put(c, its, **kw):
    r = await c.post("/queue", json={"items": its, "source": "t", **kw}, headers=W)
    assert r.status == 200, await r.text()
    return await r.json()


async def go(c, **b):
    r = await c.post("/queue/go", json=b or {"step": 1})
    return r.status, await r.json()


def test_loading_needs_the_write_secret_and_a_spoken_script(vz):
    v, _ = vz
    async def f(c):
        assert (await c.post("/queue", json={"items": items(1)})).status == 403
        assert (await c.post("/queue", json={"items": [{"id": "x", "text": "no sections"}]}, headers=W)).status == 400
        assert (await c.post("/queue", json={"items": [{"id": "bad id", "text": script(1)}]}, headers=W)).status == 400
        assert (await c.post("/queue", json={"items": items(1, 1)}, headers=W)).status == 400
        assert (await (await c.get("/queue")).json())["count"] == 0
    run(v, f)


def test_loading_never_changes_the_screen_and_next_shows_each_in_order(vz):
    v, tmp = vz
    (tmp / "speaker-script.md").write_text(script("posted"))
    async def f(c):
        head = await put(c, items(1, 2, 3))
        assert head["count"] == 3 and head["showing"] is None and "posted" in (tmp / "speaker-script.md").read_text()
        for n in (1, 2, 3):
            t0 = time.perf_counter()
            status, j = await go(c)
            assert status == 200 and time.perf_counter() - t0 < 1.0
            assert j["showing"]["id"] == f"job-{n}" and j["pos"] == n - 1 and j["showing"]["meta"] == {"frame": "face_only"}
            assert j["speaker"]["sections"][0]["lines"][0]["text"] == f"hello {n}" and j["speaker"]["job"] == f"Job {n}"
            sp = await (await c.get("/speaker-script")).json()
            assert sp["queue"] == {"id": f"job-{n}", "pos": n - 1, "count": 3}
            st = await (await c.get("/state")).json()
            assert st["current_line_index"] == 0 and st["current_line_text"] == f"hello {n}"
        assert (await go(c, step=-1))[1]["showing"]["id"] == "job-2"
        assert (await go(c, id="job-1"))[1]["showing"]["id"] == "job-1"
        assert (await go(c, step=-1))[0] == 404
        assert (await go(c, id="nope"))[0] == 404
    run(v, f)


def test_a_script_posted_over_the_queue_is_not_attributed_to_the_item(vz):
    v, _ = vz
    async def f(c):
        await put(c, items(1))
        await go(c)
        assert (await c.post("/speaker-script", data=script("other"), headers=W)).status == 200
        head = await (await c.get("/queue")).json()
        assert head["current"] == "job-1" and head["showing"] is None
        assert (await (await c.get("/speaker-script")).json())["queue"] is None
        await put(c, items(1, 2))
        assert (await c.post("/speaker-script", data=script(2), headers=W)).status == 200   # a queued text posted directly
        head = await (await c.get("/queue")).json()
        assert head["showing"]["id"] == "job-2" and head["pos"] == 1 and head["current"] == "job-1"
    run(v, f)


def test_recorded_is_skipped_redo_comes_next_and_survives_restart(vz):
    v, _ = vz
    async def f(c):
        await put(c, items(1, 2, 3, 4))
        await go(c)
        await c.post("/queue/mark", json={"id": "job-1", "state": "recorded"})
        await c.post("/queue/mark", json={"id": "job-2", "state": "recorded"})
        assert (await go(c))[1]["showing"]["id"] == "job-3"
        r = await (await c.post("/queue/mark", json={"id": "job-1", "state": "redo"})).json()
        assert [i["id"] for i in r["items"]] == ["job-2", "job-3", "job-1", "job-4"]
        assert (await go(c))[1]["showing"]["id"] == "job-1"
        assert (await go(c))[1]["showing"]["id"] == "job-4"
        for n in (1, 3, 4):
            await c.post("/queue/mark", json={"id": f"job-{n}", "state": "recorded"})
        assert (await go(c))[0] == 404
        assert (await c.post("/queue/mark", json={"id": "job-1", "state": "gone"})).status == 400
    run(v, f)
    async def again(c):
        head = await (await c.get("/queue")).json()
        assert head["count"] == 4 and head["current"] == "job-4" and head["showing"]["id"] == "job-4"
    run(importlib.reload(v), again)


def test_replace_syncs_one_source_and_keeps_the_screen(vz):
    v, tmp = vz
    async def f(c):
        await put(c, items(1, 2))
        await c.post("/queue", json={"items": [{"id": "other", "text": script("o")}], "source": "o"}, headers=W)
        await go(c)
        await c.post("/queue/mark", json={"id": "job-1", "state": "recorded"})
        head = await put(c, items(3), replace=True)   # job-1 is on screen: kept; job-2 expired: dropped
        assert [i["id"] for i in head["items"]] == ["job-1", "job-3", "other"] and head["showing"]["id"] == "job-1"
        seq = head["seq"]
        assert (await put(c, items(3), replace=True))["seq"] == seq          # nothing changed: no new seq
        changed = [{**items(1)[0], "text": script(1, " rewritten")}]
        head = await put(c, changed)
        assert head["showing"] is None and head["items"][0]["state"] == "ready"   # new text: unread, the screen keeps the old
        assert "rewritten" not in (tmp / "speaker-script.md").read_text()
        assert (await go(c, id="job-1"))[1]["showing"]["id"] == "job-1"
    run(v, f)


def test_a_script_with_no_spoken_line_is_refused_and_never_counts_ready(vz):
    v, _ = vz

    async def f(c):
        await put(c, items(1, 2))
        r = await c.post("/queue", json={"items": [{"id": "empty", "text": "# t\n## [Reader] Intro\n\n## [Reader] Next\n"}], "source": "t"}, headers=W)
        assert r.status == 400
        q = await (await c.get("/queue")).json()
        assert q["ready"] == 2 and q["count"] == 2
        # a legacy empty item already stored is not counted ready and Next skips it
        d = v.queue_load()
        d["items"].insert(1, {"id": "legacy", "title": "x", "text": "## [Reader] Intro\n", "meta": {}, "source": "t", "state": "ready"})
        v.queue_save(d)
        q = await (await c.get("/queue")).json()
        assert q["count"] == 3 and q["ready"] == 2
        st, a = await go(c, id="job-1")
        st, b = await go(c, step=1)
        assert b["showing"]["id"] == "job-2"
    run(v, f)


def test_a_redo_keeps_one_line_on_the_script_until_it_is_recorded(vz):
    v, _ = vz
    async def f(c):
        await put(c, items(1, 2, 3))
        await go(c)
        await c.post("/queue/mark", json={"id": "job-3", "state": "recorded"})
        r = await (await c.post("/queue/mark", json={"id": "job-3", "state": "redo", "reason": "Redo: line 2\n wasn't heard."})).json()
        assert [i["id"] for i in r["items"]] == ["job-1", "job-3", "job-2"]          # it comes up next
        assert r["items"][1]["redo"] == "Redo: line 2 wasn't heard." and r["items"][1]["state"] == "redo"
        again = await put(c, items(1, 2, 3), replace=True)                           # the 30 s feed reload keeps the line
        assert next(i for i in again["items"] if i["id"] == "job-3")["redo"] == "Redo: line 2 wasn't heard."
        await go(c, id="job-3")
        assert (await (await c.get("/speaker-script")).json())["redo"] == "Redo: line 2 wasn't heard."
        r = await (await c.post("/queue/mark", json={"id": "job-3", "state": "recorded"})).json()
        assert "redo" not in next(i for i in r["items"] if i["id"] == "job-3")
        assert (await (await c.get("/speaker-script")).json())["redo"] == ""
    run(v, f)
