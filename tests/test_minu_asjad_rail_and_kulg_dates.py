"""Owner's UX review of 2026-10-26 — fixes 1, 2 and 4.

1. Minu asjad's rail reads alerts → Kalender → Järgmise tegevuseta →
   Kuupäevata → Märkmed → Minu statistika (UX package of 2026-10-28: the notes
   moved up, «Viimati muudetud» left the rail for «Viimased tegevused»); a
   colleague's page has Kiirvaade where the notes are. The calendar lost its
   footer note.
2. A `Menetluse kulg` step's date editor opens an undated step on today
   (Europe/Tallinn) in all four `Täpsus` controls, a dated one on its own date.
4. The rail no longer writes «Eesolev tähtaeg» above an upcoming deadline.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.intelligence.services import add_important_date
from app.matters.forms import RailStepDateForm, rail_date_prefill
from app.matters.models import MatterAssignmentNotice
from app.matters.process_phases import PHASE_JOUSTUMINE, PHASE_VALITSUS
from app.matters.services import set_rail_step_date
from app.workflow.dates import period_bounds
from app.workflow.enums import DatePrecision
from tests import factories
from tests.test_rail_step_date import _file, _post, _rail, _row, _url

pytestmark = pytest.mark.django_db

MY_WORK = reverse("matters:my_work")
SELF_ORDER = [
    "Kalender",
    "Järgmise tegevuseta",
    "Kuupäevata",
    "Märkmed",
    "Statistika",
]

#: A colleague's desk: Kiirvaade in the notes' slot, the rest identical.
COLLEAGUE_ORDER = ["Kalender", "Järgmise tegevuseta", "Kuupäevata", "Kiirvaade", "Statistika"]


def _rail_blocks(html: str) -> list[str]:
    aside = html[html.index('<aside class="worklayout2__rail"') : html.index("</aside>")]
    # Top-level blocks only: the calendar's popovers carry no `railblock`.
    return re.findall(r'<section class="railblock[^"]*"[^>]*aria-label="([^"]+)"', aside)


# ---------------------------------------------------------------------------
# 1. The rail's order
# ---------------------------------------------------------------------------


def test_the_calendar_is_first_on_ones_own_desk_without_alerts(client, specialist):
    client.force_login(specialist)

    assert _rail_blocks(client.get(MY_WORK).content.decode()) == SELF_ORDER


def test_alerts_come_before_the_calendar_and_only_when_there_are_any(
    client, specialist, other_specialist
):
    matter = factories.MatterFactory(owner=specialist, title="Äsja antud teema")
    MatterAssignmentNotice.objects.create(
        matter=matter, recipient=specialist, assigned_by=other_specialist
    )
    client.force_login(specialist)

    blocks = _rail_blocks(client.get(MY_WORK).content.decode())

    # One «Teavitused» block for every alert since docs/adr/0198.
    assert blocks == ["Teavitused", *SELF_ORDER]


def test_a_colleagues_page_has_kiirvaade_in_the_notes_slot_and_no_notes(
    client, specialist, department_head
):
    from app.matters.person_work import save_scratchpad

    save_scratchpad(specialist, "Väga isiklik märge")
    client.force_login(department_head)

    body = client.get(reverse("matters:person_work", kwargs={"pk": specialist.pk})).content.decode()

    assert _rail_blocks(body) == COLLEAGUE_ORDER
    assert "Märkmed" not in _rail_blocks(body)
    assert "Väga isiklik märge" not in body
    assert "pw-note-body" not in body


def test_the_notes_keep_their_text_autosave_and_revision_after_the_move(client, specialist):
    from app.matters.person_work import save_scratchpad

    save_scratchpad(specialist, "Minu meelespea")
    client.force_login(specialist)

    body = client.get(MY_WORK).content.decode()
    notes = body[body.index('aria-label="Märkmed"') :]

    assert "Minu meelespea" in notes
    assert reverse("matters:save_scratchpad") in notes
    assert 'id="pw-note-revision"' in notes and 'id="pw-note-conflict"' in notes


@pytest.mark.parametrize("view", ["", "kuu", "kvartal", "aasta"])
def test_the_calendar_has_no_footer_note_in_any_view(client, specialist, view):
    client.force_login(specialist)

    body = client.get(MY_WORK, {"kalender": view} if view else {}).content.decode()

    assert 'id="minu-kalender"' in body
    assert "Näitab kehtivaid kirjeid" not in body
    assert "mitte tähtaeg" not in body
    # The legend that tells the two categories apart stays.
    assert "Tähtajad ja tegevused" in body and "Jõustumised" in body


# ---------------------------------------------------------------------------
# 2. The rail step's date editor opens on today, or on what it holds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("today", "month", "quarter"),
    [
        (date(2026, 10, 26), "10", "4"),
        (date(2026, 12, 31), "12", "4"),
        (date(2027, 1, 1), "1", "1"),
    ],
)
def test_an_undated_step_opens_on_today_in_all_four_controls(today, month, quarter):
    form = RailStepDateForm(today=today)

    assert form["kp_precision"].value() == "EXACT"
    assert form["kp_date"].value() == today
    assert form["kp_month"].value() == month
    assert form["kp_quarter"].value() == quarter
    assert form["kp_year"].value() == today.year


@pytest.mark.parametrize(
    ("stored", "precision", "expected"),
    [
        (
            date(2027, 3, 15),
            "EXACT",
            {"kp_date": date(2027, 3, 15), "kp_month": "3", "kp_quarter": "1", "kp_year": 2027},
        ),
        (date(2027, 5, 1), "MONTH", {"kp_month": "5", "kp_quarter": "2", "kp_year": 2027}),
        (date(2027, 7, 1), "QUARTER", {"kp_quarter": "3", "kp_year": 2027}),
        (date(2027, 1, 1), "YEAR", {"kp_year": 2027}),
    ],
)
def test_a_recorded_date_wins_and_nothing_comes_from_today(stored, precision, expected):
    form = RailStepDateForm(occurs_on=stored, precision=precision, today=date(2026, 10, 26))

    assert form["kp_precision"].value() == precision
    for name in ("kp_date", "kp_month", "kp_quarter", "kp_year"):
        assert form[name].value() == expected.get(name), name


def test_the_prefill_is_the_rail_editors_alone():
    """The shared helper adds nothing a stored half-year or another form did not ask for."""
    assert rail_date_prefill("kp", date(2027, 1, 1), "HALF_YEAR", None) == {"kp_year": 2027}
    stored = RailStepDateForm(
        occurs_on=date(2027, 7, 1), precision="HALF_YEAR", today=date(2026, 10, 26)
    )
    assert stored["kp_precision"].value() == "HALF_YEAR"
    assert stored["kp_date"].value() is None
    # The year a half-year is in, so `Aasta` opens on it rather than empty.
    assert stored["kp_year"].value() == 2027
    inferred = RailStepDateForm(
        occurs_on=date(2027, 5, 9), precision="INFERRED", today=date(2026, 10, 26)
    )
    assert inferred["kp_precision"].value() == "INFERRED"
    assert inferred["kp_date"].value() == date(2027, 5, 9)


def test_the_panel_opens_on_tallinns_today(signed_in, specialist, monkeypatch):
    matter = _file(specialist)
    monkeypatch.setattr(timezone, "localdate", lambda *a, **k: date(2026, 10, 26))

    body = signed_in.get(
        _url(matter, PHASE_VALITSUS), headers={"HX-Request": "true"}
    ).content.decode()

    assert re.search(r'value="EXACT"[^>]*checked', body)
    assert 'value="26.10.2026"' in body
    assert re.search(r'<option value="10" selected>', body)
    assert re.search(r'<option value="4" selected>', body)
    assert 'value="2026"' in body
    assert 'data-period-follow="kp"' in body
    # Opening writes nothing.
    assert _row(matter, PHASE_VALITSUS) is None


def test_a_dated_step_reopens_on_its_own_date_not_today(signed_in, specialist, monkeypatch):
    matter = _file(specialist)
    set_rail_step_date(
        matter=matter,
        rail_key=PHASE_JOUSTUMINE,
        occurs_on=date(2028, 2, 1),
        precision=DatePrecision.MONTH.value,
        actor=specialist,
    )
    monkeypatch.setattr(timezone, "localdate", lambda *a, **k: date(2026, 10, 26))

    body = signed_in.get(
        _url(matter, PHASE_JOUSTUMINE), headers={"HX-Request": "true"}
    ).content.decode()

    assert re.search(r'value="MONTH"[^>]*checked', body)
    assert re.search(r'<option value="2" selected>', body)
    assert 'value="2028"' in body
    assert "26.10.2026" not in body


def test_an_emptied_box_still_clears_and_is_not_refilled(signed_in, specialist):
    matter = _file(specialist)
    _post(signed_in, matter, PHASE_VALITSUS, kp_precision="EXACT", kp_date="01.12.2026")

    _post(signed_in, matter, PHASE_VALITSUS, kp_precision="EXACT", kp_date="")

    assert _row(matter, PHASE_VALITSUS) is None


def test_a_refused_post_shows_what_was_posted_not_today(signed_in, specialist):
    matter = _file(specialist)

    response = _post(signed_in, matter, PHASE_VALITSUS, kp_precision="MONTH", kp_year="2031")

    body = response.content.decode()
    assert 'value="2031"' in body
    assert re.search(r'value="MONTH"[^>]*checked', body)
    assert _row(matter, PHASE_VALITSUS) is None


# ---------------------------------------------------------------------------
# 4. No «Eesolev tähtaeg» above an upcoming deadline on the rail
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("precision", ["EXACT", "QUARTER"])
def test_an_upcoming_deadline_on_the_rail_has_no_type_heading(signed_in, specialist, precision):
    matter = _file(specialist)
    today = timezone.localdate()
    start = today + timedelta(days=40) if precision == "EXACT" else date(today.year + 1, 10, 1)
    _start, end = period_bounds(start, precision)
    add_important_date(
        matter=matter,
        title="Valitsus kavandab planeeringu kehtestada",
        date_value=start,
        period_end=end,
        date_precision=precision,
        actor=specialist,
    )
    second = today + timedelta(days=80)
    add_important_date(
        matter=matter,
        title="Teine tähtaeg",
        date_value=second,
        period_end=second,
        actor=specialist,
    )

    rail = _rail(
        signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()
    )

    assert "Valitsus kavandab planeeringu kehtestada" in rail and "Teine tähtaeg" in rail
    assert rail.count("tl-step--event") >= 2
    assert "Eesolev tähtaeg" not in rail
