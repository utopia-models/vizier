"""Replay (a reported regression): every POST /script threw the reader off the CONTEXT tab.
Cause: the posted script is posted-script.md, which has no `-context.md` sibling, so /prompter/context came back empty
and the client dropped the hidden tab. A text update must keep the context of the script it replaces."""
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
    s = tmp_path / "pinned" / "eagle-script.md"
    s.parent.mkdir()
    s.write_text("# Eagle\n## 1 · Hook\nfirst\n")
    (s.parent / "eagle-context.md").write_text("- job key abc\n- budget $500\n")
    monkeypatch.setenv("VIZIER_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("VIZIER_SCRIPTS", str(s))
    import vizier
    return importlib.reload(vizier)


def run(app, fn):
    async def go():
        async with TestClient(TestServer(app)) as c:
            return await fn(c)
    return asyncio.run(go())


def test_post_with_context_active_keeps_context(vz):
    async def f(c):
        before = await (await c.get("/prompter/context")).json()
        await c.post("/script", json={"markdown": "# Eagle\n## 1 · Hook\nsecond\n"})
        after = await (await c.get("/prompter/context")).json()
        await c.post("/script", json={"markdown": "# Eagle\n## 1 · Hook\nthird\n"})   # a second update keeps it too
        again = await (await c.get("/prompter/context")).json()
        takes = await (await c.get("/prompter/scripts")).json()
        return before, after, again, takes
    before, after, again, takes = run(vz.make_app(), f)
    assert before["context"] and after["context"] == before["context"] == again["context"], "POST /script dropped the CONTEXT tab"
    assert takes[0]["lines"] == ["third"], "the new script text is not what the Script tab shows"


def test_context_can_be_replaced_or_dropped(vz):
    async def f(c):
        await c.post("/script", json={"markdown": "# X\n## 1 · A\nx\n", "context": "- new\n"})
        a = (await (await c.get("/prompter/context")).json())["context"]
        await c.post("/script", json={"markdown": "# X\n## 1 · A\nx\n", "context": ""})
        b = (await (await c.get("/prompter/context")).json())["context"]
        return a, b
    a, b = run(vz.make_app(), f)
    assert a == "- new\n" and b == ""
