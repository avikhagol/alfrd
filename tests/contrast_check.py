"""Contrast checks for the built-in Studio themes (WS9 / D2).

Standard library only. Tokens come from the CSS files. ``studio.css`` ``:root``
is the dark base; a theme's override file is applied after it, the same order
the browser uses. Every rgba value and every ``opacity`` is composited over its
opaque backdrop before the WCAG 2.x ratio is taken.

Run ``python3 tests/contrast_check.py`` to print every pair and its ratio.
"""

import re
from dataclasses import dataclass
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "src" / "alfrd" / "web"
BASE_CSS = WEB / "css" / "studio.css"
THEMES = {
    "studio-base": [],
    "obsidian-orbit": [WEB / "css" / "themes" / "obsidian-orbit" / "theme.css"],
    "daylight-orbit": [WEB / "css" / "themes" / "daylight-orbit" / "theme.css"],
}

TEXT_MIN = 4.5
UI_MIN = 3.0
PAGE = "surface"

_ROOT_RE = re.compile(r":root\s*\{([^}]*)\}")
_DECL_RE = re.compile(r"(--[\w-]+)\s*:\s*([^;]+?)\s*;")
_VAR_RE = re.compile(r"var\(\s*(--[\w-]+)\s*\)")
_HEX_RE = re.compile(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})")
_FUNC_RE = re.compile(r"rgba?\(([^)]*)\)")


@dataclass(frozen=True)
class Pair:
    kind: str  # "text", "ui", "focus" or "disabled"
    fg: str  # token for the text or UI colour
    bg: str  # token for the surface the colour sits on
    minimum: float
    opacity: float = 1.0  # element opacity, applied to foreground and background together

    @property
    def name(self):
        suffix = f" @{self.opacity:g}" if self.opacity < 1 else ""
        return f"{self.kind}: {self.fg} on {self.bg}{suffix}"


def read_root_tokens(path):
    """Custom properties in the first top-level ``:root`` block of a CSS file."""
    match = _ROOT_RE.search(path.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError(f"no :root block in {path}")
    return dict(_DECL_RE.findall(match.group(1)))


def load_tokens(theme):
    """The cascade for one theme: base ``studio.css`` then its override files."""
    tokens = read_root_tokens(BASE_CSS)
    for path in THEMES[theme]:
        tokens.update(read_root_tokens(path))
    return tokens


def parse_color(value, tokens, _depth=0):
    """``(r, g, b, a)`` for a hex, rgb()/rgba() or var() value; r, g, b in 0-255."""
    if _depth > 8:
        raise ValueError(f"var() cycle at {value!r}")
    value = value.strip()
    match = _VAR_RE.fullmatch(value)
    if match:
        return parse_color(tokens[match.group(1)], tokens, _depth + 1)
    match = _HEX_RE.fullmatch(value)
    if match:
        digits = match.group(1)
        if len(digits) == 3:
            digits = "".join(c * 2 for c in digits)
        r, g, b = (int(digits[i : i + 2], 16) for i in (0, 2, 4))
        return (r, g, b, 1.0)
    match = _FUNC_RE.fullmatch(value)
    if match:
        parts = [p.strip() for p in match.group(1).split(",")]
        if len(parts) not in (3, 4):
            raise ValueError(f"bad colour function {value!r}")
        r, g, b = (float(p) for p in parts[:3])
        a = float(parts[3]) if len(parts) == 4 else 1.0
        return (r, g, b, a)
    raise ValueError(f"not a colour: {value!r}")


def composite(top, bottom):
    """Blend a colour with alpha ``top[3]`` over an opaque ``bottom`` (r, g, b)."""
    a = top[3]
    return tuple(a * t + (1 - a) * b for t, b in zip(top[:3], bottom))


def luminance(rgb):
    """WCAG relative luminance of an opaque (r, g, b) colour."""

    def linear(channel):
        c = channel / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(first, second):
    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def measure(theme, pair, tokens=None):
    """WCAG ratio of a pair in a theme, with rgba and opacity composited over the page."""
    tokens = load_tokens(theme) if tokens is None else tokens
    page = parse_color(tokens[f"--{PAGE}"], tokens)[:3]
    bg = composite(parse_color(tokens[f"--{pair.bg}"], tokens), page)
    fg = composite(parse_color(tokens[f"--{pair.fg}"], tokens), bg)
    if pair.opacity < 1:
        fg = composite((*fg, pair.opacity), page)
        bg = composite((*bg, pair.opacity), page)
    return contrast_ratio(fg, bg)


def all_pairs():
    pairs = []
    surfaces = ("bg", "surface", "surface-2", "surface-float", "console-bg")
    for fg in ("text", "text-2", "text-3"):
        for bg in surfaces:
            pairs.append(Pair("text", fg, bg, TEXT_MIN))
    for fg in ("accent-text", "ok-text", "warn-text", "fail-text"):
        for bg in ("surface", "surface-2"):
            pairs.append(Pair("text", fg, bg, TEXT_MIN))
    # Badge and pill tints are rgba over the surface they sit on.
    for fg, bg in (
        ("accent-text", "accent-bg"),
        ("ok-text", "ok-bg"),
        ("warn-text", "warn-bg"),
        ("fail-text", "fail-bg"),
        ("text-3", "muted-bg"),
    ):
        pairs.append(Pair("text", fg, bg, TEXT_MIN))
    pairs.append(Pair("text", "on-accent", "accent-control", TEXT_MIN))
    pairs.append(Pair("text", "on-accent", "accent", TEXT_MIN))
    pairs.append(Pair("text", "on-ok", "ok-control", TEXT_MIN))

    for bg in surfaces:
        pairs.append(Pair("focus", "accent", bg, UI_MIN))
    pairs.append(Pair("ui", "accent-control", "surface", UI_MIN))
    for fg in ("ok", "warn", "fail"):
        pairs.append(Pair("ui", fg, "surface", UI_MIN))
    # Form controls (.input, .select-wrap, .picker) use --border as their only edge.
    pairs.append(Pair("ui", "border", "surface", UI_MIN))

    pairs.append(Pair("disabled", "text-2", "surface", UI_MIN, opacity=0.45))
    pairs.append(Pair("disabled", "on-accent", "accent-control", UI_MIN, opacity=0.45))
    pairs.append(Pair("disabled", "text-3", "surface", UI_MIN))
    return pairs


def main():
    for theme in THEMES:
        tokens = load_tokens(theme)
        print(f"== {theme}")
        for pair in all_pairs():
            ratio = measure(theme, pair, tokens)
            verdict = "ok  " if ratio >= pair.minimum else "FAIL"
            print(f"{verdict} {ratio:6.2f}:1 (min {pair.minimum:g})  {pair.name}")


if __name__ == "__main__":
    main()
