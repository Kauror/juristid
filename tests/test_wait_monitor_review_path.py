"""A step that waits keeps its meaning on edit, and can be reviewed (ENG-021).

The defect this closes
----------------------
Two halves of one gap, reproduced on 9df0da63 before anything changed.

**`Muuda` re-classified the step.** `NextActionForm.as_service_kwargs` wrote
`DO` / `DEADLINE` for every save, so fixing a typo in an imported
«Ootame ministeeriumi vastust» (a `WAIT` whose review date, 22.9, had come round)
produced a `DO` deadline `2 p` late: it left `?tegevus=ulevaatus`, joined
`?tegevus=hilinenud`, and the department's overdue count went up by one. The
form already carried the replaced step's *precision* forward (ADR 0079 §9) and
not its kind or date meaning.

**And there was no review to do instead.** Minu asjad's «Vaatasin üle…» linked
to `#praegune-tegevus`, which offered `Muuda` and completion; the review route
existed and no page posted to it. A second POST of the same review wrote a
second `NEXT_ACTION_REVIEWED` for one look at the file.

What this module holds
----------------------
* a text-only `Muuda` keeps `WAIT`, `MONITOR` and `DO` what they were, with the
  date meaning, date and precision, and never makes a waiting step late;
* new work — `Muuda` on a Matter with no visible step, the `+ Märge` next step —
  is still `DO` / `DEADLINE`;
* `Vaatasin üle` is on the page beside a waiting step, labelled, and nowhere
  else; Minu asjad's link lands on it;
* the review goes through `acknowledge_review`: same step, new date, one event,
  and a repeated POST does not write a second one;
* a refused review writes nothing — a bad date, a plan, a superseded step, a
  reader, an administrator, a step on another Matter.

Every date is frozen.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.dates import format_estonian_date
from app.matters import work_items as wi
from app.matters.forms import period_initial
from app.matters.workspace import add_procedural_development
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics
from app.workflow.models import NextAction
from app.workflow.services import acknowledge_review, set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

TODAY = datetime.date(2026, 9, 24)
PASSED = datetime.date(2026, 9, 22)


@pytest.fixture(autouse=True)
def frozen(monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: TODAY)
    return TODAY


def _step(matter, owner, *, kind, semantics, on=PASSED, precision=DatePrecision.EXACT, text=None):
    return set_next_action(
        matter=matter,
        text=text or "Ootame ministeeriumi vastust",
        kind=kind,
        date_semantics=semantics,
        target_date=on,
        date_precision=precision,
        responsible=owner,
        actor=owner,
    )


def _muuda(action, text):
    """What `Muuda` posts when only the sentence was changed.

    Built from `period_initial`, which is what the page prefills the editor
    with — so this is the date and the `Täpsus` chip the person saw, left alone.
    """
    data = {"text": text}
    for key, value in period_initial(
        "next", action.target_date, action.date_precision, date_field="target_date"
    ).items():
        data[key] = format_estonian_date(value) if isinstance(value, datetime.date) else value
    return data


def _open(matter):
    return NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)


def _events(matter, event_type):
    return ChangeEvent.objects.filter(matter=matter, event_type=event_type)


def _detail(client, matter) -> str:
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


def _review_url(matter, action):
    return reverse("matters:review_action", kwargs={"pk": matter.pk, "action_id": action.pk})


# ---------------------------------------------------------------------------
# `Muuda` keeps what kind of work the step is
# ---------------------------------------------------------------------------

KEPT = [
    (ActionKind.WAIT, DateSemantics.REVIEW_ON, DatePrecision.EXACT, PASSED),
    (ActionKind.WAIT, DateSemantics.EXPECTED_AROUND, DatePrecision.EXACT, PASSED),
    (
        ActionKind.WAIT,
        DateSemantics.EXPECTED_AROUND,
        DatePrecision.MONTH,
        datetime.date(2026, 9, 1),
    ),
    (ActionKind.MONITOR, DateSemantics.REVIEW_ON, DatePrecision.EXACT, PASSED),
    (ActionKind.MONITOR, DateSemantics.REVIEW_ON, DatePrecision.QUARTER, datetime.date(2026, 7, 1)),
    (
        ActionKind.MONITOR,
        DateSemantics.EXPECTED_AROUND,
        DatePrecision.HALF_YEAR,
        datetime.date(2026, 7, 1),
    ),
    (ActionKind.DO, DateSemantics.DEADLINE, DatePrecision.EXACT, datetime.date(2026, 10, 2)),
    (ActionKind.DO, DateSemantics.EXPECTED_AROUND, DatePrecision.MONTH, datetime.date(2026, 10, 1)),
]


@pytest.mark.parametrize(("kind", "semantics", "precision", "on"), KEPT)
def test_a_text_only_edit_keeps_the_kind_the_meaning_and_the_date(
    client, specialist, normal_matter, kind, semantics, precision, on
):
    before = _step(
        normal_matter, specialist, kind=kind, semantics=semantics, on=on, precision=precision
    )
    client.force_login(specialist)

    response = client.post(
        reverse("matters:set_action", kwargs={"pk": normal_matter.pk}),
        _muuda(before, "Ootame ministeeriumi vastust (parandatud)"),
    )

    assert response.status_code == 200
    after = _open(normal_matter)
    assert after.pk != before.pk, "an edit supersedes; it does not rewrite history"
    assert after.text == "Ootame ministeeriumi vastust (parandatud)"
    assert (after.kind, after.date_semantics) == (kind, semantics)
    assert (after.target_date, after.date_precision) == (on, precision)
    event = _events(normal_matter, ChangeEventType.NEXT_ACTION_SET).latest("occurred_at")
    assert (event.payload["kind"], event.payload["date_semantics"]) == (kind, semantics)


@pytest.mark.parametrize("kind", [ActionKind.WAIT, ActionKind.MONITOR])
def test_a_typo_fix_does_not_make_a_waiting_step_late(client, specialist, normal_matter, kind):
    """The audit's reproduction: 22.9 had come round, and the edit made it `2 p` late."""
    before = _step(normal_matter, specialist, kind=kind, semantics=DateSemantics.REVIEW_ON)
    assert before.is_overdue(TODAY) is False
    assert before.is_due_for_review(TODAY) is True
    client.force_login(specialist)

    client.post(
        reverse("matters:set_action", kwargs={"pk": normal_matter.pk}),
        _muuda(before, "Ootame ministeeriumi vastust!"),
    )

    after = _open(normal_matter)
    assert after.is_overdue(TODAY) is False
    assert after.days_late == 0
    assert after.is_due_for_review(TODAY) is True
    assert not NextAction.objects.overdue(TODAY).filter(matter=normal_matter).exists()
    assert NextAction.objects.due_for_review(TODAY).filter(matter=normal_matter).exists()


def test_choosing_another_date_in_muuda_keeps_the_step_a_review(client, specialist, normal_matter):
    """ORDINARY-EDIT SEMANTICS, pending an owner decision.

    ADR 0052 §3 calls every step saved from this form a native `DO`; ADR 0079
    §9 and ADR 0052 §6 say an edit does not coerce what nobody touched. Whether
    deliberately choosing a new date turns a review into a plan is not settled
    by either, so it does not: the edit keeps what the step is, and the review
    path below is how a waiting step is ordinarily moved on (ENG-021, OWNER
    DECISION REQUIRED). This test pins the interim behaviour so that a decision
    changes it on purpose.
    """
    before = _step(
        normal_matter, specialist, kind=ActionKind.WAIT, semantics=DateSemantics.REVIEW_ON
    )
    client.force_login(specialist)
    data = _muuda(before, before.text)
    data["target_date"] = "15.10.2026"

    client.post(reverse("matters:set_action", kwargs={"pk": normal_matter.pk}), data)

    after = _open(normal_matter)
    assert (after.kind, after.date_semantics) == (ActionKind.WAIT, DateSemantics.REVIEW_ON)
    assert after.target_date == datetime.date(2026, 10, 15)


def test_muuda_on_a_matter_with_no_step_creates_ordinary_work(client, specialist, normal_matter):
    """New work stays `DO` / `DEADLINE`. Nothing is carried from nowhere."""
    client.force_login(specialist)

    client.post(
        reverse("matters:set_action", kwargs={"pk": normal_matter.pk}),
        {"text": "Koostan arvamuse", "target_date": "30.09.2026", "next_precision": "EXACT"},
    )

    action = _open(normal_matter)
    assert (action.kind, action.date_semantics) == (ActionKind.DO, DateSemantics.DEADLINE)


def test_a_crafted_kind_is_still_not_read_from_the_post(client, specialist, normal_matter):
    """The kind comes from the step being edited, never from the request."""
    before = _step(
        normal_matter, specialist, kind=ActionKind.MONITOR, semantics=DateSemantics.REVIEW_ON
    )
    client.force_login(specialist)
    data = _muuda(before, "Jälgin")
    data.update({"kind": ActionKind.DO, "date_semantics": DateSemantics.DEADLINE})

    client.post(reverse("matters:set_action", kwargs={"pk": normal_matter.pk}), data)

    after = _open(normal_matter)
    assert (after.kind, after.date_semantics) == (ActionKind.MONITOR, DateSemantics.REVIEW_ON)


def test_a_next_step_written_in_marge_is_new_work(specialist, normal_matter):
    """`+ Märge`'s `Järgmine tegevus` is a new instruction, not an edit of the
    open one — it is typed fresh beside the development that prompted it — so
    it replaces a waiting step with an ordinary plan."""
    _step(normal_matter, specialist, kind=ActionKind.WAIT, semantics=DateSemantics.REVIEW_ON)

    add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Ministeerium saatis uue versiooni",
        next_text="Koostan arvamuse uuele versioonile",
        next_date=datetime.date(2026, 10, 5),
    )

    action = _open(normal_matter)
    assert (action.kind, action.date_semantics) == (ActionKind.DO, DateSemantics.DEADLINE)


# ---------------------------------------------------------------------------
# `Vaatasin üle` is on the page, and only where it means something
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", [ActionKind.WAIT, ActionKind.MONITOR])
def test_a_waiting_step_offers_a_labelled_review_control(client, specialist, normal_matter, kind):
    action = _step(normal_matter, specialist, kind=kind, semantics=DateSemantics.REVIEW_ON)
    client.force_login(specialist)

    body = _detail(client, normal_matter)

    zone = body.split('id="praegune-tegevus"')[1].split("</section>")[0]
    assert 'id="vaatasin-ule"' in zone
    panel = zone.split('id="vaatasin-ule"')[1].split("</details>")[0]
    assert ">Vaatasin üle</summary>" in panel
    assert _review_url(normal_matter, action) in panel
    # Labelled: the box has a `<label for>` naming it, and its help is wired.
    assert 'for="id_ulevaatus_next_review_date"' in panel
    assert 'id="id_ulevaatus_next_review_date"' in panel
    assert 'aria-describedby="id_ulevaatus_next_review_date_helptext"' in panel
    assert 'id="id_ulevaatus_next_review_date_helptext"' in panel
    assert "Salvesta ülevaatus" in panel
    # The stored classification is never printed (ADR 0054).
    for word in ("WAIT", "MONITOR", "REVIEW_ON", "Ootan", "Jälgin"):
        assert word not in panel, word


def test_a_plan_offers_no_review(client, specialist, normal_matter):
    _step(normal_matter, specialist, kind=ActionKind.DO, semantics=DateSemantics.DEADLINE)
    client.force_login(specialist)

    assert 'id="vaatasin-ule"' not in _detail(client, normal_matter)


def test_a_reader_sees_the_step_and_no_review_control(client, reader, specialist, normal_matter):
    _step(normal_matter, specialist, kind=ActionKind.WAIT, semantics=DateSemantics.REVIEW_ON)
    client.force_login(reader)

    body = _detail(client, normal_matter)

    assert "Ootame ministeeriumi vastust" in body
    assert 'id="vaatasin-ule"' not in body


def test_minu_asjad_links_a_ripe_review_to_the_control_that_does_it(
    client, specialist, normal_matter
):
    _step(normal_matter, specialist, kind=ActionKind.MONITOR, semantics=DateSemantics.REVIEW_ON)
    client.force_login(specialist)

    work = client.get(reverse("matters:my_work")).content.decode()
    href = f"/teemad/{normal_matter.pk}/#vaatasin-ule"

    assert href in work
    assert (
        work[work.index(href) : work.index(href) + 200].split("</a>")[0].endswith("Vaatasin üle…")
    )
    assert 'id="vaatasin-ule"' in _detail(client, normal_matter)


def test_arrival_opens_the_review_panel():
    """`ux.js` opens only the fragments it lists; an unlisted one scrolls to a
    shut box (tests/test_drilldown_arrival.py)."""
    from django.conf import settings

    source = (Path(settings.BASE_DIR) / "static" / "js" / "ux.js").read_text(encoding="utf-8")

    assert '"vaatasin-ule"' in source.split("NEXT_STEP_TARGETS")[1].split("]")[0]


# ---------------------------------------------------------------------------
# The review itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", [ActionKind.WAIT, ActionKind.MONITOR])
def test_a_review_moves_the_date_and_keeps_the_step(client, specialist, normal_matter, kind):
    action = _step(normal_matter, specialist, kind=kind, semantics=DateSemantics.EXPECTED_AROUND)
    client.force_login(specialist)

    response = client.post(_review_url(normal_matter, action), {"next_review_date": "15.10.2026"})

    assert response.status_code == 200
    after = _open(normal_matter)
    assert after.pk == action.pk, "a review is not a replacement"
    assert (after.kind, after.date_semantics, after.text) == (
        kind,
        DateSemantics.EXPECTED_AROUND,
        action.text,
    )
    assert (after.target_date, after.date_precision) == (
        datetime.date(2026, 10, 15),
        DatePrecision.EXACT,
    )
    assert after.is_due_for_review(TODAY) is False
    assert after.is_overdue(datetime.date(2027, 1, 1)) is False
    events = _events(normal_matter, ChangeEventType.NEXT_ACTION_REVIEWED)
    assert events.count() == 1
    assert events.get().payload == {"from": "2026-09-22", "to": "2026-10-15", "kind": kind}
    # And the step has left `Ülevaatamiseks`, having been reviewed.
    items = wi.work_items(specialist, today=TODAY)
    assert normal_matter.pk not in wi.work_population_ids(
        specialist, wi.WORK_RIPE, today=TODAY, items=items
    )


def test_the_same_review_posted_twice_is_one_review(client, specialist, normal_matter):
    """A double press, or the form sent again: one look at the file, one event."""
    action = _step(
        normal_matter, specialist, kind=ActionKind.WAIT, semantics=DateSemantics.REVIEW_ON
    )
    client.force_login(specialist)
    url = _review_url(normal_matter, action)

    first = client.post(url, {"next_review_date": "15.10.2026"})
    second = client.post(url, {"next_review_date": "15.10.2026"})

    assert (first.status_code, second.status_code) == (200, 200)
    assert _events(normal_matter, ChangeEventType.NEXT_ACTION_REVIEWED).count() == 1
    assert _open(normal_matter).target_date == datetime.date(2026, 10, 15)


def test_two_reviews_are_two_entries_in_the_history(specialist, normal_matter):
    """Exactly once *per review* — a later look with a new date is a new fact."""
    action = _step(
        normal_matter, specialist, kind=ActionKind.WAIT, semantics=DateSemantics.REVIEW_ON
    )

    acknowledge_review(
        action=action, actor=specialist, next_review_date=datetime.date(2026, 10, 15)
    )
    acknowledge_review(action=action, actor=specialist, next_review_date=datetime.date(2026, 11, 2))

    payloads = [
        (event.payload["from"], event.payload["to"])
        for event in _events(normal_matter, ChangeEventType.NEXT_ACTION_REVIEWED).order_by(
            "occurred_at"
        )
    ]
    assert payloads == [("2026-09-22", "2026-10-15"), ("2026-10-15", "2026-11-02")]


def test_an_empty_review_date_is_an_answer(client, specialist, normal_matter):
    action = _step(
        normal_matter, specialist, kind=ActionKind.MONITOR, semantics=DateSemantics.REVIEW_ON
    )
    client.force_login(specialist)

    response = client.post(_review_url(normal_matter, action), {"next_review_date": ""})

    assert response.status_code == 200
    assert _open(normal_matter).target_date is None
    assert _events(normal_matter, ChangeEventType.NEXT_ACTION_REVIEWED).count() == 1


# ---------------------------------------------------------------------------
# A refused review writes nothing
# ---------------------------------------------------------------------------


def _nothing_written(matter, action, before_events):
    action.refresh_from_db()
    assert action.status == ActionStatus.OPEN
    assert action.target_date == PASSED
    assert ChangeEvent.objects.filter(matter=matter).count() == before_events


@pytest.mark.parametrize("typed", ["31.02.2026", "2026-02-30", "homme"])
def test_a_date_that_is_not_a_day_is_refused_in_the_panel(client, specialist, normal_matter, typed):
    action = _step(
        normal_matter, specialist, kind=ActionKind.WAIT, semantics=DateSemantics.REVIEW_ON
    )
    before = ChangeEvent.objects.filter(matter=normal_matter).count()
    client.force_login(specialist)

    response = client.post(_review_url(normal_matter, action), {"next_review_date": typed})

    assert response.status_code == 400
    body = response.content.decode()
    panel = body.split('id="vaatasin-ule"')[1].split("</details>")[0]
    # The panel it came from, reopened, with the sentence on the box.
    assert body.split('id="vaatasin-ule"')[1].split(">")[0].rstrip().endswith("open")
    assert "Kirjuta kuupäev kujul 7.9.2026." in panel
    assert 'id="id_ulevaatus_next_review_date_error"' in panel
    assert 'aria-invalid="true"' in panel
    _nothing_written(normal_matter, action, before)


def test_a_plan_cannot_be_reviewed_through_the_route(client, specialist, normal_matter):
    action = _step(normal_matter, specialist, kind=ActionKind.DO, semantics=DateSemantics.DEADLINE)
    before = ChangeEvent.objects.filter(matter=normal_matter).count()
    client.force_login(specialist)

    response = client.post(_review_url(normal_matter, action), {"next_review_date": "15.10.2026"})

    assert response.status_code == 400
    assert "Üle vaadata saab ainult ootamist või jälgimist." in response.content.decode()
    _nothing_written(normal_matter, action, before)


def test_a_superseded_step_is_not_reviewed(client, specialist, normal_matter):
    """A stale tab: the step was replaced after the page was drawn."""
    stale = _step(
        normal_matter, specialist, kind=ActionKind.WAIT, semantics=DateSemantics.REVIEW_ON
    )
    _step(
        normal_matter,
        specialist,
        kind=ActionKind.WAIT,
        semantics=DateSemantics.REVIEW_ON,
        text="Uus",
    )
    before = _events(normal_matter, ChangeEventType.NEXT_ACTION_REVIEWED).count()
    client.force_login(specialist)

    response = client.post(_review_url(normal_matter, stale), {"next_review_date": "15.10.2026"})

    assert response.status_code == 400
    assert "Ainult kehtivat tegevust saab üle vaadata." in response.content.decode()
    stale.refresh_from_db()
    assert stale.status == ActionStatus.SUPERSEDED
    assert stale.target_date == PASSED
    assert _open(normal_matter).target_date == PASSED
    assert _events(normal_matter, ChangeEventType.NEXT_ACTION_REVIEWED).count() == before


@pytest.mark.parametrize("actor", ["reader", "administrator"])
def test_a_non_writer_cannot_review(request, client, specialist, normal_matter, actor):
    action = _step(
        normal_matter, specialist, kind=ActionKind.WAIT, semantics=DateSemantics.REVIEW_ON
    )
    before = ChangeEvent.objects.filter(matter=normal_matter).count()
    client.force_login(request.getfixturevalue(actor))

    response = client.post(_review_url(normal_matter, action), {"next_review_date": "15.10.2026"})

    assert response.status_code == 404
    _nothing_written(normal_matter, action, before)


def test_a_step_on_another_matter_is_not_reachable_through_this_one(
    client, specialist, normal_matter
):
    elsewhere = factories.MatterFactory(owner=specialist)
    action = _step(elsewhere, specialist, kind=ActionKind.WAIT, semantics=DateSemantics.REVIEW_ON)
    before = ChangeEvent.objects.filter(matter=elsewhere).count()
    client.force_login(specialist)

    response = client.post(
        reverse("matters:review_action", kwargs={"pk": normal_matter.pk, "action_id": action.pk}),
        {"next_review_date": "15.10.2026"},
    )

    assert response.status_code == 404
    _nothing_written(elsewhere, action, before)
