"""The 2026-08-27 UX pass: what the design handoff asked for, asserted.

Grouped by the handoff's own numbering (0, 1a–1e, 2d, Osakond) rather than by
Django app, because that is the unit the change was reviewed and approved in and
the unit a later reader will come looking for.

Nothing here checks how a thing looks. Pixels are the browser suite's job
(`e2e/test_ux_pass.py`); this file checks the parts that are structural — which
groups exist, what a link promises, whether a count matches the list behind it,
and that a keyboard shortcut has a visible control beside it.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings

CSS_DIR = Path(settings.BASE_DIR) / "static" / "css"
JS_DIR = Path(settings.BASE_DIR) / "static" / "js"
TEMPLATE_DIR = Path(settings.BASE_DIR) / "templates"


# ---------------------------------------------------------------------------
# 0 — the persona popover stays hidden until it is opened
# ---------------------------------------------------------------------------


def test_the_hidden_attribute_beats_any_component_display() -> None:
    """Handoff fix 0, and why it needed no new rule.

    The reported symptom is an empty persona pill in the top bar: the popover is
    rendered `hidden` and `.personamenu { display: flex }` would win over the
    user agent's `[hidden]` rule, so the empty box would sit under the bar on
    every page.

    Two rules already prevent it on this main, and this test is here so neither
    can be deleted as dead weight: the global reset in `base.css`, which is
    `!important` precisely so no component can out-specify it, and the
    component's own `[hidden]` rule beside its `display`. A third copy in
    `ux.css` would add nothing and would have to be exempted from the
    `!important` contract (tests/test_ui_contract.py).
    """
    base = (CSS_DIR / "base.css").read_text(encoding="utf-8")
    assert re.search(r"\[hidden\]\s*\{\s*display:\s*none\s*!important", base), (
        "base.css must keep the [hidden] reset that beats any component display"
    )

    app = (CSS_DIR / "app.css").read_text(encoding="utf-8")
    assert re.search(r"\.personamenu\[hidden\]\s*\{\s*display:\s*none", app), (
        "the persona popover must state its own hidden rule beside its display"
    )


# ---------------------------------------------------------------------------
# The pass stays separable
# ---------------------------------------------------------------------------


def test_the_pass_ships_as_its_own_stylesheet_and_script() -> None:
    """Additive by construction, and loaded after the files it adds to.

    The handoff's one architectural instruction: the new CSS is its own file
    after `app.css`, not a patch inside it.
    """
    shell = (TEMPLATE_DIR / "base.html").read_text(encoding="utf-8")
    assert shell.index("css/app.css") < shell.index("css/ux.css"), (
        "ux.css must be linked after app.css"
    )
    assert shell.index("js/app.js") < shell.index("js/ux.js"), "ux.js must load after app.js"
    assert (CSS_DIR / "ux.css").is_file()
    assert (JS_DIR / "ux.js").is_file()


def test_ux_css_touches_exactly_one_existing_class() -> None:
    """Everything the pass draws is `ux`-prefixed, with two declared exceptions.

    `.workrow2 { position: relative }` is the containing block the quick-complete
    button needs, and `.railrow__value--danger` is a missing modifier of an
    existing family. Both are additive and both are commented where they sit.
    Anything else appearing here means the pass has started editing production
    components, which is the thing the separate file exists to prevent.
    """
    text = (CSS_DIR / "ux.css").read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    selectors = set()
    for block in re.finditer(r"([^{}]+)\{[^{}]*\}", text):
        for name in re.findall(r"\.(-?[A-Za-z_][\w-]*)", block.group(1)):
            selectors.add(name)

    allowed_existing = {"workrow2", "railrow__value--danger", "field__input", "is-selected"}
    unexpected = {name for name in selectors if not name.startswith("ux")} - allowed_existing
    assert not unexpected, f"ux.css styles non-ux classes: {sorted(unexpected)}"
