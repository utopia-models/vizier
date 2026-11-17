// follow.js: the prompter follows the reader's voice, word by word, with no paid service.
//
//   hears    Vosk (Kaldi, Apache-2.0) running in the window as WebAssembly, on the reader's mic: partial words while they
//            are still speaking, ~0.3 s behind the voice. Nothing leaves the machine.
//   paces    a cadence clock: the reader's words per second, re-measured on every confirmed word and remembered between
//            takes. It advances only while the mic hears speech (energy VAD), so a pause holds the highlight.
//   corrects the heard words are fuzzy-aligned to the script near the highlight. A match only CORRECTS the clock: it
//            pushes the highlight forward to (matched word + 1 + words spoken during the recognizer's lag), and pulls it
//            back only when the clock is more than PULLBACK words ahead (the reader, 2026-11-17: "it just needs to be
//            further ahead and assume cadence"). Two heard words that match further on re-lock a skipped sentence.
//   override the arrow keys and the wheel move the highlight; following continues from where they put it.
//
// The pure parts (tokenize, Tracker, Vad) have no DOM and are replayed in tests against a recorded take
// (server/tests/test_voice_follow.py). VoiceFollow wires them to the mic and the recognizer in the app.

const ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
  "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"];
const TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"];
function numWords(n) {   // 0..9999 as spoken words
  if (n < 20) return [ONES[n]];
  if (n < 100) return [TENS[Math.floor(n / 10)], ...(n % 10 ? [ONES[n % 10]] : [])];
  if (n < 1000) return [ONES[Math.floor(n / 100)], "hundred", ...(n % 100 ? numWords(n % 100) : [])];
  return [...numWords(Math.floor(n / 1000)), "thousand", ...(n % 1000 ? numWords(n % 1000) : [])];
}
// One written token -> the words a recognizer would emit for it ("7.2" -> seven point two, "faith-aligned" -> faith aligned).
export function spokenForms(tok) {
  const s = String(tok).toLowerCase().replace(/[’‘`]/g, "'").replace(/%/g, " percent ").replace(/&/g, " and ");
  const out = [];
  for (const part of s.split(/[\s\-–—/_]+/)) {
    const p = part.replace(/^[^a-z0-9]+|[^a-z0-9]+$/g, "");
    if (!p) continue;
    const num = p.match(/^(\d{1,4})(?:\.(\d+))?$/);
    if (num) {
      out.push(...numWords(+num[1]));
      if (num[2]) out.push("point", ...num[2].split("").map(d => ONES[+d]));
      continue;
    }
    const w = p.replace(/[^a-z0-9]/g, "");
    if (w) out.push(w);
  }
  return out;
}
// lines: the spoken lines of the script (one string each). Returns the recognizer-form words, each pointing back at the
// line and the written word (index in that line's whitespace split) it came from.
// `start` marks the first word of each sentence (a line's first word, or after . ? ! : ;): the clock stops there.
export function tokenize(lines) {
  const words = [];
  lines.forEach((line, li) => {
    let start = true;
    String(line).split(/\s+/).filter(Boolean).forEach((tok, wi) => {
      spokenForms(tok).forEach((w, k) => words.push({ w, line: li, word: wi, start: start && k === 0 }));
      if (spokenForms(tok).length) start = /[.?!:;]["')\]]*$/.test(tok);
    });
  });
  return words;
}
// The recognizer's words, normalized like the script's. "[unk]" (a word the model has no entry for) stays as "~", which
// matches nothing but keeps its place, so an ad-lib reads as a gap and not as the next script word.
export function heardWords(text) { return String(text || "").toLowerCase().split(/\s+/).map(w => w === "[unk]" ? "~" : w.replace(/[^a-z0-9]/g, "")).filter(Boolean); }

function lev(a, b) {
  if (a === b) return 0;
  const m = a.length, n = b.length; if (!m || !n) return m || n;
  let prev = Array.from({ length: n + 1 }, (_, j) => j);
  for (let i = 1; i <= m; i++) {
    const cur = [i];
    for (let j = 1; j <= n; j++) cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    prev = cur;
  }
  return prev[n];
}
export function sim(a, b) { return a === b ? 1 : 1 - lev(a, b) / Math.max(a.length, b.length); }

// Tuning, each value measured on a real 146 s read (2026-11-17) by server/tests/test_voice_follow.py's
// replay; the numbers are in the PR that set them.
export const TUNE = {
  LAG: 0.40,        // s: the recognizer's lag from a word's end to its first partial, until finals have measured it
  LEAD: 1.5,        // words: the highlight sits this many words past the last word heard (the reader asked for "+ one word"; 1.5
                    // measured better on two real takes: fewer stretches behind the reader, none during a pause)
  PULLBACK: 3,      // words: the clock may run ahead of the heard words by this much before a match pulls it back
  MAXRUN: 6,        // words: past the last confirmed word the clock stops (an ad-lib, a word the recognizer missed)
  WINDOW: 60,       // words: how far ahead of the highlight a match is looked for (a skipped sentence or two)
  BACK: 12,         // words: how far behind (a re-read)
  WPS: 3.5,         // words per second before a final has measured it (a real reader: 3.2-3.7 inside phrases); the app
                    // seeds it from the last take instead (localStorage vizier.wps)
  WPS_MIN: 1.2, WPS_MAX: 5,
  K: 8,             // heard words aligned per update
  CROSS: 1,         // the clock may enter the next sentence once the heard words reach this close to its end
  RESUME: 0.25,     // s of silence with the highlight held at a sentence start, after which speech means that sentence began
  RESTART: 0.25,    // s of continuous speech after a pause before the clock runs again
  TAILW: 2,         // alignment score lost per newest heard word left unmatched: they are where the reader is NOW (0.3 kept the
                    // highlight in the sentence before a skip for 6 words; 2 re-locks on the first 2 heard words)
  TAIL: 0,          // unmatched heard words after the last match counted as script words (0: "with help" for "withheld"
                    // moved the highlight two words into the next sentence during a pause)
};

// The tracker. Time is the caller's clock in seconds; nothing here reads a clock.
export class Tracker {
  constructor(words, opt = {}) {
    this.words = words; this.T = { ...TUNE, ...opt };
    this.locked = false;     // the clock runs only once heard words have matched the script
    this.pos = 0;            // fractional index into words: floor(pos) is the highlighted word
    this.anchor = -1;        // the last script word a heard word confirmed
    this.anchorSpeech = 0;   // speech-seconds at the confirmation
    this.wps = this.T.WPS; this.speech = 0; this.speaking = false; this.last = null;
    this.speechAt = [];      // [t, speech-seconds] ring, to know how much was spoken since a past moment
    this.final = [];         // heard words of finished utterances (the tail is kept)
    this.sig = "";
    this.lag = this.T.LAG;   // s from a word's end to its first partial: re-measured from every final (lagFrom)
    this.lags = []; this.first = []; this.off = Infinity; this.quiet = 0; this.released = -1; this.run = 0; this.paused = false;
  }
  // The recognizer's word times are in seconds of audio fed to it; sync(t, fed) maps them onto this clock.
  sync(t, fedSeconds) { this.off = Math.min(this.off, t - fedSeconds); }
  index() { return Math.max(0, Math.min(this.words.length - 1, Math.floor(this.pos + 1e-9))); }
  // The cadence clock. speaking comes from the VAD for the interval [last, t].
  tick(t, speaking) {
    if (this.last !== null && t > this.last) {
      const dt = Math.min(0.25, t - this.last);
      // after a pause the clock waits for RESTART s of continuous speech: a breath or a chair creak 1.7 s into a pause
      // moved the highlight a word on a real take (25.9-28.9 s, the read already over)
      this.run = this.speaking ? this.run + dt : 0;
      if (this.paused && this.run >= this.T.RESTART) this.paused = false;
      if (this.speaking && this.locked && !this.paused) {   // no clock before the first heard words locked on (talk before the read)
        this.speech += dt;
        // past the last confirmed word the clock runs on for the words the recognizer has not caught up with, plus MAXRUN
        // and never past the first word of the next sentence until the heard words reach this one's last two: a pause
        // falls at a sentence end, and a clock running into the next line during a breath is the highlight jumping
        // ahead during the pause (measured on the ai-data take, 102.2-104.6 s).
        const cap = Math.min(this.nextStart(), (this.anchor >= 0 ? this.anchor + this.T.LEAD + this.wps * this.lag : 0) + this.T.MAXRUN);
        if (this.pos < cap) this.pos = Math.min(cap, this.pos + this.wps * dt);
      }
    }
    // A pause with the highlight held on a sentence's first word: when speech starts again it is that sentence, even if
    // the recognizer never heard the end of the last one ("blessings that god has given me" heard as "blushes or guys
    // get"), so the hold lifts and the clock runs from there.
    if (this.last !== null) this.quiet = speaking ? 0 : this.quiet + Math.max(0, t - this.last);
    if (this.quiet >= this.T.RESUME) this.paused = true;
    if (this.locked && this.quiet >= this.T.RESUME) { const b = this.nextStart(); if (this.pos >= b - 1e-6) this.released = Math.max(this.released, b); }
    this.speaking = !!speaking; this.last = t;
    this.speechAt.push([t, this.speech]); while (this.speechAt.length > 400) this.speechAt.shift();
    return this.index();
  }
  nextStart() {   // the first word of the sentence after the one the last heard word is in (crossable from its last CROSS words)
    const W = this.words; let i = Math.max(0, this.anchor + 1);
    while (i < W.length && !W[i].start) i++;
    if (i - this.anchor <= this.T.CROSS) { i++; while (i < W.length && !W[i].start) i++; }
    while (i < W.length && i <= this.released) { i++; while (i < W.length && !W[i].start) i++; }
    return Math.min(W.length - 1, i);
  }
  spokenSince(t0) {   // speech-seconds between t0 and now
    let s = this.speech;
    for (let i = this.speechAt.length - 1; i >= 0; i--) { if (this.speechAt[i][0] <= t0) { s = this.speechAt[i][1]; break; } if (i === 0) s = this.speechAt[0][1]; }
    return Math.max(0, this.speech - s);
  }
  // A key or the wheel put the reader on word i (or the first word of line `line`).
  // lock=false (a new script): the clock waits for the first heard words; a key press means the reader is there now.
  jump(i, lock = true) { this.locked = lock; this.released = -1; this.pos = Math.max(0, Math.min(this.words.length - 1, i)); this.anchor = Math.floor(this.pos) - 1; this.anchorSpeech = this.speech; this.final = []; this.sig = ""; }
  jumpLine(line, lock = true) { const i = this.words.findIndex(w => w.line >= line); if (i >= 0) this.jump(i, lock); }
  // The recognizer: `text` is the current utterance so far (partial) or finished (final). Returns true when it moved.
  // `result` (finals only): [{word, start, end}] in fed seconds, from which the recognizer's current lag is measured.
  hear(t, text, isFinal, result) {
    const cur = heardWords(text);
    for (let k = 0; k < cur.length; k++) if (this.first[k] === undefined) this.first[k] = t;
    if (isFinal) { this.lagFrom(result); this.first = []; }
    const heard = this.final.concat(cur).slice(-this.T.K);
    if (isFinal) this.final = this.final.concat(cur).slice(-this.T.K);
    const sig = heard.join(" ");
    if (!heard.length || sig === this.sig) return false;
    this.sig = sig;
    const m = this.align(heard);
    if (!m) return false;
    const before = this.pos;
    // m.end = the script word matched to the newest matched heard word; m.tail heard words came after it, unmatched.
    const end = m.end + Math.min(this.T.TAIL, m.tail);
    // Back is the expensive direction: a partial the recognizer is still revising ("the blessings that god has given"
    // -> "the blessings i got as good", 17.4-17.7 s on a take) re-matched 10 words back and yanked the highlight there.
    // Only the newest heard words, two in a row and three in all, may pull it back.
    if (end + this.T.LEAD < this.pos - this.T.PULLBACK && !(m.tail <= 1 && m.run >= 2 && m.c >= 3)) return false;
    this.confirm(end);
    const lead = this.wps * Math.min(this.lag, this.spokenSince(t - this.lag));
    const target = Math.min(this.words.length - 1, end + this.T.LEAD + lead);
    if (target > this.pos || this.pos - target > this.T.PULLBACK || m.jump) this.pos = target;
    return Math.floor(before) !== Math.floor(this.pos);
  }
  // Cadence and lag both come from a final's word times. Cadence = words per second of speech inside the utterance (gaps
  // over 0.35 s between words are pauses, not slowness): the reader's real rate, independent of how well the words matched
  // the script. The alignment-based rate it replaces read 1.5 w/s on a 3.3 w/s read, because ad-libs and the
  // recognizer's lag counted as slowness, and the highlight fell behind.
  lagFrom(result) {
    if (!Array.isArray(result)) return;
    let n = 0, dur = 0;
    for (let k = 1; k < result.length; k++) { const d = result[k].start - result[k - 1].start; if (d > 0 && result[k].start - result[k - 1].end < 0.35) { n++; dur += d; } }
    if (n >= 3 && dur > 0.5) this.wps = Math.max(this.T.WPS_MIN, Math.min(this.T.WPS_MAX, 0.6 * this.wps + 0.4 * (n / dur)));
    if (!isFinite(this.off)) return;
    result.forEach((w, k) => { if (this.first[k] !== undefined && w && isFinite(w.end)) this.lags.push(this.first[k] - (w.end + this.off)); });
    while (this.lags.length > 40) this.lags.shift();
    if (this.lags.length >= 5) { const x = this.lags.slice().sort((a, b) => a - b); this.lag = Math.max(0.1, Math.min(3, x[Math.floor(x.length / 2)])); }
  }
  confirm(end) {
    if (end > this.anchor || end < this.anchor - this.T.PULLBACK) {
      this.anchor = end; this.anchorSpeech = this.speech;
      this.locked = true;
    }
  }
  // Local alignment (Smith-Waterman) of the heard words against the script around the highlight.
  align(heard) {
    const W = this.words, here = Math.max(0, this.anchor);
    const lo = Math.max(0, here - this.T.BACK), hi = Math.min(W.length - 1, Math.floor(this.pos) + this.T.WINDOW);
    const n = heard.length, m = hi - lo + 1;
    let best = null;
    const H = Array.from({ length: n + 1 }, () => new Float32Array(m + 1));
    const C = Array.from({ length: n + 1 }, () => new Int16Array(m + 1));     // matched words on the best path
    const E = Array.from({ length: n + 1 }, () => new Int16Array(m + 1));     // exact matches on the best path
    const R = Array.from({ length: n + 1 }, () => new Int16Array(m + 1));     // current run of consecutive matches
    for (let i = 1; i <= n; i++) for (let j = 1; j <= m; j++) {
      const a = heard[i - 1], b = W[lo + j - 1].w, s = sim(a, b);
      const ms = s >= 0.8 ? 2 * s : s >= 0.6 && Math.min(a.length, b.length) >= 4 ? 0.8 : -1;
      const d = H[i - 1][j - 1] + ms, u = H[i - 1][j] - 0.7, l = H[i][j - 1] - 0.7;
      let v = 0, c = 0, e = 0, r = 0;
      if (d >= u && d >= l && d > 0) { v = d; c = C[i - 1][j - 1] + (ms > 0); e = E[i - 1][j - 1] + (s === 1 && b.length >= 2); r = ms > 0 ? R[i - 1][j - 1] + 1 : 0; }
      else if (u >= l && u > 0) { v = u; c = C[i - 1][j]; e = E[i - 1][j]; }
      else if (l > 0) { v = l; c = C[i][j - 1]; e = E[i][j - 1]; }
      H[i][j] = v; C[i][j] = c; E[i][j] = e; R[i][j] = r;
      if (ms <= 0 || v <= 0) continue;   // a candidate ends ON a matched word
      const end = lo + j - 1, tail = n - i, dist = end - here;
      if (tail > 3) continue;            // the newest heard words matched nothing: an ad-lib, not a position
      // near the highlight one solid word is enough; a jump (skip ahead / re-read) needs a run of consecutive words
      const far = !this.locked || dist > Math.max(6, this.pos - here + 4) || dist < -this.T.PULLBACK;
      // one exact word of 4+ letters is enough when it lies between the last confirmed word and just past the clock
      const one = c === 1 && e === 1 && b.length >= 4 && dist > 0 && dist <= Math.max(4, this.pos - here + 3);
      if (far ? (dist < 0 ? r < 3 : r < 2 || e < 2) : (c < 2 && !one)) continue;
      const score = v - 0.04 * Math.abs(dist - (this.pos - here)) - this.T.TAILW * tail;
      if (!best || score > best.score) best = { score, end, tail, jump: far, run: r, c };
    }
    return best;
  }
}

// Energy voice-activity detector over 20 ms frames: speech = more than `margin` dB over the noise floor, the floor being
// the 10th percentile of the last 8 s of frames (the dips between words reach it even mid-sentence). A running minimum
// failed on a real take: one digital-zero frame pinned it at -112 dB and every breath after counted as speech.
export class Vad {
  constructor(rate, opt = {}) {
    this.n = Math.round(rate * 0.02); this.acc = 0; this.k = 0; this.hist = []; this.floor = -60; this.on = false; this.quiet = 0; this.loud = 0;
    this.hang = opt.hang ?? 0.15; this.margin = opt.margin ?? 12; this.onset = opt.onset ?? 5;   // frames (20 ms each) of sound before it is speech
  }
  push(samples) {   // Float32Array; returns the speaking flag after these samples
    for (let k = 0; k < samples.length; k++) {
      this.acc += samples[k] * samples[k];
      if (++this.k < this.n) continue;
      const db = 10 * Math.log10(this.acc / this.n + 1e-12); this.acc = 0; this.k = 0;
      if (db > -90) { this.hist.push(db); if (this.hist.length > 400) this.hist.shift(); }
      if (this.hist.length >= 25 && (this.hist.length % 5 === 0)) { const x = this.hist.slice().sort((a, b) => a - b); this.floor = x[Math.floor(x.length * 0.1)]; }
      if (db > this.floor + this.margin) { this.quiet = 0; if (++this.loud >= this.onset) this.on = true; }   // a click or a breath is not speech
      else { this.loud = 0; if (this.on && (this.quiet += 0.02) >= this.hang) this.on = false; }
    }
    return this.on;
  }
}

// The app side: mic -> (VAD, Vosk in a worker) -> Tracker -> onMove(word). Vosk is window.Vosk (vosk.js, loaded by
// index.html); the model is models/vosk-model-small-en-us-0.15.tar.gz beside index.html (app/build.sh fetches it).
export class VoiceFollow {
  constructor({ onMove, onState, model = "models/vosk-model-small-en-us-0.15.tar.gz", deviceId } = {}) {
    this.onMove = onMove || (() => {}); this.onState = onState || (() => {}); this.modelUrl = model; this.deviceId = deviceId;
    this.tracker = null; this.on = false; this.raf = 0; this.lastIdx = -1; this.lagT = [];
  }
  setScript(lines, keepLine = 0) {
    const w = tokenize(lines);
    const wps = this.tracker ? this.tracker.wps : (+localStorage.getItem("vizier.wps") || TUNE.WPS);
    this.tracker = new Tracker(w, { WPS: wps }); this.tracker.jumpLine(keepLine, false); this.lastIdx = -1;
  }
  overrideLine(line) { if (this.tracker) { this.tracker.jumpLine(line); this.lastIdx = this.tracker.index(); } }
  async start(stream) {
    if (this.on) return; this.on = true;
    try {
      if (!this.model) {
        // fetched here and handed over as a blob: URL, because the recognizer's worker cannot fetch the app's own
        // tauri:// asset URLs itself
        this.onState("loading");
        const r = await fetch(this.modelUrl); if (!r.ok) throw new Error(`model ${this.modelUrl}: HTTP ${r.status}`);
        this.model = await window.Vosk.createModel(URL.createObjectURL(await r.blob()));
      }
      this.ctx = new AudioContext({ sampleRate: 16000 });
      this.stream = stream || await navigator.mediaDevices.getUserMedia({ audio: {
        ...(this.deviceId ? { deviceId: { exact: this.deviceId } } : {}), echoCancellation: false, noiseSuppression: true, autoGainControl: true } });
      const rec = new this.model.KaldiRecognizer(16000); this.rec = rec;
      rec.setWords(true);   // finals carry word times: the cadence and the lag are measured from them
      const vad = new Vad(16000); let fed = 0;
      const now = () => this.ctx.currentTime;
      rec.on("partialresult", m => this.hear(now(), m.result.partial, false));
      rec.on("result", m => this.hear(now(), m.result.text, true, m.result.result));
      this.src = this.ctx.createMediaStreamSource(this.stream);
      this.sp = this.ctx.createScriptProcessor(1024, 1, 1);   // 64 ms at 16 kHz
      this.sp.onaudioprocess = e => {
        if (!this.on) return;
        const speaking = vad.push(e.inputBuffer.getChannelData(0));
        try { rec.acceptWaveform(e.inputBuffer); } catch (_) { /* a recognizer being torn down */ }
        fed += e.inputBuffer.length;
        if (this.tracker) { this.tracker.sync(now(), fed / 16000); this.tracker.tick(now(), speaking); }
      };
      this.src.connect(this.sp); this.sp.connect(this.ctx.destination);
      const loop = () => { if (!this.on) return; this.paint(); this.raf = requestAnimationFrame(loop); };
      this.raf = requestAnimationFrame(loop);
      this.onState("on");
    } catch (e) { this.on = false; this.onState("error", e && e.message || String(e)); }
  }
  hear(t, text, fin, result) { if (this.tracker && this.on) { this.tracker.hear(t, text, fin, result); this.paint(); } }
  paint() {
    if (!this.tracker) return;
    if (this.ctx && this.on) this.tracker.tick(this.ctx.currentTime, this.tracker.speaking);   // the clock between audio callbacks
    const i = this.tracker.index();
    if (i !== this.lastIdx) { this.lastIdx = i; this.onMove(this.tracker.words[i], i); }
  }
  stop() {
    this.on = false; cancelAnimationFrame(this.raf);
    if (this.tracker) localStorage.setItem("vizier.wps", this.tracker.wps.toFixed(2));
    try { this.sp && this.sp.disconnect(); this.src && this.src.disconnect(); } catch (_) {}
    if (this.stream && !this.sharedStream) this.stream.getTracks().forEach(t => t.stop());
    try { this.rec && this.rec.remove(); } catch (_) {}
    try { this.ctx && this.ctx.close(); } catch (_) {}
    this.ctx = this.rec = this.sp = this.src = this.stream = null;
    this.onState("off");
  }
}
