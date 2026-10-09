"""The light/dark theme's contract, held without a database or a browser.

Dark is the default and light is a fully supported alternative, chosen per
browser with the switch on the bar (docs/adr/0147). Most of what makes that true
is a property of three files — the token layer, the early script and the base
template — and each property below is one that would fail quietly in a browser:
a role the light block forgot falls through to its dark value, a script loaded
a moment too late paints one dark frame, and a light-only override written for
one component is the first of the hundred the token layer exists to prevent.

What a page does with all of it is in e2e/test_theme.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings

ROOT = Path(settings.BASE_DIR)
CSS = ROOT / "static" / "css"
TOKENS = (CSS / "tokens.css").read_text(encoding="utf-8")
SCRIPT = (ROOT / "static" / "js" / "theme.js").read_text(encoding="utf-8")
TOGGLE = (ROOT / "templates" / "components" / "theme_toggle.html").read_text(encoding="utf-8")

#: The two documents that open an HTML page. Every other template extends one
#: of them — the sign-in, the door and the persona pages extend `base.html`,
#: the 404, 500 and CSRF pages extend `error_page.html`.
DOCUMENT_ROOTS = ("base.html", "error_page.html")

COMMENT = re.compile(r"/\*.*?\*/", re.S)
TEMPLATE_COMMENT = re.compile(r"{#.*?#}|{%\s*comment\s*%}.*?{%\s*endcomment\s*%}", re.S)


def _block(selector: str) -> str:
    """The declarations of one top-level block of tokens.css, comments removed."""
    text = COMMENT.sub("", TOKENS)
    start = text.index(selector + " {")
    return text[start : text.index("}", start)]


def _names(block: str) -> set[str]:
    return set(re.findall(r"(--[\w-]+)\s*:", block))


#: Declared in the dark block for history's sake, but geometry rather than
#: colour: a focus ring is as thick in either theme, so the light block
#: inherits them from `:root` on purpose.
SHARED = {"--focus-ring-width", "--focus-ring-offset-width"}

DARK = _names(_block(':root,\n:root[data-theme="dark"]')) - SHARED
LIGHT = _names(_block(':root[data-theme="light"]'))


# ---------------------------------------------------------------------------
# The token layer
# ---------------------------------------------------------------------------


def test_the_light_theme_defines_every_role_the_dark_theme_does():
    """A role the light block leaves out is not an error anywhere.

    `:root` carries the dark values, so a light page reading a role the light
    block never redefined simply gets the dark one — a near-black fill on a
    white page, rendered without complaint. The one way to find it is here.
    """
    assert DARK - LIGHT == set(), f"dark-only roles: {sorted(DARK - LIGHT)}"
    assert LIGHT - DARK == set(), f"light-only roles: {sorted(LIGHT - DARK)}"


def test_both_themes_tell_the_browser_which_one_they_are():
    """`color-scheme` is what native controls, scrollbars and pickers follow."""
    assert "color-scheme: dark;" in _block(':root,\n:root[data-theme="dark"]')
    assert "color-scheme: light;" in _block(':root[data-theme="light"]')


def test_the_light_theme_reads_no_dark_primitive():
    """Light values are its own, or the brand-neutral white, never a dark step.

    The primitives are the graphite ramp the dark theme is built from. A light
    role pointed at one would be a dark surface or a dark-theme ink on a light
    page — which is exactly the defect `.uxav` carried until this round.
    """
    allowed = {"--primitive-neutral-0", "--primitive-neutral-900"}
    read = set(re.findall(r"var\((--primitive-[\w-]+)\)", _block(':root[data-theme="light"]')))
    assert read <= allowed, f"light roles read dark primitives: {sorted(read - allowed)}"


@pytest.mark.parametrize("name", ["app.css", "ux.css", "base.css"])
def test_components_never_read_a_primitive(name):
    """Components consume roles (docs/adr/0009); the theme decides the values.

    It was a rule in prose only, and two rules had broken it — harmlessly, while
    there was one theme. With two, a primitive read in a component is a colour
    the switch cannot reach.
    """
    text = COMMENT.sub("", (CSS / name).read_text(encoding="utf-8"))
    assert not re.findall(r"var\(--primitive-[\w-]+\)", text)


#: The only component rules allowed to read the theme attribute: the emblem's
#: filter, which is artwork rather than colour, and the switch's own icon and
#: name. Everything else that differs between the themes is a token.
THEME_SELECTORS = {
    ':root[data-theme="dark"] .topbar__logo',
    ':root[data-theme="light"] .themetoggle__icon--sun',
    ':root[data-theme="light"] .themetoggle__name--light',
    ':root:not([data-theme="light"]) .themetoggle__icon--moon',
    ':root:not([data-theme="light"]) .themetoggle__name--dark',
}


@pytest.mark.parametrize("name", ["app.css", "ux.css", "base.css"])
def test_no_component_carries_a_light_only_override(name):
    """A light theme is a token swap, not a second stylesheet.

    `[data-theme="light"] .component { … }` fixes one component and teaches the
    next author to fix theirs the same way, until the light theme is a parallel
    design maintained by hand. The way a component supports both themes is to
    read a role that is right in both; when no role is, the fix is a role
    (docs/adr/0147 §6).
    """
    text = COMMENT.sub("", (CSS / name).read_text(encoding="utf-8"))
    for selectors in re.findall(r"([^{}]+)\{", text):
        for selector in selectors.split(","):
            selector = " ".join(selector.split())
            # `data-theme-switchable` says the script ran, not which theme.
            if re.search(r"data-theme(?!-)", selector):
                assert selector in THEME_SELECTORS, f"{name}: {selector}"


def test_a_text_field_has_its_own_edge_role():
    """`--border-input`, and its two states, are field roles in both themes.

    A field is found by its edge, 3:1 in each theme (WCAG 1.4.11,
    tests/test_text_contrast.py), while badges, tags and menus keep the
    quieter `--border-control` — which is why the field edge is its own role.
    The dark theme set it to `--border-control` until docs/adr/0147's
    amendment of 2026-10-09.
    """
    for block in (':root,\n:root[data-theme="dark"]', ':root[data-theme="light"]'):
        assert "--border-input: var(--border-control);" not in _block(block), block
    fields = (".field__input", ".searchfield__input", ".teema .composer__body", ".railnote__area")
    text = COMMENT.sub("", (CSS / "app.css").read_text(encoding="utf-8"))
    for selector in fields:
        body = re.search(r"\n" + re.escape(selector) + r" \{([^}]*)\}", text)
        assert body, selector
        assert "var(--border-input)" in body.group(1), selector
    # The prominent title field and a refused field: brand and danger, drawn at
    # least as strongly as a resting field rather than as a chip's or badge's.
    states = {
        ".createform .field__input--prominent": "var(--border-input-prominent)",
        ".createform .field:has(.field__error) .field__input": "var(--border-input-refused)",
    }
    for selector, role in states.items():
        body = re.search(r"\n" + re.escape(selector) + r"\s*\{([^}]*)\}", text)
        assert body, selector
        assert role in body.group(1), selector


# ---------------------------------------------------------------------------
# The early script
# ---------------------------------------------------------------------------


def _head(template: str) -> str:
    text = TEMPLATE_COMMENT.sub("", (ROOT / "templates" / template).read_text(encoding="utf-8"))
    return text[text.index("<head>") : text.index("</head>")]


@pytest.mark.parametrize("template", DOCUMENT_ROOTS)
def test_the_theme_is_applied_before_anything_can_be_painted(template):
    """In <head>, before every stylesheet, and neither deferred nor async.

    A deferred script runs after the document is parsed, which is after the
    browser is allowed to paint it: one dark frame for somebody who chose
    light. A script ahead of the stylesheets does not wait for them, so it
    costs the first paint nothing.
    """
    head = _head(template)
    tag = re.search(r"<script[^>]*js/theme\.js[^>]*></script>", head)
    assert tag, f"{template}: no theme script in <head>"
    assert "defer" not in tag.group(0) and "async" not in tag.group(0), tag.group(0)
    first_stylesheet = head.index('rel="stylesheet"')
    assert tag.start() < first_stylesheet, f"{template}: the theme script follows a stylesheet"


@pytest.mark.parametrize("template", DOCUMENT_ROOTS)
def test_the_server_always_renders_the_dark_default(template):
    """Dark unless the browser says otherwise; the server never decides."""
    text = (ROOT / "templates" / template).read_text(encoding="utf-8")
    assert '<html lang="et" data-theme="dark">' in text


def test_the_script_keeps_one_key_with_one_of_two_values():
    """`juristid-theme`, `dark` or `light`, and nothing else in storage.

    The one thing this application keeps in the browser beside what htmx is
    told not to (ENG-009). A second key — a timestamp, a user id — would be the
    preference turning into a record of somebody.
    """
    code = COMMENT.sub("", SCRIPT)
    assert 'var KEY = "juristid-theme";' in code
    assert re.findall(r"localStorage\.(\w+)\(", code) == ["getItem", "setItem"]
    assert re.findall(r"localStorage\.setItem\((\w+),", code) == ["KEY"]
    assert 'value === "dark" || value === "light"' in code
    # Every touch of storage is guarded: a blocked store must not stop the page.
    assert code.count("try {") == 2


def test_the_script_never_talks_to_the_server():
    """A theme is not business data: nothing is sent anywhere when it changes."""
    code = COMMENT.sub("", SCRIPT)
    for call in ("fetch(", "XMLHttpRequest", "sendBeacon", "htmx.", "document.cookie", ".submit("):
        assert call not in code, call


# ---------------------------------------------------------------------------
# The switch
# ---------------------------------------------------------------------------


def test_the_switch_is_a_button_that_submits_nothing():
    markup = TEMPLATE_COMMENT.sub("", TOGGLE)
    button = re.search(r"<button[^>]*>", markup).group(0)
    assert 'type="button"' in button
    assert "data-theme-toggle" in button


def test_the_switch_names_the_action_in_estonian_for_each_state():
    """The name is what pressing does, and there is one per state."""
    markup = TEMPLATE_COMMENT.sub("", TOGGLE)
    assert (
        '<span class="visually-hidden themetoggle__name--light">Lülita heledale teemale</span>'
        in markup
    )
    assert (
        '<span class="visually-hidden themetoggle__name--dark">Lülita tumedale teemale</span>'
        in markup
    )
    # No `aria-pressed` beside a name that already changes with the state.
    assert "aria-pressed" not in markup
    assert "aria-label" not in markup


def test_the_icons_are_decoration_and_carry_no_colour_of_their_own():
    markup = TEMPLATE_COMMENT.sub("", TOGGLE)
    icons = re.findall(r"<svg[^>]*>", markup)
    assert len(icons) == 2
    for icon in icons:
        assert 'aria-hidden="true"' in icon and 'focusable="false"' in icon
    assert "fill=" not in markup and "stroke=" not in markup


def test_the_switch_is_on_the_bar_signed_in_and_signed_out():
    """Beside the account controls, and on the sign-in page.

    Signing out clears the browser's storage and the preference with it
    (`Clear-Site-Data`, ENG-009), so the page somebody lands on after signing
    out is where they meet the default again — and where the switch has to be.
    """
    base = TEMPLATE_COMMENT.sub("", (ROOT / "templates" / "base.html").read_text(encoding="utf-8"))
    assert base.count('{% include "components/theme_toggle.html" %}') == 2
    signed_in, signed_out = base.split('{% else %}\n          <span class="topbar__spacer">')
    assert signed_in.index("theme_toggle.html") < signed_in.index("persona_switcher.html")
    assert "theme_toggle.html" in signed_out.split("</header>")[0]
