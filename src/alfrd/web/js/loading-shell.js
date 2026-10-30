// Runs before the module graph: reserve the user's saved panel geometry for first paint.
(() => {
  const read = (key, fallback) => { try { return JSON.parse(localStorage.getItem(`alfrd-studio:${key}`)) ?? fallback; } catch { return fallback; } };
  const app = document.getElementById("app"), shell = read("ui:shell.v2", {});
  app.dataset.sidebar = shell.sidebarCollapsed === false ? "expanded" : "collapsed";
  if (shell.navWidth) app.style.setProperty("--nav-width", `${Math.max(180, Math.min(360, shell.navWidth))}px`);
  const collapsed = read("ui:chat.sessionsCollapsed", false) === true;
  const open = read("prefs.v2", {}).values?.["ui.chat_inspector"] === true;
  const layouts = [document.querySelector(".loading-layout"), document.getElementById("loading-outline").content.querySelector(".loading-layout")];
  for (const el of layouts) {
    el.dataset.collapsed = String(collapsed); el.dataset.inspector = open ? "open" : "closed";
    el.style.setProperty("--sessions-width", `${Math.max(160, Math.min(360, read("ui:chat.sessionsWidth", 220)))}px`);
    el.style.setProperty("--inspector-width", `${Math.max(280, Math.min(560, read("ui:chat.inspectorWidth", 320)))}px`);
  }
})();
