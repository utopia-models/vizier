"""eagle#19887: Next / Prev / id each push one `script_changed` event on GET /events (id, type, company, job, timestamp),
and GET /speaker-script carries the `<Type> · <company> · <job>` label the green header draws."""
import asyncio, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_queue import vz, run, put, go, script, W  # noqa: F401,E402


def its():
    return [{"id": "a", "title": "A", "text": script(1), "meta": {"label": "Upwork Full VSL · Acme · Senior AI Engineer"}},
            {"id": "b", "title": "B", "text": script(2), "meta": {"type": "Founder Full VSL", "company": "Zed", "job": "Founder video"}}]


def test_script_changed_event_and_label(vz):
    v, d = vz
    async def f(c):
        await put(c, its())
        r = await c.get("/events"); await r.content.readuntil(b"\n\n")   # the cursor snapshot
        out = []
        for step in (1, 1, -1):
            await go(c, step=step)
            while True:   # the move also posts a cursor event (row 1 of the new script); only script_changed is graded
                blk = (await asyncio.wait_for(r.content.readuntil(b"\n\n"), 2)).decode()
                if "event: script_changed" in blk:
                    break
            out.append((blk.split("\n")[1], json.loads(blk.split("data: ", 1)[1])))
        hdr = await (await c.get("/speaker-script")).json()
        r.close(); return out, hdr
    out, hdr = run(v, f)
    assert [e for e, _ in out] == ["event: script_changed"] * 3
    first, second, third = (d_ for _, d_ in out)
    assert (first["id"], first["type"], first["company"], first["job"]) == ("a", "Upwork Full VSL", "Acme", "Senior AI Engineer")
    assert (second["id"], second["type"], second["company"], second["job"], second["via"]) == ("b", "Founder Full VSL", "Zed", "Founder video", "next")
    assert third["id"] == "a" and third["via"] == "prev" and third["timestamp"] > 0
    assert hdr["label"]["label"] == "Upwork Full VSL · Acme · Senior AI Engineer"
