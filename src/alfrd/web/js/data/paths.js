// Pure path helpers for the server folder browser (no DOM).

/**
 * Split an absolute server path into breadcrumb items `[{name, path}]`.
 * POSIX: "/a/b" → [{"/", "/"}, {"a", "/a"}, {"b", "/a/b"}].
 * Windows: "C:\\a" → [{"C:\\", "C:\\"}, {"a", "C:\\a"}]; UNC "\\\\srv\\share\\x" keeps "\\\\srv\\share\\" as root.
 */
export function pathCrumbs(path) {
  const p = String(path || "");
  if (!p) return [];
  const win = /^[A-Za-z]:[\\/]/.test(p) || p.startsWith("\\\\");
  if (!win) {
    const parts = p.split("/").filter(Boolean);
    const out = [{ name: "/", path: "/" }];
    let acc = "";
    for (const part of parts) {
      acc += `/${part}`;
      out.push({ name: part, path: acc });
    }
    return out;
  }
  const sep = "\\";
  const norm = p.replace(/\//g, sep);
  let root;
  let rest;
  if (norm.startsWith("\\\\")) {
    const bits = norm.slice(2).split(sep).filter(Boolean);
    root = `\\\\${bits.slice(0, 2).join(sep)}${sep}`;
    rest = bits.slice(2);
  } else {
    root = `${norm.slice(0, 2)}${sep}`;
    rest = norm.slice(3).split(sep).filter(Boolean);
  }
  const out = [{ name: root, path: root }];
  let acc = root;
  for (const part of rest) {
    acc = acc.endsWith(sep) ? `${acc}${part}` : `${acc}${sep}${part}`;
    out.push({ name: part, path: acc });
  }
  return out;
}

/** Project rows of a folder listing, sorted as listed (for "Connect all projects here"). */
export function projectEntries(listing) {
  return (listing?.entries || []).filter((e) => e.is_project && !e.is_ms);
}
