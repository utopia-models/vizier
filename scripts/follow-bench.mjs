#!/usr/bin/env node
// follow-bench.mjs: replay a recorded take through the voice-follow tracker (app/ui/follow.js) and grade it against when
// each script word was actually spoken. No browser: the recognizer's events were recorded once from the
// real Vosk-in-WebAssembly the app runs (scripts/follow-capture.py), the VAD runs on the take's own audio here.
//
//   node scripts/follow-bench.mjs --lines L.json --events E.json --wav take.wav --truth T.json [--json] [--tune k=v,...]
//
//   L.json   the spoken lines, in order (what the SCRIPT tab shows, every part)
//   E.json   [{t, k: "p"|"f", s}]: the recognizer's partial/final text, t = seconds from the start of the audio
//   take.wav 16 kHz mono 16-bit PCM, the same audio
//   T.json   [{word, start, end}]: when each word was spoken (an offline pass of a larger model over the whole file)
//
// Prints, over the script words the truth places: the highlight lag (time the highlight reached the word minus the
// time the reader started saying it; negative = the highlight got there first), median and p95, and every stretch where the
// highlight sat more than 1 word behind the voice for more than 300 ms.
import { readFileSync } from "node:fs";
import { Tracker, Vad, tokenize, heardWords, sim, TUNE } from "../app/ui/follow.js";

const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : d; };
const lines = JSON.parse(readFileSync(arg("--lines"), "utf8"));
const events = arg("--events") ? JSON.parse(readFileSync(arg("--events"), "utf8")) : [];
const truth = JSON.parse(readFileSync(arg("--truth"), "utf8"));
const tune = Object.fromEntries((arg("--tune", "") || "").split(",").filter(Boolean).map(kv => { const [k, v] = kv.split("="); return [k, +v]; }));

function wav(path) {
  const b = readFileSync(path); let o = 12, data = null, rate = 16000;
  while (o < b.length) { const id = b.toString("ascii", o, o + 4), n = b.readUInt32LE(o + 4); if (id === "fmt ") rate = b.readUInt32LE(o + 12); if (id === "data") { data = b.subarray(o + 8, o + 8 + n); break; } o += 8 + n; }
  const s = new Float32Array(data.length / 2); for (let i = 0; i < s.length; i++) s[i] = data.readInt16LE(i * 2) / 32768;
  return { rate, s };
}
const { rate, s } = wav(arg("--wav"));
const words = tokenize(lines);

// When the truth says each SCRIPT word was spoken: a global alignment of the truth's words to the script's.
function place(words, truth) {
  const n = truth.length, m = words.length, D = Array.from({ length: n + 1 }, () => new Float32Array(m + 1)), B = Array.from({ length: n + 1 }, () => new Uint8Array(m + 1));
  for (let i = 1; i <= n; i++) { D[i][0] = D[i - 1][0]; B[i][0] = 1; }
  for (let j = 1; j <= m; j++) { D[0][j] = D[0][j - 1]; B[0][j] = 2; }
  for (let i = 1; i <= n; i++) for (let j = 1; j <= m; j++) {
    const x = sim(heardWords(truth[i - 1].word)[0] || "", words[j - 1].w), ms = x >= 0.75 ? 2 * x : -1;
    const d = D[i - 1][j - 1] + ms, u = D[i - 1][j] - 0.3, l = D[i][j - 1] - 0.3;
    if (d >= u && d >= l) { D[i][j] = d; B[i][j] = 0; } else if (u >= l) { D[i][j] = u; B[i][j] = 1; } else { D[i][j] = l; B[i][j] = 2; }
  }
  const at = new Array(m).fill(null); let i = n, j = m;
  while (i > 0 && j > 0) {
    if (B[i][j] === 0) { if (sim(heardWords(truth[i - 1].word)[0] || "", words[j - 1].w) >= 0.75) at[j - 1] = truth[i - 1].start; i--; j--; }
    else if (B[i][j] === 1) i--; else j--;
  }
  // keep a placement only inside a run of >= 2 neighbouring placed words: a lone match is the aligner reaching for a
  // script word inside an ad-lib (the rant after the read placed "your", "the", "line" 15 s late)
  return at.map((t, k) => t !== null && ((at[k - 1] ?? null) !== null || (at[k + 1] ?? null) !== null) ? t : null);
}
const onset = place(words, truth);

let audio = s, ev = events, on = onset;

// --timeline F.json: grade a highlight timeline recorded elsewhere ([[t, word index], ...], e.g. the real app in a browser,
// scripts/follow-e2e.py) instead of replaying the events here
const given = arg("--timeline") ? JSON.parse(readFileSync(arg("--timeline"), "utf8")) : null;
const T = new Tracker(words, tune); const vad = new Vad(rate, { onset: tune.ONSET ?? 5, margin: tune.MARGIN ?? 12, hang: tune.HANG ?? 0.15 });
const BUF = 1024, timeline = [[0, 0]];
let e = 0; const rec = t => { const i = T.index(); if (i !== timeline[timeline.length - 1][1]) timeline.push([t, i]); };
if (given) timeline.splice(0, timeline.length, ...given);
else for (let k = 0; k + BUF <= audio.length; k += BUF) {
  const t = (k + BUF) / rate;
  while (e < ev.length && ev[e].t <= t) { const x = ev[e]; if (x.k === "a") T.sync(x.t, x.s); else { T.tick(x.t, T.speaking); T.hear(x.t, x.s, x.k === "f", x.w); rec(x.t); } e++; }
  const sp = vad.push(audio.subarray(k, k + BUF));
  for (let f = 1; f <= 4; f++) { T.tick(t - BUF / rate + f * BUF / rate / 4, sp); rec(t - BUF / rate + f * BUF / rate / 4); }   // ~60 fps between callbacks
}
const end = audio.length / rate;
if (arg("--trace")) {   // re-run printing the state every 100 ms in [A,B]
  const [A, B] = arg("--trace").split(":").map(Number), T2 = new Tracker(words, tune), v2 = new Vad(rate); let e2 = 0, nx = A;
  for (let k = 0; k + BUF <= audio.length; k += BUF) {
    const t = (k + BUF) / rate; if (t > B) break;
    while (e2 < ev.length && ev[e2].t <= t) { const x = ev[e2]; if (x.k === "a") T2.sync(x.t, x.s); else { T2.tick(x.t, T2.speaking); T2.hear(x.t, x.s, x.k === "f", x.w); } e2++; }
    const sp = v2.push(audio.subarray(k, k + BUF)); T2.tick(t, sp);
    if (t >= nx) { nx += 0.1; let sw = -1; on.forEach((x, i) => { if (x !== null && x <= t) sw = i; });
      console.log(t.toFixed(2), sp ? "S" : ".", "floor", v2.floor.toFixed(0), "wps", T2.wps.toFixed(2), "lag", T2.lag.toFixed(2), "anchor", T2.anchor, "pos", T2.pos.toFixed(1), words[T2.index()].w, "| said", sw, sw >= 0 ? words[sw].w : ""); }
  }
  process.exit(0);
}
// grade
const firstAt = w => { for (const [t, i] of timeline) if (i >= w) return t; return null; };
const lags = [];
let lastPlaced = -1; on.forEach((t, w) => { if (t !== null) lastPlaced = w; });
on.forEach((t, w) => { if (t === null) return; const h = firstAt(w); if (h !== null) lags.push({ w, word: words[w].w, at: t, lag: h - t }); });
const q = (a, f) => { const x = a.slice().sort((p, r) => p - r); return x.length ? x[Math.min(x.length - 1, Math.floor(f * x.length))] : null; };
const L = lags.map(x => x.lag);
// behind: >1 word behind the voice for >300 ms
const spokenAt = t => { let w = -1; on.forEach((x, i) => { if (x !== null && x <= t) w = i; }); return w; };
const hiAt = t => { let i = 0; for (const [tt, ii] of timeline) { if (tt > t) break; i = ii; } return i; };
const lastT = Math.max(...on.filter(x => x !== null));
const behind = []; let st = null;
for (let t = 0; t <= lastT + 0.5; t += 0.01) {
  const b = spokenAt(t) - hiAt(t) > 1;
  if (b && st === null) st = t; if (!b && st !== null) { if (t - st > 0.3) behind.push([+st.toFixed(2), +(t - st).toFixed(2)]); st = null; }
}
// holds: every stretch of >= 2 s with no speech in the truth, and whether the highlight moved in it
const holds = []; const placed = truth.slice().sort((a, b) => a.start - b.start);
for (let i = 1; i < placed.length; i++) {
  const a = placed[i - 1].end, b = placed[i].start; if (b - a < 2 || a > lastT) continue;
  const moves = timeline.filter(([t]) => t > a + 0.6 && t < b).length;   // 0.6 s: the recognizer finishing the last word
  holds.push({ from: +a.toFixed(2), to: +b.toFixed(2), moves });
}
const out = { words: words.length, placed: lags.length, median_ms: Math.round(1000 * q(L, 0.5)), p95_ms: Math.round(1000 * q(L, 0.95)),
  ahead_or_on: +(L.filter(x => x <= 0.05).length / L.length).toFixed(2), behind_over_300ms: behind, holds, wps: +T.wps.toFixed(2), lag_s: +T.lag.toFixed(2), tune: { ...TUNE, ...tune } };
// --skip T: a sentence was skipped at T s. Re-lock = how many words were said after T when the highlight first sat on
// (or ahead of) the word being said, and stayed within a word of the voice for the next 3.
if (arg("--skip")) {
  const T0 = +arg("--skip"), after = on.map((t, w) => [t, w]).filter(([t]) => t !== null && t >= T0).sort((a, b) => a[0] - b[0]);
  let relock = null;
  for (let k = 0; k < after.length - 3 && relock === null; k++) {
    const okAt = j => hiAt(after[j][0] + 0.001) >= after[j][1] - (j === k ? 0 : 1);
    if (okAt(k) && okAt(k + 1) && okAt(k + 2) && okAt(k + 3)) relock = k;
  }
  out.skip = { at: T0, first: after[0] && words[after[0][1]].w, relock_words: relock };
}
if (process.argv.includes("--json")) console.log(JSON.stringify(out));
else { console.log(JSON.stringify(out, null, 1)); if (process.argv.includes("--worst")) console.log(lags.sort((a, b) => b.lag - a.lag).slice(0, 15)); }
