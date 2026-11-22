"""GET /speaker-script returns the job header (the `# ` title line) and the labelled sections. It carries no preview pages: the
diagram and the working app are plain Chrome tabs listed in take.json (Tyler 2026-11-22)."""
import asyncio, importlib.util, os, sys
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer


def load(tmp_path, monkeypatch):
    f = tmp_path / "speaker-script.md"
    f.write_text("# Upwork Intro VSL · AI chief of staff · AI Automation Architect post · Take 1\n"
                 "\n"
                 "## 1 · YOU SAY:\nHi, I'm Tyler.\n\n## 2 · AI VOICE SAYS (do not read): Your post\nI read it.\n")
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
    assert [s["title"] for s in d["sections"]] == ["1 · YOU SAY:", "2 · AI VOICE SAYS (do not read): Your post"]
    assert [l["text"] for l in d["sections"][0]["lines"]] == ["Hi, I'm Tyler."]
