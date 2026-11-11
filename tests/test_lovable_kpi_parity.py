"""«Arvamusi koostamisel» is Lovable's definition, read from Juristid's records.

docs/adr/0149, amendment of 2026-11-11 (round-4 owner brief: equivalent
dashboard metrics reconcile after a successful synchronisation). Lovable counts
every topic of its operational register whose ``valja_kuupaev`` is empty and
whose ``valja`` is not «ei saatnud» — the opinion *outcome*, never a «Koostan
arvamust» activity:

* ``src/routes/_authenticated/index.tsx``: ``topics.filter((t) =>
  !t.valja_kuupaev && !isEiSaatnud(t.valja))``;
* ``src/lib/dashboard/logic.ts``: ``isEiSaatnud(valja) =
  normalizeText(valja).includes("ei saatnud")``.

Each case below states one Lovable row and the Juristid records that mirror it,
and asserts that Juristid's figure — Osakond's strip, its team column, Minu
asjad and the register list — counts exactly the Matters Lovable's formula
counts on the same data. Every title is invented.
"""

from __future__ import annotations

import datetime as dt
import unicodedata
from dataclasses import dataclass
from typing import Any

import pytest

from app.core.enums import Visibility
from app.matters import department_dashboard as dd
from app.matters.dashboard import drafting_matters
from app.matters.enums import MatterDataClass, RecordMode, ResponseDeadlineOutcome
from app.matters.models import Matter
from app.matters.my_work import opinion_work
from app.matters.register_filters import (
    OPEN_FULL,
    OPINION_DRAFTING,
    opinion_outcome_recorded_q,
    register_population,
)
from app.matters.response_deadlines import (
    deadline_revision,
    request_response_deadline,
    resolve_response_deadline,
)
from app.matters.services import close_matter, create_matter
from app.submissions.services import create_submission
from app.workflow.enums import ActionKind, DateSemantics, Disposition
from app.workflow.services import OPINION_PREPARATION_TEXT, set_next_action
from tests import factories
from tests.follow_ups import send_koja_arvamus

pytestmark = pytest.mark.django_db

TODAY = dt.date(2026, 11, 11)


# ---------------------------------------------------------------------------
# Lovable's formula, transcribed
# ---------------------------------------------------------------------------


def _normalize(value: str | None) -> str:
    """`logic.normalizeText`: trim, lower case, collapse spaces, fold õäöüšž."""
    text = " ".join((value or "").strip().lower().split())
    for source, target in (("õ", "o"), ("ä", "a"), ("ö", "o"), ("ü", "u"), ("š", "s"), ("ž", "z")):
        text = text.replace(source, target)
    return unicodedata.normalize("NFC", text)


def lovable_koostamisel(valja: str | None, valja_kuupaev: str | None) -> bool:
    """`!t.valja_kuupaev && !isEiSaatnud(t.valja)` (index.tsx, topicsPage.kpiTopics)."""
    return not valja_kuupaev and "ei saatnud" not in _normalize(valja)


@dataclass(frozen=True)
class Row:
    """One Lovable operational topic, as far as the KPI reads it."""

    valja: str | None
    valja_kuupaev: str | None


def test_the_transcription_reads_lovables_spellings() -> None:
    assert lovable_koostamisel(None, None)
    assert lovable_koostamisel("  ", None)
    assert not lovable_koostamisel("Ei  Saatnud", None)
    assert not lovable_koostamisel("2026-11-04", "2026-11-04")
    # A non-date word that is not «ei saatnud» leaves the topic being drafted.
    assert lovable_koostamisel("ootel", None)


# ---------------------------------------------------------------------------
# Juristid's records for each Lovable row
# ---------------------------------------------------------------------------


@pytest.fixture
def addressee(db):
    return factories.OrganisationFactory(name="Sünteetiline ministeerium")


@pytest.fixture
def make(specialist):
    def build(title: str, **kw: Any) -> Matter:
        return create_matter(title=title, actor=specialist, owner=specialist, **kw)

    return build


def _requested(matter: Matter, actor: Any, day: dt.date) -> Matter:
    request_response_deadline(matter=matter, deadline=day, actor=actor)
    matter.refresh_from_db()
    return matter


def _drafting_step(matter: Matter, actor: Any) -> None:
    """The open DO «Koostan arvamuse» Juristid held before round 4 (owner's 17)."""
    set_next_action(matter=matter, text=OPINION_PREPARATION_TEXT, actor=actor)


def _ids(queryset: Any) -> set[Any]:
    return set(queryset.values_list("pk", flat=True))


@pytest.fixture
def world(make, specialist, addressee):
    """The owner's known discrepancies, and the ordinary cases beside them.

    Each entry: Lovable row → the Matter whose Juristid records mirror it.
    """
    cases: dict[str, tuple[Row, Matter]] = {}

    # 2026_216 / 2026_232 / 2026_238 / 2026_253: «ei saatnud» on a live stage,
    # while Juristid still held an open DO «Koostan arvamuse». The outcome
    # decides: the step does not.
    declined = _requested(make("Ei saatnud, DO jäi avatuks"), specialist, dt.date(2026, 11, 4))
    _drafting_step(declined, specialist)
    resolve_response_deadline(
        matter=declined, outcome=ResponseDeadlineOutcome.NOT_ANSWERING, actor=specialist
    )
    cases["ei_saatnud_with_open_step"] = (Row("ei saatnud", None), declined)

    # 2026_225: sent on 04.11 through a Koja arvamus answering the request.
    sent = _requested(make("Saadetud 04.11"), specialist, dt.date(2026, 11, 4))
    _drafting_step(sent, specialist)
    send_koja_arvamus(
        sent,
        specialist,
        [addressee],
        dt.date(2026, 11, 4),
        answers_deadline=deadline_revision(sent),
    )
    cases["sent_with_open_step"] = (Row("2026-11-04", "2026-11-04"), sent)

    # 2026_259: sent per Lovable, no document imported — the request answered in
    # words, no Submission (brief: show the status without fabricating a file).
    answered = _requested(make("Saadetud, dokumenti pole"), specialist, dt.date(2026, 11, 10))
    resolve_response_deadline(
        matter=answered,
        outcome=ResponseDeadlineOutcome.ANSWERED,
        note="Lovable: arvamus saadetud 10.11.2026.",
        actor=specialist,
    )
    cases["sent_without_document"] = (Row("2026-11-10", "2026-11-10"), answered)

    # Blank VÄLJA with a deadline that has passed: still being drafted.
    passed = _requested(make("Tähtaeg möödas"), specialist, dt.date(2026, 10, 1))
    cases["blank_passed_deadline"] = (Row(None, None), passed)

    # Blank VÄLJA, deadline ahead, the lawyer only waiting («Ootan …»): Lovable
    # counts it — Juristid's old rule did not, for lack of a DO step.
    waiting = _requested(make("Ootan, tähtaeg ees"), specialist, dt.date(2026, 11, 30))
    set_next_action(
        matter=waiting,
        text="Ootan eelnõu",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=dt.date(2026, 11, 20),
        actor=specialist,
    )
    cases["blank_waiting"] = (Row("", None), waiting)

    # Blank VÄLJA and no deadline at all.
    bare = make("Tühi VÄLJA, tähtajata")
    cases["blank_no_deadline"] = (Row(None, None), bare)

    # Sent once, a second draft written afterwards: Lovable has one VÄLJA, a date.
    redrafted = _requested(make("Saadetud, uus mustand"), specialist, dt.date(2026, 10, 20))
    send_koja_arvamus(
        redrafted,
        specialist,
        [addressee],
        dt.date(2026, 10, 19),
        answers_deadline=deadline_revision(redrafted),
    )
    create_submission(matter=redrafted, title="Täiendav mustand", actor=specialist)
    cases["sent_then_draft"] = (Row("2026-10-19", "2026-10-19"), redrafted)

    return cases


def test_each_known_discrepancy_now_reads_as_lovable_reads_it(department_head, world) -> None:
    counted = _ids(drafting_matters(department_head))
    for name, (row, matter) in world.items():
        assert (matter.pk in counted) == lovable_koostamisel(row.valja, row.valja_kuupaev), name


def test_the_figure_is_lovables_count_on_the_same_data(department_head, world) -> None:
    expected = {
        m.pk for row, m in world.values() if lovable_koostamisel(row.valja, row.valja_kuupaev)
    }
    assert _ids(drafting_matters(department_head)) == expected
    figures = {f.key: f.value for f in dd.seis_figures(department_head, TODAY)}
    assert figures["drafting"] == len(expected) == 3


def test_the_figure_the_column_and_the_list_are_one_population(
    department_head, world, specialist
) -> None:
    """Osakond's strip, its per-owner column, Minu asjad and the register list agree."""
    population = Matter.objects.visible_to(department_head)
    listed = register_population(
        department_head,
        {**OPEN_FULL, "arvamus": OPINION_DRAFTING},
        today=TODAY,
        population=population,
    )
    counted = drafting_matters(department_head)
    assert _ids(listed) == _ids(counted)
    by_owner = dd._by_owner(counted)
    assert sum(by_owner.values()) == counted.count()
    assert by_owner.get(specialist.pk, 0) == counted.count()
    assert opinion_work(department_head, specialist, TODAY).in_preparation == counted.count()


def test_the_open_drafting_step_alone_no_longer_decides(department_head, make, specialist) -> None:
    """The owner's point: an activity is not an opinion outcome."""
    matter = make("DO ilma tähtajata")
    _drafting_step(matter, specialist)
    assert matter.pk in _ids(drafting_matters(department_head))  # blank VÄLJA: drafting
    outcome = Matter.objects.filter(pk=matter.pk).filter(
        opinion_outcome_recorded_q(department_head)
    )
    assert not outcome.exists()


def test_a_cancelled_or_moved_request_is_not_an_outcome(department_head, make, specialist) -> None:
    matter = _requested(make("Tühistatud küsimine"), specialist, dt.date(2026, 11, 4))
    resolve_response_deadline(
        matter=matter, outcome=ResponseDeadlineOutcome.CANCELLED, actor=specialist
    )
    assert matter.pk in _ids(drafting_matters(department_head))


def test_a_later_request_after_a_sent_opinion_is_drafting_again(
    department_head, make, specialist, addressee
) -> None:
    """Lovable records a new consultation as a new topic; one Teema reads the same way."""
    matter = _requested(make("Kaks küsimist"), specialist, dt.date(2026, 10, 15))
    send_koja_arvamus(
        matter,
        specialist,
        [addressee],
        dt.date(2026, 10, 9),
        answers_deadline=deadline_revision(matter),
    )
    assert matter.pk not in _ids(drafting_matters(department_head))
    matter.refresh_from_db()
    _requested(matter, specialist, dt.date(2026, 12, 1))
    assert matter.pk in _ids(drafting_matters(department_head))


# ---------------------------------------------------------------------------
# The population: lifecycle, history cohort, test rows, authorization
# ---------------------------------------------------------------------------


def test_a_closed_teema_is_not_counted(department_head, make, specialist) -> None:
    """Lovable's `aktiivne` false (jõustunud, rohkem ei tegele, continued) is a closed Teema."""
    matter = make("Jõustunud")
    close_matter(
        follow_ups_confirmed=True,
        matter=matter,
        disposition=Disposition.MONITORING_STOPPED,
        actor=specialist,
    )
    assert matter.pk not in _ids(drafting_matters(department_head))


def test_the_history_cohort_is_not_counted(department_head, make) -> None:
    """Non-operational archive Teemad (Lovable: outside `operatiivne_ulatus`)."""
    matter = make("Ajalooline")
    Matter.objects.filter(pk=matter.pk).update(record_mode=RecordMode.ARCHIVE)
    assert matter.pk not in _ids(drafting_matters(department_head))


def test_test_rows_stay_out_of_the_published_count(department_head, make) -> None:
    """Lovable keeps test topics in a separate table; Statistika's `andmed=paris` does too."""
    real = make("Päris")
    test = make("Arendusandmed")
    Matter.objects.filter(pk=test.pk).update(data_class=MatterDataClass.TEST)
    population = Matter.objects.visible_to(department_head)
    published = register_population(
        department_head,
        {**OPEN_FULL, "arvamus": OPINION_DRAFTING, "andmed": "paris"},
        today=TODAY,
        population=population,
    )
    assert _ids(published) == {real.pk}


def test_a_restricted_teema_counts_for_its_readers_and_no_outsider(
    department_head, reader, administrator, make
) -> None:
    """Authorization before arithmetic: an administrator without a business role reads none."""
    hidden = make("Piiratud", visibility=Visibility.RESTRICTED)
    assert hidden.pk in _ids(drafting_matters(department_head))
    assert hidden.pk in _ids(drafting_matters(reader))
    assert drafting_matters(administrator).count() == 0
    assert {f.key: f.value for f in dd.seis_figures(administrator, TODAY)}["drafting"] == 0


def test_sent_still_means_a_date(department_head, make, specialist) -> None:
    """`?arvamus=saadetud` is unchanged: «ei saatnud» is never sent (RULE-04)."""
    matter = _requested(make("Ei saatnud"), specialist, dt.date(2026, 11, 4))
    resolve_response_deadline(
        matter=matter, outcome=ResponseDeadlineOutcome.NOT_ANSWERING, actor=specialist
    )
    population = Matter.objects.visible_to(department_head)
    sent = register_population(
        department_head, {**OPEN_FULL, "arvamus": "saadetud"}, today=TODAY, population=population
    )
    assert matter.pk not in _ids(sent)


# ---------------------------------------------------------------------------
# Lovable's own outcome, as the synchronisation records it (docs/adr/0213 §10)
# ---------------------------------------------------------------------------


def _lovable_outcome(matter: Matter, outcome: str, sent_on: dt.date | None = None) -> Any:
    import uuid

    from app.legacy_import.lovable_sync_models import (
        LovableOpinionOutcome,
        LovableSyncRun,
        SyncMode,
        SyncTrigger,
    )

    at = dt.datetime(2026, 11, 11, 3, 0, tzinfo=dt.UTC)
    run = LovableSyncRun.objects.create(
        run_key=f"kpi-{uuid.uuid4().hex[:12]}",
        trigger=SyncTrigger.SCHEDULED,
        mode=SyncMode.APPLY,
        started_at=at,
        mapping_version="lovable-sync/test",
    )
    previous = (
        LovableOpinionOutcome.objects.filter(matter=matter, superseded_by__isnull=True)
        .order_by("-recorded_at")
        .first()
    )
    return LovableOpinionOutcome.objects.create(
        matter=matter,
        source_id=uuid.uuid4(),
        outcome=outcome,
        sent_on=sent_on,
        previous=previous,
        run=run,
        source_snapshot_sha256="0" * 64,
        recorded_at=at + dt.timedelta(minutes=LovableOpinionOutcome.objects.count()),
    )


def test_lovables_current_outcome_decides_over_an_owed_request(
    department_head, make, specialist
) -> None:
    """Lovable is authoritative: «ei saatnud» or a sent date ends «koostamisel»
    even while a Juristid request still reads as owed; a later CLEARED row
    returns the topic to Lovable's blank VÄLJA, and so to «koostamisel»."""
    from app.legacy_import.lovable_sync_models import OpinionOutcome
    from app.matters.register_filters import OPINION_SENT, opinion_state_q

    declined = _requested(make("Lovable: ei saatnud"), specialist, dt.date(2026, 11, 4))
    sent = _requested(make("Lovable: saadetud"), specialist, dt.date(2026, 11, 4))
    assert {declined.pk, sent.pk} <= _ids(drafting_matters(department_head))

    _lovable_outcome(declined, OpinionOutcome.NOT_SENT)
    _lovable_outcome(sent, OpinionOutcome.SENT, dt.date(2026, 11, 4))
    counted = _ids(drafting_matters(department_head))
    assert declined.pk not in counted and sent.pk not in counted
    sent_list = _ids(Matter.objects.filter(opinion_state_q(department_head, OPINION_SENT)))
    assert sent.pk in sent_list and declined.pk not in sent_list

    _lovable_outcome(declined, OpinionOutcome.CLEARED)
    assert declined.pk in _ids(drafting_matters(department_head))
