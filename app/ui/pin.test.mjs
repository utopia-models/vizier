// node --test app/ui/pin.test.mjs
// The speaker script keeps the current row at the TOP: a 22-row script, Down on every row, the active row must land at the margin.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("./index.html", import.meta.url), "utf8");
const pinDelta = new Function(`${src.match(/function pinDelta[^\n]*\}/)[0]}; return pinDelta;`)();
const MARGIN = Number(src.match(/const PIN_MARGIN = (\d+)/)[1]);

test("Down on all 22 rows puts the active row at the top, next row visible below", () => {
  const ROWS = 22, H = 60, PANEL = 600, PANEL_TOP = 100, SPACER = 1.0 * PANEL;
  const content = ROWS * H + SPACER, maxScroll = content - PANEL;
  let scroll = 0;
  for (let i = 0; i < ROWS; i++) {
    const rowTop = PANEL_TOP + i * H - scroll;            // what getBoundingClientRect().top reports
    scroll = Math.max(0, Math.min(maxScroll, scroll + pinDelta(rowTop, PANEL_TOP, MARGIN)));
    const placed = PANEL_TOP + i * H - scroll;
    assert.ok(Math.abs(placed - (PANEL_TOP + MARGIN)) <= 0.5 || (i === 0 && placed <= PANEL_TOP + MARGIN + H), `row ${i + 1} at ${placed}`);
    if (i < ROWS - 1) assert.ok(placed + H < PANEL_TOP + PANEL, `next row after ${i + 1} is in view`);
  }
});

test("the page calls tsPin where it used selIntoView for the speaker script, and keeps the spacer", () => {
  assert.ok(!/selIntoView\("p-tscript"\)/.test(src));
  assert.equal((src.match(/tsPin\(\)/g) || []).length >= 3, true);
  assert.ok(/#p-tscript::after/.test(src));
});
