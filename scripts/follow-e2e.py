#!/usr/bin/env python3
"""follow-e2e.py: the real app, end to end, on a recorded read. Starts this repo's server on the script,
opens app/ui/index.html in headless Chromium on the SCRIPT tab, and feeds the recording to voice follow as its mic
(getUserMedia returns the file, played in real time; every other capture gets silence). Records which word the app
highlights and when, then grades it with follow-bench.mjs --timeline.

  python3 scripts/follow-e2e.py --script S.md --wav read.wav --truth T.json [--out timeline.json]

Needs: `pip install playwright aiohttp` + `playwright install chromium`, node, and scripts/fetch-voice.sh run once.
The browser's Vosk is the same WebAssembly build the app ships, so this measures the recognizer's real speed on THIS
machine: run it on a quiet box (on a box at load 40+ the decoder falls behind real time and every number moves).
"""
import argparse, json, os, socket, subprocess, sys, tempfile, threading, time, http.server, functools
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser()
ap.add_argument("--script", required=True); ap.add_argument("--wav", required=True); ap.add_argument("--truth", required=True)
ap.add_argument("--out", default=None); ap.add_argument("--lines", default=None, help="spoken lines JSON for the grader (default: parsed from --script)")
a = ap.parse_args()
from playwright.sync_api import sync_playwright


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


data = tempfile.mkdtemp(prefix="vizier-e2e-")
sport = free_port()
env = {**os.environ, "VIZIER_DATA": data, "VIZIER_SCRIPTS": str(Path(a.script).resolve())}
srv = subprocess.Popen([sys.executable, str(ROOT / "server" / "vizier.py"), "--port", str(sport)], env=env, cwd=ROOT / "server",
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
ui = ROOT / "app" / "ui"
wav = Path(a.wav).resolve()


class H(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        return str(wav) if path.startswith("/__read.wav") else super().translate_path(path)
    def log_message(self, *x):
        pass


web = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(H, directory=str(ui)))
threading.Thread(target=web.serve_forever, daemon=True).start()
for _ in range(50):
    try:
        socket.create_connection(("127.0.0.1", sport), 0.2).close(); break
    except OSError:
        time.sleep(0.2)

INIT = """
(() => {
  const real = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  window.__followLog = []; window.__audioStart = null; window.__ended = false;
  navigator.mediaDevices.getUserMedia = async (c) => {
    const au = c && c.audio;
    const isFollow = au && typeof au === "object" && au.noiseSuppression === true && au.echoCancellation === false;
    const ctx = new AudioContext(); const dst = ctx.createMediaStreamDestination();
    if (!isFollow) return dst.stream;   // Ears / self-check: silence
    const buf = await ctx.decodeAudioData(await (await fetch("/__read.wav")).arrayBuffer());
    const src = ctx.createBufferSource(); src.buffer = buf; src.connect(dst);
    const t0 = ctx.currentTime + 0.3; src.start(t0);
    window.__audioStart = performance.now() + 300; src.onended = () => { window.__ended = true; };
    return dst.stream;
  };
})();
"""
with sync_playwright() as p:
    b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--use-fake-ui-for-media-stream"])
    pg = b.new_page(viewport={"width": 1280, "height": 680})
    pg.add_init_script(INIT)
    def cors(route):   # the server allows only its own origins; the page is served from another port
        try:
            r = route.fetch()
            route.fulfill(response=r, headers={**r.headers, "access-control-allow-origin": "*"})
        except Exception:  # noqa: BLE001 (the page closing mid-request)
            pass
    pg.route(f"http://127.0.0.1:{sport}/**", cors)
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.goto(f"http://127.0.0.1:{web.server_port}/index.html?server=http://127.0.0.1:{sport}")
    pg.wait_for_function("typeof takes !== 'undefined' && takes.length > 0", timeout=30000)
    pg.keyboard.press("s")   # the SCRIPT tab
    pg.wait_for_function("window.__audioStart !== null", timeout=120000)   # follow started: model loaded, mic open
    # every change of the highlighted word, on the audio's clock
    pg.evaluate("""() => { let last = -1; const tick = () => {
        const i = follow && follow.tracker ? follow.tracker.index() : -1;
        const el = document.querySelector('#text .w.now');
        if (i !== last && el) { last = i; window.__followLog.push([(performance.now() - window.__audioStart) / 1000, i]); }
        if (!window.__ended) requestAnimationFrame(tick); }; requestAnimationFrame(tick); }""")
    pg.wait_for_function("window.__ended", timeout=3600 * 1000, polling=1000)
    log = pg.evaluate("window.__followLog")
    state = pg.evaluate("({ wps: follow.tracker.wps, lag: follow.tracker.lag, reached: follow.tracker.index(), of: follow.tracker.words.length, part: t + 1, label: document.getElementById('kfollow').textContent })")
    b.close()
srv.terminate()
out = Path(a.out or tempfile.mktemp(suffix=".json")); out.write_text(json.dumps(log))
lines = a.lines
if not lines:
    sys.path.insert(0, str(ROOT / "server")); import vizier  # noqa: E402
    lines = tempfile.mktemp(suffix=".json")
    Path(lines).write_text(json.dumps([l for k in vizier.parse_takes(Path(a.script).read_text()) for l in k["lines"]]))
r = subprocess.run(["node", str(ROOT / "scripts" / "follow-bench.mjs"), "--lines", lines, "--wav", str(wav), "--truth", a.truth,
                    "--timeline", str(out), "--json"], capture_output=True, text=True)
res = json.loads(r.stdout); res.update(app=state, timeline=str(out), changes=len(log), console_errors=errs[:5])
print(json.dumps(res, indent=1))
