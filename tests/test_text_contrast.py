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
the dark one, exactly as the cascade does it — and held to the same bar: AA for
every text role on every surface, and 3:1 for the boundaries a person has to
find, a text field's edge in each of its states among them.

The dark theme reached that bar on 2026-10-09 (docs/adr/0147, amendment). Until
then it carried three gaps the light round recorded rather than redesigned —
`--text-muted` on five raised or tinted surfaces, the quieter `--text-atypical`
at 3.61:1, and a field edge at 1.43:1. `DARK_GAPS` is where such a gap is named,
exactly, if one ever has to be recorded again: a change that widens one fails,
and so does a change that closes one without removing its entry.
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
    """The numbers the finding was raised on, and where the role is now.

    ENG-101 measured `--primitive-neutral-300`, which was `--text-muted` until
    docs/adr/0147's amendment of 2026-10-09 lifted the role a step.
    """
    tokens = _dark_tokens()
    measured = tokens["--primitive-neutral-300"]
    assert contrast(measured, tokens["--surface-selected"]) < AA_NORMAL_TEXT
    assert contrast(measured, tokens["--surface-elevated"]) < AA_NORMAL_TEXT
    # And where muted text stays muted, it passes.
    assert contrast(measured, tokens["--surface-raised"]) >= AA_NORMAL_TEXT
    # The role itself meets AA on both surfaces now.
    for surface in ("--surface-selected", "--surface-elevated"):
        assert contrast(tokens["--text-muted"], tokens[surface]) >= AA_NORMAL_TEXT


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

#: The dark theme's measured gaps. The light round recorded five
#: (docs/adr/0147 §8) — `--text-muted` on `--surface-elevated` 4.49, on
#: `--surface-overlay-hover` 4.11, on `--surface-selected` and `--accent-soft`
#: 4.29, on `--status-success-soft` 4.43 — and the owner closed them on
#: 2026-10-09 by lifting the role rather than moving its rules (§8's
#: amendment). Empty since; a gap that ever has to be recorded again is named
#: here exactly, so that it can close but not widen.
DARK_GAPS: dict[tuple[str, str], float] = {}


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


#: Where the quieter chip is held to AA. In light, every surface a chip form can
#: sit on. In dark, the page — the one surface `Uus teema` draws it on, and
#: e2e/test_theme_contrast.py measures it there in both themes. A dark chip
#: quieter than muted *and* AA on a popover would have to be as bright on the
#: page as muted was before 2026-10-09: the step the owner found too close to
#: an ordinary chip (docs/adr/0130, amendment of 2026-10-02).
ATYPICAL_SURFACES = {"light": CHIP_SURFACES, "dark": ("--surface-base",)}

#: How much quieter than muted the chip stays, as a ratio of the two
#: contrasts: a step a glance can tell rather than a rounding difference. Both
#: palettes keep about a quarter (1.23 light, 1.29 dark).
QUIETER_BY = 1.2


@pytest.mark.parametrize(
    ("theme", "surface"), [(t, s) for t in THEMES for s in ATYPICAL_SURFACES[t]]
)
def test_the_quieter_chip_is_readable_and_still_quieter(theme, surface):
    """`--text-atypical` names an option the chosen Õigusakt rarely takes.

    Quieter than `--text-muted` by design (docs/adr/0130), and AA where it is
    drawn. The dark value was 3.61:1 until docs/adr/0147's amendment of
    2026-10-09.
    """
    tokens = _tokens(theme)
    ratio = contrast(tokens["--text-atypical"], tokens[surface])
    assert ratio >= AA_NORMAL_TEXT, f"{theme}: --text-atypical on {surface}: {ratio:.2f}:1"
    muted = contrast(tokens["--text-muted"], tokens[surface])
    assert muted / ratio >= QUIETER_BY, f"{theme}: on {surface}, muted {muted:.2f} vs {ratio:.2f}"


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


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(
    "role",
    [
        # The edge of every text field at rest (docs/adr/0147 §6).
        "--border-input",
        # The prominent title field's (`Uus teema`).
        "--border-input-prominent",
        # A refused field's.
        "--border-input-refused",
    ],
)
@pytest.mark.parametrize("surface", CONTROL_SURFACES)
def test_a_field_and_its_states_can_be_found_by_their_edge(theme, role, surface):
    """A dark field's fill is the page's own, so it is found by its edge.

    The dark edges were 1.43:1 at rest, 2.11:1 prominent and 1.82:1 refused on
    the page until docs/adr/0147's amendment of 2026-10-09.
    """
    tokens = _tokens(theme)
    ratio = contrast(tokens[role], tokens[surface])
    assert ratio >= NON_TEXT, f"{theme}: {role} on {surface}: {ratio:.2f}:1"


@pytest.mark.parametrize("theme", THEMES)
def test_the_rail_note_can_be_found_by_its_edge(theme):
    """The facts rail's private note (`.railnote__area`) is a field too."""
    tokens = _tokens(theme)
    ratio = contrast(tokens["--border-input"], tokens["--surface-rail"])
    assert ratio >= NON_TEXT, f"{theme}: --border-input on --surface-rail: {ratio:.2f}:1"


@pytest.mark.parametrize("surface", CONTROL_SURFACES)
def test_a_selected_option_can_be_found_by_its_edge_in_light(surface):
    """A selected chip's and option's accent edge.

    In dark a selected chip is told by its brand fill, its weight and its ink;
    its edge stays the quiet brand rule chips, tabs and badges share
    (docs/adr/0147, amendment of 2026-10-09, what it does not change).
    """
    tokens = _tokens("light")
    ratio = contrast(tokens["--accent-border"], tokens[surface])
    assert ratio >= NON_TEXT, f"light: --accent-border on {surface}: {ratio:.2f}:1"
