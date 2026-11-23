"""issue 17403: Vizier derives row_times.json from the cursor: pure function + the /take endpoints over a fake obs-websocket."""
import asyncio, importlib, json, os, sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestServer, TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_obs
from test_loop import vz, run  # noqa: F401  (fixture + runner)


def seq(*rows, t0=1000, step=100):
    return [(t0 + k * step, r) for k, r in enumerate(rows)]


def test_down5_up_altT_down2():
    import vizier
    ev = seq(0, 1, 2, 3, 4, 5, 4, 0, 1, 2)   # start on 0, Down x5, Up, Alt+T, Down x2
    rt = vizier.row_times(ev, 5000)["rows"]
    by = {r["i"]: (r["start_ms"], r["end_ms"]) for r in rt}
    assert sorted(by) == [0, 1, 2, 3, 4, 5]
    assert by[0] == (1700, 1800)      # Alt+T reset to row 0: LAST arrival; left on the next Down
    assert by[1] == (1800, 1900)
    assert by[2] == (1900, 5000)      # final row ends at take stop
    assert by[3] == (1300, 1400)
    assert by[4] == (1600, 1700)      # visited twice: last start (the Up), left at Alt+T
    assert by[5] == (1500, 1600)


def test_every_row_has_ordered_times_and_empty_take():
    import vizier
    assert vizier.row_times([], 10) == {"rows": []}
    for r in vizier.row_times(seq(0, 1, 0, 1, 2), 9000)["rows"]:
        assert r["start_ms"] <= r["end_ms"]


def test_take_endpoints_write_row_times_json(vz, tmp_path):
    v, tmp = vz
    take = tmp / "take1"
    async def go():
        obs = TestServer(fake_obs.make_app()); await obs.start_server()
        v.OBS_URL = f"ws://127.0.0.1:{obs.port}/"
        try:
            async with TestClient(TestServer(v.make_app())) as c:
                key = lambda k: c.post("/prompter/key", json={"key": k})
                await key("AltT")
                r = await (await c.post("/take/start", json={"dir": str(take)})).json()
                assert r["take_dir"] == str(take)
                for k in ["Down"] * 5 + ["Up", "AltT", "Down", "Down"]:
                    await key(k); await asyncio.sleep(0.01)
                live = await (await c.get("/take/row_times")).json()
                assert live["active"] and len(live["rows"]) == 6
                st = await (await c.post("/take/stop", json={})).json()
                assert st["rows"] == 6
        finally:
            await obs.close()
    run(go())
    rt = json.loads((take / "row_times.json").read_text())["rows"]
    assert [r["i"] for r in rt] == [0, 1, 2, 3, 4, 5]
    assert all(r["end_ms"] >= r["start_ms"] for r in rt)
    assert rt[2]["i"] == 2 and rt[2]["end_ms"] >= rt[2]["start_ms"]
