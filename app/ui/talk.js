// Talk to an agent through a LiveKit room: Ears (your mic into the room) and Mouth (the agent's voice out of it),
// each a toggle. In the desktop app Alt+E toggles Ears and Alt+M toggles Mouth from anywhere (main.rs registers them
// as global shortcuts); in a plain browser the same keys work while this page has focus.
//
// Config (the app's `config` command, i.e. config.json / vizier.json beside the exe, or ?talkUrl=&talkToken=):
//   talkUrl    the LiveKit server, e.g. wss://livekit.example.com
//   talkToken  a join token for the agent's room (publish + subscribe + update own attributes)
// Nothing here transcribes or stores audio: an agent process in the same room does that and answers in its own voice.
// The switches are this participant's attributes, ears=on|off and mouth=on|off, so the agent side reads them with any
// LiveKit SDK. Ears off also mutes the mic track; Mouth off also unsubscribes from the agent's audio here.
// livekit-client 2.15.7 ships inside the app (ui/livekit-client.esm.js, the package's own self-contained ESM build): the
// CDN import it replaced meant no join at all, and no trace of why, whenever that fetch did not load (the user 2026-11-15:
// Vizier never tried to join room scout). Every join step is written to %PUBLIC%\\vizier\\talk.log (tlog), headless-readable.
import { AudioPresets, Room, RoomEvent, Track } from "./livekit-client.esm.js";

export function tlog(msg) {
  const line = `${new Date().toISOString()} ${msg}`;
  console.log("talk:", msg);
  try { if (window.__TAURI__) window.__TAURI__.core.invoke("talk_log", { line }).catch(() => {}); } catch (e) { /* no shell */ }
}
tlog("talk.js loaded");

// Ears and Mouth are ON at every start (the deployment's issue 13786, the user 2026-11-15: "I need Scout on demand at all times"; the
// agent's voice must be heard on the laptop every time). A switch kept off from an earlier session silenced the agent
// on the next launch with nothing on screen saying why (fresh WebView profile: both off, so nothing was ever heard).
// When the agent speaks is the room's job (its ears process answers only a line that names it), not a stale switch.
// Alt+E / Alt+M still turn either off for this session. vizier.json "talkStartOff": ["ears"|"mouth"] opts out (start()).
const KEEP = "vizier.talk";   // kept for the record (last session's choice); never read back as the start state
const state = { ears: true, mouth: true, room: null, mic: null, audio: [], status: "off" };
let started = false;
const keep = () => { try { localStorage.setItem(KEEP, JSON.stringify({ ears: state.ears, mouth: state.mouth })); } catch (e) { /* no storage */ } };

function pill() {
  let el = document.getElementById("vizier-talk");
  if (el) return el;
  el = document.createElement("div");
  el.id = "vizier-talk";
  el.setAttribute("role", "status");
  el.style.cssText = "position:fixed;top:6px;right:8px;z-index:2147483647;display:flex;gap:10px;align-items:center;" +
    "padding:3px 10px;border-radius:999px;background:rgba(13,15,18,.86);border:1px solid #2f4560;color:#eef1f5;" +
    "font:600 12px/1.6 system-ui,sans-serif;user-select:none;pointer-events:auto";
  el.innerHTML = '<span data-k="ears" title="Alt+E">Ears <b>●</b></span><span data-k="mouth" title="Alt+M">Mouth <b>●</b></span>';
  el.querySelectorAll("[data-k]").forEach((s) => s.addEventListener("click", () => toggle(s.dataset.k, "click")));
  document.body.appendChild(el);
  return el;
}

function paint() {
  const el = pill();
  for (const k of ["ears", "mouth"]) {
    const dot = el.querySelector(`[data-k="${k}"] b`);
    dot.style.color = state.room ? (state[k] ? "#3ddc84" : "#5b6472") : "#c0392b";
  }
  el.title = state.room ? `${state.room.name} · ${state.status}` : `not connected · ${state.status}`;
  el.dataset.ears = state.ears ? "on" : "off";
  el.dataset.mouth = state.mouth ? "on" : "off";
  el.dataset.connected = state.room ? "yes" : "no";
}

async function publishSwitches() {
  if (!state.room) return;
  try {
    await state.room.localParticipant.setAttributes({ ears: state.ears ? "on" : "off", mouth: state.mouth ? "on" : "off" });
  } catch (e) { console.warn("talk: attributes not set", e); }
}

async function applyEars() {
  if (!state.room) return;
  if (state.ears && !state.mic) {
    // Stock WebRTC processing on the capture (Chrome's AEC3 echo canceller, noise suppression, AGC; voiceIsolation where
    // the platform has it), one channel. Published with DTX and RED off (the user 2026-11-15 3:13 PM, "a lot of buzz
    // with Vizier"): with DTX on, a quiet mic sends sparse comfort-noise packets, and what the agent decoded from the
    // laptop was 536 Hz bursts at -30.7 dBFS RMS while the mic itself read -109 dBFS (scripts/smoke, AUDIO step).
    await state.room.localParticipant.setMicrophoneEnabled(true,
      { echoCancellation: true, noiseSuppression: true, autoGainControl: true, voiceIsolation: true, channelCount: 1 },
      { dtx: false, red: false, audioPreset: AudioPresets.speech });
    state.mic = state.room.localParticipant.getTrackPublication(Track.Source.Microphone);
  } else if (state.mic) {
    await state.room.localParticipant.setMicrophoneEnabled(state.ears);
  }
}

function applyMouth() {
  // Mouth off = the agent's audio is not even received here (unsubscribed), not just a muted element: LiveKit unmutes
  // attached elements on its own (measured: turning Ears on unmuted the agent while Mouth was off)
  if (!state.room) return;
  for (const p of state.room.remoteParticipants.values())
    for (const pub of p.trackPublications.values())
      if (pub.kind === Track.Kind.Audio) pub.setSubscribed(state.mouth);
}

let serverBase = "";
function note(src) {   // who flipped a switch, for the audit (so a switch that changed by itself can be traced)
  if (!serverBase) return;
  fetch(serverBase + "/talk/log", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ src, ears: state.ears, mouth: state.mouth }) }).catch(() => {});
}

export async function toggle(k, src = "js") {
  if (k !== "ears" && k !== "mouth") return;
  state[k] = !state[k];
  keep();
  note(`${src}:${k}`);
  paint();
  try {
    if (k === "ears") await applyEars(); else applyMouth();
    await publishSwitches();
  } catch (e) {
    state.status = `${k}: ${e.message || e}`;
    tlog(`${k} failed: ${e.message || e}`);
    console.error("talk", e);
  }
  paint();
}

async function creds(cfg) {
  const q = new URLSearchParams(location.search);
  return { url: q.get("talkUrl") || cfg.talkUrl, token: q.get("talkToken") || cfg.talkToken };
}

export async function start(cfg = {}) {
  if (!started) {                        // the start state, once per launch (a reconnect keeps this session's toggles)
    started = true;
    for (const k of (cfg.talkStartOff || [])) if (k === "ears" || k === "mouth") state[k] = false;
  }
  serverBase = (cfg.server || "").replace(/\/$/, "");
  const { url, token } = await creds(cfg);
  if (!url || !token) { tlog(`not configured: talkUrl ${url ? "set" : "missing"}, talkToken ${token ? "set" : "missing"}`); return; }
  tlog(`joining ${url} (ears ${state.ears ? "on" : "off"}, mouth ${state.mouth ? "on" : "off"})`);
  pill(); paint();
  const room = new Room({ adaptiveStream: false, dynacast: false });
  room.on(RoomEvent.TrackSubscribed, (track) => {
    if (track.kind !== Track.Kind.Audio) return;
    const a = track.attach();
    a.style.display = "none";
    document.body.appendChild(a);
    state.audio.push(a);
    if (sc.out && a.setSinkId) a.setSinkId(sc.out.deviceId).catch((e) => tlog(`sink: ${e.message || e}`));
    watchVoice(track);
  });
  // No local barge-in mute: the agent keeps talking over any sound in the room, and only its own name, heard by the
  // agent process in the room, stops it. A local mute on "mic is an active speaker" muted the agent whenever the
  // laptop mic heard anything, including the agent's own playback, so the user heard nothing.
  room.on(RoomEvent.TrackPublished, (pub) => { if (pub.kind === Track.Kind.Audio) pub.setSubscribed(state.mouth); });
  room.on(RoomEvent.TrackUnsubscribed, (track) => {
    track.detach().forEach((a) => { a.remove(); state.audio = state.audio.filter((x) => x !== a); });
  });
  room.on(RoomEvent.Disconnected, (reason) => {
    tlog(`disconnected (${reason}); rejoining in 3 s`);
    state.room = null; state.mic = null; state.status = "reconnecting"; paint();
    setTimeout(() => start(cfg), 3000);
  });
  try {
    // A connect that never settles would leave no retry at all: 20 s, then it counts as a failure and is retried.
    await Promise.race([room.connect(url, token, { autoSubscribe: false }),
      new Promise((_, no) => setTimeout(() => no(new Error("no answer in 20 s")), 20000))]);
    state.room = room; state.status = "connected";
    tlog(`joined room ${room.name} as ${room.localParticipant.identity}`);
    note("restore");   // what this start brings back: the state the user last set, from localStorage
    applyMouth();
    await applyEars();
    await publishSwitches();
    room.on(RoomEvent.ParticipantAttributesChanged, (changed, p) => {
      if (p !== room.localParticipant && changed.mode === "conversation") selfCheck("conversation");
    });
    selfCheck("start");
  } catch (e) {
    state.status = `connect failed: ${e.message || e}`;
    tlog(`join failed: ${e.message || e}; retry in 5 s`);
    try { room.disconnect(); } catch (x) { /* never connected */ }
    setTimeout(() => start(cfg), 5000);
  }
  paint();
}

// Loopback check: which speakers does this machine's mic hear? For each audio output it plays a short
// tone through that device (setSinkId) and measures the tone's level on the mic twice: raw (no echo cancellation) and
// with the same processing the Ears track uses (echoCancellation on). heard = the raw mic picks the tone up 12 dB over
// the room; cancelled_db = how far the echo canceller brought it down. A speaker the mic hears and the canceller does
// not remove is where the agent's own voice comes back as the user's words. The report goes to the server
// (POST /talk/loopback; GET returns the last one) and is returned. Run it from the console: await vizierTalk.loopback()
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function toneLevel(analyser, ac, freq, ms) {
  const bins = new Float32Array(analyser.frequencyBinCount);
  const bin = Math.round(freq / (ac.sampleRate / analyser.fftSize));
  let peak = -200;
  const end = performance.now() + ms;
  while (performance.now() < end) {
    analyser.getFloatFrequencyData(bins);
    peak = Math.max(peak, bins[bin - 1] ?? -200, bins[bin], bins[bin + 1] ?? -200);
    await sleep(25);
  }
  return Math.round(peak * 10) / 10;
}

async function micProbe(ac, constraints) {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: constraints });
  const analyser = ac.createAnalyser();
  analyser.fftSize = 4096;
  analyser.smoothingTimeConstant = 0;
  ac.createMediaStreamSource(stream).connect(analyser);
  return { stream, analyser };
}

export async function loopback({ freq = 1000, ms = 900 } = {}) {
  const ac = new AudioContext();
  const raw = await micProbe(ac, { echoCancellation: false, noiseSuppression: false, autoGainControl: false });
  const aec = await micProbe(ac, { echoCancellation: true, noiseSuppression: true, autoGainControl: true });
  const outs = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "audiooutput");
  const room = { raw: await toneLevel(raw.analyser, ac, freq, 500), aec: await toneLevel(aec.analyser, ac, freq, 500) };
  const devices = [];
  for (const d of outs) {
    const row = { label: d.label || "(no label)", deviceId: d.deviceId };
    try {
      const out = new AudioContext();
      const osc = out.createOscillator();
      const gain = out.createGain();
      const dest = out.createMediaStreamDestination();
      osc.frequency.value = freq;
      gain.gain.value = 0.3;
      osc.connect(gain).connect(dest);
      const el = new Audio();
      el.srcObject = dest.stream;
      if (el.setSinkId) await el.setSinkId(d.deviceId);
      osc.start();
      await el.play();
      await sleep(150);
      const [r, a] = await Promise.all([toneLevel(raw.analyser, ac, freq, ms), toneLevel(aec.analyser, ac, freq, ms)]);
      osc.stop(); el.pause(); el.srcObject = null; await out.close();
      Object.assign(row, { level_db: r, level_aec_db: a, over_room_db: Math.round((r - room.raw) * 10) / 10,
        heard: r - room.raw >= 12, cancelled_db: Math.round((r - a) * 10) / 10 });
    } catch (e) {
      row.error = String(e.message || e);
    }
    devices.push(row);
    await sleep(300);
  }
  [raw, aec].forEach((p) => p.stream.getTracks().forEach((t) => t.stop()));
  await ac.close();
  const report = { t: new Date().toISOString(), freq, room_db: room, devices,
    talk_output: state.audio[0]?.sinkId || "default" };
  if (serverBase) fetch(serverBase + "/talk/loopback", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(report) }).catch(() => {});
  console.table(devices.map(({ deviceId, ...r }) => r));
  return report;
}

// Device self-check (the deployment's issue 14085). The case: a Bluetooth DJI Mic Mini paired mid-session added
// "Headset (DJI Mic Mini Hands-Free)" as an output, the OS made it the default, and the agent's voice went to a device
// with no speaker (13 min 59 s of silence). Three checks, on joining the room, on every device change (a pairing fires
// devicechange; the check runs 0.8 s later, so a pairing is fixed in about a second) and on every entry to the agent's
// conversation mode (its `mode` attribute):
//   playback  the agent's audio plays on a laptop speaker output, never a Hands-Free/headset/DJI one. Fix: setSinkId
//             on every agent audio element (the app's own output, so it holds whatever the OS default becomes).
//   mic       the Ears track is on the DJI mic, and the raw mic is above digital silence. Fix: switch the track to it.
//   heard     the next time the agent speaks after a check, the raw mic (no echo cancellation) rises 10 dB over its
//             own level within 1 s. No tone is played (the room makes no sound of its own): the agent's own voice is
//             the probe. Not heard -> re-pin the output once; still not heard -> an unfixed failure.
// Each run POSTs its rows to the server (/talk/selfcheck), which adds the floor row, keeps them in
// DATA/talk-selfcheck.jsonl and has the agent speak one line only for a failure with no fix.
const BAD_OUT = /hands[- ]?free|headset|dji/i;
const DJI = /dji/i;
const MIC_FLOOR_DB = -90;      // digital silence (a muted/absent capture) reads far below this; a live mic in a quiet room is above it
const HEARD_DB = 10, HEARD_MS = 1000;
const sc = { out: null, running: false, again: null, pending: null, ac: null, mic: null, micId: null, voices: [] };
const db = (x) => Math.round((x > 0 ? 20 * Math.log10(x) : -200) * 10) / 10;

function rms(analyser) {
  const buf = new Float32Array(analyser.fftSize);
  analyser.getFloatTimeDomainData(buf);
  let s = 0;
  for (const v of buf) s += v * v;
  return db(Math.sqrt(s / buf.length));
}

function realDevices(list, kind) {   // "default"/"communications" are aliases of a real device: skip them
  return list.filter((d) => d.kind === kind && d.deviceId && d.deviceId !== "default" && d.deviceId !== "communications");
}

async function rawMic(deviceId) {    // one raw capture of the Ears device (no AEC/NS/AGC), shared by the mic and heard checks
  if (sc.mic && sc.micId === deviceId) return sc.mic;
  if (sc.mic) sc.mic.stream.getTracks().forEach((t) => t.stop());
  sc.ac = sc.ac || new AudioContext();
  const stream = await navigator.mediaDevices.getUserMedia({ audio: {
    ...(deviceId ? { deviceId: { exact: deviceId } } : {}), echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
  const analyser = sc.ac.createAnalyser();
  analyser.fftSize = 2048;
  sc.ac.createMediaStreamSource(stream).connect(analyser);
  sc.mic = { stream, analyser };
  sc.micId = deviceId;
  return sc.mic;
}

async function pinOutput(target) {
  let moved = 0;
  for (const a of state.audio) {
    if (!a.setSinkId) throw new Error("setSinkId unsupported");
    if (a.sinkId !== target.deviceId) { await a.setSinkId(target.deviceId); moved++; }
  }
  return moved;
}

async function checkPlayback(devs) {
  const outs = realDevices(devs, "audiooutput");
  const def = devs.find((d) => d.kind === "audiooutput" && d.deviceId === "default");
  const good = outs.filter((d) => !BAD_OUT.test(d.label));
  const target = good.find((d) => /speaker/i.test(d.label)) || good[0];
  const row = { check: "playback", os_default: def ? def.label : null, outputs: outs.map((d) => d.label) };
  if (!target) return { ...row, pass: false, fixed: false, code: "no_speaker_output", how: "no non-headset output" };
  const was = sc.out;
  sc.out = target;
  try {
    const moved = await pinOutput(target);
    const how = moved ? `moved ${moved} agent audio to ${target.label}` : (was && was.deviceId !== target.deviceId
      ? `output now ${target.label}` : `on ${target.label}`);
    return { ...row, pass: true, fixed: moved > 0, how, output: target.label };
  } catch (e) {
    return { ...row, pass: false, fixed: false, code: "sink_failed", how: String(e.message || e), output: target.label };
  }
}

async function checkMic(devs) {
  const ins = realDevices(devs, "audioinput");
  const dji = ins.find((d) => DJI.test(d.label));
  const track = state.mic && state.mic.track && state.mic.track.mediaStreamTrack;
  const cur = track ? (track.getSettings().deviceId || "") : "";
  const curLabel = (ins.find((d) => d.deviceId === cur) || {}).label || (track ? track.label : null);
  const row = { check: "mic", inputs: ins.map((d) => d.label) };
  let how = `on ${curLabel}`, fixed = false;
  if (!dji) {   // still tap the mic Ears uses, so "heard" is measured on it
    try { await rawMic(cur || undefined); } catch (e) { /* no mic at all */ }
    return { ...row, pass: false, fixed: false, code: "no_dji", how: `no DJI input; ears on ${curLabel}`, mic: curLabel };
  }
  if (state.room && track && cur !== dji.deviceId) {
    try {
      await state.room.switchActiveDevice("audioinput", dji.deviceId);
      how = `switched ears from ${curLabel} to ${dji.label}`; fixed = true;
    } catch (e) {
      return { ...row, pass: false, fixed: false, code: "dji_switch_failed", how: String(e.message || e), mic: curLabel };
    }
  }
  const m = await rawMic(dji.deviceId);
  let peak = -200;
  for (let i = 0; i < 30; i++) { peak = Math.max(peak, rms(m.analyser)); await sleep(50); }
  if (peak <= MIC_FLOOR_DB) return { ...row, pass: false, fixed, code: "mic_silent", how, mic: dji.label, level_db: peak };
  return { ...row, pass: true, fixed, how, mic: dji.label, level_db: peak };
}

// heard: an analyser on each agent audio track; a voice onset (after >= 0.6 s quiet) with a check pending starts a
// 1 s window on the raw mic.
function watchVoice(track) {
  try {
    sc.ac = sc.ac || new AudioContext();
    const an = sc.ac.createAnalyser();
    an.fftSize = 2048;
    sc.ac.createMediaStreamSource(new MediaStream([track.mediaStreamTrack])).connect(an);
    sc.voices.push(an);
  } catch (e) { tlog(`selfcheck: voice tap failed: ${e.message || e}`); }
}

(function heardLoop() {
  let quietSince = performance.now(), base = [];
  setInterval(async () => {
    if (!sc.mic || !sc.voices.length) return;
    const m = rms(sc.mic.analyser);
    const v = Math.max(...sc.voices.map(rms));
    if (v < -50) { base.push(m); if (base.length > 10) base.shift(); return void (quietSince = quietSince || performance.now()); }
    const onset = quietSince && performance.now() - quietSince >= 600;
    quietSince = 0;
    if (!onset || !sc.pending || sc.pending.window) return;
    const floor = base.length ? [...base].sort((a, b) => a - b)[base.length >> 1] : m;
    const p = sc.pending;
    p.window = true;
    const t0 = performance.now();
    while (performance.now() - t0 < HEARD_MS) {
      const now = rms(sc.mic.analyser);
      if (now - floor >= HEARD_DB) {
        sc.pending = null;
        return post(p.trigger, [{ check: "heard", pass: true, how: p.retry ? "heard after re-pinning the output" : "heard",
          fixed: !!p.retry, ms: Math.round(performance.now() - t0), rise_db: Math.round((now - floor) * 10) / 10,
          output: sc.out && sc.out.label }]);
      }
      await sleep(20);
    }
    if (!p.retry) {   // not heard: re-pin the output and judge the next line
      const devs = await navigator.mediaDevices.enumerateDevices();
      const pb = await checkPlayback(devs);
      sc.pending = { trigger: p.trigger, retry: true };
      return post(p.trigger, [pb]);
    }
    sc.pending = null;
    post(p.trigger, [{ check: "heard", pass: false, fixed: false, code: "not_heard", how: `no ${HEARD_DB} dB rise in ${HEARD_MS} ms`,
      output: sc.out && sc.out.label }]);
  }, 50);
})();

function post(trigger, rows) {
  rows.forEach((r) => tlog(`selfcheck ${trigger} ${r.check} ${r.pass ? "pass" : "FAIL"} ${r.how || ""}`));
  if (serverBase) fetch(serverBase + "/talk/selfcheck", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ trigger, rows }) }).catch(() => {});
}

export async function selfCheck(trigger = "manual") {
  if (sc.running) { sc.again = trigger; return null; }
  sc.running = true;
  const t = performance.now();
  try {
    const devs = await navigator.mediaDevices.enumerateDevices();
    const rows = [await checkPlayback(devs), await checkMic(devs)];
    rows.forEach((r) => { r.ms_since_trigger = Math.round(performance.now() - t); });
    sc.pending = { trigger };   // the agent's next line answers "heard"
    post(trigger, rows);
    return rows;
  } catch (e) {
    tlog(`selfcheck ${trigger} failed: ${e.message || e}`);
    return null;
  } finally {
    sc.running = false;
    if (sc.again) { const a = sc.again; sc.again = null; selfCheck(a); }
  }
}

let devTimer = null;
if (navigator.mediaDevices && navigator.mediaDevices.addEventListener)
  navigator.mediaDevices.addEventListener("devicechange", () => {
    clearTimeout(devTimer);
    devTimer = setTimeout(() => { if (state.room) selfCheck("devicechange"); }, 800);
  });

window.vizierTalk = { toggle, start, state, loopback, selfCheck };
window.vizierTalkLog = tlog;   // the page script logs its feed link to the same file
// POST /talk/loopback/run on the server asks this page to run the check (no console in the release app): poll its counter.
// POST /talk/selfcheck/run asks for a self-check the same way.
function pollRun(path, fn) {
  let seen = null;
  setInterval(async () => {
    if (!serverBase) return;
    try {
      const n = (await (await fetch(serverBase + path)).json()).n;
      if (seen === null) { seen = n; return; }   // the first read is the baseline, never a run
      if (n !== seen) { seen = n; await fn(); }
    } catch (e) { /* server not reachable yet */ }
  }, 5000);
}
pollRun("/talk/loopback/run", () => loopback());
pollRun("/talk/selfcheck/run", () => selfCheck("server"));
window.addEventListener("keydown", (e) => {   // in-page fallback (a plain browser, or the app focused)
  if (!e.altKey || e.ctrlKey || e.metaKey) return;
  const k = e.code === "KeyE" ? "ears" : e.code === "KeyM" ? "mouth" : null;
  if (k) { e.preventDefault(); toggle(k, "key"); }
});
(async () => {
  let cfg = {};
  try { if (window.__TAURI__) cfg = (await window.__TAURI__.core.invoke("config")) || {}; } catch (e) { console.warn(e); }
  if (document.readyState === "loading") await new Promise((r) => document.addEventListener("DOMContentLoaded", r));
  start(cfg);
})();
