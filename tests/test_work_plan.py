"""`Tööplaan` — one current action, guided by a lightweight plan (docs/adr/0133).

Asserted here, each where it is decided:

* **the model** — the standard template, its seeding (interactive creation only,
  idempotent, never a backfill), the step states and their constraints;
* **the invariant** — at most one open `NextAction`; a suggestion is not work;
  starting a step writes the canonical action and links it; `Muuda` keeps the
  link; an unrelated supersession completes nothing;
* **the loop** — `Mida tegid?` finishes the step and may start the next in the
  same save, atomically, never by default;
* **typed steps** — an overview, a consultation and an opinion started from the
  current step finish it in their own operation; the same records from
  `LISA TEEMALE` finish nothing;
* **the deadline** — `Arvamuse tähtaeg` on a new Teema is an obligation and no
  longer a `Koostan arvamuse` step; existing steps are untouched;
* **history** — planning writes audit, never chronology; one act is one row;
* **work surfaces** — a plan alone puts nothing on anybody's list;
* **the boundary** — readers, other Matters, stale tabs, stale plans, closed
  Matters and wrong operations are refused before anything is written.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.dates import format_estonian_date
from app.core.errors import DomainError
from app.matters import work_items as wi
from app.matters.enums import WebsiteOverviewStatus
from app.matters.intake import register_incoming, validate_uploads
from app.matters.locks import CLOSED_MATTER_REFUSAL
from app.matters.models import Entry, Matter, MatterEngagement, MatterWebsiteOverview
from app.matters.services import (
    close_matter,
    complete_engagement_feedback,
    create_imported_matter,
    create_matter,
    plan_website_overview,
    reopen_matter,
)
from app.matters.timeline import TIMELINE_EVENT_TYPES, matter_timeline
from app.matters.workspace import (
    NEXT_IS_THE_CURRENT_STEP,
    OVERVIEW_STEP_NEEDS_A_PUBLICATION,
    PLAN_STEP_NOT_CURRENT,
    PLAN_STEP_WRONG_OPERATION,
    STALE_ACTION_REFUSAL,
    add_matter_engagement,
    add_matter_koda_opinion,
    add_matter_website_overview,
    add_procedural_development,
    change_current_action,
    complete_current_action,
)
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.workflow import plan as work_plan
from app.workflow.enums import (
    ActionKind,
    ActionStatus,
    DateSemantics,
    Disposition,
    PlanStepOperation,
    PlanStepSource,
    PlanStepState,
)
from app.workflow.models import MatterPlanStep, NextAction
from app.workflow.services import (
    OPINION_PREPARATION_TEXT,
    PLAN_STEP_OF_ANOTHER_MATTER,
    establish_opinion_preparation_action,
    set_next_action_for_new_work,
)
from tests import factories

pytestmark = pytest.mark.django_db

STANDARD_TITLES = [
    "Tutvu materjaliga",
    "Koosta kodulehe ülevaade",
    "Kaasa liikmeid / küsi tagasisidet",
    "Koonda tagasiside ja kujunda Koja seisukoht",
    "Saada Koja arvamus",
]
STANDARD_OPERATIONS = [
    PlanStepOperation.GENERIC,
    PlanStepOperation.WEBSITE_OVERVIEW,
    PlanStepOperation.ENGAGEMENT,
    PlanStepOperation.GENERIC,
    PlanStepOperation.SUBMISSION,
]


def _day(offset: int) -> dt.date:
    return timezone.localdate() + dt.timedelta(days=offset)


def _et(day: dt.date) -> str:
    return format_estonian_date(day)


def _pdf(name: str = "Koja_arvamus.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, f"%PDF-1.4 {name}".encode(), content_type="application/pdf")


@pytest.fixture
def matter(specialist):
    return factories.MatterFactory(owner=specialist, stage=None)


@pytest.fixture
def planned(matter, specialist):
    """A Matter carrying the faint standard plan, nothing started."""
    work_plan.seed_standard_plan(matter=matter, actor=specialist)
    return matter


def _steps(matter) -> list[MatterPlanStep]:
    return list(MatterPlanStep.objects.filter(matter=matter).order_by("position", "created_at"))


def _step(matter, key: str) -> MatterPlanStep:
    return MatterPlanStep.objects.get(matter=matter, template_step_key=key)


def _open(matter):
    return NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN)


def _start(matter, key: str, actor, **kwargs) -> NextAction:
    return work_plan.activate_plan_step(
        matter=matter, step=_step(matter, key), actor=actor, **kwargs
    )


def _revision(matter) -> str:
    return work_plan.plan_revision(_steps(matter))


def _teema(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _zone(body: str) -> str:
    start = body.index('id="praegune-tegevus"')
    return body[start : body.index("</section>", start)]


def _plan_zone(body: str) -> str:
    start = body.index('id="tooplaan"')
    return body[start : body.index("</section>", start)]


def _rows(matter, user):
    items, _ = matter_timeline(matter=matter, user=user, limit=200)
    return items


# ---------------------------------------------------------------------------
# 1. The template and its seeding
# ---------------------------------------------------------------------------


def test_the_standard_plan_is_five_faint_suggestions_in_order(planned):
    steps = _steps(planned)

    assert [step.title for step in steps] == STANDARD_TITLES
    assert [step.operation for step in steps] == STANDARD_OPERATIONS
    assert {step.state for step in steps} == {PlanStepState.SUGGESTED}
    assert {step.source for step in steps} == {PlanStepSource.TEMPLATE}
    assert {(step.template_key, step.template_version) for step in steps} == {
        ("standard-legislative", 1)
    }
    assert [step.position for step in steps] == [0, 1, 2, 3, 4]


def test_a_suggestion_is_not_work(planned, specialist):
    assert not _open(planned).exists()
    assert not wi.work_items(specialist)
    assert not wi.undated_actions(specialist).filter(matter=planned).exists()


def test_seeding_twice_adds_nothing(planned, specialist):
    events = ChangeEvent.objects.filter(event_type=ChangeEventType.PLAN_SEEDED).count()

    assert work_plan.seed_standard_plan(matter=planned, actor=specialist) == []

    assert len(_steps(planned)) == 5
    assert ChangeEvent.objects.filter(event_type=ChangeEventType.PLAN_SEEDED).count() == events


def test_the_database_refuses_a_second_copy_of_a_template_step(planned):
    with pytest.raises(IntegrityError), transaction.atomic():
        MatterPlanStep.objects.create(
            matter=planned,
            title="Tutvu materjaliga",
            source=PlanStepSource.TEMPLATE,
            state=PlanStepState.SUGGESTED,
            template_key="standard-legislative",
            template_version=1,
            template_step_key="read-material",
        )


def test_seeding_merges_into_custom_steps_without_reordering_them(matter, specialist):
    custom = work_plan.add_plan_step(
        matter=matter, title="Kohtun ministeeriumiga", actor=specialist
    )

    work_plan.seed_standard_plan(matter=matter, actor=specialist)

    steps = _steps(matter)
    assert steps[0] == custom
    assert [step.title for step in steps[1:]] == STANDARD_TITLES


def test_seeding_only_adds_the_missing_template_steps(planned, specialist):
    # A step skipped is still on the plan, so a second seed does not resurrect it.
    work_plan.skip_plan_step(step=_step(planned, "consult-members"), actor=specialist)

    work_plan.seed_standard_plan(matter=planned, actor=specialist)

    assert len(_steps(planned)) == 5
    assert _step(planned, "consult-members").state == PlanStepState.SKIPPED


def test_uus_teema_seeds_the_plan_and_starts_nothing(signed_in, specialist):
    deadline = _day(90)
    response = signed_in.post(
        reverse("matters:matter_create"),
        {"title": "Kaugete tähtaegadega eelnõu", "response_deadline": _et(deadline)},
    )

    matter = Matter.objects.get(title="Kaugete tähtaegadega eelnõu")
    assert response.status_code == 302
    assert [step.title for step in _steps(matter)] == STANDARD_TITLES
    # The obligation is recorded and no `Koostan arvamuse` step is made from it.
    assert matter.response_deadline == deadline
    assert not _open(matter).exists()
    assert not NextAction.objects.filter(matter=matter).exists()


def test_uus_teema_filed_closed_gets_no_plan(signed_in):
    from app.workflow.models import StageVocabulary

    terminal = StageVocabulary.objects.get(key="monitoring_stopped")
    signed_in.post(
        reverse("matters:matter_create"),
        {"title": "Juba lõppenud teema", "stage": str(terminal.pk)},
    )

    matter = Matter.objects.get(title="Juba lõppenud teema")
    assert not matter.is_open
    assert not _steps(matter)


def test_saabunud_seeds_the_plan_through_its_explicit_seam(signed_in):
    signed_in.post(
        reverse("matters:intake"),
        {"title": "Saabunud eelnõu", "uploads": [_pdf("eelnou.pdf")]},
    )

    matter = Matter.objects.get(title="Saabunud eelnõu")
    assert [step.title for step in _steps(matter)] == STANDARD_TITLES
    assert not _open(matter).exists()


def test_paths_that_record_history_seed_nothing(specialist, pdf_bytes):
    native = create_matter(title="Teenuse kaudu loodud", actor=specialist)
    imported = create_imported_matter(
        title="Registrist imporditud", reference_year=2019, reference_number=7
    )
    archive = factories.ArchiveMatterFactory()
    incoming = register_incoming(
        uploads=validate_uploads([SimpleUploadedFile("a.pdf", pdf_bytes)]),
        title="Ilma plaanita",
        actor=specialist,
    )

    for record in (native, imported, archive, incoming.matter):
        assert not _steps(record)


def test_the_migration_backfills_nothing():
    """The schema migration is additive: no `RunPython`, no plan on any old Matter."""
    from importlib import import_module

    migration = import_module("app.workflow.migrations.0011_matter_plan_step").Migration
    names = {type(operation).__name__ for operation in migration.operations}
    assert "RunPython" not in names
    assert "RunSQL" not in names


def test_an_existing_matter_gains_the_plan_only_when_a_writer_asks(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    step = establish_opinion_preparation_action(
        matter=matter, prepare_by=_day(30), actor=specialist
    )
    body = _teema(signed_in, matter)
    assert not _steps(matter)
    assert "+ Lisa tavapärane tööplaan" in body

    signed_in.post(
        reverse("matters:seed_plan", kwargs={"pk": matter.pk}),
        {"revision": _revision(matter)},
        headers={"HX-Request": "true"},
    )

    assert [s.title for s in _steps(matter)] == STANDARD_TITLES
    # The old `Koostan arvamuse` step is exactly what it was.
    step.refresh_from_db()
    assert step.status == ActionStatus.OPEN
    assert step.text == OPINION_PREPARATION_TEXT
    assert step.plan_step_id is None
    assert list(_open(matter)) == [step]
    assert "+ Lisa tavapärane tööplaan" not in _teema(signed_in, matter)


# ---------------------------------------------------------------------------
# 2. Starting a step
# ---------------------------------------------------------------------------


def test_starting_a_step_writes_the_canonical_action(planned, specialist):
    action = _start(planned, "read-material", specialist)

    assert action.plan_step == _step(planned, "read-material")
    assert action.text == "Tutvu materjaliga"
    assert (action.kind, action.date_semantics) == (ActionKind.DO, DateSemantics.DEADLINE)
    assert action.target_date is None
    assert action.responsible == specialist
    assert list(_open(planned)) == [action]
    assert _step(planned, "read-material").state == PlanStepState.PLANNED
    assert ChangeEvent.objects.filter(
        event_type=ChangeEventType.NEXT_ACTION_SET, object_id=action.pk
    ).exists()


def test_starting_a_step_takes_the_persons_words_and_day(planned, specialist):
    action = _start(
        planned, "read-material", specialist, text="Loen eelnõu läbi", target_date=_day(3)
    )

    assert action.text == "Loen eelnõu läbi"
    assert action.target_date == _day(3)


def test_starting_a_step_never_replaces_the_current_action(planned, specialist):
    current = _start(planned, "read-material", specialist)

    with pytest.raises(DomainError, match="juba praegune tegevus"):
        _start(planned, "website-overview", specialist)

    current.refresh_from_db()
    assert current.status == ActionStatus.OPEN
    assert _step(planned, "website-overview").state == PlanStepState.SUGGESTED


def test_a_custom_step_is_not_work_until_started(planned, specialist):
    custom = work_plan.add_plan_step(
        matter=planned,
        title="Kohtun ministeeriumiga",
        actor=specialist,
        before=_step(planned, "send-opinion"),
    )

    assert custom.state == PlanStepState.PLANNED
    assert custom.source == PlanStepSource.CUSTOM
    assert custom.operation == PlanStepOperation.GENERIC
    assert not _open(planned).exists()
    assert not wi.work_items(specialist)
    assert [step.title for step in _steps(planned)][-2:] == [
        "Kohtun ministeeriumiga",
        "Saada Koja arvamus",
    ]


def test_muuda_keeps_the_plan_relation(planned, specialist):
    first = _start(planned, "read-material", specialist)

    edited = change_current_action(
        matter=planned, actor=specialist, action_id=first.pk, text="Loen läbi", target_date=_day(5)
    )

    first.refresh_from_db()
    assert first.status == ActionStatus.SUPERSEDED
    assert edited.plan_step_id == first.plan_step_id
    assert _step(planned, "read-material").state == PlanStepState.PLANNED


def test_muuda_from_a_stale_tab_refuses_and_carries_nothing(planned, specialist):
    first = _start(planned, "read-material", specialist)
    complete_current_action(matter=planned, author=specialist, action_id=first.pk, body="Tehtud.")
    second = _start(planned, "website-overview", specialist)

    with pytest.raises(DomainError, match="vahepeal muutunud"):
        change_current_action(
            matter=planned, actor=specialist, action_id=first.pk, text="Vana sakk"
        )

    assert list(_open(planned)) == [second]


def test_muuda_through_the_page_posts_its_step_and_keeps_the_relation(
    signed_in, planned, specialist
):
    first = _start(planned, "read-material", specialist)
    body = _zone(_teema(signed_in, planned))
    assert f'name="action_id" value="{first.pk}"' in body

    signed_in.post(
        reverse("matters:set_action", kwargs={"pk": planned.pk}),
        {"text": "Loen eelnõu läbi", "target_date": _et(_day(2)), "action_id": str(first.pk)},
        headers={"HX-Request": "true"},
    )

    edited = _open(planned).get()
    assert edited.text == "Loen eelnõu läbi"
    assert edited.plan_step_id == first.plan_step_id


def test_unrelated_work_superseding_the_step_does_not_complete_it(planned, specialist):
    first = _start(planned, "read-material", specialist)

    # `+ Märge` dated ahead with `Märgi järgmiseks tegevuseks` is new work.
    add_procedural_development(
        matter=planned,
        author=specialist,
        title="Kohtumine ministeeriumiga",
        occurred_on=_day(4),
        as_next_step=True,
    )

    first.refresh_from_db()
    assert first.status == ActionStatus.SUPERSEDED
    assert _open(planned).get().plan_step_id is None
    step = _step(planned, "read-material")
    assert step.state == PlanStepState.PLANNED
    assert step.completed_at is None


# ---------------------------------------------------------------------------
# 3. Done → next
# ---------------------------------------------------------------------------


def test_mida_tegid_finishes_the_step(planned, specialist):
    first = _start(planned, "read-material", specialist)

    result = complete_current_action(
        matter=planned, author=specialist, action_id=first.pk, body="Lugesin materjali läbi."
    )

    first.refresh_from_db()
    step = _step(planned, "read-material")
    assert first.status == ActionStatus.COMPLETED
    assert step.state == PlanStepState.COMPLETED
    assert step.completed_by == specialist
    assert step.completed_at == first.ended_at
    assert result.action is None
    assert not _open(planned).exists()
    assert Entry.objects.get(matter=planned).body == "Lugesin materjali läbi."


def test_done_and_next_in_one_save(planned, specialist):
    first = _start(planned, "read-material", specialist)

    result = complete_current_action(
        matter=planned,
        author=specialist,
        action_id=first.pk,
        body="Lugesin materjali läbi.",
        next_step_id=_step(planned, "website-overview").pk,
        next_date=_day(7),
    )

    assert _step(planned, "read-material").state == PlanStepState.COMPLETED
    following = _open(planned).get()
    assert following == result.action
    assert following.plan_step == _step(planned, "website-overview")
    assert following.text == "Koosta kodulehe ülevaade"
    assert following.target_date == _day(7)
    # One operation: the note, the completion and the next step.
    acts = (
        (ChangeEventType.ENTRY_ADDED, result.entry.pk),
        (ChangeEventType.NEXT_ACTION_COMPLETED, first.pk),
        (ChangeEventType.NEXT_ACTION_SET, following.pk),
    )
    operations = {
        ChangeEvent.objects.get(event_type=event_type, object_id=pk).operation_id
        for event_type, pk in acts
    }
    assert operations == {result.operation_id}


def test_muu_tegevus_is_an_ordinary_step(planned, specialist):
    first = _start(planned, "read-material", specialist)

    complete_current_action(
        matter=planned,
        author=specialist,
        action_id=first.pk,
        body="Lugesin.",
        next_text="Kohtun ministeeriumiga",
    )

    following = _open(planned).get()
    assert following.text == "Kohtun ministeeriumiga"
    assert following.plan_step_id is None
    assert following.target_date is None


def test_the_next_suggestion_is_never_started_for_the_person(planned, specialist):
    first = _start(planned, "read-material", specialist)

    complete_current_action(matter=planned, author=specialist, action_id=first.pk, body="Tehtud.")

    assert not _open(planned).exists()
    assert _step(planned, "website-overview").state == PlanStepState.SUGGESTED


def test_a_stale_next_step_refuses_the_whole_save(planned, specialist):
    first = _start(planned, "read-material", specialist)
    gone = _step(planned, "website-overview")
    work_plan.skip_plan_step(step=gone, actor=specialist)

    with pytest.raises(DomainError, match=re.escape(work_plan.STEP_NOT_OPEN)):
        complete_current_action(
            matter=planned,
            author=specialist,
            action_id=first.pk,
            body="Lugesin.",
            next_step_id=gone.pk,
        )

    first.refresh_from_db()
    assert first.status == ActionStatus.OPEN
    assert _step(planned, "read-material").state == PlanStepState.PLANNED
    assert not Entry.objects.filter(matter=planned).exists()


def test_the_step_being_finished_is_not_the_next_one(planned, specialist):
    first = _start(planned, "read-material", specialist)

    with pytest.raises(DomainError, match=NEXT_IS_THE_CURRENT_STEP):
        complete_current_action(
            matter=planned,
            author=specialist,
            action_id=first.pk,
            body="Lugesin.",
            next_step_id=first.plan_step_id,
        )
    assert not Entry.objects.filter(matter=planned).exists()


def test_a_step_of_another_matter_is_refused_as_next(planned, specialist):
    other = factories.MatterFactory(owner=specialist)
    work_plan.seed_standard_plan(matter=other, actor=specialist)
    first = _start(planned, "read-material", specialist)

    with pytest.raises(DomainError, match=re.escape(work_plan.STEP_NOT_ON_MATTER)):
        complete_current_action(
            matter=planned,
            author=specialist,
            action_id=first.pk,
            body="Lugesin.",
            next_step_id=_step(other, "website-overview").pk,
        )
    assert not Entry.objects.filter(matter=planned).exists()
    assert not _open(other).exists()


def test_a_stale_tab_finishes_nothing(planned, specialist):
    first = _start(planned, "read-material", specialist)
    complete_current_action(
        matter=planned,
        author=specialist,
        action_id=first.pk,
        body="Esimene sakk.",
        next_step_id=_step(planned, "website-overview").pk,
    )
    entries = Entry.objects.filter(matter=planned).count()

    with pytest.raises(DomainError, match=STALE_ACTION_REFUSAL):
        complete_current_action(
            matter=planned,
            author=specialist,
            action_id=first.pk,
            body="Teine sakk.",
            next_step_id=_step(planned, "consult-members").pk,
        )

    assert Entry.objects.filter(matter=planned).count() == entries
    assert _open(planned).get().plan_step == _step(planned, "website-overview")
    assert _step(planned, "consult-members").state == PlanStepState.SUGGESTED


def test_the_page_posts_done_and_next(signed_in, planned, specialist):
    first = _start(planned, "read-material", specialist)
    zone = _zone(_teema(signed_in, planned))
    assert "✓ Tehtud" in zone
    assert "Järgmisena" in zone
    assert "Praegu ei määra" in zone
    assert "Lisa märge" in zone

    response = signed_in.post(
        reverse("matters:complete_current_action", kwargs={"pk": planned.pk}),
        {
            "action_id": str(first.pk),
            "body": "Lugesin materjali läbi.",
            "next_choice": str(_step(planned, "website-overview").pk),
            "next_date": "",
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert _open(planned).get().plan_step == _step(planned, "website-overview")


def test_muu_tegevus_without_words_is_refused_on_the_box(signed_in, planned, specialist):
    first = _start(planned, "read-material", specialist)

    response = signed_in.post(
        reverse("matters:complete_current_action", kwargs={"pk": planned.pk}),
        {"action_id": str(first.pk), "body": "Lugesin.", "next_choice": "muu"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 400
    assert "Kirjuta järgmine tegevus." in response.content.decode()
    first.refresh_from_db()
    assert first.status == ActionStatus.OPEN


def test_a_result_is_still_required(signed_in, planned, specialist):
    first = _start(planned, "read-material", specialist)

    response = signed_in.post(
        reverse("matters:complete_current_action", kwargs={"pk": planned.pk}),
        {"action_id": str(first.pk), "body": "", "next_choice": ""},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 400
    first.refresh_from_db()
    assert first.status == ActionStatus.OPEN


def test_an_ordinary_action_completes_as_before(matter, specialist):
    action = set_next_action_for_new_work(matter=matter, text="Helistan", actor=specialist)

    complete_current_action(
        matter=matter, author=specialist, action_id=action.pk, body="Helistasin."
    )

    action.refresh_from_db()
    assert action.status == ActionStatus.COMPLETED
    assert not MatterPlanStep.objects.filter(matter=matter).exists()


# ---------------------------------------------------------------------------
# 4. Typed steps
# ---------------------------------------------------------------------------


def _current(matter, key, actor):
    return _start(matter, key, actor), _step(matter, key)


def test_an_overview_published_from_its_step_finishes_it(planned, specialist):
    action, step = _current(planned, "website-overview", specialist)

    result = add_matter_website_overview(
        matter=planned,
        author=specialist,
        url="https://www.koda.ee/uudised/eelnou",
        published_on=timezone.localdate(),
        plan_action_id=action.pk,
        plan_step_id=step.pk,
    )

    action.refresh_from_db()
    step.refresh_from_db()
    assert result.record.status == WebsiteOverviewStatus.PUBLISHED
    assert action.status == ActionStatus.COMPLETED
    assert step.state == PlanStepState.COMPLETED
    assert not Entry.objects.filter(matter=planned).exists()
    rows = _rows(planned, specialist)
    overview_rows = [row for row in rows if row.website_overview is not None]
    assert len(overview_rows) == 1
    assert overview_rows[0].completed_step is not None
    assert not [
        row
        for row in rows
        if row.record is None
        and any(event.event_type == ChangeEventType.NEXT_ACTION_COMPLETED for event in row.events)
    ]


def test_a_plan_finishes_no_overview_step(planned, specialist):
    action, step = _current(planned, "website-overview", specialist)

    plan_website_overview(matter=planned, actor=specialist)
    with pytest.raises(DomainError, match=OVERVIEW_STEP_NEEDS_A_PUBLICATION):
        add_matter_website_overview(
            matter=planned, author=specialist, plan_action_id=action.pk, plan_step_id=step.pk
        )

    action.refresh_from_db()
    assert action.status == ActionStatus.OPEN
    assert _step(planned, "website-overview").state == PlanStepState.PLANNED
    assert MatterWebsiteOverview.objects.filter(matter=planned).count() == 1


def test_an_overview_from_lisa_teemale_finishes_nothing(planned, specialist):
    action, _ = _current(planned, "website-overview", specialist)

    add_matter_website_overview(
        matter=planned, author=specialist, url="https://www.koda.ee/x", published_on=None
    )

    action.refresh_from_db()
    assert action.status == ActionStatus.OPEN
    assert _step(planned, "website-overview").state == PlanStepState.PLANNED


def test_a_consultation_asked_from_its_step_finishes_it_and_stays_open(planned, specialist):
    action, step = _current(planned, "consult-members", specialist)

    result = add_matter_engagement(
        matter=planned,
        author=specialist,
        audience="Liikmed",
        occurred_on=timezone.localdate(),
        plan_action_id=action.pk,
        plan_step_id=step.pk,
    )

    action.refresh_from_db()
    step.refresh_from_db()
    engagement = result.record
    assert action.status == ActionStatus.COMPLETED
    assert step.state == PlanStepState.COMPLETED
    assert engagement.lifecycle_tracked
    assert engagement.feedback_closed_at is None
    assert engagement.feedback_deadline is None
    # No «Ootan tagasisidet» step is invented for the wait.
    assert not _open(planned).exists()
    assert wi.undated_feedback_waits(specialist).filter(pk=engagement.pk).exists()
    rounds = [row for row in _rows(planned, specialist) if row.is_engagement]
    assert len(rounds) == 1
    assert rounds[0].completed_step is not None

    # Finishing the round invents no step either, and starts nothing.
    complete_engagement_feedback(engagement=engagement, actor=specialist)
    assert not _open(planned).exists()
    assert _step(planned, "form-position").state == PlanStepState.SUGGESTED


def test_an_opinion_sent_from_its_step_finishes_it(planned, specialist, organisation):
    action, step = _current(planned, "send-opinion", specialist)

    result = add_matter_koda_opinion(
        matter=planned,
        author=specialist,
        upload=_pdf(),
        recipients=[organisation],
        sent_on=timezone.localdate(),
        complete_action_id=action.pk,
        plan_step_id=step.pk,
    )

    action.refresh_from_db()
    step.refresh_from_db()
    assert result.record.status == SubmissionStatus.SENT
    assert action.status == ActionStatus.COMPLETED
    assert step.state == PlanStepState.COMPLETED
    sends = [row for row in _rows(planned, specialist) if row.submission is not None]
    assert len(sends) == 1
    assert sends[0].completed_step is not None


def test_a_typed_save_of_the_wrong_operation_writes_nothing(planned, specialist, organisation):
    action, step = _current(planned, "read-material", specialist)

    with pytest.raises(DomainError, match=PLAN_STEP_WRONG_OPERATION):
        add_matter_koda_opinion(
            matter=planned,
            author=specialist,
            upload=_pdf(),
            recipients=[organisation],
            sent_on=timezone.localdate(),
            complete_action_id=action.pk,
            plan_step_id=step.pk,
        )

    assert not Submission.objects.filter(matter=planned).exists()
    action.refresh_from_db()
    assert action.status == ActionStatus.OPEN


def test_a_typed_save_for_a_step_that_is_not_current_writes_nothing(planned, specialist):
    action, _ = _current(planned, "consult-members", specialist)
    other = _step(planned, "website-overview")

    with pytest.raises(DomainError, match=PLAN_STEP_NOT_CURRENT):
        add_matter_website_overview(
            matter=planned,
            author=specialist,
            url="https://www.koda.ee/x",
            plan_action_id=action.pk,
            plan_step_id=other.pk,
        )

    assert not MatterWebsiteOverview.objects.filter(matter=planned).exists()


def test_a_typed_save_from_a_stale_step_writes_nothing(planned, specialist):
    action, step = _current(planned, "consult-members", specialist)
    complete_current_action(matter=planned, author=specialist, action_id=action.pk, body="Tehtud.")

    with pytest.raises(DomainError, match=STALE_ACTION_REFUSAL):
        add_matter_engagement(
            matter=planned,
            author=specialist,
            audience="Liikmed",
            plan_action_id=action.pk,
            plan_step_id=step.pk,
        )

    assert not MatterEngagement.objects.filter(matter=planned).exists()


def test_the_typed_form_is_drawn_under_its_step_only(signed_in, planned, specialist):
    _start(planned, "consult-members", specialist)

    zone = _zone(_teema(signed_in, planned))

    assert 'id="samm-toiming"' in zone
    assert "+ Kaasamine" in zone
    assert 'name="plan_step"' in zone


def test_the_typed_page_post_finishes_the_step(signed_in, planned, specialist):
    action, step = _current(planned, "website-overview", specialist)

    response = signed_in.post(
        reverse("matters:add_website_overview", kwargs={"pk": planned.pk}),
        {
            "url": "https://www.koda.ee/uudised/eelnou",
            "published_on": _et(timezone.localdate()),
            "plan_action": str(action.pk),
            "plan_step": str(step.pk),
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    step.refresh_from_db()
    assert step.state == PlanStepState.COMPLETED


def test_a_typed_post_naming_another_matters_ids_is_a_404(signed_in, planned, specialist):
    other = factories.MatterFactory(owner=specialist)
    work_plan.seed_standard_plan(matter=other, actor=specialist)
    foreign = _start(other, "website-overview", specialist)

    response = signed_in.post(
        reverse("matters:add_website_overview", kwargs={"pk": planned.pk}),
        {
            "url": "https://www.koda.ee/x",
            "published_on": _et(timezone.localdate()),
            "plan_action": str(foreign.pk),
            "plan_step": str(foreign.plan_step_id),
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 404
    assert not MatterWebsiteOverview.objects.exists()


def test_the_opinion_tick_on_a_plan_backed_action_finishes_its_step(
    planned, specialist, organisation
):
    """ADR 0126's explicit tick still works, and finishing the action finishes its step."""
    action, step = _current(planned, "send-opinion", specialist)

    add_matter_koda_opinion(
        matter=planned,
        author=specialist,
        upload=_pdf(),
        recipients=[organisation],
        sent_on=timezone.localdate(),
        complete_action_id=action.pk,
    )

    step.refresh_from_db()
    assert step.state == PlanStepState.COMPLETED


# ---------------------------------------------------------------------------
# 5. Shaping the plan
# ---------------------------------------------------------------------------


def test_skip_and_restore(planned, specialist):
    step = _step(planned, "consult-members")

    work_plan.skip_plan_step(step=step, actor=specialist, expected_revision=_revision(planned))
    step.refresh_from_db()
    assert step.state == PlanStepState.SKIPPED
    assert step.skipped_by == specialist
    assert not MatterEngagement.objects.filter(matter=planned).exists()

    work_plan.restore_plan_step(step=step, actor=specialist, expected_revision=_revision(planned))
    step.refresh_from_db()
    assert step.state == PlanStepState.PLANNED
    assert step.skipped_at is None
    assert ChangeEvent.objects.filter(event_type=ChangeEventType.PLAN_STEP_SKIPPED).exists()
    assert ChangeEvent.objects.filter(event_type=ChangeEventType.PLAN_STEP_RESTORED).exists()


def test_the_current_step_cannot_be_skipped_moved_or_edited(planned, specialist):
    action, step = _current(planned, "read-material", specialist)

    for act in (
        lambda: work_plan.skip_plan_step(step=step, actor=specialist),
        lambda: work_plan.move_plan_step(step=step, direction="down", actor=specialist),
        lambda: work_plan.edit_plan_step(
            step=step, title="X", operation=PlanStepOperation.GENERIC, actor=specialist
        ),
    ):
        with pytest.raises(DomainError, match=re.escape(work_plan.STEP_IS_CURRENT)):
            act()

    action.refresh_from_db()
    assert action.status == ActionStatus.OPEN


def test_a_completed_step_is_history(planned, specialist):
    action, step = _current(planned, "read-material", specialist)
    complete_current_action(matter=planned, author=specialist, action_id=action.pk, body="Tehtud.")

    for act in (
        lambda: work_plan.skip_plan_step(step=step, actor=specialist),
        lambda: work_plan.edit_plan_step(
            step=step, title="Muu", operation=PlanStepOperation.GENERIC, actor=specialist
        ),
        lambda: work_plan.activate_plan_step(matter=planned, step=step, actor=specialist),
    ):
        with pytest.raises(DomainError, match=re.escape(work_plan.STEP_NOT_OPEN)):
            act()

    step.refresh_from_db()
    assert step.state == PlanStepState.COMPLETED


def test_move_reorders_only_the_steps_ahead(planned, specialist):
    _current(planned, "read-material", specialist)

    work_plan.move_plan_step(
        step=_step(planned, "consult-members"),
        direction="up",
        actor=specialist,
        expected_revision=_revision(planned),
    )

    assert [step.template_step_key for step in _steps(planned)] == [
        "read-material",
        "consult-members",
        "website-overview",
        "form-position",
        "send-opinion",
    ]
    # The first step ahead cannot go above the current one.
    with pytest.raises(DomainError, match=re.escape(work_plan.STEP_CANNOT_MOVE)):
        work_plan.move_plan_step(
            step=_step(planned, "consult-members"), direction="up", actor=specialist
        )


def test_a_stale_plan_revision_refuses(planned, specialist):
    stale = _revision(planned)
    work_plan.move_plan_step(
        step=_step(planned, "send-opinion"),
        direction="up",
        actor=specialist,
        expected_revision=stale,
    )

    with pytest.raises(DomainError, match="vahepeal muudetud"):
        work_plan.skip_plan_step(
            step=_step(planned, "read-material"), actor=specialist, expected_revision=stale
        )
    with pytest.raises(DomainError, match="vahepeal muudetud"):
        work_plan.add_plan_step(
            matter=planned, title="Uus", actor=specialist, expected_revision=stale
        )
    assert _step(planned, "read-material").state == PlanStepState.SUGGESTED
    assert len(_steps(planned)) == 5


def test_editing_a_step_stores_exactly_what_was_chosen(planned, specialist):
    step = _step(planned, "website-overview")

    work_plan.edit_plan_step(
        step=step,
        title="Kohtun ministeeriumiga",
        operation=PlanStepOperation.GENERIC,
        actor=specialist,
    )

    step.refresh_from_db()
    assert (step.title, step.operation, step.state) == (
        "Kohtun ministeeriumiga",
        PlanStepOperation.GENERIC,
        PlanStepState.PLANNED,
    )
    # Provenance stays: the step was copied from the template.
    assert step.source == PlanStepSource.TEMPLATE


def test_repeat_creates_a_new_occurrence(planned, specialist):
    action, first = _current(planned, "consult-members", specialist)
    add_matter_engagement(
        matter=planned,
        author=specialist,
        audience="Liikmed",
        plan_action_id=action.pk,
        plan_step_id=first.pk,
    )

    again = work_plan.repeat_plan_step(step=first, actor=specialist)

    first.refresh_from_db()
    assert first.state == PlanStepState.COMPLETED
    assert again.pk != first.pk
    assert (again.title, again.operation, again.state, again.source) == (
        first.title,
        PlanStepOperation.ENGAGEMENT,
        PlanStepState.PLANNED,
        PlanStepSource.CUSTOM,
    )
    second_action = work_plan.activate_plan_step(matter=planned, step=again, actor=specialist)
    add_matter_engagement(
        matter=planned,
        author=specialist,
        audience="Liikmed uuesti",
        plan_action_id=second_action.pk,
        plan_step_id=again.pk,
    )
    assert MatterEngagement.objects.filter(matter=planned).count() == 2
    assert (
        MatterPlanStep.objects.filter(
            matter=planned, operation=PlanStepOperation.ENGAGEMENT, state=PlanStepState.COMPLETED
        ).count()
        == 2
    )


def test_only_a_completed_step_repeats(planned, specialist):
    with pytest.raises(DomainError, match=re.escape(work_plan.ONLY_COMPLETED_REPEATS)):
        work_plan.repeat_plan_step(step=_step(planned, "read-material"), actor=specialist)


def test_a_title_is_required(planned, specialist):
    with pytest.raises(DomainError, match=re.escape(work_plan.STEP_NEEDS_TITLE)):
        work_plan.add_plan_step(matter=planned, title="   ", actor=specialist)
    with pytest.raises(IntegrityError), transaction.atomic():
        MatterPlanStep.objects.create(matter=planned, title="")


@pytest.mark.parametrize(
    "fields",
    [
        {"state": "BOGUS"},
        {"source": "BOGUS"},
        {"operation": "BOGUS"},
        {"state": PlanStepState.COMPLETED},
        {"state": PlanStepState.SKIPPED},
        {"source": PlanStepSource.TEMPLATE},
        {"template_key": "standard-legislative"},
    ],
)
def test_the_database_refuses_an_inconsistent_step(matter, fields):
    with pytest.raises(IntegrityError), transaction.atomic():
        MatterPlanStep.objects.create(matter=matter, title="Samm", **fields)


# ---------------------------------------------------------------------------
# 6. History: audit, not chronology
# ---------------------------------------------------------------------------


def test_planning_writes_no_chronology_row(matter, specialist):
    before = len(_rows(matter, specialist))

    work_plan.seed_standard_plan(matter=matter, actor=specialist)
    work_plan.move_plan_step(step=_step(matter, "send-opinion"), direction="up", actor=specialist)
    work_plan.skip_plan_step(step=_step(matter, "consult-members"), actor=specialist)
    work_plan.add_plan_step(matter=matter, title="Kohtun ministeeriumiga", actor=specialist)
    _start(matter, "read-material", specialist)

    assert len(_rows(matter, specialist)) == before
    planning = {
        ChangeEventType.PLAN_SEEDED,
        ChangeEventType.PLAN_STEP_MOVED,
        ChangeEventType.PLAN_STEP_SKIPPED,
        ChangeEventType.PLAN_STEP_ADDED,
        ChangeEventType.PLAN_STEP_ACTIVATED,
    }
    assert planning <= set(
        ChangeEvent.objects.filter(matter=matter).values_list("event_type", flat=True)
    )
    assert not planning & set(TIMELINE_EVENT_TYPES)


def test_a_generic_completion_reads_as_the_result(planned, specialist):
    first = _start(planned, "read-material", specialist)
    complete_current_action(
        matter=planned,
        author=specialist,
        action_id=first.pk,
        body="Lugesin materjali läbi.",
        next_step_id=_step(planned, "website-overview").pk,
    )

    rows = _rows(planned, specialist)
    notes = [row for row in rows if row.is_entry]
    assert len(notes) == 1
    assert "märkis eelmise sammu tehtuks" in notes[0].summary_verbs
    # The completion and the next step read on the note's row. The only other
    # row is the one `NEXT_ACTION_SET` has always drawn for a step set on its
    # own — here, starting the first step — exactly as `+ Määra järgmine
    # tegevus` draws it: existing semantics, and no planning noise.
    others = [row for row in rows if not row.is_entry and row.milestone is None]
    assert [{event.event_type for event in row.events} for row in others] == [
        {ChangeEventType.NEXT_ACTION_SET}
    ]


def test_the_change_log_shows_planning(signed_in, planned):
    body = signed_in.get(reverse("matters:matter_changes", kwargs={"pk": planned.pk})).content
    assert "Tavapärane tööplaan lisatud" in body.decode()


def test_teema_kaik_on_the_page_has_no_planning_rows(signed_in, planned, specialist):
    work_plan.skip_plan_step(step=_step(planned, "consult-members"), actor=specialist)

    body = _teema(signed_in, planned)
    kaik = body[body.index('id="ajajoon"') :]

    assert "Kaasa liikmeid" not in kaik
    assert "tööplaan" not in kaik.lower()


# ---------------------------------------------------------------------------
# 7. Closing, reopening and stage periods
# ---------------------------------------------------------------------------


def test_closing_completes_nothing_and_reopening_starts_nothing(signed_in, planned, specialist):
    action = _start(planned, "read-material", specialist)

    close_matter(matter=planned, disposition=Disposition.MONITORING_STOPPED, actor=specialist)

    action.refresh_from_db()
    assert action.status == ActionStatus.CANCELLED
    assert not MatterPlanStep.objects.filter(matter=planned, state=PlanStepState.COMPLETED).exists()
    assert _step(planned, "read-material").state == PlanStepState.PLANNED
    body = _teema(signed_in, planned)
    plan = _plan_zone(body)
    assert "Tutvu materjaliga" in plan
    assert "Alusta" not in plan
    assert "Muuda plaani" not in plan
    assert "+ Lisa samm" not in plan

    planned.refresh_from_db()
    reopen_matter(matter=planned, actor=specialist)

    assert not _open(planned).exists()
    assert len(_steps(planned)) == 5
    assert "Alusta" in _plan_zone(_teema(signed_in, planned))


def test_a_closed_matter_refuses_planning(planned, specialist):
    close_matter(matter=planned, disposition=Disposition.MONITORING_STOPPED, actor=specialist)

    for act in (
        lambda: _start(planned, "read-material", specialist),
        lambda: work_plan.add_plan_step(matter=planned, title="Uus", actor=specialist),
        lambda: work_plan.skip_plan_step(step=_step(planned, "read-material"), actor=specialist),
    ):
        with pytest.raises(DomainError, match=re.escape(CLOSED_MATTER_REFUSAL)):
            act()


def test_a_stage_change_moves_no_step(planned, specialist, stage):
    from app.matters.services import change_stage

    _start(planned, "read-material", specialist)
    before = [(step.pk, step.state, step.position) for step in _steps(planned)]

    change_stage(matter=planned, stage=stage, actor=specialist)

    assert [(step.pk, step.state, step.position) for step in _steps(planned)] == before


# ---------------------------------------------------------------------------
# 8. Work surfaces
# ---------------------------------------------------------------------------


def test_a_plan_alone_puts_nothing_on_a_work_list(signed_in, matter, specialist):
    items_before = wi.work_items(specialist)
    undated_before = list(wi.undated_actions(specialist))
    page_before = signed_in.get(reverse("matters:my_work")).content.decode()

    work_plan.seed_standard_plan(matter=matter, actor=specialist)
    work_plan.add_plan_step(matter=matter, title="Kohtun ministeeriumiga", actor=specialist)

    assert wi.work_items(specialist) == items_before
    assert list(wi.undated_actions(specialist)) == undated_before
    page_after = signed_in.get(reverse("matters:my_work")).content.decode()
    for title in (*STANDARD_TITLES, "Kohtun ministeeriumiga"):
        assert title not in page_after
    assert page_before.count(matter.title) == page_after.count(matter.title)


def test_a_started_step_is_work_like_any_other(planned, specialist):
    action = _start(planned, "read-material", specialist, target_date=_day(2))

    items = wi.work_items(specialist)
    assert [(item.source_type, item.object_id) for item in items] == [
        (items[0].source_type, action.pk)
    ]


def test_a_far_deadline_stays_the_obligation_beside_the_current_step(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist, response_deadline=_day(90))
    work_plan.seed_standard_plan(matter=matter, actor=specialist)
    _start(matter, "read-material", specialist, target_date=_day(5))

    zone = _zone(_teema(signed_in, matter))

    assert "Tutvu materjaliga" in zone
    assert "Arvamuse tähtaeg" in zone


# ---------------------------------------------------------------------------
# 9. The boundary
# ---------------------------------------------------------------------------

PLAN_POSTS = (
    ("matters:seed_plan", False),
    ("matters:add_plan_step", False),
    ("matters:start_plan_step", True),
    ("matters:skip_plan_step", True),
    ("matters:restore_plan_step", True),
    ("matters:repeat_plan_step", True),
    ("matters:move_plan_step", True),
    ("matters:edit_plan_step", True),
)


@pytest.mark.parametrize(("name", "takes_step"), PLAN_POSTS)
def test_a_reader_cannot_change_the_plan(client, reader, planned, name, takes_step):
    client.force_login(reader)
    kwargs = {"pk": planned.pk}
    if takes_step:
        kwargs["step_id"] = _step(planned, "read-material").pk
    before = [(step.pk, step.state, step.position, step.title) for step in _steps(planned)]

    response = client.post(
        reverse(name, kwargs=kwargs),
        {"title": "Uus", "revision": _revision(planned), "direction": "down"},
    )

    assert response.status_code in (302, 403, 404)
    assert [(s.pk, s.state, s.position, s.title) for s in _steps(planned)] == before
    assert not _open(planned).exists()


def test_a_reader_sees_the_plan_without_controls(client, reader, planned):
    client.force_login(reader)

    plan = _plan_zone(_teema(client, planned))

    assert "Tutvu materjaliga" in plan
    assert "Alusta" not in plan
    assert "Muuda plaani" not in plan


@pytest.mark.parametrize(("name", "takes_step"), PLAN_POSTS)
def test_a_restricted_matters_plan_does_not_exist_for_an_outsider(
    client, reader, restricted_matter, specialist, name, takes_step
):
    work_plan.seed_standard_plan(matter=restricted_matter, actor=specialist)
    client.force_login(reader)
    kwargs = {"pk": restricted_matter.pk}
    if takes_step:
        kwargs["step_id"] = _step(restricted_matter, "read-material").pk

    response = client.post(reverse(name, kwargs=kwargs), {"revision": "x", "title": "Uus"})

    assert response.status_code == 404
    assert len(_steps(restricted_matter)) == 5


def test_a_step_of_another_matter_is_a_404(signed_in, planned, specialist):
    other = factories.MatterFactory(owner=specialist)
    work_plan.seed_standard_plan(matter=other, actor=specialist)

    response = signed_in.post(
        reverse(
            "matters:start_plan_step",
            kwargs={"pk": planned.pk, "step_id": _step(other, "read-material").pk},
        ),
        {},
    )

    assert response.status_code == 404
    assert not _open(other).exists()
    assert not _open(planned).exists()


def test_a_stale_editor_is_refused_on_the_page(signed_in, planned, specialist):
    stale = _revision(planned)
    work_plan.skip_plan_step(step=_step(planned, "send-opinion"), actor=specialist)

    response = signed_in.post(
        reverse(
            "matters:move_plan_step",
            kwargs={"pk": planned.pk, "step_id": _step(planned, "consult-members").pk},
        ),
        {"revision": stale, "direction": "up"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 400
    assert "Tööplaani on vahepeal muudetud" in response.content.decode()
    assert [step.template_step_key for step in _steps(planned)][1] == "website-overview"


def test_the_page_controls_post_the_current_revision(signed_in, planned):
    body = _teema(signed_in, planned)

    assert f'name="revision" value="{_revision(planned)}"' in body


def test_a_plan_step_cannot_be_linked_across_matters(planned, specialist):
    other = factories.MatterFactory(owner=specialist)

    with pytest.raises(DomainError, match=re.escape(PLAN_STEP_OF_ANOTHER_MATTER)):
        set_next_action_for_new_work(
            matter=other,
            text="Vale teema",
            actor=specialist,
            plan_step=_step(planned, "read-material"),
        )
    assert not _open(other).exists()
