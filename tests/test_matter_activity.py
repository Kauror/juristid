"""When work last happened on a Matter, as opposed to when its row was written.

The failure this guards against is quiet. A 2015 register row touched by the
2026 cutover has a 2026 ``updated_at``, and a *Viimane tegevus* column reading
from it says a lawyer was on the file this morning. Nothing looks broken; the
column is simply wrong about most of the register.

Every title and section below is invented.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.core.enums import Visibility
from app.documents.enums import DocumentRole
from app.documents.services import add_evidence_version, create_document
from app.legacy_import.source_pages import (
    LegacySourcePage,
    MatterSourcePage,
    SourceMatchClass,
    SourceMatchMethod,
    SourcePageRole,
    SourceRelationshipKind,
    SourceSystem,
)
from app.matters.activity import (
    ActivityBasis,
    activity_for_matter,
    activity_of,
    annotate_last_activity,
)
from app.matters.enums import MatterOrigin
from app.matters.models import Matter
from app.matters.selectors import matter_list_queryset
from app.submissions.enums import SubmissionStatus
from app.workflow.enums import ActionStatus, Disposition
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db


def _at(year: int, month: int = 6, day: int = 15) -> dt.datetime:
    return timezone.make_aware(dt.datetime(year, month, day, 12, 0))


def _touched_in(matter: Matter, year: int) -> Matter:
    """Force ``updated_at``, the way an import run would leave it.

    ``update()`` rather than ``save()``: ``auto_now`` would overwrite exactly
    the value the test is about.
    """
    Matter.objects.filter(pk=matter.pk).update(updated_at=_at(year, 2, 3))
    matter.refresh_from_db()
    return matter


def _imported(**kwargs) -> Matter:
    return factories.ArchiveMatterFactory(origin=MatterOrigin.LEGACY_IMPORT, **kwargs)


def _page(key: str, *, created: int, modified: int | None = None) -> LegacySourcePage:
    now = timezone.now()
    return LegacySourcePage.objects.create(
        source_system=SourceSystem.ONENOTE_DESKTOP,
        source_page_id=f"1-{key}",
        page_key=key,
        source_notebook="Näidiskoja õigusloome",
        source_section="ARHIIV näidisvaldkond",
        title=f"Näidisleht {key}",
        page_role=SourcePageRole.MATTER_LIKE,
        capture_id=f"capture-{key}",
        source_created_at=_at(created),
        source_modified_at=_at(modified) if modified else None,
        first_imported_at=now,
        latest_imported_at=now,
    )


def _link(matter, page, kind=SourceRelationshipKind.PRIMARY) -> MatterSourcePage:
    return MatterSourcePage.objects.create(
        matter=matter,
        source_page=page,
        relationship_kind=kind,
        match_method=SourceMatchMethod.EXCEL_EXACT_PAGE_ID,
        match_class=SourceMatchClass.EXACT,
    )


def _fact(matter: Matter, user):
    annotated = annotate_last_activity(Matter.objects.filter(pk=matter.pk), user).get()
    return activity_of(annotated)


# -- the regression this exists for -----------------------------------------


def test_an_import_timestamp_is_never_reported_as_activity(specialist):
    """A 2015 file imported in 2026 whose page was last edited in 2018.

    The answer is 2018. ``updated_at`` says 2026 and says it about the importer.
    """
    matter = _touched_in(_imported(), 2026)
    _link(matter, _page("a", created=2017, modified=2018))

    fact = _fact(matter, specialist)
    assert fact is not None
    assert fact.occurred_on.year == 2018
    assert fact.basis == ActivityBasis.ONENOTE_MODIFIED
    assert fact.is_source_derived is True


def test_the_modified_date_beats_the_created_date_on_the_same_page(specialist):
    matter = _touched_in(_imported(), 2026)
    _link(matter, _page("a", created=2018, modified=2019))

    fact = _fact(matter, specialist)
    assert fact.occurred_on.year == 2019
    assert fact.basis == ActivityBasis.ONENOTE_MODIFIED


def test_a_page_with_no_modified_date_falls_back_to_its_creation(specialist):
    matter = _touched_in(_imported(), 2026)
    _link(matter, _page("a", created=2018))

    fact = _fact(matter, specialist)
    assert fact.occurred_on.year == 2018
    assert fact.basis == ActivityBasis.ONENOTE_CREATED


# -- the latest fact wins, whatever kind it is ------------------------------


def test_a_real_recorded_closure_later_than_the_page_wins(specialist):
    """2021 closure over a 2019 page edit."""
    matter = _touched_in(
        _imported(is_open=False, closed_at=_at(2021, 3, 4)),
        2026,
    )
    _link(matter, _page("a", created=2018, modified=2019))

    fact = _fact(matter, specialist)
    assert fact.occurred_on.year == 2021
    assert fact.basis == ActivityBasis.CLOSURE


def test_a_page_edited_after_the_closure_wins_over_the_closure(specialist):
    """The mirror image, and why this is not a source-priority list.

    A fixed "canonical beats source" order would answer one of these two pairs
    wrongly. The rule is the latest actual fact.
    """
    matter = _touched_in(
        _imported(is_open=False, closed_at=_at(2019, 3, 4)),
        2026,
    )
    _link(matter, _page("a", created=2018, modified=2021))

    fact = _fact(matter, specialist)
    assert fact.occurred_on.year == 2021
    assert fact.basis == ActivityBasis.ONENOTE_MODIFIED


def test_the_latest_of_several_linked_pages_is_used(specialist):
    """Not the first, and not the PRIMARY one.

    A RELATED page can legitimately carry the later work — the corpus attaches
    several pages to one Matter precisely because the work moved between them.
    """
    matter = _touched_in(_imported(), 2026)
    _link(matter, _page("a", created=2017, modified=2018))
    _link(matter, _page("b", created=2020, modified=2021), SourceRelationshipKind.RELATED)

    fact = _fact(matter, specialist)
    assert fact.occurred_on.year == 2021


def test_a_background_page_does_not_speak_for_the_chronology(specialist):
    """Its edit date says when somebody filed reference material, not when work
    happened on this file."""
    matter = _touched_in(_imported(), 2026)
    _link(matter, _page("a", created=2017, modified=2018))
    _link(matter, _page("b", created=2020, modified=2021), SourceRelationshipKind.BACKGROUND)

    fact = _fact(matter, specialist)
    assert fact.occurred_on.year == 2018


def test_an_authored_entry_is_activity(specialist):
    matter = factories.MatterFactory(owner=specialist)
    factories.EntryFactory(matter=matter, author=specialist, occurred_at=_at(2026, 4, 2))

    fact = _fact(matter, specialist)
    assert fact.occurred_on == dt.date(2026, 4, 2)
    assert fact.basis == ActivityBasis.ENTRY


def _sent_submission(matter, sent_at):
    """A SENT submission with the final evidence the database insists on."""
    document = create_document(
        matter=matter, title="Näidisarvamus", role=DocumentRole.KODA_SUBMISSION_FINAL
    )
    version = add_evidence_version(
        document=document,
        content=b"%PDF-1.4 synthetic",
        original_filename="naidis.pdf",
        mime_type="application/pdf",
    )
    return factories.SubmissionFactory(
        matter=matter,
        status=SubmissionStatus.SENT,
        sent_at=sent_at,
        final_version=version,
    )


def test_a_submission_sent_after_the_last_entry_wins(specialist):
    matter = factories.MatterFactory(owner=specialist)
    factories.EntryFactory(matter=matter, author=specialist, occurred_at=_at(2026, 4, 2))
    _sent_submission(matter, _at(2026, 5, 9))

    fact = _fact(matter, specialist)
    assert fact.occurred_on == dt.date(2026, 5, 9)
    assert fact.basis == ActivityBasis.SUBMISSION


def test_withdrawing_a_submission_does_not_un_happen_the_sending(specialist):
    """It went out that day. The withdrawal is a later fact, not an eraser."""
    matter = factories.MatterFactory(owner=specialist, received_date=dt.date(2026, 1, 3))
    factories.SubmissionFactory(
        matter=matter, status=SubmissionStatus.WITHDRAWN, sent_at=_at(2026, 5, 9)
    )

    fact = _fact(matter, specialist)
    assert fact.occurred_on == dt.date(2026, 5, 9)
    assert fact.basis == ActivityBasis.SUBMISSION


def test_the_received_date_is_a_fallback_and_never_the_answer_when_later_work_exists(
    specialist,
):
    matter = factories.MatterFactory(owner=specialist, received_date=dt.date(2026, 1, 8))
    assert _fact(matter, specialist).basis == ActivityBasis.RECEIVED

    factories.EntryFactory(matter=matter, author=specialist, occurred_at=_at(2026, 3, 3))
    assert _fact(matter, specialist).basis == ActivityBasis.ENTRY


def test_nothing_known_is_reported_as_nothing_known(specialist):
    """An archive row with no dates has no activity.

    Today, the import date and a dash that looks like a date would each be an
    invention.
    """
    matter = _touched_in(_imported(received_date=None), 2026)
    assert _fact(matter, specialist) is None


# -- next actions -----------------------------------------------------------


def test_an_action_a_person_set_is_activity(specialist):
    matter = factories.MatterFactory(owner=specialist)
    set_next_action(
        matter=matter, text="Käsitsi määratud", actor=specialist, target_date=dt.date(2099, 1, 1)
    )

    fact = _fact(matter, specialist)
    assert fact.basis == ActivityBasis.NEXT_ACTION
    assert fact.occurred_on == timezone.localdate()


def test_an_action_nobody_set_is_not_activity(specialist):
    """The enrichment's own actions must not become "activity today".

    That is the same error as ``updated_at``, arriving through a different
    column: a machine wrote the row, so its timestamp is processing time.
    """
    matter = _touched_in(_imported(record_mode="FULL"), 2026)
    _link(matter, _page("a", created=2017, modified=2018))
    set_next_action(
        matter=matter,
        text="Ootan eelnõud 2027. aasta 2. kvartalis",
        actor=None,
        kind="WAIT",
        date_semantics="EXPECTED_AROUND",
        target_date=dt.date(2027, 4, 1),
    )

    fact = _fact(matter, specialist)
    assert fact.occurred_on.year == 2018
    assert fact.basis == ActivityBasis.ONENOTE_MODIFIED


# -- native records ---------------------------------------------------------


def test_a_native_matter_falls_back_to_its_own_row_timestamp(specialist):
    """For a Matter created here, the system is the authoritative record."""
    matter = _touched_in(factories.MatterFactory(owner=specialist, received_date=None), 2026)

    fact = _fact(matter, specialist)
    assert fact is not None
    assert fact.basis == ActivityBasis.NATIVE_RECORD
    assert fact.occurred_on.year == 2026


def test_an_imported_matter_never_falls_back_to_its_row_timestamp(specialist):
    matter = _touched_in(_imported(received_date=None), 2026)
    assert _fact(matter, specialist) is None


# -- authorization ----------------------------------------------------------


def test_a_restricted_entry_does_not_announce_itself_through_the_date(specialist, other_specialist):
    """A date column is a channel like any other.

    Somebody who cannot open the entry must not learn from the register that
    something happened on 3 March.
    """
    matter = factories.MatterFactory(owner=specialist, received_date=dt.date(2026, 1, 8))
    factories.EntryFactory(
        matter=matter,
        author=specialist,
        occurred_at=_at(2026, 3, 3),
        visibility_override=Visibility.RESTRICTED,
    )

    assert _fact(matter, specialist).basis == ActivityBasis.ENTRY
    assert _fact(matter, other_specialist).basis == ActivityBasis.RECEIVED


# -- cost -------------------------------------------------------------------


def test_reading_the_activity_costs_no_query_per_row(specialist):
    """Six facts per Matter, and a list is not allowed to fetch them per row."""
    for index in range(10):
        matter = factories.MatterFactory(owner=specialist, title=f"Teema {index}")
        _link(matter, _page(f"p{index}", created=2018, modified=2019))

    rows = list(annotate_last_activity(matter_list_queryset(specialist), specialist))
    with CaptureQueriesContext(connection) as captured:
        facts = [activity_of(row) for row in rows]

    assert len(facts) == 10
    assert all(fact is not None for fact in facts)
    assert len(captured) == 0


def test_the_query_count_does_not_follow_the_population(specialist):
    def cost() -> int:
        with CaptureQueriesContext(connection) as captured:
            for row in annotate_last_activity(matter_list_queryset(specialist), specialist):
                activity_of(row)
        return len(captured)

    for index in range(5):
        factories.MatterFactory(owner=specialist, title=f"Väike {index}")
    small = cost()

    for index in range(5, 40):
        factories.MatterFactory(owner=specialist, title=f"Suur {index}")
    assert cost() == small


def test_reading_activity_without_the_annotations_is_refused(specialist):
    """The guard that keeps an N+1 from reappearing as a convenience."""
    matter = factories.MatterFactory(owner=specialist)
    with pytest.raises(ValueError):
        activity_of(matter)


def test_the_single_matter_helper_answers_the_same_way(specialist):
    matter = _touched_in(_imported(), 2026)
    _link(matter, _page("a", created=2017, modified=2018))

    assert activity_for_matter(matter, specialist) == _fact(matter, specialist)


# -- what it must not do ----------------------------------------------------


def test_nothing_writes_a_closure_date_onto_a_historical_row(specialist):
    """Activity display is not closure reconstruction.

    A page edited in 2019 does not mean the file closed in 2019, and reading
    this must not make it look as though it did.
    """
    matter = _touched_in(_imported(), 2026)
    _link(matter, _page("a", created=2018, modified=2019))

    _fact(matter, specialist)
    matter.refresh_from_db()
    assert matter.closed_at is None
    assert matter.disposition == ""
    assert matter.is_open is True


def test_nothing_rewrites_the_row_timestamps(specialist):
    matter = _touched_in(_imported(), 2026)
    _link(matter, _page("a", created=2018, modified=2019))
    before = matter.updated_at

    _fact(matter, specialist)
    matter.refresh_from_db()
    assert matter.updated_at == before


def test_a_cancelled_action_a_person_ended_still_counts(specialist):
    """Ending an instruction is work somebody did."""
    from app.workflow.services import cancel_next_action

    matter = factories.MatterFactory(owner=specialist)
    action = set_next_action(
        matter=matter, text="Tegevus", actor=specialist, target_date=dt.date(2099, 1, 1)
    )
    cancel_next_action(action=action, actor=specialist)

    action.refresh_from_db()
    assert action.status == ActionStatus.CANCELLED
    fact = _fact(matter, specialist)
    assert fact.basis == ActivityBasis.NEXT_ACTION


def test_a_closed_native_matter_reports_its_closure(specialist):
    matter = factories.MatterFactory(
        owner=specialist,
        is_open=False,
        closed_at=_at(2026, 7, 1),
        disposition=Disposition.COMPLETED,
    )
    fact = _fact(matter, specialist)
    assert fact.basis == ActivityBasis.CLOSURE
    assert fact.occurred_on == dt.date(2026, 7, 1)
