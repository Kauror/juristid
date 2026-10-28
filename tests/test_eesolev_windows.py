"""Osakond → Eesolev: the six date windows, exactly.

Täna · Homme (by its weekday) · Käesoleva nädala ülejäänud päevad · Järgmine nädal · Ülejäänud kuu
· Kaugemal (`department_dashboard.upcoming_windows`). Consecutive, never
overlapping, exhaustive for every future real deadline — asserted here over
every day of four years rather than on a handful of chosen ones, then on the
awkward days by name: each weekday, the end of a month and of a year, a week
that crosses either, 29 February 2028. And what the windows are *for*: each
holds the rows it counts, its register query lists exactly its files, and the
remembered open/closed state is keyed by the window's kind — never its dates.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qsl, urlparse

import pytest
from django.utils import timezone

from app.accounts.disclosures import DISCLOSURES
from app.core.dates import end_of_month
from app.matters import department_dashboard as dd
from app.matters import work_items as wi
from app.matters.register_filters import register_population
from app.matters.services import create_matter
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics
from app.workflow.services import set_next_action

KEYS = ("tana", "homme", "nadala_lopp", "nadal", "kuu", "kaugemal")


def windows(today: date) -> dict[str, tuple[date, date | None]]:
    return {key: (starts, ends) for key, _label, starts, ends in dd.upcoming_windows(today)}


def holding(cut: dict[str, tuple[date, date | None]], day: date) -> list[str]:
    return [
        key
        for key, (starts, ends) in cut.items()
        if starts <= day and (ends is None or day <= ends)
    ]


# ---------------------------------------------------------------------------
# The partition, every day of four years
# ---------------------------------------------------------------------------


def test_six_windows_in_order_consecutive_and_exhaustive_for_every_today():
    """Every `today` from 2026 to 2029 — two year ends, a leap year, every
    weekday in every month — and every day of the following year."""
    today = date(2026, 1, 1)
    while today <= date(2029, 12, 31):
        cut = dd.upcoming_windows(today)
        assert tuple(key for key, *_ in cut) == KEYS == dd.UPCOMING_WINDOWS
        # Consecutive: each starts the day after the previous one ends — an
        # empty window "ends" the day before it starts and moves nothing — and
        # the last is open.
        previous_end = today - timedelta(days=1)
        for key, _label, starts, ends in cut:
            assert starts == previous_end + timedelta(days=1), (today, key)
            if ends is not None:
                previous_end = max(previous_end, ends)
        assert cut[-1][3] is None
        by_key = {key: (starts, ends) for key, _label, starts, ends in cut}
        # Every day of the next five weeks and then a sample of the year after.
        for offset in [*range(40), *range(40, 400, 5)]:
            day = today + timedelta(days=offset)
            assert len(holding(by_key, day)) == 1, (today, day, holding(by_key, day))
        today += timedelta(days=1)


# ---------------------------------------------------------------------------
# By name
# ---------------------------------------------------------------------------


def _d(text: str) -> date:
    return date.fromisoformat(text)


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        pytest.param(
            "2026-10-26",  # Monday
            {
                "homme": ("2026-10-27", "2026-10-27"),
                "nadala_lopp": ("2026-10-28", "2026-11-01"),
                "nadal": ("2026-11-02", "2026-11-08"),
                "kuu": ("2026-11-09", "2026-11-30"),
                "kaugemal": ("2026-12-01", None),
            },
            id="monday",
        ),
        pytest.param(
            "2026-10-28",  # Wednesday: the rest of the week is Fri–Sun
            {
                "homme": ("2026-10-29", "2026-10-29"),
                "nadala_lopp": ("2026-10-30", "2026-11-01"),
                "nadal": ("2026-11-02", "2026-11-08"),
                "kuu": ("2026-11-09", "2026-11-30"),
                "kaugemal": ("2026-12-01", None),
            },
            id="wednesday",
        ),
        pytest.param(
            "2026-10-30",  # Friday: Sunday alone
            {
                "homme": ("2026-10-31", "2026-10-31"),
                "nadala_lopp": ("2026-11-01", "2026-11-01"),
                "nadal": ("2026-11-02", "2026-11-08"),
                "kuu": ("2026-11-09", "2026-11-30"),
                "kaugemal": ("2026-12-01", None),
            },
            id="friday",
        ),
        pytest.param(
            "2026-10-31",  # Saturday: tomorrow is Sunday, nothing is left of the week
            {
                "homme": ("2026-11-01", "2026-11-01"),
                "nadala_lopp": ("2026-11-02", "2026-11-01"),
                "nadal": ("2026-11-02", "2026-11-08"),
                "kuu": ("2026-11-09", "2026-11-30"),
                "kaugemal": ("2026-12-01", None),
            },
            id="saturday",
        ),
        pytest.param(
            "2026-11-01",  # Sunday: tomorrow is next week's Monday, and only Homme
            {
                "homme": ("2026-11-02", "2026-11-02"),
                "nadala_lopp": ("2026-11-03", "2026-11-01"),
                "nadal": ("2026-11-03", "2026-11-08"),
                "kuu": ("2026-11-09", "2026-11-30"),
                "kaugemal": ("2026-12-01", None),
            },
            id="sunday",
        ),
        pytest.param(
            "2026-12-23",  # next week crosses the year: the rest of January
            {
                "homme": ("2026-12-24", "2026-12-24"),
                "nadala_lopp": ("2026-12-25", "2026-12-27"),
                "nadal": ("2026-12-28", "2027-01-03"),
                "kuu": ("2027-01-04", "2027-01-31"),
                "kaugemal": ("2027-02-01", None),
            },
            id="year-end",
        ),
        pytest.param(
            "2026-12-31",  # Thursday, the last day of the year
            {
                "homme": ("2027-01-01", "2027-01-01"),
                "nadala_lopp": ("2027-01-02", "2027-01-03"),
                "nadal": ("2027-01-04", "2027-01-10"),
                "kuu": ("2027-01-11", "2027-01-31"),
                "kaugemal": ("2027-02-01", None),
            },
            id="new-years-eve",
        ),
        pytest.param(
            "2026-05-20",  # next week ends on 31.05: nothing is left of that month
            {
                "homme": ("2026-05-21", "2026-05-21"),
                "nadala_lopp": ("2026-05-22", "2026-05-24"),
                "nadal": ("2026-05-25", "2026-05-31"),
                "kuu": ("2026-06-01", "2026-05-31"),
                "kaugemal": ("2026-06-01", None),
            },
            id="next-week-ends-the-month",
        ),
        pytest.param(
            "2028-02-23",  # leap February: next week runs through 29.02 into March
            {
                "homme": ("2028-02-24", "2028-02-24"),
                "nadala_lopp": ("2028-02-25", "2028-02-27"),
                "nadal": ("2028-02-28", "2028-03-05"),
                "kuu": ("2028-03-06", "2028-03-31"),
                "kaugemal": ("2028-04-01", None),
            },
            id="leap-day-in-next-week",
        ),
        pytest.param(
            "2028-02-29",  # the leap day itself, a Tuesday
            {
                "homme": ("2028-03-01", "2028-03-01"),
                "nadala_lopp": ("2028-03-02", "2028-03-05"),
                "nadal": ("2028-03-06", "2028-03-12"),
                "kuu": ("2028-03-13", "2028-03-31"),
                "kaugemal": ("2028-04-01", None),
            },
            id="leap-day-today",
        ),
    ],
)
def test_the_windows_on_named_days(today, expected):
    cut = windows(_d(today))

    assert cut["tana"] == (_d(today), _d(today))
    for key, (starts, ends) in expected.items():
        assert cut[key] == (_d(starts), _d(ends) if ends else None), key


def test_homme_is_headed_by_its_weekday():
    labels = {key: label for key, label, _s, _e in dd.upcoming_windows(date(2026, 10, 28))}
    assert labels == {
        "tana": "Täna",
        "homme": "neljapäev",
        "nadala_lopp": "Käesoleva nädala ülejäänud päevad",
        "nadal": "Järgmine nädal",
        "kuu": "Ülejäänud kuu",
        "kaugemal": "Kaugemal",
    }


def test_ulejaanud_kuu_is_the_rest_of_the_month_next_week_ends_in():
    """Its meaning since docs/adr/0049 §5, not thirty days, and never inverted
    into the month after (2026-10-28: next week ends 08.11, so 09.11–30.11)."""
    for today in (date(2026, 10, 28) + timedelta(days=n) for n in range(120)):
        starts, ends = windows(today)["kuu"]
        next_week_end = windows(today)["nadal"][1]
        assert starts == next_week_end + timedelta(days=1)
        assert ends == end_of_month(next_week_end)


# ---------------------------------------------------------------------------
# Remembered state is keyed by the window's kind
# ---------------------------------------------------------------------------


def test_every_window_has_a_registered_semantic_key_with_the_approved_default():
    assert {key: dd.UPCOMING_DISCLOSURE_KEYS[key] for key in KEYS} == {
        "tana": "department.upcoming.today",
        "homme": "department.upcoming.tomorrow",
        "nadala_lopp": "department.upcoming.remaining_week",
        "nadal": "department.upcoming.next_week",
        "kuu": "department.upcoming.rest_month",
        "kaugemal": "department.upcoming.far",
    }
    defaults = {key: DISCLOSURES[dd.UPCOMING_DISCLOSURE_KEYS[key]].default_open for key in KEYS}
    assert defaults == {
        "tana": True,
        "homme": True,
        "nadala_lopp": True,
        "nadal": True,
        "kuu": False,
        "kaugemal": False,
    }


@pytest.mark.django_db
def test_a_saved_choice_survives_the_week_rolling_over(client, department_head, monkeypatch):
    """Folded *Järgmine nädal* on a Wednesday is still folded the next Monday,
    over a different week — the key is the window's kind, not its dates."""
    client.force_login(department_head)
    client.post("/konto/plokid/", {"key": "department.upcoming.next_week", "open": "0"})

    for today in (date(2026, 10, 28), date(2026, 11, 2)):
        monkeypatch.setattr(timezone, "localdate", lambda *a, _d=today, **k: _d)
        _deadline(department_head, windows(today)["nadal"][0], f"Järgmise nädala {today}")
        body = client.get("/osakond/").content.decode()
        assert (
            '<details class="uxdl" data-disclosure="department.upcoming.next_week" '
            'data-disclosure-saved="0">'
        ) in body, today


# ---------------------------------------------------------------------------
# The rows each window holds
# ---------------------------------------------------------------------------


def _deadline(owner, when, title, precision=DatePrecision.EXACT, matter=None):
    matter = matter or create_matter(title=title, owner=owner, reference_year=2026, actor=owner)
    set_next_action(
        matter=matter,
        text="Esita arvamus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=when,
        date_precision=precision,
        responsible=owner,
        actor=owner,
    )
    return matter


@pytest.mark.django_db
def test_every_eligible_deadline_is_in_exactly_one_window_for_many_todays(specialist):
    """A fixed set of deadlines, read on forty different days."""
    anchors = [date(2026, 10, 26) + timedelta(days=n * 3) for n in range(30)]
    for n, when in enumerate(anchors):
        _deadline(specialist, when, f"Tähtaeg {n}")
    _deadline(specialist, date(2026, 12, 1), "Detsembris", precision=DatePrecision.MONTH)
    _deadline(specialist, date(2027, 1, 1), "I kvartal", precision=DatePrecision.QUARTER)

    for today in (date(2026, 10, 24) + timedelta(days=n * 2) for n in range(40)):
        items = wi.work_items(specialist, today=today)
        eligible = sorted(
            (item.object_id for item in wi.real_deadlines(items) if not item.is_overdue), key=str
        )
        groups = dd.upcoming_groups(specialist, today, items=items)
        placed = [item.object_id for group in groups for item in group.items]
        assert sorted(placed, key=str) == eligible, today
        for group in groups:
            # An approximate date is never in a window with a last day.
            if group.ends is not None:
                assert all(not item.is_approximate for item in group.items), (today, group.key)


@pytest.mark.django_db
def test_two_deadlines_in_one_matter_are_two_rows_and_one_file(specialist):
    from app.intelligence.services import add_important_date

    today = date(2026, 10, 28)
    starts = windows(today)["nadal"][0]
    matter = _deadline(specialist, starts, "Kaks tähtaega")
    add_important_date(
        matter=matter,
        title="Teine tähtaeg",
        date_value=starts + timedelta(days=1),
        period_end=starts + timedelta(days=1),
        actor=specialist,
    )

    group = next(g for g in dd.upcoming_groups(specialist, today) if g.key == "nadal")

    assert (group.count, group.matter_count) == (2, 1)


@pytest.mark.django_db
@pytest.mark.parametrize("today", [date(2026, 10, 28), date(2026, 10, 31), date(2026, 11, 1)])
def test_each_windows_register_query_lists_exactly_its_files(specialist, today):
    """Count == register result, through the window's own `?too=tahtaeg-vahemik` query."""
    for n in range(0, 60, 2):
        _deadline(specialist, today + timedelta(days=n), f"Tähtaeg {n}")

    for group in dd.upcoming_groups(specialist, today):
        if group.is_empty_window:
            assert group.count == 0, group.key
            continue
        params = dict(parse_qsl(urlparse(group.url).query))
        listed = register_population(specialist, params, today=today)
        assert set(listed.values_list("pk", flat=True)) == {
            item.matter_id for item in group.items
        }, group.key


@pytest.mark.django_db
def test_today_is_the_tallinn_date(specialist, monkeypatch):
    """00:30 in Tallinn on 26.10 is still 25.10 in UTC; Eesolev's *Täna* is the 26th."""
    instant = datetime(2026, 10, 25, 22, 30, tzinfo=UTC)
    monkeypatch.setattr(timezone, "now", lambda: instant)
    _deadline(specialist, date(2026, 10, 26), "Tallinna täna")

    groups = {group.key: group for group in dd.upcoming_groups(specialist)}

    assert groups["tana"].starts == date(2026, 10, 26)
    assert [item.matter.title for item in groups["tana"].items] == ["Tallinna täna"]


@pytest.mark.django_db
def test_an_empty_window_is_not_drawn(client, department_head, monkeypatch):
    """Saturday: *Käesoleva nädala ülejäänud päevad* holds no days, so it has no heading."""
    saturday = date(2026, 10, 31)
    monkeypatch.setattr(timezone, "localdate", lambda *a, **k: saturday)
    _deadline(department_head, date(2026, 11, 1), "Pühapäeval")
    _deadline(department_head, date(2026, 11, 2), "Esmaspäeval")
    client.force_login(department_head)

    body = client.get("/osakond/").content.decode()
    panel = body.split('aria-label="Eesolev"')[-1].split("</section>")[0]

    assert "PÜHAPÄEV" in panel
    assert "JÄRGMINE NÄDAL" in panel
    assert "ÜLEJÄÄNUD NÄDAL" not in panel
    assert "department.upcoming.remaining_week" not in panel
