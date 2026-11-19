#!/usr/bin/env python3
"""vizier.py: the Vizier server. A display, driven over HTTP.

Vizier does no listening, no speech-to-text and no language-model work. It shows what is posted to it: the live
call transcript (ME / THEM lines), short bullets to say next, a status line, and a teleprompter script. Whatever
does the listening and the thinking (an agent, a LiveKit room, a script, a person with curl) posts to this API, and
every window on /feed redraws. The full contract is docs/openapi.yaml; connectors/ has a LiveKit and an MCP client.

  HTTP  GET  /health                         server state (the desktop app polls it)
        POST /call/start  {"call_id"?, "title"?}     POST /call/end
        POST /lines       {"ch": "me"|"them", "text", "final"?: true, "t_audio_end"?}   one line or {"lines": [...]}
        GET  /transcript  ?since=<seq>       the current call's final lines
        POST /bullets     {"bullets": ["..", ".."], "for"?: "<what they answer>", "t_audio_end"?}
        POST /status      {"text"}
        POST /script      {"markdown"} | {"path"}   the prompter's script (one `## ` block per take)
        GET  /prompter/scripts · /prompter/context · /prompter/note    POST /prompter/note {"text"}
        POST /prompter/record {"take"} | {"stop": true}   copy a camera stream into a take file (ffmpeg, optional)
        GET  /buildlog    tail of a plain-text log      GET /openapi.yaml
        GET  /settings · POST /settings {"voice"} | {"wake"}   the voice agent's settings (VIZIER_VOICE_URL)
        GET  /desks · POST /desks/open {"id"}   desks to open (VIZIER_DESKS_CMD, VIZIER_DESK_OPEN_CMD)
        GET  /agents      the team's liveness (VIZIER_AGENTS_DIR)
        GET  /speaker-script  his spoken beats, read-only, no highlight (VIZIER_SPEAKER_SCRIPT); both tabs mark edited lines
        POST /speaker-script, /horus-script  {"text"} | raw text: replace the live file (X-Vizier-Write secret); GET /state: it all in one JSON
        GET  /horus-script  the agent's cue table + the current cue (VIZIER_HORUS_CUES, VIZIER_HORUS_STATE); read-only
  WS    /feed     ← {"type": "status"|"line"|"interim"|"suggestion"|"delta"|"done"|"cancel"|"script", ..., "t"}

Configuration is environment variables, optionally loaded from a KEY=VALUE file (see vizier.env.example):
`--config <file>`, else $VIZIER_CONFIG, else ./vizier.env if it exists. A variable already set wins over the file.
Set VIZIER_TOKEN to require `Authorization: Bearer <token>` on every POST.

  vizier.py [--host 127.0.0.1] [--port 8786]
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import re
import shlex
import time
from pathlib import Path

from aiohttp import web

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def load_config(path: str | os.PathLike | None) -> Path | None:
    """Load KEY=VALUE lines (blank lines and # comments skipped, optional quotes stripped, ~ and $VARS expanded)
    into os.environ without overriding anything already set. Returns the file read, or None."""
    p = Path(path) if path else None
    if p is None or not p.is_file():
        return None
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip().removeprefix("export ").strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        os.environ.setdefault(k, os.path.expandvars(os.path.expanduser(v)))
    return p


def _config_arg() -> str | None:
    import sys
    for i, a in enumerate(sys.argv):
        if a == "--config" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith("--config="):
            return a.split("=", 1)[1]
    return os.environ.get("VIZIER_CONFIG") or ("vizier.env" if Path("vizier.env").is_file() else None)


CONFIG_FILE = load_config(_config_arg()) if __name__ == "__main__" else None
DATA = Path(os.environ.get("VIZIER_DATA", Path.home() / ".vizier"))
LOG = Path(os.environ.get("VIZIER_LOG", DATA / "events.jsonl"))
TOKEN = os.environ.get("VIZIER_TOKEN", "")
SCRIPTS = Path(os.environ.get("VIZIER_SCRIPTS", DATA / "scripts.md"))
POSTED_SCRIPT = DATA / "posted-script.md"     # where POST /script {"markdown"} lands
POSTED_CONTEXT = DATA / "posted-context.md"   # its CONTEXT tab: carried over from the script it replaces
# The script the prompter shows survives a restart (the deployment's issue 13257): POST /script records its choice here and
# make_app() restores it, so a restart never puts VIZIER_SCRIPTS (a deploy-time default, often a past client's
# script) back on top over what the agent last showed. {"clear": true} records NO_SCRIPT: the honest empty state.
CURRENT_SCRIPT = DATA / "current-script"
NO_SCRIPT = DATA / "no-script.md"             # never written: pointing here shows "no script yet"
TALK_SCRIPTS = Path(os.environ.get("VIZIER_TALK_SCRIPTS", DATA / "talk-scripts"))   # /prompter/scripts?file=<name>
BRIEFS = Path(os.environ.get("VIZIER_BRIEFS", DATA / "briefs"))                     # /prompter/scripts?brief=<slug>
TAKES = Path(os.environ.get("VIZIER_TAKES", DATA / "takes"))
CAM = os.environ.get("VIZIER_CAM", "")       # an RTSP camera stream a take copies; empty = recording off
# A shell command whose exit 0 means "a call wants the laptop camera now" (for example: the meeting browser is open).
# /health reports it as cam_wanted and the desktop app publishes the camera by itself while it is true, so nobody has
# to click Join call (and the meeting never shows "camera might be blocked" over a black frame). Empty = always false.
CAM_WANTED_CMD = os.environ.get("VIZIER_CAM_WANTED_CMD", "")
CAM_WANTED_TTL = float(os.environ.get("VIZIER_CAM_WANTED_TTL", "2"))
BUILD_LOG = Path(os.environ.get("VIZIER_BUILD_LOG", DATA / "build-log.md"))
MAX_LINES = int(os.environ.get("VIZIER_MAX_LINES", "5000"))   # a call keeps at most this many lines in memory
# The agent's own settings, desks and team (the deployment's issue 13798: "Scout == Vizier", every setting in Vizier). All optional:
# unset = the panel says it is not configured, never a made-up value.
VOICE_URL = os.environ.get("VIZIER_VOICE_URL", "").rstrip("/")      # the voice agent's control API (GET /health, GET
                                                                     # /voices, POST /voice, POST /address)
DESKS_CMD = os.environ.get("VIZIER_DESKS_CMD", "")      # prints a JSON list [{"id", "name", "agent"?, "detail"?}]
DESK_OPEN_CMD = os.environ.get("VIZIER_DESK_OPEN_CMD", "")   # opens one desk; "{id}" is replaced by the desk's id
AGENTS_DIR = os.environ.get("VIZIER_AGENTS_DIR", "")    # one <agent>.json per agent (its liveness), read on each GET
# A VSL take, started and stopped by the reader (Alt+R in the app, the SETTINGS "Recording" row, or the agent's own
# "record" command: all three POST /vsl). The command runs in the background and the POST answers at once, so the
# key acts in milliseconds while the take's own start/stop (OBS, the desk, the review) takes what it takes.
HORUS_CUES = os.environ.get("VIZIER_HORUS_CUES", "")    # cue-list files, first that exists wins (os.pathsep-separated): a markdown table
SPEAKER_SCRIPT = os.environ.get("VIZIER_SPEAKER_SCRIPT", "")  # his script (speaker-script.md): the Speaker Script tab shows its spoken lines
HORUS_STATE = os.environ.get("VIZIER_HORUS_STATE", "")  # vsl-follow's state.json: {"beat": "b3", "at": ...}, the current cue
VSL_START_CMD = os.environ.get("VIZIER_VSL_START_CMD", "")   # e.g. "vsl start {name}"; {name} is shell-quoted
VSL_STOP_CMD = os.environ.get("VIZIER_VSL_STOP_CMD", "")     # e.g. "vsl stop"
VSL_STATE = os.environ.get("VIZIER_VSL_STATE", "")           # a file that exists while a take records (vsl's current.json)
VSL_NAME = os.environ.get("VIZIER_VSL_NAME", "")             # the take's name; empty = the prompter script's name
CHANNELS = ("me", "them")


def log_event(ev: dict) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as f:
            f.write(json.dumps(ev) + "\n")
    except OSError:
        pass


class Feed:
    """Every connected /feed WebSocket; send() stamps `t` and drops a client that cannot take the frame."""

    def __init__(self):
        self.clients: set[web.WebSocketResponse] = set()

    async def send(self, frame: dict) -> dict:
        frame = {**frame, "t": time.time()}
        for ws in list(self.clients):
            try:
                await ws.send_json(frame)
            except Exception:  # noqa: BLE001
                self.clients.discard(ws)
        return frame


class Call:
    """One call: an id, a title, and its final lines in order. Nothing here listens; lines arrive over POST /lines."""

    def __init__(self, call_id: str, title: str = ""):
        self.id, self.title, self.t0 = call_id, title, time.time()
        self.lines: list[dict] = []
        self.seq = 0
        self.bullets = 0

    def add(self, ch: str, text: str, t: float, **extra) -> dict:
        self.seq += 1
        line = {"seq": self.seq, "ch": ch, "text": text, "t": t, **{k: v for k, v in extra.items() if v is not None}}
        self.lines.append(line)
        del self.lines[:-MAX_LINES]
        return line


class State:
    """The server's mutable state, one object on the app (aiohttp freezes the app's own keys once it starts)."""

    def __init__(self, feed: Feed, call: Call | None, script: Path):
        self.feed, self.call, self.script = feed, call, script


STATE: web.AppKey[State] = web.AppKey("vizier", State)


def S(app: web.Application) -> State:
    return app[STATE]


def new_call_id() -> str:
    return time.strftime("call-%Y%m%dT%H%M%S")


# ---------- auth + body helpers ----------

def authorized(req) -> bool:
    if not TOKEN:
        return True
    got = req.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    return hmac.compare_digest(got, TOKEN)


async def body(req) -> dict:
    if not authorized(req):
        raise web.HTTPUnauthorized(text=json.dumps({"error": "bad or missing bearer token"}), content_type="application/json")
    try:
        b = await req.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise web.HTTPBadRequest(text=json.dumps({"error": "body is not JSON"}), content_type="application/json")
    if not isinstance(b, dict):
        raise web.HTTPBadRequest(text=json.dumps({"error": "body must be a JSON object"}), content_type="application/json")
    return b


def bad(msg: str, status: int = 400):
    return web.json_response({"error": msg}, status=status)


# ---------- the call: start, end, lines, transcript, bullets, status ----------

async def ensure_call(app, call_id: str | None = None, title: str = "") -> Call:
    if S(app).call is None:
        S(app).call = Call(call_id or new_call_id(), title)
        await S(app).feed.send({"type": "status", "text": f"call {S(app).call.id} live"})
        log_event({"t": time.time(), "ev": "call_start", "call": S(app).call.id})
    return S(app).call


async def call_start(req):
    b = await body(req)
    app = req.app
    if S(app).call:
        await end_call(app)
    c = await ensure_call(app, str(b.get("call_id") or "") or None, str(b.get("title") or ""))
    return web.json_response({"call_id": c.id})


async def end_call(app) -> Call | None:
    c = S(app).call
    S(app).call = None
    if c:
        log_event({"t": time.time(), "ev": "call_end", "call": c.id, "lines": len(c.lines)})
        await S(app).feed.send({"type": "status", "text": "call ended"})
    return c


async def call_end(req):
    await body(req)
    c = await end_call(req.app)
    return web.json_response({"ok": True, "call_id": c.id if c else None, "lines": len(c.lines) if c else 0})


async def lines(req):
    """POST /lines: one {"ch", "text", "final"?} or {"lines": [...]}. A final line is kept in the transcript and
    drawn; a non-final one ("final": false) is the live interim under it. A line with no call open starts one."""
    b = await body(req)
    items = b["lines"] if isinstance(b.get("lines"), list) else [b]
    out = []
    for it in items:
        if not isinstance(it, dict):
            return bad("each line must be an object")
        ch, text = str(it.get("ch", "")).lower(), str(it.get("text", "")).strip()
        if ch not in CHANNELS:
            return bad('"ch" must be "me" or "them"')
        if not text:
            continue
        if it.get("final", True) is False:
            await S(req.app).feed.send({"type": "interim", "ch": ch, "text": text})
            continue
        c = await ensure_call(req.app)
        now = time.time()
        t_end = it.get("t_audio_end")
        t_end = float(t_end) if isinstance(t_end, (int, float)) else None
        line = c.add(ch, text, now, t_audio_end=t_end, final_s=round(now - t_end, 3) if t_end else None,
                     source=str(it["source"])[:64] if it.get("source") else None)
        await S(req.app).feed.send({"type": "line", **{k: v for k, v in line.items() if k != "t"}})
        log_event({"t": now, "ev": "line", "call": c.id, "seq": line["seq"], "ch": ch, "final_s": line.get("final_s")})
        out.append(line["seq"])
    return web.json_response({"call_id": S(req.app).call.id if S(req.app).call else None, "seq": out})


async def transcript(req):
    c = S(req.app).call
    try:
        since = int(req.query.get("since", "0"))
    except ValueError:
        return bad("since must be an integer")
    if req.query.get("format") == "text":
        rows = [f"{'ME' if l['ch'] == 'me' else 'THEM'}: {l['text']}" for l in (c.lines if c else []) if l["seq"] > since]
        return web.Response(text="\n".join(rows) + ("\n" if rows else ""))
    return web.json_response({"call_id": c.id if c else None, "title": c.title if c else None,
                              "lines": [l for l in (c.lines if c else []) if l["seq"] > since]})


async def bullets(req):
    """POST /bullets {"bullets": [...]} draws one suggestion card (the newest dims the older ones). Sent as the same
    suggestion/delta/done frames a streaming writer would send, so one renderer handles both."""
    b = await body(req)
    items = b.get("bullets")
    if isinstance(items, str):
        items = [s for s in (x.strip().removeprefix("- ").strip() for x in items.splitlines()) if s]
    if not isinstance(items, list) or not items or not all(isinstance(x, str) and x.strip() for x in items):
        return bad('"bullets" must be a non-empty list of strings')
    c = await ensure_call(req.app)
    c.bullets += 1
    sid = f"{c.id}-b{c.bullets}"
    feed = S(req.app).feed
    t_end = b.get("t_audio_end")
    await feed.send({"type": "suggestion", "sid": sid, "for": str(b.get("for") or "")[:400]})
    first = {"type": "delta", "sid": sid, "text": "\n".join("- " + x.strip() for x in items[:6])}
    if isinstance(t_end, (int, float)):
        first["first_glyph_s"] = round(time.time() - float(t_end), 3)
    await feed.send(first)
    await feed.send({"type": "done", "sid": sid})
    log_event({"t": time.time(), "ev": "bullets", "call": c.id, "sid": sid, "n": len(items),
               "first_glyph_s": first.get("first_glyph_s")})
    return web.json_response({"sid": sid, "call_id": c.id})


async def status(req):
    b = await body(req)
    text = str(b.get("text", "")).strip()
    if not text:
        return bad('"text" is required')
    await S(req.app).feed.send({"type": "status", "text": text[:300]})
    return web.json_response({"ok": True})


# ---------- the prompter ----------

def current_script(app) -> Path:
    return S(app).script


def restored_script() -> Path:
    """The script POST /script last chose (CURRENT_SCRIPT), else the VIZIER_SCRIPTS default."""
    try:
        p = Path(CURRENT_SCRIPT.read_text().strip())
    except OSError:
        return SCRIPTS
    return p if p.is_absolute() else SCRIPTS


def choose_script(app, p: Path) -> None:
    S(app).script = p
    try:
        CURRENT_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
        CURRENT_SCRIPT.write_text(str(p) + "\n")
    except OSError as e:   # the choice still shows now; only its survival across a restart is lost
        log_event({"t": time.time(), "ev": "script_persist_failed", "error": str(e)})


async def script(req):
    """POST /script {"markdown": "# Title\\n## 1 · Hook\\nline\\n..."} shows that script on the prompter;
    {"path": "<file on the server>"} points the prompter at a file (re-read on every request, so edits show);
    {"clear": true} takes the script down (the prompter shows "no script yet"). The choice survives a restart."""
    b = await body(req)
    if b.get("clear") is True:
        choose_script(req.app, NO_SCRIPT)
    elif isinstance(b.get("markdown"), str) and b["markdown"].strip():
        POSTED_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
        # A text update must not take the CONTEXT tab away: posted-script.md has no `-context.md` of its own, so the
        # client saw an empty context and threw the reader off the tab. The script it replaces hands
        # its context over; {"context": "..."} replaces it, {"context": ""} drops it.
        if isinstance(b.get("context"), str):
            POSTED_CONTEXT.write_text(b["context"])
        else:
            POSTED_CONTEXT.write_text(context_text(current_script(req.app)))
        POSTED_SCRIPT.write_text(b["markdown"])
        choose_script(req.app, POSTED_SCRIPT)
    elif isinstance(b.get("path"), str) and b["path"]:
        p = Path(b["path"]).expanduser().resolve()
        roots = [d.resolve() for d in (DATA, SCRIPTS.parent, TALK_SCRIPTS, BRIEFS)]
        if p.suffix != ".md" or not any(p.is_relative_to(r) for r in roots):   # GETs are open: never any file
            return bad("path must be a .md file under VIZIER_DATA, VIZIER_SCRIPTS' folder, the talk-scripts or briefs", 403)
        if not p.is_file():
            return bad(f"no such file: {p.name}", 404)
        choose_script(req.app, p)
    else:
        return bad('send "markdown", "path" or "clear": true')
    src = S(req.app).script
    n = len(parse_takes(src.read_text())) if src.is_file() else 0
    await S(req.app).feed.send({"type": "script", "takes": n})
    log_event({"t": time.time(), "ev": "script", "takes": n})
    return web.json_response({"ok": True, "takes": n})


# Only spoken lines are a take's lines (the user 2026-11-16 3:13 AM, recording a VSL from an outline: "I'm barely able to read
# the script... there's this section called coach on part... build", vizier#15). Dropped: headings (#), coaching notes (a >
# quote or a Coach: / Coach on part N: / Note: / Direction: / Cue: / Tone: / Delivery: / Beat: / Pause: label), rules (---),
# whole-line stage directions in [] or (), and the [show] / [scout] on-screen cues; a list marker is stripped. app/ui/index.html
# applies the same rule (NOT_SPOKEN, byte-identical; tests/test_spoken_lines.py holds them together), so an agent's line numbers
# (/prompter/goto, /prompter/position) are the screen's. "(scout) line" stays: scout speaks it.
NOT_SPOKEN = re.compile(r"^(#|>|(-{3,}|\*{3,}|_{3,})$|\[[^\]]*\]$|\([^)]*\)$|\[(show|scout)\]|(coach(ing)?(\s+on\s+part\s+\S+)?|notes?|directions?|cues?|tone|delivery|beat|pause)\s*[:\-–—])", re.I)
LIST_MARK = re.compile(r"^([-*•]|\d+[.)])\s+")


def spoken(lines) -> list[str]:
    out = []
    for l in lines:
        l = LIST_MARK.sub("", l.strip().replace("**", "")).strip()
        if l and not NOT_SPOKEN.match(l):
            out.append(l)
    return out


def parse_takes(text: str) -> list[dict]:
    takes = []
    for block in ("\n" + text).split("\n## ")[1:]:
        title, *rest = block.split("\n")
        takes.append({"title": title.strip(), "lines": spoken(rest)})
    # A script with numbered parts ("1 · Hello") keeps only those: its other blocks ("Do not say") are notes.
    if any(re.match(r"\d", k["title"]) for k in takes):
        takes = [k for k in takes if re.match(r"\d", k["title"])]
    return takes


async def prompter(_):
    return web.FileResponse(HERE / "prompter.html")


async def prompter_scripts(req):
    """The teleprompter's takes: each `## ` block of the current script, one line per breath (re-read on every
    request). `?file=<name>` reads $VIZIER_TALK_SCRIPTS/<name>.md, `?brief=<slug>` reads $VIZIER_BRIEFS/<slug>.md."""
    src = current_script(req.app)
    name, brief = req.query.get("file", ""), req.query.get("brief", "")
    for val, base, what in ((brief, BRIEFS, "brief"), (name, TALK_SCRIPTS, "file")):
        if not val:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", val):
            return bad(f"bad {what} name")
        src = base / f"{val}.md"
        if not src.exists():
            return web.json_response([{"title": "waiting", "lines": [f"no {what} {val} yet"]}])
        break
    if not src.exists():
        return web.json_response([{"title": "waiting", "lines": ["no script yet: POST /script"]}])
    return web.json_response(parse_takes(src.read_text()))


def context_file(src: Path):
    """The CONTEXT tab's file for a script: its `<name>-context.md` sibling; the posted script's is POSTED_CONTEXT."""
    if src == POSTED_SCRIPT:
        return POSTED_CONTEXT
    return src.with_name(re.sub(r"-script\.md$", "", src.name) + "-context.md") if src.name.endswith("-script.md") else None


def context_text(src: Path) -> str:
    f = context_file(src)
    try:
        return f.read_text() if f and f.exists() else ""
    except OSError:
        return ""


async def prompter_context(req):
    """The prompter's other tabs: CONTEXT is the script's sibling `<name>-context.md`; NOTES are the script's
    blocks that are not numbered parts. Both re-read on every request."""
    src = current_script(req.app)
    ctx = context_file(src)
    notes, title = [], ""
    try:
        raw = src.read_text()
        m = re.match(r"#\s+(.+)", raw)
        title = m.group(1).strip() if m else ""
        for block in ("\n" + raw).split("\n## ")[1:]:
            head, *rest = block.split("\n")
            if not re.match(r"\d", head.strip()):
                notes.append({"title": head.strip(), "text": "\n".join(rest).strip()})
    except OSError:
        pass
    text = ctx.read_text() if ctx and ctx.exists() else ""
    return web.json_response({"script": src.name, "title": title, "context_file": ctx.name if ctx else None,
                              "context": text, "notes": notes})


NOTE = {"text": "", "take": "", "t": 0.0}   # one line shown on the prompter (e.g. an agent's review of the last take)
POS: dict = {"part": 0, "parts": 0, "title": "", "line": 0, "lines": 0, "text": "", "t": 0.0}   # where the prompter is (POST /prompter/position)
REC: dict = {}                              # {"proc", "path", "take", "t0"} while a take is recording


async def prompter_note(req):
    if req.method == "POST":
        b = await body(req)
        NOTE.update(text=str(b.get("text", ""))[:300], take=str(b.get("take", ""))[:32], t=time.time())
        return web.json_response({"ok": True})
    return web.json_response({**NOTE, "recording": REC.get("take")})


async def prompter_position(req):
    """POST {"part","parts","title","line","lines","text"} from the app on every move; GET returns it with its age, so
    whoever drives the screen follows the reader (an agent can know where the reader is in the script)."""
    if req.method == "POST":
        b = await body(req)
        POS.update(part=int(b.get("part", 0) or 0), parts=int(b.get("parts", 0) or 0), title=str(b.get("title", ""))[:120],
                   line=int(b.get("line", 0) or 0), lines=int(b.get("lines", 0) or 0), text=str(b.get("text", ""))[:400], t=time.time())
        return web.json_response({"ok": True})
    return web.json_response({**POS, "age_s": round(time.time() - POS["t"], 1) if POS["t"] else None})


async def talk_log(req):
    """POST {"src","ears","mouth"}: the app says who flipped Ears/Mouth (hotkey, click, key, restore on start): an audit
    line in DATA/talk-switch.log, so a switch that changed 'by itself' can be traced."""
    b = await body(req)
    with open(DATA / "talk-switch.log", "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {b.get('src','?')} ears={b.get('ears')} mouth={b.get('mouth')}\n")
    return web.json_response({"ok": True})


async def talk_loopback(req):
    """POST: the app's loopback check (vizierTalk.loopback()): per audio output, whether this machine's
    mic hears it and how far echo cancellation brings it down. Kept in DATA/talk-loopback.json; GET returns the last."""
    f = DATA / "talk-loopback.json"
    if req.method == "POST":
        b = await body(req)
        f.write_text(json.dumps(b, indent=1))
        return web.json_response({"ok": True})
    try:
        return web.json_response(json.loads(f.read_text()))
    except (OSError, ValueError):
        return web.json_response({"error": "no loopback report yet: run vizierTalk.loopback() in the app"}, status=404)


LOOPBACK_RUN = {"n": 0}   # bumped by POST /talk/loopback/run; the app polls it and runs the loopback check when it changes


async def talk_loopback_run(req):
    """POST: ask the app to run vizierTalk.loopback() now (it polls GET; nothing to reload). GET: {"n": N}."""
    if req.method == "POST":
        LOOPBACK_RUN["n"] += 1
    return web.json_response(dict(LOOPBACK_RUN))


# ---------- device self-check (the deployment's issue 14085) ----------
# The app checks the laptop's devices (talk.js selfCheck: playback, mic, heard) on joining the room, on every device
# change (a Bluetooth mic pairing adds a "Hands-Free" output that the OS can make the default) and on every entry to
# the agent's conversation mode, fixes what it can, and POSTs {"trigger", "rows": [{"check", "pass", "fixed"?, "how",
# ...}]}. This server adds the floor row (VIZIER_FLOOR, the shared floor record both voice rooms read; unset = no floor
# row), appends every row to DATA/talk-selfcheck.jsonl, and when a row failed with no fix, speaks ONE line through the
# voice agent (VIZIER_VOICE_URL POST /say), never the same line twice within SELFCHECK_SAY_GAP_S. GET returns the last run.
FLOOR = os.environ.get("VIZIER_FLOOR", "")
FLOOR_OTHER = os.environ.get("VIZIER_FLOOR_OTHER", "")       # the other room's name in the floor record's last_said
SELFCHECK_SAY_GAP_S = float(os.environ.get("VIZIER_SELFCHECK_SAY_GAP_S", "600"))
SELFCHECK = {"last": None, "said": {}, "run": 0}


def floor_row(now: float | None = None) -> dict | None:
    """The floor check: the shared floor record parses, and the other room's last_said is empty or carries a time
    (the echo filter's input). None when VIZIER_FLOOR is unset."""
    if not FLOOR:
        return None
    now = time.time() if now is None else now
    try:
        rec = json.loads(Path(FLOOR).read_text())
    except (OSError, ValueError) as e:
        return {"check": "floor", "pass": False, "fixed": False, "code": "floor_unreadable",
                "how": f"floor record unreadable: {type(e).__name__}"}
    other = ((rec.get("last_said") or {}).get(FLOOR_OTHER) or {}) if FLOOR_OTHER else {}
    t = other.get("t")
    row = {"check": "floor", "holder": rec.get("holder"), "how": "read"}
    if other and not isinstance(t, (int, float)):
        return {**row, "pass": False, "fixed": False, "code": "floor_no_time",
                "how": f"last_said[{FLOOR_OTHER}] has no time"}
    if isinstance(t, (int, float)):
        row["other_said_age_s"] = round(now - t, 1)
    return {**row, "pass": True}


# What the agent says for an unfixed failure: a fixed table keyed by the row's `code` (the endpoint is open like
# /talk/log, so no client text is ever spoken).
SELFCHECK_SAY = {
    "no_speaker_output": "no laptop speaker output exists, only a headset",
    "sink_failed": "I could not move my voice to the laptop speakers",
    "not_heard": "the mic did not hear my voice from the speakers; check the laptop volume",
    "no_dji": "the DJI mic is not connected",
    "dji_switch_failed": "I could not switch to the DJI mic",
    "mic_silent": "the mic is silent",
    "floor_unreadable": "the floor record is unreadable",
    "floor_no_time": "the floor record is missing the other room's last line",
}
SELFCHECK_CHECKS = {"playback", "heard", "mic", "floor"}


def selfcheck_line(rows: list) -> str:
    """One sentence for every failure that has no fix ('' = nothing to say)."""
    bad = [SELFCHECK_SAY.get(str(r.get("code")), f"the {r['check']} check failed") for r in rows
           if r.get("pass") is False and not r.get("fixed") and r.get("check") in SELFCHECK_CHECKS]
    return ("Self-check: " + "; ".join(dict.fromkeys(bad)) + ".") if bad else ""


async def talk_selfcheck(req):
    """POST {"trigger", "rows": [...]} from the app; GET: the last run."""
    if req.method == "GET":
        return web.json_response(SELFCHECK["last"] or {"error": "no self-check yet"}, status=200 if SELFCHECK["last"] else 404)
    b = await body(req)
    rows = [r for r in (b.get("rows") or []) if isinstance(r, dict) and r.get("check") in SELFCHECK_CHECKS][:16]
    fr = floor_row()
    if fr:
        rows.append(fr)
    now = time.time()
    run = {"t": now, "trigger": str(b.get("trigger") or "?"), "rows": rows}
    with open(DATA / "talk-selfcheck.jsonl", "a") as f:
        for r in rows:
            f.write(json.dumps({"t": now, "trigger": run["trigger"], **r}) + "\n")
    line = selfcheck_line(rows)
    run["said"] = None
    if line and VOICE_URL and now - SELFCHECK["said"].get(line, 0) >= SELFCHECK_SAY_GAP_S:
        SELFCHECK["said"][line] = now
        st, out = await _voice("POST", "/say", {"say": line})
        run["said"] = {"text": line, "status": st, "spoken": out.get("spoken", st == 200)}
    SELFCHECK["last"] = run
    return web.json_response({"ok": True, "rows": len(rows), "said": run["said"]})


async def talk_selfcheck_run(req):
    """POST: ask the app to run its self-check now (it polls GET {"n"}, like /talk/loopback/run)."""
    if req.method == "POST":
        SELFCHECK["run"] += 1
    return web.json_response({"n": SELFCHECK["run"]})


TALK = {"seq": 0, "lines": [], "partial": None}   # the live conversation with the agent, newest last (TALK mode in the app)
TALK_KEEP = 300


async def talk_transcript(req):
    """POST {"rows":[{"ch":"me"|"agent","text","final":bool}]}: a forwarder next to the speech gateway sends what was heard
    (ch me) and what the agent said (ch agent). A final row is appended with a seq; a non-final row replaces the one live
    partial and is cleared by the next final from the same side. GET ?since=<seq> returns {seq, lines:[newer than since],
    partial:{ch,text}|null}; the app polls it. Nothing is stored beyond the last TALK_KEEP lines, in memory."""
    if req.method == "POST":
        b = await body(req)
        for r in b.get("rows") or []:
            if not isinstance(r, dict):
                continue
            ch = "me" if r.get("ch") == "me" else "agent"
            text = str(r.get("text") or "").strip()[:2000]
            if not text:
                continue
            if r.get("final"):
                TALK["seq"] += 1
                TALK["lines"].append({"seq": TALK["seq"], "ch": ch, "text": text, "t": time.time()})
                del TALK["lines"][:-TALK_KEEP]
                if TALK["partial"] and TALK["partial"]["ch"] == ch:
                    TALK["partial"] = None
            else:
                TALK["partial"] = {"ch": ch, "text": text, "t": time.time()}
        return web.json_response({"ok": True, "seq": TALK["seq"]})
    try:
        since = int(req.query.get("since", "0"))
    except ValueError:
        since = 0
    p = TALK["partial"]
    if p and time.time() - p["t"] > 30:   # a partial whose final never came is not live any more
        p = TALK["partial"] = None
    return web.json_response({"seq": TALK["seq"], "lines": [x for x in TALK["lines"] if x["seq"] > since], "partial": p})


FOLLOW = {"on": False}   # OFF by default (ask 821: the reader steps lines with his own keys; voice-follow lags). POST /prompter/follow {"on": bool} or {"toggle": true}: the agent's speech-follow and screen driving


async def prompter_follow(req):
    if req.method == "POST":
        b = await body(req)
        FOLLOW["on"] = (not FOLLOW["on"]) if b.get("toggle") else bool(b.get("on", True))
    return web.json_response(FOLLOW)


SNAP = {"seq": 0}   # POST /prompter/snap asks the app for a picture of the prompter (live.png beside the exe's public dir)


async def prompter_snap(req):
    if req.method == "POST":
        SNAP["seq"] += 1
    return web.json_response(SNAP)


GOTO = {"part": 0, "line": 0, "seq": 0}   # a jump request for the app (POST /prompter/goto), polled by the app


async def prompter_goto(req):
    """POST {"part": 3, "line": 2} (1-based) moves the prompter there: the app polls GET and follows when `seq` changes.
    This is how a driver that hears the reader keeps the prompter on the reader's words, hands-free."""
    if req.method == "POST":
        b = await body(req)
        GOTO.update(part=int(b.get("part", 1) or 1), line=int(b.get("line", 1) or 1), seq=GOTO["seq"] + 1)
        return web.json_response({"ok": True, **GOTO})
    return web.json_response(GOTO)


async def rec_stop():
    p = REC.pop("proc", None)
    if p and p.returncode is None:
        p.stdin.write(b"q")          # ffmpeg finalizes the mp4 (moov atom) on q
        await p.stdin.drain()
        try:
            await asyncio.wait_for(p.wait(), 8)
        except asyncio.TimeoutError:
            p.kill()
        log_event({"t": time.time(), "ev": "take_stop", "path": REC.get("path"), "s": round(time.time() - REC.get("t0", 0), 1)})
    return {k: REC.pop(k, None) for k in ("path", "take", "t0")}


async def prompter_record(req):
    """POST /prompter/record {"take": "A"} copies $VIZIER_CAM (no re-encode) into $VIZIER_TAKES/<take>-<n>.mp4,
    stopping any running take; {"stop": true} stops. Off (409) when VIZIER_CAM is unset."""
    b = await body(req)
    prev = await rec_stop()
    if b.get("stop"):
        return web.json_response({"stopped": prev.get("path")})
    if not CAM:
        return bad("recording is off: set VIZIER_CAM to an RTSP camera stream", 409)
    take = re.sub(r"[^A-Za-z0-9]", "", str(b.get("take") or "X"))[:8] or "X"
    TAKES.mkdir(parents=True, exist_ok=True)
    n = 1 + max([int(m.group(1)) for f in TAKES.glob(f"{take}-*.mp4") if (m := re.match(rf"{take}-(\d+)\.mp4$", f.name))] or [0])
    path = TAKES / f"{take}-{n}.mp4"
    p = await asyncio.create_subprocess_exec("ffmpeg", "-loglevel", "error", "-rtsp_transport", "tcp", "-i", CAM,
                                             "-c", "copy", "-movflags", "+faststart", str(path),
                                             stdin=asyncio.subprocess.PIPE)
    REC.update(proc=p, path=str(path), take=f"{take}-{n}", t0=time.time())
    log_event({"t": time.time(), "ev": "take_start", "path": str(path)})
    return web.json_response({"recording": f"{take}-{n}", "stopped": prev.get("path")})


# ---------- VSL takes: POST /vsl, the one switch behind Alt+R, the SETTINGS row and the agent's "record" ----------
VSL: dict = {"want": None, "busy": None, "error": "", "name": None, "since": None, "on": False}


def vsl_actual() -> bool:
    """Is a take recording? The state file when there is one (vsl writes it once OBS records and removes it after the
    review), else what this server last ran successfully."""
    return Path(VSL_STATE).expanduser().is_file() if VSL_STATE else bool(VSL["on"])


def vsl_view() -> dict:
    actual = vsl_actual()
    on = VSL["want"] if VSL["want"] is not None else actual
    return {"configured": bool(VSL_START_CMD and VSL_STOP_CMD), "on": on, "actual": actual,
            "pending": VSL["want"] is not None, "busy": VSL["busy"], "name": VSL["name"], "since": VSL["since"],
            "error": VSL["error"]}


async def _vsl_sync() -> None:
    """Runs start/stop until the take matches what was last asked for. One command at a time: a start asked while a
    stop is still reviewing waits for it (a start during a stop would have its state file deleted by that stop)."""
    if VSL["busy"]:
        return
    try:
        while VSL["want"] is not None and VSL["want"] != vsl_actual():
            want = VSL["want"]
            cmd = VSL_START_CMD.replace("{name}", shlex.quote(VSL["name"] or "vsl")) if want else VSL_STOP_CMD
            VSL["busy"] = "starting" if want else "saving"
            t0 = time.time()
            p = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await p.communicate()
            tail = out.decode(errors="replace").strip().splitlines()[-1:] or [""]
            log_event({"t": time.time(), "ev": "vsl_" + ("start" if want else "stop"), "rc": p.returncode,
                       "s": round(time.time() - t0, 1), "out": tail[0][:300]})
            if p.returncode != 0:
                VSL.update(error=f"{'start' if want else 'stop'} failed (exit {p.returncode}): {tail[0][:200]}", want=None)
                break
            VSL["on"] = want
            if want == VSL["want"] and VSL_STATE and vsl_actual() != want:
                break   # the command succeeded but the state file disagrees: report the file, do not loop
        VSL["want"] = None
    finally:
        VSL["busy"] = None


async def vsl(req):
    """GET /vsl: {configured, on, actual, pending, busy, name, since, error}. POST /vsl {"on": bool} or {"toggle": true}
    (optional "name"): answers at once with the state asked for; the command runs in the background."""
    if req.method == "GET":
        return web.json_response(vsl_view())
    b = await body(req)
    if not (VSL_START_CMD and VSL_STOP_CMD):
        return bad("VSL recording is not configured on this server (VIZIER_VSL_START_CMD / VIZIER_VSL_STOP_CMD)", 409)
    cur = vsl_view()["on"]
    want = (not cur) if b.get("toggle") else bool(b.get("on"))
    if want and not cur:
        name = b.get("name") or VSL_NAME or current_script(req.app).stem
        VSL.update(name=re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")[:40] or "vsl", since=time.time())
    VSL.update(want=want, error="")
    log_event({"t": time.time(), "ev": "vsl_ask", "on": want, "src": str(b.get("src") or "")[:40]})
    asyncio.get_running_loop().create_task(_vsl_sync())
    return web.json_response(vsl_view())


# ---------- the agent's settings, desks and team (the deployment's issue 13798) ----------
# The app's SETTINGS panel reads GET /settings and changes a value with POST /settings {"voice": "next"|"prev"|<name>}
# or {"wake": {"on"?: bool, "call_n"?: int}}; this server forwards to the voice agent (VIZIER_VOICE_URL). Ears and Mouth
# are the app's own LiveKit attributes (talk.js), so the panel flips them there, not here. DESKS lists VIZIER_DESKS_CMD's
# output and POST /desks/open {"id"} runs VIZIER_DESK_OPEN_CMD for a listed id only. AGENTS summarizes every JSON
# file in VIZIER_AGENTS_DIR: {agent, health, at, reasons, doing} from its `liveness` block (or the top level).

async def _voice(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    import aiohttp
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as c:
            async with c.request(method, VOICE_URL + path, json=payload) as r:
                try:
                    return r.status, await r.json(content_type=None)
                except ValueError:
                    return r.status, {"error": (await r.text())[:200]}
    except Exception as e:  # noqa: BLE001 - an unreachable agent is a status, not a traceback
        return 0, {"error": f"{type(e).__name__}: {e}"[:200]}


async def settings(req):
    if not VOICE_URL:
        if req.method == "POST":
            await body(req)
        return web.json_response({"configured": False, "error": "VIZIER_VOICE_URL is not set"})
    if req.method == "POST":
        b = await body(req)
        if "voice" in b:
            v = str(b["voice"])
            st, out = await _voice("POST", "/voice", {"step": v} if v in ("next", "prev") else {"name": v})
        elif isinstance(b.get("wake"), dict):
            w = {k: b["wake"][k] for k in ("on", "call_n") if k in b["wake"]}
            st, out = await _voice("POST", "/address", w)
        else:
            raise web.HTTPBadRequest(text=json.dumps({"error": "body: {voice} or {wake}"}), content_type="application/json")
        log_event({"t": time.time(), "ev": "setting", "body": b, "status": st})
        return web.json_response({"ok": 200 <= st < 300, "status": st, **out}, status=200 if 200 <= st < 300 else 502)
    (hs, h), (vs, v) = await asyncio.gather(_voice("GET", "/health"), _voice("GET", "/voices"))
    if hs != 200:
        return web.json_response({"configured": True, "reachable": False, "error": h.get("error") or f"HTTP {hs}"})
    voices = [{k: x.get(k) for k in ("name", "accent", "provider")} for x in (v.get("voices") or [])] if vs == 200 else []
    return web.json_response({
        "configured": True, "reachable": True, "room": h.get("room"),
        "ears": h.get("ears"), "mouth": h.get("mouth"),
        "voice": v.get("voice") if vs == 200 else None, "voices": voices,
        "wake": h.get("address_rules"), "call_live": (h.get("address") or {}).get("call_live"),
        "say": (h.get("address") or {}).get("say"),
        "clients": [{"identity": c.get("identity"), "ears": c.get("ears"), "mouth": c.get("mouth")}
                    for c in (h.get("clients") or []) if isinstance(c, dict)]})


async def _run_json(cmd: str, timeout: float = 10) -> object:
    p = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        p.kill()
        raise RuntimeError(f"timed out after {timeout:g} s")
    if p.returncode != 0:
        raise RuntimeError(f"exit {p.returncode}: {err.decode(errors='replace').strip()[-200:]}")
    return json.loads(out or b"null")


async def _desk_list() -> list[dict]:
    rows = await _run_json(DESKS_CMD)
    return [{k: r.get(k) for k in ("id", "name", "agent", "detail")} for r in (rows or []) if isinstance(r, dict) and r.get("id")]


async def desks(req):
    if not DESKS_CMD:
        return web.json_response({"configured": False, "desks": []})
    try:
        return web.json_response({"configured": True, "desks": await _desk_list()})
    except Exception as e:  # noqa: BLE001
        return web.json_response({"configured": True, "desks": [], "error": str(e)[:200]})


async def desks_open(req):
    b = await body(req)
    if not (DESKS_CMD and DESK_OPEN_CMD):
        return web.json_response({"ok": False, "error": "VIZIER_DESKS_CMD / VIZIER_DESK_OPEN_CMD are not set"}, status=501)
    want = str(b.get("id") or "")
    try:
        ids = {d["id"] for d in await _desk_list()}
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False, "error": str(e)[:200]}, status=502)
    if want not in ids:   # only a listed id ever reaches the command line
        return web.json_response({"ok": False, "error": f"no desk {want!r}"}, status=404)
    import shlex
    argv = [a.replace("{id}", want) for a in shlex.split(DESK_OPEN_CMD)]
    t0 = time.time()
    p = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(p.communicate(), 60)
    except asyncio.TimeoutError:
        p.kill()
        return web.json_response({"ok": False, "error": "desk open timed out after 60 s"}, status=504)
    said = out.decode(errors="replace").strip()[-300:]
    log_event({"t": time.time(), "ev": "desk_open", "id": want, "rc": p.returncode, "s": round(time.time() - t0, 2)})
    return web.json_response({"ok": p.returncode == 0, "rc": p.returncode, "said": said, "s": round(time.time() - t0, 2)},
                             status=200 if p.returncode == 0 else 502)


def agent_row(name: str, d: dict) -> dict:
    lv = d.get("liveness") if isinstance(d.get("liveness"), dict) else d
    doing = lv.get("task") or lv.get("doing") or lv.get("externalEvent")
    return {"agent": d.get("agent") or name, "health": lv.get("health") or "unknown",
            "at": lv.get("healthAt") or lv.get("lastCycleAt") or d.get("fetchedAt"),
            "reasons": [str(x) for x in (lv.get("healthReasons") or [])][:3],
            "doing": doing if isinstance(doing, str) else (json.dumps(doing)[:120] if doing else None)}


async def agents(_req):
    if not AGENTS_DIR:
        return web.json_response({"configured": False, "agents": []})
    rows = []
    for f in sorted(Path(AGENTS_DIR).glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(d, dict):
            rows.append(agent_row(f.stem, d))
    return web.json_response({"configured": True, "agents": rows})


# ---------- Eagle Script: the agent's own cue list, read-only (the reader's view of what the agent will do) ----------

_SEEN: dict = {}


def track_changes(key, items):
    """Mark what an edit touched. The first read of `key` is the baseline (nothing marked); after that an item not seen
    before gets the time it first appeared, so a changed file could mark exactly the new or edited lines. Unused: the panels send changed=null (no marks).
    Returns [epoch or None] per item; the app shows the mark for a few minutes."""
    now = time.time()
    seen = _SEEN.get(key)
    if seen is None:
        _SEEN[key] = {i: None for i in items}
        return [None] * len(items)
    cur = {i: seen[i] if i in seen else now for i in items}
    _SEEN[key] = cur
    return [cur[i] for i in items]


def spoken_sections(text):
    """speaker-script.md -> [{title, lines}]: the numbered sections' spoken lines only (no headings of other kinds, no
    [horus] cue lines, no Do-not-say, no notes)."""
    out, cur = [], None
    for line in text.splitlines():
        if line.startswith("#"):
            cur = {"title": re.sub(r"^#+\s*", "", line), "lines": []} if re.match(r"##\s*\d", line) else None
            if cur: out.append(cur)
            continue
        l = line.strip()
        if cur is None or not l or l.startswith(("[horus]", "[scout]", "|", ">", "-", "`")):
            continue
        cur["lines"].append(l)
    return out


async def speaker_script(_req):
    """GET /speaker-script: his spoken lines per section. Read-only; no current line (the page never highlights or scrolls)."""
    p = Path(SPEAKER_SCRIPT).expanduser() if SPEAKER_SCRIPT else None
    if not p or not p.is_file():
        return web.json_response({"configured": bool(SPEAKER_SCRIPT), "sections": [], "source": None})
    text = p.read_text()
    secs = spoken_sections(text)
    flat = [l for s in secs for l in s["lines"]]
    for s in secs:   # "changed" is always null: the user does not want 'changed HH:MM' marks (track_changes is kept, unused)
        s["lines"] = [{"text": l, "changed": None} for l in s["lines"]]
    m = re.match(r"#\s*(.*)", text)
    return web.json_response({"configured": True, "source": p.name, "title": m.group(1)[:120] if m else "",
                              "sections": secs, "now": time.time()})


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_cues(text):
    """A markdown table -> [{beat, id, on, do, wait, fail}]. Columns are found by header name, so both a horus-script.md
    (id | on | do | wait-for | on-fail) and a runsheet.md (t | Speaker says | ... | horus action | ... | live adjust) read.
    `beat` is vsl-follow's id for the row: b<row index>."""
    rows, head = [], None
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        c = _cells(line)
        if head is None:
            head = [h.lower() for h in c]; continue
        if all(re.fullmatch(r":?-{2,}:?", x) for x in c if x):
            continue
        def pick(*names):
            for i, h in enumerate(head):
                if i < len(c) and any(n in h for n in names):
                    return c[i]
            return ""
        rows.append({"beat": f"b{len(rows)}", "id": pick("id", "t") or f"b{len(rows)}",
                     "on": pick("on-phrase", "on phrase", "says", "on"), "do": pick("horus action", "do"),
                     "wait": pick("wait", "mark"), "fail": pick("on-fail", "adjust", "fail")})
    return rows


async def horus_script(_req):
    """GET /horus-script: the cue table from the first existing VIZIER_HORUS_CUES file, and `current` (a beat id) from
    VIZIER_HORUS_STATE. Nothing here writes."""
    src = next((Path(p).expanduser() for p in HORUS_CUES.split(os.pathsep) if p and Path(p).expanduser().is_file()), None)
    if not src:
        return web.json_response({"configured": bool(HORUS_CUES), "cues": [], "current": None, "source": None})
    cur = at = None
    if HORUS_STATE:
        try:
            st = json.loads(Path(HORUS_STATE).expanduser().read_text())
            cur, at = st.get("beat"), st.get("at")
        except (OSError, ValueError):
            pass
    cues = parse_cues(src.read_text())
    for c in cues:
        c["changed"] = None   # no 'changed HH:MM' marks (track_changes is kept, unused)
    return web.json_response({"configured": True, "source": src.name, "cues": cues, "current": cur, "at": at,
                              "now": time.time()})


# ---------- WRITE API: POST /speaker-script, POST /horus-script, GET /state (the agent edits what the tabs show) ----------
# Local only (the server binds loopback). A write needs the shared secret in `X-Vizier-Write`, read from the file
# VIZIER_WRITE_SECRET_FILE (mode 0640, group fleet-vizier-write). The file is read on every request, never cached, never
# logged, never in git. Unset or unreadable = writes are off (403), reads stay open. Each write is atomic (temp + rename in
# the live file's directory), appended to VIZIER_WRITE_LOG with who/when/sha, and the previous text is kept (last 20).
WRITE_SECRET_FILE = os.environ.get("VIZIER_WRITE_SECRET_FILE", "")
WRITE_KEEP = int(os.environ.get("VIZIER_WRITE_KEEP", "20"))
WRITE_MAX = 512 * 1024
FRONT = {"tab": None, "at": None}   # the tab the app reports as front (POST /state/front)


def write_log_path() -> Path:
    return Path(os.environ.get("VIZIER_WRITE_LOG") or DATA / "script-writes.jsonl")


def write_secret() -> str:
    try:
        return Path(WRITE_SECRET_FILE).expanduser().read_text().strip() if WRITE_SECRET_FILE else ""
    except OSError:
        return ""


def write_allowed(req) -> bool:
    sec = write_secret()
    return bool(sec) and hmac.compare_digest(req.headers.get("X-Vizier-Write", "").strip(), sec)


def script_target(kind: str) -> Path | None:
    """The live file a tab reads: the speaker script, or the first existing cue file (else the first one listed)."""
    if kind == "speaker-script":
        return Path(SPEAKER_SCRIPT).expanduser() if SPEAKER_SCRIPT else None
    paths = [Path(p).expanduser() for p in HORUS_CUES.split(os.pathsep) if p]
    return next((p for p in paths if p.is_file()), paths[0] if paths else None)


def sha_of(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def history_dir(kind: str) -> Path:
    return DATA / "script-history" / kind


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        mode = 0o644
    tmp.write_text(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def keep_previous(kind: str, path: Path) -> None:
    if not path.is_file():
        return
    d = history_dir(kind); d.mkdir(parents=True, exist_ok=True)
    old = path.read_text()
    (d / f"{time.time_ns()}-{sha_of(old)}.md").write_text(old)
    for f in sorted(d.glob("*.md"))[:-WRITE_KEEP]:
        f.unlink(missing_ok=True)


async def write_script(req):
    """POST /speaker-script | /horus-script: JSON {"text": ...} or the raw text. -> {ok, bytes, sha, at}."""
    kind = req.path.strip("/")
    if not write_allowed(req):
        return bad("writes need the X-Vizier-Write secret (group fleet-vizier-write)", 403)
    target = script_target(kind)
    if not target:
        return bad(f"{kind} is not configured on this server", 409)
    raw = await req.read()
    if len(raw) > WRITE_MAX:
        return bad("text over 512 KB", 413)
    try:
        text = raw.decode()
    except UnicodeDecodeError:
        return bad("body is not UTF-8")
    if "json" in (req.content_type or "") or text.lstrip().startswith("{"):
        try:
            b = json.loads(text)
        except ValueError:
            b = None
        if isinstance(b, dict):
            text = b.get("text")
            if not isinstance(text, str):
                return bad('JSON body needs a string "text"')
    if not text.strip():
        return bad("empty text refused (it would blank the tab); send the full script")
    if not text.endswith("\n"):
        text += "\n"
    who = re.sub(r"[^\w.@-]", "", req.headers.get("X-Vizier-Who", "") or "unknown")[:32] or "unknown"
    keep_previous(kind, target)
    atomic_write(target, text)
    rec = {"ok": True, "bytes": len(text.encode()), "sha": sha_of(text), "at": time.time()}
    try:
        lp = write_log_path(); lp.parent.mkdir(parents=True, exist_ok=True)
        with lp.open("a") as f:
            f.write(json.dumps({**rec, "kind": kind, "who": who, "file": str(target)}) + "\n")
    except OSError:
        pass
    return web.json_response(rec)


async def write_undo(req):
    """POST /speaker-script/undo | /horus-script/undo: restore the previous version (itself kept, so undo toggles)."""
    kind = req.path.strip("/").removesuffix("/undo")
    if not write_allowed(req):
        return bad("writes need the X-Vizier-Write secret (group fleet-vizier-write)", 403)
    target = script_target(kind)
    hist = sorted(history_dir(kind).glob("*.md")) if target else []
    if not hist:
        return bad("no previous version", 404)
    text = hist[-1].read_text()
    hist[-1].unlink(missing_ok=True)
    keep_previous(kind, target)
    atomic_write(target, text)
    return web.json_response({"ok": True, "bytes": len(text.encode()), "sha": sha_of(text), "at": time.time()})


def file_view(p: Path | None) -> dict:
    if not p or not p.is_file():
        return {"configured": bool(p), "source": None, "text": None, "sha": None, "mtime": None}
    t = p.read_text()
    return {"configured": True, "source": p.name, "text": t, "sha": sha_of(t), "mtime": p.stat().st_mtime}


async def state(req):
    """GET /state: everything the agent needs in one read: his script (text, sha, mtime), the cue sheet summary and the
    current cue, the take, whether it records, and the front tab (when the app reports it)."""
    sp = file_view(script_target("speaker-script"))
    if sp["text"] is not None:
        sp["sections"] = len(spoken_sections(sp["text"]))
    hv = file_view(script_target("horus-script"))
    cur = at = None
    if HORUS_STATE:
        try:
            st = json.loads(Path(HORUS_STATE).expanduser().read_text()); cur, at = st.get("beat"), st.get("at")
        except (OSError, ValueError):
            pass
    cues = parse_cues(hv["text"]) if hv["text"] else []
    horus = {k: hv[k] for k in ("configured", "source", "sha", "mtime")}
    horus.update(cue_count=len(cues), current_beat=cur, current_at=at, current_cue=next((c for c in cues if c["beat"] == cur), None))
    v = vsl_view()
    return web.json_response({
        "ok": True, "now": time.time(), "speaker_script": sp, "horus_script": horus,
        "take": {"name": v["name"] or VSL_NAME or current_script(req.app).stem, "recording": v["actual"], "pending": v["pending"],
                 "prompter_recording": REC.get("take")},
        "front_tab": FRONT["tab"], "front_tab_at": FRONT["at"], "write_enabled": bool(write_secret())})


async def state_front(req):
    """POST /state/front {"tab"}: the app reports which tab is front (so GET /state can say)."""
    b = await body(req)
    FRONT.update(tab=str(b.get("tab", ""))[:24] or None, at=time.time())
    return web.json_response({"ok": True})


# ---------- APPROVAL: GET /approval, POST /approve (the approver sees the gate evidence; Ready needs gates + approval) ----------
# GET /approval (read-only): the live speaker script's sha, the gate report the pipeline wrote (VIZIER_GATE_REPORT, JSON:
# {"sha", "gates": {"<id>": {"pass": bool, "advisory": bool, "findings": [str]}}, "references": [{"id","title","source",
# "status"}]}), the diff from the previous kept version, the approval record and `ready`: the refusals that stop Ready.
# POST /approve needs its OWN secret in `X-Vizier-Approve` (file VIZIER_APPROVE_SECRET_FILE, held by the approver only, never
# the write secret) and `X-Vizier-Who` in VIZIER_APPROVERS (default "architect,agent"). It records who/when/sha in the
# approval file and appends the same row to VIZIER_APPROVAL_LOG. An approval is bound to the script's sha: any later edit
# makes the take draft again.
APPROVE_SECRET_FILE = os.environ.get("VIZIER_APPROVE_SECRET_FILE", "")
APPROVERS = {a.strip() for a in os.environ.get("VIZIER_APPROVERS", "architect,agent").split(",") if a.strip()}


def approval_path() -> Path:
    return Path(os.environ.get("VIZIER_APPROVAL_FILE") or DATA / "approval.json")


def approval_log_path() -> Path:
    return Path(os.environ.get("VIZIER_APPROVAL_LOG") or DATA / "approvals.jsonl")


def approve_secret() -> str:
    try:
        return Path(APPROVE_SECRET_FILE).expanduser().read_text().strip() if APPROVE_SECRET_FILE else ""
    except OSError:
        return ""


def approve_allowed(req) -> bool:
    sec = approve_secret()
    return bool(sec) and hmac.compare_digest(req.headers.get("X-Vizier-Approve", "").strip(), sec)


def read_json(p: Path | None):
    try:
        return json.loads(Path(p).expanduser().read_text()) if p else None
    except (OSError, ValueError):
        return None


def last_version_diff(text: str) -> dict:
    """Unified diff from the newest kept previous version to the live text."""
    hist = sorted(history_dir("speaker-script").glob("*.md"))
    if not hist:
        return {"has_previous": False, "lines": []}
    import difflib
    old = hist[-1].read_text()
    lines = list(difflib.unified_diff(old.splitlines(), text.splitlines(), "previous", "current", lineterm="", n=2))
    return {"has_previous": True, "previous_sha": sha_of(old), "changed": bool(lines), "lines": lines[:400]}


def readiness(sha: str | None, report, appr) -> list:
    """Why Ready is refused: every reason names the failing gate or the missing approval. Empty = Ready allowed."""
    if not sha:
        return ["script: no speaker script is set"]
    rs = []
    if not isinstance(report, dict) or not isinstance(report.get("gates"), dict) or not report["gates"]:
        return ["gates: no gate report was written for this script"] + (
            [] if isinstance(appr, dict) and appr.get("sha") == sha else ["approval: this script version is not approved"])
    if report.get("sha") != sha:
        rs.append(f"gates: the report is for {report.get('sha')}, the script is {sha}; re-run the gates")
    else:
        for gid, g in sorted(report["gates"].items()):
            if isinstance(g, dict) and not g.get("advisory") and g.get("pass") is not True:
                f = (g.get("findings") or [""])[0]
                rs.append(f"gate {gid} failed" + (f": {str(f)[:120]}" if f else ""))
    if not (isinstance(appr, dict) and appr.get("status") == "approved" and appr.get("sha") == sha):
        rs.append("approval: this script version is not approved" + (
            f" (approved {appr.get('sha')}, script is {sha})" if isinstance(appr, dict) and appr.get("sha") else ""))
    return rs


def approval_view() -> dict:
    sp = file_view(script_target("speaker-script"))
    sha = sp["sha"]
    report = read_json(os.environ.get("VIZIER_GATE_REPORT"))
    appr = read_json(approval_path())
    live = isinstance(appr, dict) and appr.get("sha") == sha and appr.get("status") == "approved"
    gates = (report or {}).get("gates") if isinstance(report, dict) else None
    return {"ok": True, "configured": bool(sp["configured"]), "sha": sha, "source": sp["source"],
            "gates": gates or {}, "gates_sha": report.get("sha") if isinstance(report, dict) else None,
            "gates_at": (report or {}).get("at") if isinstance(report, dict) else None,
            "references": (report or {}).get("references", []) if isinstance(report, dict) else [],
            "diff": last_version_diff(sp["text"]) if sp["text"] else {"has_previous": False, "lines": []},
            "approval": {"status": "approved" if live else "draft", **({k: appr.get(k) for k in ("approver", "at", "sha")} if isinstance(appr, dict) else {})},
            "ready": {"ok": not (r := readiness(sha, report, appr)), "refusals": r}}


async def approval(req):
    return web.json_response(approval_view())


# ---------- QC: GET /qc (the QA checklist and the last vsl_qa verdict; read-only, ask 821) ----------
# VIZIER_QC_FILE: the checklist the pipeline wrote ({"checklist": [{"id", "text", "pass": true|false|null}]}).
# VIZIER_QA_VERDICT: the last vsl_qa verdict ({"verdict": "PASS"|"FAIL", "at", "file", "findings": [..]}). Either may be absent:
# the tab then says so rather than showing a stand-in.
def qc_view() -> dict:
    qc = read_json(os.environ.get("VIZIER_QC_FILE"))
    ver = read_json(os.environ.get("VIZIER_QA_VERDICT"))
    items = [i for i in (qc.get("checklist") if isinstance(qc, dict) else None) or [] if isinstance(i, dict)]
    return {"ok": True, "configured": bool(os.environ.get("VIZIER_QC_FILE") or os.environ.get("VIZIER_QA_VERDICT")),
            "checklist": items, "verdict": ver if isinstance(ver, dict) else None, "now": time.time()}


async def qc(_req):
    return web.json_response(qc_view())


async def approve(req):
    """POST /approve {"sha"?}: the approver marks the CURRENT script version approved. -> the approval record."""
    if not approve_allowed(req):
        return bad("approve needs the X-Vizier-Approve secret (held by the approver only)", 403)
    who = re.sub(r"[^\w.@-]", "", req.headers.get("X-Vizier-Who", "") or "")[:32]
    if who not in APPROVERS:
        return bad(f"approver {who or '(none)'!r} is not allowed; X-Vizier-Who must be one of {sorted(APPROVERS)}", 403)
    sp = file_view(script_target("speaker-script"))
    if not sp["sha"]:
        return bad("no speaker script is set", 409)
    try:
        b = await req.json() if req.can_read_body else {}
    except ValueError:
        b = {}
    want = b.get("sha") if isinstance(b, dict) else None
    if want and want != sp["sha"]:
        return bad(f"the script changed: you approved {want}, the live script is {sp['sha']}", 409)
    rec = {"status": "approved", "sha": sp["sha"], "approver": who, "at": time.time()}
    report = read_json(os.environ.get("VIZIER_GATE_REPORT"))
    rec["gates_failing"] = [g for g, v in ((report or {}).get("gates") or {}).items()
                            if isinstance(v, dict) and not v.get("advisory") and v.get("pass") is not True] if isinstance(report, dict) and report.get("sha") == sp["sha"] else ["no current gate report"]
    atomic_write(approval_path(), json.dumps(rec) + "\n")
    try:
        lp = approval_log_path(); lp.parent.mkdir(parents=True, exist_ok=True)
        with lp.open("a") as f:
            f.write(json.dumps({**rec, "event": "approve"}) + "\n")
    except OSError:
        pass
    return web.json_response({"ok": True, **rec})


# ---------- misc ----------

async def buildlog(req):
    """GET /buildlog tails a plain-text log (one line per step) so the app can show a running build."""
    try:
        n = max(1, min(50, int(req.query.get("n", "12"))))
    except ValueError:
        n = 12
    try:
        st = BUILD_LOG.stat()
        with BUILD_LOG.open("rb") as f:
            f.seek(max(0, st.st_size - 65536))
            raw = f.read().decode("utf-8", "replace")
    except OSError as e:
        return web.json_response({"source": BUILD_LOG.name, "lines": [], "error": e.strerror or type(e).__name__})
    rows = [re.sub(r"^\s*(?:[-*]\s+)", "", s).strip() for s in raw.splitlines()]
    rows = [s for s in rows if s and not s.startswith("#")][-n:]
    return web.json_response({"source": BUILD_LOG.name, "lines": rows, "mtime": st.st_mtime, "size": st.st_size})


_CAM_WANTED = {"at": 0.0, "v": False}


async def cam_wanted() -> bool:
    """VIZIER_CAM_WANTED_CMD's exit status, cached CAM_WANTED_TTL seconds (the app polls /health every second). A
    command that fails to run or takes over 3 s counts as not wanted."""
    if not CAM_WANTED_CMD:
        return False
    if time.monotonic() - _CAM_WANTED["at"] < CAM_WANTED_TTL:
        return _CAM_WANTED["v"]
    v = False
    try:
        p = await asyncio.create_subprocess_shell(CAM_WANTED_CMD, stdout=asyncio.subprocess.DEVNULL,
                                                  stderr=asyncio.subprocess.DEVNULL)
        try:
            v = await asyncio.wait_for(p.wait(), 3) == 0
        except asyncio.TimeoutError:
            p.kill()
    except OSError:
        pass
    _CAM_WANTED.update(at=time.monotonic(), v=v)
    return v


STUDIO: dict = {}   # the last beacon from studiod on the recording machine, and when it came (time.time())


async def beacon(req):
    """POST /beacon: studiod (the recording machine's daemon, the deployment's issue 15077) posts its state every 2 s.
    GET /beacon reads it back. /health carries it as `studio` with `age_s`, so silence from the recorder shows as age."""
    if req.method == "POST":
        try:
            b = await req.json()
        except Exception:  # noqa: BLE001
            return bad("body must be JSON")
        STUDIO.clear(); STUDIO.update(state=b if isinstance(b, dict) else {}, at=time.time())
        return web.json_response({"ok": True})
    return web.json_response(studio_view())


def studio_view() -> dict | None:
    if not STUDIO:
        return None
    s = STUDIO["state"]
    return {"age_s": round(time.time() - STUDIO["at"], 1), "recording": s.get("recording"), "take_id": s.get("take_id"),
            "name": s.get("name"), "beat": s.get("beat"), "obs_up": (s.get("obs") or {}).get("up")}


async def health(req):
    c = S(req.app).call
    return web.json_response({"ok": True, "recording": REC.get("take"), "call": c.id if c else None,
                              "lines": len(c.lines) if c else 0, "feed_clients": len(S(req.app).feed.clients),
                              "script": current_script(req.app).name, "cam_wanted": await cam_wanted(),
                              "vsl": vsl_view(), "studio": studio_view()})


async def openapi(_):
    return web.FileResponse(ROOT / "docs" / "openapi.yaml", headers={"Content-Type": "application/yaml"})


async def page(_):
    return web.FileResponse(HERE / "feed.html")


async def feed(req):
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(req)
    S(req.app).feed.clients.add(ws)
    c = S(req.app).call
    await ws.send_json({"type": "status", "text": f"call {c.id} live" if c else "no call", "t": time.time()})
    if c:   # a window that opens mid-call gets the last lines it would have drawn
        for line in c.lines[-8:]:
            await ws.send_json({"type": "line", **line})
    async for _ in ws:
        pass
    S(req.app).feed.clients.discard(ws)
    return ws


# The desktop app serves its UI from the webview's own origin and calls this service cross-origin: Tauri on Windows
# is https://tauri.localhost, on macOS tauri://localhost; "null" is a file:// page. WebSockets need no CORS.
APP_ORIGINS = {"https://tauri.localhost", "http://tauri.localhost", "tauri://localhost", "null",
               *filter(None, os.environ.get("VIZIER_ALLOWED_ORIGINS", "").split(","))}


@web.middleware
async def cors(req, handler):
    origin = req.headers.get("Origin")
    if req.method == "OPTIONS" and origin in APP_ORIGINS:
        resp = web.Response(status=204)
    else:
        try:
            resp = await handler(req)
        except web.HTTPException as e:   # 400/401/404 reach the app with CORS headers, so it can read the error
            resp = web.Response(status=e.status, text=e.text, content_type=e.content_type)
    if origin in APP_ORIGINS and not resp.prepared:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        resp.headers["Vary"] = "Origin"
    return resp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", help="KEY=VALUE file loaded into the environment (already-set variables win)")
    ap.add_argument("--host", default=os.environ.get("VIZIER_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("VIZIER_PORT", "8786")))
    a = ap.parse_args()
    # A stop must take about a second, not 30 (the deployment's issue 15077): on 2026-11-17 a restart of the display
    # server took 29 s and 32 s because aiohttp's graceful shutdown waited on every open /feed WebSocket, whose handler
    # never ends on its own, and the laptop's tabs showed "failed to fetch" for the whole gap. close_feeds (on_shutdown)
    # closes them with 1001 Going Away, so the app reconnects to the new process; shutdown_timeout caps whatever is left.
    web.run_app(make_app(), host=a.host, port=a.port, print=None, shutdown_timeout=1.0)


async def close_feeds(app: web.Application):
    """on_shutdown: close every /feed WebSocket (1001 Going Away) so the graceful stop is not held open by them."""
    for ws in list(app[STATE].feed.clients):
        try:
            await asyncio.wait_for(ws.close(code=1001, message=b"server restarting"), 0.5)
        except Exception:  # noqa: BLE001
            pass
    app[STATE].feed.clients.clear()


def make_app() -> web.Application:
    app = web.Application(middlewares=[cors])
    app[STATE] = State(Feed(), None, restored_script())
    app.on_shutdown.append(close_feeds)
    r = app.router
    r.add_get("/", page)
    r.add_get("/health", health)
    r.add_get("/openapi.yaml", openapi)
    r.add_post("/call/start", call_start)
    r.add_post("/call/end", call_end)
    r.add_post("/lines", lines)
    r.add_get("/transcript", transcript)
    r.add_post("/bullets", bullets)
    r.add_post("/status", status)
    r.add_post("/script", script)
    r.add_get("/prompter", prompter)
    r.add_get("/prompter/scripts", prompter_scripts)
    r.add_get("/prompter/context", prompter_context)
    r.add_get("/prompter/note", prompter_note)
    r.add_post("/prompter/note", prompter_note)
    r.add_get("/prompter/position", prompter_position)
    r.add_post("/prompter/position", prompter_position)
    r.add_get("/prompter/goto", prompter_goto)
    r.add_get("/prompter/snap", prompter_snap)
    r.add_post("/talk/log", talk_log)
    r.add_get("/talk/loopback", talk_loopback)
    r.add_post("/talk/loopback", talk_loopback)
    r.add_get("/talk/loopback/run", talk_loopback_run)
    r.add_post("/talk/loopback/run", talk_loopback_run)
    r.add_get("/talk/selfcheck", talk_selfcheck)
    r.add_post("/talk/selfcheck", talk_selfcheck)
    r.add_get("/talk/selfcheck/run", talk_selfcheck_run)
    r.add_post("/talk/selfcheck/run", talk_selfcheck_run)
    r.add_get("/talk/transcript", talk_transcript)
    r.add_post("/talk/transcript", talk_transcript)
    r.add_get("/qc", qc)
    r.add_get("/prompter/follow", prompter_follow)
    r.add_post("/prompter/follow", prompter_follow)
    r.add_post("/prompter/snap", prompter_snap)
    r.add_post("/prompter/goto", prompter_goto)
    r.add_post("/prompter/record", prompter_record)
    r.add_get("/feed", feed)
    r.add_get("/buildlog", buildlog)
    r.add_get("/settings", settings)
    r.add_post("/settings", settings)
    r.add_get("/desks", desks)
    r.add_post("/desks/open", desks_open)
    r.add_get("/agents", agents)
    r.add_get("/horus-script", horus_script)
    r.add_get("/speaker-script", speaker_script)
    r.add_post("/speaker-script", write_script)
    r.add_post("/horus-script", write_script)
    r.add_post("/speaker-script/undo", write_undo)
    r.add_post("/horus-script/undo", write_undo)
    r.add_get("/state", state)
    r.add_get("/approval", approval)
    r.add_post("/approve", approve)
    r.add_post("/state/front", state_front)
    r.add_get("/beacon", beacon)
    r.add_post("/beacon", beacon)
    r.add_get("/vsl", vsl)
    r.add_post("/vsl", vsl)
    return app


if __name__ == "__main__":
    main()
