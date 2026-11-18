"""GET /approval, POST /approve: the approver sees gates + references + diff; Ready needs passing gates and an approval."""
import asyncio, importlib, json, os, sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
W = {"X-Vizier-Write": "w-secret", "X-Vizier-Who": "horus"}
A = {"X-Vizier-Approve": "a-secret", "X-Vizier-Who": "architect"}


@pytest.fixture
def vz(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("VIZIER_"):
            monkeypatch.delenv(k)
    (tmp_path / "w").write_text("w-secret\n"); (tmp_path / "a").write_text("a-secret\n")
    (tmp_path / "speaker-script.md").write_text("# T\n\n## 1 · open\nHello there.\n")
    monkeypatch.setenv("VIZIER_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("VIZIER_WRITE_SECRET_FILE", str(tmp_path / "w"))
    monkeypatch.setenv("VIZIER_APPROVE_SECRET_FILE", str(tmp_path / "a"))
    monkeypatch.setenv("VIZIER_SPEAKER_SCRIPT", str(tmp_path / "speaker-script.md"))
    monkeypatch.setenv("VIZIER_GATE_REPORT", str(tmp_path / "gates.json"))
    import vizier
    return importlib.reload(vizier), tmp_path


def run(v, fn):
    async def go():
        async with TestClient(TestServer(v.make_app())) as c:
            return await fn(c)
    return asyncio.run(go())


def report(d, sha, **gates):
    g = {"6a": {"pass": True}, "6b": {"pass": True}, "6c": {"pass": True}, "6d": {"pass": False, "advisory": True}}
    g.update(gates)
    (d / "gates.json").write_text(json.dumps({"sha": sha, "gates": g, "at": 1, "references": [{"id": "r1", "title": "ex", "source": "u", "status": "accepted"}]}))


def test_view_shows_gates_references_and_diff(vz):
    v, d = vz
    async def f(c):
        a = await (await c.get("/approval")).json()
        report(d, a["sha"], **{"6b": {"pass": False, "findings": ["claim 3 has no fact id"]}})
        await c.post("/speaker-script", json={"text": "# T\n\n## 1 · open\nHello again.\n"}, headers=W)
        b = await (await c.get("/approval")).json()
        report(d, b["sha"], **{"6b": {"pass": False, "findings": ["claim 3 has no fact id"]}})
        return await (await c.get("/approval")).json()
    r = run(v, f)
    assert r["gates"]["6b"]["findings"] == ["claim 3 has no fact id"] and r["references"][0]["id"] == "r1"
    assert r["diff"]["has_previous"] and any(l == "+Hello again." for l in r["diff"]["lines"])
    assert any("gate 6b failed" in x for x in r["ready"]["refusals"]) and not r["ready"]["ok"]


def test_ready_needs_gates_and_approval_then_edit_revokes(vz):
    v, d = vz
    async def f(c):
        sha = (await (await c.get("/approval")).json())["sha"]
        report(d, sha)
        r0 = await (await c.get("/approval")).json()
        assert r0["ready"]["refusals"] == ["approval: this script version is not approved"]
        assert (await c.post("/approve", headers={"X-Vizier-Approve": "no", "X-Vizier-Who": "architect"})).status == 403
        assert (await c.post("/approve", headers={**A, "X-Vizier-Who": "horus"})).status == 403
        assert (await c.post("/approve", json={"sha": "stale"}, headers=A)).status == 409
        ok = await c.post("/approve", json={"sha": sha}, headers=A)
        assert ok.status == 200
        r1 = await (await c.get("/approval")).json()
        assert r1["ready"]["ok"] and r1["approval"]["approver"] == "architect" and r1["approval"]["status"] == "approved"
        await c.post("/speaker-script", json={"text": "# T\n\n## 1 · open\nChanged.\n"}, headers=W)
        return await (await c.get("/approval")).json()
    r2 = run(v, f)
    assert r2["approval"]["status"] == "draft" and not r2["ready"]["ok"]
    row = json.loads((d / "data" / "approvals.jsonl").read_text().splitlines()[0])
    assert row["approver"] == "architect" and row["at"] and row["event"] == "approve"


def test_no_report_refuses(vz):
    v, d = vz
    r = run(v, lambda c: c.get("/approval"))
    async def f(c):
        return await (await c.get("/approval")).json()
    r = run(v, f)
    assert r["ready"]["refusals"][0].startswith("gates: no gate report")
