"""Issue 97: the Voice/Ears switch survives a restart; a fresh data dir defaults both ON."""
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
    monkeypatch.setenv("VIZIER_DATA", str(tmp_path / "data"))
    import vizier
    return importlib.reload(vizier)


def run(app, fn):
    async def go():
        async with TestClient(TestServer(app)) as c:
            return await fn(c)
    return asyncio.run(go())


def test_switch_survives_restart(vz):
    async def first(c):
        assert await (await c.get("/talk/switch")).json() == {"ears": True, "mouth": True}   # fresh dir: today's default
        r = await c.post("/talk/switch", json={"mouth": False})
        assert r.status == 200
    run(vz.make_app(), first)

    async def second(c):   # a fresh app = a restarted server
        assert await (await c.get("/talk/switch")).json() == {"ears": True, "mouth": False}
    run(vz.make_app(), second)
