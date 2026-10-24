"""Built-in theme contrast checks (WS9 / D2). Token reading and maths live in contrast_check.py.

Pairs that fail today are marked xfail(strict=True) with their measured ratio. The CSS is
deliberately not changed here: fixing a pair makes its test XPASS, which fails the run until
its entry is removed from KNOWN_FAILURES. Failures are listed in
docs/v0.4.0.0/impl/contrast_failures.md in the brainstorm workspace.
"""

import pytest
from contrast_check import (
    BASE_CSS,
    THEMES,
    all_pairs,
    composite,
    contrast_ratio,
    load_tokens,
    measure,
    parse_color,
    read_root_tokens,
)

KNOWN_FAILURES = {
    ("studio-base", "text: on-accent on accent"),
    ("studio-base", "focus: accent on surface-float"),
    ("studio-base", "ui: border on surface"),
    ("studio-base", "disabled: on-accent on accent-control @0.45"),
    ("obsidian-orbit", "text: on-accent on accent"),
    ("obsidian-orbit", "focus: accent on surface-float"),
    ("obsidian-orbit", "ui: border on surface"),
    ("obsidian-orbit", "disabled: on-accent on accent-control @0.45"),
    ("daylight-orbit", "ui: border on surface"),
    ("daylight-orbit", "disabled: text-2 on surface @0.45"),
    ("daylight-orbit", "disabled: on-accent on accent-control @0.45"),
}


def _param(theme, pair):
    key = (theme, pair.name)
    marks = []
    if key in KNOWN_FAILURES:
        ratio = measure(theme, pair)
        marks.append(
            pytest.mark.xfail(
                strict=True,
                reason=f"known failure: measured {ratio:.2f}:1, needs {pair.minimum:g}:1",
            )
        )
    return pytest.param(theme, pair, id=f"{theme}-{pair.name}", marks=marks)


@pytest.mark.parametrize("theme,pair", [_param(t, p) for t in THEMES for p in all_pairs()])
def test_theme_pair_contrast(theme, pair):
    ratio = measure(theme, pair)
    assert ratio >= pair.minimum, f"{theme}: {pair.name} is {ratio:.2f}:1, needs {pair.minimum:g}:1"


def test_known_failures_name_real_pairs():
    names = {p.name for p in all_pairs()}
    stale = {(theme, name) for theme, name in KNOWN_FAILURES if name not in names or theme not in THEMES}
    assert not stale, f"KNOWN_FAILURES entries that match no pair: {sorted(stale)}"


def test_reference_contrast_ratios():
    assert contrast_ratio((255, 255, 255), (0, 0, 0)) == pytest.approx(21.0)
    assert contrast_ratio((119, 119, 119), (119, 119, 119)) == pytest.approx(1.0)


def test_parse_color_forms():
    assert parse_color("#fff", {}) == (255.0, 255.0, 255.0, 1.0)
    assert parse_color("#0F1523", {}) == (15, 21, 35, 1.0)
    assert parse_color("rgba(15, 23, 42, .10)", {}) == (15, 23, 42, 0.10)
    assert parse_color("var(--bg)", {"--bg": "#090D16"}) == (9, 13, 22, 1.0)


def test_rgba_composites_over_surface():
    rgb = composite(parse_color("rgba(255, 255, 255, .08)", {}), (0, 0, 0))
    assert rgb == pytest.approx((20.4, 20.4, 20.4))


def test_daylight_overrides_base_tokens():
    base = read_root_tokens(BASE_CSS)
    daylight = load_tokens("daylight-orbit")
    assert base["--bg"] == "#090D16"
    assert daylight["--bg"] == "#F6F7FB"
    assert daylight["--text"] == "#0F172A"
    # Tokens the light theme does not override keep the dark base value.
    assert daylight["--rail"] == base["--rail"]


def test_every_pair_token_resolves_in_every_theme():
    for theme in THEMES:
        tokens = load_tokens(theme)
        for pair in all_pairs():
            measure(theme, pair, tokens)
