"""Every workspace refusal lands in an error slot the Teema page actually draws.

The Teema page routes a refused save by string keys. `_workspace_refusal` is
called with a form key; `WORKSPACE_PANELS` maps that key to a launcher family
and a sub-choice id; `WORKSPACE_CHOICE_FAMILY` maps the sub-choice to the
template variable that checks its radio; and each panel's template prints
``workspace_error`` behind a literal ``open_panel == "…"`` or
``open_choice == "…"`` comparison. Nothing tied the four together. An
unregistered key, or a misspelt id on either side, sends the sentence to a slot
that is never drawn, or is drawn shut. The person then sees a 400 that looks as
if nothing happened. This has happened before: until docs/adr/0170, `add_note`
and stale planned-row refusals were printed inside a collapsed «Meile saadetud
tagasiside» panel (JARCH-003, 2026-10-17 audit).

This file is the completeness test. It reads the keys from the code rather than
from a list kept here, so a new door is covered the day it is written:

* every key any `_workspace_refusal` call can receive is registered;
* every registered family and choice id is one the templates draw and route;
* every template comparison names a registered id;
* the step launcher's canonical forms agree with the registry;
* and, rendered for real, each key's refusal sentence appears exactly once,
  inside a `formerror` whose every enclosing panel is open.

Tests only; nothing in the product changes.
"""

from __future__ import annotations

import ast
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from bs4 import BeautifulSoup, Tag
from django import forms
from django.urls import reverse
from django.utils import timezone

from app.matters import step_launch, views
from app.matters.views import (
    PLANNED_ROW_FORMS,
    WORKSPACE_CHOICE_FAMILY,
    WORKSPACE_DEFAULT_CHOICES,
    WORKSPACE_PANELS,
    _workspace_refusal,
)
from app.workflow.enums import ActionKind, DateSemantics
from app.workflow.plan import seed_standard_plan
from app.workflow.services import add_planned_action, set_next_action
from tests import factories

ROOT = Path(__file__).resolve().parents[1]
VIEWS = ROOT / "app" / "matters" / "views.py"
TEMPLATES = ROOT / "templates"

#: Refusals that are routed to the workspace-level slot on purpose, because
#: their panel left the column: `add_note`'s `Tavaline` (2026-10-07). The
#: render test below holds them to that slot rather than to a panel.
COMPOSER_ONLY = {"progress_form"}

#: The families whose sub-choice is chosen by a `<family>_choice` variable.
CHOICE_VARIABLES = set(WORKSPACE_CHOICE_FAMILY.values())


# =========================================================================
# What the code can pass, read from the code
# =========================================================================


def _string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    return next((kw.value for kw in call.keywords if kw.arg == name), None)


def _calls_to(tree: ast.AST, name: str) -> list[tuple[ast.Call, ast.FunctionDef]]:
    """Every call to ``name`` with the function it sits in."""
    found = []
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == name
            ):
                found.append((node, function))
    # A nested function is walked from its parent too; keep the innermost.
    innermost: dict[int, tuple[ast.Call, ast.FunctionDef]] = {}
    for call, function in found:
        current = innermost.get(id(call))
        if current is None or function.lineno > current[1].lineno:
            innermost[id(call)] = (call, function)
    return list(innermost.values())


def _resolve_key(tree: ast.AST, node: ast.AST, function: ast.FunctionDef, depth: int = 0) -> set:
    """The literal keys an argument can carry: a literal, a local assigned one,
    or a parameter whose every caller passes one. Anything else is unresolved."""
    literal = _string(node)
    if literal is not None:
        return {literal}
    if not isinstance(node, ast.Name) or depth > 3:
        return {f"<unresolved {ast.unparse(node)} in {function.name}>"}
    assigned = [
        _string(statement.value)
        for statement in ast.walk(function)
        if isinstance(statement, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == node.id for t in statement.targets)
    ]
    if assigned:
        return {value or f"<unresolved {node.id} in {function.name}>" for value in assigned}
    parameters = {arg.arg for arg in function.args.args + function.args.kwonlyargs}
    if node.id not in parameters:
        return {f"<unresolved {node.id} in {function.name}>"}
    keys: set[str] = set()
    callers = _calls_to(tree, function.name)
    if not callers:
        return {f"<unresolved {node.id}: {function.name} has no caller>"}
    for call, caller in callers:
        passed = _keyword(call, node.id)
        if passed is None:
            keys.add(f"<unresolved {node.id}: {caller.name} passes it positionally>")
            continue
        keys |= _resolve_key(tree, passed, caller, depth + 1)
    return keys


def refusal_keys() -> set[str]:
    tree = ast.parse(VIEWS.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for call, function in _calls_to(tree, "_workspace_refusal"):
        argument = _keyword(call, "key")
        assert argument is not None, f"{function.name}: _workspace_refusal without key="
        keys |= _resolve_key(tree, argument, function)
    return keys


def test_the_key_reader_finds_every_door() -> None:
    """The reader itself: it must see the doors that pass a key indirectly."""
    keys = refusal_keys()
    # A literal, a local `key = "…"`, a parameter of `_record_external_position`
    # and one of `_closure_needs_confirmation`.
    assert {"other_form", "website_overview_form", "engagement_reply_form"} <= keys
    assert "current_action_form" in keys
    assert len(keys) >= 19


def test_every_key_a_refusal_can_receive_is_registered() -> None:
    keys = refusal_keys()
    unresolved = sorted(key for key in keys if key.startswith("<"))
    assert not unresolved, f"keys the contract cannot read: {unresolved}"
    assert keys <= set(WORKSPACE_PANELS), sorted(keys - set(WORKSPACE_PANELS))


def test_every_registered_key_is_a_door_that_exists() -> None:
    """No dead entry: a registered key nothing can refuse with is a stale route."""
    assert set(WORKSPACE_PANELS) <= refusal_keys(), sorted(set(WORKSPACE_PANELS) - refusal_keys())


def test_planned_row_forms_are_registered_keys() -> None:
    assert set(PLANNED_ROW_FORMS) <= set(WORKSPACE_PANELS)


# =========================================================================
# The ids agree with each other and with the templates
# =========================================================================


def test_every_choice_id_has_a_family_and_a_door() -> None:
    routed = {choice for _family, choice in WORKSPACE_PANELS.values() if choice}
    assert routed == set(WORKSPACE_CHOICE_FAMILY), sorted(routed ^ set(WORKSPACE_CHOICE_FAMILY))


def test_every_default_choice_is_one_of_its_own_family() -> None:
    for variable, choice in WORKSPACE_DEFAULT_CHOICES.items():
        assert WORKSPACE_CHOICE_FAMILY[choice] == variable


def test_a_family_with_sub_choices_routes_all_of_them_to_one_variable() -> None:
    by_family: dict[str, set[str]] = {}
    for family, choice in WORKSPACE_PANELS.values():
        if choice:
            by_family.setdefault(family, set()).add(WORKSPACE_CHOICE_FAMILY[choice])
    for family, variables in by_family.items():
        assert len(variables) == 1, (family, variables)


def _template_sources() -> dict[Path, str]:
    return {path: path.read_text(encoding="utf-8") for path in TEMPLATES.rglob("*.html")}


COMPARISON = re.compile(r"\b(open_panel|open_choice|[a-z]+_choice)\s*==\s*\"([^\"]+)\"")


def _comparisons() -> list[tuple[Path, str, str]]:
    return [
        (path, variable, value)
        for path, source in _template_sources().items()
        for variable, value in COMPARISON.findall(source)
    ]


def test_the_template_comparisons_are_found() -> None:
    assert len(_comparisons()) >= 25


def test_every_template_comparison_names_a_registered_id() -> None:
    families = {family for family, _choice in WORKSPACE_PANELS.values()}
    wrong = []
    for path, variable, value in _comparisons():
        name = path.relative_to(TEMPLATES).as_posix()
        if variable == "open_panel" and value not in families:
            wrong.append((name, variable, value))
        elif variable == "open_choice" and value not in WORKSPACE_CHOICE_FAMILY:
            wrong.append((name, variable, value))
        elif variable in CHOICE_VARIABLES and WORKSPACE_CHOICE_FAMILY.get(value) != variable:
            wrong.append((name, variable, value))
    assert not wrong, wrong


def test_every_registered_id_has_an_error_slot_in_a_template() -> None:
    """The static half: some template prints `workspace_error` behind the id."""
    sources = "\n".join(_template_sources().values())
    missing = []
    for key, (family, choice) in WORKSPACE_PANELS.items():
        if key in COMPOSER_ONLY:
            continue
        variable, value = ("open_choice", choice) if choice else ("open_panel", family)
        slot = re.compile(
            rf"{variable} == \"{re.escape(value)}\" and workspace_error"
            rf"|workspace_error and {variable} == \"{re.escape(value)}\""
        )
        if not slot.search(sources):
            missing.append((key, variable, value))
    assert not missing, missing


def test_the_step_launchers_forms_agree_with_the_registry() -> None:
    """docs/adr/0168: `Alusta` opens the same panel a refusal of that form reopens."""
    for form in step_launch.FORMS_BY_SLUG.values():
        assert WORKSPACE_PANELS[form.form_key] == (form.family, form.choice), form
        if form.choice:
            # `apply_open_request` names the radio variable by family.
            variable = "kaasamine_choice" if form.family == "lisa-kaasamine" else "arvamus_choice"
            assert WORKSPACE_CHOICE_FAMILY[form.choice] == variable, form


# =========================================================================
# Rendered: the sentence is drawn once, in a panel that is open
# =========================================================================

SENTINEL = "Lepingu-kontrolllause-{key}"


def _opened(panel: Tag, page: BeautifulSoup) -> bool:
    """Whether a launcher panel is shown: `<details open>`, or a `cx-panel`
    whose controlling radio or checkbox is checked."""
    if panel.name == "details":
        return panel.has_attr("open")
    panel_id = panel.get("id")
    control = page.find("input", attrs={"aria-controls": panel_id}) if panel_id else None
    return control is not None and control.has_attr("checked")


def _shown(page: BeautifulSoup, sentence: str) -> tuple[Tag, list[str]]:
    hits = list(page.find_all(string=re.compile(re.escape(sentence))))
    assert len(hits) == 1, f"{sentence!r} printed {len(hits)} times"
    slot = hits[0].parent
    assert isinstance(slot, Tag)
    shut = []
    for ancestor in [slot, *slot.parents]:
        if not isinstance(ancestor, Tag):
            continue
        if ancestor.has_attr("hidden"):
            shut.append(f"{ancestor.name}#{ancestor.get('id')} hidden")
        classes = ancestor.get("class") or []
        if ancestor.name == "details" or "cx-panel" in classes:
            if not _opened(ancestor, page):
                shut.append(f"{ancestor.name}#{ancestor.get('id')} shut")
    return slot, shut


@pytest.fixture
def teema(db, specialist):
    matter = factories.MatterFactory(owner=specialist)
    seed_standard_plan(matter=matter, actor=specialist)
    return matter


def _current(matter, actor, *, waits: bool = False):
    return set_next_action(
        matter=matter,
        text="Sünteetiline praegune tegevus",
        kind=ActionKind.WAIT if waits else ActionKind.DO,
        date_semantics=DateSemantics.REVIEW_ON if waits else DateSemantics.DEADLINE,
        target_date=timezone.localdate() + timedelta(days=7),
        actor=actor,
    )


#: The page state each panel is drawn in. Anything not named here is a
#: `LISA TEEMALE` panel, drawn on any open Matter the reader may write to.
STATE = {
    "current_action_form": "current",
    "action_form": "current",
    "planned_action_form": "current",
    "planned_edit_form": "planned",
    "planned_done_form": "planned",
    "review_form": "waiting",
    "start_plan_form": "suggested",
    "plan_revision_form": "suggested",
}


def _arrange(state: str, matter, actor) -> None:
    if state in ("current", "planned"):
        _current(matter, actor)
    if state == "planned":
        add_planned_action(
            matter=matter,
            text="Sünteetiline planeeritud tegevus",
            target_date=timezone.localdate() + timedelta(days=14),
            actor=actor,
        )
    if state == "waiting":
        _current(matter, actor, waits=True)


def _refused_form(key: str, context: Any) -> Any:
    row = PLANNED_ROW_FORMS.get(key)
    if row:
        planned = context["planned_actions"][0]
        form = getattr(planned, f"{row}_form")
        form.planned_action_id = planned.pk
        return form
    if key in context:
        return context[key]
    # `add_note`'s form has no panel to render it in; any form will do.
    return forms.Form()


@pytest.mark.parametrize("key", sorted(WORKSPACE_PANELS))
def test_each_refusal_is_drawn_once_in_an_open_slot(signed_in, specialist, teema, key) -> None:
    _arrange(STATE.get(key, ""), teema, specialist)
    page = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": teema.pk}))
    assert page.status_code == 200
    sentence = SENTINEL.format(key=key)

    response = _workspace_refusal(
        page.wsgi_request,
        teema,
        key=key,
        form=_refused_form(key, page.context),
        error=sentence,
    )

    assert response.status_code == 400
    html = BeautifulSoup(response.content.decode(), "html.parser")
    slot, shut = _shown(html, sentence)
    assert "formerror" in (slot.get("class") or []), slot
    assert not shut, shut
    in_composer = "workspace__error" in (slot.get("class") or [])
    assert in_composer is (key in COMPOSER_ONLY), (key, slot)


def test_the_render_check_would_catch_a_slot_drawn_shut(signed_in, specialist, teema) -> None:
    """The checker itself: a refusal routed to a panel that stays closed fails."""
    page = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": teema.pk}))
    original = dict(WORKSPACE_PANELS)
    try:
        # Route `other_form` to its family but to a sibling's sub-choice: the
        # family opens, the sentence prints nowhere it is drawn.
        views.WORKSPACE_PANELS["other_form"] = ("lisa-marge", "marge-tahtaeg-typo")
        response = _workspace_refusal(
            page.wsgi_request,
            teema,
            key="other_form",
            form=page.context["other_form"],
            error="Lepingu-kontrolllause-negatiivne",
        )
    finally:
        views.WORKSPACE_PANELS.clear()
        views.WORKSPACE_PANELS.update(original)
    html = BeautifulSoup(response.content.decode(), "html.parser")
    assert not html.find_all(string=re.compile("Lepingu-kontrolllause-negatiivne"))
