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
import { Rejoin } from "./backoff.js";

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
const pending = { ears: false, mouth: false };
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
  el.innerHTML = '<span data-k="ears" title="Alt+E">Ears <b>●</b></span><span data-k="mouth" title="Alt+M">Mouth <b>●</b></span><span data-onair title="the mic is capturing only while this is red">MIC OFF</span>';
  el.querySelectorAll("[data-k]").forEach((s) => s.addEventListener("click", () => toggle(s.dataset.k, "click")));
  document.body.appendChild(el);
  return el;
}

// Is a mic track actually live in the room right now: published, its capture running (readyState "live", not ended by
// an unplug), enabled and not muted. The Ears dot is green ONLY on this (issue 14720: the dot showed green all night
// while the room saw mic_published=false, because it only ever looked at the switch and the room).
export function micLive() {
  const pub = state.mic;
  const t = pub && pub.track;
  const mst = t && t.mediaStreamTrack;
  return !!(state.room && t && mst && mst.readyState === "live" && mst.enabled && !t.isMuted && !pub.isMuted);
}

function paint() {
  const el = pill();
  for (const k of ["ears", "mouth"]) {
    const dot = el.querySelector(`[data-k="${k}"] b`);
    // Ears: green = a live mic track in the room; grey = switched off; red = no room, or on with no live track.
    const on = k === "ears" ? micLive() : !!state.room;
    // pending: a press whose work (opening the mic, publishing) is still running shows amber at once, never the old colour.
    dot.style.color = !state.room ? "#c0392b" : !state[k] ? "#5b6472" : pending[k] ? "#f0b429" : on ? "#3ddc84" : "#c0392b";
  }
  const live = micLive();
  // On-air indicator (knowledge#14536): a red ON AIR chip exactly while a mic track is live and capturing, a dim MIC OFF
  // otherwise. It reads micLive() (the real track), never the switch, so it cannot show off over an open mic.
  const air = el.querySelector("[data-onair]");
  air.textContent = live ? "● ON AIR" : "MIC OFF";
  air.style.cssText = live ? "color:#fff;background:#c0392b;border-radius:999px;padding:0 8px;font-weight:700"
    : "color:#5b6472;font-weight:600";
  el.dataset.onAir = live ? "yes" : "no";
  el.title = state.room ? `${state.room.name} · ${state.status}${state.ears && !live ? " · NO MIC TRACK" : ""}`
    : `not connected · ${state.status}`;
  el.dataset.ears = state.ears ? "on" : "off";
  el.dataset.mouth = state.mouth ? "on" : "off";
  el.dataset.connected = state.room ? "yes" : "no";
  el.dataset.micLive = live ? "yes" : "no";
}

async function publishSwitches() {
  if (!state.room) return;
  try {
    await state.room.localParticipant.setAttributes({ ears: state.ears ? "on" : "off", mouth: state.mouth ? "on" : "off" });
  } catch (e) { console.warn("talk: attributes not set", e); }
}

// The input Ears should use: the DJI receiver's own USB input, never its Bluetooth "Hands-Free" profile (picking that
// one drops the headset to the narrowband call profile); else any real input that is not a Hands-Free one; else the
// OS default. Labels are empty until mic permission is granted, which grant_media gives this page.
async function pickMic() {
  const ins = realDevices(await navigator.mediaDevices.enumerateDevices(), "audioinput");
  const ok = ins.filter((d) => !/hands[- ]?free/i.test(d.label));
  return ok.find((d) => DJI.test(d.label)) || ok[0] || ins[0] || null;
}

// (Re)open the Ears mic from scratch: drop whatever track there is (ended, muted, or on a device that is gone) and
// publish a new one on pickMic()'s device (LiveKit asks for it exact and, if that fails, retries it as ideal, so a gone
// device never fails the open). LiveKit's own unmute restarts a track
// with its old constraints, which on ETH 2026-11-17 05:58Z and 06:01Z failed as OverconstrainedError (the device
// id it held had gone with a DJI replug) and left Ears with no track at all.
let reopening = null;
export function ensureMic(why, force = false) {
  if (reopening) return reopening;
  if (!state.room || !state.ears || (micLive() && !force)) return Promise.resolve(false);
  reopening = (async () => {
    const lp = state.room.localParticipant;
    const old = state.mic || lp.getTrackPublication(Track.Source.Microphone);
    const dev = await pickMic().catch(() => null);
    if (old && old.track) {
      try { await lp.unpublishTrack(old.track, true); } catch (e) { tlog(`ears: unpublish failed: ${e.message || e}`); }
    }
    state.mic = null;
    await lp.setMicrophoneEnabled(true,
      { ...(dev ? { deviceId: dev.deviceId } : {}),   // a string: LiveKit asks exact, then retries as ideal
        echoCancellation: true, noiseSuppression: true, autoGainControl: true, voiceIsolation: true, channelCount: 1 },
      { dtx: false, red: false, audioPreset: AudioPresets.speech });
    state.mic = lp.getTrackPublication(Track.Source.Microphone) || null;
    watchEnded();
    state.status = micLive() ? "connected" : "mic not live";
    tlog(`ears: mic ${micLive() ? "live" : "NOT live"} on ${dev ? dev.label : "the default input"} (${why})`);
    return true;
  })().catch((e) => {
    state.status = `ears: ${e.name || ""} ${e.message || e}`.trim();
    tlog(`ears: reopen (${why}) failed: ${e.name || ""} ${e.message || e}`);
    return false;
  }).finally(() => { reopening = null; paint(); });
  return reopening;
}

// An unplug ends the capture track: re-open at once rather than wait for the watchdog.
function watchEnded() {
  const mst = state.mic && state.mic.track && state.mic.track.mediaStreamTrack;
  if (!mst || mst.__vizierWatched) return;
  mst.__vizierWatched = true;
  mst.addEventListener("ended", () => { tlog("ears: mic track ended (device gone?)"); paint(); ensureMic("track ended"); });
}

// The watchdog: once a second, Ears on with no live track -> re-open (a replug is back live within ~1-2 s, under the
// 5 s the acceptance allows); the dot is repainted on every tick so it can never sit green over a dead track.
// Every 5th tick, a live track on the wrong input (LiveKit restarts an ended track on the OS default, the laptop's own
// array, measured on ETH 2026-11-17 06:56Z) is moved back to pickMic()'s device, so a replugged DJI is used again.
// A published track can be live (readyState "live", unmuted) yet carry exact zeros: scout measured 25 min of them on
// 2026-11-17 and only an app restart cured it. Tap the published track; SILENT_MS of nothing but 0.0 samples with Ears
// on -> drop the track and getUserMedia again. A real mic, even in a silent room, never reads exactly 0.
const SILENT_MS = 60000;
const sil = { track: null, an: null, ac: null, since: 0 };
function watchSilence() {
  const mst = state.mic && state.mic.track && state.mic.track.mediaStreamTrack;
  if (!state.ears || !mst || !micLive()) { sil.track = null; return; }
  if (sil.track !== mst) {
    try {
      sil.ac = sil.ac || new AudioContext();
      sil.an = sil.ac.createAnalyser();
      sil.ac.createMediaStreamSource(new MediaStream([mst])).connect(sil.an);
      sil.track = mst; sil.since = performance.now();
    } catch (e) { sil.track = null; return; }
  }
  const buf = new Float32Array(sil.an.fftSize);
  sil.an.getFloatTimeDomainData(buf);
  if (buf.some((v) => v !== 0)) { sil.since = performance.now(); return; }
  if (performance.now() - sil.since < SILENT_MS) return;
  sil.since = performance.now();
  tlog(`ears: published track silent (all zeros) for ${SILENT_MS / 1000} s; re-acquiring the mic`);
  ensureMic("silent track", true);
}

let tick = 0, moving = false;
setInterval(async () => {
  if (!state.room) return;
  if (state.ears && !micLive()) ensureMic("watchdog");
  else watchSilence();
  paint();
  if (++tick % 5 || moving || !state.ears || !micLive()) return;
  moving = true;
  try {
    const want = await pickMic();
    const cur = state.mic.track.mediaStreamTrack.getSettings().deviceId || "";
    const curLabel = state.mic.track.mediaStreamTrack.label || "";
    if (want && cur !== want.deviceId && curLabel !== want.label) {
      await state.room.switchActiveDevice("audioinput", want.deviceId);
      tlog(`ears: moved the mic from ${curLabel} to ${want.label}`);
    }
  } catch (e) { tlog(`ears: move to the preferred mic failed: ${e.message || e}`); }
  finally { moving = false; paint(); }
}, 1000);

async function applyEars() {
  if (!state.room) return;
  if (state.ears && !micLive()) {
    await ensureMic("ears on");
    return;
  }
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
  watchEnded();
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
  pending[k] = true;
  paint();
  try {
    if (k === "ears") await applyEars(); else applyMouth();
    await publishSwitches();
  } catch (e) {
    state.status = `${k}: ${e.message || e}`;
    tlog(`${k} failed: ${e.message || e}`);
    console.error("talk", e);
  }
  pending[k] = false;
  paint();
}

async function creds(cfg) {
  const q = new URLSearchParams(location.search);
  return { url: q.get("talkUrl") || cfg.talkUrl, token: q.get("talkToken") || cfg.talkToken };
}

// ONE join in flight, ONE retry timer, 5 s doubling to 60 s with jitter, reset by a successful join (backoff.js; the
// deployment's issue 14858: on 2026-11-17 a stalled server had this file stacking joins, about 100 a second within 73 s,
// and the renderer crashed). start() from anywhere (launch, a retry, a caller) goes through the same gate.
const rejoin = new Rejoin();
export function start(cfg = {}) { return rejoin.run(() => join(cfg)); }
function retry(cfg, what) {
  const ms = rejoin.schedule(() => join(cfg));
  tlog(ms ? `${what}; rejoining in ${Math.round(ms / 1000)} s` : `${what}; a rejoin is already scheduled`);
}

async function join(cfg) {
  if (state.room) { tlog("start: already in the room"); return; }
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
    // Only the room this page is IN rejoins here. A failed connect's cleanup disconnect (below) and a stale room's late
    // event both land here too, and used to schedule a second join beside the failure path's own retry: the doubling.
    if (state.room !== room) return;
    state.room = null; state.mic = null; state.status = "reconnecting"; paint();
    retry(cfg, `disconnected (${reason})`);
  });
  try {
    // A connect that never settles would leave no retry at all: 20 s, then it counts as a failure and is retried.
    await Promise.race([room.connect(url, token, { autoSubscribe: false }),
      new Promise((_, no) => setTimeout(() => no(new Error("no answer in 20 s")), 20000))]);
    state.room = room; state.status = "connected"; rejoin.success();
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
    try { if (room.removeAllListeners) room.removeAllListeners(); room.disconnect(); } catch (x) { /* never connected */ }
    retry(cfg, `join failed: ${e.message || e}`);
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
  const dji = ins.find((d) => DJI.test(d.label) && !/hands[- ]?free/i.test(d.label)) || ins.find((d) => DJI.test(d.label));
  const track = state.mic && state.mic.track && state.mic.track.mediaStreamTrack;
  const cur = track ? (track.getSettings().deviceId || "") : "";
  const curLabel = (ins.find((d) => d.deviceId === cur) || {}).label || (track ? track.label : null);
  const row = { check: "mic", inputs: ins.map((d) => d.label) };
  let how = `on ${curLabel}`, fixed = false;
  if (state.ears && !micLive()) {   // no live track is a failure, never "pass on null"; the watchdog re-opens it
    const ok = await ensureMic("selfcheck");
    if (!micLive()) return { ...row, pass: false, fixed: false, code: "no_track", how: "Ears on but no live mic track", mic: null };
    fixed = ok; how = "re-opened the mic track";
  }
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
    devTimer = setTimeout(async () => {
      if (!state.room) return;
      await ensureMic("devicechange");
      selfCheck("devicechange");
    }, 800);
  });

window.vizierTalk = { toggle, start, state, loopback, selfCheck, micLive, ensureMic, paint };
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
