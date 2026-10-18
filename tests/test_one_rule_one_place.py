"""One business rule, one spelling (JARCH-002, JDATA-005; 2026-10-17 audit).

«Is this round's wait open» was written out at nine call sites, each with its
own extra clauses, and the two cutover planners had forgotten that a record a
lawyer removed («Kustuta») is not on the file. The rule now has one spelling
per layer — `MatterEngagement.has_open_feedback_wait` on a row,
`MatterEngagementQuerySet.open_waits()` / `answerable()` in SQL, and
`RemovableQuerySet.live()` for «not removed» where no reader is involved —
and every call site keeps its own extra clauses beside the call.

Three kinds of proof live here:

* **Nothing moved.** Each rewritten site is compared, over a world holding
  every combination of tracked / finished / removed / restricted rounds on open
  and closed `FULL` and `ARCHIVE` Matters, against the expression it used to
  spell out — copied here verbatim as the reference.
* **The behaviour fix.** A removed open round, plan or record no longer holds a
  Matter back in either cutover planner (JDATA-005).
* **The overview step reads the kind the page shows.** A legacy row with no
  stored kind on a koda.ee *news* address is shown as «Uudis»; it no longer
  counts as the published «Ülevaade» a «Koosta kodulehe ülevaade» step asks
  for (docs/adr/0161, amendment of 2026-10-18).

Synthetic data only.
"""

from __future__ import annotations

import datetime as dt
import itertools
from dataclasses import dataclass, field
from typing import Any

import pytest
from django.apps import apps
from django.db.models import Exists, OuterRef
from django.utils import timezone

from app.core.enums import Visibility
from app.core.invariants import _closed_matter_findings
from app.core.models import RemovableRecord
from app.legacy_import import historical_cutover
from app.legacy_import.final_cutover import Action, native_activity
from app.legacy_import.final_cutover import ReviewReason as FinalReviewReason
from app.legacy_import.final_cutover import build_cutover_plan as build_final_plan
from app.matters import services
from app.matters.closure_work import live_work_at_closure
from app.matters.engagement_replies import answerable_rounds, finish_wait_on_reply
from app.matters.enums import (
    EngagementKind,
    RecordMode,
    WebsiteOverviewKind,
    WebsiteOverviewStatus,
)
from app.matters.models import Entry, Matter, MatterEngagement, MatterWebsiteOverview
from app.matters.next_step import without_next_step
from app.matters.plan_view import RecordedWork
from app.matters.publication_kind import overview_fulfils_step
from app.matters.removal import remove_matter_record
from app.matters.work_items import open_feedback_waits
from app.matters.workspace import ROUND_NOT_OPEN, _named_open_rounds
from app.workflow.enums import Disposition
from tests import factories
from tests.refusals import refused
from tests.synthetic_cutover import (
    FINAL_SNAPSHOT,
    RETIRING_NO_PLANS,
    approve_snapshot,
    build_world,
)
from tests.test_historical_cutover import register_matter

pytestmark = pytest.mark.django_db

KODA_NEWS = "https://www.koda.ee/et/uudised/sunteetiline-uudis"
KODA_OVERVIEW = "https://www.koda.ee/et/meie-moju/hetkel-kasil/sunteetiline-ulevaade"
ELSEWHERE = "https://example.invalid/sunteetiline-kirjutis"


# =========================================================================
# A world with every combination
# =========================================================================

#: (lifecycle_tracked, finished) — the three states the database allows.
ROUND_STATES = ((False, False), (True, False), (True, True))


@dataclass
class World:
    people: dict[str, Any]
    matters: dict[str, Matter]
    rounds: list[MatterEngagement] = field(default_factory=list)
    plans: list[MatterWebsiteOverview] = field(default_factory=list)


def _round(matter: Matter, *, tracked: bool, finished: bool, removed: bool, restricted: bool):
    now = timezone.now()
    return MatterEngagement.objects.create(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="Sünteetiline ring",
        lifecycle_tracked=tracked,
        feedback_closed_at=now if finished else None,
        removed_at=now if removed else None,
        visibility_override=Visibility.RESTRICTED if restricted else "",
    )


@pytest.fixture
def world(db) -> World:
    owner = factories.UserFactory()
    people = {
        "owner": owner,
        "colleague": factories.UserFactory(),
        "reader": factories.ReaderFactory(),
        "head": factories.DepartmentHeadFactory(),
    }
    matters = {
        "open-full": factories.MatterFactory(owner=owner),
        "closed-full": factories.MatterFactory(
            owner=owner,
            is_open=False,
            disposition=Disposition.COMPLETED,
            closed_at=timezone.now(),
        ),
        "open-archive": factories.ArchiveMatterFactory(),
        "closed-archive": factories.ArchiveMatterFactory(is_open=False),
        "untouched": factories.MatterFactory(owner=owner),
    }
    built = World(people=people, matters=matters)
    for name, matter in matters.items():
        if name == "untouched":
            continue
        for (tracked, finished), removed, restricted in itertools.product(
            ROUND_STATES, (False, True), (False, True)
        ):
            built.rounds.append(
                _round(
                    matter,
                    tracked=tracked,
                    finished=finished,
                    removed=removed,
                    restricted=restricted,
                )
            )
        for removed in (False, True):
            built.plans.append(
                MatterWebsiteOverview.objects.create(
                    matter=matter,
                    status=WebsiteOverviewStatus.PLANNED,
                    removed_at=timezone.now() if removed else None,
                )
            )
    return built


def _ids(queryset: Any) -> set[Any]:
    return set(queryset.values_list("pk", flat=True))


# =========================================================================
# The row predicate and the SQL rule are one rule
# =========================================================================


def test_open_waits_is_the_sql_twin_of_has_open_feedback_wait(world) -> None:
    in_sql = _ids(MatterEngagement.objects.open_waits())
    for engagement in MatterEngagement.objects.all():
        assert (engagement.pk in in_sql) is engagement.has_open_feedback_wait


def test_live_is_exactly_not_removed_on_every_removable_family(world) -> None:
    families = [model for model in apps.get_models() if issubclass(model, RemovableRecord)]
    assert len(families) >= 8
    for model in families:
        assert str(model.objects.live().query) == str(
            model.objects.filter(removed_at__isnull=True).query
        ), model


# =========================================================================
# Every rewritten site returns exactly what it did before
# =========================================================================


def test_answerable_rounds_is_unchanged(world) -> None:
    for person, matter in itertools.product(world.people.values(), world.matters.values()):
        before = MatterEngagement.objects.visible_to(person).filter(
            matter=matter,
            lifecycle_tracked=True,
            matter__record_mode=RecordMode.FULL,
        )
        assert _ids(answerable_rounds(person, matter)) == _ids(before)


def test_open_feedback_waits_is_unchanged(world) -> None:
    for person in world.people.values():
        for owner in (None, world.people["owner"], world.people["colleague"]):
            before = MatterEngagement.objects.visible_to(person).filter(
                lifecycle_tracked=True,
                feedback_closed_at__isnull=True,
                matter__is_open=True,
                matter__record_mode=RecordMode.FULL,
            )
            if owner is not None:
                before = before.filter(matter__owner=owner)
            assert _ids(open_feedback_waits(person, owner=owner)) == _ids(before)


def test_without_next_step_is_unchanged(world) -> None:
    for person in world.people.values():
        waiting = MatterEngagement.objects.visible_to(person).filter(
            matter=OuterRef("pk"), lifecycle_tracked=True, feedback_closed_at__isnull=True
        )
        # The world holds no step and no milestone, so the wait decides alone.
        before = Matter.objects.annotate(waiting=Exists(waiting)).filter(waiting=False)
        assert _ids(without_next_step(Matter.objects.all(), person)) == _ids(before)


def test_live_work_at_closure_is_unchanged(world) -> None:
    for matter in world.matters.values():
        work = live_work_at_closure(matter)
        assert (
            work.waits
            == MatterEngagement.objects.filter(
                matter_id=matter.pk,
                lifecycle_tracked=True,
                feedback_closed_at__isnull=True,
                removed_at__isnull=True,
            ).count()
        )
        assert (
            work.overviews
            == MatterWebsiteOverview.objects.filter(
                matter_id=matter.pk, status=WebsiteOverviewStatus.PLANNED, removed_at__isnull=True
            ).count()
        )


@pytest.mark.parametrize("name", ["open-full", "closed-full", "open-archive", "closed-archive"])
def test_closing_the_waits_with_the_file_is_unchanged(world, name) -> None:
    matter = world.matters[name]
    before = _ids(
        MatterEngagement.objects.filter(
            matter=matter,
            lifecycle_tracked=True,
            feedback_closed_at__isnull=True,
            removed_at__isnull=True,
        )
    )
    closed = services.close_open_feedback_waits_for_closure(matter=matter, actor=None)
    assert {engagement.pk for engagement in closed} == before
    assert len(before) == 2


def test_the_closed_matter_integrity_rules_are_unchanged(world) -> None:
    findings = _closed_matter_findings()
    waits = {f.subject for f in findings if f.kind == "closed-matter-open-feedback-wait"}
    plans = {f.subject for f in findings if f.kind == "closed-matter-planned-website-overview"}
    assert waits == {
        str(pk)
        for pk in MatterEngagement.objects.filter(
            matter__is_open=False,
            lifecycle_tracked=True,
            feedback_closed_at__isnull=True,
            removed_at__isnull=True,
        ).values_list("pk", flat=True)
    }
    assert plans == {
        str(pk)
        for pk in MatterWebsiteOverview.objects.filter(
            status="PLANNED", matter__is_open=False, removed_at__isnull=True
        ).values_list("pk", flat=True)
    }
    assert len(waits) == 4
    assert len(plans) == 2


def test_finishing_a_wait_on_reply_is_unchanged(world) -> None:
    for engagement in MatterEngagement.objects.filter(matter=world.matters["open-full"]):
        was_open = engagement.lifecycle_tracked and engagement.feedback_closed_at is None
        assert finish_wait_on_reply(engagement=engagement, actor=world.people["owner"]) is was_open
        engagement.refresh_from_db()
        assert engagement.feedback_closed_at is not None or not engagement.lifecycle_tracked


def test_naming_a_round_to_finish_is_unchanged(world) -> None:
    matter = world.matters["open-full"]
    for engagement in MatterEngagement.objects.filter(matter=matter):
        still_open = (
            engagement.removed_at is None
            and engagement.lifecycle_tracked
            and engagement.feedback_closed_at is None
        )
        if still_open:
            assert _named_open_rounds(matter, [(engagement.pk, "")]) == [(engagement, "")]
        else:
            with refused(ROUND_NOT_OPEN):
                _named_open_rounds(matter, [(engagement.pk, "")])


# =========================================================================
# The cutover planners: everything as before, except what was removed
# =========================================================================


def test_the_final_planner_reads_only_live_records(world) -> None:
    ids = [matter.pk for matter in world.matters.values()]
    entry_matter = world.matters["untouched"]
    factories.EntryFactory(matter=entry_matter, removed_at=timezone.now())

    activity = native_activity(ids)

    live_waits = MatterEngagement.objects.filter(
        matter_id__in=ids,
        lifecycle_tracked=True,
        feedback_closed_at__isnull=True,
        removed_at__isnull=True,
    )
    assert activity.open_feedback_waits == frozenset(live_waits.values_list("matter_id", flat=True))
    assert activity.planned_overviews == frozenset(
        MatterWebsiteOverview.objects.filter(
            matter_id__in=ids, status=WebsiteOverviewStatus.PLANNED, removed_at__isnull=True
        ).values_list("matter_id", flat=True)
    )
    assert entry_matter.pk not in activity.entries
    assert entry_matter.pk not in activity.authored_records
    assert Entry.objects.filter(matter=entry_matter).count() == 1


def test_the_historical_planner_reads_only_live_records(world) -> None:
    ids = [matter.pk for matter in world.matters.values()]
    reasons = historical_cutover.live_work(ids)
    # Every touched Matter still holds a live open round; the untouched one
    # holds nothing.
    assert reasons == {
        matter.pk: historical_cutover.ReviewReason.OPEN_FEEDBACK_WAIT
        for name, matter in world.matters.items()
        if name != "untouched"
    }


def _removed(matter, record, kind_key, actor):
    return remove_matter_record(
        matter_id=matter.pk, kind_key=kind_key, record_id=record.pk, actor=actor
    )


def _open_round(matter, actor):
    return services.add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="Sünteetilised liikmed",
        occurred_on=timezone.localdate() - dt.timedelta(days=3),
        feedback_deadline=timezone.localdate() + dt.timedelta(days=10),
        actor=actor,
    )


def test_a_removed_open_round_gives_the_historical_planner_no_review_reason(specialist) -> None:
    """JDATA-005: «Kustuta» says the round was never on the file."""
    matter = register_matter(2014)
    engagement = _open_round(matter, specialist)
    _removed(matter, engagement, "kaasamine", specialist)

    plan = historical_cutover.build_cutover_plan(cutover_year=2026)
    candidate = next(c for c in plan.candidates if c.matter.pk == matter.pk)

    assert candidate.review_reason == ""
    assert candidate.classification != historical_cutover.Classification.REVIEW_REQUIRED


def test_a_removed_planned_overview_gives_the_historical_planner_no_review_reason(
    specialist,
) -> None:
    matter = register_matter(2014)
    overview = services.plan_website_overview(matter=matter, actor=specialist)
    _removed(matter, overview, "ulevaade", specialist)

    plan = historical_cutover.build_cutover_plan(cutover_year=2026)
    candidate = next(c for c in plan.candidates if c.matter.pk == matter.pk)

    assert candidate.review_reason == ""


@pytest.mark.parametrize(
    ("make", "kind_key"),
    [
        (_open_round, "kaasamine"),
        (lambda m, a: services.plan_website_overview(matter=m, actor=a), "ulevaade"),
        (
            lambda m, a: services.record_procedural_development(
                matter=m,
                title="Sünteetiline menetluse samm",
                occurred_on=timezone.localdate() - dt.timedelta(days=2),
                actor=a,
            ),
            "marge",
        ),
    ],
    ids=["open-round", "planned-overview", "marge"],
)
def test_a_removed_record_does_not_hold_a_final_retirement_back(
    monkeypatch, make, kind_key
) -> None:
    """JDATA-005: neither a review reason nor «authored» native work."""
    approve_snapshot(monkeypatch, sha256=FINAL_SNAPSHOT)
    world = build_world()
    matter = world[RETIRING_NO_PLANS]
    actor = world.people.sandra

    record = make(matter, actor)
    held = next(
        c
        for c in build_final_plan(snapshot_sha256=FINAL_SNAPSHOT).candidates
        if c.matter.title == RETIRING_NO_PLANS
    )
    assert held.action == Action.REVIEW_REQUIRED

    _removed(matter, record, kind_key, actor)
    candidate = next(
        c
        for c in build_final_plan(snapshot_sha256=FINAL_SNAPSHOT).candidates
        if c.matter.title == RETIRING_NO_PLANS
    )
    assert candidate.action == Action.RETIRE
    assert candidate.review_reason not in (
        FinalReviewReason.OPEN_FEEDBACK_WAIT,
        FinalReviewReason.PLANNED_WEBSITE_OVERVIEW,
        FinalReviewReason.AUTHORED_RECORDS,
    )


# =========================================================================
# «Does this overview finish the step» — one predicate, on the shown kind
# =========================================================================


@pytest.mark.parametrize(
    ("status", "kind", "url", "fulfils"),
    [
        (WebsiteOverviewStatus.PUBLISHED, WebsiteOverviewKind.OVERVIEW, KODA_OVERVIEW, True),
        (WebsiteOverviewStatus.PUBLISHED, WebsiteOverviewKind.OVERVIEW, KODA_NEWS, True),
        (WebsiteOverviewStatus.PUBLISHED, WebsiteOverviewKind.NEWS, KODA_NEWS, False),
        (WebsiteOverviewStatus.PUBLISHED, WebsiteOverviewKind.NEWS, KODA_OVERVIEW, False),
        # A row from before the kind was stored reads as what its address says…
        (WebsiteOverviewStatus.PUBLISHED, "", KODA_OVERVIEW, True),
        (WebsiteOverviewStatus.PUBLISHED, "", KODA_NEWS, False),
        # …and as an overview where the address says nothing, which is what
        # every one of them was filed as (docs/adr/0161).
        (WebsiteOverviewStatus.PUBLISHED, "", ELSEWHERE, True),
        (WebsiteOverviewStatus.PLANNED, WebsiteOverviewKind.OVERVIEW, "", False),
        (WebsiteOverviewStatus.CANCELLED, WebsiteOverviewKind.OVERVIEW, "", False),
    ],
    ids=[
        "overview",
        "overview-stored-wins",
        "news",
        "news-stored-wins",
        "legacy-overview-address",
        "legacy-news-address",
        "legacy-unclassified",
        "planned",
        "cancelled",
    ],
)
def test_overview_fulfils_step(status, kind, url, fulfils) -> None:
    overview = MatterWebsiteOverview(status=status, kind=kind, url=url)
    assert overview_fulfils_step(overview) is fulfils


def _published(matter, *, kind, url) -> MatterWebsiteOverview:
    return MatterWebsiteOverview.objects.create(
        matter=matter,
        status=WebsiteOverviewStatus.PUBLISHED,
        kind=kind,
        url=url,
        published_on=timezone.localdate(),
        published_at=timezone.now(),
    )


@pytest.mark.parametrize(
    ("kind", "url", "proves"),
    [
        ("", KODA_NEWS, False),
        ("", KODA_OVERVIEW, True),
        ("", ELSEWHERE, True),
        (WebsiteOverviewKind.NEWS, KODA_NEWS, False),
        (WebsiteOverviewKind.OVERVIEW, KODA_OVERVIEW, True),
    ],
    ids=["legacy-news", "legacy-overview", "legacy-unclassified", "news", "overview"],
)
def test_recorded_work_reads_the_shown_kind(specialist, kind, url, proves) -> None:
    """A legacy row shown as «Uudis» is not the published overview (ADR 0161)."""
    matter = factories.MatterFactory(owner=specialist)
    _published(matter, kind=kind, url=url)

    done = RecordedWork.of(matter, specialist)

    assert done.published_overview is proves
    assert done.published_any is True
