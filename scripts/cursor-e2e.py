#!/usr/bin/env python3
"""cursor-e2e.py: the real page, real key presses, the real server. Opens app/ui/index.html in headless Chromium on the
Speaker Script tab, presses ArrowDown 20 times, and times each press until GET /state shows the new line and until the
GET /events stream delivered it (the stream is read in the page, so both clocks are the browser's). Prints p50/p95 in ms.

  python3 scripts/cursor-e2e.py [--trials 20]        needs: pip install playwright aiohttp; playwright install chromium"""
import argparse, functools, http.server, json, os, socket, statistics, subprocess, sys, tempfile, threading, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser(); ap.add_argument("--trials", type=int, default=20); a = ap.parse_args()
from playwright.sync_api import sync_playwright


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


tmp = Path(tempfile.mkdtemp(prefix="vizier-cursor-"))
(tmp / "speaker-script.md").write_text("# Cursor test\n\n## 1 · open\n" + "\n".join(f"Line number {i} of the script." for i in range(1, 41)) + "\n")
sport = free_port(); WEBPORT = free_port()
env = {**os.environ, "VIZIER_ALLOWED_ORIGINS": "http://127.0.0.1:%d" % WEBPORT, "VIZIER_DATA": str(tmp / "data"), "VIZIER_SPEAKER_SCRIPT": str(tmp / "speaker-script.md")}
srv = subprocess.Popen([sys.executable, str(ROOT / "server" / "vizier.py"), "--port", str(sport)], env=env, cwd=ROOT / "server",
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *x): pass


web = http.server.ThreadingHTTPServer(("127.0.0.1", WEBPORT), functools.partial(H, directory=str(ROOT / "app" / "ui")))
threading.Thread(target=web.serve_forever, daemon=True).start()
for _ in range(50):
    try: socket.create_connection(("127.0.0.1", sport), 0.2).close(); break
    except OSError: time.sleep(0.2)
S = f"http://127.0.0.1:{sport}"
try:
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None); pg = b.new_page(viewport={"width": 1280, "height": 680})
        pg.goto(f"http://127.0.0.1:{web.server_port}/index.html?server={S}")
        pg.wait_for_selector("#p-tscript .row[data-i]", state="attached", timeout=15000)
        pg.evaluate("goTab('tscript')"); pg.wait_for_selector("#p-tscript .row.sel", timeout=5000)
        # the stream is read here (the page's origin is not the server's, so the browser could not); both sides use the epoch clock
        import urllib.request
        EV = {}
        def reader():   # a raw socket: urllib buffers a chunked stream
            k = socket.create_connection(("127.0.0.1", sport)); k.sendall(b"GET /events HTTP/1.1\r\nHost: x\r\n\r\n"); buf = b""
            while True:
                d = k.recv(65536)
                if not d: return
                buf += d
                while b"\n\n" in buf:
                    blk, buf = buf.split(b"\n\n", 1)
                    m = [l for l in blk.split(b"\n") if l.startswith(b"data: ")]
                    if m:
                        j = json.loads(m[0][6:]); EV.setdefault(j["current_line_index"], time.time() * 1000)
        threading.Thread(target=reader, daemon=True).start()
        def wait(cond, to=5.0):
            t = time.time()
            while not cond():
                if time.time() - t > to: raise SystemExit("timeout waiting for the cursor: " + str(sorted(EV)) + " " + str(i if "i" in globals() else "-"))
                time.sleep(0.0005)
        wait(lambda: 0 in EV)   # the page has reported its first line
        ev_ms, st_ms = [], []
        for i in range(a.trials):
            pg.evaluate("window.__t0 = null; document.addEventListener('keydown', e => { if (e.key === 'ArrowDown') window.__t0 = performance.timeOrigin + performance.now(); }, { once: true, capture: true })")
            pg.keyboard.press("ArrowDown")
            wait(lambda: (i + 1) in EV)
            t0 = pg.evaluate("window.__t0"); ev_ms.append(EV[i + 1] - t0)
            def shown():
                with urllib.request.urlopen(S + "/state") as r: return json.loads(r.read())["current_line_index"] == i + 1
            wait(shown); st_ms.append(time.time() * 1000 - t0)
            time.sleep(0.15)
        last = json.loads(urllib.request.urlopen(S + "/state").read())
        b.close()
finally:
    srv.terminate()
q = lambda v, f: sorted(v)[min(len(v) - 1, int(round(f * (len(v) - 1))))]
print(json.dumps({"trials": a.trials, "event_ms": {"p50": round(statistics.median(ev_ms), 1), "p95": round(q(ev_ms, .95), 1), "max": round(max(ev_ms), 1)},
                  "state_ms": {"p50": round(statistics.median(st_ms), 1), "p95": round(q(st_ms, .95), 1), "max": round(max(st_ms), 1)},
                  "final": {k: last[k] for k in ("seq", "current_line_index", "current_line_text", "total_lines", "script_name")}}, indent=1))
