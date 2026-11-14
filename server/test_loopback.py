"""Self-test for GET/POST /talk/loopback: python3 server/test_loopback.py (needs aiohttp, no network)."""
import asyncio
import sys
import tempfile
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vizier  # noqa: E402


async def main() -> int:
    vizier.DATA = Path(tempfile.mkdtemp())
    async with TestClient(TestServer(vizier.make_app())) as c:
        r = await c.get("/talk/loopback")
        assert r.status == 404, r.status
        rep = {"t": "2026-11-14T00:00:00Z", "devices": [{"label": "Speakers", "heard": True, "cancelled_db": 31.5}]}
        assert (await (await c.post("/talk/loopback", json=rep)).json()) == {"ok": True}
        j = await (await c.get("/talk/loopback")).json()
        assert j == rep, j
    print("PASS")
    return 0

sys.exit(asyncio.run(main()))
