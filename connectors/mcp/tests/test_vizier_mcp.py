"""The MCP server end to end: a real Vizier server on a free port, the MCP server over stdio, every tool called."""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def test_tools_drive_a_live_server(tmp_path):
    port = free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("VIZIER_")}
    srv = subprocess.Popen([sys.executable, str(ROOT / "server/vizier.py"), "--port", str(port)],
                           env={**env, "VIZIER_DATA": str(tmp_path)})
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(url + "/health", timeout=1); break
            except OSError:
                time.sleep(0.1)
        mcp = subprocess.Popen([sys.executable, str(ROOT / "connectors/mcp/vizier_mcp.py")], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, text=True, env={**env, "VIZIER_URL": url})
        n = 0

        def rpc(method, params=None):
            nonlocal n
            n += 1
            mcp.stdin.write(json.dumps({"jsonrpc": "2.0", "id": n, "method": method, "params": params or {}}) + "\n")
            mcp.stdin.flush()
            return json.loads(mcp.stdout.readline())

        init = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})
        assert init["result"]["serverInfo"]["name"] == "vizier" and "experimental" not in init["result"]["capabilities"]
        mcp.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        names = [t["name"] for t in rpc("tools/list")["result"]["tools"]]
        assert {"post_line", "post_bullets", "read_transcript", "set_script", "set_status"} <= set(names)
        tool = lambda name, args: rpc("tools/call", {"name": name, "arguments": args})["result"]
        assert not tool("start_call", {"call_id": "t1"})["isError"]
        assert not tool("post_line", {"ch": "them", "text": "Can you start Monday?"})["isError"]
        assert tool("post_bullets", {"bullets": ["Yes, Monday works.", "What is first?"]})["content"][0]["text"] == "shown as card t1-b1"
        assert tool("set_script", {"markdown": "## 1 · A\nx\n## 2 · B\ny\n"})["content"][0]["text"] == "script set: 2 takes"
        assert not tool("set_status", {"text": "ok"})["isError"]
        t = tool("read_transcript", {})["content"][0]["text"]
        assert "[1] THEM: Can you start Monday?" in t
        bad = tool("post_line", {"ch": "nobody", "text": "x"})
        assert bad["isError"] and "HTTP 400" in bad["content"][0]["text"]
        assert "1 line(s)" in tool("end_call", {})["content"][0]["text"]
        mcp.kill()
    finally:
        srv.kill()


def test_push_ch_them_pushes_only_the_other_side(tmp_path):
    """VIZIER_MCP_PUSH_CH=them pushes only THEM lines, and a call that POST /lines starts loses none of its first lines."""
    port = free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("VIZIER_")}
    srv = subprocess.Popen([sys.executable, str(ROOT / "server/vizier.py"), "--port", str(port)],
                           env={**env, "VIZIER_DATA": str(tmp_path)})
    url = f"http://127.0.0.1:{port}"

    def post(path, body):
        req = urllib.request.Request(url + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=5).read() or b"{}")
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(url + "/health", timeout=1); break
            except OSError:
                time.sleep(0.1)
        mcp = subprocess.Popen([sys.executable, str(ROOT / "connectors/mcp/vizier_mcp.py")], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, text=True,
                               env={**env, "VIZIER_URL": url, "VIZIER_MCP_PUSH": "1", "VIZIER_MCP_PUSH_CH": "them",
                                    "VIZIER_MCP_POLL_S": "0.1"})
        mcp.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}) + "\n")
        mcp.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        mcp.stdin.flush()
        init = json.loads(mcp.stdout.readline())
        assert "experimental" in init["result"]["capabilities"]
        time.sleep(0.5)                                   # the pusher has read the transcript's current end
        post("/lines", {"ch": "me", "text": "my own words", "final": True})
        post("/lines", {"ch": "them", "text": "Can you start Monday?", "final": True})
        post("/lines", {"ch": "me", "text": "more of mine", "final": True})
        post("/lines", {"ch": "them", "text": "Great.", "final": True})
        got = []

        def read():                                       # a reader thread, so a missing event fails, never hangs
            for raw in mcp.stdout:
                m = json.loads(raw)
                if m.get("method") == "notifications/claude/channel":
                    got.append(m["params"]["content"])
        threading.Thread(target=read, daemon=True).start()
        time.sleep(2)
        assert got == ["THEM: Can you start Monday?", "THEM: Great."]
        mcp.kill()
    finally:
        srv.kill()
