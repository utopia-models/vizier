"""GET /speaker-script returns the job header (the `# ` title line) and the labelled sections. It carries no preview pages: the
diagram and the working app are plain Chrome tabs listed in take.json (the reader 2026-11-22)."""
import asyncio, importlib.util, os, sys
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer


def load(tmp_path, monkeypatch):
    f = tmp_path / "speaker-script.md"
    f.write_text("# Upwork Intro VSL · AI chief of staff · AI Automation Architect post · Take 1\n"
                 "\n"
                 "## [Host] Intro\nHi, I'm Alex.\n\n## [AI voice] Your post\nI read it.\n")
    monkeypatch.setenv("VIZIER_SPEAKER_SCRIPT", str(f)); monkeypatch.setenv("VIZIER_DATA", str(tmp_path))
    for k in list(os.environ):
        if k.startswith("VIZIER_") and k not in ("VIZIER_SPEAKER_SCRIPT", "VIZIER_DATA"):
            monkeypatch.delenv(k)
    import vizier
    return importlib.reload(vizier)


def test_header_and_labelled_sections(tmp_path, monkeypatch):
    m = load(tmp_path, monkeypatch)
    async def go():
        async with TestClient(TestServer(m.make_app())) as c:
            return await (await c.get("/speaker-script")).json()
    d = asyncio.run(go())
    assert d["title"].startswith("Upwork Intro VSL · AI chief of staff") and d["title"].endswith("Take 1")
    assert "previews" not in d
    assert [s["title"] for s in d["sections"]] == ["[Host] Intro", "[AI voice] Your post"]
    assert [l["text"] for l in d["sections"][0]["lines"]] == ["Hi, I'm Alex."]


def test_mvp_lines_and_ui_wiring(tmp_path, monkeypatch):
    """the reader 2026-11-22: which VSL, where the MVP is, the transcript on the Speaker Script, a fast reset to line one."""
    m = load(tmp_path, monkeypatch)
    (tmp_path / "speaker-script.md").write_text("# T\n\n## [Host] Intro\nHi.\n\n## [AI voice] MVP\nThe app does X.\nIt does Y.\n\n## [AI voice] Diagram\nD.\n")
    async def go():
        async with TestClient(TestServer(m.make_app())) as c:
            return await (await c.get("/speaker-script")).json()
    assert asyncio.run(go())["mvp"] == ["The app does X.", "It does Y."]
    html = (Path(__file__).resolve().parents[2] / "app" / "ui" / "index.html").read_text()
    assert 'class="vslk">VSL</span>' in html and 'id="tstalk"' in html and "every(120" in html


def test_job_post_title(tmp_path, monkeypatch):
    """asks 2506: the job post title rides GET /speaker-script (post.md heading, run.json title, else the title line)."""
    m = load(tmp_path, monkeypatch)
    sp = tmp_path / "speaker-script.md"
    sp.write_text("# Vizier speaker script: Clio expert to automate workflows (one-shot, #11180)\n\n## [Host] Intro\nHi.\n")
    async def go():
        async with TestClient(TestServer(m.make_app())) as c:
            return await (await c.get("/speaker-script")).json()
    assert asyncio.run(go())["job"] == "Clio expert to automate workflows"
    (tmp_path / "post.md").write_text("# Need an AI automation architect\n\ntext\n")
    assert asyncio.run(go())["job"] == "Need an AI automation architect"
    html = (Path(__file__).resolve().parents[2] / "app" / "ui" / "index.html").read_text()
    assert 'class="vslk">JOB</span>' in html and 'id="topbtn"' in html
