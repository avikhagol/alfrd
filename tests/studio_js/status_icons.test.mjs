import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { STATUS_ICONS, STATUS_ICON_STATES, statusIcon } from "../../src/alfrd/web/js/components/status_icons.js";
import { icon } from "../../src/alfrd/web/js/utils/dom.js";

const STATES = ["done", "running", "failed", "blocked", "queued", "cancelled"];
const css = readFileSync(new URL("../../src/alfrd/web/css/chat.css", import.meta.url), "utf8");

test("all six states have an icon and a text label", () => {
  assert.deepEqual([...STATUS_ICON_STATES].sort(), [...STATES].sort());
  for (const state of STATES) {
    const meta = STATUS_ICONS[state];
    assert.ok(meta.label && meta.label.trim(), `${state} needs a label`);
    // An icon name that falls back to the info glyph would render the wrong picture.
    if (meta.icon !== "info") assert.notEqual(icon(meta.icon), icon("info"), `${state} icon "${meta.icon}" is unknown`);
    const html = statusIcon(state);
    assert.match(html, new RegExp(`data-status="${state}"`));
    assert.ok(html.includes(icon(meta.icon)), `${state} renders its icon`);
    assert.ok(html.includes(`<span class="status-label">${meta.label}</span>`), `${state} renders its label as text`);
  }
});

test("each state is distinguishable by text and by glyph, not colour alone", () => {
  const labels = STATES.map((s) => STATUS_ICONS[s].label);
  const glyphs = STATES.map((s) => STATUS_ICONS[s].icon);
  assert.equal(new Set(labels).size, STATES.length);
  assert.equal(new Set(glyphs).size, STATES.length);
});

test("unknown states fall back to a neutral glyph and the raw name", () => {
  const html = statusIcon("weird<x>");
  assert.match(html, /data-status="unknown"/);
  assert.ok(html.includes(icon("info")));
  assert.ok(html.includes("weird&lt;x&gt;"));
  assert.ok(!html.includes("weird<x>"));
});

test("chat.css defines a colour token for every state", () => {
  for (const state of STATES) {
    assert.match(css, new RegExp(`--status-${state}:`), `--status-${state} token missing`);
    assert.match(css, new RegExp(`\\.status-icon\\[data-status="${state}"\\] \\.ic`), `${state} colour rule missing`);
  }
});
