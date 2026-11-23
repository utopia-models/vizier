"""The clip player (the deployment's clip player ask): on every Down the laptop PLAYS that row's pre-recorded clip full
screen, so the reader SEES the cursor, clicks, scroll and typing move; nothing depends on live Chrome focus or OS input.

  GET  /player?slug=<slug>            the kiosk page (player.html): one <video>, follows GET /events, plays row-<i>.webm
  GET  /clips/<slug>/<row-N.webm>     the clip files (VIZIER_CLIPS, default $VIZIER_DATA/clips); 404 = "no clip"
  POST /player/log {event,row,...}    the page reports each clip start / missing card; kept in memory + player.jsonl
  GET  /player/log[?n=50]             the last rows (the test reads it)
  GET  /player/banner                 {"banner": null | {"text","row","at"}}; GET /state carries the same key

A missing clip is never a silent still screen: the page shows a "no clip for row N" card AND the banner is set here,
which the Vizier app draws red (app/ui/index.html #clipbanner). The banner clears on the next row that has its clip.
The player window is a normal capturable window; Vizier itself stays WDA_EXCLUDEFROMCAPTURE and is never touched here.
"""
import json
import os
import re
import time
from pathlib import Path

from aiohttp import web

HERE = Path(__file__).resolve().parent
LOG: list = []                       # newest last, capped
BANNER: dict = {"banner": None}
CLIP_RE = re.compile(r"^row-\d+\.(webm|mp4)$")
SLUG_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def clips_dir() -> Path:
    return Path(os.environ.get("VIZIER_CLIPS") or (Path(os.environ.get("VIZIER_DATA", Path.home() / ".vizier")) / "clips"))


def log_path() -> Path:
    return Path(os.environ.get("VIZIER_PLAYER_LOG") or (Path(os.environ.get("VIZIER_DATA", Path.home() / ".vizier")) / "player.jsonl"))


def banner_view():
    return BANNER["banner"]


async def player_page(_):
    return web.FileResponse(HERE / "player.html", headers={"Cache-Control": "no-store"})


async def clip_file(req):
    slug, name = req.match_info["slug"], req.match_info["name"]
    if not SLUG_RE.match(slug) or not CLIP_RE.match(name):
        raise web.HTTPNotFound(text="no such clip")
    p = clips_dir() / slug / name
    if not p.is_file():
        raise web.HTTPNotFound(text=f"no clip {slug}/{name}")
    return web.FileResponse(p, headers={"Cache-Control": "max-age=300"})   # cacheable: the page warms the next rows


async def player_log(req):
    if req.method == "POST":
        try:
            b = await req.json()
        except ValueError:
            raise web.HTTPBadRequest(text="json body")
        ev = str(b.get("event", ""))[:40]
        row = {"at": time.time(), "event": ev, "row": b.get("row"), "seq": b.get("seq"), "clip": str(b.get("clip", ""))[:160],
               "ms_from_cursor": b.get("ms_from_cursor"), "server_ms_from_cursor": b.get("server_ms_from_cursor")}
        LOG.append(row)
        del LOG[:-500]
        if ev == "missing":
            BANNER["banner"] = {"text": f"no clip for row {b.get('row')}", "row": b.get("row"), "at": row["at"]}
        elif ev == "started":
            BANNER["banner"] = None
        try:
            lp = log_path()
            lp.parent.mkdir(parents=True, exist_ok=True)
            with open(lp, "a") as f:
                f.write(json.dumps(row) + "\n")
        except OSError:
            pass
        return web.json_response({"ok": True})
    n = max(1, min(500, int(req.query.get("n", 50))))
    return web.json_response({"ok": True, "rows": LOG[-n:]})


async def player_banner(_):
    return web.json_response({"ok": True, "banner": BANNER["banner"]})


def register(router):
    router.add_get("/player", player_page)
    router.add_get("/clips/{slug}/{name}", clip_file)
    router.add_get("/player/log", player_log)
    router.add_post("/player/log", player_log)
    router.add_get("/player/banner", player_banner)
