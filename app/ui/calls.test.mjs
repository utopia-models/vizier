// node --test app/ui/calls.test.mjs
// The Calls pane's pure render: newest card on top, ME / THEM tags, interim lines dim, the listening flag.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("./index.html", import.meta.url), "utf8");
const body = src.match(/\/\/ <calls-render>\n([\s\S]*?)\/\/ <\/calls-render>/)[1];
const callsRender = new Function(`${body}; return callsRender;`)();
const NOW = 1_800_000_000_000;
const st = {
  title: "Demo call",
  transcript: [
    { id: 1, t: NOW / 1000 - 5, speaker: "THEM", text: "what does it cost <b>", final: true },
    { id: 2, t: NOW / 1000 - 3, speaker: "ME", text: "about", final: false },
  ],
  cards: [
    { id: 3, t: NOW / 1000 - 40, asked: "first ask", say: "first say", next: "first next" },
    { id: 4, t: NOW / 1000 - 4, asked: "second ask", say: "second say", next: "second next" },
  ],
};

test("newest card is on top, with ASKED / SAY / NEXT", () => {
  const r = callsRender(st, NOW, 0);
  assert.ok(r.cards.indexOf("second ask") < r.cards.indexOf("first ask"));
  for (const k of ["ASKED", "SAY", "NEXT", "second say", "second next"]) assert.ok(r.cards.includes(k), k);
});

test("lines carry ME / THEM tags, in order, escaped; interim lines are dim", () => {
  const r = callsRender(st, NOW, 0);
  assert.ok(r.lines.indexOf("THEM</i>") < r.lines.indexOf("ME</i>"));
  assert.ok(r.lines.includes("&lt;b>") && !r.lines.includes("<b>"));
  const [finalLn, interimLn] = r.lines.split("</p>");
  assert.ok(finalLn.includes('class="ln them"') && !finalLn.includes("partial"));
  assert.ok(interimLn.includes('class="ln me partial"'));
});

test("listening is true only when something arrived in the last 15 s; title falls back", () => {
  assert.equal(callsRender(st, NOW, 0).listening, true);
  assert.equal(callsRender(st, NOW + 20_000, 0).listening, false);
  assert.equal(callsRender({ transcript: [], cards: [] }, NOW, 0).listening, false);
  assert.equal(callsRender(st, NOW, 0).title, "Demo call");
  assert.equal(callsRender(null, NOW, 0).title, "no call");
});

test("Clear hides everything older than the cut", () => {
  const r = callsRender(st, NOW, NOW / 1000 - 4.5);
  assert.ok(!r.cards.includes("first ask") && r.cards.includes("second ask") && !r.lines.includes("THEM</i>"));
});
