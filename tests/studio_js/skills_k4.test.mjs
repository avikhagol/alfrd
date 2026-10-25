import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

globalThis.document = { hidden: false, addEventListener() {}, querySelector: () => ({}), head: { append() {} }, createElement: () => ({}) };
const { previewHtml, skillsHtml, selectionSize, mountSkills } = await import("../../src/alfrd/web/js/components/skills_settings.js");
const { mergeChat, chatBlock } = await import("../../src/alfrd/web/js/data/chat.js");
const { server } = await import("../../src/alfrd/web/js/data/server.js");
const flags = ["saved_skills", "native_skills", "native_plugins", "native_mcp"].map((key) => ({ key: `chat.${key}` }));
const agents = { claude: { adapter: "claude", native: { skills: false, plugins: false, mcp: false }, effective_args: ["claude", "--disable-slash-commands"], notes: ["Native switches apply"] }, codex: { adapter: "codex", native: {}, effective_args: ["codex"], notes: ["Codex: no effect"] } };
const catalogue = [{ id: "one", name: "One", bytes: 1024, source: "skills/one/SKILL.md", description: "Available guidance" }];
const state = () => ({ agents: { claude: { lean: true }, codex: {} }, skill_selection: { enabled: true, selected_ids: ["one"] } });

test("K4 preview escapes all untrusted markup and copy uses selected/available", () => {
  const attack = '</pre><script>alert(1)</script><img src=x onerror="alert(1)"><a href="javascript:x">x</a>';
  const html = previewHtml({ name: attack, content: attack });
  assert.doesNotMatch(html, /<script|<img|<a |onerror="|href="javascript:/);
  assert.match(html, /&lt;script&gt;/);
  const ui = skillsHtml(state(), catalogue, agents, flags);
  assert.match(ui, /1 selected · 1.0 KiB \/ 128 KiB/);
  assert.match(ui, /Lean is a preset/);
  assert.match(ui, /Codex: no effect/);
  assert.match(ui, /Effective args from saved settings/);
  assert.match(ui, /--disable-slash-commands/);
  assert.doesNotMatch(ui, /attached/i);
  for (const file of ["skills_settings", "chat_settings", "chat_setup"]) {
    const source = readFileSync(new URL(`../../src/alfrd/web/js/components/${file}.js`, import.meta.url), "utf8");
    assert.doesNotMatch(source, /attached/i);
  }
  assert.deepEqual(selectionSize(state(), catalogue), { count: 1, bytes: 1024 });
  const locked = skillsHtml(state(), catalogue, agents, flags.map((f) => ({ ...f, locked: true, reason: "Set by Superuser" })));
  assert.match(locked, /data-saved-skills checked disabled/);
  assert.match(locked, /Set by Superuser/);
});

test("K4 selection and independent native options survive ordinary chat serialization", () => {
  const raw = { skill_selection: { enabled: true, selected_ids: ["one"] }, agents: { claude: { lean: true, skills: true, plugins: false, mcp: "default" } } };
  const s = mergeChat(raw); s.agents.claude.model = "sonnet";
  const out = chatBlock(s);
  assert.deepEqual(out.skill_selection, raw.skill_selection);
  for (const key of ["lean", "skills", "plugins", "mcp"]) assert.equal(out.agents.claude[key], raw.agents.claude[key]);
});

test("K4 mounted chooser edits one enclosing draft and renders a safe fetched preview", async () => {
  const old = server.request, s = state(), events = {}, preview = { isConnected: true };
  const host = { textContent: "", innerHTML: "", addEventListener(name, fn) { events[name] = fn; }, querySelector() { return preview; } };
  server.request = async (path) => ({ ok: true, async json() {
    if (path.includes("/preview?")) return { name: "One", content: "<svg onload=alert(1)>" };
    if (path.includes("ui-flags")) return { flags };
    return { skills: catalogue, agents };
  } });
  try {
    const panel = mountSkills("p", host, s);
    await new Promise((resolve) => setImmediate(resolve));
    assert.match(host.innerHTML, /1 selected/);
    events.change({ target: { dataset: { skillId: "one" }, checked: false, hasAttribute: () => false } });
    assert.deepEqual(s.skill_selection.selected_ids, []);
    events.change({ target: { dataset: { nativeAgent: "claude", nativeKey: "skills" }, checked: true, hasAttribute: () => false } });
    assert.equal(s.agents.claude.skills, true);
    await events.click({ target: { closest: () => ({ dataset: { leanAgent: "claude" } }) } });
    assert.deepEqual(s.agents.claude, { lean: true, skills: false, plugins: false, mcp: "none" });
    await events.click({ target: { closest: () => ({ dataset: { previewSkill: "one" } }) } });
    assert.match(preview.innerHTML, /&lt;svg/);
    assert.doesNotMatch(preview.innerHTML, /<svg/);
    s.skill_selection.selected_ids = ["one"];
    catalogue[0].bytes = 131073;
    assert.match(panel.problem(), /128 KiB/);
    catalogue[0].bytes = 1024;
  } finally { server.request = old; }
});
