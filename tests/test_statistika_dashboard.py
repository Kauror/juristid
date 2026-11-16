"""Statistika's first page, read from the database (`/statistika/`).

`test_opinion_stats` settles the arithmetic on hand-built facts. This file is
about the two things that only a database can show: that the one reader
(`app.reporting.opinions`) finds a sent opinion in each place Juristid records
one — and counts it once, for a reader who may see its Teema — and that the page
and the list behind its figure read that same list.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid

import pytest
from django.conf import settings
from django.shortcuts import resolve_url
from django.urls import reverse
from django.utils import timezone

from app.legacy_import.lovable_sync import SYNC_ENDING_NOTE
from app.legacy_import.lovable_sync_models import (
    LovableOpinionOutcome,
    LovableSyncRun,
    OpinionOutcome,
    SyncMode,
    SyncTrigger,
)
from app.matters.enums import ResponseDeadlineOutcome
from app.matters.models import Matter, MatterResponseDeadline
from app.reporting import opinions
from app.reporting.opinions import (
    EU_INSTRUMENT_KEYS,
    SOURCE_ANSWERED,
    SOURCE_IMPORTED,
    SOURCE_SUBMISSION,
)
from app.taxonomy.models import LegalInstrumentType
from app.workflow.enums import Track
from tests import factories

pytestmark = pytest.mark.django_db

TODAY = dt.date(2026, 11, 16)
_REAL_LOCALDATE = timezone.localdate

#: Inside the default period (the last 12 months: 17.11.2025–16.11.2026).
INSIDE = dt.date(2026, 6, 1)
#: Outside it and outside its comparison period.
OUTSIDE = dt.date(2024, 5, 5)


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    """«Today» is fixed; a moment handed in is still converted for real.

    `opinion_facts` calls `timezone.localdate(sent_at)` for the day an opinion
    went out, so the usual `lambda *a, **k: TODAY` would move every such
    opinion to today.
    """

    def frozen(value=None, timezone=None):
        return TODAY if value is None else _REAL_LOCALDATE(value, timezone)

    monkeypatch.setattr("django.utils.timezone.localdate", frozen)
    return TODAY


@pytest.fixture
def mari(db):
    return factories.UserFactory(display_name="Mari Maasikas")


@pytest.fixture
def jaan(db):
    return factories.UserFactory(display_name="Jaan Tamm")


def _at(day: dt.date, hour: int = 12) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, hour, tzinfo=dt.UTC)


def _imported(
    matter: Matter,
    outcome: str,
    sent_on: dt.date | None = None,
    *,
    request_deadline: dt.date | None = None,
) -> LovableOpinionOutcome:
    """The synchronisation's statement about ``matter``; each call supersedes the last.

    The same rows `tests/test_lovable_kpi_parity._lovable_outcome` writes, with
    the request's deadline beside them.
    """
    at = dt.datetime(2026, 11, 11, 3, 0, tzinfo=dt.UTC)
    run = LovableSyncRun.objects.create(
        run_key=f"statdash-{uuid.uuid4().hex[:12]}",
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
        request_deadline=request_deadline,
        previous=previous,
        run=run,
        source_snapshot_sha256="0" * 64,
        recorded_at=at + dt.timedelta(minutes=LovableOpinionOutcome.objects.count()),
    )


def _answered(
    matter: Matter,
    deadline: dt.date,
    *,
    ended: dt.date,
    ended_by=None,
    note: str = "",
    submission=None,
) -> MatterResponseDeadline:
    return MatterResponseDeadline.objects.create(
        matter=matter,
        deadline=deadline,
        outcome=ResponseDeadlineOutcome.ANSWERED,
        submission=submission,
        note=note,
        ended_at=_at(ended),
        ended_by=ended_by,
    )


def _facts_for(user, matter: Matter) -> list[opinions.OpinionFact]:
    return [fact for fact in opinions.opinion_facts(user) if fact.matter.matter_id == matter.pk]


# ---------------------------------------------------------------------------
# opinion_facts: where a sent opinion is recorded
# ---------------------------------------------------------------------------


def test_an_imported_sent_statement_is_one_fact_with_its_day_and_deadline(specialist):
    matter = factories.MatterFactory(owner=specialist)
    _imported(matter, OpinionOutcome.SENT, INSIDE, request_deadline=dt.date(2026, 6, 10))

    facts = opinions.opinion_facts(specialist)

    assert len(facts) == 1
    fact = facts[0]
    assert fact.matter.matter_id == matter.pk
    assert fact.sent_on == INSIDE
    assert fact.deadline == dt.date(2026, 6, 10)
    assert fact.source == SOURCE_IMPORTED
    assert fact.on_time is True


def test_an_imported_statement_without_a_deadline_has_none(specialist):
    matter = factories.MatterFactory(owner=specialist)
    _imported(matter, OpinionOutcome.SENT, INSIDE)

    (fact,) = opinions.opinion_facts(specialist)

    assert fact.deadline is None
    assert fact.on_time is None


def test_not_sent_is_not_a_sent_opinion(specialist):
    matter = factories.MatterFactory(owner=specialist)
    _imported(matter, OpinionOutcome.NOT_SENT)

    assert opinions.opinion_facts(specialist) == []


def test_a_statement_the_source_took_back_is_not_a_sent_opinion(specialist):
    """SENT, then CLEARED: the current row says nothing, so nothing is counted."""
    matter = factories.MatterFactory(owner=specialist)
    _imported(matter, OpinionOutcome.SENT, INSIDE)
    _imported(matter, OpinionOutcome.CLEARED)

    assert opinions.opinion_facts(specialist) == []


def test_a_superseded_sent_statement_is_not_counted_twice(specialist):
    """The source corrected the day: only the current row is the opinion."""
    matter = factories.MatterFactory(owner=specialist)
    _imported(
        matter, OpinionOutcome.SENT, dt.date(2026, 5, 1), request_deadline=dt.date(2026, 5, 2)
    )
    _imported(matter, OpinionOutcome.SENT, INSIDE, request_deadline=dt.date(2026, 6, 10))

    facts = opinions.opinion_facts(specialist)

    assert [(fact.sent_on, fact.deadline) for fact in facts] == [(INSIDE, dt.date(2026, 6, 10))]


def test_a_sent_statement_later_replaced_by_not_sent_is_not_counted(specialist):
    matter = factories.MatterFactory(owner=specialist)
    _imported(matter, OpinionOutcome.SENT, INSIDE)
    _imported(matter, OpinionOutcome.NOT_SENT)

    assert opinions.opinion_facts(specialist) == []


def test_a_request_a_person_ended_as_answered_without_a_document_is_a_fact(specialist):
    matter = factories.MatterFactory(owner=specialist)
    _answered(
        matter,
        dt.date(2026, 4, 20),
        ended=dt.date(2026, 4, 15),
        ended_by=specialist,
        note="Vastasime kohtumisel.",
    )

    (fact,) = opinions.opinion_facts(specialist)

    assert fact.matter.matter_id == matter.pk
    assert fact.sent_on == dt.date(2026, 4, 15)
    assert fact.deadline == dt.date(2026, 4, 20)
    assert fact.source == SOURCE_ANSWERED


def test_the_synchronisations_own_ending_is_not_a_second_fact(specialist):
    """No actor, no document, the sync's note: the imported statement is the fact."""
    matter = factories.MatterFactory(owner=specialist)
    _answered(
        matter,
        dt.date(2026, 4, 20),
        ended=dt.date(2026, 11, 11),
        ended_by=None,
        note=f"{SYNC_ENDING_NOTE} saadetud 15.04.2026.",
    )
    assert SYNC_ENDING_NOTE == "Lovable'i järgi"
    assert opinions.opinion_facts(specialist) == []

    _imported(
        matter, OpinionOutcome.SENT, dt.date(2026, 4, 15), request_deadline=dt.date(2026, 4, 20)
    )

    facts = opinions.opinion_facts(specialist)
    assert [(fact.sent_on, fact.source) for fact in facts] == [
        (dt.date(2026, 4, 15), SOURCE_IMPORTED)
    ]


def test_an_ending_whose_person_is_gone_is_still_a_persons(specialist):
    """`ended_by` is SET_NULL; the note is theirs, not the synchronisation's."""
    matter = factories.MatterFactory(owner=specialist)
    _answered(
        matter,
        dt.date(2026, 4, 20),
        ended=dt.date(2026, 4, 15),
        ended_by=None,
        note="Vastasime telefonitsi.",
    )

    assert [fact.source for fact in opinions.opinion_facts(specialist)] == [SOURCE_ANSWERED]


@pytest.mark.parametrize(
    "outcome",
    [
        ResponseDeadlineOutcome.NOT_ANSWERING,
        ResponseDeadlineOutcome.MOVED,
        ResponseDeadlineOutcome.SUPERSEDED,
    ],
)
def test_a_request_that_ended_otherwise_is_not_a_sent_opinion(outcome, specialist):
    matter = factories.MatterFactory(owner=specialist)
    MatterResponseDeadline.objects.create(
        matter=matter,
        deadline=dt.date(2026, 4, 20),
        outcome=outcome,
        ended_at=_at(dt.date(2026, 4, 15)),
        ended_by=specialist,
    )

    assert opinions.opinion_facts(specialist) == []


def test_a_sent_koja_arvamus_is_a_fact_and_the_request_it_answered_is_not_another(specialist):
    matter = factories.MatterFactory(owner=specialist)
    submission = factories.send_opinion_through_services(matter, specialist)
    submission.refresh_from_db()
    _answered(
        matter,
        dt.date(2027, 1, 15),
        ended=TODAY,
        ended_by=specialist,
        submission=submission,
    )

    facts = _facts_for(specialist, matter)

    assert [fact.source for fact in facts] == [SOURCE_SUBMISSION]
    assert facts[0].sent_on == _REAL_LOCALDATE(submission.sent_at)
    assert facts[0].deadline == dt.date(2027, 1, 15)


def test_a_deleted_teema_contributes_nothing(specialist):
    kept = factories.MatterFactory(owner=specialist)
    gone = factories.MatterFactory(owner=specialist)
    _imported(kept, OpinionOutcome.SENT, INSIDE)
    _imported(gone, OpinionOutcome.SENT, INSIDE)
    _answered(gone, dt.date(2026, 4, 20), ended=dt.date(2026, 4, 15), ended_by=specialist)
    assert len(opinions.opinion_facts(specialist)) == 3

    Matter.all_objects.filter(pk=gone.pk).update(deleted_at=timezone.now())

    assert [fact.matter.matter_id for fact in opinions.opinion_facts(specialist)] == [kept.pk]
    assert set(opinions.matter_facts(specialist)) == {kept.pk}
    assert opinions.matter_deadlines(specialist, opinions.matter_facts(specialist)) == {}


def test_an_administrator_without_a_business_role_reads_nothing(specialist, administrator):
    matter = factories.MatterFactory(owner=specialist)
    _imported(matter, OpinionOutcome.SENT, INSIDE)
    _answered(matter, dt.date(2026, 4, 20), ended=dt.date(2026, 4, 15), ended_by=specialist)
    assert len(opinions.opinion_facts(specialist)) == 2

    assert opinions.matter_facts(administrator) == {}
    assert opinions.opinion_facts(administrator) == []


def test_a_reader_reads_the_same_facts_as_a_specialist(specialist, reader):
    matter = factories.MatterFactory(owner=specialist)
    _imported(matter, OpinionOutcome.SENT, INSIDE)

    assert opinions.opinion_facts(reader) == opinions.opinion_facts(specialist)


def test_facts_are_listed_oldest_first(specialist):
    late = factories.MatterFactory(owner=specialist)
    early = factories.MatterFactory(owner=specialist)
    _imported(late, OpinionOutcome.SENT, INSIDE)
    _imported(early, OpinionOutcome.SENT, OUTSIDE)

    assert [fact.sent_on for fact in opinions.opinion_facts(specialist)] == [OUTSIDE, INSIDE]


def test_facts_can_be_read_for_a_narrower_set_of_teemad(specialist):
    one = factories.MatterFactory(owner=specialist)
    other = factories.MatterFactory(owner=specialist)
    _imported(one, OpinionOutcome.SENT, INSIDE)
    _imported(other, OpinionOutcome.SENT, INSIDE)
    known = opinions.matter_facts(specialist)

    narrowed = opinions.opinion_facts(specialist, {one.pk: known[one.pk]})

    assert [fact.matter.matter_id for fact in narrowed] == [one.pk]
    assert opinions.opinion_facts(specialist, {}) == []


# ---------------------------------------------------------------------------
# matter_facts: what a statistic knows about a Teema
# ---------------------------------------------------------------------------


def test_the_owner_is_called_by_the_short_name(specialist, mari):
    matter = factories.MatterFactory(owner=mari)

    assert opinions.matter_facts(specialist)[matter.pk].owner == "Mari"
    assert mari.get_short_name() == "Mari"


def test_a_former_owner_is_called_by_the_first_word_of_the_name(specialist):
    matter = factories.MatterFactory(owner=None, former_owner_name="Mait Palts")

    assert opinions.matter_facts(specialist)[matter.pk].owner == "Mait"


def test_the_current_owner_is_named_before_a_former_one(specialist, mari):
    matter = factories.MatterFactory(owner=mari, former_owner_name="Mait Palts")

    assert opinions.matter_facts(specialist)[matter.pk].owner == "Mari"


@pytest.mark.parametrize("former", ["", "   "])
def test_a_teema_naming_nobody_is_maaramata(former, specialist):
    matter = factories.MatterFactory(owner=None, former_owner_name=former)

    assert opinions.matter_facts(specialist)[matter.pk].owner == "Määramata"
    assert opinions.NO_OWNER == "Määramata"


def _instrument(key: str) -> LegalInstrumentType:
    instrument, _created = LegalInstrumentType.objects.get_or_create(
        key=key, defaults={"label_et": f"Liik {key}"}
    )
    return instrument


@pytest.mark.parametrize("key", sorted(EU_INSTRUMENT_KEYS))
def test_an_eu_instrument_makes_the_teema_eu(key, specialist):
    matter = factories.MatterFactory(owner=specialist)
    instrument = _instrument(key)
    matter.legal_instruments.add(instrument)

    found = opinions.matter_facts(specialist)[matter.pk]

    assert found.is_eu is True
    assert found.instrument == instrument.label_et


def test_a_domestic_instrument_or_none_is_not_eu(specialist):
    bare = factories.MatterFactory(owner=specialist)
    domestic = factories.MatterFactory(owner=specialist)
    domestic.legal_instruments.add(_instrument("statdash-riigisisene"))
    both = factories.MatterFactory(owner=specialist)
    both.legal_instruments.add(_instrument("statdash-riigisisene"), _instrument("direktiiv"))

    found = opinions.matter_facts(specialist)

    assert found[bare.pk].is_eu is False
    assert found[bare.pk].instrument == "Määramata"
    assert found[domestic.pk].is_eu is False
    assert found[domestic.pk].instrument == "Liik statdash-riigisisene"
    assert found[both.pk].is_eu is True
    assert found[both.pk].instrument == "Mitu liiki"


def test_kodas_own_initiative_is_read_from_the_track(specialist):
    own = factories.MatterFactory(owner=specialist, track=Track.KODA_INITIATIVE)
    other = factories.MatterFactory(owner=specialist, track=Track.DOMESTIC)
    eu = factories.MatterFactory(owner=specialist, track=Track.EU_INITIATIVE)

    found = opinions.matter_facts(specialist)

    assert found[own.pk].koda_initiative is True
    assert found[other.pk].koda_initiative is False
    # The procedure is not the instrument: an EU track alone is not «ELiga seotud».
    assert found[eu.pk].koda_initiative is False
    assert found[eu.pk].is_eu is False


def test_the_reference_year_and_arrival_are_carried(specialist):
    matter = factories.MatterFactory(
        owner=specialist, reference_year=2024, received_date=dt.date(2024, 2, 3)
    )

    found = opinions.matter_facts(specialist)[matter.pk]

    assert found.year == 2024
    assert found.received == dt.date(2024, 2, 3)
    assert found.reference == f"2024_{matter.reference_number}"
    assert found.title == matter.title


def test_a_teemas_deadline_is_the_open_one_else_the_last_ended_else_the_imported(specialist):
    open_one = factories.MatterFactory(owner=specialist, response_deadline=dt.date(2026, 12, 1))
    _answered(open_one, dt.date(2026, 3, 1), ended=dt.date(2026, 2, 20), ended_by=specialist)
    ended = factories.MatterFactory(owner=specialist)
    _imported(ended, OpinionOutcome.SENT, INSIDE, request_deadline=dt.date(2026, 6, 10))
    _answered(ended, dt.date(2026, 3, 1), ended=dt.date(2026, 2, 20), ended_by=specialist)
    _answered(ended, dt.date(2026, 9, 1), ended=dt.date(2026, 8, 20), ended_by=specialist)
    imported = factories.MatterFactory(owner=specialist)
    _imported(imported, OpinionOutcome.SENT, INSIDE, request_deadline=dt.date(2026, 6, 10))
    bare = factories.MatterFactory(owner=specialist)

    known = opinions.matter_facts(specialist)
    found = opinions.matter_deadlines(specialist, known)

    assert found == {
        open_one.pk: dt.date(2026, 12, 1),
        ended.pk: dt.date(2026, 9, 1),
        imported.pk: dt.date(2026, 6, 10),
    }
    assert bare.pk not in found


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded(mari, jaan):
    """Three opinions inside the last 12 months (two Mari's, one Jaan's), one in 2024."""
    world = {
        "mari_may": factories.MatterFactory(
            owner=mari, title="Mari maikuu teema", received_date=dt.date(2026, 4, 20)
        ),
        "mari_june": factories.MatterFactory(owner=mari, title="Mari juunikuu teema"),
        "jaan_march": factories.MatterFactory(owner=jaan, title="Jaani märtsikuu teema"),
        "jaan_2024": factories.MatterFactory(
            owner=jaan,
            title="Jaani vana teema",
            reference_year=2024,
            received_date=dt.date(2024, 4, 1),
        ),
        "unsent": factories.MatterFactory(owner=jaan, title="Saatmata teema"),
    }
    _imported(
        world["mari_may"],
        OpinionOutcome.SENT,
        dt.date(2026, 5, 4),
        request_deadline=dt.date(2026, 5, 10),
    )
    _imported(world["mari_june"], OpinionOutcome.SENT, INSIDE)
    _imported(
        world["jaan_march"],
        OpinionOutcome.SENT,
        dt.date(2026, 3, 9),
        request_deadline=dt.date(2026, 3, 1),
    )
    _imported(world["jaan_2024"], OpinionOutcome.SENT, OUTSIDE)
    _imported(world["unsent"], OpinionOutcome.NOT_SENT)
    return world


def _tile(body: str, label: str) -> str:
    """The value a tile shows under ``label``."""
    match = re.search(
        rf'<span class="stattile__label">{re.escape(label)}</span>\s*'
        r'<span class="stattile__value">([^<]*)</span>',
        body,
    )
    assert match, f"no tile «{label}»"
    return match.group(1).strip()


def test_the_page_opens_for_a_specialist(client, specialist, seeded):
    client.force_login(specialist)

    response = client.get(reverse("reporting:dashboard"))

    assert response.status_code == 200
    assert reverse("reporting:dashboard") == "/statistika/"
    body = response.content.decode()
    for label in (
        "Välja saadetud arvamused",
        "Sisse tulnud teemad",
        "Tähtajaks esitatud",
        "Aega vastamiseks",
    ):
        assert f'<span class="stattile__label">{label}</span>' in body
    assert "Pikaajalised trendid" in body
    assert "Välja saadetud arvamused kuude lõikes" in body
    assert "Rekordid" in body


def test_the_tab_strip_opens_on_ulevaade_and_offers_seis(client, specialist, seeded):
    client.force_login(specialist)

    response = client.get(reverse("reporting:dashboard"))

    assert response.context["active_tab"] == "arvamused"
    body = response.content.decode()
    active = re.findall(
        r'<a class="tabs__tab is-active"\s+aria-current="page"\s+href="([^"]+)">([^<]+)</a>', body
    )
    assert active == [("/statistika/", "Ülevaade")]
    assert re.search(
        rf'<a class="tabs__tab "\s+href="{re.escape(reverse("reporting:overview"))}">Seis</a>',
        body,
    )


def test_the_sent_tile_counts_the_opinions_of_the_last_twelve_months(client, specialist, seeded):
    client.force_login(specialist)

    response = client.get(reverse("reporting:dashboard"))

    period = response.context["period"]
    assert (period.key, period.start, period.end) == ("12", dt.date(2025, 11, 17), TODAY)
    summary = response.context["summary"]
    assert summary.sent == 3
    assert summary.sent_before == 0
    body = response.content.decode()
    assert _tile(body, "Välja saadetud arvamused") == "3"
    # One of three deadlines was missed … and one Teema arrived in the period.
    assert (summary.on_time, summary.with_deadline) == (1, 2)
    assert _tile(body, "Tähtajaks esitatud") == "50%"
    assert summary.received == 1
    assert _tile(body, "Sisse tulnud teemad") == "1"
    assert summary.days_given == 20
    assert _tile(body, "Aega vastamiseks") == "20 p"
    assert [(row.owner, row.count) for row in summary.owners] == [("Mari", 2), ("Jaan", 1)]


def test_choosing_a_past_year_moves_the_figure_to_that_year(client, specialist, seeded):
    client.force_login(specialist)

    response = client.get(reverse("reporting:dashboard"), {"periood": "aasta", "aasta": "2024"})

    period = response.context["period"]
    assert (period.start, period.end) == (dt.date(2024, 1, 1), dt.date(2024, 12, 31))
    assert response.context["summary"].sent == 1
    body = response.content.decode()
    assert _tile(body, "Välja saadetud arvamused") == "1"
    assert "01.01.2024–31.12.2024" in body
    assert [(row.owner, row.count) for row in response.context["summary"].owners] == [("Jaan", 1)]


def test_a_year_with_nothing_sent_says_so(client, specialist, seeded):
    client.force_login(specialist)

    response = client.get(reverse("reporting:dashboard"), {"periood": "aasta", "aasta": "2019"})

    assert response.context["summary"].sent == 0
    body = response.content.decode()
    assert _tile(body, "Välja saadetud arvamused") == "0"
    assert _tile(body, "Tähtajaks esitatud") == "—"
    assert _tile(body, "Aega vastamiseks") == "—"
    assert "Sel perioodil ei ole välja saadetud arvamusi." in body


@pytest.mark.parametrize(("name", "expected"), [("Mari", 2), ("Jaan", 1)])
def test_choosing_a_lawyer_narrows_the_figure(name, expected, client, specialist, seeded):
    client.force_login(specialist)

    response = client.get(reverse("reporting:dashboard"), {"jurist": name})

    assert response.context["jurist"] == name
    assert response.context["summary"].sent == expected
    assert _tile(response.content.decode(), "Välja saadetud arvamused") == str(expected)
    assert [row.owner for row in response.context["summary"].owners] == [name]


def test_a_lawyer_and_a_year_narrow_together(client, specialist, seeded):
    client.force_login(specialist)
    url = reverse("reporting:dashboard")

    jaan = client.get(url, {"periood": "aasta", "aasta": "2024", "jurist": "Jaan"})
    mari = client.get(url, {"periood": "aasta", "aasta": "2024", "jurist": "Mari"})

    assert jaan.context["summary"].sent == 1
    assert mari.context["summary"].sent == 0


def test_a_name_nobody_carries_narrows_nothing(client, specialist, seeded):
    client.force_login(specialist)

    response = client.get(reverse("reporting:dashboard"), {"jurist": "Keegi Teine"})

    assert response.status_code == 200
    assert response.context["jurist"] == ""
    assert response.context["summary"].sent == 3


def test_a_malformed_period_is_the_default_one(client, specialist, seeded):
    client.force_login(specialist)

    response = client.get(
        reverse("reporting:dashboard"), {"periood": "vali", "alates": "x", "trend": "99"}
    )

    assert response.status_code == 200
    assert response.context["period"].key == "12"
    assert response.context["summary"].sent == 3
    assert response.context["trend_span"] == "2011–2026"


@pytest.mark.parametrize(
    ("trend", "span", "years"),
    [("5", "2022–2026", 5), ("10", "2017–2026", 10), ("koik", "2011–2026", 16)],
)
def test_the_trend_range_decides_the_years_drawn(trend, span, years, client, specialist, seeded):
    client.force_login(specialist)

    response = client.get(reverse("reporting:dashboard"), {"trend": trend})

    assert response.context["trend_span"] == span
    found = response.context["trends"]
    assert [item.key for item in found] == ["sent", "days", "ontime", "eu", "koda"]
    assert all(len(item.bars) == years for item in found)
    assert found[0].bars[-1].year == 2026 and found[0].bars[-1].partial


def test_the_page_links_its_figure_to_the_opinions_it_counted(client, specialist, seeded):
    client.force_login(specialist)

    default = client.get(reverse("reporting:dashboard"))
    narrowed = client.get(
        reverse("reporting:dashboard"), {"periood": "aasta", "aasta": "2024", "jurist": "Jaan"}
    )

    assert default.context["rows_url"] == reverse("reporting:opinion_rows")
    assert narrowed.context["rows_url"] == (
        reverse("reporting:opinion_rows") + "?periood=aasta&aasta=2024&jurist=Jaan"
    )


def test_a_signed_out_reader_is_sent_to_sign_in(client, seeded):
    for name in ("reporting:dashboard", "reporting:opinion_rows"):
        response = client.get(reverse(name))

        assert response.status_code == 302
        assert response.url.startswith(resolve_url(settings.LOGIN_URL))


def test_an_administrator_sees_the_page_with_nothing_on_it(client, administrator, seeded):
    client.force_login(administrator)

    response = client.get(reverse("reporting:dashboard"))

    assert response.status_code == 200
    assert response.context["summary"].sent == 0
    assert response.context["summary"].received == 0
    body = response.content.decode()
    assert _tile(body, "Välja saadetud arvamused") == "0"
    assert "Mari" not in body and "Jaani" not in body


# ---------------------------------------------------------------------------
# The opinions a figure counted
# ---------------------------------------------------------------------------


def _detail(matter: Matter) -> str:
    return reverse("matters:matter_detail", kwargs={"pk": matter.pk})


def _listed(body: str) -> list[str]:
    """The Teema links of the list, in page order."""
    return re.findall(
        r'<a href="(/[^"]+)">[^<]+</a>\s*(?:<span class="statrows__ref">|</td>)', body
    )


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({}, ["mari_june", "mari_may", "jaan_march"]),
        ({"periood": "12"}, ["mari_june", "mari_may", "jaan_march"]),
        ({"periood": "aasta", "aasta": "2024"}, ["jaan_2024"]),
        ({"periood": "aasta", "aasta": "2026"}, ["mari_june", "mari_may", "jaan_march"]),
        ({"jurist": "Mari"}, ["mari_june", "mari_may"]),
        ({"jurist": "Jaan"}, ["jaan_march"]),
        ({"periood": "aasta", "aasta": "2024", "jurist": "Mari"}, []),
        (
            {"periood": "vali", "alates": "2026-05-04", "kuni": "2026-06-01"},
            ["mari_june", "mari_may"],
        ),
        ({"periood": "vali", "alates": "2026-05-05", "kuni": "2026-05-31"}, []),
        ({"periood": "3"}, []),
    ],
)
def test_the_list_holds_exactly_what_the_tile_counted_newest_first(
    params, expected, client, specialist, seeded
):
    client.force_login(specialist)

    tile = client.get(reverse("reporting:dashboard"), params).context["summary"].sent
    response = client.get(reverse("reporting:opinion_rows"), params)

    assert response.status_code == 200
    rows = response.context["rows"]
    assert len(rows) == tile == len(expected)
    assert [fact.matter.matter_id for fact in rows] == [seeded[key].pk for key in expected]
    assert [fact.sent_on for fact in rows] == sorted((fact.sent_on for fact in rows), reverse=True)
    body = response.content.decode()
    assert _listed(body) == [_detail(seeded[key]) for key in expected]
    period = response.context["period"]
    jurist = f" · {params['jurist']}" if "jurist" in params else ""
    assert f'<span class="pagehead__context">{period.span}{jurist} · {tile}</span>' in body
    if not expected:
        assert "Sel perioodil ei ole välja saadetud arvamusi." in body


def test_the_list_names_each_opinions_day_deadline_and_lawyer(client, specialist, seeded):
    client.force_login(specialist)

    body = client.get(reverse("reporting:opinion_rows")).content.decode()

    assert "Välja saadetud arvamused" in body
    for title in ("Mari maikuu teema", "Mari juunikuu teema", "Jaani märtsikuu teema"):
        assert title in body
    assert "Jaani vana teema" not in body
    assert "Saatmata teema" not in body
    assert "04.05.2026" in body and "10.05.2026" in body
    # Sent 09.03 against a deadline of 01.03: the only late one.
    assert body.count('<span class="statrows__late">hiljem</span>') == 1


def test_the_list_links_back_to_the_page_it_came_from(client, specialist, seeded):
    client.force_login(specialist)

    response = client.get(
        reverse("reporting:opinion_rows"), {"periood": "aasta", "aasta": "2024", "jurist": "Jaan"}
    )

    assert response.context["back_url"] == (
        reverse("reporting:dashboard") + "?periood=aasta&aasta=2024&jurist=Jaan"
    )


def test_a_months_bar_links_to_that_months_opinions(client, specialist, seeded):
    client.force_login(specialist)

    bars = client.get(reverse("reporting:dashboard")).context["bars"]

    assert len(bars) == 13
    may = next(item for item in bars if item["bar"].label == "mai 26")
    assert may["bar"].total == 1
    rows = client.get(may["url"]).context["rows"]
    assert [fact.matter.matter_id for fact in rows] == [seeded["mari_may"].pk]
    for item in bars:
        assert len(client.get(item["url"]).context["rows"]) == item["bar"].total


def test_an_administrator_lists_nothing(client, administrator, seeded):
    client.force_login(administrator)

    response = client.get(reverse("reporting:opinion_rows"))

    assert response.status_code == 200
    assert response.context["rows"] == []


# ---------------------------------------------------------------------------
# The page that used to be first
# ---------------------------------------------------------------------------


def test_the_counts_page_kept_its_name_and_moved_to_seis(client, specialist, seeded):
    assert reverse("reporting:overview") == "/statistika/seis/"
    client.force_login(specialist)

    response = client.get(reverse("reporting:overview"))

    assert response.status_code == 200
    assert response.context["active_tab"] == "ulevaade"
    assert "Statistika" in response.content.decode()


def test_an_imported_statement_and_a_native_record_of_the_same_day_are_one_opinion(specialist):
    """A person answering the opinion the register says went out that day is one act."""
    matter = factories.MatterFactory(owner=specialist)
    day = dt.date(2026, 10, 5)
    _imported(matter, OpinionOutcome.SENT, day, request_deadline=dt.date(2026, 10, 9))
    _answered(
        matter, dt.date(2026, 10, 9), ended=day, ended_by=specialist, note="Saatsime e-kirjaga"
    )

    facts = [
        fact for fact in opinions.opinion_facts(specialist) if fact.matter.matter_id == matter.pk
    ]

    assert [(fact.sent_on, fact.source) for fact in facts] == [(day, opinions.SOURCE_ANSWERED)]


def test_an_imported_statement_and_a_native_record_of_another_day_are_two_opinions(specialist):
    matter = factories.MatterFactory(owner=specialist)
    _imported(matter, OpinionOutcome.SENT, dt.date(2026, 3, 2))
    _answered(
        matter,
        dt.date(2026, 10, 9),
        ended=dt.date(2026, 10, 5),
        ended_by=specialist,
        note="Saatsime e-kirjaga",
    )

    facts = [
        fact for fact in opinions.opinion_facts(specialist) if fact.matter.matter_id == matter.pk
    ]

    assert sorted(fact.source for fact in facts) == [
        opinions.SOURCE_ANSWERED,
        opinions.SOURCE_IMPORTED,
    ]
