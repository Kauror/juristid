"""Text and boundaries meet WCAG AA on the surfaces they sit on, in both themes.

ENG-101 found `--text-muted` (#7d8b99) at 4.29:1 on `--surface-selected` and
4.49:1 on `--surface-elevated` — under the 4.5:1 AA asks of normal-size text.
Three rules put it there: the selected segmented option's count, the
inactive-looking «Loo teema» (which still submits), and the Minu asjad pad's
meta line. Each now uses `--text-secondary`. These tests read the colours out of
the stylesheets and recompute the ratios, so a later token or rule change that
drops one below AA fails here rather than in a screen reader user's afternoon.

Two palettes are reachable since docs/adr/0147: the dark default and the light
theme a browser can choose. Each is resolved on its own — the light block over
the dark one, exactly as the cascade does it — and held to what it meets:

* the light theme meets AA for every text role on every surface, and 3:1 for
  the boundaries a person has to find;
* the dark theme meets AA for every role but `--text-muted` on four raised or
  tinted surfaces and the deliberately quiet `--text-atypical`, and its
  control edges are under 3:1. Those gaps were there before the light theme
  and are not changed by it — the dark theme is the approved design and stays
  as it was — so they are named below, exactly, as `DARK_GAPS`. A change that
  widens one fails; a change that closes one fails too, and asks for the entry
  to go.
"""

from __future__ import annotations

import itertools
import re
from pathlib import Path

import pytest
from django.conf import settings

CSS = Path(settings.BASE_DIR) / "static" / "css"
AA_NORMAL_TEXT = 4.5
NON_TEXT = 3.0
THEMES = ("dark", "light")


def _tokens(theme: str = "dark") -> dict[str, str]:
    """`--name: value` for one theme, resolved through every `var()`.

    The primitives and the dark block sit on `:root`; the light block overrides
    them on `:root[data-theme="light"]`. So dark is everything before the light
    block, and light is that with the light block laid over it.
    """
    text = (CSS / "tokens.css").read_text(encoding="utf-8")
    split = text.index(':root[data-theme="light"]')
    raw = dict(re.findall(r"(--[\w-]+):\s*([^;]+);", text[:split]))
    if theme == "light":
        raw.update(re.findall(r"(--[\w-]+):\s*([^;]+);", text[split:]))

    def resolve(value: str, depth: int = 0) -> str:
        match = re.fullmatch(r"var\((--[\w-]+)\)", value.strip())
        if match and depth < 10:
            return resolve(raw[match.group(1)], depth + 1)
        return value.strip()

    return {name: resolve(value) for name, value in raw.items()}


def _dark_tokens() -> dict[str, str]:
    return _tokens("dark")


def _rule(selector: str) -> str:
    text = (CSS / "app.css").read_text(encoding="utf-8")
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", text)
    assert match, f"no rule for {selector}"
    return match.group(1)


def _colour_of(selector: str, tokens: dict[str, str]) -> str:
    body = _rule(selector)
    token = re.search(r"(?<!-)color:\s*var\((--[\w-]+)\)", body)
    assert token, f"{selector} sets no colour token"
    return tokens[token.group(1)]


def _luminance(hex_colour: str) -> float:
    value = hex_colour.lstrip("#")
    channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


# ---------------------------------------------------------------------------
# ENG-101: the three rules that were moved off `--text-muted`
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(
    ("selector", "surface"),
    [
        (".segmented__option.is-active .segmented__count", "--surface-selected"),
        ('.button--primary[data-inactive="true"]', "--surface-elevated"),
        (".pw-note__meta", "--surface-elevated"),
    ],
)
def test_the_failing_combinations_now_meet_aa(selector, surface, theme):
    tokens = _tokens(theme)
    ratio = contrast(_colour_of(selector, tokens), tokens[surface])
    assert ratio >= AA_NORMAL_TEXT, f"{theme}: {selector} on {surface}: {ratio:.2f}:1"


def test_the_measured_shortfall_was_real():
    """The numbers the finding was raised on, from the dark tokens as they stand."""
    tokens = _dark_tokens()
    muted = tokens["--text-muted"]
    assert contrast(muted, tokens["--surface-selected"]) < AA_NORMAL_TEXT
    assert contrast(muted, tokens["--surface-elevated"]) < AA_NORMAL_TEXT
    # And where muted text stays muted, it passes.
    assert contrast(muted, tokens["--surface-raised"]) >= AA_NORMAL_TEXT


# ---------------------------------------------------------------------------
# Every role on every surface, in each theme
# ---------------------------------------------------------------------------

#: Everything text is ever painted on: the page surfaces, the hover, selection
#: and overlay steps, and the soft fills of the four statuses.
SURFACES = (
    "--surface-base",
    "--surface-nav",
    "--surface-raised",
    "--surface-panel",
    "--surface-elevated",
    "--surface-hover",
    "--surface-overlay-hover",
    "--surface-selected",
    "--surface-capture",
    "--surface-rail",
    "--accent-soft",
    "--status-info-soft",
    "--status-warning-soft",
    "--status-success-soft",
    "--status-danger-soft",
)

#: Every role that is text.
TEXT_ROLES = (
    "--text-primary",
    "--text-body",
    "--text-secondary",
    "--text-muted",
    "--accent-link",
    "--accent-hover",
    "--status-danger",
    "--status-warning",
    "--status-success",
    "--status-info",
)

#: The surfaces a chip's name is drawn on: the form's own surfaces, never a
#: selection — a checked chip stops being atypical and takes `--text-primary`.
CHIP_SURFACES = (
    "--surface-base",
    "--surface-raised",
    "--surface-panel",
    "--surface-elevated",
    "--surface-capture",
)

#: Where a field, a focus ring or a selected option's accent edge is drawn:
#: on the page and on the cards and bands that sit on it.
CONTROL_SURFACES = (
    "--surface-base",
    "--surface-nav",
    "--surface-raised",
    "--surface-panel",
    "--surface-elevated",
    "--surface-capture",
)

#: The dark theme's measured gaps, as of the light theme's arrival
#: (docs/adr/0147 §8). Not introduced by it, and not changed by it: the dark
#: theme is the approved appearance. Each closes the day the dark theme is
#: revisited, and this table is where that is noticed.
DARK_GAPS: dict[tuple[str, str], float] = {
    # ENG-101's own pair. The rules it named moved off muted; the token did
    # not, and nine popover labels still sit on `--surface-elevated` at 4.49.
    ("--text-muted", "--surface-elevated"): 4.49,
    ("--text-muted", "--surface-overlay-hover"): 4.11,
    ("--text-muted", "--surface-selected"): 4.29,
    ("--text-muted", "--accent-soft"): 4.29,
    ("--text-muted", "--status-success-soft"): 4.43,
}


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(("role", "surface"), list(itertools.product(TEXT_ROLES, SURFACES)))
def test_every_text_role_meets_aa_on_every_surface(theme, role, surface):
    tokens = _tokens(theme)
    ratio = contrast(tokens[role], tokens[surface])
    known = DARK_GAPS.get((role, surface)) if theme == "dark" else None
    if known is not None:
        assert ratio < AA_NORMAL_TEXT, (
            f"dark: {role} on {surface} now meets AA ({ratio:.2f}:1) — remove it from DARK_GAPS"
        )
        assert round(ratio, 2) >= known, f"dark: {role} on {surface} got worse: {ratio:.2f}:1"
        return
    assert ratio >= AA_NORMAL_TEXT, f"{theme}: {role} on {surface}: {ratio:.2f}:1"


@pytest.mark.parametrize(
    ("foreground", "fill"),
    [
        # A primary button, a link-coloured badge, the test badge.
        ("--text-inverse", "--accent-primary"),
        ("--text-inverse", "--accent-hover"),
        ("--text-inverse", "--accent-link"),
        ("--text-inverse", "--status-warning"),
        # The one irreversible button (`.button--danger-strong`).
        ("--surface-base", "--status-danger"),
        # Selected text (`::selection`, static/css/base.css).
        ("--text-primary", "--accent-border"),
        # A document preview's own paper.
        ("--text-on-document", "--surface-document"),
    ],
)
@pytest.mark.parametrize("theme", THEMES)
def test_text_on_a_coloured_fill_meets_aa(theme, foreground, fill):
    tokens = _tokens(theme)
    ratio = contrast(tokens[foreground], tokens[fill])
    assert ratio >= AA_NORMAL_TEXT, f"{theme}: {foreground} on {fill}: {ratio:.2f}:1"


@pytest.mark.parametrize("surface", CHIP_SURFACES)
def test_the_quieter_chip_is_still_readable_in_light(surface):
    """`--text-atypical` names an option the chosen Õigusakt rarely takes.

    Quieter than `--text-muted` by design (docs/adr/0130), and in the dark
    theme quieter than AA too — 3.6:1 on the page, `DARK_ATYPICAL` below. The
    light palette has the room to keep it both quieter than muted and AA.
    """
    tokens = _tokens("light")
    ratio = contrast(tokens["--text-atypical"], tokens[surface])
    assert ratio >= AA_NORMAL_TEXT, f"light: --text-atypical on {surface}: {ratio:.2f}:1"
    assert ratio < contrast(tokens["--text-muted"], tokens[surface])


#: Measured on the dark page (`--surface-base`); see the note above.
DARK_ATYPICAL = 3.61


def test_the_quieter_chip_in_dark_is_as_it_was():
    tokens = _tokens("dark")
    assert round(contrast(tokens["--text-atypical"], tokens["--surface-base"]), 2) == DARK_ATYPICAL


# ---------------------------------------------------------------------------
# Boundaries a person has to find (WCAG 1.4.11)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("role", ["--focus-ring", "--accent-primary"])
@pytest.mark.parametrize("surface", SURFACES)
def test_focus_and_the_selected_marker_stand_out_everywhere(theme, role, surface):
    """The focus ring, and the brand edge that marks the active tab and row."""
    tokens = _tokens(theme)
    ratio = contrast(tokens[role], tokens[surface])
    assert ratio >= NON_TEXT, f"{theme}: {role} on {surface}: {ratio:.2f}:1"


@pytest.mark.parametrize(
    "role",
    [
        # The edge of every text field (docs/adr/0147 §6).
        "--border-input",
        # A selected chip's and option's edge, and the prominent title field's.
        "--accent-border",
        # A refused field's edge.
        "--status-danger-border",
    ],
)
@pytest.mark.parametrize("surface", CONTROL_SURFACES)
def test_a_field_and_its_states_can_be_found_by_their_edge_in_light(role, surface):
    tokens = _tokens("light")
    ratio = contrast(tokens[role], tokens[surface])
    assert ratio >= NON_TEXT, f"light: {role} on {surface}: {ratio:.2f}:1"


def test_the_dark_field_edge_is_the_control_edge_it_always_was():
    """Under 3:1, and unchanged: the dark theme draws what it drew before.

    A field in the dark theme is told from the page by its fill as much as its
    edge, and the edge has measured 1.43:1 on the page since the CVI palette
    arrived. Recorded rather than fixed here, with the other dark gaps.
    """
    tokens = _tokens("dark")
    assert tokens["--border-input"] == tokens["--border-control"]
    assert round(contrast(tokens["--border-input"], tokens["--surface-base"]), 2) == 1.43
