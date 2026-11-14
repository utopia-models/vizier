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
import { Room, RoomEvent, Track } from "https://cdn.jsdelivr.net/npm/livekit-client@2.15.7/+esm";

const KEEP = "vizier.talk";   // Ears/Mouth survive an app restart or deploy (the WebView profile keeps localStorage)
const kept = (() => { try { return JSON.parse(localStorage.getItem(KEEP) || "{}"); } catch (e) { return {}; } })();
const state = { ears: !!kept.ears, mouth: !!kept.mouth, room: null, mic: null, audio: [], status: "off" };
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
    await state.room.localParticipant.setMicrophoneEnabled(true, { echoCancellation: true, noiseSuppression: true, autoGainControl: true });
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
    console.error("talk", e);
  }
  paint();
}

async function creds(cfg) {
  const q = new URLSearchParams(location.search);
  return { url: q.get("talkUrl") || cfg.talkUrl, token: q.get("talkToken") || cfg.talkToken };
}

export async function start(cfg = {}) {
  serverBase = (cfg.server || "").replace(/\/$/, "");
  const { url, token } = await creds(cfg);
  if (!url || !token) return;            // not configured: no pill, nothing joins (Vizier works as before)
  pill(); paint();
  const room = new Room({ adaptiveStream: false, dynacast: false });
  room.on(RoomEvent.TrackSubscribed, (track) => {
    if (track.kind !== Track.Kind.Audio) return;
    const a = track.attach();
    a.style.display = "none";
    document.body.appendChild(a);
    state.audio.push(a);
  });
  // No local barge-in mute: the agent keeps talking over any sound in the room, and only its own name, heard by the
  // agent process in the room, stops it. A local mute on "mic is an active speaker" muted the agent whenever the
  // laptop mic heard anything, including the agent's own playback, so the user heard nothing.
  room.on(RoomEvent.TrackPublished, (pub) => { if (pub.kind === Track.Kind.Audio) pub.setSubscribed(state.mouth); });
  room.on(RoomEvent.TrackUnsubscribed, (track) => {
    track.detach().forEach((a) => { a.remove(); state.audio = state.audio.filter((x) => x !== a); });
  });
  room.on(RoomEvent.Disconnected, () => {
    state.room = null; state.mic = null; state.status = "reconnecting"; paint();
    setTimeout(() => start(cfg), 3000);
  });
  try {
    await room.connect(url, token, { autoSubscribe: false });
    state.room = room; state.status = "connected";
    note("restore");   // what this start brings back: the state the user last set, from localStorage
    applyMouth();
    await applyEars();
    await publishSwitches();
  } catch (e) {
    state.status = `connect failed: ${e.message || e}`;
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

window.vizierTalk = { toggle, start, state, loopback };
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
