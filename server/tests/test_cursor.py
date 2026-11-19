"""The reading cursor: POST /prompter/position -> GET /state, GET /cursor (long-poll), GET /events (SSE), cursor.jsonl."""
import asyncio, importlib, json, os, statistics, sys, time
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def vz(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("VIZIER_"):
            monkeypatch.delenv(k)
    (tmp_path / "speaker-script.md").write_text("# T\n\n## 1 · open\nHello there.\nSecond line.\n")
    monkeypatch.setenv("VIZIER_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("VIZIER_SPEAKER_SCRIPT", str(tmp_path / "speaker-script.md"))
    import vizier
    return importlib.reload(vizier), tmp_path


def run(v, fn):
    async def go():
        async with TestClient(TestServer(v.make_app())) as c:
            return await fn(c)
    return asyncio.run(go())


def pos(line, text="x", lines=5):
    return {"part": 1, "parts": 1, "title": "speaker script", "line": line, "lines": lines, "text": text}


def test_state_carries_the_cursor_and_seq_only_grows_on_a_change(vz):
    v, d = vz
    async def f(c):
        s0 = await (await c.get("/state")).json()
        r1 = await (await c.post("/prompter/position", json=pos(1, "Hello there."))).json()
        r1b = await (await c.post("/prompter/position", json=pos(1, "Hello there."))).json()   # unchanged: no new seq
        r2 = await (await c.post("/prompter/position", json=pos(2, "Second line."))).json()
        return s0, r1, r1b, r2, await (await c.get("/state")).json()
    s0, r1, r1b, r2, s = run(v, f)
    assert s0["seq"] == 0 and s0["current_line_index"] is None
    assert (r1["seq"], r1b["seq"], r2["seq"]) == (1, 1, 2)
    assert s["current_line_index"] == 1 and s["current_line_text"] == "Second line." and s["total_lines"] == 5
    assert s["seq"] == 2 and s["updated_at"] and s["script_name"] == "speaker script" and s["script_id"] and s["part"] == 1
    rows = [json.loads(l) for l in (d / "data" / "cursor.jsonl").read_text().splitlines()]
    assert [r["seq"] for r in rows] == [1, 2] and rows[0]["current_line_index"] == 0


def test_long_poll_waits_for_the_next_change(vz):
    v, d = vz
    async def f(c):
        await c.post("/prompter/position", json=pos(1))
        waiter = asyncio.ensure_future(c.get("/cursor?since=1&timeout=5"))
        await asyncio.sleep(0.1)
        t0 = time.perf_counter(); await c.post("/prompter/position", json=pos(2, "b"))
        j = await (await waiter).json()
        dt = time.perf_counter() - t0
        quiet = await (await c.get("/cursor?since=2&timeout=0.2")).json()
        return j, dt, quiet
    j, dt, quiet = run(v, f)
    assert j["changed"] and j["seq"] == 2 and j["current_line_index"] == 1 and dt < 0.1
    assert quiet["changed"] is False and quiet["seq"] == 2


def test_sse_pushes_each_change(vz):
    v, d = vz
    async def f(c):
        r = await c.get("/events")
        async def ev():
            await r.content.readuntil(b"\n\n")   # the snapshot
            blk = (await r.content.readuntil(b"\n\n")).decode()
            return blk
        t = asyncio.ensure_future(ev())
        await asyncio.sleep(0.05)
        await c.post("/prompter/position", json=pos(3, "third"))
        blk = await asyncio.wait_for(t, 2)
        r.close(); return blk
    blk = run(v, f)
    assert blk.startswith("id: 1\nevent: cursor\n") and '"current_line_index": 2' in blk


def test_key_to_state_and_event_latency(vz):
    """20 trials: the POST (what the app sends on a keypress) -> /state shows it and the SSE event arrived, in ms."""
    v, d = vz
    async def f(c):
        r = await c.get("/events"); await r.content.readuntil(b"\n\n")
        ms = []
        for i in range(20):
            t0 = time.perf_counter()
            await c.post("/prompter/position", json=pos(i + 1, f"l{i}", 40))
            await r.content.readuntil(b"\n\n"); t_ev = time.perf_counter() - t0
            st = await (await c.get("/state")).json(); assert st["current_line_index"] == i
            ms.append(t_ev * 1000)
        r.close(); return ms
    ms = sorted(run(v, f))
    print(f"event ms p50={statistics.median(ms):.1f} p95={ms[18]:.1f}")
    assert ms[18] < 100
