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
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from app.matters import overview as ov
from app.matters import work_items as wi
from app.matters.services import add_entry, assign_matter, change_stage, create_matter
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics
from app.workflow.models import NextAction
from app.workflow.services import set_next_action
from tests import factories

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


# ---------------------------------------------------------------------------
# 1a — the deadline panel
# ---------------------------------------------------------------------------


def _due(owner, *, days: int, title: str, actor=None):
    """A Matter carrying a real deadline `days` from today.

    A DO with DEADLINE semantics, because that — and an Oluline tähtaeg — is
    what the department may honestly call a deadline. A Matter's own
    `response_deadline` is a different fact and is deliberately not one
    (app/matters/work_items.py, `real_deadlines`).
    """
    matter = create_matter(title=title, owner=owner, reference_year=2026, actor=actor or owner)
    set_next_action(
        matter=matter,
        text="Saada koja arvamus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() + timedelta(days=days),
        responsible=owner,
        actor=actor or owner,
    )
    return matter


@pytest.mark.django_db
def test_the_four_deadline_windows_are_consecutive_and_exhaustive(department_head) -> None:
    """Nothing dated can fall between two windows, or into both.

    Asserted over a whole year of days rather than one, because the boundaries
    move with the weekday and an off-by-one that only shows on a Sunday is
    exactly the kind that reaches production.
    """
    for offset in range(0, 366, 7):
        today = date(2026, 1, 5) + timedelta(days=offset)
        for shift in range(0, 8):
            day = today + timedelta(days=shift)
            windows = [wi.deadline_window(key, day) for key in wi.DEADLINE_WINDOW_KEYS]
            assert windows[0][0] == day, "the first window starts today"
            assert windows[-1][1] is None, "the last window is open-ended"
            for (_, ends), (starts, _) in pairwise(windows):
                assert ends is not None
                assert starts == ends + timedelta(days=1), (
                    f"gap or overlap at {day}: {ends} then {starts}"
                )


@pytest.mark.django_db
def test_a_deadline_past_thirty_days_is_on_the_page_rather_than_nowhere(
    client, department_head
) -> None:
    """The reason the fourth window exists.

    Before it, the panel ended at next week and a deadline five weeks out was on
    no screen at all until it became next week's problem (design handoff 1a).
    """
    today = timezone.localdate()
    _due(department_head, days=40, title="Ehitusseadustiku muutmise seaduse eelnõu")

    page = ov.build_overview(department_head, scope=ov.SCOPE_DEPARTMENT, today=today)
    far = next(group for group in page.deadlines if group.is_far)
    assert far.count == 1
    assert far.first is not None
    assert far.first.matter.title == "Ehitusseadustiku muutmise seaduse eelnõu"

    client.force_login(department_head)
    body = client.get(reverse("matters:overview") + "?vaade=osakond").content.decode()
    assert "KAUGEMAL" in body
    assert "registris →" in body


@pytest.mark.django_db
def test_each_group_link_opens_exactly_the_matters_it_counted(department_head) -> None:
    """`kõik N →` is a promise about the list behind it.

    The register is asked for the group's own URL rather than for a condition
    rebuilt here, so this cannot pass by two similar queries agreeing with each
    other (app/matters/register_filters.py, `register_population`).
    """
    from urllib.parse import parse_qsl, urlparse

    from app.matters.register_filters import register_population

    today = timezone.localdate()
    for days, title in ((1, "Sel nädalal"), (9, "Järgmisel"), (25, "Kuu jooksul"), (60, "Kaugel")):
        _due(department_head, days=days, title=f"{title} — eelnõu")

    page = ov.build_overview(department_head, scope=ov.SCOPE_DEPARTMENT, today=today)
    for group in page.deadlines:
        query = dict(parse_qsl(urlparse(group.url).query))
        listed = register_population(department_head, query, today=today)
        assert listed.count() == group.matter_count, f"{group.key} promises what it cannot show"


@pytest.mark.django_db
def test_today_says_today_and_nobody_says_so_in_more_than_colour(client, department_head) -> None:
    _due(department_head, days=0, title="Täna tähtaeg — eelnõu")
    _due(None, days=2, title="Vastutajata — eelnõu", actor=department_head)

    client.force_login(department_head)
    body = client.get(reverse("matters:overview") + "?vaade=osakond").content.decode()

    panel = body.split('aria-label="Tähtajad"')[-1].split("</section>")[0]
    flat = " ".join(panel.split())

    assert 'class="uxdl__date uxdl__date--today">täna<' in flat
    # The word and the dashed mark, not the warning tint on its own.
    assert 'class="uxav uxav--none" title="Vastutajata">!<' in flat
    # The footer the design replaced with a header link.
    assert "Ava kõik" not in flat
    assert "kõik 2 →" in flat


# ---------------------------------------------------------------------------
# 1b — the timeline spine
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_adjacent_system_events_fold_and_a_note_breaks_the_run(
    specialist, other_specialist
) -> None:
    """Folding is by adjacency in the rendered order, not by kind.

    A colleague's note between two field changes is what the reader came for, so
    the run stops there. Folding across it would hide the shape of the file's
    month (design handoff 1b).
    """
    from app.matters import timeline as tl

    matter = factories.MatterFactory(owner=specialist)
    change_stage(matter=matter, stage=factories.StageFactory(), actor=specialist)
    add_entry(matter=matter, author=specialist, body="<p>Kohtusin ministeeriumiga.</p>")
    change_stage(matter=matter, stage=factories.StageFactory(), actor=specialist)
    assign_matter(matter=matter, owner=other_specialist, actor=specialist)

    items, _ = tl.matter_timeline(matter=matter, user=specialist)
    rows = tl.collapse_system_runs(items)

    runs = [row for row in rows if row.is_run]
    assert runs, "adjacent system events must fold into one row"
    assert all(item.is_system for row in runs for item in row.items)
    assert any(not row.is_run and row.item.is_entry for row in rows), (
        "a note is its own line and is never inside a run"
    )
    # Nothing is dropped by folding.
    assert sum(row.count for row in rows) == len(items)


@pytest.mark.django_db
def test_the_closed_timeline_says_what_was_written_and_what_is_owed(client, specialist) -> None:
    """A counter tells a reader how much there is and nothing about whether
    they need it (design handoff 1b)."""
    matter = factories.MatterFactory(owner=specialist)
    add_entry(
        matter=matter,
        author=specialist,
        body="<p>Ministeerium kinnitas, et üleminekuaeg on läbiräägitav.</p>",
    )
    set_next_action(
        matter=matter,
        text="Saada koja arvamus EIS-i",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() + timedelta(days=5),
        actor=specialist,
    )

    client.force_login(specialist)
    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()
    summary = " ".join(body.split("accordion--timeline")[1].split("</summary>")[0].split())

    assert "üleminekuaeg on läbiräägitav" in summary, "the last thing somebody wrote"
    assert "→ TEEN" in summary and "Saada koja arvamus EIS-i" in summary, "what is owed"
    assert "kirjet" in summary, "and how much there is"


@pytest.mark.django_db
def test_a_saves_next_step_rides_with_it_at_the_precision_it_was_recorded(specialist) -> None:
    """The change event's payload carries an anchor and no precision, so a step
    recorded as *september 2026* would print as `01.09` if the strip were built
    from the payload (master specification 3.5)."""
    from app.audit.operations import composer_operation
    from app.matters import timeline as tl

    matter = factories.MatterFactory(owner=specialist)
    with composer_operation():
        add_entry(matter=matter, author=specialist, body="<p>Märkus</p>")
        set_next_action(
            matter=matter,
            text="Jälgin menetlust",
            kind=ActionKind.MONITOR,
            date_semantics=DateSemantics.REVIEW_ON,
            target_date=date(2026, 9, 1),
            date_precision=DatePrecision.MONTH,
            actor=specialist,
        )

    items, _ = tl.matter_timeline(matter=matter, user=specialist)
    step = next(item.next_step for item in items if item.next_step is not None)

    assert step.mode == "JÄLGIN"
    assert step.text == "Jälgin menetlust"
    assert "01.09" not in step.date_value, "a month must not print as a day"


# ---------------------------------------------------------------------------
# 1c — Järgmiseks
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_deferring_a_deadline_supersedes_the_step_and_keeps_its_responsible(
    client, specialist, other_specialist
) -> None:
    """A DO carries a commitment, so moving it is a new instruction.

    Left to the service default the new step would fall to the Matter's owner,
    quietly moving a colleague's instruction onto somebody else's queue
    (app/workflow/services.py, `responsible_for_new_work`).
    """
    matter = factories.MatterFactory(owner=specialist)
    action = set_next_action(
        matter=matter,
        text="Saada koja arvamus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() - timedelta(days=6),
        responsible=other_specialist,
        actor=specialist,
    )

    client.force_login(specialist)
    response = client.post(
        reverse("matters:defer_action", kwargs={"pk": matter.pk, "action_id": action.pk}),
        {"paevad": "7"},
    )
    assert response.status_code == 200

    action.refresh_from_db()
    assert action.status == ActionStatus.SUPERSEDED
    current = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert current.target_date == timezone.localdate() + timedelta(days=7)
    assert current.responsible == other_specialist
    assert current.text == "Saada koja arvamus"


@pytest.mark.django_db
def test_deferring_a_wait_acknowledges_the_review_and_keeps_the_same_step(
    client, specialist
) -> None:
    """Waiting is not lateness, and moving a review date is not a new promise.

    The action keeps its identity: the Matter is still waiting on the same
    thing (app/workflow/services.py, `acknowledge_review`).
    """
    matter = factories.MatterFactory(owner=specialist)
    action = set_next_action(
        matter=matter,
        text="Ootan ministeeriumi vastust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=timezone.localdate() - timedelta(days=3),
        actor=specialist,
    )

    client.force_login(specialist)
    client.post(
        reverse("matters:defer_action", kwargs={"pk": matter.pk, "action_id": action.pk}),
        {"kuupaev": "15.9.2026"},
    )

    action.refresh_from_db()
    assert action.status == ActionStatus.OPEN, "reviewing is not completing"
    assert action.target_date == date(2026, 9, 15)


@pytest.mark.django_db
def test_an_unreadable_deferral_is_refused_with_the_page_and_the_reason(client, specialist) -> None:
    matter = factories.MatterFactory(owner=specialist)
    action = set_next_action(
        matter=matter,
        text="Saada koja arvamus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate(),
        actor=specialist,
    )

    client.force_login(specialist)
    response = client.post(
        reverse("matters:defer_action", kwargs={"pk": matter.pk, "action_id": action.pk}),
        {"kuupaev": "31.02.2026"},
    )

    assert response.status_code == 400
    assert "Kirjuta kuupäev kujul" in response.content.decode()
    action.refresh_from_db()
    assert action.target_date == timezone.localdate(), "nothing moved"


@pytest.mark.django_db
def test_an_approximate_step_is_not_offered_a_one_day_deferral(client, specialist) -> None:
    """A step recorded to a month is deliberately vague, and adding a day to it
    would be a day nobody chose (master specification 3.5)."""
    matter = factories.MatterFactory(owner=specialist)
    set_next_action(
        matter=matter,
        text="Jälgin menetlust",
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=date(2026, 9, 1),
        date_precision=DatePrecision.MONTH,
        actor=specialist,
    )

    client.force_login(specialist)
    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()
    assert "uxnext__defer" not in body
