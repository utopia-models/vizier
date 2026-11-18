#!/usr/bin/env python3
"""live-read.py: read the RUNNING Vizier window over CDP (DevTools on 127.0.0.1:9340, VIZIER_CDP_PORT) and grade the
readable-prompter acceptance (issue 14087): script type <= 28 px, the window <= 1/3 of the screen, 0 outline labels
("Coach on part", "Build") in the DOM. Read-only: it evaluates one expression and changes nothing on the screen.

    python3 scripts/live-read.py --host eth           # ssh -L to the Windows box, then read
    python3 scripts/live-read.py --cdp 127.0.0.1:9340 # a port you already forwarded

Prints one JSON object; exit 0 = every check passes, 1 = a check fails, 2 = no window to read.
"""
import argparse, asyncio, json, socket, subprocess, sys, time

import aiohttp

READ = r"""(() => { const t = document.getElementById('text'), c = document.body.cloneNode(true);
  c.querySelectorAll('script,style').forEach(x => x.remove()); const h = c.textContent, r = t.getBoundingClientRect();   // text, so id="build" is not a label
  return { mode: document.body.dataset.mode || null, font_px: parseFloat(getComputedStyle(t).fontSize),
    window: [outerWidth, outerHeight], screen: [screen.availWidth, screen.availHeight],
    window_frac_of_screen: +(outerWidth * outerHeight / (screen.availWidth * screen.availHeight)).toFixed(3),
    script_pane_frac_of_window: +(r.width * r.height / (innerWidth * innerHeight)).toFixed(3),
    coach_labels: (h.match(/Coach on part/gi) || []).length, build_labels: (h.match(/\bbuild\b/gi) || []).length,
    lines_shown: [...t.querySelectorAll('p')].filter(p => { const b = p.getBoundingClientRect(); return b.bottom > r.top && b.top < r.bottom; }).length }; })()"""


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


async def read(cdp):
    async with aiohttp.ClientSession() as http:
        pages = await (await http.get(f"http://{cdp}/json", timeout=aiohttp.ClientTimeout(total=10))).json()
        page = next((p for p in pages if p.get("type") == "page"), None)
        if not page:
            return None
        async with http.ws_connect(page["webSocketDebuggerUrl"], max_msg_size=0) as ws:
            await ws.send_json({"id": 1, "method": "Runtime.evaluate", "params": {"expression": READ, "returnByValue": True}})
            async for m in ws:
                j = json.loads(m.data)
                if j.get("id") == 1:
                    return j["result"]["result"].get("value")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", help="ssh host of the Windows box running vizier.exe")
    ap.add_argument("--cdp", help="host:port of an already reachable DevTools endpoint")
    ap.add_argument("--port", type=int, default=9340, help="the app's VIZIER_CDP_PORT on that box")
    a = ap.parse_args()
    tun = None
    if a.cdp:
        cdp = a.cdp
    elif a.host:
        lp = free_port(); cdp = f"127.0.0.1:{lp}"
        tun = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-N", "-L",
                                f"{lp}:127.0.0.1:{a.port}", a.host])
        time.sleep(3)
    else:
        ap.error("--host or --cdp")
    try:
        v = asyncio.run(read(cdp))
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}", "cdp": cdp})); return 2
    finally:
        if tun: tun.terminate()
    if not v:
        print(json.dumps({"error": "no page on the DevTools endpoint", "cdp": cdp})); return 2
    v["checks"] = {"font_le_28px": v["font_px"] <= 28, "window_le_third_of_screen": v["window_frac_of_screen"] <= 1 / 3,
                   "no_outline_labels": v["coach_labels"] == 0 and v["build_labels"] == 0}
    print(json.dumps(v))
    return 0 if all(v["checks"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
