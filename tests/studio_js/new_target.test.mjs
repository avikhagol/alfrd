import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";


const read = (rel) => readFileSync(new URL(`../../src/alfrd/web/js/${rel}`, import.meta.url), "utf8");
const strip = (s) => s.replace(/^import .*;\n/gm, "").replace(/export /g, "");
const { targetNameError } = runInNewContext(`${strip(read("components/new_target.js"))}; ({ targetNameError })`, {});

test("new target names: required, plan-CSV safe, no path separators, unique", () => {
  assert.equal(targetNameError("NGC 1052", ["3C84"]), "");
  assert.match(targetNameError("  ", []), /Enter a name/);
  assert.match(targetNameError("#x", []), /#/);
  assert.match(targetNameError("a@b", []), /@/);
  assert.match(targetNameError("a/b", []), /\//);
  assert.match(targetNameError(" 3C84 ", ["3C84"]), /already exists/);
  assert.equal(targetNameError("3c84", ["3C84"]), ""); // the server compares exactly, so does the form
});

test("new task names follow agent_loop.validate_target (they become folders)", () => {
  assert.equal(targetNameError("fix-login_2", [], true), "");
  assert.match(targetNameError(".hidden", [], true), /no leading dot/);
  assert.match(targetNameError("two words", [], true), /letters, digits/);
  assert.match(targetNameError("x".repeat(65), [], true), /1–64/);
  assert.equal(targetNameError("NGC 1052", [], false), ""); // plain targets may contain spaces
});

test("selection token: a switch aborts the old request and marks its reply stale", () => {
  const src = read("app.js");
  const block = src.slice(src.indexOf("let selectionAc"), src.indexOf("ctx.selection = ")) + src.match(/ctx\.selection = .*/)[0];
  const ctx = {};
  const sel = runInNewContext(`${block}; ({ bump: bumpSelection, claim: ctx.selection })`, { ctx, AbortController });
  const first = sel.claim();
  assert.equal(first.aborted, false);
  sel.bump();
  assert.equal(first.aborted, true);
  assert.equal(sel.claim().aborted, false);
});

test("app wiring: the Target picker offers New target… only where it can be created", () => {
  const app = read("app.js");
  assert.match(app, /action: \(\) => \(canCreateTarget\(\) \? \{ label: "New target…", run: newTarget \} : null\)/);
  assert.match(app, /server\.session\?\.mutations_enabled && state\.trees\?\.\[activeProject\(\)\]\?\.provider === "server"/);
  assert.match(app, /import\("\.\/components\/new_target\.js"\)/); // lazy, not in the startup payload
  assert.match(app, /if \(next !== state\.selectedTarget\) bumpSelection\(\);/);
  const picker = read("components/picker.js");
  assert.match(picker, /trigger\.disabled = !items\.length && !action\(\);/); // an empty list still opens to create one
});
