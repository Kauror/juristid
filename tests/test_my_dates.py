"""Minu tähtajad and Minu jõustumised (app/matters/my_dates.py).

What is pinned here:

* the population is the Minu asjad work model for the signed-in person
  (`work_items.work_items(responsible=user)`) and active commencements on the
  person's own Teemad — nothing invented, nothing of anybody else's;
* self only: another person's rows never appear, and a department head opening
  the pages reads their own, not the department's;
* the buckets, the calendar placement (no day invented for a period), the
  relative wording, the filters and the empty states;
* the retired `/jalgimine/` addresses land on the new pages for a person and
  keep the register fallback for a reader with no persona.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.http import QueryDict
from django.urls import reverse
from django.utils import timezone

from app.intelligence.enums import EffectiveDateKind, FactStatus, ImportantDateKind
from app.matters import my_dates
from app.matters.my_dates import (
    BUCKET_LATER,
    BUCKET_NEXT_30,
    BUCKET_PAST,
    BUCKET_TODAY,
    BUCKET_WEEK,
)
from app.workflow.enums import ActionStatus, DatePrecision
from tests import factories

pytestmark = pytest.mark.django_db

DEADLINES = reverse("matters:my_deadlines")
EFFECTIVE = reverse("matters:my_effective_dates")
REGISTER = reverse("matters:matter_list")


def _q(**params: str) -> QueryDict:
    query = QueryDict(mutable=True)
    for key, value in params.items():
        query[key] = value
    return query


def _step(matter, person, day, text="Saada kommentaarid"):
    return factories.NextActionFactory(
        matter=matter, responsible=person, target_date=day, text=text
    )


def _milestone(matter, day, title="Komisjoni istung", **extra):
    return factories.ImportantDateFactory(
        matter=matter, title=title, date_value=day, period_end=extra.pop("period_end", day), **extra
    )


def _commence(matter, day=None, description="põhiosa", **extra):
    if day is None and "kind" not in extra:
        day = timezone.localdate()
    return factories.EffectiveDateFactory(
        matter=matter,
        date_value=day,
        period_end=extra.pop("period_end", day),
        description=description,
        **extra,
    )


# ---------------------------------------------------------------------------
# A. pure rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "offset,words",
    [
        (0, "täna"),
        (1, "homme"),
        (3, "3 päeva pärast"),
        (-1, "eile"),
        (-2, "2 päeva möödas"),
    ],
)
def test_relative_day_words(today, offset, words):
    assert my_dates.relative_day(today + dt.timedelta(days=offset), today) == words


def test_a_period_never_counts_days_to_a_day_nobody_named(today):
    november = (dt.date(2026, 11, 1), dt.date(2026, 11, 30))
    assert (
        my_dates.relative_period(*november, DatePrecision.MONTH, today)
        == "periood algab 4 päeva pärast"
    )
    october = (dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    assert my_dates.relative_period(*october, DatePrecision.MONTH, today) == "periood käimas"
    september = (dt.date(2026, 9, 1), dt.date(2026, 9, 30))
    assert my_dates.relative_period(*september, DatePrecision.MONTH, today) == "periood möödunud"


def test_buckets_are_disjoint_and_follow_the_iso_week(today):
    # 2026-10-28 is a Wednesday; the week ends on Sunday 1 November.
    assert my_dates.bucket_of(today - dt.timedelta(days=1), today) == BUCKET_PAST
    assert my_dates.bucket_of(today, today) == BUCKET_TODAY
    assert my_dates.bucket_of(dt.date(2026, 11, 1), today) == BUCKET_WEEK
    assert my_dates.bucket_of(dt.date(2026, 11, 2), today) == BUCKET_NEXT_30
    assert my_dates.bucket_of(today + dt.timedelta(days=30), today) == BUCKET_NEXT_30
    assert my_dates.bucket_of(today + dt.timedelta(days=31), today) == BUCKET_LATER


def test_month_and_day_parameters_fall_back_rather_than_error(today):
    assert my_dates.month_from("2027-03", today) == dt.date(2027, 3, 1)
    assert my_dates.month_from("2027-13", today) == dt.date(2026, 10, 1)
    assert my_dates.month_from("eile", today) == dt.date(2026, 10, 1)
    assert my_dates.day_from("2026-11-02") == dt.date(2026, 11, 2)
    assert my_dates.day_from("2.11.2026") is None
    assert my_dates.day_from("2026-02-30") is None


# ---------------------------------------------------------------------------
# B. Minu tähtajad — the population
# ---------------------------------------------------------------------------


def test_the_deadlines_are_the_minu_asjad_work_model_for_the_person(specialist, today):
    from app.matters import work_items as wi

    mine = factories.MatterFactory(owner=specialist, title="Minu teema")
    _step(mine, specialist, today + dt.timedelta(days=2))
    _milestone(mine, today + dt.timedelta(days=10))
    # A response deadline on a Teema with no open step: an open step
    # suppresses its file's response deadline in the work model.
    answering = factories.MatterFactory(owner=specialist, title="Arvamuse teema")
    answering.response_deadline = today + dt.timedelta(days=5)
    answering.save(update_fields=["response_deadline"])

    rows = my_dates.deadline_rows(specialist, today)
    expected = [
        (item.source_type, item.object_id)
        for item in wi.work_items(specialist, today=today, responsible=specialist)
        if item.when is not None
    ]
    assert [(row.item.source_type, row.item.object_id) for row in rows] == expected
    assert {row.category for row in rows} == {
        my_dates.CATEGORY_ACTION,
        my_dates.CATEGORY_IMPORTANT,
        my_dates.CATEGORY_RESPONSE,
    }


def test_cancelled_superseded_and_informational_milestones_are_not_deadlines(specialist, today):
    matter = factories.MatterFactory(owner=specialist)
    day = today + dt.timedelta(days=3)
    _milestone(matter, day, title="Tühistatud", status=FactStatus.CANCELLED)
    _milestone(matter, day, title="Asendatud", status=FactStatus.SUPERSEDED)
    _milestone(matter, day, title="Eeldatav", kind=ImportantDateKind.ESTIMATED_COMMENCEMENT)
    _milestone(matter, day, title="Päris")

    titles = [row.what for row in my_dates.deadline_rows(specialist, today)]
    assert titles == ["Päris"]


def test_closed_teemad_and_done_steps_are_not_deadlines(specialist, today):
    from app.workflow.enums import Disposition

    closed = factories.MatterFactory(
        owner=specialist,
        is_open=False,
        disposition=Disposition.COMPLETED,
        closed_at=timezone.now(),
    )
    _step(closed, specialist, today + dt.timedelta(days=1), text="Suletud teemal")
    open_matter = factories.MatterFactory(owner=specialist)
    done = _step(open_matter, specialist, today, text="Tühistatud samm")
    done.status = ActionStatus.CANCELLED
    done.save(update_fields=["status"])

    assert my_dates.deadline_rows(specialist, today) == []


def test_another_persons_deadlines_never_appear(specialist, other_specialist, today):
    theirs = factories.MatterFactory(owner=other_specialist, title="Kolleegi teema")
    _step(theirs, other_specialist, today + dt.timedelta(days=1), text="Kolleegi samm")
    _milestone(theirs, today + dt.timedelta(days=2), title="Kolleegi tähtaeg")

    assert my_dates.deadline_rows(specialist, today) == []


def test_a_step_i_answer_for_on_a_colleagues_teema_is_mine(specialist, other_specialist, today):
    """The Minu asjad rule: the step's responsible person, not the Teema's owner."""
    theirs = factories.MatterFactory(owner=other_specialist)
    _step(theirs, specialist, today + dt.timedelta(days=1), text="Minu samm kolleegi teemal")
    _milestone(theirs, today + dt.timedelta(days=2), title="Kolleegi tähtaeg")

    assert [row.what for row in my_dates.deadline_rows(specialist, today)] == [
        "Minu samm kolleegi teemal"
    ]


def test_the_current_step_column_is_the_teemas_open_step(specialist, today):
    matter = factories.MatterFactory(owner=specialist)
    _step(matter, specialist, today + dt.timedelta(days=4), text="Kirjuta arvamus")
    _milestone(matter, today + dt.timedelta(days=9), title="Avalikustamine")

    rows = my_dates.deadline_rows(specialist, today)
    assert {row.current_step for row in rows} == {"Kirjuta arvamus"}


def test_each_row_edits_through_the_existing_form(specialist, today):
    matter = factories.MatterFactory(owner=specialist)
    step = _step(matter, specialist, today + dt.timedelta(days=1))
    milestone = _milestone(matter, today + dt.timedelta(days=2))

    by_source = {row.item.source_type: row for row in my_dates.deadline_rows(specialist, today)}
    matter_url = reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    assert by_source["NEXT_ACTION"].edit_url == f"{matter_url}#lisa-jargmine"
    assert by_source["IMPORTANT_DEADLINE"].edit_url == reverse(
        "intelligence:edit_important_date",
        kwargs={"matter_id": matter.pk, "pk": milestone.pk},
    )
    assert step.pk == by_source["NEXT_ACTION"].item.object_id


# ---------------------------------------------------------------------------
# C. Minu tähtajad — the page
# ---------------------------------------------------------------------------


def _page(user, today, **params):
    return my_dates.build_my_deadlines(user, _q(**params), DEADLINES, today)


def test_count_cards_count_every_bucket_and_filter_the_table(specialist, today):
    matter = factories.MatterFactory(owner=specialist)
    _milestone(matter, today - dt.timedelta(days=2), title="Möödas")
    _milestone(matter, today, title="Täna")
    _milestone(matter, dt.date(2026, 10, 31), title="Laupäeval")
    _milestone(matter, today + dt.timedelta(days=12), title="Kahe nädala pärast")
    _milestone(matter, today + dt.timedelta(days=60), title="Hiljem")

    page = _page(specialist, today)
    assert [(card.key, card.count) for card in page.cards] == [
        (BUCKET_PAST, 1),
        (BUCKET_TODAY, 1),
        (BUCKET_WEEK, 1),
        (BUCKET_NEXT_30, 1),
        (BUCKET_LATER, 1),
    ]
    assert page.cards[0].is_alert
    assert "aeg=moodunud" in page.cards[0].url

    filtered = _page(specialist, today, aeg=BUCKET_PAST)
    assert [row.what for row in filtered.rows] == ["Möödas"]
    assert filtered.cards[0].is_active
    # Pressing the active card again clears it.
    assert "aeg=" not in filtered.cards[0].url


def test_search_category_and_sort(specialist, today):
    matter = factories.MatterFactory(owner=specialist, title="Toiduseadus")
    other = factories.MatterFactory(owner=specialist, title="Maksuseadus")
    _step(matter, specialist, today + dt.timedelta(days=1), text="Helista ministeeriumi")
    _milestone(other, today + dt.timedelta(days=5), title="Istung")

    assert [row.matter.title for row in _page(specialist, today, q="toidu").rows] == ["Toiduseadus"]
    assert [row.what for row in _page(specialist, today, liik="oluline").rows] == ["Istung"]
    assert [row.what for row in _page(specialist, today, liik="vale").rows] == [
        "Helista ministeeriumi",
        "Istung",
    ]
    assert [row.what for row in _page(specialist, today, jarjestus="hilisemad").rows] == [
        "Istung",
        "Helista ministeeriumi",
    ]


def test_calendar_places_days_and_lists_periods_beside_the_grid(specialist, today):
    matter = factories.MatterFactory(owner=specialist, title="Kalendriteema")
    _milestone(matter, dt.date(2026, 10, 20), title="Möödunud päev")
    _milestone(matter, dt.date(2026, 10, 30), title="Tulev päev")
    _milestone(
        matter,
        dt.date(2026, 10, 1),
        title="Oktoobri jooksul",
        period_end=dt.date(2026, 10, 31),
        date_precision=DatePrecision.MONTH,
    )

    page = _page(specialist, today)
    cells = {cell.day: cell for week in page.calendar.weeks for cell in week}
    assert cells[dt.date(2026, 10, 20)].count == 1
    assert cells[dt.date(2026, 10, 20)].is_past
    assert cells[dt.date(2026, 10, 30)].count == 1
    assert not cells[dt.date(2026, 10, 30)].is_past
    assert cells[dt.date(2026, 10, 1)].count == 0  # no day invented for a month
    assert [row.what for row in page.calendar.period_rows] == ["Oktoobri jooksul"]
    assert cells[today].is_today and cells[today].is_selected
    # Monday first.
    assert page.calendar.weeks[0][0].day.weekday() == 0


def test_the_day_panel_shows_the_chosen_days_rows(specialist, today):
    matter = factories.MatterFactory(owner=specialist)
    _milestone(matter, dt.date(2026, 11, 3), title="Kolmandal")

    page = _page(specialist, today, paev="2026-11-03", kuu="2026-11")
    assert page.selected_day == dt.date(2026, 11, 3)
    assert [row.what for row in page.day_rows] == ["Kolmandal"]
    assert page.day_rows[0].relative == "6 päeva pärast"
    # Another month with no day named opens on its first busy day.
    assert _page(specialist, today, kuu="2026-11").selected_day == dt.date(2026, 11, 3)


def test_the_deadlines_page_renders_for_the_signed_in_person(client, specialist):
    matter = factories.MatterFactory(owner=specialist, title="Nähtav teema")
    today = timezone.localdate()
    _milestone(matter, today + dt.timedelta(days=3), title="Minu oluline tähtaeg")
    client.force_login(specialist)

    response = client.get(DEADLINES)
    body = response.content.decode()
    assert response.status_code == 200
    assert "Minu oluline tähtaeg" in body
    assert "3 päeva pärast" in body
    assert 'aria-current="page">Minu tähtajad' in body
    assert reverse("matters:my_statistics") in body
    assert "css/my-dates.css" in body


def test_the_empty_states(client, specialist):
    client.force_login(specialist)
    assert "Sul ei ole tulevasi tähtaegu" in client.get(DEADLINES).content.decode()
    assert "Sul ei ole tulevasi jõustumisi" in client.get(EFFECTIVE).content.decode()


def test_anonymous_is_sent_to_sign_in(client):
    for url in (DEADLINES, EFFECTIVE):
        response = client.get(url)
        assert response.status_code == 302
        assert f"next={url}" in response["Location"]


def test_the_pages_are_get_only(client, specialist):
    client.force_login(specialist)
    for url in (DEADLINES, EFFECTIVE):
        assert client.post(url).status_code == 405


# ---------------------------------------------------------------------------
# D. self only — no IDOR, no manager view
# ---------------------------------------------------------------------------


def test_a_department_head_reads_only_their_own_dates(client, department_head, specialist):
    today = timezone.localdate()
    theirs = factories.MatterFactory(owner=specialist, title="Spetsialisti teema")
    _milestone(theirs, today + dt.timedelta(days=2), title="Spetsialisti tähtaeg")
    _commence(theirs, today + dt.timedelta(days=4), description="Spetsialisti jõustumine")
    own = factories.MatterFactory(owner=department_head, title="Juhi teema")
    _milestone(own, today + dt.timedelta(days=2), title="Juhi tähtaeg")
    client.force_login(department_head)

    deadlines = client.get(DEADLINES).content.decode()
    effective = client.get(EFFECTIVE).content.decode()
    assert "Juhi tähtaeg" in deadlines
    assert "Spetsialisti" not in deadlines
    assert "Spetsialisti" not in effective


def test_no_parameter_selects_another_person(client, specialist, other_specialist):
    today = timezone.localdate()
    theirs = factories.MatterFactory(owner=other_specialist, title="Võõras teema")
    _milestone(theirs, today + dt.timedelta(days=2), title="Võõras tähtaeg")
    _commence(theirs, today + dt.timedelta(days=2), description="Võõras jõustumine")
    client.force_login(specialist)

    for url in (DEADLINES, EFFECTIVE):
        for params in (
            {"isik": str(other_specialist.pk)},
            {"pk": str(other_specialist.pk)},
            {"periood": "koik", "aeg": "", "q": "Võõras"},
        ):
            body = client.get(url, params).content.decode()
            assert "Võõras" not in body.replace('value="Võõras"', ""), (url, params)


# ---------------------------------------------------------------------------
# E. Minu jõustumised
# ---------------------------------------------------------------------------


def _effective(user, today, **params):
    return my_dates.build_my_effective_dates(user, _q(**params), EFFECTIVE, today)


def test_commencements_on_my_teemad_open_and_closed(specialist, other_specialist, today):
    from app.workflow.enums import Disposition

    open_matter = factories.MatterFactory(owner=specialist, title="Avatud")
    closed = factories.MatterFactory(
        owner=specialist,
        title="Suletud",
        is_open=False,
        disposition=Disposition.COMPLETED,
        closed_at=timezone.now(),
    )
    theirs = factories.MatterFactory(owner=other_specialist, title="Kolleegi")
    _commence(open_matter, today + dt.timedelta(days=5), description="avatud teemal")
    _commence(closed, today + dt.timedelta(days=40), description="suletud teemal")
    _commence(theirs, today + dt.timedelta(days=5), description="kolleegi teemal")
    _commence(open_matter, today, description="tühistatud", status=FactStatus.CANCELLED)

    rows = my_dates.effective_rows(specialist, today)
    assert [row.what for row in rows] == ["avatud teemal", "suletud teemal"]


def test_certainty_badges_use_the_models_labels(specialist, today):
    matter = factories.MatterFactory(owner=specialist)
    _commence(matter, today + dt.timedelta(days=3), description="täpne")
    _commence(
        matter,
        dt.date(2027, 1, 1),
        period_end=dt.date(2027, 3, 31),
        date_precision=DatePrecision.QUARTER,
        description="kvartal",
    )
    _commence(matter, None, kind=EffectiveDateKind.GENERAL_ORDER, description="üldine")
    _commence(matter, None, kind=EffectiveDateKind.UNKNOWN, description="teadmata")

    badges = {row.what: row.certainty_label for row in my_dates.effective_rows(specialist, today)}
    assert badges == {
        "täpne": "Teadaolev kuupäev",
        "kvartal": "Kvartali täpsusega",
        "üldine": "Jõustub üldises korras",
        "teadmata": "Kuupäev täpsustamisel",
    }


def test_periods_next_three_and_the_30_day_headline(specialist, today):
    matter = factories.MatterFactory(owner=specialist)
    _commence(matter, today - dt.timedelta(days=10), description="möödunud")
    _commence(matter, today + dt.timedelta(days=2), description="kohe")
    _commence(matter, today + dt.timedelta(days=20), description="kuu sees")
    _commence(matter, today + dt.timedelta(days=45), description="hiljem")
    _commence(matter, today + dt.timedelta(days=90), description="veel hiljem")
    _commence(matter, None, kind=EffectiveDateKind.UNKNOWN, description="teadmata")

    page = _effective(specialist, today)
    assert page.next_30_count == 2
    assert page.next_30_label == "jõustumist"
    assert "periood=30-paeva" in page.next_30_url
    assert [row.what for row in page.upcoming] == ["kohe", "kuu sees", "hiljem"]
    # Default: Tulevased — the future and the undated.
    assert [row.what for row in page.rows] == [
        "kohe",
        "kuu sees",
        "hiljem",
        "veel hiljem",
        "teadmata",
    ]
    assert [row.what for row in _effective(specialist, today, periood="30-paeva").rows] == [
        "kohe",
        "kuu sees",
    ]
    assert [row.what for row in _effective(specialist, today, periood="moodunud").rows] == [
        "möödunud"
    ]
    assert len(_effective(specialist, today, periood="koik").rows) == 6
    assert [
        row.what for row in _effective(specialist, today, periood="koik", olek="tapsustamisel").rows
    ] == ["teadmata"]
    assert [row.what for row in _effective(specialist, today, q="KUU").rows] == ["kuu sees"]


def test_a_past_commencement_is_never_called_commenced(client, specialist):
    matter = factories.MatterFactory(owner=specialist, title="Möödunud jõustumine")
    _commence(matter, timezone.localdate() - dt.timedelta(days=3), description="põhiosa")
    client.force_login(specialist)

    body = client.get(EFFECTIVE, {"periood": "moodunud"}).content.decode()
    assert "kirjas olev kuupäev on möödunud" in body
    assert "jõustunud" not in body.lower()


def test_the_effective_dates_page_renders_with_edit_links(client, specialist):
    matter = factories.MatterFactory(owner=specialist, title="Jõustuv teema")
    record = _commence(matter, timezone.localdate() + dt.timedelta(days=7), description="osa A")
    client.force_login(specialist)

    response = client.get(EFFECTIVE)
    body = response.content.decode()
    assert response.status_code == 200
    assert "osa A" in body
    assert "Järgmise 30 päeva jooksul" in body
    assert 'aria-current="page">Minu jõustumised' in body
    assert (
        reverse(
            "intelligence:edit_effective_date", kwargs={"matter_id": matter.pk, "pk": record.pk}
        )
        in body
    )


def test_a_reader_sees_no_edit_link(client, reader):
    matter = factories.MatterFactory(owner=reader, title="Lugeja teema")
    record = _commence(matter, timezone.localdate() + dt.timedelta(days=7))
    client.force_login(reader)

    body = client.get(EFFECTIVE).content.decode()
    assert (
        reverse(
            "intelligence:edit_effective_date", kwargs={"matter_id": matter.pk, "pk": record.pk}
        )
        not in body
    )


def test_the_query_count_does_not_grow_with_rows(client, specialist, django_assert_max_num_queries):
    today = timezone.localdate()
    for index in range(15):
        matter = factories.MatterFactory(owner=specialist, title=f"Teema {index}")
        _step(matter, specialist, today + dt.timedelta(days=index))
        _milestone(matter, today + dt.timedelta(days=index + 1))
        _commence(matter, today + dt.timedelta(days=index + 2))
    client.force_login(specialist)

    with django_assert_max_num_queries(40):
        assert client.get(DEADLINES).status_code == 200
    with django_assert_max_num_queries(40):
        assert client.get(EFFECTIVE).status_code == 200


# ---------------------------------------------------------------------------
# F. the retired addresses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,route",
    [
        ("/jalgimine/tahtajad/", "matters:my_deadlines"),
        ("/olulised-tahtajad/", "matters:my_deadlines"),
        ("/jalgimine/joustumised/", "matters:my_effective_dates"),
        ("/joustuvad-aktid/", "matters:my_effective_dates"),
    ],
)
def test_a_retired_date_page_lands_on_the_persons_own_page(client, specialist, path, route):
    client.force_login(specialist)
    response = client.get(path)
    assert response.status_code == 302
    assert response["Location"] == reverse(route)
    assert client.get(response["Location"]).status_code == 200
