"""A record may be dated in the past, today or the future (docs/adr/0121 §3).

ENG-004 refused a `Kaasamine`, a `Väline seisukoht`, a published `Ülevaade` and
a `Märge` dated after today, because Teema käik then hid the row until its day.
The owner withdrew the refusal: the application records what happened, what is
happening and what is planned, and a person entering a date is not told that
tomorrow is not allowed. **Validity is separate from meaning**:

* every writer accepts a past, today's or a future date, at every precision the
  record supports, and a correction may move a date forward;
* an accepted row is on Teema käik at once — a row dated ahead of today is drawn
  and marked `Eesolev` (`ChronologyMilestone.ahead`), never hidden;
* a date ahead of today is still not *history*: it is not the file's last
  activity, and the invariant report does not call it a defect.

**One rule is kept:** a `Koja arvamus` recorded as *sent* may not be dated after
today (ENG-043). `SENT` asserts that the letter went out — it locks the evidence
and is counted in the sent-opinion statistics — so a send that has not happened
is a planned step, not a sent opinion.

Every date is fixed. Today is frozen at `TODAY` through `timezone.localdate`.
"""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.invariants import check_domain_invariants
from app.matters import services, workspace
from app.matters.activity import activity_of, annotate_last_activity
from app.matters.enums import EngagementKind
from app.matters.models import (
    Matter,
    MatterEngagement,
    MatterExternalPosition,
    MatterWebsiteOverview,
)
from app.matters.services import (
    engagement_revision_token,
    external_position_revision,
    website_overview_revision,
)
from app.matters.timeline import AHEAD_LABEL, projected_milestones
from app.organisations.models import Organisation
from app.submissions.services import SENT_DATE_IN_THE_FUTURE
from app.workflow.dates import format_estonian_date
from app.workflow.enums import DatePrecision

pytestmark = pytest.mark.django_db

#: A Thursday far from a month end, so «tomorrow» is ordinary.
TODAY = datetime.date(2026, 9, 24)
YESTERDAY = TODAY - datetime.timedelta(days=1)
TOMORROW = TODAY + datetime.timedelta(days=1)
#: `24.09.62` — two digits of year read as 2062. A valid date now, if an odd one.
FAR_AHEAD = datetime.date(2062, 9, 24)
DAYS = (YESTERDAY, TODAY, TOMORROW, FAR_AHEAD)

#: Past, current and future periods at the three broad precisions, as stored.
PERIODS = [
    ("past month", datetime.date(2026, 8, 1), DatePrecision.MONTH.value, False),
    ("current month", datetime.date(2026, 9, 1), DatePrecision.MONTH.value, False),
    ("future month", datetime.date(2026, 10, 1), DatePrecision.MONTH.value, True),
    ("past quarter", datetime.date(2026, 4, 1), DatePrecision.QUARTER.value, False),
    ("future quarter", datetime.date(2026, 10, 1), DatePrecision.QUARTER.value, True),
    ("past year", datetime.date(2025, 1, 1), DatePrecision.YEAR.value, False),
    ("current year", datetime.date(2026, 1, 1), DatePrecision.YEAR.value, False),
    ("future year", datetime.date(2027, 1, 1), DatePrecision.YEAR.value, True),
]

HX = {"headers": {"HX-Request": "true"}}
_REAL_LOCALDATE = timezone.localdate


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    """`timezone.localdate()` answers `TODAY`; converting a value still converts."""

    def frozen(value=None, timezone=None):
        if value is None:
            return TODAY
        return _REAL_LOCALDATE(value, timezone)

    monkeypatch.setattr("django.utils.timezone.localdate", frozen)
    return TODAY


@pytest.fixture
def ministry(db):
    return Organisation.objects.create(name="Kliimaministeerium")


def _day(value: datetime.date) -> str:
    return format_estonian_date(value)


def _row(matter, user, record):
    """The one Teema käik row this record draws, or ``None``."""
    rows = [
        item
        for item in projected_milestones(matter=matter, user=user, today=TODAY)
        if item.record == record
    ]
    assert len(rows) <= 1
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# Kaasamine
# ---------------------------------------------------------------------------


def _engagement(matter, actor, occurred_on, precision=DatePrecision.EXACT.value):
    return services.add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="liikmed",
        occurred_on=occurred_on,
        occurred_on_precision=precision,
        actor=actor,
    )


@pytest.mark.parametrize("day", DAYS)
def test_a_consultation_on_any_day_is_recorded_and_drawn(normal_matter, specialist, day):
    engagement = _engagement(normal_matter, specialist, day)

    assert engagement.occurred_on == day
    row = _row(normal_matter, specialist, engagement)
    assert row is not None
    assert row.milestone.ahead is (day > TODAY)


@pytest.mark.parametrize(("label", "anchor", "precision", "ahead"), PERIODS)
def test_a_consultation_period_on_either_side_of_today_is_recorded(
    normal_matter, specialist, label, anchor, precision, ahead
):
    engagement = _engagement(normal_matter, specialist, anchor, precision)

    assert (engagement.occurred_on, engagement.occurred_on_precision) == (anchor, precision)
    row = _row(normal_matter, specialist, engagement)
    assert row is not None, label
    assert row.milestone.ahead is ahead, label


def test_the_compatibility_door_accepts_tomorrow(normal_matter, specialist):
    engagement = services.record_engagement(
        matter=normal_matter,
        kind=EngagementKind.SURVEY,
        title="liikmed",
        occurred_on=TOMORROW,
        actor=specialist,
    )

    assert engagement.occurred_on == TOMORROW


def test_a_correction_may_move_a_consultation_forward(normal_matter, specialist):
    engagement = _engagement(normal_matter, specialist, YESTERDAY)

    services.correct_engagement(engagement=engagement, occurred_on=TOMORROW, actor=specialist)

    engagement.refresh_from_db()
    assert engagement.occurred_on == TOMORROW


def test_a_consultation_ahead_is_not_the_files_last_activity(normal_matter, specialist):
    """A plan is not activity: the round dated ahead does not move «viimati»."""
    _engagement(normal_matter, specialist, YESTERDAY)
    _engagement(normal_matter, specialist, TOMORROW)

    matter = annotate_last_activity(Matter.objects.filter(pk=normal_matter.pk), specialist)[0]
    fact = activity_of(matter)

    assert fact is not None
    assert fact.occurred_on <= TODAY


def _post_engagement(client, matter, **fields):
    payload = {"audience": "liikmed"}
    payload.update(fields)
    return client.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}), payload, **HX
    )


@pytest.mark.parametrize("day", DAYS)
def test_the_panel_records_any_day(signed_in, normal_matter, day):
    response = _post_engagement(signed_in, normal_matter, occurred_on=_day(day))

    assert response.status_code == 200
    assert MatterEngagement.objects.get(matter=normal_matter).occurred_on == day


def test_the_panel_draws_a_consultation_ahead_as_eesolev(signed_in, normal_matter):
    response = _post_engagement(signed_in, normal_matter, occurred_on=_day(TOMORROW))

    assert response.status_code == 200
    assert f'<span class="uxtl__msahead">{AHEAD_LABEL}</span>' in response.content.decode()


def _engagement_fields(engagement, **changes) -> dict[str, str]:
    payload = {
        "title": engagement.title,
        "response_count": "",
        "smaily_url": engagement.smaily_url,
        "alchemer_url": engagement.alchemer_url,
        "occurred_on": _day(engagement.occurred_on) if engagement.occurred_on else "",
        "feedback_deadline": "",
        "feedback_received": "",
        "revision": engagement_revision_token(engagement),
    }
    payload.update(changes)
    return payload


def test_the_correction_form_moves_a_consultation_forward(signed_in, normal_matter, specialist):
    engagement = _engagement(normal_matter, specialist, YESTERDAY)

    response = signed_in.post(
        reverse(
            "matters:update_engagement",
            kwargs={"pk": normal_matter.pk, "engagement_id": engagement.pk},
        ),
        _engagement_fields(engagement, occurred_on=_day(TOMORROW), title="uus pealkiri"),
        **HX,
    )

    assert response.status_code == 200
    engagement.refresh_from_db()
    assert engagement.occurred_on == TOMORROW
    assert engagement.title == "uus pealkiri"


# ---------------------------------------------------------------------------
# Väline seisukoht
# ---------------------------------------------------------------------------


def _position(matter, actor, organisation, stated_on, **kwargs):
    return workspace.add_matter_external_position(
        matter=matter,
        author=actor,
        organisation=organisation,
        summary="Toetab eelnõu.",
        stated_on=stated_on,
        **kwargs,
    ).record


@pytest.mark.parametrize("day", DAYS)
def test_a_position_on_any_day_is_recorded_and_drawn(normal_matter, specialist, ministry, day):
    position = _position(normal_matter, specialist, ministry, day)

    assert position.stated_on == day
    row = _row(normal_matter, specialist, position)
    assert row is not None
    assert row.milestone.ahead is (day > TODAY)


def test_a_correction_may_move_a_position_forward(normal_matter, specialist, ministry):
    position = _position(normal_matter, specialist, ministry, YESTERDAY)

    services.correct_external_position(
        position=position,
        organisation=ministry,
        url="",
        stated_on=TOMORROW,
        stated_on_precision=DatePrecision.EXACT.value,
        summary="Toetab eelnõu.",
        engagement=None,
        actor=specialist,
    )

    position.refresh_from_db()
    assert position.stated_on == TOMORROW


def test_the_panel_records_a_position_ahead(signed_in, normal_matter, ministry):
    response = signed_in.post(
        reverse("matters:add_external_position", kwargs={"pk": normal_matter.pk}),
        {
            "organisation": str(ministry.pk),
            "stated_on": _day(TOMORROW),
            "summary": "Ministeerium toetab muudatust.",
        },
        **HX,
    )

    assert response.status_code == 200
    assert MatterExternalPosition.objects.get(matter=normal_matter).stated_on == TOMORROW


def test_the_position_correction_form_moves_it_forward(
    signed_in, normal_matter, specialist, ministry
):
    position = _position(normal_matter, specialist, ministry, YESTERDAY)

    response = signed_in.post(
        reverse(
            "matters:update_external_position",
            kwargs={"pk": normal_matter.pk, "position_id": position.pk},
        ),
        {
            "organisation": str(ministry.pk),
            "summary": "Parandatud kokkuvõte.",
            "position_precision": "EXACT",
            "stated_on": _day(TOMORROW),
            "revision": external_position_revision(position),
        },
        **HX,
    )

    assert response.status_code == 200
    position.refresh_from_db()
    assert position.stated_on == TOMORROW


# ---------------------------------------------------------------------------
# Kodulehe ülevaade / uudis
# ---------------------------------------------------------------------------

PAGE = "https://www.koda.ee/uudised/eelnou"


def test_an_undated_publication_is_still_an_ordinary_publication(normal_matter, specialist):
    """docs/adr/0089 §8: an empty date is *unknown*."""
    overview = workspace.add_matter_website_overview(
        matter=normal_matter, author=specialist, url=PAGE, published_on=None
    ).record

    assert overview.is_published
    assert overview.published_on is None


@pytest.mark.parametrize("day", DAYS)
def test_a_publication_on_any_day_is_recorded_and_drawn(normal_matter, specialist, day):
    overview = workspace.add_matter_website_overview(
        matter=normal_matter, author=specialist, url=PAGE, published_on=day
    ).record

    assert overview.is_published
    assert overview.published_on == day
    row = _row(normal_matter, specialist, overview)
    assert row is not None
    assert row.milestone.ahead is (day > TODAY)


def test_publishing_a_plan_for_tomorrow_is_recorded(normal_matter, specialist):
    plan = services.plan_website_overview(matter=normal_matter, actor=specialist)

    services.publish_website_overview(
        overview=plan, url=PAGE, published_on=TOMORROW, actor=specialist
    )

    plan.refresh_from_db()
    assert plan.is_published
    assert plan.published_on == TOMORROW


def test_a_correction_may_move_a_publication_forward(normal_matter, specialist):
    overview = workspace.add_matter_website_overview(
        matter=normal_matter, author=specialist, url=PAGE, published_on=YESTERDAY
    ).record

    services.correct_website_overview_link(
        overview=overview, url=PAGE, published_on=FAR_AHEAD, actor=specialist
    )

    overview.refresh_from_db()
    assert overview.published_on == FAR_AHEAD


def test_the_panel_records_a_publication_ahead(signed_in, normal_matter):
    response = signed_in.post(
        reverse("matters:add_website_overview", kwargs={"pk": normal_matter.pk}),
        {"url": PAGE, "published_on": _day(TOMORROW)},
        **HX,
    )

    assert response.status_code == 200
    assert MatterWebsiteOverview.objects.get(matter=normal_matter).published_on == TOMORROW


def test_the_link_correction_form_moves_a_publication_forward(signed_in, normal_matter, specialist):
    overview = workspace.add_matter_website_overview(
        matter=normal_matter, author=specialist, url=PAGE, published_on=YESTERDAY
    ).record

    response = signed_in.post(
        reverse(
            "matters:correct_website_overview",
            kwargs={"pk": normal_matter.pk, "overview_id": overview.pk},
        ),
        {
            "url": PAGE,
            "published_on": _day(TOMORROW),
            "revision": website_overview_revision(overview),
        },
        **HX,
    )

    assert response.status_code == 200
    overview.refresh_from_db()
    assert overview.published_on == TOMORROW


# ---------------------------------------------------------------------------
# Nothing saved and then hidden; nothing ahead reported as a defect
# ---------------------------------------------------------------------------


def test_every_accepted_save_is_on_teema_kaik_at_once(normal_matter, specialist, ministry):
    """Any day, any of the three families: the row is there, and says whether
    it is ahead."""
    for day in DAYS:
        for write in (
            lambda d: _engagement(normal_matter, specialist, d),
            lambda d: _position(normal_matter, specialist, ministry, d),
            lambda d: (
                workspace.add_matter_website_overview(
                    matter=normal_matter,
                    author=specialist,
                    url=f"{PAGE}-{d.isoformat()}",
                    published_on=d,
                ).record
            ),
        ):
            record = write(day)
            row = _row(normal_matter, specialist, record)
            assert row is not None, (record, day)
            assert row.milestone.ahead is (day > TODAY), (record, day)


def test_the_invariant_report_does_not_call_a_planned_row_a_defect(
    normal_matter, specialist, ministry
):
    _engagement(normal_matter, specialist, FAR_AHEAD)
    _position(normal_matter, specialist, ministry, TOMORROW)
    workspace.add_matter_website_overview(
        matter=normal_matter, author=specialist, url=PAGE, published_on=FAR_AHEAD
    )

    report = check_domain_invariants(today=TODAY)

    assert report.ok, report.findings


def test_a_sent_koja_arvamus_still_may_not_be_dated_ahead():
    """The one rule kept, deliberately (docs/adr/0121 §3).

    `SENT` asserts the letter went out: it locks the evidence and is counted in
    the sent-opinion statistics. A send that has not happened is a planned step
    (`Järgmine tegevus`, `Oluline tähtaeg`), not a sent opinion. The refusals
    themselves are asserted in `tests/test_invariants_below_the_form.py`.
    """
    from tests import test_invariants_below_the_form as eng043

    assert SENT_DATE_IN_THE_FUTURE == "Saatmise kuupäev ei saa olla tulevikus."
    assert callable(eng043.test_correct_sent_opinion_refuses_moving_a_send_into_the_future)
