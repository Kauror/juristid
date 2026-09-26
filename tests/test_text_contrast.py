"""Small secondary text meets WCAG AA on the surfaces it actually sits on (ENG-101).

`--text-muted` (#7d8b99) is 4.29:1 on `--surface-selected` and 4.49:1 on
`--surface-elevated` — under the 4.5:1 AA asks of normal-size text. Three rules
put it there: the selected segmented option's count, the inactive-looking
«Loo teema» (which still submits), and the Minu asjad pad's meta line. Each now
uses `--text-secondary`. These tests read the colours out of the stylesheets and
recompute the ratios, so a later token or rule change that drops one below AA
fails here rather than in a screen reader user's afternoon.

Only the dark palette is reachable (`templates/base.html` hard-codes
`data-theme="dark"`), so only its values are resolved.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings

CSS = Path(settings.BASE_DIR) / "static" / "css"
AA_NORMAL_TEXT = 4.5


def _dark_tokens() -> dict[str, str]:
    """`--name: value` from the dark `:root` block of tokens.css, resolved."""
    text = (CSS / "tokens.css").read_text(encoding="utf-8")
    # The primitives, then the dark semantic block; everything after the light
    # override is a palette no page can reach.
    dark = text[: text.index(':root[data-theme="light"]')]
    raw = dict(re.findall(r"(--[\w-]+):\s*([^;]+);", dark))

    def resolve(value: str, depth: int = 0) -> str:
        match = re.fullmatch(r"var\((--[\w-]+)\)", value.strip())
        if match and depth < 10:
            return resolve(raw[match.group(1)], depth + 1)
        return value.strip()

    return {name: resolve(value) for name, value in raw.items()}


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


@pytest.mark.parametrize(
    ("selector", "surface"),
    [
        (".segmented__option.is-active .segmented__count", "--surface-selected"),
        ('.button--primary[data-inactive="true"]', "--surface-elevated"),
        (".pw-note__meta", "--surface-elevated"),
    ],
)
def test_the_failing_combinations_now_meet_aa(selector, surface):
    tokens = _dark_tokens()
    ratio = contrast(_colour_of(selector, tokens), tokens[surface])
    assert ratio >= AA_NORMAL_TEXT, f"{selector} on {surface}: {ratio:.2f}:1"


def test_the_measured_shortfall_was_real():
    """The numbers the finding was raised on, from the tokens as they stand."""
    tokens = _dark_tokens()
    muted = tokens["--text-muted"]
    assert contrast(muted, tokens["--surface-selected"]) < AA_NORMAL_TEXT
    assert contrast(muted, tokens["--surface-elevated"]) < AA_NORMAL_TEXT
    # And where muted text stays muted, it passes.
    assert contrast(muted, tokens["--surface-raised"]) >= AA_NORMAL_TEXT
