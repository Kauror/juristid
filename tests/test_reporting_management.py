"""Management statistics (docs/adr/0160): deadlines, timeliness, comparisons, composition.

A small invented department, built for these six metrics alone so that the
shared statistics world — and every count already derived from it by hand —
stays exactly as it was (tests/synthetic_statistics.py, "opt-in extensions").

Every expectation below is derived from the list in ``mgmt`` by hand, not by
running the code. The day is fixed at 13 October 2026 so the same-period
comparison and the "future month" cells do not move with the calendar.

What each block proves:

* the population boundaries of every metric, including the period edges in
  Europe/Tallinn (a letter sent at 23:30 on 31 December belongs to that year);
* that a Chamber initiative is excluded from both deadline metrics and that the
  number excluded is said;
* that an answer is judged against the deadline in force when it was sent —
  never against a later revision — and is *not verifiable* otherwise;
* that a missing deadline is a visible group, not a zero;
* that a restricted Matter and a restricted opinion move nothing for a reader
  who may not see them;
* that every linked number opens a list of exactly that many rows.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.accounts.enums import UserRole
from app.core.enums import Visibility
from app.matters.enums import MatterDataClass, MatterOrigin, RecordMode, ResponseDeadlineOutcome
from app.matters.models import Matter, MatterResponseDeadline
from app.reporting import metric_catalogue as keys
from app.reporting.context import ReportingContext, parse_period
from app.reporting.metric_types import MetricStatus
from app.reporting.selectors import management
from app.reporting.services import compute
from app.submissions.enums import SentAtPrecision, SubmissionStatus
from app.submissions.models import Submission
from app.taxonomy.models import LegalInstrumentType
from app.workflow.enums import Track
from tests.synthetic_statistics import _document, _user, _version

pytestmark = pytest.mark.django_db

TODAY = date(2026, 10, 13)
TALLINN = ZoneInfo("Europe/Tallinn")


def at(day: date, hour: int = 9, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=TALLINN)


@dataclass
class Mgmt:
    head: Any
    reader: Any
    owner: Any
    matters: dict[str, Matter]


def _matter(name: str, **fields: Any) -> Matter:
    defaults: dict[str, Any] = {
        "title": f"Näidisteema {name}",
        "record_mode": RecordMode.FULL,
        "origin": MatterOrigin.NATIVE,
        "reporting_year": 2026,
    }
    defaults.update(fields)
    return Matter.objects.create(**defaults)


def _sent(
    matter: Matter,
    title: str,
    when: datetime,
    *,
    status: str = SubmissionStatus.SENT,
    precision: str = SentAtPrecision.TIMESTAMP,
    restricted: bool = False,
) -> Submission:
    document = _document(matter, f"{title} lõplik")
    if restricted:
        document.visibility_override = Visibility.RESTRICTED
        document.save(update_fields=["visibility_override"])
    version = _version(document, filename=f"{title}.pdf", payload=f"arvamus {title} {matter.pk}")
    return Submission.objects.create(
        matter=matter,
        title=title,
        status=status,
        sent_at=when,
        sent_at_precision=precision,
        final_version=version,
        visibility_override=Visibility.RESTRICTED if restricted else "",
    )


def _ended(
    matter: Matter,
    deadline: date,
    *,
    outcome: str,
    ended: datetime,
    requested: datetime | None,
    submission: Submission | None = None,
    next_deadline: date | None = None,
    note: str = "",
) -> MatterResponseDeadline:
    return MatterResponseDeadline.objects.create(
        matter=matter,
        deadline=deadline,
        requested_at=requested,
        outcome=outcome,
        submission=submission,
        next_deadline=next_deadline,
        note=note,
        ended_at=ended,
    )


ANSWERED = ResponseDeadlineOutcome.ANSWERED
MOVED = ResponseDeadlineOutcome.MOVED


def _instruments(matter: Matter, *instrument_keys: str) -> None:
    matter.legal_instruments.set(LegalInstrumentType.objects.filter(key__in=instrument_keys))


@pytest.fixture
def mgmt(db) -> Mgmt:
    head = _user("mgmt-juht@example.invalid", "Testjuht", UserRole.DEPARTMENT_HEAD)
    reader = _user("mgmt-lugeja@example.invalid", "Testlugeja", UserRole.READER)
    owner = _user("mgmt-omanik@example.invalid", "Testomanik", UserRole.SPECIALIST)
    m: dict[str, Matter] = {}

    # -- incoming, with a deadline answered on time ------------------------
    m["fast"] = _matter(
        "kiire", received_date=date(2026, 3, 1), track=Track.EU_INITIATIVE, owner=owner
    )
    _instruments(m["fast"], "direktiiv")
    s = _sent(m["fast"], "Kiire arvamus", at(date(2026, 3, 5)))
    _ended(
        m["fast"],
        date(2026, 3, 6),
        outcome=ANSWERED,
        ended=at(date(2026, 3, 5), 10),
        requested=at(date(2026, 2, 28), 10),
        submission=s,
    )

    # -- moved before sending, then answered late against the moved date ---
    m["moved"] = _matter("nihutatud", received_date=date(2026, 4, 1), track=Track.DOMESTIC)
    _instruments(m["moved"], "seadus")
    _ended(
        m["moved"],
        date(2026, 4, 15),
        outcome=MOVED,
        ended=at(date(2026, 4, 10)),
        requested=at(date(2026, 4, 1)),
        next_deadline=date(2026, 5, 30),
    )
    s = _sent(m["moved"], "Hiline arvamus", at(date(2026, 6, 2)))
    _ended(
        m["moved"],
        date(2026, 5, 30),
        outcome=ANSWERED,
        ended=at(date(2026, 6, 2), 12),
        requested=at(date(2026, 4, 1)),
        submission=s,
    )

    # -- a deadline from before the history was kept ------------------------
    m["legacy"] = _matter("vana", received_date=date(2026, 2, 1))
    _instruments(m["legacy"], "seadus", "direktiiv")
    s = _sent(m["legacy"], "Vana tähtaja arvamus", at(date(2026, 3, 10)))
    _ended(
        m["legacy"],
        date(2026, 3, 15),
        outcome=ANSWERED,
        ended=at(date(2026, 3, 10), 12),
        requested=None,
        submission=s,
    )

    # -- incoming without any deadline, and one with a deadline before arrival
    m["nodl"] = _matter("tähtajata", received_date=date(2026, 5, 1))
    _sent(m["nodl"], "Tähtajata arvamus", at(date(2026, 4, 4)))  # named by no deadline
    m["bad"] = _matter(
        "vigane",
        received_date=date(2026, 6, 10),
        response_deadline=date(2026, 6, 1),
        response_requested_at=at(date(2026, 6, 10)),
    )
    _instruments(m["bad"], "seadus", "konsultatsioon")

    # -- the Chamber's own initiative, holding an old pair of dates ---------
    m["init"] = _matter("algatus", received_date=date(2026, 2, 2), track=Track.KODA_INITIATIVE)
    _instruments(m["init"], "koja-ettepanek")
    s = _sent(m["init"], "Koja ettepanek", at(date(2026, 2, 15)))
    _ended(
        m["init"],
        date(2026, 2, 20),
        outcome=ANSWERED,
        ended=at(date(2026, 2, 15), 12),
        requested=at(date(2026, 2, 2)),
        submission=s,
    )

    # -- a restricted Matter, answered on time ------------------------------
    m["restricted"] = _matter(
        "piiratud", received_date=date(2026, 3, 3), visibility=Visibility.RESTRICTED
    )
    _instruments(m["restricted"], "el-maarus")
    s = _sent(m["restricted"], "Piiratud arvamus", at(date(2026, 3, 8)))
    _ended(
        m["restricted"],
        date(2026, 3, 10),
        outcome=ANSWERED,
        ended=at(date(2026, 3, 8), 12),
        requested=at(date(2026, 3, 3)),
        submission=s,
    )

    # -- an ordinary Matter whose answering opinion is restricted ------------
    m["fast2"] = _matter("piiratud arvamus")
    s = _sent(m["fast2"], "Konfidentsiaalne vastus", at(date(2026, 9, 1)), restricted=True)
    _ended(
        m["fast2"],
        date(2026, 9, 5),
        outcome=ANSWERED,
        ended=at(date(2026, 9, 1), 12),
        requested=at(date(2026, 8, 20)),
        submission=s,
    )

    # -- the deadline changed after the letter went --------------------------
    # At sending the deadline was 8 July and the letter (10 July) was late; the
    # current, moved date (20 July) would call it on time. Neither may be said.
    m["changed"] = _matter("muudetud")
    s = _sent(m["changed"], "Muutunud tähtaja arvamus", at(date(2026, 7, 10)))
    _ended(
        m["changed"],
        date(2026, 7, 8),
        outcome=MOVED,
        ended=at(date(2026, 7, 12)),
        requested=at(date(2026, 7, 1)),
        next_deadline=date(2026, 7, 20),
    )
    _ended(
        m["changed"],
        date(2026, 7, 20),
        outcome=ANSWERED,
        ended=at(date(2026, 7, 13)),
        requested=at(date(2026, 7, 1)),
        submission=s,
    )

    # -- a date-only send, and a move on that same day: order unknown -------
    m["same"] = _matter("sama päev")
    s = _sent(
        m["same"],
        "Kuupäevaga arvamus",
        at(date(2026, 9, 10), 0),
        precision=SentAtPrecision.DATE,
    )
    _ended(
        m["same"],
        date(2026, 9, 9),
        outcome=MOVED,
        ended=at(date(2026, 9, 10), 15),
        requested=at(date(2026, 9, 1)),
        next_deadline=date(2026, 9, 12),
    )
    _ended(
        m["same"],
        date(2026, 9, 12),
        outcome=ANSWERED,
        ended=at(date(2026, 9, 11)),
        requested=at(date(2026, 9, 1)),
        submission=s,
    )

    # -- the named opinion was withdrawn since ------------------------------
    m["wd"] = _matter("tagasi võetud")
    s = _sent(
        m["wd"], "Tagasi võetud arvamus", at(date(2026, 8, 1)), status=SubmissionStatus.WITHDRAWN
    )
    _ended(
        m["wd"],
        date(2026, 8, 5),
        outcome=ANSWERED,
        ended=at(date(2026, 8, 1), 12),
        requested=at(date(2026, 7, 20)),
        submission=s,
    )

    # -- answered in words only, and the period / clock edges ---------------
    m["note"] = _matter("selgitus")
    _ended(
        m["note"],
        date(2026, 5, 10),
        outcome=ANSWERED,
        ended=at(date(2026, 5, 5)),
        requested=at(date(2026, 5, 1)),
        note="Vastati koosolekul.",
    )
    _sent(m["note"], "Mullune arvamus", at(date(2025, 3, 3)))
    _sent(m["note"], "Hilisõhtune arvamus", at(date(2026, 10, 13), 23, 30))
    _sent(m["note"], "Homne arvamus", at(date(2026, 10, 14), 0, 30))

    m["prev"] = _matter("mullune", reporting_year=2025, received_date=date(2025, 12, 31))
    s = _sent(m["prev"], "Aastavahetuse arvamus", at(date(2025, 12, 31), 23, 30))
    _ended(
        m["prev"],
        date(2026, 1, 10),
        outcome=ANSWERED,
        ended=at(date(2026, 1, 2)),
        requested=at(date(2025, 12, 31), 10),
        submission=s,
    )

    m["onenote"] = _matter(
        "onenote",
        record_mode=RecordMode.ARCHIVE,
        origin=MatterOrigin.LEGACY_ONENOTE,
        reporting_year=2019,
        is_open=False,
    )
    return Mgmt(head=head, reader=reader, owner=owner, matters=m)


def ctx(viewer: Any, period: str = "2026", *, today: date = TODAY, **overrides: Any):
    return ReportingContext(
        viewer=viewer,
        period=parse_period(period, today),
        today=today,
        now=timezone.now(),
        **overrides,
    )


def groups(result) -> dict[str, int]:
    return {segment.label: segment.value for segment in result.segments}


def total_at(client, url: str) -> int:
    parsed = urlparse(url)
    query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
    response = client.get(parsed.path, query)
    assert response.status_code == 200, f"{url} -> {response.status_code}"
    return int(response.context["total"])


# ---------------------------------------------------------------------------
# 1. Time available to prepare an opinion
# ---------------------------------------------------------------------------


def test_preparation_time_counts_incoming_matters_of_the_period(mgmt):
    result = compute(keys.RESPONSE_PREPARATION_TIME, ctx(mgmt.head))
    # fast 5, moved 14 (first date, not the moved one), legacy 42, restricted 7.
    assert result.value == 4
    assert result.distribution.n == 4
    assert result.distribution.median == 10.5
    assert result.distribution.maximum == 42
    by = groups(result)
    assert by["0–7 päeva"] == 2
    assert by["8–14 päeva"] == 1
    assert by["31–60 päeva"] == 1
    # Unknown is a group of its own, never a zero day count.
    assert by["Arvamuse tähtaeg puudub"] == 1
    assert by["Tähtaeg on enne saabumist"] == 1
    # Coverage: six incoming Matters, five with a deadline.
    assert (result.coverage_count, result.coverage_denominator) == (5, 6)


def test_preparation_time_uses_the_first_deadline_not_the_moved_one(mgmt):
    rows = {row.title: row for row in management.preparation_rows(ctx(mgmt.head))}
    assert rows["Näidisteema nihutatud"].deadline == date(2026, 4, 15)
    assert rows["Näidisteema nihutatud"].days == 14


def test_preparation_time_excludes_and_names_the_chambers_initiatives(mgmt):
    result = compute(keys.RESPONSE_PREPARATION_TIME, ctx(mgmt.head))
    titles = {row.title for row in management.preparation_rows(ctx(mgmt.head))}
    assert "Näidisteema algatus" not in titles
    assert any("1 Koja algatust" in note for note in result.notes)


def test_preparation_time_says_how_many_deadlines_predate_the_history(mgmt):
    result = compute(keys.RESPONSE_PREPARATION_TIME, ctx(mgmt.head))
    assert any(note.startswith("1 mõõdetud tähtaega pärineb") for note in result.notes)


def test_preparation_time_follows_the_arrival_date_across_the_year_edge(mgmt):
    previous = compute(keys.RESPONSE_PREPARATION_TIME, ctx(mgmt.head, "2025"))
    assert previous.value == 1
    assert previous.distribution.median == 10


def test_preparation_time_hides_a_restricted_matter_from_a_reader(mgmt):
    result = compute(keys.RESPONSE_PREPARATION_TIME, ctx(mgmt.reader))
    assert result.value == 3
    assert groups(result)["0–7 päeva"] == 1


def test_preparation_time_declines_when_nothing_is_measured(mgmt):
    result = compute(keys.RESPONSE_PREPARATION_TIME, ctx(mgmt.head, "2020"))
    assert result.status == MetricStatus.INSUFFICIENT_DATA
    assert not result.has_value


# ---------------------------------------------------------------------------
# 2. Opinions submitted on time
# ---------------------------------------------------------------------------


def test_on_time_is_judged_on_verifiable_answers_only(mgmt):
    result = compute(keys.OPINIONS_ON_TIME, ctx(mgmt.head))
    by = groups(result)
    assert by["Tähtajaks"] == 3  # fast, restricted Matter, restricted opinion
    assert by["Pärast tähtaega"] == 1  # moved
    assert result.value == 75
    assert (result.coverage_count, result.coverage_denominator) == (4, 8)


def test_a_deadline_moved_after_sending_is_never_used_to_judge_the_answer(mgmt):
    rows = {row.matter_title: row for row in management.answer_rows(ctx(mgmt.head))}
    changed = rows["Näidisteema muudetud"]
    # Against the current date it would read as on time; it is not judged at all.
    assert changed.sent_on <= changed.deadline
    assert changed.group == management.CHANGED_AFTER_SENDING
    assert not changed.is_verifiable


def test_a_move_on_the_sending_day_of_a_date_only_send_is_not_verifiable(mgmt):
    rows = {row.matter_title: row for row in management.answer_rows(ctx(mgmt.head))}
    assert rows["Näidisteema sama päev"].group == management.CHANGED_AFTER_SENDING


def test_a_move_before_sending_judges_against_the_moved_date(mgmt):
    rows = {row.matter_title: row for row in management.answer_rows(ctx(mgmt.head))}
    moved = rows["Näidisteema nihutatud"]
    assert moved.deadline == date(2026, 5, 30)
    assert moved.group == management.LATE
    assert moved.days_late == 3


def test_unverifiable_answers_are_counted_and_shown_apart(mgmt):
    by = groups(compute(keys.OPINIONS_ON_TIME, ctx(mgmt.head)))
    assert by[management.ANSWER_GROUP_LABELS[management.CHANGED_AFTER_SENDING]] == 2
    assert by[management.ANSWER_GROUP_LABELS[management.BEFORE_HISTORY]] == 1
    assert by[management.ANSWER_GROUP_LABELS[management.WITHDRAWN]] == 1


def test_on_time_excludes_the_chambers_initiative_and_says_so(mgmt):
    result = compute(keys.OPINIONS_ON_TIME, ctx(mgmt.head))
    titles = {row.matter_title for row in management.answer_rows(ctx(mgmt.head))}
    assert "Näidisteema algatus" not in titles
    assert any("1 Koja algatust" in note for note in result.notes)


def test_on_time_names_answers_without_a_sent_opinion_and_unnamed_opinions(mgmt):
    notes = " ".join(compute(keys.OPINIONS_ON_TIME, ctx(mgmt.head)).notes)
    assert "1 tähtajale vastati ilma Juristides saadetud arvamuseta" in notes
    # 2026: the deadline-less opinion and the two October ones on «selgitus».
    assert "Perioodil saadetud 3 arvamust ei ole ühegi tähtaja vastuseks" in notes


def test_on_time_hides_restricted_matters_and_restricted_opinions(mgmt):
    result = compute(keys.OPINIONS_ON_TIME, ctx(mgmt.reader))
    by = groups(result)
    assert by["Tähtajaks"] == 1
    assert by["Pärast tähtaega"] == 1
    assert result.value == 50
    assert result.coverage_denominator == 6


def test_on_time_reads_the_send_date_in_tallinn_at_the_year_edge(mgmt):
    rows = management.answer_rows(ctx(mgmt.head, "2025"))
    assert [row.matter_title for row in rows] == ["Näidisteema mullune"]
    assert rows[0].sent_on == date(2025, 12, 31)
    assert rows[0].group == management.ON_TIME
    assert all(
        row.matter_title != "Näidisteema mullune" for row in management.answer_rows(ctx(mgmt.head))
    )


def test_on_time_declines_rather_than_reporting_zero_percent(mgmt):
    result = compute(keys.OPINIONS_ON_TIME, ctx(mgmt.head, "2020"))
    assert result.status == MetricStatus.INSUFFICIENT_DATA
    assert not result.has_value


# ---------------------------------------------------------------------------
# 3. Same period last year
# ---------------------------------------------------------------------------


def test_the_comparison_cuts_both_years_on_the_same_day_in_tallinn(mgmt):
    result = compute(keys.SUBMISSIONS_SENT_YOY_CHANGE, ctx(mgmt.head))
    comparison = result.comparison
    # 2026 to 13 October: fast, legacy, nodl, moved, init, restricted Matter,
    # restricted opinion, changed, same, and the one sent at 23:30 on the 13th.
    # The withdrawn one is not SENT; the one at 00:30 on the 14th is after the cut.
    assert comparison.current_value == 10
    # 2025 to 13 October: only March. The 31 December letter is outside.
    assert comparison.previous_value == 1
    assert comparison.current_period_label == "1.1.2026 – 13.10.2026"
    assert comparison.previous_period_label == "1.1.2025 – 13.10.2025"
    assert result.status == MetricStatus.AVAILABLE


def test_the_comparison_hides_restricted_material(mgmt):
    assert compute(keys.SUBMISSIONS_SENT_YOY_CHANGE, ctx(mgmt.reader)).comparison.current_value == 8


def test_the_comparison_declines_when_last_year_was_never_measured(mgmt):
    narrowed = ctx(mgmt.head, owner_id=mgmt.owner.pk)
    result = compute(keys.SUBMISSIONS_SENT_YOY_CHANGE, narrowed)
    assert result.status == MetricStatus.INSUFFICIENT_DATA
    assert any("Eelmise aasta kohta mõõtmist ei ole" in note for note in result.notes)


def test_a_leap_day_is_compared_with_the_last_day_of_february(mgmt):
    result = compute(keys.SUBMISSIONS_SENT_YOY_CHANGE, ctx(mgmt.head, today=date(2028, 2, 29)))
    assert result.comparison.previous_period_label == "1.1.2027 – 28.2.2027"
    assert "kuni=02-28" in result.comparison.previous_url


def test_both_sides_of_the_comparison_open_exactly_what_they_counted(mgmt, client):
    client.force_login(mgmt.head)
    comparison = compute(keys.SUBMISSIONS_SENT_YOY_CHANGE, ctx(mgmt.head)).comparison
    assert total_at(client, comparison.current_url) == comparison.current_value
    assert total_at(client, comparison.previous_url) == comparison.previous_value


@pytest.mark.parametrize(
    "query", [{"kuu": "3"}, {"aasta": "2026", "kuu": "13"}, {"aasta": "2026", "kuni": "02-30"}]
)
def test_a_month_or_cutoff_nobody_could_draw_is_refused(mgmt, client, query):
    client.force_login(mgmt.head)
    assert client.get("/statistika/arvamused/", query).status_code == 404


# ---------------------------------------------------------------------------
# 4. European share by year
# ---------------------------------------------------------------------------


def _matrix(result) -> dict[str, dict[str, int]]:
    matrix = result.matrix
    return {
        row.label: {
            column: cell.value for column, cell in zip(matrix.columns, row.cells, strict=True)
        }
        for row in matrix.rows
    }


def test_eu_share_reads_only_the_reviewed_instrument_group(mgmt):
    result = compute(keys.EU_RELATED_MATTERS_BY_YEAR, ctx(mgmt.head))
    cells = _matrix(result)["2026"]
    # EU: fast (direktiiv), legacy (seadus + direktiiv), restricted (EL määrus).
    assert cells["ELiga seotud õigusakti järgi"] == 3
    # `fast` is also on an EU track; Õigusakt comes first, so it is not here.
    assert cells["ELiga seotud ainult menetlusliigi järgi"] == 0
    # Domestic: moved (seadus), init (koja-ettepanek). `bad` carries a
    # version-1.0 «Konsultatsioon», which belongs to neither group.
    assert cells["Siseriiklik"] == 2
    assert cells["Määramata"] == 7
    row = result.matrix.rows[0]
    assert row.share_text == "60,0%"
    assert (result.coverage_count, result.coverage_denominator) == (5, 12)


def test_eu_share_hides_a_restricted_matter(mgmt):
    cells = _matrix(compute(keys.EU_RELATED_MATTERS_BY_YEAR, ctx(mgmt.reader)))["2026"]
    assert cells["ELiga seotud õigusakti järgi"] == 2


def test_eu_share_shows_the_unknown_year_only_over_all_years(mgmt):
    everything = _matrix(compute(keys.EU_RELATED_MATTERS_BY_YEAR, ctx(mgmt.head, "koik")))
    assert set(everything) == {"2025", "2026", "Teadmata aasta"}
    assert "Teadmata aasta" not in _matrix(compute(keys.EU_RELATED_MATTERS_BY_YEAR, ctx(mgmt.head)))


def test_every_eu_cell_opens_exactly_its_matters(mgmt, client):
    client.force_login(mgmt.head)
    result = compute(keys.EU_RELATED_MATTERS_BY_YEAR, ctx(mgmt.head, "koik"))
    linked = [cell for row in result.matrix.rows for cell in row.cells if cell.url]
    assert linked
    for cell in linked:
        assert total_at(client, cell.url) == cell.value, cell.url


# ---------------------------------------------------------------------------
# 5. Chamber initiatives by year
# ---------------------------------------------------------------------------


def test_initiatives_by_year_count_the_stored_track_and_its_share(mgmt):
    result = compute(keys.KODA_INITIATIVES_BY_YEAR, ctx(mgmt.head))
    cells = _matrix(result)["2026"]
    assert cells == {"Koja algatus": 1, "Muu menetlusliik": 2, "Menetlusliik määramata": 9}
    assert result.matrix.rows[0].share_text == "8,3%"
    assert result.value == 1


def test_every_initiative_cell_opens_exactly_its_matters(mgmt, client):
    client.force_login(mgmt.head)
    result = compute(keys.KODA_INITIATIVES_BY_YEAR, ctx(mgmt.head, "koik"))
    linked = [cell for row in result.matrix.rows for cell in row.cells if cell.url]
    # Every cell opens the Statistika list, which has a CSV; the register is
    # offered from there for the groups it can express.
    assert linked and all("/statistika/loend/" in cell.url for cell in linked)
    for cell in linked:
        assert total_at(client, cell.url) == cell.value, cell.url


# ---------------------------------------------------------------------------
# 6. Sending by month and year
# ---------------------------------------------------------------------------


def test_month_by_year_spans_the_measured_years_and_leaves_the_future_blank(mgmt):
    result = compute(keys.SUBMISSIONS_SENT_BY_MONTH_AND_YEAR, ctx(mgmt.head))
    matrix = result.matrix
    assert matrix.columns == ("2025", "2026")
    december = matrix.rows[11]
    assert december.cells[0].value == 1  # 31 Dec 2025, 23:30 in Tallinn
    assert december.cells[1].missing
    assert not matrix.rows[9].cells[1].missing  # October 2026 has begun
    # October 2026: the 13th at 23:30 and the 14th at 00:30.
    assert matrix.rows[9].cells[1].value == 2
    assert matrix.column_totals == (2, 11)


def test_every_month_cell_opens_exactly_what_it_counted(mgmt, client):
    client.force_login(mgmt.head)
    result = compute(keys.SUBMISSIONS_SENT_BY_MONTH_AND_YEAR, ctx(mgmt.head))
    linked = [cell for row in result.matrix.rows for cell in row.cells if cell.url]
    assert linked
    for cell in linked:
        assert total_at(client, cell.url) == cell.value, cell.url


# ---------------------------------------------------------------------------
# Drill-through and the list
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", [keys.RESPONSE_PREPARATION_TIME, keys.OPINIONS_ON_TIME])
@pytest.mark.parametrize("viewer", ["head", "reader"])
def test_every_deadline_group_opens_exactly_what_it_counted(mgmt, client, key, viewer):
    user = getattr(mgmt, viewer)
    client.force_login(user)
    result = compute(key, ctx(user))
    linked = [segment for segment in result.segments if segment.url]
    assert linked
    for segment in linked:
        assert total_at(client, segment.url) == segment.value, segment.label


def test_the_headline_links_open_the_population_the_number_is_about(mgmt, client):
    client.force_login(mgmt.head)
    preparation = compute(keys.RESPONSE_PREPARATION_TIME, ctx(mgmt.head))
    assert total_at(client, preparation.drillthrough_url) == preparation.value
    on_time = compute(keys.OPINIONS_ON_TIME, ctx(mgmt.head))
    assert total_at(client, on_time.drillthrough_url) == on_time.coverage_count


def test_the_list_never_names_a_restricted_matter_to_a_reader(mgmt, client):
    client.force_login(mgmt.reader)
    url = management.metric_list_url(ctx(mgmt.reader), keys.OPINIONS_ON_TIME, management.ON_TIME)
    parsed = urlparse(url)
    response = client.get(parsed.path, {k: v[0] for k, v in parse_qs(parsed.query).items()})
    body = response.content.decode()
    assert "Näidisteema piiratud" not in body
    assert "Konfidentsiaalne vastus" not in body


@pytest.mark.parametrize(
    "query",
    [
        {"naitaja": "MATTERS_TOTAL", "ruhm": "x"},
        {"naitaja": keys.OPINIONS_ON_TIME, "ruhm": "tundmatu"},
        {"naitaja": keys.EU_RELATED_MATTERS_BY_YEAR, "ruhm": "el"},
        {"naitaja": keys.EU_RELATED_MATTERS_BY_YEAR, "ruhm": "el", "aasta": "abc"},
        {"naitaja": keys.KODA_INITIATIVES_BY_YEAR, "ruhm": "algatus"},
        {"naitaja": keys.KODA_INITIATIVES_BY_YEAR, "ruhm": "x", "aasta": "2026"},
        {"naitaja": keys.EU_RELATED_MATTERS_BY_YEAR, "ruhm": "el-menetlus", "aasta": "20x6"},
    ],
)
def test_a_list_link_nobody_drew_is_refused(mgmt, client, query):
    client.force_login(mgmt.head)
    assert client.get("/statistika/loend/", query).status_code == 404


def test_the_list_exports_the_same_rows(mgmt, client):
    client.force_login(mgmt.head)
    url = management.metric_list_url(
        ctx(mgmt.head), keys.OPINIONS_ON_TIME, management.CHANGED_AFTER_SENDING
    )
    params = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    response = client.get("/statistika/eksport/loend.csv", params)
    assert response.status_code == 200
    lines = b"".join(response.streaming_content).decode("utf-8-sig").strip().splitlines()
    assert len(lines) == 1 + 2


def test_the_tabs_render_the_new_sections(mgmt, client):
    client.force_login(mgmt.head)
    activity = client.get("/statistika/tegevus/", {"periood": "2026"}).content.decode()
    assert "Arvamuse tähtajad" in activity
    assert "Aeg arvamuse koostamiseks" in activity
    assert "Saadetud arvamused eelmise aasta sama ajaga" in activity
    assert "Saadetud arvamused kuude ja aastate kaupa" in activity
    matters = client.get("/statistika/teemad/", {"periood": "2026"}).content.decode()
    assert "ELiga seotud teemad aastate kaupa" in matters
    assert "Koja algatused aastate kaupa" in matters
    definitions = client.get("/statistika/definitsioonid/").content.decode()
    assert "Lugeja" in definitions and "Nimetaja" in definitions


# ---------------------------------------------------------------------------
# Round 2 (docs/adr/0160, 2026-10-13)
# ---------------------------------------------------------------------------
#
# * a deadline recorded after the send stays not verifiable, and says so;
# * *ELiga seotud* has two bases, Õigusakt first, each Matter counted once;
# * TEST Matters and restricted Matters move nothing, and every drill-through —
#   the Statistika list, the submission list, the register and both CSVs —
#   opens exactly the rows its number counted;
# * every new card states its coverage limit on the card itself;
# * a Chamber initiative holding its own deadline (docs/adr/0151, amendment of
#   2026-10-13, PR #427) stays out of both deadline populations, is named in
#   both exclusion notes, and leaves the initiative-by-year counts alone. The
#   rows are written directly, so these pass with and without #427;
# * the number of queries per metric does not grow with the rows.

ROUND2_METRICS = (
    keys.RESPONSE_PREPARATION_TIME,
    keys.OPINIONS_ON_TIME,
    keys.SUBMISSIONS_SENT_YOY_CHANGE,
    keys.EU_RELATED_MATTERS_BY_YEAR,
    keys.KODA_INITIATIVES_BY_YEAR,
    keys.SUBMISSIONS_SENT_BY_MONTH_AND_YEAR,
)


def test_a_deadline_recorded_after_the_send_is_not_verifiable(mgmt):
    late_record = _matter("hiljem kirja pandud")
    s = _sent(late_record, "Enne tähtaja kirjapanekut saadetud", at(date(2026, 6, 20)))
    _ended(
        late_record,
        date(2026, 6, 30),
        outcome=ANSWERED,
        ended=at(date(2026, 6, 25)),
        requested=at(date(2026, 6, 24)),  # recorded four days after the letter went
        submission=s,
    )
    rows = {row.matter_title: row for row in management.answer_rows(ctx(mgmt.head))}
    # Sent before the deadline it names, and still never called on time.
    assert rows["Näidisteema hiljem kirja pandud"].sent_on <= date(2026, 6, 30)
    assert rows["Näidisteema hiljem kirja pandud"].group == management.CHANGED_AFTER_SENDING
    spec = compute(keys.OPINIONS_ON_TIME, ctx(mgmt.head)).definition
    assert "salvestati alles pärast saatmist" in spec.exclusions_et
    assert "jääb alati kontrollimatuks" in spec.notes_et


@pytest.fixture
def eu_bases(mgmt) -> dict[str, Matter]:
    """One 2027 Matter per precedence case, so the 2027 row is all of them."""
    cases: dict[str, Matter] = {}

    def add(name: str, *instrument_keys: str, **fields: Any) -> None:
        cases[name] = _matter(name, reporting_year=2027, **fields)
        _instruments(cases[name], *instrument_keys)

    add("instrument only", "direktiiv")
    add("both bases", "el-maarus", track=Track.EU_INITIATIVE)  # counted once, on Õigusakt
    add("track only", track=Track.EU_INITIATIVE)
    add("transposition, retired instrument", "konsultatsioon", track=Track.NATIONAL_TRANSPOSITION)
    add("domestic on an EU track", "seadus", track=Track.NATIONAL_TRANSPOSITION)
    add("notice only", "el-teatis")
    add("notice on an EU track", "el-teatis", track=Track.EU_INITIATIVE)
    add("nothing", track=Track.DOMESTIC)
    add("restricted track only", track=Track.EU_INITIATIVE, visibility=Visibility.RESTRICTED)
    return cases


def test_eu_bases_count_each_matter_once_with_the_instrument_first(mgmt, eu_bases):
    result = compute(keys.EU_RELATED_MATTERS_BY_YEAR, ctx(mgmt.head, "2027"))
    cells = _matrix(result)["2027"]
    assert cells == {
        # instrument only, both bases, and — owner's decision 2026-10-13 — the
        # historical «EL teatis», alone or on an EU track: once, on Õigusakt.
        "ELiga seotud õigusakti järgi": 4,
        "ELiga seotud ainult menetlusliigi järgi": 3,  # track, transposition, restricted
        "Siseriiklik": 1,  # domestic on an EU track
        "Määramata": 1,  # nothing
    }
    assert sum(cells.values()) == len(eu_bases)  # nobody twice, nobody lost
    assert result.value == 7
    assert result.matrix.rows[0].share_text == "87,5%"  # 7 / (7 + 1)
    notes = " ".join(result.notes)
    assert "Katvus: 1 teemal 9-st on määramata" in notes
    assert "1 siseriikliku õigusaktiga teemal on ELi menetlusliik" in notes
    assert "ei mõõda kõiki siseriiklikke õigusloome teemasid" in notes
    # The notice is identifiable: «sh 2 …», opening exactly those two.
    (subset,) = result.matrix.subsets
    assert subset.label == "sh 2 ajaloolise õigusaktiga «EL teatis»"
    assert subset.value == 2


def test_eu_basis_cells_open_exactly_their_matters(mgmt, eu_bases):
    context = ctx(mgmt.head, "2027")
    expected = {
        management.EU_RELATED: {
            "instrument only",
            "both bases",
            "notice only",
            "notice on an EU track",
        },
        management.EU_BY_TRACK: {
            "track only",
            "transposition, retired instrument",
            "restricted track only",
        },
        management.DOMESTIC: {"domestic on an EU track"},
        management.UNCLASSIFIED: {"nothing"},
    }
    for group, names in expected.items():
        listing = management.metric_rows(context, keys.EU_RELATED_MATTERS_BY_YEAR, group, "2027")
        assert {row.title for row in listing.rows} == {f"Näidisteema {n}" for n in names}, group


def test_eu_bases_hide_a_restricted_matter_from_a_reader(mgmt, eu_bases):
    cells = _matrix(compute(keys.EU_RELATED_MATTERS_BY_YEAR, ctx(mgmt.reader, "2027")))["2027"]
    assert cells["ELiga seotud ainult menetlusliigi järgi"] == 2


def _add_test_matters() -> None:
    """TEST Matters mirroring every real case the new metrics count.

    Each one would move some metric if the data class leaked anywhere: an
    incoming Matter answered on time on an EU track, an initiative, an
    unknown-year one, an unclassified one on a transposition track, and sent
    opinions in months nothing real was sent in.
    """
    test_matter: dict[str, Any] = {"data_class": MatterDataClass.TEST}
    t = _matter(
        "TEST saabunud",
        received_date=date(2026, 3, 2),
        track=Track.EU_INITIATIVE,
        **test_matter,
    )
    _instruments(t, "direktiiv")
    s = _sent(t, "TEST arvamus", at(date(2026, 1, 20)))
    _ended(
        t,
        date(2026, 3, 6),
        outcome=ANSWERED,
        ended=at(date(2026, 1, 20), 12),
        requested=at(date(2026, 1, 10)),
        submission=s,
    )
    _matter("TEST algatus", track=Track.KODA_INITIATIVE, **test_matter)
    _matter("TEST aastata", reporting_year=None, **test_matter)
    _matter("TEST ülevõtmine", track=Track.NATIONAL_TRANSPOSITION, **test_matter)
    _sent(_matter("TEST mullu", reporting_year=2025, **test_matter), "TEST", at(date(2025, 8, 8)))


@pytest.fixture
def mixed(mgmt) -> Mgmt:
    """The same department, with TEST Matters beside the restricted ones."""
    _add_test_matters()
    return mgmt


def _values(result) -> Any:
    matrix = result.matrix
    return (
        result.value,
        result.coverage_count,
        result.coverage_denominator,
        tuple(segment.value for segment in result.segments),
        None if matrix is None else tuple(tuple(c.value for c in r.cells) for r in matrix.rows),
        None
        if result.comparison is None
        else (result.comparison.current_value, result.comparison.previous_value),
    )


@pytest.mark.parametrize("key", ROUND2_METRICS)
@pytest.mark.parametrize("period", ["2026", "koik"])
def test_test_matters_move_no_new_metric(mgmt, key, period):
    before = _values(compute(key, ctx(mgmt.head, period)))
    _add_test_matters()
    assert _values(compute(key, ctx(mgmt.head, period))) == before


def _drill_links(result) -> list[tuple[str, int, str]]:
    """(what, expected rows, url) for every number on the card that is a link."""
    links: list[tuple[str, int, str]] = []
    if result.drillthrough_url:
        if result.comparison is not None:
            expected = result.comparison.current_value
        elif result.definition.key == keys.OPINIONS_ON_TIME:
            expected = result.coverage_count
        else:
            expected = result.value
        links.append(("headline", expected, result.drillthrough_url))
    for segment in result.segments:
        if segment.url:
            links.append((segment.label, segment.value, segment.url))
    if result.matrix is not None:
        for row in result.matrix.rows:
            for column, cell in zip(result.matrix.columns, row.cells, strict=True):
                if cell.url:
                    links.append((f"{row.label}/{column}", cell.value, cell.url))
    if result.comparison is not None and result.comparison.previous_url:
        links.append(("previous", result.comparison.previous_value, result.comparison.previous_url))
    return links


CSV_FOR = {
    "/statistika/loend/": "/statistika/eksport/loend.csv",
    "/statistika/arvamused/": "/statistika/eksport/arvamused.csv",
}


def _csv_rows(client, path: str, query: dict[str, str]) -> int:
    response = client.get(path, query)
    assert response.status_code == 200, path
    text = b"".join(response.streaming_content).decode("utf-8-sig")
    return len(list(csv.reader(io.StringIO(text), delimiter=";"))) - 1


@pytest.mark.parametrize("key", ROUND2_METRICS)
@pytest.mark.parametrize("viewer", ["head", "reader"])
@pytest.mark.parametrize("period", ["2026", "koik"])
def test_cell_list_and_csv_agree_with_test_and_restricted_matters_present(
    mixed, client, key, viewer, period
):
    user = getattr(mixed, viewer)
    client.force_login(user)
    links = _drill_links(compute(key, ctx(user, period)))
    assert links, key
    for what, expected, url in links:
        parsed = urlparse(url)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        assert total_at(client, url) == expected, (what, url)
        if parsed.path in CSV_FOR:
            assert _csv_rows(client, CSV_FOR[parsed.path], query) == expected, (what, url)
        else:
            # The register has no CSV; it must carry the reporting data class.
            assert parsed.path.startswith("/teemad/"), url
            assert query.get("andmed") == "paris", url


def test_the_initiative_list_offers_the_register_with_the_same_rows(mixed, client):
    client.force_login(mixed.head)
    context = ctx(mixed.head, "koik")
    for group in (management.INITIATIVE, management.NO_TRACK):
        listing = management.metric_rows(context, keys.KODA_INITIATIVES_BY_YEAR, group, "2026")
        assert listing.rows, group
        assert "andmed=paris" in listing.register_url
        assert total_at(client, listing.register_url) == len(listing.rows), group
    other = management.metric_rows(
        context, keys.KODA_INITIATIVES_BY_YEAR, management.OTHER_TRACK, "2026"
    )
    assert other.register_url == ""


def test_every_new_card_states_its_coverage_on_the_card(mixed, client):
    client.force_login(mixed.head)
    activity = client.get("/statistika/tegevus/", {"periood": "2026"}).content.decode()
    matters = client.get("/statistika/teemad/", {"periood": "2026"}).content.decode()
    # On-time: only deadlines linked to an opinion, since 4.10.2026.
    assert "Katvus: arvesse lähevad ainult seotud tähtajad — alates 4.10.2026" in activity
    assert "tähtaegade ajalugu peetakse alates 4.10.2026" in activity
    assert "Katvus: struktuursed arvamuse kirjed alates aastast 2025" in activity
    # Initiatives: how many Teemad carry no track at all.
    assert "Katvus: menetlusliik puudub 9 teemal 12-st" in matters
    # EU: how many cannot be placed.
    assert "Katvus: 7 teemal 12-st on määramata" in matters


@pytest.fixture
def initiative_with_deadline(mgmt) -> Matter:
    """A native Chamber initiative after #427: no Saabus, its own deadline, answered.

    Written straight to the tables — the deadline, the sent proposal and the
    ANSWERED history row naming it — which is the state «Märgi saadetud» leaves
    through `answer_current_deadline_with`, so the test does not depend on #427.
    """
    initiative = _matter("algatus tähtajaga", track=Track.KODA_INITIATIVE)
    _instruments(initiative, "koja-ettepanek")
    s = _sent(initiative, "Koja ettepanek tähtajaga", at(date(2026, 9, 20)))
    _ended(
        initiative,
        date(2026, 9, 30),
        outcome=ANSWERED,
        ended=at(date(2026, 9, 20), 12),
        requested=at(date(2026, 9, 1)),
        submission=s,
    )
    # An unsent one holding a deadline is outstanding work, still not a request.
    _matter(
        "algatus saatmata",
        track=Track.KODA_INITIATIVE,
        response_deadline=date(2026, 11, 15),
        response_requested_at=at(date(2026, 10, 1)),
    )
    return initiative


def test_an_initiative_with_its_own_deadline_stays_out_of_preparation_time(
    mgmt, initiative_with_deadline
):
    result = compute(keys.RESPONSE_PREPARATION_TIME, ctx(mgmt.head))
    titles = {row.title for row in management.preparation_rows(ctx(mgmt.head))}
    assert not any(title.startswith("Näidisteema algatus") for title in titles)
    assert result.value == 4 and result.population_count == 6  # as without them
    # The fixture's arrived initiative, and the two without Saabus.
    assert any(
        "3 Koja algatust" in note and "Neist 2 on ilma saabumiseta" in note for note in result.notes
    )


def test_an_initiative_with_its_own_deadline_stays_out_of_on_time(mgmt, initiative_with_deadline):
    result = compute(keys.OPINIONS_ON_TIME, ctx(mgmt.head))
    titles = {row.matter_title for row in management.answer_rows(ctx(mgmt.head))}
    assert "Näidisteema algatus tähtajaga" not in titles
    assert result.value == 75 and result.coverage_denominator == 8  # as without it
    assert any("2 Koja algatust" in note for note in result.notes)
    # Its sent proposal is not an «unnamed» opinion either.
    assert any("Perioodil saadetud 3 arvamust" in note for note in result.notes)


def test_initiative_deadlines_leave_the_initiative_counts_alone(mgmt, initiative_with_deadline):
    cells = _matrix(compute(keys.KODA_INITIATIVES_BY_YEAR, ctx(mgmt.head)))["2026"]
    # The fixture's one, plus the two new ones: counted on the track alone.
    assert cells == {"Koja algatus": 3, "Muu menetlusliik": 2, "Menetlusliik määramata": 9}


def _queries(key: str, viewer: Any, period: str) -> int:
    with CaptureQueriesContext(connection) as captured:
        compute(key, ctx(viewer, period))
    return len(captured.captured_queries)


def _grow(scale: int) -> None:
    """More of everything, across more years, so a per-row or per-year query shows."""
    for n in range(scale):
        year = 2020 + n % 7
        month = 1 + n % 12
        matter = _matter(
            f"kasv {n}",
            reporting_year=year,
            received_date=date(year, month, 1),
            track=(Track.EU_INITIATIVE, Track.KODA_INITIATIVE, "")[n % 3],
        )
        _instruments(matter, ("direktiiv", "seadus", "el-teatis")[n % 3])
        s = _sent(matter, f"Kasv {n}", at(date(year, month, 10)))
        _ended(
            matter,
            date(year, month, 20),
            outcome=ANSWERED,
            ended=at(date(year, month, 10), 12),
            requested=at(date(year, month, 2)),
            submission=s,
        )


@pytest.mark.parametrize("key", ROUND2_METRICS)
def test_the_query_count_does_not_grow_with_the_rows(mgmt, key):
    small = {period: _queries(key, mgmt.head, period) for period in ("2026", "koik")}
    _grow(21)
    large = {period: _queries(key, mgmt.head, period) for period in ("2026", "koik")}
    assert large == small, (key, small, large)


def test_the_eu_notice_subset_opens_its_rows_and_csv_with_test_matters_present(
    mgmt, eu_bases, client
):
    test_notice = _matter("TEST teatis", reporting_year=2027, data_class=MatterDataClass.TEST)
    _instruments(test_notice, "el-teatis")
    client.force_login(mgmt.head)
    result = compute(keys.EU_RELATED_MATTERS_BY_YEAR, ctx(mgmt.head, "2027"))
    (subset,) = result.matrix.subsets
    parsed = urlparse(subset.url)
    query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert query["ruhm"] == management.EU_NOTICE_SUBSET and "aasta" not in query
    assert total_at(client, subset.url) == subset.value == 2
    assert _csv_rows(client, "/statistika/eksport/loend.csv", query) == 2
    listing = management.metric_rows(
        ctx(mgmt.head, "2027"), keys.EU_RELATED_MATTERS_BY_YEAR, management.EU_NOTICE_SUBSET
    )
    assert {row.title for row in listing.rows} == {
        "Näidisteema notice only",
        "Näidisteema notice on an EU track",
    }


def test_the_notice_is_european_for_statistics_only(mgmt, client):
    """Still retired for capture: neither Uus teema nor Muuda teemat offers it."""
    from app.taxonomy.legal_instruments import EU_LEGAL_INSTRUMENT_KEYS
    from app.taxonomy.vocabulary import selectable_legal_instrument_types

    assert "el-teatis" not in EU_LEGAL_INSTRUMENT_KEYS  # the shared groups are untouched
    assert "el-teatis" in management.REPORTING_EU_INSTRUMENT_KEYS
    assert not selectable_legal_instrument_types().filter(key="el-teatis").exists()
    client.force_login(mgmt.owner)
    create = client.get("/teemad/uus/").content.decode()
    edit = client.get(f"/teemad/{mgmt.matters['moved'].pk}/muuda/").content.decode()
    for body in (create, edit):
        assert "Õigusakt" in body
        assert "EL teatis" not in body


def test_register_links_carry_the_data_class_once_even_if_the_helper_adds_it(mgmt, monkeypatch):
    """Idempotent: if `register_url` later defaults to `andmed=paris` itself, the
    explicit value is an override of the same key, never a second parameter."""
    from app.reporting.selectors import base

    def future_register_url(context, **overrides):
        return base.register_url(context, **{"andmed": "paris", **overrides})

    context = ctx(mgmt.head, "koik")
    for helper in (base.register_url, future_register_url):
        monkeypatch.setattr(management, "register_url", helper)
        urls = [compute(keys.KODA_INITIATIVES_BY_YEAR, context).drillthrough_url]
        for group in (management.INITIATIVE, management.NO_TRACK):
            urls.append(
                management.metric_rows(
                    context, keys.KODA_INITIATIVES_BY_YEAR, group, "2026"
                ).register_url
            )
        for url in urls:
            assert parse_qs(urlparse(url).query)["andmed"] == ["paris"], url
