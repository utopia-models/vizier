#!/usr/bin/env python3
"""A minimal copilot loop over the HTTP API: read new transcript lines, and when THEM asks a question, post bullets.

Bring your own brain: `suggest()` is where your model call goes. This example answers from a tiny FAQ so it runs with
no API key. Standard library only.  VIZIER_URL (default http://127.0.0.1:8786), VIZIER_TOKEN (optional).
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

V = os.environ.get("VIZIER_URL", "http://127.0.0.1:8786").rstrip("/")
TOKEN = os.environ.get("VIZIER_TOKEN", "")
FAQ = {"rate": ["My rate is 90 an hour.", "What budget did you have in mind?"],
       "start": ["I can start next week.", "When do you need the first milestone?"]}


def call(method, path, body=None):
    req = urllib.request.Request(V + path, json.dumps(body).encode() if body is not None else None, method=method)
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", "Bearer " + TOKEN)
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def suggest(question: str) -> list[str] | None:
    q = question.lower()
    return next((b for k, b in FAQ.items() if k in q), None) if q.rstrip().endswith("?") else None


def main():
    seen, call_id = 0, None
    while True:
        t = call("GET", f"/transcript?since={seen}")
        if t["call_id"] != call_id:
            call_id, seen = t["call_id"], 0
            continue
        for line in t["lines"]:
            seen = line["seq"]
            if line["ch"] == "them" and (b := suggest(line["text"])):
                call("POST", "/bullets", {"bullets": b, "for": line["text"], "t_audio_end": line.get("t_audio_end")})
        time.sleep(0.3)


if __name__ == "__main__":
    main()
