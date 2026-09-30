// Fuzzy scorer for the command palette (no library): every query word must match
// the text, word starts count most. "j07 rpi" → "J0742+103 · rpicard · casa.log".

const isAlnum = (c) => /[a-z0-9]/i.test(c);

function starts(text) {
  const out = new Set([0]);
  for (let i = 1; i < text.length; i += 1) {
    const a = text[i - 1];
    const b = text[i];
    if ((!isAlnum(a) && isAlnum(b)) || (/[a-z]/.test(a) && /[A-Z]/.test(b))) out.add(i);
  }
  return out;
}

/** Score of one query word in `text` (lower-cased `low`, word starts `ws`); -1 = no match. */
function wordScore(word, low, ws) {
  let best = -1;
  let at = low.indexOf(word);
  while (at >= 0) {
    best = Math.max(best, (ws.has(at) ? 100 : 40) + word.length * 4 - Math.min(at, 20) * 0.5);
    at = low.indexOf(word, at + 1);
  }
  if (best >= 60) return best;
  // Initials: every character at a word start ("pp" → "Pause plan").
  const heads = [...ws].sort((a, b) => a - b);
  let k = 0;
  for (const h of heads) if (k < word.length && low[h] === word[k]) k += 1;
  if (k === word.length && word.length > 1) best = Math.max(best, 60 + word.length * 4);
  if (best >= 0) return best;
  // Subsequence: each character in order, bonus at word starts and for runs.
  let score = 0;
  let pos = -1;
  let run = 0;
  for (const ch of word) {
    const next = low.indexOf(ch, pos + 1);
    if (next < 0) return -1;
    run = next === pos + 1 ? run + 1 : 0;
    score += 1 + (ws.has(next) ? 6 : 0) + run * 2 - Math.min(next - pos - 1, 10) * 0.3;
    pos = next;
  }
  return score;
}

/** Score of `query` against `text` (higher is better), or -1 when a word does not match. */
export function fuzzyScore(query, text) {
  const words = String(query || "").toLowerCase().split(/\s+/).filter(Boolean);
  const raw = String(text || "");
  if (!words.length) return 0;
  const low = raw.toLowerCase();
  const ws = starts(raw);
  let total = 0;
  for (const w of words) {
    const s = wordScore(w, low, ws);
    if (s < 0) return -1;
    total += s;
  }
  return total - raw.length * 0.05;
}

/** Items ({label, detail, keywords}) that match, best first; ties keep their input order. */
export function fuzzyRank(query, items, limit = 50) {
  const scored = [];
  items.forEach((it, i) => {
    const s = fuzzyScore(query, [it.label, it.detail, it.keywords].filter(Boolean).join(" · "));
    if (s >= 0) scored.push([s + (it.boost || 0), i, it]);
  });
  scored.sort((a, b) => b[0] - a[0] || a[1] - b[1]);
  return scored.slice(0, limit).map((x) => x[2]);
}
