"""ask 821: follow OFF by default, a lead marker one line ahead, his own keys step the lines, and a QC tab
(QA checklist + the last vsl_qa verdict) read from GET /qc."""
import asyncio, importlib, json, os, re, sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
HTML = (ROOT / "app" / "ui" / "index.html").read_text()
MAIN = (ROOT / "app" / "src" / "main.rs").read_text()


@pytest.fixture
def vz(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("VIZIER_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("VIZIER_DATA", str(tmp_path / "data"))
    import vizier
    return importlib.reload(vizier), tmp_path, monkeypatch


def run(v, fn):
    async def go():
        async with TestClient(TestServer(v.make_app())) as c:
            return await fn(c)
    return asyncio.run(go())


def test_follow_is_off_by_default_on_server_and_page(vz):
    v, _, _ = vz
    assert run(v, lambda c: _get(c, "/prompter/follow")) == {"on": False}
    assert re.search(r"followOn = false;", HTML)


async def _get(c, path):
    return await (await c.get(path)).json()


def test_follow_can_still_be_turned_on(vz):
    v, _, _ = vz
    async def f(c):
        r = await (await c.post("/prompter/follow", json={"toggle": True})).json()
        return r
    assert run(v, f) == {"on": True}


def test_qc_unconfigured_says_so(vz):
    v, _, _ = vz
    r = run(v, lambda c: _get(c, "/qc"))
    assert r["configured"] is False and r["checklist"] == [] and r["verdict"] is None


def test_qc_serves_checklist_and_last_verdict(vz):
    v, d, mp = vz
    (d / "qc.json").write_text(json.dumps({"checklist": [{"id": "mic", "text": "DJI mic", "pass": True}, {"id": "obs", "text": "OBS full screen", "pass": None}]}))
    (d / "verdict.json").write_text(json.dumps({"verdict": "FAIL", "at": "2026-11-18T20:00:00Z", "findings": ["silence at 0:42"]}))
    mp.setenv("VIZIER_QC_FILE", str(d / "qc.json")); mp.setenv("VIZIER_QA_VERDICT", str(d / "verdict.json"))
    r = run(v, lambda c: _get(c, "/qc"))
    assert r["configured"] and [i["id"] for i in r["checklist"]] == ["mic", "obs"]
    assert r["verdict"]["verdict"] == "FAIL" and r["verdict"]["findings"] == ["silence at 0:42"]


def test_qc_tab_is_wired_into_the_page():
    assert 'id="tab-qc"' in HTML and 'id="m-qc"' in HTML and 'readPanel("qc", "qc"' in HTML


def test_current_line_is_lit_no_next_marker_and_only_keys_and_clicks_move_the_cursor():
    fn = HTML[HTML.index("function drawTscript()"):HTML.index("async function loadTscript()")]
    assert "i === tsCur" in fn and "NEXT" not in fn and "lead" not in fn   # the CURRENT line is lit; the next one is only dimmed (no NEXT marker)
    step = HTML[HTML.index("function panelStep(d)"):][:700]
    assert 'mode === "tscript"' in step and "tsCur = Math.max(0, Math.min(n - 1, tsCur + d))" in step
    # nothing else assigns the cursor (no voice, no timer)
    # the declaration, the clamp when the script shrinks, the keyed step, and a click on a row
    assert len(re.findall(r"\btsCur\s*=[^=]", HTML)) == 4


def test_his_global_keys_are_registered():
    for k in ("KeyE", "KeyM", "ArrowDown", "ArrowUp"):
        assert f"Code::{k}" in MAIN, k
    for name in ('"alt+e"', '"alt+m"', '"down" | "up"'):
        assert name in MAIN, name
