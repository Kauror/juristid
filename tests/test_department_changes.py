"""*Muudatused* — the department-wide state-change read model (docs/adr/0159).

What this file pins:

* **each category is exactly its canonical events** — a person's act, on a
  current-work Teema, of the curated types and nothing else;
* **imported is not new** — a Teema copied in from the register is a coverage
  figure, never «Uued teemad», and system rows are counted aside;
* **the window is Tallinn's, both ends inclusive**, and the fortnight and the
  week start where Tehtud's parser says;
* **a custom range the reader typed is refused in words**, not silently
  replaced;
* **authorization before arithmetic** — a restricted Teema or a restricted step
  reaches neither a count nor a row for a reader who may not see it;
* **outputs are counted once** — the view carries Tehtud's own digest;
* **snapshots compare states and say when they are from**, or say there are
  none;
* **the queries are bounded** — more rows, the same number of queries.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.matters import department_changes as dc
from app.matters import department_dashboard as dd
from app.matters.services import (
    assign_matter,
    change_stage,
    close_matter,
    create_imported_matter,
    create_matter,
    reopen_matter,
)
from app.reporting.selectors.snapshots import capture
from app.workflow.enums import ActionKind, DateSemantics, Disposition
from app.workflow.models import StageVocabulary
from app.workflow.services import (
    acknowledge_review,
    cancel_next_action,
    complete_next_action,
    set_next_action,
)
from tests import factories

pytestmark = pytest.mark.django_db

TALLINN = ZoneInfo("Europe/Tallinn")


def _stage(key: str) -> StageVocabulary:
    return StageVocabulary.objects.get(key=key)


def _category(changes: dc.DepartmentChanges, key: str) -> dc.CategoryResult:
    return next(result for result in changes.categories if result.key == key)


def _counts(changes: dc.DepartmentChanges) -> dict[str, int]:
    return {result.key: result.count for result in changes.categories}


def _at(day: date, clock: time) -> datetime:
    return datetime.combine(day, clock, tzinfo=TALLINN)


@pytest.fixture
def today() -> date:
    return timezone.localdate()


# =========================================================================
# Category predicates
# =========================================================================


def test_every_category_is_its_own_canonical_events(department_head, specialist, other_specialist):
    matter = create_matter(title="Uus eelnõu", owner=specialist, actor=specialist)
    change_stage(matter=matter, stage=_stage("idea"), actor=specialist)
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    assign_matter(matter=matter, owner=other_specialist, actor=department_head)
    action = set_next_action(matter=matter, text="Kirjuta arvamus", actor=other_specialist)
    complete_next_action(action=action, actor=other_specialist)
    second = set_next_action(matter=matter, text="Helista ministeeriumi", actor=other_specialist)
    cancel_next_action(action=second, actor=other_specialist)
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=other_specialist)
    reopen_matter(matter=matter, actor=other_specialist)

    changes = dc.build_changes(department_head)

    assert _counts(changes) == {
        "uued": 1,
        "hetkeseis": 2,
        "vastutaja": 1,
        "tegevused": 4,
        "taasavatud": 1,
    }
    breakdown = dict(_category(changes, "tegevused").breakdown)
    assert breakdown["määras järgmise tegevuse"] == 2
    assert breakdown["lõpetas järgmise tegevuse"] == 1
    assert breakdown["tühistas järgmise tegevuse"] == 1


def test_a_row_says_before_and_after_from_the_events_own_payload(department_head, specialist):
    matter = create_matter(title="Hetkeseisu teema", owner=None, actor=specialist)
    change_stage(matter=matter, stage=_stage("idea"), actor=specialist)
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    assign_matter(matter=matter, owner=specialist, actor=department_head)

    changes = dc.build_changes(department_head)

    stage_rows = _category(changes, "hetkeseis").rows
    assert [row.detail for row in stage_rows] == ["Idee → Kooskõlastusringil", "määramata → Idee"]
    assert stage_rows[0].actor_name == specialist.display_name
    assert stage_rows[0].matter_title == "Hetkeseisu teema"
    transfer = _category(changes, "vastutaja").rows[0]
    assert transfer.detail == f"→ {specialist.display_name}"
    assert transfer.actor_name == department_head.display_name


def test_a_hand_over_names_both_people(department_head, specialist, other_specialist):
    matter = create_matter(title="Üleantav", owner=specialist, actor=specialist)
    assign_matter(matter=matter, owner=other_specialist, actor=department_head)

    row = _category(dc.build_changes(department_head), "vastutaja").rows[0]

    assert row.detail == f"{specialist.display_name} → {other_specialist.display_name}"


def test_what_changes_no_state_is_not_counted(department_head, specialist, today):
    """A rename, a review and a closure are not this view's.

    The title is not a state; «Vaatasin üle» confirms a step without moving it;
    and a closure is Tehtud's output, counted there once.
    """
    matter = create_matter(title="Vana pealkiri", owner=specialist, actor=specialist)
    ChangeEvent.objects.create(
        event_type=ChangeEventType.MATTER_TITLE_CHANGED,
        matter=matter,
        actor=specialist,
        summary="Uus pealkiri",
    )
    waiting = set_next_action(
        matter=matter,
        text="Ootan vastust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=today + timedelta(days=3),
        actor=specialist,
    )
    acknowledge_review(action=waiting, actor=specialist, next_review_date=today + timedelta(days=9))
    other = create_matter(title="Suletav", owner=specialist, actor=specialist)
    close_matter(matter=other, disposition=Disposition.COMPLETED, actor=specialist)

    changes = dc.build_changes(department_head)

    assert _counts(changes) == {
        "uued": 2,
        "hetkeseis": 0,
        "vastutaja": 0,
        "tegevused": 1,
        "taasavatud": 0,
    }
    assert ChangeEventType.NEXT_ACTION_REVIEWED not in dc.CURATED_EVENT_TYPES
    assert ChangeEventType.MATTER_CLOSED not in dc.CURATED_EVENT_TYPES
    assert ChangeEventType.MATTER_TITLE_CHANGED not in dc.CURATED_EVENT_TYPES


def test_an_archive_row_is_not_current_work(department_head, specialist):
    archive = factories.ArchiveMatterFactory(title="Arhiivirida")
    ChangeEvent.objects.create(
        event_type=ChangeEventType.MATTER_ASSIGNED,
        matter=archive,
        actor=specialist,
        payload={"from_name": None, "to_name": specialist.display_name},
    )

    assert dc.build_changes(department_head).total == 0


# =========================================================================
# Imported versus native
# =========================================================================


def test_an_imported_teema_is_coverage_and_never_new(department_head, specialist):
    create_matter(title="Siin loodud", owner=specialist, actor=specialist)
    create_imported_matter(
        title="Registrist imporditud",
        reference_year=2026,
        reference_number=901,
        record_mode="FULL",
        owner=specialist,
        stage=_stage("government"),
    )

    changes = dc.build_changes(department_head)

    assert [row.matter_title for row in _category(changes, "uued").rows] == ["Siin loodud"]
    assert changes.coverage.imported_matters == 1
    # The import's own MATTER_CREATED has nobody behind it.
    assert changes.coverage.system_events == 1
    # Its stage began at a moment nobody recorded (docs/adr/0131 §3).
    assert changes.coverage.stage_start_unknown == 1


def test_a_person_working_on_an_imported_teema_is_a_change(department_head, specialist):
    imported = create_imported_matter(
        title="Imporditud, siis muudetud",
        reference_year=2026,
        reference_number=902,
        record_mode="FULL",
        owner=None,
    )
    assign_matter(matter=imported, owner=specialist, actor=department_head)

    changes = dc.build_changes(department_head)

    assert _counts(changes)["vastutaja"] == 1
    assert _counts(changes)["uued"] == 0


def test_a_batch_assignment_is_counted_aside(department_head, specialist):
    """The owner backfill writes MATTER_ASSIGNED with provenance and no actor."""
    imported = create_imported_matter(
        title="Tagantjärele omanik",
        reference_year=2026,
        reference_number=903,
        record_mode="FULL",
        owner=None,
    )
    assign_matter(matter=imported, owner=specialist, provenance={"source": "BACKFILL"})

    changes = dc.build_changes(department_head)

    assert _counts(changes)["vastutaja"] == 0
    assert changes.coverage.system_events == 2  # the creation and the backfill


# =========================================================================
# The window
# =========================================================================


def test_both_ends_of_the_window_are_inclusive_in_tallinn(department_head, specialist):
    matter = factories.MatterFactory(owner=specialist)
    start, end = date(2026, 3, 2), date(2026, 3, 8)
    moments = {
        "day before, last minute": _at(start - timedelta(days=1), time(23, 59)),
        "first day, first minute": _at(start, time(0, 0, 30)),
        "last day, last minute": _at(end, time(23, 59)),
        "day after, first minute": _at(end + timedelta(days=1), time(0, 0, 30)),
    }
    for label, moment in moments.items():
        ChangeEvent.objects.create(
            event_type=ChangeEventType.MATTER_STAGE_CHANGED,
            matter=matter,
            actor=specialist,
            occurred_at=moment,
            summary=label,
            payload={"from_label": "Idee", "to_label": label},
        )

    changes = dc.build_changes(
        department_head,
        {"periood": "vahemik", "alates": "02.03.2026", "kuni": "08.03.2026"},
        today=date(2026, 3, 20),
    )

    details = {row.detail for row in _category(changes, "hetkeseis").rows}
    assert details == {"Idee → first day, first minute", "Idee → last day, last minute"}
    # 23:59 in Tallinn is 21:59 UTC the same day, and 00:00:30 is the previous
    # UTC day: a UTC `__date` would have moved both rows across the boundary.
    assert moments["first day, first minute"].astimezone(ZoneInfo("UTC")).date() < start


@pytest.mark.parametrize(
    ("key", "days"),
    [("7", 7), ("14", 14), ("30", 30), ("kvartal", 7), ("tundmatu", 7), ("", 7)],
)
def test_the_named_windows_are_tehtuds(key, days):
    today = date(2026, 10, 13)

    period, error = dc.changes_period({"periood": key}, today)

    assert error == ""
    assert (period.start, period.end) == (today - timedelta(days=days), today)
    # One parser: the same key gives Tehtud the same window.
    if key in {"7", "14", "30"}:
        assert dd.period_from({"periood": key}, today) == period


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        ("10.10.2026", "01.10.2026", dc.CUSTOM_RANGE_REVERSED),
        ("31.02.2026", "01.03.2026", dc.CUSTOM_RANGE_UNREADABLE),
        ("eile", "01.03.2026", dc.CUSTOM_RANGE_UNREADABLE),
        ("", "01.03.2026", dc.CUSTOM_RANGE_INCOMPLETE),
        ("01.03.2026", "", dc.CUSTOM_RANGE_INCOMPLETE),
    ],
)
def test_a_custom_range_the_reader_typed_is_refused_in_words(start, end, message):
    today = date(2026, 10, 13)

    period, error = dc.changes_period({"periood": "vahemik", "alates": start, "kuni": end}, today)

    assert error == message
    assert period == dd.period_from({}, today)


def test_a_valid_custom_range_is_used_as_typed():
    period, error = dc.changes_period(
        {"periood": "vahemik", "alates": "2026-09-01", "kuni": "30.09.2026"}, date(2026, 10, 13)
    )

    assert error == ""
    assert (period.key, period.start, period.end) == (
        "vahemik",
        date(2026, 9, 1),
        date(2026, 9, 30),
    )


def test_a_one_day_range_is_a_window(department_head, specialist):
    matter = factories.MatterFactory(owner=specialist)
    ChangeEvent.objects.create(
        event_type=ChangeEventType.MATTER_REOPENED,
        matter=matter,
        actor=specialist,
        occurred_at=_at(date(2026, 5, 4), time(12)),
    )

    changes = dc.build_changes(
        department_head, {"periood": "vahemik", "alates": "04.05.2026", "kuni": "04.05.2026"}
    )

    assert _counts(changes)["taasavatud"] == 1


def test_tehtuds_own_control_is_unchanged():
    assert [key for key, _ in dd.PERIOD_LABELS] == ["7", "30", "kvartal", "aasta"]


# =========================================================================
# Visibility
# =========================================================================


def test_a_restricted_teemas_changes_reach_no_count_and_no_row(reader, specialist, department_head):
    hidden = create_matter(
        title="SALAJANE-TEEMA-311",
        owner=specialist,
        actor=specialist,
        visibility=Visibility.RESTRICTED,
    )
    change_stage(matter=hidden, stage=_stage("idea"), actor=specialist)
    set_next_action(matter=hidden, text="Peidetud samm", actor=specialist)
    create_matter(title="Avalik teema", owner=specialist, actor=specialist)

    for_reader = dc.build_changes(reader)
    for_owner = dc.build_changes(specialist)

    assert _counts(for_reader) == {
        "uued": 1,
        "hetkeseis": 0,
        "vastutaja": 0,
        "tegevused": 0,
        "taasavatud": 0,
    }
    titles = {row.matter_title for result in for_reader.categories for row in result.rows}
    assert "SALAJANE-TEEMA-311" not in titles
    assert _counts(for_owner)["hetkeseis"] == 1
    assert _counts(for_owner)["uued"] == 2


def test_a_restricted_step_on_an_ordinary_teema_stays_hidden(reader, specialist):
    matter = create_matter(title="Tavaline teema", owner=specialist, actor=specialist)
    action = set_next_action(matter=matter, text="SALAJANE-SAMM-312", actor=specialist)
    action.visibility_override = Visibility.RESTRICTED
    action.save(update_fields=["visibility_override"])

    assert _counts(dc.build_changes(reader))["tegevused"] == 0
    assert _counts(dc.build_changes(reader))["uued"] == 1
    assert _counts(dc.build_changes(specialist))["tegevused"] == 1


def test_coverage_counts_only_what_the_reader_may_see(reader, specialist):
    create_imported_matter(
        title="Peidetud import",
        reference_year=2026,
        reference_number=904,
        record_mode="FULL",
        visibility=Visibility.RESTRICTED,
        stage=_stage("government"),
    )

    coverage = dc.build_changes(reader).coverage

    assert (coverage.imported_matters, coverage.system_events, coverage.stage_start_unknown) == (
        0,
        0,
        0,
    )


# =========================================================================
# Outputs, empty state, links
# =========================================================================


def test_the_outputs_are_tehtuds_own_digest(department_head, specialist):
    matter = create_matter(title="Lõpetatav", owner=specialist, actor=specialist)
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist)

    changes = dc.build_changes(department_head, {"periood": "14"})
    digest = dd.build_digest(department_head, changes.period)

    assert changes.outputs is not None
    assert (changes.outputs.sent, changes.outputs.closed, changes.outputs.victories) == (
        digest.sent,
        digest.closed,
        digest.victories,
    )
    assert changes.outputs.closed == 1


def test_an_empty_department_says_so_and_claims_no_coverage(department_head):
    changes = dc.build_changes(department_head)

    assert changes.is_empty
    assert changes.total == 0
    assert all(result.rows == [] for result in changes.categories)
    assert changes.coverage.recorded_since is None
    assert changes.coverage.starts_after(changes.period)
    assert not changes.snapshots.available


def test_a_window_before_the_first_recorded_act_is_marked_partial(department_head, specialist):
    create_matter(title="Esimene", owner=specialist, actor=specialist)
    today = timezone.localdate()

    changes = dc.build_changes(department_head, {"periood": "30"})

    assert changes.coverage.recorded_since == today
    assert changes.coverage.starts_after(changes.period)
    assert not changes.coverage.starts_after(dd.Period("vahemik", today, today))


def test_every_row_opens_its_teema(department_head, specialist):
    matter = create_matter(title="Lingitav", owner=specialist, actor=specialist)
    change_stage(matter=matter, stage=_stage("idea"), actor=specialist)

    changes = dc.build_changes(department_head)

    expected = reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    rows = [row for result in changes.categories for row in result.rows]
    assert rows
    assert {row.url for row in rows} == {expected}


def test_a_long_category_is_capped_and_says_so(department_head, specialist, monkeypatch):
    monkeypatch.setattr(dc, "ROW_LIMIT", 2)
    for number in range(3):
        create_matter(title=f"Teema {number}", owner=specialist, actor=specialist)

    result = _category(dc.build_changes(department_head), "uued")

    assert result.count == 3
    assert len(result.rows) == 2
    assert result.truncated
    assert [row.matter_title for row in result.rows] == ["Teema 2", "Teema 1"]


# =========================================================================
# Snapshots
# =========================================================================


def test_snapshots_compare_two_photographs_and_name_their_days(department_head, specialist):
    first = create_matter(title="Avatud kogu aeg", owner=specialist, actor=specialist)
    capture(on=date(2026, 9, 1))
    create_matter(title="Hiljem lisatud", owner=None, actor=specialist)
    capture(on=date(2026, 9, 10))
    assert first.is_open

    snapshots = dc.build_changes(
        department_head, {"periood": "vahemik", "alates": "03.09.2026", "kuni": "12.09.2026"}
    ).snapshots

    assert (snapshots.start_date, snapshots.end_date) == (date(2026, 9, 1), date(2026, 9, 10))
    figures = {
        figure.label: (figure.start, figure.end, figure.delta) for figure in snapshots.figures
    }
    assert figures == {"Avatud teemasid": (1, 2, 1), "Vastutajata teemasid": (0, 1, 1)}


def test_no_photograph_before_the_window_means_no_comparison(department_head, specialist):
    create_matter(title="Ainult hiljem", owner=specialist, actor=specialist)
    capture(on=date(2026, 9, 10))

    snapshots = dc.build_changes(
        department_head, {"periood": "vahemik", "alates": "03.09.2026", "kuni": "12.09.2026"}
    ).snapshots

    assert not snapshots.available
    assert snapshots.figures == []
    assert snapshots.end_date == date(2026, 9, 10)


def test_a_snapshot_of_a_restricted_teema_is_not_counted_for_a_reader(reader, specialist):
    create_matter(
        title="Peidetud",
        owner=None,
        actor=specialist,
        visibility=Visibility.RESTRICTED,
    )
    capture(on=date(2026, 9, 1))
    capture(on=date(2026, 9, 10))

    snapshots = dc.build_changes(
        reader, {"periood": "vahemik", "alates": "01.09.2026", "kuni": "10.09.2026"}
    ).snapshots

    assert snapshots.available
    assert {figure.label: (figure.start, figure.end) for figure in snapshots.figures} == {
        "Avatud teemasid": (0, 0),
        "Vastutajata teemasid": (0, 0),
    }


# =========================================================================
# Bounded queries
# =========================================================================


def _queries(user) -> int:
    with CaptureQueriesContext(connection) as captured:
        dc.build_changes(user)
    return len(captured.captured_queries)


def test_more_changes_cost_no_more_queries(department_head, specialist, other_specialist):
    def busy(count: int) -> None:
        for number in range(count):
            matter = create_matter(title=f"Koormus {number}", owner=specialist, actor=specialist)
            change_stage(matter=matter, stage=_stage("idea"), actor=specialist)
            assign_matter(matter=matter, owner=other_specialist, actor=department_head)
            set_next_action(matter=matter, text=f"Samm {number}", actor=other_specialist)

    busy(2)
    few = _queries(department_head)
    busy(6)
    many = _queries(department_head)

    assert many == few
    assert few <= 25


# =========================================================================
# The page — /osakond/muudatused/ (docs/adr/0159 §6)
# =========================================================================

PAGE = "/osakond/muudatused/"
OSAKOND = "/osakond/"


def _lend_management_view(person):
    person.capability_overrides = {
        **(person.capability_overrides or {}),
        "department.view_management": "allow",
    }
    person.save(update_fields=["capability_overrides"])
    return person


def test_the_route_is_where_the_adr_puts_it():
    assert reverse("matters:department_changes") == PAGE


def test_the_head_reads_the_page(client, department_head, specialist):
    matter = create_matter(title="Pealik näeb seda", owner=specialist, actor=specialist)
    change_stage(matter=matter, stage=_stage("idea"), actor=specialist)
    client.force_login(department_head)

    response = client.get(PAGE)

    assert response.status_code == 200
    body = response.content.decode()
    assert '<h1 class="ovhead__title">Muudatused</h1>' in body
    for result in response.context["page"].categories:
        assert f'data-category="{result.key}"' in body
        assert result.label in body
    assert "Pealik näeb seda — muutis hetkeseisu: määramata → Idee" in body
    assert reverse("matters:matter_detail", kwargs={"pk": matter.pk}) in body
    assert "data-outputs" in body and "data-snapshots" in body and "data-coverage" in body
    assert "data-changes-empty" not in body


@pytest.mark.parametrize("who", ["specialist", "reader", "administrator"])
def test_anybody_else_is_refused_with_a_404(client, request, who):
    client.force_login(request.getfixturevalue(who))

    assert client.get(PAGE).status_code == 404


def test_nothing_is_built_before_the_refusal(client, specialist, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("built for somebody who may not open the page")

    monkeypatch.setattr("app.matters.department_views.build_changes", refuse)
    client.force_login(specialist)

    assert client.get(PAGE).status_code == 404


def test_a_reader_lent_the_view_opens_it_and_sees_no_restricted_teema(client, reader, specialist):
    create_matter(
        title="SALAJANE-TEEMA-411",
        owner=specialist,
        actor=specialist,
        visibility=Visibility.RESTRICTED,
    )
    create_matter(title="Avalik teema 411", owner=specialist, actor=specialist)
    client.force_login(_lend_management_view(reader))

    response = client.get(PAGE)

    assert response.status_code == 200
    body = response.content.decode()
    assert "Avalik teema 411" in body
    assert "SALAJANE-TEEMA-411" not in body
    assert response.context["page"].categories[0].count == 1


class TestBehindTheSharedGate:
    PASSWORD = "muudatuste-lehe-test-parool"  # noqa: S105

    @pytest.fixture(autouse=True)
    def gate_mode(self, settings):
        from tests.gate import apply_shared_gate

        return apply_shared_gate(settings, self.PASSWORD)

    def test_a_session_with_no_persona_is_refused(self, client):
        response = client.post(reverse("accounts:shared_gate"), {"password": self.PASSWORD})
        assert response.status_code == 302

        assert client.get(PAGE).status_code == 404


def test_the_empty_state_is_said_in_words(client, department_head):
    client.force_login(department_head)

    body = client.get(PAGE).content.decode()

    assert "data-changes-empty" in body
    assert "Valitud perioodil ei ole ühtegi muudatust salvestatud." in body
    assert "Ühtegi muudatust ei ole veel salvestatud." in body
    assert "võrdlust ei ole" in body


def test_a_refused_range_says_why_and_keeps_what_was_typed(client, department_head):
    client.force_login(department_head)

    response = client.get(PAGE, {"periood": "vahemik", "alates": "10.10.2026", "kuni": "1.10.2026"})

    body = response.content.decode()
    assert dc.CUSTOM_RANGE_REVERSED in body
    assert 'value="10.10.2026"' in body and 'value="1.10.2026"' in body
    assert response.context["page"].period.key == dd.PERIOD_7
    assert not any(option.active for option in response.context["page"].period_options)


def test_the_fortnight_chip_selects_the_fortnight(client, department_head):
    client.force_login(department_head)

    response = client.get(PAGE, {"periood": "14"})

    options = {option.key: option.active for option in response.context["page"].period_options}
    assert options == {"7": False, "14": True, "30": False}
    assert 'href="?periood=14"' in response.content.decode()


def test_the_outputs_link_opens_tehtud_on_the_same_window(department_head):
    custom = dd.Period(dd.PERIOD_CUSTOM, date(2026, 9, 1), date(2026, 9, 30))

    assert dc.tehtud_url(custom) == (
        "/osakond/?vaade=osakond&periood=vahemik&alates=1.9.2026&kuni=30.9.2026&liik=koik#tehtud"
    )
    fortnight = dd.period_from({"periood": "14"}, date(2026, 10, 13))
    assert dc.tehtud_url(fortnight).startswith("/osakond/?vaade=osakond&periood=14&")


def test_the_link_from_tehtud_keeps_tehtuds_window():
    today = date(2026, 10, 13)

    assert dc.changes_url(dd.period_from({"periood": "30"}, today)) == f"{PAGE}?periood=30"
    quarter = dd.period_from({"periood": "kvartal"}, today)
    assert dc.changes_url(quarter) == (
        f"{PAGE}?periood=vahemik&alates={quarter.start.day}.{quarter.start.month}."
        f"{quarter.start.year}&kuni=13.10.2026"
    )


def test_the_osakond_link_is_the_heads_alone(client, department_head, specialist):
    client.force_login(department_head)
    head_body = client.get(OSAKOND).content.decode()
    client.force_login(specialist)
    specialist_body = client.get(OSAKOND).content.decode()

    assert f'href="{PAGE}?periood=7">Muudatused →</a>' in head_body
    assert 'id="tehtud"' in head_body
    assert "Muudatused →" not in specialist_body
    assert PAGE not in specialist_body


def test_the_link_carries_the_window_tehtud_is_showing(client, department_head):
    client.force_login(department_head)

    body = client.get(OSAKOND, {"periood": "30"}).content.decode()

    assert f'href="{PAGE}?periood=30">Muudatused →</a>' in body
