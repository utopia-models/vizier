#!/usr/bin/env python3
"""queue-e2e.py: the real app page in headless Chromium against a real server: N scripts are loaded, then the Next button and
the Right key are used, and the time from the input to the new script being drawn is measured. Exits non-zero over 1000 ms.
  pip install aiohttp playwright; python3 scripts/queue-e2e.py [--scripts 40] [--server URL --secret-file F]"""
import argparse, json, os, statistics, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scripts", type=int, default=40)
    ap.add_argument("--presses", type=int, default=20)
    ap.add_argument("--port", type=int, default=18797)
    a = ap.parse_args()
    d = Path(tempfile.mkdtemp(prefix="vizier-queue-"))
    (d / "secret").write_text("e2e\n")
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("VIZIER_")}, "VIZIER_DATA": str(d / "data"),
           "VIZIER_SPEAKER_SCRIPT": str(d / "speaker.md"), "VIZIER_WRITE_SECRET_FILE": str(d / "secret")}
    srv = subprocess.Popen([sys.executable, str(ROOT / "server" / "vizier.py"), "--port", str(a.port)], env=env, cwd=d)
    base = f"http://127.0.0.1:{a.port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/health", timeout=1); break
            except OSError:
                time.sleep(0.1)
        items = [{"id": f"job-{i}", "title": f"Job number {i}", "text": f"# Speaker script: job {i}\n## [Reader] Intro\n"
                  + "".join(f"job {i} line {k} read out loud\n" for k in range(30))} for i in range(a.scripts)]
        urllib.request.urlopen(urllib.request.Request(base + "/queue", json.dumps({"items": items}).encode(),
                               {"X-Vizier-Write": "e2e", "Content-Type": "application/json"}), timeout=10)
        ms = []
        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page(viewport={"width": 700, "height": 800})
            page.goto((ROOT / "app" / "ui" / "index.html").as_uri() + "?server=" + base)
            page.wait_for_selector("#qnext", state="visible", timeout=10000)
            for n in range(a.presses):
                want = f"job {n} line 0 read out loud"
                t0 = time.perf_counter()
                if n % 2:
                    page.keyboard.press("ArrowRight")
                else:
                    page.click("#qnext")
                page.wait_for_function("w => { const r = document.querySelector('#p-tscript .row.sel .v'); return r && r.textContent === w }",
                                       arg=want, timeout=5000, polling="raf")
                ms.append((time.perf_counter() - t0) * 1000)
                assert page.inner_text("#qpos").startswith(f"{n + 1} / {a.scripts}")
                if n == 3:   # a 2 s poll must not put an older script back
                    page.wait_for_timeout(2500)
                    assert page.inner_text("#p-tscript .row.sel .v") == want
            if os.environ.get("QUEUE_SHOT"):
                page.screenshot(path=os.environ["QUEUE_SHOT"])
            b.close()
        print(json.dumps({"scripts_loaded": a.scripts, "presses": len(ms), "ms_p50": round(statistics.median(ms), 1),
                          "ms_max": round(max(ms), 1)}))
        return 0 if max(ms) < 1000 else 1
    finally:
        srv.terminate(); srv.wait(timeout=5)


if __name__ == "__main__":
    sys.exit(main())
