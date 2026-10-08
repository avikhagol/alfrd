import { marked } from "./marked.esm.js";

export function activate(api) {
  api.registerViewer({
    id: "markdown",
    match: [".md", ".markdown", "text/markdown"],
    render(file, host) {
      host.classList.add("markdown-body");
      // The host API preloads DOMPurify before activation; sanitize is synchronous.
      host.innerHTML = api.sanitize(marked.parse(file.text || "", { async: false }));
    },
  });
}
