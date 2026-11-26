#!/usr/bin/env python3
"""ready-check.py: open every queue item in the real Vizier page, screenshot it with its text showing, and report the items that
do not render a script (the "No script" screen). Exit 1 when any fails; the failing ids are printed as JSON so the caller can pull them.
  python3 scripts/ready-check.py --server http://127.0.0.1:18787 --shots /tmp/ready [--secret-file F]  (needs playwright)"""
import argparse, json, sys, urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:18787")
    ap.add_argument("--shots", default="/tmp/vizier-ready")
    ap.add_argument("--chromium", help="browser executable when playwright's own build is not installed")
    a = ap.parse_args()
    shots = Path(a.shots); shots.mkdir(parents=True, exist_ok=True)
    head = json.load(urllib.request.urlopen(a.server + "/queue", timeout=10))
    bad = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=a.chromium) if a.chromium else p.chromium.launch()
        page = b.new_page(viewport={"width": 1280, "height": 800})
        page.goto((ROOT / "app" / "ui" / "index.html").as_uri() + "?server=" + a.server)
        page.wait_for_selector("#qnext", state="visible", timeout=10000)
        for it in head["items"]:
            urllib.request.urlopen(urllib.request.Request(a.server + "/queue/go", json.dumps({"id": it["id"]}).encode(),
                                                          {"Content-Type": "application/json"}), timeout=10)
            page.wait_for_timeout(400)
            n = page.locator("#p-tscript .row .v").count()
            txt = page.inner_text("#p-tscript")
            ok = n > 0 and "No script" not in txt and bool((it.get("meta") or {}).get("label"))
            page.screenshot(path=str(shots / (it["id"].replace(":", "_") + ".png")))
            if not ok:
                bad.append({"id": it["id"], "rows": n, "label": (it.get("meta") or {}).get("label")})
        b.close()
    print(json.dumps({"checked": len(head["items"]), "failed": bad}))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
