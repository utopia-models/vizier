#!/usr/bin/env python3
"""smoke.py: drive a BUILT vizier.exe on a real Windows box, the way its user does, before it is staged.

    python3 scripts/smoke/smoke.py --exe <vizier.exe> --host <ssh host of the Windows box> [--out DIR]
        [--script FILE] [--talk-url ws://127.0.0.1:17880 --livekit-key K --livekit-secret S]

What it does (every step is a PASS/FAIL line, with a screenshot in --out; exit 0 only when every step passes):
  1. Starts a Vizier server here (server/vizier.py, a temp VIZIER_DATA) holding a real script (--script), 25 desks and
     25 agents (so every list is longer than the window), and a DRY VSL switch: start/stop only touch a state file, so
     Alt+R never reaches OBS. ssh -R makes it 127.0.0.1:<port> on the Windows box; ssh -L brings back the DevTools port.
  2. Copies the exe to %PUBLIC%\\vizier\\smoke\\ and starts it in the signed-in user's desktop session (a scheduled
     task) with `--smoke`: WebView2 opens a DevTools port in a profile of its own, no global shortcut is registered (the
     user's own Vizier keeps running and keeps its keys), and `smoke_key` presses Alt+V / Alt+R / Alt+E / Alt+M through
     the same code the shortcuts run (main.rs hotkey()).
  3. Over the DevTools protocol, with real key and mouse-wheel input (Input.dispatchKeyEvent / dispatchMouseEvent):
     the page links to the server (and to LiveKit when --talk-url is given); Tab visits TALK, SETTINGS, DESKS, AGENTS and
     SCRIPT in that order and comes back to TALK; SCRIPT shows the script's text in the window; every tab scrolls to its
     bottom by wheel and by arrow keys, and the selected row is always fully on screen; every SETTINGS row that can change
     changes and changes back; Alt+E and Alt+M flip Ears and Mouth (keys in the page, and the native shortcut when the
     talk link is up); Alt+V hides the window and Alt+V brings it back; Alt+R starts a (dry) take and Alt+R stops it.
  4. Stops the smoke instance and its task, whatever happened.
Needs: ssh to the box (Windows OpenSSH, PowerShell), python3 with aiohttp here (server/requirements.txt).
"""
import argparse, asyncio, base64, hashlib, hmac, json, os, shlex, socket, subprocess, sys, tempfile, time
from pathlib import Path

import aiohttp

REPO = Path(__file__).resolve().parents[2]
RESULTS = []
AUDIO_SECS, ROOM = 5, "vizier-smoke"


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def ok(name, cond, detail=""):
    RESULTS.append((bool(cond), name, detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""), flush=True)
    return bool(cond)


def ssh(host, cmd, timeout=60):
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "LogLevel=ERROR", host, cmd], capture_output=True,
                          text=True, timeout=timeout)


def ps(host, script, timeout=60):
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    return ssh(host, f"powershell -NoProfile -NonInteractive -EncodedCommand {enc}", timeout)


def jwt(key, secret, room, ident, ttl=1800):   # a LiveKit join token (HS256), stdlib only
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    now = int(time.time())
    body = {"iss": key, "sub": ident, "nbf": now - 10, "exp": now + ttl, "name": ident,
            "video": {"roomJoin": True, "room": room, "canPublish": True, "canSubscribe": True, "canPublishData": True,
                      "canUpdateOwnMetadata": True}}
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()) + "." + b64(json.dumps(body).encode())
    return head + "." + b64(hmac.new(secret.encode(), head.encode(), hashlib.sha256).digest())


def fixture(d: Path, script: Path):
    """The server's world: the script, 25 desks, 25 agents, a dry VSL switch."""
    (d / "agents").mkdir(parents=True)
    for i in range(25):
        (d / "agents" / f"agent{i:02}.json").write_text(json.dumps(
            {"agent": f"agent{i:02}", "health": "green", "doing": f"smoke row {i}", "at": "2026-01-01T00:00:00Z"}))
    desks = [{"id": f"d{i}", "name": f"Desk {i}", "agent": f"agent{i:02}"} for i in range(25)]
    (d / "desks.json").write_text(json.dumps(desks))
    (d / "scripts.md").write_text(script.read_text())
    state = d / "vsl-current.json"
    return {"VIZIER_DATA": str(d / "data"), "VIZIER_SCRIPTS": str(d / "scripts.md"),
            "VIZIER_AGENTS_DIR": str(d / "agents"), "VIZIER_DESKS_CMD": f"cat {shlex.quote(str(d / 'desks.json'))}",
            "VIZIER_DESK_OPEN_CMD": "true",
            "VIZIER_VSL_START_CMD": f"echo '{{}}' > {shlex.quote(str(state))}",
            "VIZIER_VSL_STOP_CMD": f"rm -f {shlex.quote(str(state))}", "VIZIER_VSL_STATE": str(state)}, state


class CDP:
    def __init__(self, ws): self.ws, self.n, self.pending, self.log = ws, 0, {}, []

    async def pump(self):
        async for m in self.ws:
            j = json.loads(m.data)
            if "id" in j and j["id"] in self.pending:
                self.pending.pop(j["id"]).set_result(j)
            elif j.get("method") == "Log.entryAdded":   # network refusals (CORS, blocked requests) land here, not in JS
                e = j["params"]["entry"]; self.log.append(f"{e.get('level')} {e.get('source')}: {e.get('text', '')[:200]} {e.get('url', '')[:100]}")
            elif j.get("method") == "Runtime.exceptionThrown":
                d = j["params"]["exceptionDetails"]; self.log.append(f"exception: {d.get('text')} {d.get('exception', {}).get('description', '')[:200]}")
            elif j.get("method") == "Runtime.consoleAPICalled" and j["params"].get("type") in ("error", "warning"):
                self.log.append("console." + j["params"]["type"] + ": " + " ".join(str(a.get("value", a.get("description", ""))) for a in j["params"]["args"])[:200])

    async def call(self, method, **params):
        self.n += 1; f = asyncio.get_event_loop().create_future(); self.pending[self.n] = f
        await self.ws.send_str(json.dumps({"id": self.n, "method": method, "params": params}))
        try:
            j = await asyncio.wait_for(f, 30)
        except asyncio.TimeoutError:
            raise RuntimeError(f"{method} {str(params)[:120]}: no answer in 30 s")
        if "error" in j: raise RuntimeError(f"{method}: {j['error']}")
        return j.get("result", {})

    async def js(self, expr):
        r = await self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r: raise RuntimeError(f"js {expr[:80]}: {r['exceptionDetails'].get('text')} {r.get('result', {}).get('description', '')}")
        return r["result"].get("value")

    async def key(self, key, code=None, vk=None, alt=False):
        code = code or key; mods = 1 if alt else 0
        vk = vk or {"Tab": 9, "Enter": 13, "ArrowDown": 40, "ArrowUp": 38, "ArrowLeft": 37, "ArrowRight": 39,
                    "PageDown": 34, "End": 35}.get(key) or ord(key.upper()[0])
        for t in ("rawKeyDown", "keyUp"):
            await self.call("Input.dispatchKeyEvent", type=t, key=key, code=code, windowsVirtualKeyCode=vk, modifiers=mods)
        await asyncio.sleep(0.12)

    async def wheel(self, x, y, dy):
        await self.call("Input.dispatchMouseEvent", type="mouseWheel", x=x, y=y, deltaX=0, deltaY=dy)
        await asyncio.sleep(0.08)

    async def shot(self, out: Path, name):
        r = await self.call("Page.captureScreenshot", format="png")
        (out / f"{len(list(out.glob('*.png'))):02}-{name}.png").write_bytes(base64.b64decode(r["data"]))


async def until(fn, secs=10, step=0.2):
    end = time.time() + secs; v = None
    while time.time() < end:
        try:
            v = await fn()
            if v: return v
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(step)
    return v


# The laptop mic as the room gets it: the Ears track itself (after the WebRTC processing the app asks for) and, beside it,
# the same mic with every processing step off. 20 ms frames -> RMS dBFS (p10 = the floor between words, where a buzz
# lives), and the share of energy under 300 Hz (mains hum and fan buzz) from an FFT.
AUDIO_JS = """(async (secs) => {
  const st = window.vizierTalk.state, pub = st.mic, track = pub && pub.track && pub.track.mediaStreamTrack;
  const raw = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
  const ac = new AudioContext(); await ac.resume();
  const meter = (stream) => { const a = ac.createAnalyser(); a.fftSize = 2048; ac.createMediaStreamSource(stream).connect(a);
    return { a, frames: [], low: 0, all: 0, peakHz: {} }; };
  const m = { ears: track ? meter(new MediaStream([track])) : null, raw: meter(raw) };
  const t = new Float32Array(2048), f = new Float32Array(1024), binHz = ac.sampleRate / 2048, end = performance.now() + secs * 1000;
  while (performance.now() < end) {
    for (const k in m) { const x = m[k]; if (!x) continue;
      x.a.getFloatTimeDomainData(t); let e = 0; for (const v of t) e += v * v; x.frames.push(Math.sqrt(e / t.length));
      x.a.getFloatFrequencyData(f); let best = 0;
      for (let i = 1; i < f.length; i++) { const p = Math.pow(10, f[i] / 10); x.all += p; if (i * binHz < 300) x.low += p; if (f[i] > f[best]) best = i; }
      const hz = Math.round(best * binHz / 10) * 10; x.peakHz[hz] = (x.peakHz[hz] || 0) + 1; }
    await new Promise(r => setTimeout(r, 20));
  }
  raw.getTracks().forEach(x => x.stop()); await ac.close();
  const db = v => Math.round(20 * Math.log10(Math.max(v, 1e-9)) * 10) / 10;
  const out = { sampleRate: ac.sampleRate, settings: track ? track.getSettings() : null };
  for (const k in m) { const x = m[k]; if (!x) { out[k] = null; continue; }
    const s = x.frames.slice().sort((p, q) => p - q), rms = Math.sqrt(x.frames.reduce((p, v) => p + v * v, 0) / x.frames.length);
    out[k] = { rms_db: db(rms), floor_p10_db: db(s[Math.floor(s.length * .1)]), p50_db: db(s[Math.floor(s.length * .5)]),
      under300Hz_pct: Math.round(100 * x.low / Math.max(x.all, 1e-30)),
      peakHz: Object.entries(x.peakHz).sort((p, q) => q[1] - p[1]).slice(0, 3).map(e => +e[0]) }; }
  return out;
})"""

MODE = "document.body.dataset.mode + '/' + (window.vizierPanels ? '' : '') + (document.querySelector('header .tab.on')||{}).id"


async def scroll_check(c, out, sel, name):
    """Wheel the scroller to its bottom, then back; -> PASS when it moves and reaches the bottom (or never overflowed)."""
    g = await c.js(f"(()=>{{const e=document.querySelector({json.dumps(sel)});const r=e.getBoundingClientRect();"
                   f"return {{x:r.x+r.width/2,y:r.y+Math.min(r.height/2,120),sh:e.scrollHeight,ch:e.clientHeight,top:e.scrollTop}}}})()")
    if g["sh"] <= g["ch"] + 1:
        return ok(f"{name}: wheel scroll (fits, nothing to scroll)", True, f"{g['sh']}<={g['ch']}")
    for _ in range(40):
        await c.wheel(g["x"], g["y"], 240)
    b = await c.js(f"(()=>{{const e=document.querySelector({json.dumps(sel)});return {{top:e.scrollTop,max:e.scrollHeight-e.clientHeight}}}})()")
    await c.shot(out, f"{name}-wheel-bottom")
    good = ok(f"{name}: the mouse wheel scrolls it to the bottom", b["top"] >= b["max"] - 2, f"scrollTop {b['top']:.0f} of {b['max']:.0f}")
    for _ in range(40):
        await c.wheel(g["x"], g["y"], -240)
    return good


async def sel_visible(c, pid):
    return await c.js(f"(()=>{{const p=document.getElementById('{pid}'),r=p.querySelector('.row.sel');if(!r)return null;"
                      "const a=p.getBoundingClientRect(),b=r.getBoundingClientRect(),"
                      "lg=[...document.querySelectorAll('.keylegend,#keylegend')].filter(x=>x.offsetParent!==null||getComputedStyle(x).position==='fixed').map(x=>x.getBoundingClientRect()).filter(x=>x.height>0&&x.top>a.top);"
                      "const bottom=Math.min(a.bottom,...lg.map(x=>x.top));return {i:+r.dataset.i,top:b.top,bottom:b.bottom,ptop:a.top,pbottom:bottom,in:b.top>=a.top-1&&b.bottom<=bottom+1}})()")


async def drive(c: CDP, out: Path, talk: bool, state: Path):
    await c.call("Page.enable"); await c.call("Runtime.enable"); await c.call("Log.enable")
    ok("the page loaded", await until(lambda: c.js("!!window.vizierPanels && !!window.vizierTalk"), 20))
    ok("the page linked to the server (feed)", await until(lambda: c.js("document.getElementById('dot').classList.contains('on')"), 15))
    got = await until(lambda: c.js("document.querySelectorAll('#text p').length > 0"), 15)
    why = "" if got else await c.js("fetch(SERVER + '/prompter/scripts').then(r => r.status + ' ' + r.headers.get('access-control-allow-origin')).catch(e => 'fetch error: ' + e)")
    ok("the script reached the page", got, f"{await c.js('document.querySelectorAll(\"#text p\").length')} lines; mode {await c.js('mode')}; takes {await c.js('takes.length')}; origin {await c.js('location.origin')} {why}")
    if talk:
        ok("the talk link joined LiveKit", await until(lambda: c.js("!!window.vizierTalk.state.room"), 20),
           str(await c.js("window.vizierTalk.state.status")))
    await c.shot(out, "start")
    if talk and AUDIO_SECS:
        await until(lambda: c.js("!!window.vizierTalk.state.mic"), 8)
        print("AUDIO-DEVICES " + json.dumps(await c.js("(async()=>{const st=vizierTalk.state;const d=await navigator.mediaDevices.enumerateDevices();"
              "return {status:st.status,room:st.room&&st.room.name,me:st.room&&st.room.localParticipant.identity,mic:!!st.mic,"
              "devices:d.map(x=>x.kind+':'+x.label)}})()")), flush=True)
        print(f"AUDIO-HOLD {AUDIO_SECS}s: Ears and Mouth on, in room {ROOM}", flush=True)
        r = await c.js(f"{AUDIO_JS}({AUDIO_SECS})")
        print("AUDIO " + json.dumps(r), flush=True)
        ok("audio: the Ears track is the processed mic (echo cancellation, noise suppression, auto gain on)",
           r.get("ears") and all((r.get("settings") or {}).get(k) is not False for k in ("echoCancellation", "noiseSuppression", "autoGainControl")),
           f"ears {r.get('ears')} raw {r.get('raw')} settings {r.get('settings')}")

    # ---- Tab: TALK -> SETTINGS -> DESKS -> AGENTS -> SCRIPT -> TALK
    seen = []
    for _ in range(6):
        await c.key("Tab"); await asyncio.sleep(0.25)
        m = await c.js("document.body.dataset.mode + (document.body.dataset.mode==='rec' ? ':' + (document.querySelector('#tab-rec.on') ? 'script' : 'other') : '')")
        seen.append(m); await c.shot(out, f"tab-{m.replace(':', '-')}")
    want = ["talk", "settings", "desks", "agents", "rec:script", "talk"]
    ok("Tab visits TALK, SETTINGS, DESKS, AGENTS, SCRIPT and back to TALK", seen == want, " > ".join(seen))

    # ---- every tab to the bottom: wheel, then keys with the selection on screen
    await c.js("window.vizierPanels.goTab('talk')")
    await c.js("(()=>{const b=document.getElementById('talkmain');for(let i=0;i<60;i++){const p=document.createElement('p');p.className='ln me';p.textContent='smoke line '+i;b.appendChild(p)}})()")
    await scroll_check(c, out, "#talkmain", "TALK")

    for tab, pid in (("settings", "p-settings"), ("desks", "p-desks")):
        await c.js(f"window.vizierPanels.goTab('{tab}')"); await asyncio.sleep(0.6)
        await until(lambda: c.js(f"document.querySelectorAll('#{pid} .row').length > 1"), 8)
        await scroll_check(c, out, f"#{pid}", tab.upper())
        n = await c.js(f"document.querySelectorAll('#{pid} .row').length")
        await c.js(f"window.vizierPanels.panel['{tab}'].sel = 0; document.getElementById('{pid}').scrollTop = 0")
        await c.js("drawSettings()" if tab == "settings" else "drawDesks()")
        bad = []
        for i in range(n + 1):
            v = await sel_visible(c, pid)
            if not (v and v["in"]): bad.append((i, v))
            if i < n: await c.key("ArrowDown")
        await c.shot(out, f"{tab}-arrows-bottom")
        last = await c.js(f"window.vizierPanels.panel['{tab}'].sel")
        ok(f"{tab.upper()}: arrow-down reaches the last row, and the selected row is on screen at every step",
           not bad and last == n - 1, f"{n} rows, last sel {last}" + (f", off screen at {bad[:2]}" if bad else ""))
        # the global Down (main.rs -> vizierStep), the path the laptop takes while another app has focus
        await c.js(f"window.vizierPanels.panel['{tab}'].sel = 0; document.getElementById('{pid}').scrollTop = 0; " + ("drawSettings()" if tab == "settings" else "drawDesks()"))
        for _ in range(n): await c.js("window.vizierStep(1)")
        v = await sel_visible(c, pid)
        ok(f"{tab.upper()}: the global Down (vizierStep) keeps the selected row on screen", v and v["in"], str(v))

    await c.js("window.vizierPanels.goTab('agents')"); await asyncio.sleep(0.8)
    await until(lambda: c.js("document.querySelectorAll('#p-agents .row').length > 1"), 8)
    await scroll_check(c, out, "#p-agents", "AGENTS")

    await c.js("window.vizierPanels.goTab('script')"); await asyncio.sleep(0.4)
    vis = await c.js("(()=>{const p=document.querySelector('#text p.cur');if(!p)return null;const r=p.getBoundingClientRect();return {t:p.textContent.slice(0,60),top:r.top,bottom:r.bottom,h:innerHeight,disp:getComputedStyle(document.getElementById('prompt')).display}})()")
    ok("SCRIPT shows the script's text inside the window", vis and vis["t"] and vis["disp"] != "none" and 0 <= vis["top"] < vis["h"], str(vis))
    n = await c.js("document.querySelectorAll('#text p').length")
    for _ in range(n + 2): await c.key("ArrowDown")
    cur = await c.js("(()=>{const ps=[...document.querySelectorAll('#text p')],p=document.querySelector('#text p.cur'),r=p.getBoundingClientRect();return {i:ps.indexOf(p),n:ps.length,top:r.top,h:innerHeight}})()")
    await c.shot(out, "script-bottom")
    ok("SCRIPT: arrow-down reaches the last line of the part and it is on screen", cur["i"] == cur["n"] - 1 and 0 <= cur["top"] < cur["h"], str(cur))
    await c.key("Home"); await scroll_check(c, out, "#text", "SCRIPT")

    # ---- every SETTINGS row that can change: change it, then change it back
    await c.js("window.vizierPanels.goTab('settings')"); await asyncio.sleep(0.6)
    rows = await c.js("window.vizierPanels.settingRows().map(r=>({k:r.k,ro:!!r.ro}))")
    for i, r in enumerate(rows):
        if r["ro"] or r["k"] == "vsl": continue   # vsl: the Alt+R steps below drive the same switch
        if r["k"] in ("ears", "mouth") and not talk:
            ok(f"SETTINGS {r['k']}: changes (needs the talk link; run with --talk-url)", True, "skipped: no talk link"); continue
        if r["k"] == "voice" and not await c.js("((window.vizierPanels.panel.settings.data||{}).voices||[]).length"):
            ok("SETTINGS voice: changes", True, "skipped: no voice agent on the smoke server"); continue
        if r["k"].startswith("wake") and not await c.js("!!(window.vizierPanels.panel.settings.data||{}).wake"):
            ok(f"SETTINGS {r['k']}: changes", True, "skipped: no voice agent on the smoke server"); continue
        await c.js(f"window.vizierPanels.panel.settings.sel = {i}; drawSettings()")
        before = await c.js(f"document.querySelectorAll('#p-settings .row')[{i}].querySelector('.v').textContent")
        await c.key("ArrowRight"); await asyncio.sleep(0.5)
        after = await c.js(f"document.querySelectorAll('#p-settings .row')[{i}].querySelector('.v').textContent")
        await c.shot(out, f"settings-{r['k']}")
        await c.key("ArrowLeft" if r["k"] in ("voice", "wake_call") else "ArrowRight"); await asyncio.sleep(0.5)
        back = await c.js(f"document.querySelectorAll('#p-settings .row')[{i}].querySelector('.v').textContent")
        ok(f"SETTINGS {r['k']}: changes and changes back", before != after and back == before, f"{before!r} -> {after!r} -> {back!r}")

    # ---- Alt+E / Alt+M: the keys in the page, then the native shortcut (needs the talk link)
    for k, name in (("e", "ears"), ("m", "mouth")):
        a = await c.js(f"window.vizierTalk.state.{name}")
        await c.key(k, code=f"Key{k.upper()}", alt=True); await asyncio.sleep(0.4)
        b = await c.js(f"window.vizierTalk.state.{name}")
        await c.shot(out, f"alt-{k}")
        await c.key(k, code=f"Key{k.upper()}", alt=True); await asyncio.sleep(0.4)
        z = await c.js(f"window.vizierTalk.state.{name}")
        ok(f"Alt+{k.upper()} flips {name.capitalize()} (key in the page) and flips it back", a != b and z == a, f"{a} -> {b} -> {z}")
        if talk:
            await c.js(f"window.__TAURI__.core.invoke('smoke_key', {{key: 'alt+{k}'}})"); await asyncio.sleep(0.5)
            b2 = await c.js(f"window.vizierTalk.state.{name}")
            await c.js(f"window.__TAURI__.core.invoke('smoke_key', {{key: 'alt+{k}'}})"); await asyncio.sleep(0.5)
            ok(f"Alt+{k.upper()} global shortcut flips {name.capitalize()}", b2 != a and await c.js(f"window.vizierTalk.state.{name}") == a)

    # ---- Alt+V: hide, then back
    m1 = await c.js("window.__TAURI__.core.invoke('smoke_key', {key: 'alt+v'})"); await asyncio.sleep(0.6)
    m2 = await c.js("window.__TAURI__.core.invoke('smoke_key', {key: 'alt+v'})"); await asyncio.sleep(0.6)
    await c.shot(out, "alt-v-back")
    ok("Alt+V hides the window, and Alt+V brings it back", m1 is True and m2 is False, f"minimized {m1} then {m2}")

    # ---- Alt+R (dry: the smoke server's VSL commands only touch a file)
    await c.js("window.vizierPanels.goTab('settings')")
    await c.js("window.__TAURI__.core.invoke('smoke_key', {key: 'alt+r'})")
    on = await until(lambda: c.js("document.getElementById('vsl').classList.contains('on')"), 6)
    started = await until(lambda: asyncio.sleep(0, state.exists()), 8)
    await c.shot(out, "alt-r-on")
    await c.js("window.__TAURI__.core.invoke('smoke_key', {key: 'alt+r'})")
    off = await until(lambda: c.js("!document.getElementById('vsl').classList.contains('on')"), 6)
    stopped = await until(lambda: asyncio.sleep(0, not state.exists()), 8)
    await c.shot(out, "alt-r-off")
    ok("Alt+R starts a (dry) take and Alt+R stops it; the REC pill follows", on and started and off and stopped,
       f"pill on={on} take started={started} pill off={off} take stopped={stopped}")
    errs = await c.js("(window.__smokeErrors||[]).slice(0,5)")
    ok("no uncaught page errors", not errs, str(errs))


async def run(a):
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*.png"): f.unlink()
    tmp = Path(tempfile.mkdtemp(prefix="vizier-smoke-"))
    env, state = fixture(tmp, Path(a.script))
    sport, cport = free_port(), a.cdp_port
    srv = subprocess.Popen([sys.executable, str(REPO / "server" / "vizier.py"), "--host", "127.0.0.1", "--port", str(sport)],
                           env={**os.environ, **env}, stdout=subprocess.DEVNULL, stderr=open(tmp / "server.log", "w"))
    tun = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-o", "LogLevel=ERROR", "-o", "ExitOnForwardFailure=yes", "-N",
                            "-R", f"127.0.0.1:{a.remote_port}:127.0.0.1:{sport}", "-L", f"127.0.0.1:{cport}:127.0.0.1:{cport}", a.host])
    d = r"C:\Users\Public\vizier\smoke"
    talk = bool(a.talk_url and a.livekit_key and a.livekit_secret)
    cfg = {"server": f"http://127.0.0.1:{a.remote_port}", "hideLabels": ["scout"]}
    if talk:
        cfg.update(talkUrl=a.talk_url, talkToken=jwt(a.livekit_key, a.livekit_secret, a.room, "vizier-smoke"))
    try:
        r = ps(a.host, f"New-Item -ItemType Directory -Force '{d}' | Out-Null; Remove-Item '{d}\\talk.log' -ErrorAction SilentlyContinue; Stop-ScheduledTask vizier-smoke -ErrorAction SilentlyContinue; "
                       f"Get-CimInstance Win32_Process -Filter \"name='vizier.exe'\" | Where-Object {{ $_.CommandLine -match '--smoke' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}; 'ready'")
        if "ready" not in r.stdout: return ok("the Windows box is reachable", False, r.stderr.strip()[:200])
        (tmp / "smoke.json").write_text(json.dumps(cfg))
        for src, dst in ((a.exe, "vizier.exe"), (tmp / "smoke.json", "smoke.json")):
            cp = subprocess.run(["scp", "-q", "-o", "BatchMode=yes", str(src), f"{a.host}:C:/Users/Public/vizier/smoke/{dst}"], capture_output=True, text=True)
            if cp.returncode: return ok(f"copy {dst} to the box", False, cp.stderr.strip()[:200])
        user = a.user or ps(a.host, "(Get-ScheduledTask vizier-app).Principal.UserId").stdout.strip()
        r = ps(a.host, f"""$a = New-ScheduledTaskAction -Execute '{d}\\vizier.exe' -Argument '--smoke --smoke-port={cport} --config={d}\\smoke.json' -WorkingDirectory '{d}'
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::FromMinutes(15))
$p = New-ScheduledTaskPrincipal -UserId '{user}' -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName vizier-smoke -Action $a -Settings $s -Principal $p -Force | Out-Null; Start-ScheduledTask vizier-smoke; 'started'""")
        if not ok("the built exe started in the user's session (--smoke)", "started" in r.stdout, (r.stderr or r.stdout).strip()[:200]): return
        async with aiohttp.ClientSession() as http:
            async def page():
                async with http.get(f"http://127.0.0.1:{cport}/json") as j:
                    return next((t for t in await j.json() if t.get("type") == "page"), None)
            t = await until(page, 30, 0.5)
            if not ok("WebView2 DevTools answered", t, f"port {cport}"): return
            async with http.ws_connect(t["webSocketDebuggerUrl"], max_msg_size=0) as ws:
                c = CDP(ws); pump = asyncio.ensure_future(c.pump())
                await c.call("Runtime.evaluate", expression="window.__smokeErrors=[];addEventListener('error',e=>__smokeErrors.push(String(e.message)));addEventListener('unhandledrejection',e=>__smokeErrors.push(String(e.reason)))")
                try:
                    await drive(c, out, talk, state)
                except Exception as e:  # noqa: BLE001
                    ok("the run finished", False, f"{type(e).__name__}: {e}")
                    try: await c.shot(out, "crash")
                    except Exception: pass  # noqa: BLE001,E701
                for l in list(dict.fromkeys(c.log))[:15]: print("  page log:", l)
                tl = ps(a.host, f"Get-Content '{d}\\talk.log' -Tail 30 -ErrorAction SilentlyContinue").stdout
                ok("talk.log records the page's link (headless-readable)", "talk.js loaded" in tl and "feed linked" in tl
                   and (not talk or f"joined room {a.room}" in tl), " | ".join(tl.strip().splitlines()[-4:])[:300])
                pump.cancel()
    finally:
        ps(a.host, "Stop-ScheduledTask vizier-smoke -ErrorAction SilentlyContinue; Get-CimInstance Win32_Process -Filter \"name='vizier.exe'\" | "
                   "Where-Object { $_.CommandLine -match '--smoke' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; "
                   "Unregister-ScheduledTask vizier-smoke -Confirm:$false -ErrorAction SilentlyContinue")
        tun.terminate(); srv.terminate()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--exe", required=True); ap.add_argument("--host", required=True)
    ap.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "vizier-smoke"))
    ap.add_argument("--script", default=str(Path(__file__).with_name("sample-script.md")))
    ap.add_argument("--user", default="", help="Windows user the app runs as (default: the vizier-app task's)")
    ap.add_argument("--cdp-port", type=int, default=9339); ap.add_argument("--remote-port", type=int, default=18799)
    ap.add_argument("--talk-url", default=""); ap.add_argument("--room", default="vizier-smoke")
    ap.add_argument("--livekit-key", default=os.environ.get("LIVEKIT_API_KEY", ""))
    ap.add_argument("--livekit-secret", default=os.environ.get("LIVEKIT_API_SECRET", ""))
    ap.add_argument("--audio-seconds", type=float, default=5, help="measure the laptop mic this long (needs the talk link); 0 = skip")
    a = ap.parse_args()
    global AUDIO_SECS, ROOM
    AUDIO_SECS, ROOM = a.audio_seconds, a.room
    asyncio.run(run(a))
    bad = [r for r in RESULTS if not r[0]]
    print(f"\n{'PASS' if RESULTS and not bad else 'FAIL'}: {len(RESULTS) - len(bad)}/{len(RESULTS)} steps; screenshots in {a.out}")
    sys.exit(0 if RESULTS and not bad else 1)


if __name__ == "__main__":
    main()
