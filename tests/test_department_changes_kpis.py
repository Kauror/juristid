"""*Muudatused* — KPI cards, the per-person table, the change table and its
filters (docs/adr/0159, amendment of 2026-10-30).

What this file pins:

* **each KPI is its definition** — distinct Teemad, events, new native Teemad,
  Tehtud's sent opinions and closures counted whole (not at the digest cap),
  the snapshot comparison, and the work-state cards' «ei mõõdetud»;
* **the per-person table groups by the Teema's current owner**, never the
  actor, and every additive column sums to its KPI;
* **«Enne» / «Pärast» come only from the event's payload**;
* **filters narrow one population** and an unreadable value falls back;
* **authorization before arithmetic** — an outsider gets nothing anywhere,
  including search;
* **the query count does not grow with the data**, filters included;
* **the photograph records the two work-state memberships** forward only, a
  narrower scope cannot read them, and the automatic daily capture runs once.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.authorization import Scope
from app.core.enums import Visibility
from app.matters import department_changes as dc
from app.matters import department_dashboard as dd
from app.matters import work_items as wi
from app.matters.services import (
    assign_matter,
    change_stage,
    close_matter,
    create_imported_matter,
    create_matter,
)
from app.reporting.models import OperationalMatterSnapshot
from app.reporting.selectors import snapshots as snapshot_selectors
from app.reporting.selectors.snapshots import (
    capture,
    capture_daily_if_missing,
    visible_snapshots,
)
from app.submissions.enums import SubmissionStatus
from app.workflow.enums import ActionKind, DateSemantics, Disposition
from app.workflow.models import StageVocabulary
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

TALLINN = ZoneInfo("Europe/Tallinn")
PAGE = "/osakond/muudatused/"


def _stage(key: str) -> StageVocabulary:
    return StageVocabulary.objects.get(key=key)


def _kpis(changes: dc.DepartmentChanges) -> dict[str, dc.Kpi]:
    return {kpi.key: kpi for kpi in changes.kpis}


def _values(changes: dc.DepartmentChanges) -> dict[str, int | None]:
    return {kpi.key: kpi.value for kpi in changes.kpis}


_EVIDENCE: dict[str, object] = {}


@pytest.fixture(autouse=True)
def _evidence(capture_evidence):
    """A sent opinion needs its immutable final text (a database constraint)."""
    _EVIDENCE["capture"] = capture_evidence
    yield
    _EVIDENCE.clear()


def _sent(matter):
    """A SENT Submission with the final evidence the database insists on."""
    capture = _EVIDENCE["capture"]
    version = capture(matter, b"%PDF-1.4 synthetic opinion", "arvamus.pdf", "application/pdf")  # type: ignore[operator]
    return factories.SubmissionFactory(
        matter=matter,
        status=SubmissionStatus.SENT,
        sent_at=timezone.now(),
        final_version=version,
    )


def _world(department_head, specialist, other_specialist):
    """Three native Teemad, an import, a sent opinion and a closure."""
    first = create_matter(title="Esimene teema", owner=specialist, actor=specialist)
    change_stage(matter=first, stage=_stage("idea"), actor=specialist)
    change_stage(matter=first, stage=_stage("consultation"), actor=specialist)
    second = create_matter(title="Teine teema", owner=other_specialist, actor=other_specialist)
    set_next_action(matter=second, text="Kirjuta", actor=other_specialist)
    third = create_matter(title="Kolmas teema", owner=None, actor=department_head)
    create_imported_matter(
        title="Imporditud",
        reference_year=2026,
        reference_number=777,
        record_mode="FULL",
        stage=_stage("government"),
    )
    _sent(first)
    close_matter(
        matter=second,
        disposition=Disposition.COMPLETED,
        actor=other_specialist,
        follow_ups_confirmed=True,
    )
    return first, second, third


# =========================================================================
# KPI definitions
# =========================================================================


def test_every_kpi_is_its_definition(department_head, specialist, other_specialist):
    _world(department_head, specialist, other_specialist)

    changes = dc.build_changes(department_head)
    values = _values(changes)

    # Three native Teemad changed; the import is not a person's act.
    assert values["matters"] == 3
    assert values["new"] == 3
    # first: created + 2 stages; second: created + set + closure (one row);
    # third: created. The closure's cancelled step is one entry.
    assert values["events"] == changes.detail.count == changes.total == 7
    sent = dd.sent_submissions(
        department_head, since=changes.period.start, until=changes.period.end
    )
    closed = dd.closed_matters(
        department_head, since=changes.period.start, until=changes.period.end
    )
    assert values["sent"] == sent.count() == 1
    assert values["closed"] == closed.count() == 1
    assert changes.outputs is not None
    assert (values["sent"], values["closed"]) == (changes.outputs.sent, changes.outputs.closed)


def test_each_card_says_what_it_counts(department_head):
    units = {kpi.key: kpi.unit for kpi in dc.build_changes(department_head).kpis}

    assert units == {
        "matters": "teemat",
        "events": "muudatust",
        "new": "teemat",
        "sent": "arvamust",
        "closed": "teemat",
        "open": "teemat",
        "attention": "teemat",
        "overdue": "teemat",
    }


def test_outputs_are_counted_whole_not_at_the_digest_cap(department_head, specialist, monkeypatch):
    matter = create_matter(title="Palju arvamusi", owner=specialist, actor=specialist)
    for _ in range(3):
        _sent(matter)
    monkeypatch.setattr(dd, "DIGEST_LIMIT", 1)

    changes = dc.build_changes(department_head)

    assert changes.outputs is not None and changes.outputs.sent == 1
    assert _values(changes)["sent"] == 3


def test_one_submission_is_one_opinion_whatever_its_audit_rows(department_head, specialist):
    matter = create_matter(title="Üks arvamus", owner=specialist, actor=specialist)
    submission = _sent(matter)
    for _ in range(2):
        ChangeEvent.objects.create(
            event_type=ChangeEventType.SUBMISSION_SENT,
            matter=matter,
            actor=specialist,
            object_type="submissions.Submission",
            object_id=submission.pk,
            payload={},
        )

    assert _values(dc.build_changes(department_head))["sent"] == 1


def test_no_photographs_mean_no_comparison_never_zero(department_head, specialist):
    create_matter(title="Avatud", owner=specialist, actor=specialist)

    kpis = _kpis(dc.build_changes(department_head))

    for key in ("open", "attention", "overdue"):
        assert kpis[key].compared
        assert not kpis[key].measured
        assert kpis[key].start is None and kpis[key].end is None
        assert kpis[key].note == dc.SNAPSHOT_UNAVAILABLE


def test_menetluses_compares_two_photographs(department_head, specialist):
    today = timezone.localdate()
    create_matter(title="Vana", owner=specialist, actor=specialist)
    capture(on=today - timedelta(days=10))
    create_matter(title="Uus", owner=specialist, actor=specialist)
    capture(on=today)

    kpi = _kpis(dc.build_changes(department_head))["open"]

    assert (kpi.start, kpi.end, kpi.delta) == (1, 2, 1)


def test_work_state_cards_show_today_and_measure_only_recorded_photographs(
    department_head, specialist
):
    today = timezone.localdate()
    late = create_matter(title="Hilinenud", owner=specialist, actor=specialist)
    set_next_action(
        matter=late,
        text="Ammu tähtaeg",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today - timedelta(days=3),
        actor=specialist,
    )
    create_matter(title="Ilma tegevuseta", owner=specialist, actor=specialist)
    capture(on=today - timedelta(days=10))
    capture(on=today)

    kpis = _kpis(dc.build_changes(department_head))
    overdue_now = wi.work_population_ids(department_head, wi.WORK_OVERDUE, today=today)
    attention_now = wi.work_population_ids(department_head, wi.WORK_NEEDS_ATTENTION, today=today)

    assert kpis["overdue"].value == len(overdue_now) == 1
    assert kpis["attention"].value == len(attention_now) == 2
    assert kpis["overdue"].measured and kpis["attention"].measured

    # A photograph from before the columns existed recorded nothing: unknown.
    OperationalMatterSnapshot.objects.filter(snapshot_date=today - timedelta(days=10)).update(
        is_overdue=None, needs_attention=None
    )
    kpis = _kpis(dc.build_changes(department_head))
    assert kpis["overdue"].value == 1
    assert not kpis["overdue"].measured and not kpis["attention"].measured
    assert kpis["overdue"].note == dc.WORK_STATE_UNMEASURED
    # «Menetluses» is unaffected: open Teemad were always photographed.
    assert kpis["open"].measured


def test_the_page_says_ei_moodetud_and_never_zero(client, department_head, specialist):
    create_matter(title="Avatud", owner=specialist, actor=specialist)
    client.force_login(department_head)

    body = client.get(PAGE).content.decode()

    assert body.count("Periood: ei mõõdetud") == 2
    assert "ei mõõdetud</span>" in body


# =========================================================================
# The per-person table
# =========================================================================


def _people(changes: dc.DepartmentChanges) -> dict[str, dc.PersonRow]:
    return {row.key: row for row in changes.people}


def test_every_additive_column_sums_to_its_kpi(department_head, specialist, other_specialist):
    _world(department_head, specialist, other_specialist)

    changes = dc.build_changes(department_head)
    values = _values(changes)
    rows = changes.people

    assert sum(row.matters for row in rows) == values["matters"]
    assert sum(row.events for row in rows) == values["events"]
    assert sum(row.new for row in rows) == values["new"]
    assert sum(row.sent for row in rows) == values["sent"]
    assert sum(row.closed for row in rows) == values["closed"]
    assert changes.people_total is not None
    assert changes.people_total.matters == values["matters"]
    assert _people(changes)[dc.OWNER_NONE].label == "Vastutajata"


def test_a_change_counts_for_the_current_owner_and_the_row_keeps_its_actor(
    department_head, specialist, other_specialist
):
    """Marko changes a Teema, then it is handed to another lawyer: the line is
    the new owner's, the change row still says Marko did it."""
    marko = other_specialist
    matter = create_matter(title="Üleantav teema", owner=marko, actor=marko)
    change_stage(matter=matter, stage=_stage("idea"), actor=marko)
    assign_matter(matter=matter, owner=specialist, actor=department_head)

    changes = dc.build_changes(department_head)
    people = _people(changes)

    assert set(people) == {str(specialist.pk)}
    assert people[str(specialist.pk)].matters == 1
    assert people[str(specialist.pk)].events == 3
    [stage_row] = [row for row in changes.detail.rows if row.category_key == "hetkeseis"]
    assert stage_row.actor_name == marko.display_name
    assert stage_row.owner_name == specialist.display_name


def test_an_inactive_owner_keeps_a_named_line(department_head, specialist):
    create_matter(title="Lahkunu teema", owner=specialist, actor=specialist)
    specialist.is_active = False
    specialist.save(update_fields=["is_active"])

    row = _people(dc.build_changes(department_head))[str(specialist.pk)]

    assert row.label == f"{specialist.display_name} (mitteaktiivne)"


def test_the_people_table_sorts_by_its_column(department_head, specialist, other_specialist):
    create_matter(title="Üks", owner=specialist, actor=specialist)
    for number in range(2):
        create_matter(title=f"Teine {number}", owner=other_specialist, actor=other_specialist)

    by_count = dc.build_changes(department_head)
    ascending = dc.build_changes(department_head, {"isikud": "teemad"})

    assert [row.key for row in by_count.people] == [str(other_specialist.pk), str(specialist.pk)]
    assert [row.key for row in ascending.people] == [str(specialist.pk), str(other_specialist.pk)]
    assert by_count.people_headings["teemad"].direction == "descending"
    assert ascending.people_headings["teemad"].direction == "ascending"
    assert by_count.people_headings["nimi"].direction == "none"


# =========================================================================
# The change table
# =========================================================================


def _event(matter, actor, event_type, payload, *, step=None):
    """A hand-written audit row; a step event points at a real step, because a
    child's event is read only while its child is visible (`scope_change_events`)."""
    return ChangeEvent.objects.create(
        event_type=event_type,
        matter=matter,
        actor=actor,
        object_type="workflow.NextAction" if step is not None else "",
        object_id=step.pk if step is not None else None,
        payload=payload,
    )


def test_before_and_after_come_only_from_the_payload(department_head, specialist):
    matter = factories.MatterFactory(owner=specialist, title="Tõendite teema")
    step = factories.NextActionFactory(matter=matter)
    _event(
        matter,
        specialist,
        ChangeEventType.NEXT_ACTION_RESCHEDULED,
        {"from": "2026-10-01", "to": "2026-10-15"},
        step=step,
    )
    _event(
        matter,
        specialist,
        ChangeEventType.NEXT_ACTION_SET,
        {"target_date": "2026-11-02"},
        step=step,
    )
    _event(
        matter,
        specialist,
        ChangeEventType.NEXT_ACTION_COMPLETED,
        {"text": "VABA TEKST"},
        step=step,
    )
    _event(matter, specialist, ChangeEventType.MATTER_STAGE_CHANGED, {"to_label": "Idee"})
    _event(
        matter, specialist, ChangeEventType.MATTER_ASSIGNED, {"from_name": None, "to_name": "Kati"}
    )

    cells = {
        row.event_type: (row.before, row.after)
        for row in dc.build_changes(department_head).detail.rows
    }

    assert cells[ChangeEventType.NEXT_ACTION_RESCHEDULED] == ("1.10.2026", "15.10.2026")
    assert cells[ChangeEventType.NEXT_ACTION_SET] == ("teadmata", "2.11.2026")
    assert cells[ChangeEventType.NEXT_ACTION_COMPLETED] == ("teadmata", "")
    # No earlier label recorded: unknown, never today's stage.
    assert cells[ChangeEventType.MATTER_STAGE_CHANGED] == ("teadmata", "Idee")
    # Recorded as nobody: a fact, not an unknown.
    assert cells[ChangeEventType.MATTER_ASSIGNED] == ("vastutajata", "Kati")


def test_the_page_draws_no_free_text(client, department_head, specialist):
    matter = create_matter(title="Vaba tekst", owner=specialist, actor=specialist)
    set_next_action(matter=matter, text="SALAJANE-SAMMU-TEKST", actor=specialist)
    client.force_login(department_head)

    assert "SALAJANE-SAMMU-TEKST" not in client.get(PAGE).content.decode()


def test_the_change_table_sorts_in_the_database(department_head, specialist):
    for title in ("Bravo", "Alfa", "Charlie"):
        create_matter(title=title, owner=specialist, actor=specialist)

    newest = dc.build_changes(department_head)
    by_title = dc.build_changes(department_head, {"sort": "teema"})
    by_title_desc = dc.build_changes(department_head, {"sort": "-teema"})

    assert [row.matter_title for row in newest.detail.rows] == ["Charlie", "Alfa", "Bravo"]
    assert [row.matter_title for row in by_title.detail.rows] == ["Alfa", "Bravo", "Charlie"]
    assert [row.matter_title for row in by_title_desc.detail.rows] == ["Charlie", "Bravo", "Alfa"]
    assert by_title.detail.headings["teema"].direction == "ascending"
    assert newest.detail.headings["aeg"].direction == "descending"


def test_a_closure_is_one_row_in_the_table(department_head, specialist):
    matter = create_matter(title="Suletav", owner=specialist, actor=specialist)
    set_next_action(matter=matter, text="Üks", actor=specialist)
    close_matter(
        matter=matter,
        disposition=Disposition.COMPLETED,
        actor=specialist,
        follow_ups_confirmed=True,
    )

    rows = [
        row
        for row in dc.build_changes(department_head).detail.rows
        if row.matter_title == "Suletav"
    ]

    assert [row.event_type for row in rows].count(dc.CLOSURE_ROW) == 1
    [closure] = [row for row in rows if row.event_type == dc.CLOSURE_ROW]
    assert closure.change == "sulges teema: lõppes 1 tegevus"


def test_the_page_renders_accessible_sortable_tables(client, department_head, specialist):
    create_matter(title="Tabeliteema", owner=specialist, actor=specialist)
    client.force_login(department_head)

    body = client.get(PAGE, {"sort": "teema"}).content.decode()

    assert '<th scope="col" aria-sort="ascending"><a class="colhead colhead--sort"' in body
    assert body.count('<div class="tablewrap chtable">') == 2
    assert '<th scope="row">' in body
    assert "data-person-total" in body


# =========================================================================
# Filters
# =========================================================================


def test_vastutaja_narrows_every_figure_consistently(department_head, specialist, other_specialist):
    _world(department_head, specialist, other_specialist)

    mine = dc.build_changes(department_head, {"vastutaja": str(specialist.pk)})
    nobody = dc.build_changes(department_head, {"vastutaja": "puudub"})

    assert _values(mine)["matters"] == 1
    assert _values(mine)["events"] == 3
    assert _values(mine)["sent"] == 1
    assert _values(mine)["closed"] == 0
    assert {row.key for row in mine.people} == {str(specialist.pk)}
    assert {row.matter_title for row in mine.detail.rows} == {"Esimene teema"}
    assert {row.matter_title for row in nobody.detail.rows} == {"Kolmas teema"}
    assert mine.filters.active and mine.filters.narrows_matters


def test_liik_narrows_the_events_but_not_the_category_counts(
    department_head, specialist, other_specialist
):
    _world(department_head, specialist, other_specialist)

    whole = dc.build_changes(department_head)
    stages = dc.build_changes(department_head, {"liik": "hetkeseis"})

    assert [result.count for result in stages.categories] == [
        result.count for result in whole.categories
    ]
    assert _values(stages)["events"] == 2
    assert _values(stages)["matters"] == 1
    assert _values(stages)["new"] == 0
    # Outputs are not a change category: «Liik» leaves them.
    assert _values(stages)["sent"] == _values(whole)["sent"]
    assert {row.category_key for row in stages.detail.rows} == {"hetkeseis"}
    assert sum(row.events for row in stages.people) == 2
    assert next(result for result in stages.categories if result.key == "hetkeseis").active


def test_otsi_finds_by_title_and_reference(department_head, specialist):
    matter = create_matter(title="Maksueelnõu", owner=specialist, actor=specialist)
    create_matter(title="Muu", owner=specialist, actor=specialist)
    matter.refresh_from_db()

    by_title = dc.build_changes(department_head, {"otsi": "maksu"})
    by_reference = dc.build_changes(department_head, {"otsi": matter.display_reference})

    assert {row.matter_title for row in by_title.detail.rows} == {"Maksueelnõu"}
    assert {row.matter_title for row in by_reference.detail.rows} == {"Maksueelnõu"}


@pytest.mark.parametrize(
    "params",
    [
        {"vastutaja": "not-a-uuid"},
        {"vastutaja": "00000000-0000-0000-0000-000000000000"},
        {"liik": "kõik-muu"},
        {"leht": "abc"},
        {"leht": "999"},
        {"sort": "parool"},
        {"sort": "--aeg"},
        {"isikud": "-"},
        {"otsi": "x" * 5000},
    ],
)
def test_an_unreadable_filter_falls_back_safely(client, department_head, specialist, params):
    create_matter(title="Turvaline", owner=specialist, actor=specialist)
    client.force_login(department_head)

    response = client.get(PAGE, params)

    assert response.status_code == 200
    filters = response.context["page"].filters
    assert filters.owner == "" and filters.category == ""
    assert filters.sort == dc.DEFAULT_DETAIL_SORT
    assert filters.people_sort == dc.DEFAULT_PEOPLE_SORT
    assert len(filters.search) <= dc.SEARCH_MAX


def test_filters_survive_sorting_paging_and_the_window(client, department_head, specialist):
    create_matter(title="Säiliv", owner=specialist, actor=specialist)
    client.force_login(department_head)
    params = {"periood": "30", "vastutaja": str(specialist.pk), "otsi": "säil", "liik": "uued"}

    page = client.get(PAGE, params).context["page"]

    for query in (
        page.detail.headings["teema"].query,
        page.people_headings["uued"].query,
        page.detail.query_string,
        next(option.query for option in page.period_options if option.key == "14"),
    ):
        assert f"vastutaja={specialist.pk}" in query
        assert "liik=uued" in query
        assert "otsi=" in query
    assert page.clear_filters_query == "periood=30"


def test_the_owner_choices_are_workers_and_current_owners(
    department_head, specialist, other_specialist
):
    create_matter(title="Lahkunu teema", owner=other_specialist, actor=other_specialist)
    other_specialist.is_active = False
    other_specialist.save(update_fields=["is_active"])

    choices = {
        choice.value: choice.label for choice in dc.build_changes(department_head).owner_choices
    }

    assert str(specialist.pk) in choices
    assert choices[str(other_specialist.pk)].endswith("(mitteaktiivne)")


# =========================================================================
# Authorization before arithmetic
# =========================================================================


def test_an_outsider_gets_nothing_in_any_figure_row_or_search(reader, specialist, administrator):
    hidden = create_matter(
        title="SALAJANE-KPI-TEEMA",
        owner=specialist,
        actor=specialist,
        visibility=Visibility.RESTRICTED,
    )
    change_stage(matter=hidden, stage=_stage("idea"), actor=specialist)
    _sent(hidden)
    close_matter(
        matter=hidden,
        disposition=Disposition.COMPLETED,
        actor=specialist,
        follow_ups_confirmed=True,
    )

    for_reader = dc.build_changes(reader, {"otsi": "SALAJANE"})
    for_outsider = dc.build_changes(administrator, {"otsi": "SALAJANE"})

    assert _values(for_reader)["matters"] == 1
    assert _values(for_reader)["sent"] == 1
    assert {row.matter_title for row in for_reader.detail.rows} == {"SALAJANE-KPI-TEEMA"}
    for key in ("matters", "events", "new", "sent", "closed", "attention", "overdue"):
        assert _values(for_outsider)[key] == 0, key
    assert for_outsider.people == []
    assert for_outsider.detail.rows == []


def test_a_restricted_step_reaches_no_count_for_an_outsider(reader, specialist, administrator):
    matter = create_matter(title="Tavaline", owner=specialist, actor=specialist)
    action = set_next_action(matter=matter, text="Piiratud samm", actor=specialist)
    action.visibility_override = Visibility.RESTRICTED
    action.save(update_fields=["visibility_override"])

    assert _values(dc.build_changes(reader))["events"] == 2
    outsider = dc.build_changes(administrator)
    assert _values(outsider)["events"] == 0
    assert outsider.detail.rows == [] and outsider.people == []


# =========================================================================
# Bounded queries
# =========================================================================


def _queries(user, params) -> int:
    with CaptureQueriesContext(connection) as captured:
        dc.build_changes(user, params)
    return len(captured.captured_queries)


@pytest.mark.parametrize(
    "params",
    [{}, {"liik": "hetkeseis", "otsi": "Koormus", "sort": "teema", "isikud": "nimi", "leht": "1"}],
    ids=["unfiltered", "filtered"],
)
def test_more_changes_cost_no_more_queries_with_filters(
    department_head, specialist, other_specialist, params
):
    def busy(count: int) -> None:
        for number in range(count):
            matter = create_matter(title=f"Koormus {number}", owner=specialist, actor=specialist)
            change_stage(matter=matter, stage=_stage("idea"), actor=specialist)
            assign_matter(matter=matter, owner=other_specialist, actor=department_head)
            set_next_action(matter=matter, text=f"Samm {number}", actor=other_specialist)
            _sent(matter)
            if number % 2:
                close_matter(
                    matter=matter,
                    disposition=Disposition.COMPLETED,
                    actor=other_specialist,
                    follow_ups_confirmed=True,
                )

    params = {**params, "vastutaja": str(other_specialist.pk)} if params else params
    busy(2)
    few = _queries(department_head, params)
    busy(6)
    many = _queries(department_head, params)

    assert many == few
    # No photographs here; two more queries read them where they exist.
    assert few <= 32


# =========================================================================
# The photograph's work-state memberships (D4)
# =========================================================================


def test_capture_records_the_memberships_and_the_step_facts(specialist):
    today = timezone.localdate()
    late = create_matter(title="Hilinenud", owner=specialist, actor=specialist)
    action = set_next_action(
        matter=late,
        text="Tähtaeg möödas",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today - timedelta(days=1),
        actor=specialist,
    )
    quiet = create_matter(title="Vaikne", owner=specialist, actor=specialist)

    capture(on=today)

    rows = {row.matter_id: row for row in OperationalMatterSnapshot.objects.all()}
    assert rows[late.pk].is_overdue is True
    assert rows[late.pk].needs_attention is True
    assert rows[late.pk].next_action_date_precision == action.date_precision
    assert rows[late.pk].next_action_responsible_id == action.responsible_id
    assert rows[quiet.pk].is_overdue is False
    # No next action: «Vajab sekkumist», not overdue.
    assert rows[quiet.pk].needs_attention is True


def test_a_narrower_scope_reads_no_membership(monkeypatch, specialist, reader):
    create_matter(title="Pildistatud", owner=specialist, actor=specialist)
    capture(on=timezone.localdate())

    row = visible_snapshots(reader).get()
    assert row.visible_is_overdue is False and row.visible_needs_attention is True

    # A scope that reads Matters but not every restricted child cannot know
    # whether a membership came from a child it may not see: NULL, «ei mõõdetud».
    narrow = Scope(
        user=reader, is_authenticated=True, sees_restricted_by_role=False, break_glass_grant_id=None
    )
    monkeypatch.setattr(snapshot_selectors, "scope_for_user", lambda user: narrow)
    row = visible_snapshots(reader).get()
    assert row.visible_is_overdue is None and row.visible_needs_attention is None


def _local(day: date, clock: time) -> datetime:
    return datetime.combine(day, clock, tzinfo=TALLINN)


def test_the_daily_capture_waits_for_three_and_runs_once(specialist):
    day = timezone.localdate()
    matter = create_matter(title="Hommikune", owner=specialist, actor=specialist)

    assert capture_daily_if_missing(_local(day, time(2, 59))) is False
    assert not OperationalMatterSnapshot.objects.exists()

    assert capture_daily_if_missing(_local(day, time(3, 0))) is True
    first = OperationalMatterSnapshot.objects.get(snapshot_date=day)

    # Later the same day, with the Teema changed: no second, overwriting capture.
    assign_matter(matter=matter, owner=None, actor=specialist)
    assert capture_daily_if_missing(_local(day, time(12, 0))) is False
    again = OperationalMatterSnapshot.objects.get(snapshot_date=day)
    assert again.captured_at == first.captured_at
    assert again.owner_id == specialist.pk


def test_the_daily_capture_reads_the_tallinn_day(specialist):
    create_matter(title="Kesköö", owner=specialist, actor=specialist)
    # 01:30 UTC on 31 October is 03:30 in Tallinn (EET, UTC+2 from 25 October).
    moment = datetime(2026, 10, 31, 1, 30, tzinfo=ZoneInfo("UTC"))

    assert capture_daily_if_missing(moment) is True
    assert set(OperationalMatterSnapshot.objects.values_list("snapshot_date", flat=True)) == {
        date(2026, 10, 31)
    }
