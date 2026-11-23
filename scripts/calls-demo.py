#!/usr/bin/env python3
"""Dry run for the Calls tab: starts a fake call and posts a few made-up lines and two cards.
Needs VIZIER_WRITE_SECRET (the write secret) in the environment; VIZIER_URL defaults to http://127.0.0.1:8786."""
import json, os, sys, time, urllib.request

URL = os.environ.get("VIZIER_URL", "http://127.0.0.1:8786").rstrip("/")
SECRET = os.environ.get("VIZIER_WRITE_SECRET", "")
if not SECRET:
    sys.exit("set VIZIER_WRITE_SECRET")


def post(path, body):
    req = urllib.request.Request(URL + path, json.dumps(body).encode(), {"Content-Type": "application/json", "X-Vizier-Write": SECRET})
    return json.load(urllib.request.urlopen(req, timeout=5))


post("/calls/start", {"title": "Demo call (fake)"})
post("/calls/transcript", {"speaker": "ME", "text": "Thanks for making the time today.", "final": True})
a = post("/calls/transcript", {"speaker": "THEM", "text": "So how long would a rollout", "final": False})
time.sleep(1)
post("/calls/transcript", {"speaker": "THEM", "text": "So how long would a rollout take for a small team?", "final": True, "id": a["id"]})
post("/calls/card", {"asked": "Rollout time for a small team", "say": "About two weeks end to end.", "next": "How many people would use it?"})
post("/calls/transcript", {"speaker": "ME", "text": "Around two weeks. How many people would use it?", "final": True})
post("/calls/card", {"asked": "Team size", "say": "Start with five seats, grow later.", "next": "Who signs off on the budget?"})
print("posted")
