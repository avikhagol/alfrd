import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { viewerFor, registerViewer, renderViewer, fileObject, openViewer } from "../../src/alfrd/web/js/components/viewers.js";
import { activate } from "../../examples/plugins/alfrd-markdown/alfrd_markdown/web/index.js";

// Exercise each private click handler without starting the chat pollers.
async function handler(component, deps) {
  const source = await readFile(new URL(`../../src/alfrd/web/js/components/${component}.js`, import.meta.url), "utf8");
  const start = source.indexOf("async function viewArtifact(ref)");
  const body = source.slice(start, source.indexOf("\n}\n", start) + 2)
    .replace('await import("./logview.js")', 'logview')
    .replace('await import("./viewers.js")', 'viewers');
  return new Function(...Object.keys(deps), `${body}; return viewArtifact;`)(...Object.values(deps));
}

const element = () => ({ textContent: "", innerHTML: "", dataset: {}, classList: { add() {} } });
const settle = () => new Promise((resolve) => setImmediate(resolve));

for (const component of ["chat_view", "chat_dock"]) {
  test(`${component}: artifact Markdown uses the installed plugin and verified bytes; other files retain text routing`, async () => {
    const el = element(), more = {}, seen = [], urls = [], textViews = [], warnings = [];
    let html, path, eof = false;
    const ctx = {
      modal(markup, setup) {
        html = markup;
        setup({ querySelector: (selector) => selector === ".viewer-full" ? el : null });
      },
      toast: (message) => warnings.push(message),
    };
    const pluginApi = { registerViewer, sanitize: (html) => html };
    activate(pluginApi);
    const markdown = viewerFor("docs/result.md");
    const original = markdown.render;
    markdown.render = (file, host) => { seen.push(file); return original(file, host); };
    const view = await handler(component, {
      ctxRef: ctx, S: { project: "p", sid: "s" }, D: { project: "p", sid: "s" },
      base: () => "/chat", URLSearchParams, viewerFor, renderViewer, fileObject, openViewer,
      viewers: { viewerFor, renderViewer, fileObject },
      esc: (s) => String(s), icon: () => "", $: (selector) => selector === "#chat-art" ? el : more,
      logview: { openFull: (...args) => textViews.push(args) },
      get: async (url) => {
        urls.push(url);
        const offset = Number(new URL(url, "http://localhost").searchParams.get("offset"));
        return { path, content: offset ? "\n## Complete" : "# Result", offset, length: offset ? 12 : 8, eof: eof || !!offset, sha256: "recorded", size: 20 };
      },
    });
    for (path of ["docs/result.md", "docs/RESULT.MARKDOWN"]) {
      await view(`${path}@recorded`); await settle();
      assert.match(el.innerHTML, /<h1>Result<\/h1>/);
      assert.equal(seen.at(-1).text, "# Result");
      assert.equal(seen.at(-1).path, path);
      assert.equal(seen.at(-1).project, "p");
      assert.match(html, /class="viewer viewer-full"/);
      if (component === "chat_view") {
        assert.equal(more.hidden, false);
        await more.onclick();
        assert.match(el.innerHTML, /<h2>Complete<\/h2>/);
        assert.equal(seen.at(-1).text, "# Result\n## Complete");
        assert.equal(more.hidden, true);
      } else assert.match(warnings.at(-1), /Partial text/);
    }
    assert.ok(urls.every((url) => url.includes("ref=docs%2F") && url.includes("%40recorded")));
    const count = seen.length;
    for (path of ["docs/result.txt", "docs/result.json", "plot.png", "report.pdf"]) {
      await view(`${path}@recorded`); await settle();
      assert.equal(seen.length, count);
      if (component === "chat_view") {
        assert.match(html, /<pre class="log log-full"/);
        assert.equal(el.textContent, "# Result");
      } else assert.equal(textViews.at(-1)[1], path);
    }
    // Missing plugin keeps the existing source view.
    registerViewer({ id: "markdown", match: [], render: original });
    path = "docs/result.md"; eof = true;
    await view(`${path}@recorded`); await settle();
    assert.equal(seen.length, count);
    if (component === "chat_view") assert.match(html, /<pre class="log log-full"/);
    else assert.equal(textViews.at(-1)[1], path);
    activate(pluginApi);
    if (component === "chat_view") {
      viewerFor(path).render = () => { throw Error("preview failed"); };
      await view(`${path}@recorded`); await settle();
      assert.equal(el.textContent, "# Result");
      assert.equal(warnings.at(-1), "preview failed");
    }
  });
}
