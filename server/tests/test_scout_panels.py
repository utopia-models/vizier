"""the deployment's issue 13798 ("Scout == Vizier"): every setting of the voice agent is in Vizier, a desk opens from Vizier, and
the team's status shows in Vizier. The server side: GET/POST /settings proxies a voice agent's control API, /desks lists
and opens desks through two commands, /agents summarizes a directory of liveness files. Nothing configured = the panel
says so (never a made-up value)."""
import asyncio
import importlib
import json
import os
import sys
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
PY = sys.executable


def load(tmp_path, monkeypatch, **env):
    for k in list(os.environ):
        if k.startswith("VIZIER_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("VIZIER_DATA", str(tmp_path))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import vizier
    return importlib.reload(vizier)


def fake_voice_agent(state):
    """The voice agent's control API as the server uses it (ears.py: /health, /voices, /voice, /address)."""
    async def health(_):
        return web.json_response({"room": "scout", "ears": True, "mouth": True, "clients": [],
                                  "address": {"call_live": False, "say": "allowed"},
                                  "address_rules": {"on": True, "word": "scout", "call_n": state["call_n"], "outside_n": 1}})

    async def voices(_):
        return web.json_response({"voice": state["voice"], "voices": [{"name": "Brian", "accent": "British, male",
                                                                       "provider": "polly"},
                                                                      {"name": "Zayd", "accent": "Gulf Arabic, male",
                                                                       "provider": "polly"}]})

    async def voice(req):
        b = await req.json()
        state["voice"] = "Zayd" if b.get("step") == "next" else b.get("name") or state["voice"]
        return web.json_response({"ok": True, "voice": state["voice"]})

    async def address(req):
        b = await req.json()
        state["call_n"] = b.get("call_n", state["call_n"])
        return web.json_response({"ok": True, "call_n": state["call_n"]})
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/voices", voices)
    app.router.add_post("/voice", voice)
    app.router.add_post("/address", address)
    return app


def test_nothing_configured_says_so(tmp_path, monkeypatch):
    vz = load(tmp_path, monkeypatch)

    async def go():
        async with TestClient(TestServer(vz.make_app())) as c:
            return [await (await c.get(p)).json() for p in ("/settings", "/desks", "/agents")]
    s, d, a = asyncio.run(go())
    assert s["configured"] is False and d["configured"] is False and a["configured"] is False


def test_settings_read_and_change_the_voice_and_wake_rules(tmp_path, monkeypatch):
    state = {"voice": "Brian", "call_n": 3}

    async def go():
        agent = TestServer(fake_voice_agent(state))
        await agent.start_server()
        vz = load(tmp_path, monkeypatch, VIZIER_VOICE_URL=str(agent.make_url("")).rstrip("/"))
        async with TestClient(TestServer(vz.make_app())) as c:
            first = await (await c.get("/settings")).json()
            nxt = await c.post("/settings", json={"voice": "next"})
            wake = await c.post("/settings", json={"wake": {"call_n": 2}})
            after = await (await c.get("/settings")).json()
        await agent.close()
        return first, nxt.status, wake.status, after
    first, s1, s2, after = asyncio.run(go())
    assert first["reachable"] and first["voice"] == "Brian" and first["wake"]["call_n"] == 3
    assert [v["name"] for v in first["voices"]] == ["Brian", "Zayd"]
    assert s1 == 200 and s2 == 200
    assert after["voice"] == "Zayd" and after["wake"]["call_n"] == 2


def test_an_unreachable_voice_agent_is_reported_not_invented(tmp_path, monkeypatch):
    vz = load(tmp_path, monkeypatch, VIZIER_VOICE_URL="http://127.0.0.1:9")

    async def go():
        async with TestClient(TestServer(vz.make_app())) as c:
            return await (await c.get("/settings")).json()
    s = asyncio.run(go())
    assert s["configured"] and s["reachable"] is False and s["error"]


def test_desks_list_and_open_only_a_listed_desk(tmp_path, monkeypatch):
    listing = tmp_path / "desks.json"
    listing.write_text(json.dumps([{"id": "upwork", "name": "Upwork", "agent": "upwork"},
                                   {"id": "cold-apps", "name": "Cold applications", "agent": "jobs"}]))
    opened = tmp_path / "opened.txt"
    vz = load(tmp_path, monkeypatch, VIZIER_DESKS_CMD=f"cat {listing}",
              VIZIER_DESK_OPEN_CMD=f"{PY} -c \"import sys; open('{opened}', 'a').write(sys.argv[1])\" {{id}}")

    async def go():
        async with TestClient(TestServer(vz.make_app())) as c:
            lst = await (await c.get("/desks")).json()
            ok = await c.post("/desks/open", json={"id": "upwork"})
            bad = await c.post("/desks/open", json={"id": "upwork; rm -rf /"})
            return lst, ok.status, bad.status
    lst, ok, bad = asyncio.run(go())
    assert [d["id"] for d in lst["desks"]] == ["upwork", "cold-apps"]
    assert ok == 200 and opened.read_text() == "upwork"
    assert bad == 404, "an id not in the list never reaches the command"


def test_agents_summarize_each_liveness_file(tmp_path, monkeypatch):
    d = tmp_path / "usage"
    d.mkdir()
    (d / "upwork.json").write_text(json.dumps({"agent": "upwork", "liveness": {
        "health": "green", "healthAt": "2026-11-15T20:03:28Z", "healthReasons": [],
        "externalEvent": "Upwork: a client reply"}}))
    (d / "jobs.json").write_text(json.dumps({"agent": "jobs", "fetchedAt": "2026-11-15T20:00:00Z"}))
    (d / "broken.json").write_text("{not json")
    vz = load(tmp_path, monkeypatch, VIZIER_AGENTS_DIR=str(d))

    async def go():
        async with TestClient(TestServer(vz.make_app())) as c:
            return await (await c.get("/agents")).json()
    a = asyncio.run(go())
    rows = {r["agent"]: r for r in a["agents"]}
    assert set(rows) == {"upwork", "jobs"}
    assert rows["upwork"]["health"] == "green" and rows["upwork"]["doing"] == "Upwork: a client reply"
    assert rows["jobs"]["health"] == "unknown", "no liveness block: unknown, never green"


def test_horus_script_reads_the_cue_table_and_the_current_beat(tmp_path, monkeypatch):
    (tmp_path / "rs.md").write_text("| t | Tyler says | on screen | horus action | mark | live adjust |\n|---|---|---|---|---|---|\n"
                                    "| 0:00 | I'm going | post | scroll | START | |\n| 0:20 | An email | demo | click Send | | click again |\n")
    (tmp_path / "state.json").write_text(json.dumps({"beat": "b1", "at": "x"}))
    vz = load(tmp_path, monkeypatch, VIZIER_HORUS_CUES=str(tmp_path / "none.md") + os.pathsep + str(tmp_path / "rs.md"),
              VIZIER_HORUS_STATE=str(tmp_path / "state.json"))

    async def go():
        async with TestClient(TestServer(vz.make_app())) as c:
            return await (await c.get("/horus-script")).json()
    j = asyncio.run(go())
    assert j["source"] == "rs.md" and j["current"] == "b1" and len(j["cues"]) == 2
    assert j["cues"][1] == {"beat": "b1", "id": "0:20", "on": "An email", "do": "click Send", "wait": "", "fail": "click again"}
