// Rejoin backoff for the talk room (the deployment's issue 14858). On 2026-11-17 the room's server stalled and talk.js
// stacked joins: every failed connect scheduled a retry in 5 s AND its cleanup disconnect fired the Disconnected handler,
// which scheduled another in 3 s, so the joins doubled each round: 2855 "join failed" lines in 73 s (about 100 a second)
// and the WebView2 renderer crashed. The rules, kept here so they can be unit-tested with fake timers (backoff.test.mjs):
//   one join in flight (run() returns the pending promise to a second caller), one retry timer at a time (schedule() is a
//   no-op while one is armed), the delay 5 s doubling to 60 s with up to 25% jitter added (never under the base, never over
//   the cap), reset to 5 s by success().
export const BASE_MS = 5000;
export const CAP_MS = 60000;

export function nextDelay(attempt, { base = BASE_MS, cap = CAP_MS, rand = Math.random } = {}) {
  const n = Math.max(0, Math.floor(Number(attempt) || 0));
  const d = Math.min(cap, base * 2 ** Math.min(n, 30));
  return Math.min(cap, d + Math.floor(rand() * 0.25 * d));
}

export class Rejoin {
  constructor({ base = BASE_MS, cap = CAP_MS, rand = Math.random, setTimeout: st, clearTimeout: ct } = {}) {
    this.opts = { base, cap, rand };
    this.st = st || ((f, ms) => globalThis.setTimeout(f, ms));
    this.ct = ct || ((t) => globalThis.clearTimeout(t));
    this.attempt = 0;
    this.timer = null;
    this.inflight = null;
  }
  get pending() { return !!(this.timer || this.inflight); }
  // Run one join now. A join already in flight is returned instead (no second join); an armed retry timer is dropped
  // (this run supersedes it). fn's own failure handling decides what comes next (schedule()).
  run(fn) {
    if (this.inflight) return this.inflight;
    this.cancel();
    this.inflight = Promise.resolve().then(fn).finally(() => { this.inflight = null; });
    return this.inflight;
  }
  // Arm ONE retry: the next backoff delay in ms, or 0 when a retry is already armed (nothing was added).
  schedule(fn) {
    if (this.timer) return 0;
    const ms = nextDelay(this.attempt++, this.opts);
    this.timer = this.st(() => { this.timer = null; this.run(fn); }, ms);
    return ms;
  }
  success() { this.attempt = 0; }
  cancel() { if (this.timer) { this.ct(this.timer); this.timer = null; } }
}
