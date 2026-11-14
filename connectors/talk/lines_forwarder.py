#!/usr/bin/env python3
"""lines_forwarder.py: the live conversation with the agent onto Vizier's TALK mode.

Tails the voice gateway's lines.jsonl and POSTs what it reads to the display server's POST /talk/transcript:
  ev final (and late)  -> ch me, final        (what was said to the agent)
  ev interim           -> ch me, partial      (the utterance so far; the final replaces it)
  ev say               -> ch agent, final     (what the agent spoke; its `text` when the gateway logs one, else the word count)
Starts at the end of the file plus the last BACKFILL seconds of finals, so a restart does not replay history.

env: VIZIER_URL (default http://127.0.0.1:18787) · VIZIER_TOKEN · V3_LINES (default ~/.local/state/voice-v3/lines.jsonl)
     VIZIER_TALK_BACKFILL (seconds, default 120)
  --selftest   feed fixture rows through the mapping and a local fake server, print PASS/FAIL, exit
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

URL = os.environ.get("VIZIER_URL", "http://127.0.0.1:18787").rstrip("/")
TOKEN = os.environ.get("VIZIER_TOKEN", "")
LINES = Path(os.environ.get("V3_LINES", Path.home() / ".local/state/voice-v3/lines.jsonl"))
BACKFILL = float(os.environ.get("VIZIER_TALK_BACKFILL", "120"))


def to_rows(batch: list[dict]) -> list[dict]:
    """Gateway rows -> POST rows. Of the interims in a batch only the last matters, and none after a final."""
    out, partial = [], None
    for r in batch:
        ev, text = r.get("ev"), str(r.get("text") or "").strip()
        if ev == "interim" and text:
            partial = {"ch": "me", "text": text, "final": False}
        elif ev in ("final", "late") and text:
            partial = None
            out.append({"ch": "me", "text": text, "final": True})
        elif ev == "say":
            out.append({"ch": "agent", "final": True,
                        "text": text or f"(spoke {int(r.get('words') or 0)} words)"})
    if partial:
        out.append(partial)
    return out


def post(rows: list[dict]) -> None:
    req = urllib.request.Request(URL + "/talk/transcript", data=json.dumps({"rows": rows}).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": "Bearer " + TOKEN} if TOKEN else {})})
    urllib.request.urlopen(req, timeout=5).read()


def parse(raw: bytes) -> list[dict]:
    out = []
    for ln in raw.decode("utf-8", "replace").splitlines():
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def start_offset() -> int:
    """Byte offset to begin at: the first row of the last BACKFILL seconds (finals and says only, interims are stale)."""
    size = LINES.stat().st_size
    with LINES.open("rb") as f:
        f.seek(max(0, size - 200_000))
        data = f.read()
    off, cut = size - len(data), time.time() - BACKFILL
    pos = 0
    for ln in data.splitlines(keepends=True):
        try:
            if json.loads(ln).get("t", 0) >= cut:
                return off + pos
        except ValueError:
            pass
        pos += len(ln)
    return size


def run() -> None:
    while not LINES.exists():
        time.sleep(2)
    off, first = start_offset(), True
    while True:
        try:
            size = LINES.stat().st_size
            if size < off:   # rotated or truncated
                off = 0
            if size > off:
                with LINES.open("rb") as f:
                    f.seek(off)
                    raw = f.read(size - off)
                end = raw.rfind(b"\n") + 1   # only whole lines
                batch = parse(raw[:end])
                if first:
                    batch = [r for r in batch if r.get("ev") != "interim"]
                rows = to_rows(batch)
                if rows:
                    post(rows)
                off += end
                first = False
        except OSError as e:   # the display server or tunnel is down: keep the offset, retry
            print("forwarder:", e, file=sys.stderr, flush=True)
            time.sleep(2)
        time.sleep(0.15)


def selftest() -> int:
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    got = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
        def log_message(self, *a): pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    global URL
    URL = f"http://127.0.0.1:{srv.server_port}"
    fixture = [{"ev": "interim", "text": "hello"}, {"ev": "interim", "text": "hello scout"},
               {"ev": "final", "text": "Hello Scout."}, {"ev": "say", "words": 4},
               {"ev": "say", "text": "Hi there.", "words": 2}, {"ev": "late", "text": "x"},
               {"ev": "interim", "text": "and"}, {"ev": "interim", "text": "and then"}]
    rows = to_rows(parse(("\n".join(json.dumps(r) for r in fixture) + "\n").encode()))
    post(rows)
    want = [{"ch": "me", "text": "Hello Scout.", "final": True},
            {"ch": "agent", "final": True, "text": "(spoke 4 words)"},
            {"ch": "agent", "final": True, "text": "Hi there."},
            {"ch": "me", "text": "x", "final": True},
            {"ch": "me", "text": "and then", "final": False}]
    ok = rows == want and got == [{"rows": want}]
    print("PASS" if ok else f"FAIL\n{rows}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else run())
