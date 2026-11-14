"""Self-test for GET/POST /talk/transcript: python3 server/test_talk.py (needs aiohttp, no network)."""
import asyncio
import sys
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vizier  # noqa: E402


async def main() -> int:
    async with TestClient(TestServer(vizier.make_app())) as c:
        post = lambda rows: c.post("/talk/transcript", json={"rows": rows})
        get = lambda since=0: c.get(f"/talk/transcript?since={since}")
        j = await (await get()).json()
        assert j == {"seq": 0, "lines": [], "partial": None}, j
        await post([{"ch": "me", "text": "hello sc", "final": False}])
        j = await (await get()).json()
        assert j["partial"]["text"] == "hello sc" and j["lines"] == [], j
        await post([{"ch": "me", "text": "Hello Scout.", "final": True}, {"ch": "agent", "text": "Hi.", "final": True},
                    {"ch": "me", "text": "   ", "final": True}, "junk"])
        j = await (await get()).json()
        assert [x["text"] for x in j["lines"]] == ["Hello Scout.", "Hi."] and j["partial"] is None and j["seq"] == 2, j
        j = await (await get(1)).json()
        assert [x["ch"] for x in j["lines"]] == ["agent"], j
        await post([{"ch": "me", "text": "and", "final": False}])
        await post([{"ch": "agent", "text": "Sure.", "final": True}])   # the agent's final does not clear his partial
        j = await (await get(2)).json()
        assert j["partial"]["text"] == "and" and j["lines"][0]["text"] == "Sure.", j
    print("PASS")
    return 0

sys.exit(asyncio.run(main()))
