import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch { /* optional */ }
const repo = fileURLToPath(new URL("../../", import.meta.url)), web = resolve(repo, "src/alfrd/web");
const ids = ["artifact-handoff", "blocker-reporting", "failure-triage", "mailbox-protocol", "plan-json-author", "verification-checklist"];
const attack = '</pre><script>window.skillXss=true</script><img src=x onerror="window.skillXss=true">';
const inventory = ids.map((id) => ({ id, name: id, description: `Guidance for ${id}`, source_root: "skills", source: `skills/${id}/SKILL.md`, path: `/project/skills/${id}/SKILL.md`, bytes: 123, sha256: "a".repeat(64), reference_files: [`skills/${id}/references/guide.md`], providers: { claude: { delivery: "injected" }, codex: { delivery: "native", fallback: "injected" }, generic: { delivery: "injected" } } }));
inventory.push(...[".alfrd/skills", ".claude/skills"].map((root, i) => ({ ...inventory[0], id: `root-${i}`, name: `Root ${i}`, source_root: root, path: `/project/${root}/root-${i}/SKILL.md`, providers: { claude: { delivery: i ? "native" : "injected" } } })));

test("Skills Explorer is chat-only, absent from global navigation, and the six library manifests exist", async () => {
  const app = await readFile(resolve(web, "js/app.js"), "utf8");
  assert.doesNotMatch(app, /id: "skills"|Skills Explorer|skills_explorer/);
  const chat = await readFile(resolve(web, "js/components/chat_view.js"), "utf8");
  assert.match(chat, /import.*skills_explorer.js/);
  assert.match(chat, /data-chat="skills" aria-haspopup="dialog"/);
  assert.doesNotMatch(chat, /class="chat-skills"/);
  for (const id of ids) assert.match(await readFile(resolve(repo, `skills/${id}/SKILL.md`), "utf8"), /description:/);
});

test("Skills Explorer in Chromium", { skip: !chromium && "Playwright not available" }, async (t) => {
  const app = await readFile(resolve(web, "js/app.js"), "utf8");
  const shell = app.slice(app.indexOf("function modal(html"), app.indexOf("ctx.modal = modal;"));
  const requests = [];
  const activations = new Map();
  const service = createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost"); requests.push({ method: req.method, path: url.pathname });
    const send = (body, type = "text/html", code = 200) => { res.writeHead(code, { "Content-Type": type }); res.end(body); };
    if (url.pathname === "/") return send('<!doctype html><link rel="stylesheet" href="/css/studio.css"><body style="display:block;height:auto"><main id="host"></main><div id="modal-host" hidden></div></body>');
    if (url.pathname === "/shell.js") return send(`import { $ } from "/js/utils/dom.js"; let clearModalKeys; export ${shell}`, "text/javascript");
    if (url.pathname.endsWith("/activations")) {
      let state = activations.get(url.pathname) || { version: 1, activations: [], hash: "missing", legacy: true, roles: ["development", "verification", "custom role"], unavailable: [] };
      if (req.method === "PUT") {
        let raw = ""; for await (const chunk of req) raw += chunk;
        const body = JSON.parse(raw);
        if (body.base_hash !== state.hash) return send(JSON.stringify({ error: { message: "Skills activations changed; refresh before saving" } }), "application/json", 409);
        state = { ...state, activations: body.activations, hash: state.hash + "-saved", legacy: false };
        activations.set(url.pathname, state);
      }
      return send(JSON.stringify(state), "application/json");
    }
    if (url.pathname.endsWith("/inventory")) return send(JSON.stringify({ skills: inventory }), "application/json");
    if (url.pathname.endsWith("/preview")) return send(JSON.stringify({ name: url.searchParams.get("id"), content: attack, sha256: "a".repeat(64) }), "application/json");
    const path = resolve(web, url.pathname.slice(1));
    if (!path.startsWith(web + sep)) return send("missing", "text/plain", 404);
    try { send(await readFile(path), [".js", ".mjs"].includes(extname(path)) ? "text/javascript" : "text/css"); }
    catch { send("missing", "text/plain", 404); }
  });
  await new Promise((done) => service.listen(0, "127.0.0.1", done));
  t.after(() => new Promise((done) => { service.close(done); service.closeAllConnections(); }));
  const browser = await chromium.launch({ ...(process.env.ALFRD_CHROMIUM_EXECUTABLE ? { executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  async function open(width = 1280, mode = "server", project = "p / one", chat = false) {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    await page.goto(`http://127.0.0.1:${service.address().port}/`);
    await page.evaluate(async ({ mode, project, chat }) => {
      window.explorer = await import("/js/components/skills_explorer.js");
      window.ctx = { state: { mode, selectedProject: project } };
      if (chat) {
        const { server } = await import("/js/data/server.js");
        const { DEFAULTS } = await import("/js/data/chat.js");
        const request = server.request.bind(server);
        server.request = (url, opts) => url.includes("/skills/") ? request(url, opts) : Promise.resolve(new Response(JSON.stringify(url.endsWith("/settings") ? DEFAULTS : { sessions: [] })));
        window.ctx.modal = (await import("/shell.js")).modal;
        window.chat = await import("/js/components/chat_view.js");
        Object.assign(window.ctx, { activeProject: () => window.ctx.state.selectedProject, toast() {}, update() { window.chat.render(document.querySelector("#host"), window.ctx); } });
        window.ctx.update();
      } else window.explorer.render(document.querySelector("#host"), window.ctx);
    }, { mode, project, chat });
    t.after(() => page.close()); return page;
  }
  for (const width of [1280, 360]) await t.test(`chat Skills modal at ${width}px: menu, focus, close paths and project scope`, async () => {
    const page = await open(width, "server", "chat-project", true);
    const errors = []; page.on("pageerror", (e) => errors.push(e.message));
    const trigger = page.locator(".chat-session-menu summary");
    const skills = page.getByRole("button", { name: "Skills", exact: true });
    assert.equal(await skills.isVisible(), false);
    const show = async () => { await trigger.click(); await skills.click(); await page.locator("[data-skill]").first().waitFor(); };
    await show();
    const dialog = page.getByRole("dialog", { name: "Skills Explorer" });
    assert.equal(await dialog.getAttribute("aria-modal"), "true");
    assert.equal(await dialog.locator(":focus").count(), 1);
    await page.getByRole("searchbox", { name: "Search skills" }).fill("verification");
    const content = attack + "\nReadable skill instructions".repeat(100);
    await page.route("**/preview?**", (r) => r.fulfill({ json: { name: "verification-checklist", content, sha256: "a".repeat(64) } }));
    await page.locator('[data-skill="verification-checklist"]').click();
    await page.locator("[data-skill-content] pre").waitFor();
    assert.equal(await page.locator("[data-skill-content] pre").textContent(), content);
    assert.ok(await dialog.evaluate((el) => el.scrollHeight > el.clientHeight));
    await page.evaluate(() => { window.ctx.state.liveStatus = { state: "live" }; window.ctx.update(); });
    await page.keyboard.press("Escape");
    assert.equal(await dialog.count(), 0);
    assert.equal(await trigger.evaluate((el) => el === document.activeElement), true);
    await show(); await page.getByRole("button", { name: "Close Skills Explorer" }).click();
    assert.equal(await trigger.evaluate((el) => el === document.activeElement), true);
    await show(); await page.locator(".modal-back").click({ position: { x: 1, y: 1 } });
    assert.equal(await dialog.count(), 0);
    assert.equal(await trigger.evaluate((el) => el === document.activeElement), true);
    await show();
    await page.evaluate(() => { window.ctx.state.selectedProject = "second-chat"; window.ctx.update(); });
    assert.equal(await dialog.count(), 0);
    await show();
    assert.equal(await page.getByRole("searchbox", { name: "Search skills" }).inputValue(), "");
    assert.ok(requests.some((r) => r.path.includes("/chat-project/chat/skills/inventory")));
    assert.ok(requests.some((r) => r.path.includes("/second-chat/chat/skills/inventory")));
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert.deepEqual(errors, []);
    await page.evaluate(() => window.chat.leave());
    assert.equal(await dialog.count(), 0);
  });
  for (const theme of ["obsidian-orbit", "daylight-orbit"]) await t.test(`compact badges, full details and keyboard activation at 360px (${theme})`, async () => {
    const page = await open(360, "server", `badges-${theme}`, true);
    await page.addStyleTag({ url: `/css/themes/${theme}/theme.css` });
    const description = 'Full description <img src=x onerror="window.skillXss=true"> ' + "long guidance ".repeat(30);
    const name = "Long skill name ".repeat(12);
    const roles = ["development", "design", "verification", "custom role", "very-long-custom-role-".repeat(12)];
    await page.route("**/inventory", (r) => r.fulfill({ json: { skills: [
      { ...inventory[0], name, description },
      { ...inventory[0], origin: "global", source_root: "Global", name: "Global copy" },
    ] } }));
    await page.route("**/activations", (r) => r.fulfill({ json: {
      version: 1, hash: "fixture", legacy: true, roles, activations: [], unavailable: [],
    } }));
    await page.locator(".chat-session-menu summary").click();
    await page.getByRole("button", { name: "Skills", exact: true }).click();
    const card = page.locator('[data-origin="project"][data-skill="artifact-handoff"]');
    await card.waitFor();
    assert.deepEqual(await card.locator('.badge').allTextContents(), ["Project", "skills", "Inactive"]);
    assert.deepEqual(await page.locator('[data-origin="global"] .badge').allTextContents(), ["Global", "Inactive"]);
    assert.equal(await card.locator('.skills-explorer-description').getAttribute('title'), description);
    assert.equal(await card.locator('.skills-explorer-name').getAttribute('title'), name);
    assert.ok(await card.locator('.skills-explorer-description').evaluate((el) => {
      const style = getComputedStyle(el);
      return style.whiteSpace === 'nowrap' && style.textOverflow === 'ellipsis' && el.scrollWidth > el.clientWidth;
    }));
    assert.equal(await card.locator('.mono').count(), 0);
    assert.equal(await card.innerText().then((text) => text.includes('fallback:')), false);
    await card.focus(); await page.keyboard.press('Enter');
    await page.locator('[data-skill-content] pre').waitFor();
    assert.ok((await page.locator('[data-skills-detail]').textContent()).includes(description));
    assert.equal(await page.locator('[data-skills-detail] img').count(), 0);
    await page.getByText('About skills', { exact: true }).click();
    assert.equal(await page.getByText('Changes apply to new requests. Legacy selection is replaced by explicit scopes when saved; role-less tasks will receive none.', { exact: true }).isVisible(), true);
    const checks = page.locator('[data-activation-scope]');
    for (let i = 0; i < await checks.count(); i++) {
      await checks.nth(i).focus(); await page.keyboard.press('Space');
      assert.equal(await checks.nth(i).isChecked(), true);
      assert.equal(await checks.nth(i).evaluate((el) => el === document.activeElement), true);
    }
    assert.deepEqual(await card.locator('.badge').allTextContents(), ["Project", "skills", "Orchestrator", "Developer", "Designer", "Verifier", ...roles.slice(3)]);
    assert.equal(await page.locator('.badge:not([aria-label])').count(), 0);
    assert.ok(await page.getByRole('dialog').evaluate((el) => el.scrollWidth <= el.clientWidth));
    assert.ok(await card.evaluate((el) => [...el.querySelectorAll('.badge')].every((badge) => {
      const b = badge.getBoundingClientRect(), c = el.getBoundingClientRect();
      return b.left >= c.left && b.right <= c.right + 1;
    })));
    await card.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `/tmp/skills-badges-${theme}-360.png`, fullPage: true });
    for (let i = 0; i < await checks.count(); i++) await checks.nth(i).uncheck();
    assert.deepEqual(await card.locator('.badge').allTextContents(), ["Project", "skills", "Inactive"]);
    await page.keyboard.press('Escape');
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.evaluate(() => window.chat.leave());
  });
  await t.test("modal toggles persist by source and scope, cancel and stale saves", async () => {
    const page = await open(390, "server", "activation-project", true);
    const globals = [{ ...inventory[0], origin: "global", source_root: "Global", path: "/global/artifact-handoff/SKILL.md" }];
    await page.route("**/inventory", (r) => r.fulfill({ json: { skills: [...inventory, ...globals] } }));
    const show = async () => {
      await page.locator(".chat-session-menu summary").click();
      await page.getByRole("button", { name: "Skills", exact: true }).click();
      await page.locator('[data-origin="global"]').waitFor();
    };
    await show();
    await page.locator('[data-origin="global"]').click();
    await page.getByRole("checkbox", { name: "Orchestrator", exact: true }).check();
    await page.getByRole("checkbox", { name: "custom role", exact: true }).check();
    await page.getByRole("button", { name: "Save activations", exact: true }).click();
    await page.getByText("Activations saved. Applies to new requests.", { exact: true }).waitFor();
    await page.locator('[data-origin="project"][data-skill="artifact-handoff"]').click();
    assert.equal(await page.getByRole("checkbox", { name: "Orchestrator", exact: true }).isChecked(), false);
    await page.getByRole("checkbox", { name: "Orchestrator", exact: true }).check();
    await page.getByRole("checkbox", { name: "development", exact: true }).check();
    await page.getByRole("button", { name: "Save activations", exact: true }).click();
    await page.getByText("Activations saved. Applies to new requests.", { exact: true }).waitFor();
    await page.locator('[data-origin="global"]').click();
    assert.equal(await page.getByRole("checkbox", { name: "Orchestrator · Overridden", exact: true }).isChecked(), true);
    await page.getByRole("button", { name: "Close Skills Explorer", exact: true }).click();
    await page.reload();
    // Recreate the app after a full browser reload and read saved API state.
    await page.evaluate(async () => {
      const { server } = await import("/js/data/server.js"), { DEFAULTS } = await import("/js/data/chat.js");
      const request = server.request.bind(server);
      server.request = (url, opts) => url.includes("/skills/") ? request(url, opts) : Promise.resolve(new Response(JSON.stringify(url.endsWith("/settings") ? DEFAULTS : { sessions: [] })));
      window.chat = await import("/js/components/chat_view.js");
      window.ctx = { state: { mode: "server", selectedProject: "activation-project" }, activeProject: () => "activation-project", toast() {}, modal: (await import("/shell.js")).modal, update() { window.chat.render(document.querySelector("#host"), window.ctx); } };
      window.ctx.update();
    });
    await show();
    await page.locator('[data-origin="project"][data-skill="artifact-handoff"]').click();
    assert.equal(await page.getByRole("checkbox", { name: "development", exact: true }).isChecked(), true);
    await page.getByRole("checkbox", { name: "development", exact: true }).uncheck();
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await page.getByRole("checkbox", { name: "development", exact: true }).waitFor();
    assert.equal(await page.getByRole("checkbox", { name: "development", exact: true }).isChecked(), true);
    await page.route("**/activations", (r) => r.request().method() === "PUT" ? r.fulfill({ status: 409, json: { error: { message: "Skills activations changed; refresh before saving" } } }) : r.continue());
    await page.getByRole("checkbox", { name: "development", exact: true }).uncheck();
    await page.getByRole("button", { name: "Save activations", exact: true }).click();
    await page.getByText("Skills activations changed; refresh before saving", { exact: true }).waitFor();
    assert.equal(await page.getByRole("checkbox", { name: "development", exact: true }).isChecked(), false);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  });
  await t.test("groups, six skills, search, keyboard, safe preview, metadata and live rerender", async () => {
    const page = await open();
    await page.getByRole("status").filter({ hasText: "8 of 8 skills" }).waitFor();
    assert.deepEqual(await page.locator("[data-skills-list] h3").allTextContents(), [".alfrd/skills/", ".claude/skills/", "skills/"]);
    for (const id of ids) assert.equal(await page.locator(`[data-skill="${id}"]`).count(), 1);
    const search = page.getByRole("searchbox", { name: "Search skills" });
    await search.fill("verification"); assert.equal(await page.locator("[data-skill]").count(), 1);
    await page.keyboard.press("Tab"); await page.keyboard.press("Enter");
    await page.locator("[data-skill-content] pre").waitFor();
    assert.equal(await page.locator("[data-skill-content] pre").textContent(), attack);
    assert.equal(await page.locator("[data-skills-detail] script, [data-skills-detail] img").count(), 0);
    assert.equal(await page.evaluate(() => window.skillXss), undefined);
    const detail = await page.locator("[data-skills-detail]").innerText();
    for (const text of ["123 bytes", "a".repeat(64), "/project/skills/verification-checklist/SKILL.md", "references/guide.md", "codex: native (fallback: injected)", "claude: injected"]) assert.ok(detail.includes(text), text);
    await page.evaluate(() => window.explorer.render(document.querySelector("#host"), window.ctx));
    assert.equal(await search.inputValue(), "verification");
    await search.fill("does not exist"); await page.getByText("No skills match your search.").waitFor();
    await search.fill(""); await page.locator("[data-skill]").first().focus();
    await page.keyboard.press("End"); assert.equal(await page.locator("[data-skill]").last().evaluate((b) => b === document.activeElement), true);
    await page.keyboard.press("Home"); await page.keyboard.press("ArrowDown"); await page.keyboard.press("Space");
    assert.equal(await page.locator('[data-skill="root-1"]').getAttribute("aria-pressed"), "true");
  });
  await t.test("inventory loading, empty, error and retry; mobile layout", async () => {
    const page = await open(390);
    await page.locator("[data-skill]").first().waitFor();
    let pending;
    const requested = new Promise((done) => { pending = done; });
    await page.route("**/inventory", (route) => pending(route));
    await page.getByRole("button", { name: "Refresh skills" }).click();
    const route = await requested;
    await page.getByText("Loading installed skills…").waitFor();
    await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ error: { message: attack } }) });
    await page.getByRole("alert").waitFor(); assert.equal(await page.getByRole("alert").textContent(), attack);
    await page.unroute("**/inventory");
    await page.route("**/inventory", (r) => r.fulfill({ json: { skills: [] } }));
    await page.getByRole("button", { name: "Refresh skills" }).click();
    await page.getByText("No installed skills found", { exact: false }).waitFor();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.unroute("**/inventory"); await page.getByRole("button", { name: "Refresh skills" }).click();
    await page.locator("[data-skill]").first().click(); await page.locator("[data-skill-content] pre").waitFor();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  });
  await t.test("late previews cannot overwrite a newer selection or project; preview retry", async () => {
    const page = await open(); await page.locator("[data-skill]").first().waitFor();
    let pending; const requested = new Promise((done) => { pending = done; });
    await page.route("**/preview?**", (route) => new URL(route.request().url()).searchParams.get("id") === "artifact-handoff" ? pending(route) : route.continue());
    await page.locator('[data-skill="artifact-handoff"]').click(); const route = await requested;
    await page.locator('[data-skill="blocker-reporting"]').click();
    await page.locator("[data-skill-content] pre").waitFor();
    await route.fulfill({ json: { name: "STALE", content: "old preview" } });
    await page.unroute("**/preview?**");
    assert.equal(await page.locator("[data-skill-content] h4").textContent(), "blocker-reporting");
    await page.route("**/preview?**", (r) => r.fulfill({ status: 404, json: { error: { message: "Skill is unavailable" } } }));
    await page.locator('[data-skill="artifact-handoff"]').click(); await page.getByRole("alert").waitFor();
    await page.unroute("**/preview?**"); await page.getByRole("button", { name: "Retry preview" }).click();
    await page.locator("[data-skill-content] pre").waitFor();
    let next; const changing = new Promise((done) => { next = done; });
    await page.route("**/preview?**", (r) => next(r));
    await page.locator('[data-skill="failure-triage"]').click(); const old = await changing;
    await page.evaluate(() => { window.ctx.state.selectedProject = "other"; window.explorer.render(document.querySelector("#host"), window.ctx); });
    await page.getByRole("status").filter({ hasText: "8 of 8" }).waitFor();
    await old.fulfill({ json: { name: "STALE PROJECT", content: "old" } });
    assert.equal(await page.locator("[data-skill-content]").count(), 0);
  });
  await t.test("browser-only and no-project states", async () => {
    const page = await open(1280, "browser"); await page.getByText("Skills Explorer needs alfrd serve.").waitFor();
    assert.equal(await page.getByRole("searchbox").isDisabled(), true);
    const empty = await open(1280, "server", null); await empty.getByText("Choose a project to browse its installed skills.").waitFor();
  });
  for (const width of [1280, 360]) for (const theme of ["obsidian-orbit", "daylight-orbit"]) await t.test(`authoring and secure import preview at ${width}px (${theme})`, async () => {
    const page=await open(width,'server',`authoring-${width}-${theme}`,true);
    await page.addStyleTag({url:`/css/themes/${theme}/theme.css`});
    const doc='---\nname: review\ndescription: Review diffs\n---\n# Instructions\nRead the diff.\n';
    let saved=[],confirmed=0,content=doc,revision='revision-1';
    const skill={...inventory[0],id:'review',name:'review',origin:'project',editable:true,revision,content:doc};
    await page.route('**/inventory',r=>r.fulfill({json:{skills:[skill],project_path:'/project',global_path:'/global'}}));
    await page.route('**/preview?**',r=>r.fulfill({json:{...skill,content,revision}}));
    await page.route('**/validate',r=>{
      const value=r.request().postDataJSON().content;
      const start=value.indexOf('name: ')+6, end=start+(value.slice(start).split('\n')[0].split(' #')[0]).length;
      return value.startsWith('---') ? r.fulfill({json:{fields:{name:'review',description:'Review diffs'},scalar_spans:{name:[start,end]}}}) : r.fulfill({status:400,json:{error:{message:'Invalid YAML',line:1}}});
    });
    await page.route('**/create',r=>{saved.push(r.request().postDataJSON()); return r.fulfill({json:skill,status:201});});
    await page.route('**/content',r=>{
      const value=r.request().postDataJSON();saved.push(value);
      if(value.content.includes('conflict')) return r.fulfill({status:409,json:{error:{message:'Skill changed since you opened it.',current_content:doc+'upstream'}}});
      content=value.content;revision='revision-2';return r.fulfill({json:{...skill,content,revision}});
    });
    await page.route('**/imports/preview',r=>r.fulfill({json:{token:'preview-fixture',proposed_id:'imported',digest:'f'.repeat(64),total_bytes:123,content:doc+attack,files:[{path:'SKILL.md',bytes:123}],source:{source_url:'https://example.org/SKILL.md',resolved_url:'https://example.org/SKILL.md',fetched_at:'2026-10-30T00:00:00Z',commit:null}}}));
    await page.route('**/imports/confirm',r=>{confirmed++;return r.fulfill({json:skill,status:201});});
    await page.locator('.chat-session-menu summary').click();await page.getByRole('button',{name:'Skills',exact:true}).click();
    await page.locator('[data-skill="review"]').waitFor();
    await page.getByRole('button',{name:'New skill',exact:true}).click();
    const area=page.locator('[data-author-content]');await area.waitFor();
    await page.locator('[data-author-id]').fill('new-review');
    await area.fill(doc+'\ncreated');
    await page.getByText('Valid SKILL.md',{exact:true}).waitFor();
    await page.getByRole('button',{name:'Save skill',exact:true}).click();
    await page.locator('[data-skill-content] pre').waitFor();
    assert.equal(saved[0].id,'new-review'); assert.equal(saved[0].origin,'project');
    await page.locator('[data-card-edit="review"]').click();
    await area.fill(doc.replace('name: review','name: review # keep inline'));
    await page.getByText('Valid SKILL.md',{exact:true}).waitFor();
    await page.getByLabel('Name',{exact:true}).fill('Structured title');
    await page.waitForFunction(()=>document.querySelector('[data-author-content]').value.includes('name: \"Structured title\" # keep inline'));
    assert.ok((await area.inputValue()).includes('Read the diff.'));
    await page.screenshot({path:`/tmp/skills-editor-${theme}-${width}.png`,fullPage:true});
    await area.fill('bad');await page.getByText('Invalid YAML (line 1)',{exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:'Save skill',exact:true}).isDisabled(),true);
    await area.fill(doc+'conflict');await page.getByText('Valid SKILL.md',{exact:true}).waitFor();
    await page.getByRole('button',{name:'Save skill',exact:true}).click();
    await page.getByText('Skill changed since you opened it.',{exact:true}).waitFor();
    assert.equal(await area.inputValue(),doc+'conflict');
    assert.equal(await page.locator('[data-author-conflict] pre').textContent(),doc+'upstream');
    // Dirty close retains the document on Cancel and supports an explicit discard.
    page.once('dialog',dialog=>dialog.dismiss());await page.keyboard.press('Escape');
    assert.equal(await area.count(),1);
    await area.fill(doc+'saved');await page.getByText('Valid SKILL.md',{exact:true}).waitFor();
    await page.getByRole('button',{name:'Save skill',exact:true}).click();await page.locator('[data-skill-content] pre').waitFor();
    assert.equal(saved.at(-1).base_revision,'revision-1');
    await page.getByRole('button',{name:'Import from URL',exact:true}).click();
    await page.getByLabel('URL',{exact:true}).fill('https://example.org/SKILL.md');
    await page.getByRole('button',{name:'Fetch preview',exact:true}).click();
    await page.getByText('Review before saving',{exact:true}).waitFor();
    assert.equal(confirmed,0);
    await page.screenshot({path:`/tmp/skills-import-${theme}-${width}.png`,fullPage:true});
    assert.equal(await page.locator('[data-import-preview] pre').textContent(),doc+attack);
    assert.equal(await page.locator('[data-import-preview] img, [data-import-preview] script').count(),0);
    assert.ok((await page.locator('[data-author-target]').textContent()).includes('/project/.alfrd/skills/imported'));
    await page.locator('[data-author-origin]').selectOption('global');
    assert.ok((await page.locator('[data-author-target]').textContent()).includes('shared library'));
    assert.ok(await page.getByRole('dialog').evaluate(el=>el.scrollWidth<=el.clientWidth));
    await page.getByRole('button',{name:'Save skill',exact:true}).click();
    await page.locator('[data-skill-content] pre').waitFor();assert.equal(confirmed,1);
    await page.evaluate(()=>window.chat.leave());
  });
  assert.ok(requests.some((r) => r.path.includes("p%20%2F%20one")));
  assert.ok(requests.every((r) => r.method === "GET" || r.method === "PUT" && r.path.endsWith("/activations")));
});
