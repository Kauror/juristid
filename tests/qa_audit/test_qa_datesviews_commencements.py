"""QA audit 2026-10-14 (area `datesviews`) — Jõustumised inside /teemad/ (ADR 0158).

Independent probes at source 81a7d6e4 for what tests/test_register_commencements.py
does not already pin:

* every approximate precision (month, half-year, year — the quarter is pinned
  there) is written at its precision, never as its anchor or end day;
* the calendar's «longer period» strip for a half-year and a year, and a quarter
  that does not overlap the month stays out of it;
* a restricted record that is a Matter's *only* in-window commencement keeps the
  Matter out of the register for a reader, while a lawyer sees it;
* a Matter whose only commencement is cancelled is not «on» and is counted as
  having none;
* the real 100-row bound with 101 synthetic rows, and «näita kõiki»;
* a calendar day link under the default status keeps the closed files;
* `?andmed=paris` keeps a TEST twin's commencements out of rows, calendar and
  coverage.
"""

from __future__ import annotations

import datetime
from urllib.parse import parse_qs

import pytest
from django.http import QueryDict
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.intelligence import commencements
from app.intelligence.enums import EffectiveDateKind, FactStatus
from app.matters.enums import MatterDataClass
from app.workflow.enums import DatePrecision, Disposition
from tests import factories

pytestmark = pytest.mark.django_db

REGISTER = reverse("matters:matter_list")
TODAY = datetime.date(2026, 10, 14)


@pytest.fixture(autouse=True)
def pinned_today(monkeypatch):
    monkeypatch.setattr(commencements, "today", lambda: TODAY)
    return TODAY


def commence(matter, start=None, end=None, *, precision=DatePrecision.EXACT, **extra):
    kind = extra.pop("kind", EffectiveDateKind.KNOWN_DATE if start else EffectiveDateKind.UNKNOWN)
    return factories.EffectiveDateFactory(
        matter=matter,
        kind=kind,
        date_value=start,
        period_end=(end or start) if start else None,
        date_precision=precision,
        **extra,
    )


def listing(client, **params):
    return client.get(REGISTER, {"joustumine": "on", "vaade": "joustumised", **params})


def descriptions(response) -> list[str]:
    return [
        entry.record.description
        for bucket in response.context["commencement_buckets"]
        for entry in bucket.entries
    ]


def closed_matter(**extra):
    return factories.MatterFactory(
        is_open=False, disposition=Disposition.COMPLETED, closed_at=timezone.now(), **extra
    )


# ---------------------------------------------------------------------------
# Precision is shown honestly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "precision", "written", "label"),
    [
        (
            datetime.date(2027, 3, 1),
            datetime.date(2027, 3, 31),
            DatePrecision.MONTH,
            "märts 2027",
            "kuu täpsusega",
        ),
        (
            datetime.date(2027, 7, 1),
            datetime.date(2027, 12, 31),
            DatePrecision.HALF_YEAR,
            "II poolaasta 2027",
            "poolaasta täpsusega",
        ),
        (
            datetime.date(2028, 1, 1),
            datetime.date(2028, 12, 31),
            DatePrecision.YEAR,
            "2028",
            "aasta täpsusega",
        ),
    ],
)
def test_every_period_is_written_at_its_precision_never_as_a_day(
    signed_in, start, end, precision, written, label
):
    commence(factories.MatterFactory(), start, end, precision=precision, description="periood")

    response = listing(signed_in)
    body = response.content.decode()

    (bucket,) = response.context["commencement_buckets"]
    (entry,) = bucket.entries
    assert entry.record.display_when == written
    assert entry.precision_label == label
    for day in (start, end):
        assert f"{day.day}.{day.month}.{day.year}" not in body
        assert day.strftime("%d.%m.%Y") not in body


def test_an_inferred_day_is_a_day_on_its_cell(signed_in):
    commence(
        factories.MatterFactory(),
        datetime.date(2027, 2, 10),
        precision=DatePrecision.INFERRED,
        description="tuletatud",
    )
    response = signed_in.get(REGISTER, {"joustumine": "on", "vaade": "kalender", "kuu": "2027-02"})
    cal = response.context["commencement_calendar"]
    placed = {
        cell.day: [e.record.description for e in cell.entries]
        for week in cal.weeks
        for cell in week
        if cell.entries
    }
    assert placed == {datetime.date(2027, 2, 10): ["tuletatud"]}
    assert cal.month_entries == [] and cal.wider_entries == []


def test_half_year_and_year_overlapping_a_month_are_flagged_never_placed(signed_in):
    matter = factories.MatterFactory()
    commence(
        matter,
        datetime.date(2027, 1, 1),
        datetime.date(2027, 6, 30),
        precision=DatePrecision.HALF_YEAR,
        description="poolaasta",
    )
    commence(
        matter,
        datetime.date(2027, 1, 1),
        datetime.date(2027, 12, 31),
        precision=DatePrecision.YEAR,
        description="aasta",
    )
    commence(
        matter,
        datetime.date(2027, 4, 1),
        datetime.date(2027, 6, 30),
        precision=DatePrecision.QUARTER,
        description="teine kvartal",
    )
    commence(
        matter,
        datetime.date(2027, 2, 1),
        datetime.date(2027, 2, 28),
        precision=DatePrecision.MONTH,
        description="veebruar",
    )

    response = signed_in.get(REGISTER, {"joustumine": "on", "vaade": "kalender", "kuu": "2027-03"})
    cal = response.context["commencement_calendar"]

    assert sorted(e.record.description for e in cal.wider_entries) == ["aasta", "poolaasta"]
    assert cal.month_entries == []
    assert all(not cell.entries for week in cal.weeks for cell in week)
    body = response.content.decode()
    assert "võib jõustuda ka sel kuul" in body
    assert "teine kvartal" not in body
    assert "veebruar" not in body


# ---------------------------------------------------------------------------
# Authorization: restricted records and restricted Matters, by persona
# ---------------------------------------------------------------------------


def test_a_restricted_record_alone_in_the_window_selects_its_matter_for_readers_only(
    client, reader, specialist, administrator
):
    """Since docs/adr/0181 a READER reads a RESTRICTED record exactly as its
    owner does; the perimeter is the administrator without a grant."""
    readable = factories.MatterFactory(title="Loetav teema QA")
    commence(readable, datetime.date(2027, 1, 1), description="väljaspool akent")
    commence(
        readable,
        datetime.date(2027, 5, 5),
        description="piiratud aknas",
        visibility_override=Visibility.RESTRICTED,
    )
    window = {"joustub_alates": "1.5.2027", "joustub_kuni": "31.5.2027"}

    for insider in (reader, specialist):
        client.force_login(insider)
        table = client.get(REGISTER, {"olek": "koik", **window})
        assert table.context["total"] == 1
        assert "Loetav teema QA" in table.content.decode()
        assert descriptions(listing(client, **window)) == ["piiratud aknas"]
        as_calendar = client.get(REGISTER, {"vaade": "kalender", "kuu": "2027-05", **window})
        assert "piiratud aknas" in as_calendar.content.decode()

    client.force_login(administrator)
    table = client.get(REGISTER, {"olek": "koik", **window})
    assert table.context["total"] == 0
    assert "Loetav teema QA" not in table.content.decode()
    as_list = listing(client, **window)
    assert as_list.context["commencement_total"] == 0
    assert "piiratud aknas" not in as_list.content.decode()
    as_calendar = client.get(REGISTER, {"vaade": "kalender", "kuu": "2027-05", **window})
    assert "piiratud aknas" not in as_calendar.content.decode()


@pytest.mark.parametrize("persona", ["specialist", "department_head", "other_specialist"])
def test_lawyers_see_restricted_commencements_by_role(client, request, persona):
    hidden = factories.MatterFactory(title="Piiratud teema QA", visibility=Visibility.RESTRICTED)
    commence(hidden, datetime.date(2027, 1, 1), description="piiratud teema kirje")
    client.force_login(request.getfixturevalue(persona))
    assert descriptions(listing(client)) == ["piiratud teema kirje"]


# ---------------------------------------------------------------------------
# Active only, and coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [FactStatus.CANCELLED, FactStatus.SUPERSEDED])
def test_a_matter_whose_only_commencement_is_inactive_has_none(signed_in, status):
    matter = factories.MatterFactory(title="Tühistatud jõustumisega")
    commence(matter, datetime.date(2027, 1, 1), description="vana", status=status)

    response = listing(signed_in)

    assert response.context["total"] == 0
    assert response.context["commencement_total"] == 0
    assert response.context["commencement_coverage"]["missing"] == 1
    none_view = signed_in.get(REGISTER, {"joustumine": "puudub", "olek": "koik"})
    assert [m.title for m in none_view.context["page"].object_list] == ["Tühistatud jõustumisega"]


def test_a_removed_record_is_nowhere(signed_in):
    matter = factories.MatterFactory()
    record = commence(matter, datetime.date(2027, 1, 1), description="eemaldatud")
    record.removed_at = timezone.now()
    record.save(update_fields=["removed_at"])

    assert listing(signed_in).context["commencement_total"] == 0


# ---------------------------------------------------------------------------
# The real 100-row bound
# ---------------------------------------------------------------------------


def test_one_hundred_and_one_rows_show_one_hundred_and_the_whole_on_request(signed_in):
    assert commencements.BUCKET_LIMIT == 100
    matter = factories.MatterFactory(title="Sada üks")
    for number in range(101):
        commence(
            matter,
            datetime.date(2027, 1, 1) + datetime.timedelta(days=number),
            description=f"rida {number:03d}",
        )

    response = listing(signed_in)
    (bucket,) = response.context["commencement_buckets"]

    assert response.context["total"] == 1
    assert bucket.total == 101
    assert len(bucket.entries) == 100
    assert bucket.entries[-1].record.description == "rida 099"
    body = response.content.decode()
    assert "Näidatud 100 / 101" in body
    assert "näita kõiki 101" in body

    whole = signed_in.get(REGISTER, QueryDict(bucket.expand_query))
    (expanded,) = whole.context["commencement_buckets"]
    assert len(expanded.entries) == 101
    assert "Näidatud" not in whole.content.decode()

    descending = listing(signed_in, joustumiste_jarjestus="kuupaev_desc")
    (first_rows,) = descending.context["commencement_buckets"]
    assert first_rows.entries[0].record.description == "rida 100"


# ---------------------------------------------------------------------------
# Calendar → list keeps the population
# ---------------------------------------------------------------------------


def test_a_day_link_under_the_default_status_keeps_closed_files(signed_in):
    commence(closed_matter(title="Suletud QA"), datetime.date(2027, 4, 7), description="suletu")
    commence(
        factories.MatterFactory(title="Avatud QA"), datetime.date(2027, 4, 7), description="avatu"
    )

    calendar_view = signed_in.get(
        REGISTER, {"joustumine": "on", "vaade": "kalender", "kuu": "2027-04"}
    )
    day = next(
        cell
        for week in calendar_view.context["commencement_calendar"].weeks
        for cell in week
        if cell.day == datetime.date(2027, 4, 7)
    )
    assert len(day.entries) == 2

    narrowed = signed_in.get(REGISTER, QueryDict(day.query))
    assert sorted(descriptions(narrowed)) == ["avatu", "suletu"]
    assert narrowed.context["total"] == 2


def test_the_month_title_list_holds_the_months_days_and_month_rows_only(signed_in):
    matter = factories.MatterFactory()
    commence(matter, datetime.date(2027, 4, 30), description="päev")
    commence(
        matter,
        datetime.date(2027, 4, 1),
        datetime.date(2027, 4, 30),
        precision=DatePrecision.MONTH,
        description="kuu",
    )
    commence(
        matter,
        datetime.date(2027, 4, 1),
        datetime.date(2027, 6, 30),
        precision=DatePrecision.QUARTER,
        description="kvartal",
    )
    commence(matter, datetime.date(2027, 5, 1), description="järgmine kuu")

    calendar_view = signed_in.get(
        REGISTER, {"joustumine": "on", "vaade": "kalender", "kuu": "2027-04"}
    )
    month_list = signed_in.get(
        REGISTER, QueryDict(calendar_view.context["commencement_month_query"])
    )
    assert sorted(descriptions(month_list)) == ["kuu", "päev"]
    query = parse_qs(calendar_view.context["commencement_month_query"])
    assert query["joustub_kuni"] == ["30.4.2027"]


# ---------------------------------------------------------------------------
# TEST twins
# ---------------------------------------------------------------------------


def test_andmed_paris_keeps_a_test_twin_out_of_rows_calendar_and_coverage(signed_in):
    real = factories.MatterFactory(title="Päris QA")
    commence(real, datetime.date(2027, 6, 2), description="päris kirje")
    twin = factories.MatterFactory(title="TEST QA", data_class=MatterDataClass.TEST)
    commence(twin, datetime.date(2027, 6, 2), description="test kirje")
    factories.MatterFactory(title="TEST ilma", data_class=MatterDataClass.TEST)

    both = listing(signed_in)
    assert sorted(descriptions(both)) == ["päris kirje", "test kirje"]

    real_only = listing(signed_in, andmed="paris")
    assert descriptions(real_only) == ["päris kirje"]
    assert real_only.context["commencement_coverage"]["missing"] == 0
    assert parse_qs(real_only.context["commencement_coverage"]["missing_query"])["andmed"] == [
        "paris"
    ]

    cal = signed_in.get(
        REGISTER, {"joustumine": "on", "vaade": "kalender", "kuu": "2027-06", "andmed": "paris"}
    )
    assert "test kirje" not in cal.content.decode()
    assert "päris kirje" in cal.content.decode()


# ---------------------------------------------------------------------------
# The retired destination (ADR 0155)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/jalgimine/joustumised/", "/joustuvad-aktid/"])
@pytest.mark.parametrize("persona", ["specialist", "reader", "department_head"])
def test_the_retired_page_redirects_for_every_persona(client, request, path, persona):
    client.force_login(request.getfixturevalue(persona))
    response = client.get(path)
    assert response.status_code == 302
    assert response["Location"] == f"{REGISTER}?joustumine=on&olek=koik"


def test_no_commencement_destination_on_the_bar(signed_in):
    body = signed_in.get(REGISTER).content.decode()
    assert 'href="/jalgimine/joustumised/"' not in body
    assert 'href="/joustuvad-aktid/"' not in body
