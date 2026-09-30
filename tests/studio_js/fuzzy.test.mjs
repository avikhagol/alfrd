// Command palette scorer (data/fuzzy.js).
import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const { fuzzyScore, fuzzyRank } = await import(path.join(path.resolve(here, "../../src/alfrd/web/js"), "data/fuzzy.js"));

const items = [
  { label: "J0741+100", detail: "target · BV019" },
  { label: "rpicard", detail: "step · VLBI calibration" },
  { label: "casa.log_1", detail: "log · J0741+100 · fits_to_ms" },
  { label: "casa.log_2", detail: "log · J0742+103 · rpicard" },
  { label: "J0742+103", detail: "target · BV019, RDV41" },
  { label: "Pause plan", detail: "action" },
];
const labels = (q) => fuzzyRank(q, items).map((i) => `${i.label} ${i.detail}`);

test("fuzzy: every word must match; word starts win", () => {
  assert.equal(labels("j07 rpi")[0], "casa.log_2 log · J0742+103 · rpicard");
  assert.equal(labels("j0742")[0], "J0742+103 target · BV019, RDV41");
  assert.equal(labels("rpi")[0], "rpicard step · VLBI calibration");
  assert.deepEqual(labels("zzz"), []);
  assert.equal(fuzzyScore("pp", "Pause plan") > fuzzyScore("pp", "zappa"), true, "initials beat letters inside a word");
  assert.equal(fuzzyScore("", "anything"), 0);
  assert.equal(labels("paus")[0], "Pause plan action");
});

test("fuzzy: ties keep the input order and limit applies", () => {
  const same = [{ label: "a1" }, { label: "a2" }, { label: "a3" }];
  assert.deepEqual(fuzzyRank("a", same, 2).map((i) => i.label), ["a1", "a2"]);
  assert.deepEqual(fuzzyRank("a", [{ label: "x a" }, { label: "a", boost: 5 }]).map((i) => i.label), ["a", "x a"]);
});
