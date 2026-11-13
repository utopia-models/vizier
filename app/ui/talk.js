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
  // Barge-in (the agent stops talking when the user starts): the moment the local mic is an active speaker
  // the agent's audio is muted here, and it stays muted until 1.5 s after the user stops, so nothing queued talks over them.
  let hush = 0;
  room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
    const me = state.ears && speakers.some((p) => p === room.localParticipant);
    if (me) {
      clearTimeout(hush);
      state.audio.forEach((a) => { a.muted = true; });
    } else if (state.audio.some((a) => a.muted)) {
      clearTimeout(hush);
      hush = setTimeout(() => state.audio.forEach((a) => { a.muted = false; }), 1500);
    }
  });
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

window.vizierTalk = { toggle, start, state };
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
