"""Focus-ring token checks (WS9 / D1).

The ``--focus`` token is the one colour for ``:focus-visible`` outlines. It must reach
3:1 against every surface a built-in theme paints, and the changed surfaces must use it.
Ratios come from contrast_check.py, so rgba and opacity are composited the same way.
"""

import re

import pytest
from contrast_check import (
    BASE_CSS,
    THEMES,
    UI_MIN,
    WEB,
    Pair,
    measure,
    read_root_tokens,
)

# Mirrors the surfaces contrast_check.all_pairs() uses for focus and text.
FOCUS_SURFACES = ("bg", "surface", "surface-2", "surface-float", "console-bg")
CHAT_CSS = WEB / "css" / "chat.css"
STUDIO_CSS = WEB / "css" / "studio.css"


def _rule_bodies(text, selector_part):
    """Declaration blocks of every rule whose selector list contains ``selector_part``."""
    return [
        m.group(2)
        for m in re.finditer(r"([^{}]*)\{([^}]*)\}", text)
        if selector_part in m.group(1)
    ]


@pytest.mark.parametrize("theme", list(THEMES))
@pytest.mark.parametrize("surface", FOCUS_SURFACES)
def test_focus_token_contrast_on_every_surface(theme, surface):
    ratio = measure(theme, Pair("focus", "focus", surface, UI_MIN))
    assert ratio >= UI_MIN, (
        f"{theme}: --focus on {surface} is {ratio:.2f}:1, needs {UI_MIN:g}:1"
    )


def test_focus_token_defined_in_base_and_overridden_by_light_theme():
    assert "--focus" in read_root_tokens(BASE_CSS)
    assert "--focus" in read_root_tokens(
        WEB / "css" / "themes" / "daylight-orbit" / "theme.css"
    )


def test_chat_focus_outlines_use_token():
    text = CHAT_CSS.read_text(encoding="utf-8")
    offenders = [
        line
        for line in text.splitlines()
        if ":focus-visible" in line
        and "outline:" in line
        and "outline: none" not in line
        and "var(--focus)" not in line
    ]
    assert not offenders, (
        f"chat.css focus-visible outlines without --focus: {offenders}"
    )


def test_studio_wave_focus_rules_use_token():
    text = STUDIO_CSS.read_text(encoding="utf-8")
    assert "outline: 2px solid var(--focus); outline-offset: 2px; }" in text
    for selector in (
        ".att-list button:focus-visible",
        ":where(button, a, input, select, textarea):focus-visible",
    ):
        bodies = _rule_bodies(text, selector)
        assert bodies, f"{selector} rule missing"
        assert any("var(--focus)" in body for body in bodies), (
            f"{selector} does not use --focus"
        )


def test_search_and_select_wrappers_show_focus_within():
    text = STUDIO_CSS.read_text(encoding="utf-8")
    for wrapper in (".search:focus-within", ".select-wrap:focus-within"):
        bodies = _rule_bodies(text, wrapper)
        assert bodies, f"{wrapper} rule missing"
        assert any("var(--focus)" in body and "outline" in body for body in bodies), (
            f"{wrapper} does not draw a --focus outline"
        )
