#!/usr/bin/env bash
# A whole call from the shell: open a call, two lines, bullets, a status, the transcript, a prompter script.
# Run the server first (server/vizier.py) and open http://127.0.0.1:8786/ to watch it.
set -euo pipefail
V=${VIZIER_URL:-http://127.0.0.1:8786}
H=(-H 'Content-Type: application/json')
[ -n "${VIZIER_TOKEN:-}" ] && H+=(-H "Authorization: Bearer $VIZIER_TOKEN")
post() { curl -fsS "${H[@]}" "$V$1" -d "$2"; echo; }
post /call/start '{"title": "demo"}'
post /lines '{"ch": "them", "text": "Have you shipped Rust in production?"}'
post /lines '{"ch": "me", "text": "I have, for about two years."}'
post /bullets '{"bullets": ["Two years of Rust in production, a trading engine", "Ask what they are building in Rust"]}'
post /status '{"text": "demo running"}'
curl -fsS "$V/transcript?format=text"
post /script '{"markdown": "# Demo\n## 1 · Hook\nThis line scrolls past the reading line.\n## 2 · Close\nThanks for watching."}'
