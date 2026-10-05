// Lightweight YAML reader/writer for ALFRD manifests (alfrd.yaml, templates).
//
// Supports the subset ALFRD manifests use: block mappings and sequences,
// "- key: value" sequence items, flow collections ([a, b], {a: 1}), quoted and
// plain scalars, block scalars (| and >), comments and a leading "---".
// Anchors, tags and multi-document streams are intentionally unsupported and
// reported as errors instead of being silently misread.

export class YamlError extends Error {
  constructor(message, line) {
    super(line ? `${message} (line ${line})` : message);
    this.line = line;
  }
}

function stripComment(text) {
  let quote = null;
  for (let i = 0; i < text.length; i += 1) {
    const c = text[i];
    if (quote) {
      if (c === "\\" && quote === '"') i += 1;
      else if (c === quote) quote = null;
    } else if (c === '"' || c === "'") {
      quote = c;
    } else if (c === "#" && (i === 0 || /\s/.test(text[i - 1]))) {
      return text.slice(0, i).trimEnd();
    }
  }
  return text.trimEnd();
}

function lex(source) {
  const lines = [];
  source.replace(/\r\n?/g, "\n").split("\n").forEach((raw, index) => {
    if (/^\t/.test(raw)) throw new YamlError("tabs are not allowed for indentation", index + 1);
    lines.push({ raw, no: index + 1 });
  });
  return lines;
}

/** Split `key: value` at the first mapping colon outside quotes/brackets. */
function splitKey(text) {
  let quote = null;
  let depth = 0;
  for (let i = 0; i < text.length; i += 1) {
    const c = text[i];
    if (quote) {
      if (c === "\\" && quote === '"') i += 1;
      else if (c === quote) quote = null;
      continue;
    }
    if (c === '"' || c === "'") quote = c;
    else if (c === "[" || c === "{") depth += 1;
    else if (c === "]" || c === "}") depth -= 1;
    else if (c === ":" && depth === 0 && (i === text.length - 1 || text[i + 1] === " ")) {
      return [text.slice(0, i).trim(), text.slice(i + 1).trim()];
    }
  }
  return null;
}

function unquote(key) {
  if ((key.startsWith('"') && key.endsWith('"')) || (key.startsWith("'") && key.endsWith("'"))) {
    return scalar(key);
  }
  return key;
}

export function scalar(text) {
  const t = text.trim();
  if (t === "" || t === "~" || t === "null" || t === "Null" || t === "NULL") return null;
  if (t.startsWith('"')) {
    if (!t.endsWith('"') || t.length < 2) throw new YamlError(`unterminated string ${t}`);
    try {
      return JSON.parse(t);
    } catch {
      return t.slice(1, -1);
    }
  }
  if (t.startsWith("'")) {
    if (!t.endsWith("'") || t.length < 2) throw new YamlError(`unterminated string ${t}`);
    return t.slice(1, -1).replace(/''/g, "'");
  }
  if (/^(true|True|TRUE)$/.test(t)) return true;
  if (/^(false|False|FALSE)$/.test(t)) return false;
  if (/^[-+]?\d+$/.test(t)) return Number.parseInt(t, 10);
  if (/^[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?$/.test(t)) return Number.parseFloat(t);
  if (/^[-+]?\.(inf|Inf)$/.test(t)) return t.startsWith("-") ? -Infinity : Infinity;
  if (t.startsWith("&") || t.startsWith("*") || t.startsWith("!")) {
    throw new YamlError(`anchors, aliases and tags are not supported: ${t}`);
  }
  return t;
}

/** Parse a flow collection or scalar such as `[a, {b: 1}]`. */
function flow(text, lineNo) {
  let i = 0;
  const s = text.trim();
  const ws = () => {
    while (i < s.length && /\s/.test(s[i])) i += 1;
  };
  const value = () => {
    ws();
    if (s[i] === "[") {
      i += 1;
      const out = [];
      ws();
      if (s[i] === "]") {
        i += 1;
        return out;
      }
      for (;;) {
        out.push(value());
        ws();
        if (s[i] === ",") {
          i += 1;
          ws();
          if (s[i] === "]") {
            i += 1;
            return out;
          }
        } else if (s[i] === "]") {
          i += 1;
          return out;
        } else throw new YamlError(`expected , or ] in ${s}`, lineNo);
      }
    }
    if (s[i] === "{") {
      i += 1;
      const out = {};
      ws();
      if (s[i] === "}") {
        i += 1;
        return out;
      }
      for (;;) {
        ws();
        const start = i;
        let quote = null;
        while (i < s.length) {
          const c = s[i];
          if (quote) {
            if (c === quote) quote = null;
          } else if (c === '"' || c === "'") quote = c;
          else if (c === ":" || c === "," || c === "}") break;
          i += 1;
        }
        const key = unquote(s.slice(start, i).trim());
        let val = null;
        if (s[i] === ":") {
          i += 1;
          val = value();
        }
        out[key] = val;
        ws();
        if (s[i] === ",") i += 1;
        else if (s[i] === "}") {
          i += 1;
          return out;
        } else throw new YamlError(`expected , or } in ${s}`, lineNo);
      }
    }
    const start = i;
    let quote = null;
    let depth = 0;
    while (i < s.length) {
      const c = s[i];
      if (quote) {
        if (c === "\\" && quote === '"') i += 1;
        else if (c === quote) quote = null;
      } else if (c === '"' || c === "'") quote = c;
      else if (c === "[" || c === "{") depth += 1;
      else if ((c === "]" || c === "}") && depth > 0) depth -= 1;
      else if ((c === "," || c === "]" || c === "}") && depth === 0) break;
      i += 1;
    }
    return scalar(s.slice(start, i));
  };
  const result = value();
  ws();
  if (i < s.length) throw new YamlError(`unexpected trailing content: ${s.slice(i)}`, lineNo);
  return result;
}

function inlineValue(text, lineNo) {
  if (text.startsWith("[") || text.startsWith("{")) return flow(text, lineNo);
  return scalar(text);
}

class Parser {
  constructor(source) {
    this.lines = lex(source);
    this.i = 0;
  }

  // Skip blank/comment lines; return the next meaningful line or null.
  peek() {
    while (this.i < this.lines.length) {
      const line = this.lines[this.i];
      const text = stripComment(line.raw);
      if (text.trim() === "" || text.trim() === "---" || text.trim() === "...") {
        if (text.trim() === "---" && this.seenContent) {
          throw new YamlError("multiple YAML documents are not supported", line.no);
        }
        this.i += 1;
        continue;
      }
      const indent = text.length - text.trimStart().length;
      return { indent, text: text.trim(), no: line.no, raw: line.raw };
    }
    return null;
  }

  parse() {
    const first = this.peek();
    if (!first) return null;
    this.seenContent = true;
    const value = this.block(first.indent);
    const rest = this.peek();
    if (rest) throw new YamlError(`unexpected indentation`, rest.no);
    return value;
  }

  block(indent) {
    const line = this.peek();
    if (!line) return null;
    if (line.text === "-" || line.text.startsWith("- ")) return this.sequence(line.indent);
    if (!splitKey(line.text)) {
      // A bare scalar document or continuation.
      this.i += 1;
      return inlineValue(line.text, line.no);
    }
    return this.mapping(line.indent);
  }

  sequence(indent) {
    const out = [];
    for (;;) {
      const line = this.peek();
      if (!line || line.indent < indent) break;
      if (line.indent > indent) throw new YamlError("bad sequence indentation", line.no);
      if (!(line.text === "-" || line.text.startsWith("- "))) break;
      const rest = line.text === "-" ? "" : line.text.slice(2).trim();
      if (rest === "") {
        this.i += 1;
        const next = this.peek();
        out.push(next && next.indent > indent ? this.block(next.indent) : null);
        continue;
      }
      const kv = splitKey(rest);
      if (kv && !rest.startsWith("[") && !rest.startsWith("{")) {
        // "- key: value" starts an inline mapping; rewrite the line so the
        // mapping parser sees the key at its real column.
        const col = indent + 2 + (line.text.slice(2).length - line.text.slice(2).trimStart().length);
        this.lines[this.i] = { raw: " ".repeat(col) + rest, no: line.no };
        out.push(this.mapping(col));
      } else {
        this.i += 1;
        out.push(this.scalarOrBlockScalar(rest, indent, line.no));
      }
    }
    return out;
  }

  mapping(indent) {
    const out = {};
    for (;;) {
      const line = this.peek();
      if (!line || line.indent < indent) break;
      if (line.indent > indent) throw new YamlError("bad mapping indentation", line.no);
      if (line.text.startsWith("- ")) break;
      const kv = splitKey(line.text);
      if (!kv) throw new YamlError(`expected "key: value", got "${line.text}"`, line.no);
      const key = unquote(kv[0]);
      if (Object.prototype.hasOwnProperty.call(out, key)) {
        throw new YamlError(`duplicate key "${key}"`, line.no);
      }
      this.i += 1;
      if (kv[1] === "") {
        const next = this.peek();
        if (next && next.indent > indent) out[key] = this.block(next.indent);
        else if (next && next.indent === indent && (next.text.startsWith("- ") || next.text === "-")) {
          out[key] = this.sequence(indent);
        } else out[key] = null;
      } else {
        out[key] = this.scalarOrBlockScalar(kv[1], indent, line.no);
      }
    }
    return out;
  }

  // A quoted scalar continued over several lines (as PyYAML writes long strings):
  // a line break folds to a space, each blank line to "\n", `\` ends a "…" line without one.
  multiline(text, indent) {
    const q = text[0];
    const closed = (t) => {
      for (let i = 1; i < t.length; i += 1) {
        if (q === '"' && t[i] === "\\") { i += 1; continue; }
        if (t[i] === q) {
          if (q === "'" && t[i + 1] === "'") { i += 1; continue; }
          return true;
        }
      }
      return false;
    };
    if (closed(text)) return text;
    let out = text.trimEnd(), newlines = 0;
    while (this.i < this.lines.length) {
      const raw = this.lines[this.i].raw;
      const ind = raw.length - raw.trimStart().length;
      if (raw.trim() !== "" && ind <= indent) break;
      this.i += 1;
      if (raw.trim() === "") { newlines += 1; continue; }
      const part = raw.trim();
      if (q === '"' && out.endsWith("\\") && !newlines) out = out.slice(0, -1) + part;
      else out += (newlines ? "\n".repeat(newlines) : " ") + part;
      newlines = 0;
      if (closed(out)) return q === '"' ? out.replace(/\n/g, "\\n") : out;
    }
    return out;  // still open: scalar() reports the unterminated string
  }

  scalarOrBlockScalar(text, indent, lineNo) {
    if (text[0] === "'" || text[0] === '"') text = this.multiline(text, indent);
    const m = /^([|>])([+-]?)$/.exec(text);
    if (!m) return inlineValue(text, lineNo);
    const folded = m[1] === ">";
    const keep = m[2];
    const collected = [];
    let blockIndent = null;
    while (this.i < this.lines.length) {
      const raw = this.lines[this.i].raw;
      if (raw.trim() === "") {
        collected.push("");
        this.i += 1;
        continue;
      }
      const ind = raw.length - raw.trimStart().length;
      if (ind <= indent) break;
      if (blockIndent === null) blockIndent = ind;
      if (ind < blockIndent) break;
      collected.push(raw.slice(blockIndent));
      this.i += 1;
    }
    while (collected.length && collected[collected.length - 1] === "" && keep !== "+") collected.pop();
    let body = folded
      ? collected.reduce((acc, l, idx) => (idx === 0 ? l : l === "" ? `${acc}\n` : acc.endsWith("\n") ? acc + l : `${acc} ${l}`), "")
      : collected.join("\n");
    if (keep !== "-") body += "\n";
    return body;
  }
}

/** Parse YAML text into plain JS values. Throws YamlError with a line number. */
export function parseYaml(source) {
  return new Parser(String(source ?? "")).parse();
}

function dumpScalar(value, forceQuoted = false) {
  if (value === null || value === undefined) return "null";
  if (typeof value === "boolean" || typeof value === "number") return String(value);
  const s = String(value);
  if (forceQuoted || /[\x00-\x1f\x7f-\x9f\u2028\u2029]/.test(s)) return JSON.stringify(s).replace(/[\x7f-\x9f\u2028\u2029]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, "0")}`);
  if (s === "" || /^[\s]|[\s]$|[:#\[\]{},&*!|>'"%@`]|^-|^(true|false|null|~|yes|no)$/i.test(s) || /^[-+]?[\d.]+([eE][-+]?\d+)?$/.test(s)) {
    return JSON.stringify(s);
  }
  return s;
}

/** Serialize plain JS values back to readable block-style YAML. */
export function dumpYaml(value, indent = 0) {
  const pad = " ".repeat(indent);
  if (Array.isArray(value)) {
    if (!value.length) return `${pad}[]\n`;
    return value
      .map((item) => {
        if (item && typeof item === "object" && !Array.isArray(item) && Object.keys(item).length) {
          const inner = dumpYaml(item, indent + 2);
          return `${pad}- ${inner.slice(indent + 2)}`;
        }
        if (Array.isArray(item) && item.length) return `${pad}-\n${dumpYaml(item, indent + 2)}`;
        return `${pad}- ${item && typeof item === "object" ? (Array.isArray(item) ? "[]" : "{}") : dumpScalar(item)}\n`;
      })
      .join("");
  }
  if (value && typeof value === "object") {
    const keys = Object.keys(value);
    if (!keys.length) return `${pad}{}\n`;
    return keys
      .map((key) => {
        const v = value[key];
        const k = dumpScalar(key);
        if (Array.isArray(v) && v.length) {
          const simple = v.every((x) => x === null || typeof x !== "object");
          if (simple && v.length <= 6 && v.join(", ").length < 60) return `${pad}${k}: [${v.map((item) => dumpScalar(item)).join(", ")}]\n`;
          return `${pad}${k}:\n${dumpYaml(v, indent + 2)}`;
        }
        if (v && typeof v === "object" && !Array.isArray(v) && Object.keys(v).length) {
          return `${pad}${k}:\n${dumpYaml(v, indent + 2)}`;
        }

        return `${pad}${k}: ${Array.isArray(v) ? "[]" : v && typeof v === "object" ? "{}" : dumpScalar(v, key === "instructions")}\n`;
      })
      .join("");
  }
  return `${pad}${dumpScalar(value)}\n`;
}
