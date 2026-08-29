"""Contract tests for the stylesheet and the templates that consume it.

These need no database and no browser. They exist because every defect they
check for shipped silently at least once, and none of them is visible in a test
that asserts a page returned 200:

* a rule left unclosed swallowed the 238 lines after it, so a whole section of
  the component library was parsed as one invalid declaration and dropped;
* classes were written in templates that no stylesheet ever defined, so the
  element fell back to whatever its tag happened to do;
* a custom property was read with a hard-coded fallback because nothing
  defined it;
* a rule that should have been scoped to a group was written unscoped, landed
  after the modifier it collided with, and silently won.

A stylesheet cannot be unit-tested for beauty. It can be tested for the things
that make it stop being a stylesheet.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pytest
from django.conf import settings

CSS_DIR = Path(settings.BASE_DIR) / "static" / "css"
JS_DIR = Path(settings.BASE_DIR) / "static" / "js"
TEMPLATE_DIR = Path(settings.BASE_DIR) / "templates"
APP_DIR = Path(settings.BASE_DIR) / "app"

CSS_FILES = sorted(CSS_DIR.glob("*.css"))
TEMPLATES = sorted(TEMPLATE_DIR.rglob("*.html"))

COMMENT = re.compile(r"/\*.*?\*/", re.S)


def strip_comments(text: str) -> str:
    """Blank out comments while keeping line numbers intact."""
    return COMMENT.sub(lambda m: "\n" * m.group().count("\n"), text)


def all_css() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in CSS_FILES)


# ---------------------------------------------------------------------------
# The stylesheet parses as the author intended
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", CSS_FILES, ids=lambda p: p.name)
def test_every_rule_is_closed(path: Path) -> None:
    """Braces balance, and no rule opens inside a declaration block.

    A missing `}` is not a syntax error to a browser: it consumes every rule
    after it as an invalid declaration and drops them. The file still loads,
    the page still renders, and a seventh of the design is simply absent.
    """
    text = strip_comments(path.read_text(encoding="utf-8"))
    depth = 0
    line = 1
    for character in text:
        if character == "\n":
            line += 1
        elif character == "{":
            depth += 1
            assert depth <= 2, f"{path.name}:{line}: rule opened inside a declaration block"
        elif character == "}":
            depth -= 1
            assert depth >= 0, f"{path.name}:{line}: unmatched closing brace"
    assert depth == 0, (
        f"{path.name}: {depth} unclosed rule(s) — everything after the last one is dead"
    )


@pytest.mark.parametrize("path", CSS_FILES, ids=lambda p: p.name)
def test_no_selector_is_defined_twice_in_a_file(path: Path) -> None:
    """One component, one definition.

    Two blocks for the same selector are a merge accident, and the second one
    wins wherever they overlap — which is how a rule meant for one group of
    rows ended up neutralising the overdue colour on all of them.
    """
    text = strip_comments(path.read_text(encoding="utf-8"))
    counts: dict[str, int] = defaultdict(int)
    depth = 0
    buffer = ""
    for character in text:
        if character == "{":
            if depth == 0:
                selector = " ".join(buffer.split())
                if selector and not selector.startswith("@"):
                    counts[selector] += 1
                buffer = ""
            depth += 1
        elif character == "}":
            depth -= 1
            buffer = ""
        elif depth == 0:
            buffer += character

    repeated = sorted(selector for selector, count in counts.items() if count > 1)
    assert not repeated, f"{path.name}: defined more than once: {repeated}"


# ---------------------------------------------------------------------------
# The token layer is the only place raw values live
# ---------------------------------------------------------------------------


def test_every_custom_property_read_is_also_defined() -> None:
    """No `var(--x)` resolves to nothing, and none needs a hard-coded fallback.

    A `var()` that resolves to nothing makes the whole declaration invalid, so
    the component loses a property rather than reporting anything. Writing
    `var(--spacing-45, 18px)` hides that: the fallback is a raw value outside
    the token layer, wearing a token's name.
    """
    css = all_css()
    defined = set(re.findall(r"(--[\w-]+)\s*:", css))
    read = set(re.findall(r"var\(\s*(--[\w-]+)", css))
    for template in TEMPLATES:
        read |= set(re.findall(r"var\(\s*(--[\w-]+)", template.read_text(encoding="utf-8")))

    missing = sorted(read - defined)
    assert not missing, f"read but never defined: {missing}"

    with_fallback = sorted(set(re.findall(r"var\(\s*(--[\w-]+)\s*,", css)))
    assert not with_fallback, f"token read with a fallback value: {with_fallback}"


def test_raw_colours_live_only_in_the_token_layer() -> None:
    """Components consume roles, not values (docs/adr/0009).

    A hex in `app.css` is a CVI correction nobody can make and a light theme
    nobody can add.
    """
    offenders = []
    for path in CSS_FILES:
        if path.name == "tokens.css":
            continue
        for number, line in enumerate(
            strip_comments(path.read_text(encoding="utf-8")).splitlines(), 1
        ):
            if re.search(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(", line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, "raw colour outside tokens.css:\n" + "\n".join(offenders)


def test_important_is_reserved_for_the_two_places_that_need_it() -> None:
    """`!important` hides a structural problem rather than fixing one.

    Two uses are legitimate and both are stated: the reduced-motion override,
    which must beat any component transition, and the `[hidden]` reset, which
    must beat any component that sets `display`.
    """
    allowed = {
        ("app.css", "transition-duration"),
        ("app.css", "animation-duration"),
        ("base.css", "display"),
    }
    found = set()
    for path in CSS_FILES:
        for line in strip_comments(path.read_text(encoding="utf-8")).splitlines():
            if "!important" in line:
                found.add((path.name, line.split(":")[0].strip()))
    assert found <= allowed, f"unexpected !important: {sorted(found - allowed)}"


# ---------------------------------------------------------------------------
# Templates and the stylesheet agree about what exists
# ---------------------------------------------------------------------------


def template_classes() -> dict[str, set[str]]:
    """Every class literal in every template, mapped to the files using it.

    Names produced by interpolation (`mode--{{ kind|lower }}`) are skipped:
    the prefix on its own is not a class anybody wrote.
    """
    used: dict[str, set[str]] = defaultdict(set)
    for template in TEMPLATES:
        text = template.read_text(encoding="utf-8")
        for attribute in re.findall(r'class="([^"]*)"', text):
            cleaned = re.sub(r"\{%.*?%\}|\{\{.*?\}\}", " ", attribute)
            for name in cleaned.split():
                if name.endswith("--"):
                    continue
                used[name].add(str(template.relative_to(TEMPLATE_DIR)))
    return used


def test_every_class_a_template_uses_has_a_rule() -> None:
    """A class with no rule is a component that was designed and never built."""
    defined = set(re.findall(r"\.(-?[A-Za-z_][\w-]*)", all_css()))
    undefined = {
        name: sorted(files) for name, files in template_classes().items() if name not in defined
    }
    assert not undefined, "template classes with no CSS rule: " + repr(undefined)


def test_component_styling_does_not_live_in_style_attributes() -> None:
    """Layout belongs to the stylesheet; only data may be inline.

    A geometry that comes from a number in the database — a bar's width, a
    swatch's token — has nowhere else to live. Padding does.
    """
    offenders = []
    for template in TEMPLATES:
        for number, line in enumerate(template.read_text(encoding="utf-8").splitlines(), 1):
            match = re.search(r'\sstyle="([^"]*)"', line)
            if match and "{{" not in match.group(1):
                offenders.append(f"{template.relative_to(TEMPLATE_DIR)}:{number}: {match.group(1)}")
    assert not offenders, "static styling in a style attribute:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# The assets the shell asks for are actually there
# ---------------------------------------------------------------------------


def test_base_template_only_links_assets_that_exist() -> None:
    """Every `{% static %}` path in the shell resolves to a file in the repo.

    A missing stylesheet in production is an unstyled application, and the
    request that reveals it is the first one after a deploy.
    """
    base = (TEMPLATE_DIR / "base.html").read_text(encoding="utf-8")
    referenced = re.findall(r"\{%\s*static\s+'([^']+)'\s*%\}", base)
    assert referenced, "the shell links no static assets at all"
    for reference in referenced:
        assert (Path(settings.BASE_DIR) / "static" / reference).is_file(), f"missing: {reference}"


def test_every_self_hosted_font_file_exists() -> None:
    """The approved fallback stack ships with the application.

    Nothing is fetched from a font CDN at runtime, so a missing file is a
    silent substitution to Arial rather than a failed request somebody notices.
    """
    fonts = (CSS_DIR / "fonts.css").read_text(encoding="utf-8")
    for reference in re.findall(r"url\(['\"]?([^'\")]+)", fonts):
        name = reference.rsplit("/", 1)[-1]
        assert (Path(settings.BASE_DIR) / "static" / "fonts" / name).is_file(), f"missing: {name}"


def test_no_remote_asset_is_fetched_at_runtime() -> None:
    """Nothing in the UI reaches the network for a stylesheet, script or font.

    This application runs behind a corporate proxy and, in the pilot, on a
    machine that may have no route out at all.
    """
    for path in [*CSS_FILES, *sorted(JS_DIR.glob("*.js")), *TEMPLATES]:
        text = path.read_text(encoding="utf-8")
        assert "fonts.googleapis.com" not in text, f"{path.name} fetches a remote font"
        assert "cdn." not in text, f"{path.name} references a CDN"


# ---------------------------------------------------------------------------
# Accessibility properties that are structural rather than visual
# ---------------------------------------------------------------------------


def test_focus_is_never_removed() -> None:
    """`outline: none` is only acceptable where something else shows focus.

    Two selectors are allowed, and both are the same shape: a borderless
    textarea inside a card whose `:focus-within` gives the card a brand border
    and a halo — so focus is visible, on the surface a person is looking at.
    Anything else removing an outline is removing focus.
    """
    allowed = {".composer__body:focus", ".pw-note textarea:focus"}
    offenders = set()
    for path in CSS_FILES:
        text = strip_comments(path.read_text(encoding="utf-8"))
        for block in re.finditer(r"([^{}]+)\{([^{}]*)\}", text):
            if re.search(r"outline\s*:\s*(none|0)\b", block.group(2)):
                offenders.add(" ".join(block.group(1).split()))
    assert offenders <= allowed, (
        f"focus removed without a replacement: {sorted(offenders - allowed)}"
    )
    for card in (".composer:focus-within", ".pw-note:focus-within"):
        assert re.search(re.escape(card) + r"\s*\{", all_css()), (
            f"{card} removes its field's outline and must show focus on the card instead"
        )


def test_every_form_control_in_a_template_has_a_label() -> None:
    """A control with no label is unusable with a screen reader.

    Checked structurally: the control is inside a <label>, or it names one
    with `id`, or it carries `aria-label`/`aria-labelledby`. Django-rendered
    widgets (`{{ form.field }}`) are wrapped by their own <label> in the
    templates and are not reachable by this scan.
    """
    control = re.compile(r"<(input|select|textarea)\b[^>]*>", re.S)
    django_comment = re.compile(r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}", re.S)
    offenders = []
    for template in TEMPLATES:
        text = django_comment.sub("", template.read_text(encoding="utf-8"))
        for match in control.finditer(text):
            tag = match.group(0)
            if re.search(r'type="(hidden|submit|button)"', tag):
                continue
            if "aria-label" in tag or "id=" in tag:
                continue
            # inside a <label> ... </label> that opened before this point
            before = text[: match.start()]
            if before.count("<label") > before.count("</label>"):
                continue
            offenders.append(f"{template.relative_to(TEMPLATE_DIR)}: {' '.join(tag.split())[:90]}")
    assert not offenders, "form control with no accessible name:\n" + "\n".join(offenders)


def test_no_template_renders_the_same_id_twice() -> None:
    """Duplicate ids break every `for`, `aria-labelledby` and HTMX target."""
    for template in TEMPLATES:
        text = template.read_text(encoding="utf-8")
        literal = [
            value
            for value in re.findall(r'\sid="([^"{}]+)"', text)
            if "{{" not in value and "{%" not in value
        ]
        duplicates = sorted({value for value in literal if literal.count(value) > 1})
        assert not duplicates, f"{template.relative_to(TEMPLATE_DIR)}: duplicate id {duplicates}"


def test_every_button_states_its_type() -> None:
    """A <button> with no type submits the form it is in.

    Disclosure and reveal buttons live inside forms all over this UI; one
    missing `type="button"` turns a progressive-disclosure toggle into a save.
    """
    offenders = []
    for template in TEMPLATES:
        text = template.read_text(encoding="utf-8")
        for match in re.finditer(r"<button\b[^>]*>", text):
            if "type=" not in match.group(0):
                offenders.append(
                    f"{template.relative_to(TEMPLATE_DIR)}: {' '.join(match.group(0).split())[:80]}"
                )
    assert not offenders, "button with no type:\n" + "\n".join(offenders)


def test_paragraphs_are_not_used_as_flex_or_grid_containers_by_accident() -> None:
    """The prose measure in base.css must not reach component paragraphs.

    `p { max-width: 76ch }` is right for body copy and wrong for the breadcrumb,
    the register count, the timeline's system events and the facts rail — all
    of which are built on <p> and all of which wrapped or lost their alignment
    because of it.
    """
    base = (CSS_DIR / "base.css").read_text(encoding="utf-8")
    assert re.search(r"p\[class\]\s*\{[^}]*max-width:\s*none", base), (
        "base.css must exempt classed paragraphs from the prose measure"
    )
