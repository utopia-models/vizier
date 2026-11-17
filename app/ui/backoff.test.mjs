// node --test app/ui/backoff.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { nextDelay, Rejoin, BASE_MS, CAP_MS } from "./backoff.js";

function clock() {   // fake timers: fire() runs every armed callback once, in order
  const q = []; let id = 0;
  return {
    setTimeout: (f, ms) => { q.push({ id: ++id, f, ms }); return id; },
    clearTimeout: (t) => { const i = q.findIndex((x) => x.id === t); if (i >= 0) q.splice(i, 1); },
    armed: () => q.map((x) => x.ms),
    fire: () => { const run = q.splice(0); run.forEach((x) => x.f()); },
  };
}
const tick = () => new Promise((r) => setImmediate(r));

test("the delay doubles from 5 s to the 60 s cap and never goes under the base", () => {
  const seq = [0, 1, 2, 3, 4, 5, 9].map((a) => nextDelay(a, { rand: () => 0 }));
  assert.deepEqual(seq, [5000, 10000, 20000, 40000, 60000, 60000, 60000]);
  for (let a = 0; a < 8; a++) {
    const lo = nextDelay(a, { rand: () => 0 }), hi = nextDelay(a, { rand: () => 0.999 });
    assert.ok(lo >= BASE_MS && hi <= CAP_MS, `attempt ${a}: ${lo}..${hi}`);
    assert.ok(hi >= lo && hi <= Math.min(CAP_MS, lo * 1.25), `jitter adds at most 25% (attempt ${a}: ${lo}..${hi})`);
  }
  assert.equal(nextDelay(-3, { rand: () => 0 }), 5000);
  assert.equal(nextDelay("x", { rand: () => 0 }), 5000);
});

test("one join in flight: a second start() gets the same promise, no second join", async () => {
  const c = clock(); const r = new Rejoin({ ...c });
  let joins = 0; let release;
  const join = () => new Promise((res) => { joins++; release = res; });
  const p1 = r.run(join), p2 = r.run(join), p3 = r.run(join);
  await tick();
  assert.equal(joins, 1);
  assert.equal(p1, p2); assert.equal(p2, p3);
  assert.ok(r.pending);
  release(); await p1; await tick();
  assert.ok(!r.pending);
  r.run(join); await tick();
  assert.equal(joins, 2, "after the first settles a new start joins again");
});

test("one retry timer: a disconnect and a failure in the same round arm one retry, not two", async () => {
  const c = clock(); const r = new Rejoin({ ...c, rand: () => 0 });
  let joins = 0;
  const join = () => { joins++; };
  assert.equal(r.schedule(join), 5000, "the first retry honours the 5 s base");
  assert.equal(r.schedule(join), 0, "a second schedule while one is armed adds nothing");
  assert.equal(r.schedule(join), 0);
  assert.deepEqual(c.armed(), [5000]);
  c.fire(); await tick();
  assert.equal(joins, 1, "the armed retry ran exactly one join");
  assert.equal(r.schedule(join), 10000, "the next failure waits 10 s");
  c.fire(); await tick();
  assert.equal(r.schedule(join), 20000);
  c.fire(); await tick(); r.schedule(join); c.fire(); await tick(); r.schedule(join); c.fire(); await tick();
  assert.equal(r.schedule(join), 60000, "capped at 60 s");
  c.fire(); await tick();
  assert.equal(joins, 6);
});

test("success resets the backoff; run() drops an armed retry so a direct start never doubles it", async () => {
  const c = clock(); const r = new Rejoin({ ...c, rand: () => 0 });
  let joins = 0; const join = () => { joins++; };
  r.schedule(join); r.schedule(join); c.fire(); await tick();
  r.schedule(join);
  assert.equal(r.attempt, 2);
  r.success();
  assert.equal(r.attempt, 0);
  assert.deepEqual(c.armed(), [10000], "the armed timer is untouched by success()");
  r.run(join); await tick();
  assert.deepEqual(c.armed(), [], "a direct run cancels the armed retry");
  assert.equal(joins, 2);
  assert.equal(r.schedule(join), 5000, "back at the 5 s base");
});

test("a failed join that schedules from inside itself arms exactly one retry", async () => {
  const c = clock(); const r = new Rejoin({ ...c, rand: () => 0 });
  let joins = 0;
  const join = async () => { joins++; r.schedule(join); r.schedule(join); };   // failure path + a stray Disconnected both call schedule
  await r.run(join); await tick();
  assert.equal(joins, 1); assert.deepEqual(c.armed(), [5000]);
  c.fire(); await tick();
  assert.equal(joins, 2); assert.deepEqual(c.armed(), [10000]);
  c.fire(); await tick();
  assert.equal(joins, 3); assert.deepEqual(c.armed(), [20000]);
});
