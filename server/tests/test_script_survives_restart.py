"""Replay test (Tyler 2026-11-14, knowledge#13257, said twice): "the Mohamed script is outdated, why is it still up
here?" The prompter showed a past client's script because every restart put the deploy-time VIZIER_SCRIPTS default
back on top over what the agent had last shown. The script POST /script chose must survive a restart, and an agent
must be able to take a script down ({"clear": true}) so the prompter shows the honest empty state."""
import asyncio
import importlib
import os
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OLD = "# Milestone 2 walkthrough for a past client\n## 1 · Hello\nthe outdated script\n"
NEW = "# Eagle Eye\n## 1 · Pitch\nthe current script\n"


@pytest.fixture
def vz(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("VIZIER_"):
            monkeypatch.delenv(k)
    old = tmp_path / "pinned" / "m2-walkthrough-script.md"
    old.parent.mkdir()
    old.write_text(OLD)
    monkeypatch.setenv("VIZIER_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("VIZIER_SCRIPTS", str(old))   # the unit's deploy-time default, a past client's script
    import vizier
    return importlib.reload(vizier)


def run(app, fn):
    async def go():
        async with TestClient(TestServer(app)) as c:
            return await fn(c)
    return asyncio.run(go())


def shown(vz):
    async def f(c):
        return await (await c.get("/prompter/scripts")).json()
    return run(vz.make_app(), f)   # a fresh app = a restarted server


def test_posted_script_survives_a_restart(vz):
    async def post(c):
        return (await c.post("/script", json={"markdown": NEW})).status
    assert run(vz.make_app(), post) == 200
    assert shown(vz)[0]["lines"] == ["the current script"], "a restart put the outdated default script back on top"


def test_clear_takes_the_script_down_and_stays_down(vz):
    assert shown(vz)[0]["lines"] == ["the outdated script"]   # no choice yet: the default shows

    async def clear(c):
        r = await c.post("/script", json={"clear": True})
        return r.status, await r.json()
    status, j = run(vz.make_app(), clear)
    assert status == 200 and j["takes"] == 0
    assert shown(vz) == [{"title": "waiting", "lines": ["no script yet: POST /script"]}]
