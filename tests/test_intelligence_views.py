"""The generated pages and the Matter-page sections, through the real views.

These assert the things a selector test cannot: that the number in the heading
is the number of rows below it, that a filter link leads to the population it
promises, that an approximate period is rendered as a period, and that the write
forms accept what a browser actually posts.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.intelligence.enums import EffectiveDateKind, FactStatus, WorkVictoryStatus
from app.intelligence.services import (
    add_effective_date,
    add_important_date,
    add_work_victory_candidate,
    confirm_work_victory,
)
from app.workflow.dates import quarter_bounds, year_bounds
from app.workflow.enums import DatePrecision
from tests import factories

pytestmark = pytest.mark.django_db

IMPORTANT_DATES = "intelligence:important_dates"
EFFECTIVE_DATES = "intelligence:effective_dates"
WORK_VICTORIES = "intelligence:work_victories"


def _text(response) -> str:
    return response.content.decode()


# -- Olulised tähtajad ------------------------------------------------------


def test_the_calendar_renders_an_approximate_period_as_a_period(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    start, end = quarter_bounds(2030, 2)
    add_important_date(
        matter=matter,
        title="Eeldatav VTK kooskõlastusring",
        date_value=start,
        period_end=end,
        date_precision=DatePrecision.QUARTER,
        actor=specialist,
    )

    body = _text(signed_in.get(reverse(IMPORTANT_DATES), {"suund": "koik"}))
    assert "II kvartal 2030" in body
    # Both spellings: the application writes `1.4.2030` now, and a test that
    # only refused the padded form would stop catching the invented day
    # (app/core/dates.py).
    assert "01.04.2030" not in body
    assert "1.4.2030" not in body


def test_the_calendar_labels_a_commencement_as_one(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    add_effective_date(
        matter=matter,
        description="alkoholiregister kaotatakse",
        date_value=date(2030, 11, 1),
        period_end=date(2030, 11, 1),
        actor=specialist,
    )

    body = _text(signed_in.get(reverse(IMPORTANT_DATES), {"suund": "koik"}))
    assert "Jõustumine" in body
    assert "alkoholiregister kaotatakse" in body


def test_the_source_selector_narrows_the_calendar(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    add_important_date(
        matter=matter,
        title="Ainult tähtaeg",
        date_value=date(2030, 5, 1),
        period_end=date(2030, 5, 1),
        actor=specialist,
    )
    add_effective_date(
        matter=matter,
        description="Ainult jõustumine",
        date_value=date(2030, 6, 1),
        period_end=date(2030, 6, 1),
        actor=specialist,
    )

    only_dates = _text(
        signed_in.get(reverse(IMPORTANT_DATES), {"suund": "koik", "allikad": "tahtajad"})
    )
    assert "Ainult tähtaeg" in only_dates
    assert "Ainult jõustumine" not in only_dates

    only_commencements = _text(
        signed_in.get(reverse(IMPORTANT_DATES), {"suund": "koik", "allikad": "joustumised"})
    )
    assert "Ainult jõustumine" in only_commencements
    assert "Ainult tähtaeg" not in only_commencements


def test_upcoming_and_past_split_on_the_end_of_the_period(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    today = timezone.localdate()
    add_important_date(
        matter=matter,
        title="Tulevik",
        date_value=today + timedelta(days=5),
        period_end=today + timedelta(days=5),
        actor=specialist,
    )
    add_important_date(
        matter=matter,
        title="Minevik",
        date_value=today - timedelta(days=5),
        period_end=today - timedelta(days=5),
        actor=specialist,
    )

    upcoming = _text(signed_in.get(reverse(IMPORTANT_DATES), {"suund": "tulevased"}))
    assert "Tulevik" in upcoming
    assert "Minevik" not in upcoming

    past = _text(signed_in.get(reverse(IMPORTANT_DATES), {"suund": "moodunud"}))
    assert "Minevik" in past
    assert "Tulevik" not in past


def test_the_heading_count_matches_the_rows(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    for index in range(3):
        add_important_date(
            matter=matter,
            title=f"Tähtaeg {index}",
            date_value=date(2030, 5, index + 1),
            period_end=date(2030, 5, index + 1),
            actor=specialist,
        )

    response = signed_in.get(reverse(IMPORTANT_DATES), {"suund": "koik"})
    body = _text(response)
    assert response.context["total"] == 3
    # The v2 page draws named sections over a register table rather than one
    # month-grouped list, so the rows are `<tr>`. The rule the test exists for
    # is unchanged: the number in the heading is the number of rows behind it,
    # including the ones the «Näita veel» disclosure holds — those are a second
    # `tbody` of the same table, not a second query
    # (01-EHITUSJUHIS §3.3, 02-EKRAANID §D).
    sections = response.context["sections"]
    assert sum(section.count for section in sections) == 3
    assert len(re.findall(r'class="table__titlelink"', body)) == 3


def test_a_hand_edited_year_parameter_does_not_break_the_page(signed_in):
    response = signed_in.get(reverse(IMPORTANT_DATES), {"aasta": "eelmine"})
    assert response.status_code == 200
    assert response.context["total"] == 0


def test_the_empty_state_explains_where_the_records_come_from(signed_in):
    body = _text(signed_in.get(reverse(IMPORTANT_DATES)))
    assert "Seda loendit ei peeta käsitsi" in body


# -- Jõustuvad aktid --------------------------------------------------------


def test_the_commencement_page_groups_by_period(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    add_effective_date(
        matter=matter,
        description="põhiosa",
        date_value=date(2030, 9, 27),
        period_end=date(2030, 9, 27),
        actor=specialist,
    )
    add_effective_date(
        matter=matter,
        description="osad sätted",
        date_value=date(2030, 10, 1),
        period_end=date(2030, 10, 1),
        actor=specialist,
    )

    # Month headings are gone: the v2 page names two sections by what a reader
    # is asking — what lands soon, and what lands later — and prints each row's
    # own date at the precision it was recorded to (02-EKRAANID §D). What has
    # to stay true is that both records reach the page and neither date is
    # rewritten.
    response = signed_in.get(reverse(EFFECTIVE_DATES), {"suund": "koik"})
    body = _text(response)
    rows = [row for section in response.context["sections"] for row in section.rows]
    assert {row.effective_date.description for row in rows} == {"põhiosa", "osad sätted"}
    assert "27.9.2030" in body
    assert "1.10.2030" in body


def test_the_undated_view_is_reachable_and_counted_separately(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    add_effective_date(
        matter=matter,
        kind=EffectiveDateKind.GENERAL_ORDER,
        description="rakendusmäärus",
        actor=specialist,
    )

    listed = _text(signed_in.get(reverse(EFFECTIVE_DATES), {"suund": "tapsustamisel"}))
    assert "Jõustub üldises korras" in listed
    assert "rakendusmäärus" in listed

    dated = signed_in.get(reverse(EFFECTIVE_DATES))
    assert dated.context["undated_count"] == 1
    assert "kuupäev on täpsustamisel" in _text(dated)


def test_a_source_url_is_offered_beside_the_matter_and_not_instead_of_it(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    add_effective_date(
        matter=matter,
        description="põhiosa",
        date_value=date(2030, 9, 27),
        period_end=date(2030, 9, 27),
        source_url="https://www.riigiteataja.ee/akt/000000000",
        actor=specialist,
    )

    body = _text(signed_in.get(reverse(EFFECTIVE_DATES), {"suund": "koik"}))
    assert reverse("matters:matter_detail", kwargs={"pk": matter.pk}) in body
    assert "Ametlik allikas" in body


# -- Töövõidud --------------------------------------------------------------


def test_the_work_victory_filters_agree_with_their_rows(signed_in, specialist, department_head):
    matter = factories.MatterFactory(owner=specialist)
    start, end = year_bounds(2026)
    confirmed = add_work_victory_candidate(
        matter=matter,
        title="Kinnitatud võit",
        period_date=start,
        period_end=end,
        date_precision=DatePrecision.YEAR,
        actor=specialist,
    )
    confirm_work_victory(record=confirmed, actor=department_head)
    add_work_victory_candidate(
        matter=matter,
        title="Kandidaat",
        period_date=start,
        period_end=end,
        date_precision=DatePrecision.YEAR,
        actor=specialist,
    )
    add_work_victory_candidate(matter=matter, title="Teadmata ajaga", actor=specialist)

    response = signed_in.get(
        reverse(WORK_VICTORIES), {"aasta": "2026", "staatus": WorkVictoryStatus.CONFIRMED}
    )
    body = _text(response)
    assert response.context["total"] == 1
    assert "Kinnitatud võit" in body
    assert "Kandidaat" not in body
    assert "Teadmata ajaga" not in body

    unknown = signed_in.get(reverse(WORK_VICTORIES), {"aasta": "teadmata"})
    assert unknown.context["total"] == 1
    assert "Teadmata ajaga" in _text(unknown)


def test_the_unknown_period_option_only_appears_when_there_is_one(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    start, end = year_bounds(2026)
    add_work_victory_candidate(
        matter=matter,
        title="Dateeritud",
        period_date=start,
        period_end=end,
        date_precision=DatePrecision.YEAR,
        actor=specialist,
    )

    keys = [
        option["key"] for option in signed_in.get(reverse(WORK_VICTORIES)).context["year_options"]
    ]
    assert "teadmata" not in keys

    add_work_victory_candidate(matter=matter, title="Teadmata", actor=specialist)
    keys = [
        option["key"] for option in signed_in.get(reverse(WORK_VICTORIES)).context["year_options"]
    ]
    assert "teadmata" in keys


def test_the_page_publishes_no_rate_or_ranking(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    add_work_victory_candidate(matter=matter, title="Kandidaat", actor=specialist)

    response = signed_in.get(reverse(WORK_VICTORIES))
    body = _text(response)
    for forbidden in ("edukus", "edetabel", "protsent", "tulemuslikkus"):
        assert forbidden not in body.lower()
    # And nothing in the context is a ratio waiting for a template to render it.
    assert "rate" not in response.context
    assert "share" not in response.context


# -- the Matter page --------------------------------------------------------


def test_an_empty_section_does_not_render_at_all(signed_in, specialist):
    """The redesign's rule: no permanently visible empty sections.

    These three headings, each over a sentence reporting an absence and an add
    button, used to occupy about forty per cent of a new Matter's page. What
    replaces them is one quiet row of add affordances, and `Oluline tähtaeg` is
    not even in that — the composer offers it beside the note it belongs to
    (Teema redesign §3, §24).
    """
    matter = factories.MatterFactory(owner=specialist)
    body = _text(signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})))

    assert 'id="olulised-tahtajad"' not in body
    assert 'id="joustumine"' not in body
    assert 'id="toovoidud"' not in body
    assert "Olulisi tähtaegu pole lisatud." not in body
    assert "Jõustumise infot pole lisatud." not in body
    assert "Töövõite ega kandidaate pole lisatud." not in body
    # The one quiet row that replaces all three.
    assert "+ Jõustumine" in body
    assert "+ Töövõit" in body


def test_a_populated_section_still_renders(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    add_important_date(
        matter=matter,
        title="Kooskõlastusringi lõpp",
        date_value=timezone.localdate() + timedelta(days=10),
        period_end=timezone.localdate() + timedelta(days=10),
        actor=specialist,
    )
    body = _text(signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})))

    assert 'id="olulised-tahtajad"' in body
    assert "Kooskõlastusringi lõpp" in body
    # The two that are still empty stay away.
    assert 'id="joustumine"' not in body
    assert 'id="toovoidud"' not in body


def test_the_matter_page_marks_a_cancelled_milestone(signed_in, specialist):
    from app.intelligence.services import cancel_important_date

    matter = factories.MatterFactory(owner=specialist)
    record = add_important_date(
        matter=matter,
        title="Ärajäänud ring",
        date_value=timezone.localdate() + timedelta(days=10),
        period_end=timezone.localdate() + timedelta(days=10),
        actor=specialist,
    )
    cancel_important_date(record=record, actor=specialist)

    body = _text(signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})))
    assert "Ärajäänud ring" in body
    assert "Tühistatud" in body


def test_a_reader_sees_the_facts_and_none_of_the_controls(client, specialist):
    matter = factories.MatterFactory(owner=specialist)
    add_important_date(
        matter=matter,
        title="Nähtav tähtaeg",
        date_value=date(2030, 5, 1),
        period_end=date(2030, 5, 1),
        actor=specialist,
    )
    reader = factories.UserFactory(role="READER")
    client.force_login(reader)

    body = _text(client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})))
    assert "Nähtav tähtaeg" in body
    assert "+ Lisa oluline tähtaeg" not in body
    assert "+ Lisa töövõit" not in body


def test_a_specialist_sees_no_confirmation_control(signed_in, specialist):
    """Adding is theirs; adjudicating somebody else's candidate is not.

    The two are different acts on purpose. A specialist may state a victory
    they know about, and still not be the person who decides the fate of a
    proposal a machine or an import produced.
    """
    matter = factories.MatterFactory(owner=specialist)
    add_work_victory_candidate(matter=matter, title="Kandidaat", actor=specialist)

    body = _text(signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})))
    assert "+ Lisa töövõit" in body
    assert "Kinnita töövõiduks" not in body


def test_adding_a_victory_from_the_matter_page_confirms_it(signed_in, specialist):
    """One click, one truthful row — no waiting to be approved by somebody.

    The person filling the form has already made the judgement the review step
    exists to make, and they did it on a Matter they may write to.
    """
    matter = factories.MatterFactory(owner=specialist)
    response = signed_in.post(
        reverse("intelligence:add_work_victory", kwargs={"matter_id": matter.pk}),
        {"title": "Ettepanek võeti arvesse", "precision": "YEAR", "year": "2026"},
    )

    assert response.status_code == 302
    record = matter.work_victories.get()
    assert record.status == WorkVictoryStatus.CONFIRMED
    assert record.created_by == specialist
    assert record.confirmed_by == specialist
    assert record.confirmed_at is not None

    events = ChangeEvent.objects.filter(object_id=record.pk)
    assert [event.event_type for event in events] == [ChangeEventType.WORK_VICTORY_CONFIRMED]
    assert events.get().payload["origin"] == "MANUAL"


def test_the_add_form_never_calls_the_saved_row_a_candidate(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    body = _text(
        signed_in.get(reverse("intelligence:add_work_victory", kwargs={"matter_id": matter.pk}))
    )
    assert "Lisa töövõit" in body
    assert "kandidaadina" not in body.casefold()


def test_a_reader_still_cannot_add_a_victory(client, specialist):
    """The gate is the ordinary business-write permission, not a new one."""
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(factories.UserFactory(role="READER"))

    response = client.post(
        reverse("intelligence:add_work_victory", kwargs={"matter_id": matter.pk}),
        {"title": "Ei tohiks salvestuda", "precision": "YEAR", "year": "2026"},
    )
    # 404, not the 403 this module used to answer. Business-write refusals
    # are one answer across the application now, and it is the one that
    # tells a reader nothing about what exists for somebody else
    # (app/core/decorators.py, AUTH-002).
    assert response.status_code == 404
    assert matter.work_victories.count() == 0


def test_adding_a_victory_grants_no_right_over_somebody_elses_candidate(
    signed_in, specialist, department_head
):
    """The manual door must not quietly widen the review role."""
    matter = factories.MatterFactory(owner=specialist)
    signed_in.post(
        reverse("intelligence:add_work_victory", kwargs={"matter_id": matter.pk}),
        {"title": "Minu võit", "precision": "YEAR", "year": "2026"},
    )
    candidate = add_work_victory_candidate(
        matter=matter, title="Masina pakkumine", actor=department_head
    )

    refused = signed_in.post(
        reverse(
            "intelligence:confirm_work_victory",
            kwargs={"matter_id": matter.pk, "pk": candidate.pk},
        )
    )
    assert refused.status_code == 403
    candidate.refresh_from_db()
    assert candidate.status == WorkVictoryStatus.CANDIDATE


def test_the_department_head_sees_the_confirmation_control(client, specialist, department_head):
    matter = factories.MatterFactory(owner=specialist)
    add_work_victory_candidate(matter=matter, title="Kandidaat", actor=specialist)
    client.force_login(department_head)

    body = _text(client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})))
    assert "Kinnita töövõiduks" in body


# -- the write forms --------------------------------------------------------


def test_a_quarter_posted_from_the_form_stores_a_quarter(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    response = signed_in.post(
        reverse("intelligence:add_important_date", kwargs={"matter_id": matter.pk}),
        {"title": "Kooskõlastusring", "precision": "QUARTER", "quarter": "2", "year": "2027"},
    )

    assert response.status_code == 302
    record = matter.important_dates.get()
    assert record.date_precision == DatePrecision.QUARTER
    assert (record.date_value, record.period_end) == (date(2027, 4, 1), date(2027, 6, 30))
    assert record.display_date == "II kvartal 2027"


def test_a_quarter_with_no_year_is_refused_on_the_field_that_is_missing(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    response = signed_in.post(
        reverse("intelligence:add_important_date", kwargs={"matter_id": matter.pk}),
        {"title": "Kooskõlastusring", "precision": "QUARTER", "quarter": "2"},
    )

    assert response.status_code == 400
    assert matter.important_dates.count() == 0
    assert "Aasta on puudu." in _text(response)


def test_a_general_order_commencement_refuses_an_offered_date(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    response = signed_in.post(
        reverse("intelligence:add_effective_date", kwargs={"matter_id": matter.pk}),
        {
            "kind": EffectiveDateKind.GENERAL_ORDER,
            "precision": "EXACT",
            "exact_date": "2026-01-01",
            "description": "rakendusmäärus",
        },
    )

    assert response.status_code == 400
    assert matter.effective_dates.count() == 0
    assert "ainult teadaoleva jõustumise" in _text(response)


def test_a_general_order_commencement_saves_with_no_date(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    response = signed_in.post(
        reverse("intelligence:add_effective_date", kwargs={"matter_id": matter.pk}),
        {"kind": EffectiveDateKind.GENERAL_ORDER, "description": "rakendusmäärus"},
    )

    assert response.status_code == 302
    record = matter.effective_dates.get()
    assert record.date_value is None
    assert record.display_when == "Jõustub üldises korras"


def test_a_work_victory_form_saves_a_confirmed_victory_with_its_period(signed_in, specialist):
    """The period is entered; the status is what the person's act was."""
    matter = factories.MatterFactory(owner=specialist)
    signed_in.post(
        reverse("intelligence:add_work_victory", kwargs={"matter_id": matter.pk}),
        {"title": "Ettepanek arvestati", "precision": "YEAR", "year": "2026"},
    )

    record = matter.work_victories.get()
    assert record.status == WorkVictoryStatus.CONFIRMED
    assert record.display_period == "2026"


def test_a_work_victory_may_be_saved_with_no_period(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    signed_in.post(
        reverse("intelligence:add_work_victory", kwargs={"matter_id": matter.pk}),
        {"title": "Millalgi varem", "precision": ""},
    )

    record = matter.work_victories.get()
    assert record.period_date is None


def test_the_edit_form_reopens_a_quarter_as_a_quarter(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    start, end = quarter_bounds(2027, 3)
    record = add_important_date(
        matter=matter,
        title="Kooskõlastusring",
        date_value=start,
        period_end=end,
        date_precision=DatePrecision.QUARTER,
        actor=specialist,
    )

    response = signed_in.get(
        reverse(
            "intelligence:edit_important_date",
            kwargs={"matter_id": matter.pk, "pk": record.pk},
        )
    )
    initial = response.context["form"].initial
    assert initial["precision"] == DatePrecision.QUARTER
    assert initial["quarter"] == "3"
    assert initial["year"] == 2027
    assert "exact_date" not in initial


def test_cancelling_through_the_form_keeps_the_record(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    record = add_important_date(
        matter=matter,
        title="Ärajäänud",
        date_value=date(2030, 5, 1),
        period_end=date(2030, 5, 1),
        actor=specialist,
    )

    response = signed_in.post(
        reverse(
            "intelligence:cancel_important_date",
            kwargs={"matter_id": matter.pk, "pk": record.pk},
        ),
        {"reason": "Ministeerium loobus"},
    )

    assert response.status_code == 302
    record.refresh_from_db()
    assert record.status == FactStatus.CANCELLED


def test_the_confirmation_page_states_what_is_being_claimed(client, specialist, department_head):
    matter = factories.MatterFactory(owner=specialist)
    record = add_work_victory_candidate(
        matter=matter, title="Ettepanek arvestati", actor=specialist
    )
    client.force_login(department_head)

    body = _text(
        client.get(
            reverse(
                "intelligence:confirm_work_victory",
                kwargs={"matter_id": matter.pk, "pk": record.pk},
            )
        )
    )
    assert "Ettepanek arvestati" in body
    assert "Teadmata periood" in body
    assert "Kinnita töövõiduks" in body
