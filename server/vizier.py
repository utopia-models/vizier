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
  WS    /feed     ← {"type": "status"|"line"|"interim"|"suggestion"|"delta"|"done"|"cancel"|"script", ..., "t"}

Configuration is environment variables, optionally loaded from a KEY=VALUE file (see vizier.env.example):
`--config <file>`, else $VIZIER_CONFIG, else ./vizier.env if it exists. A variable already set wins over the file.
Set VIZIER_TOKEN to require `Authorization: Bearer <token>` on every POST.

  vizier.py [--host 127.0.0.1] [--port 8786]
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import re
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
TALK_SCRIPTS = Path(os.environ.get("VIZIER_TALK_SCRIPTS", DATA / "talk-scripts"))   # /prompter/scripts?file=<name>
BRIEFS = Path(os.environ.get("VIZIER_BRIEFS", DATA / "briefs"))                     # /prompter/scripts?brief=<slug>
TAKES = Path(os.environ.get("VIZIER_TAKES", DATA / "takes"))
CAM = os.environ.get("VIZIER_CAM", "")       # an RTSP camera stream a take copies; empty = recording off
BUILD_LOG = Path(os.environ.get("VIZIER_BUILD_LOG", DATA / "build-log.md"))
MAX_LINES = int(os.environ.get("VIZIER_MAX_LINES", "5000"))   # a call keeps at most this many lines in memory
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


async def script(req):
    """POST /script {"markdown": "# Title\\n## 1 · Hook\\nline\\n..."} shows that script on the prompter;
    {"path": "<file on the server>"} points the prompter at a file (re-read on every request, so edits show)."""
    b = await body(req)
    if isinstance(b.get("markdown"), str) and b["markdown"].strip():
        POSTED_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
        POSTED_SCRIPT.write_text(b["markdown"])
        S(req.app).script = POSTED_SCRIPT
    elif isinstance(b.get("path"), str) and b["path"]:
        p = Path(b["path"]).expanduser().resolve()
        roots = [d.resolve() for d in (DATA, SCRIPTS.parent, TALK_SCRIPTS, BRIEFS)]
        if p.suffix != ".md" or not any(p.is_relative_to(r) for r in roots):   # GETs are open: never any file
            return bad("path must be a .md file under VIZIER_DATA, VIZIER_SCRIPTS' folder, the talk-scripts or briefs", 403)
        if not p.is_file():
            return bad(f"no such file: {p.name}", 404)
        S(req.app).script = p
    else:
        return bad('send "markdown" or "path"')
    n = len(parse_takes(S(req.app).script.read_text()))
    await S(req.app).feed.send({"type": "script", "takes": n})
    log_event({"t": time.time(), "ev": "script", "takes": n})
    return web.json_response({"ok": True, "takes": n})


def parse_takes(text: str) -> list[dict]:
    takes = []
    for block in ("\n" + text).split("\n## ")[1:]:
        title, *rest = block.split("\n")
        takes.append({"title": title.strip(), "lines": [l.strip().replace("**", "") for l in rest if l.strip()]})
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


async def prompter_context(req):
    """The prompter's other tabs: CONTEXT is the script's sibling `<name>-context.md`; NOTES are the script's
    blocks that are not numbered parts. Both re-read on every request."""
    src = current_script(req.app)
    ctx = src.with_name(re.sub(r"-script\.md$", "", src.name) + "-context.md") if src.name.endswith("-script.md") else None
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


FOLLOW = {"on": True}   # POST /prompter/follow {"on": bool} or {"toggle": true}: the agent's speech-follow and screen driving


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


async def health(req):
    c = S(req.app).call
    return web.json_response({"ok": True, "recording": REC.get("take"), "call": c.id if c else None,
                              "lines": len(c.lines) if c else 0, "feed_clients": len(S(req.app).feed.clients),
                              "script": current_script(req.app).name})


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
    web.run_app(make_app(), host=a.host, port=a.port, print=None)


def make_app() -> web.Application:
    app = web.Application(middlewares=[cors])
    app[STATE] = State(Feed(), None, SCRIPTS)
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
    r.add_get("/talk/transcript", talk_transcript)
    r.add_post("/talk/transcript", talk_transcript)
    r.add_get("/prompter/follow", prompter_follow)
    r.add_post("/prompter/follow", prompter_follow)
    r.add_post("/prompter/snap", prompter_snap)
    r.add_post("/prompter/goto", prompter_goto)
    r.add_post("/prompter/record", prompter_record)
    r.add_get("/feed", feed)
    r.add_get("/buildlog", buildlog)
    return app


if __name__ == "__main__":
    main()
