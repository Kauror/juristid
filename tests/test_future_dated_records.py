"""A timeline record dated in the future is refused, not saved and hidden (ENG-004).

Teema käik is the history of what has happened: `projected_milestones` leaves out
a `Kaasamine`, a `Väline seisukoht` or a published `Ülevaade` whose date has not
arrived. Before this, every writer of those three accepted a future date — the
save answered 200, the row left the page together with its only `Muuda` and
`Kustuta`, and `24.09.62` is 2062. `+ Märge` already refused (QA-07), and a
sent `Koja arvamus` has refused since ENG-043.

So the rule is the services', with the forms repeating it beside the date box:

* a **new** record dated after the Tallinn business day is refused;
* a **correction** is refused only when it *moves* the date into the future — a
  row stored with a future date before this rule existed stays correctable,
  including back to the day it really happened;
* a refusal writes no row, no audit event and no file.

Every date is fixed. Today is frozen at `TODAY` through `timezone.localdate`.
"""

from __future__ import annotations

import datetime

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.core.invariants import SERVICE_RULES, check_domain_invariants
from app.documents.models import Document
from app.matters import services, workspace
from app.matters.enums import EngagementKind
from app.matters.models import MatterEngagement, MatterExternalPosition, MatterWebsiteOverview
from app.matters.services import (
    ENGAGEMENT_CANNOT_BE_FUTURE,
    EXTERNAL_POSITION_CANNOT_BE_FUTURE,
    WEBSITE_OVERVIEW_PUBLISHED_IN_FUTURE,
    engagement_revision_token,
    external_position_revision,
    website_overview_revision,
)
from app.matters.timeline import projected_milestones
from app.organisations.models import Organisation
from app.workflow.dates import format_estonian_date
from app.workflow.enums import DatePrecision

pytestmark = pytest.mark.django_db

#: A Thursday far from a month end, so «tomorrow» is ordinary.
TODAY = datetime.date(2026, 9, 24)
YESTERDAY = TODAY - datetime.timedelta(days=1)
TOMORROW = TODAY + datetime.timedelta(days=1)
#: `24.09.62` — two digits of year read as 2062, the audit's example.
MISTYPED = datetime.date(2062, 9, 24)

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


def _counts(matter) -> tuple[int, int, int, int, int]:
    return (
        MatterEngagement.objects.filter(matter=matter).count(),
        MatterExternalPosition.objects.filter(matter=matter).count(),
        MatterWebsiteOverview.objects.filter(matter=matter).count(),
        ChangeEvent.objects.filter(matter=matter).count(),
        Document.objects.filter(matter=matter).count(),
    )


def _history(matter, user) -> list:
    return list(projected_milestones(matter=matter, user=user, today=TODAY))


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


@pytest.mark.parametrize("day", [TODAY, YESTERDAY])
def test_a_consultation_today_or_before_is_recorded_and_is_history(normal_matter, specialist, day):
    engagement = _engagement(normal_matter, specialist, day)

    assert engagement.occurred_on == day
    assert [item for item in _history(normal_matter, specialist) if item.record == engagement]


@pytest.mark.parametrize("day", [TOMORROW, MISTYPED])
def test_a_future_consultation_is_refused_and_writes_nothing(normal_matter, specialist, day):
    before = _counts(normal_matter)

    with pytest.raises(DomainError, match=ENGAGEMENT_CANNOT_BE_FUTURE):
        _engagement(normal_matter, specialist, day)

    assert _counts(normal_matter) == before


def test_a_period_covering_today_is_not_the_future(normal_matter, specialist):
    """*september 2026* read on the 24th has begun; *oktoober 2026* has not."""
    _engagement(normal_matter, specialist, datetime.date(2026, 9, 1), DatePrecision.MONTH.value)

    with pytest.raises(DomainError, match=ENGAGEMENT_CANNOT_BE_FUTURE):
        _engagement(
            normal_matter, specialist, datetime.date(2026, 10, 1), DatePrecision.MONTH.value
        )


def test_the_workspace_door_refuses_too(normal_matter, specialist):
    with pytest.raises(DomainError, match=ENGAGEMENT_CANNOT_BE_FUTURE):
        services.record_engagement(
            matter=normal_matter,
            kind=EngagementKind.SURVEY,
            title="liikmed",
            occurred_on=TOMORROW,
            actor=specialist,
        )


def test_a_correction_may_not_move_a_consultation_into_the_future(normal_matter, specialist):
    engagement = _engagement(normal_matter, specialist, YESTERDAY)
    events = ChangeEvent.objects.filter(matter=normal_matter).count()

    with pytest.raises(DomainError, match=ENGAGEMENT_CANNOT_BE_FUTURE):
        services.correct_engagement(engagement=engagement, occurred_on=MISTYPED, actor=specialist)

    engagement.refresh_from_db()
    assert engagement.occurred_on == YESTERDAY
    assert ChangeEvent.objects.filter(matter=normal_matter).count() == events


def test_an_old_future_consultation_stays_correctable(normal_matter, specialist):
    """A row stored before the rule: its title can change, and its date can come back."""
    engagement = _engagement(normal_matter, specialist, YESTERDAY)
    MatterEngagement.objects.filter(pk=engagement.pk).update(occurred_on=MISTYPED)
    engagement.refresh_from_db()

    services.correct_engagement(engagement=engagement, title="kõik liikmed", actor=specialist)
    engagement.refresh_from_db()
    assert engagement.title == "kõik liikmed"
    assert engagement.occurred_on == MISTYPED

    services.correct_engagement(engagement=engagement, occurred_on=TODAY, actor=specialist)
    engagement.refresh_from_db()
    assert engagement.occurred_on == TODAY
    assert [item for item in _history(normal_matter, specialist) if item.record == engagement]


def _post_engagement(client, matter, **fields):
    payload = {"kind": EngagementKind.SURVEY, "audience": "liikmed", "note": "Küsitlus läks välja."}
    payload.update(fields)
    return client.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}), payload, **HX
    )


def test_the_panel_refuses_a_future_consultation_beside_the_date(signed_in, normal_matter):
    response = _post_engagement(signed_in, normal_matter, occurred_on="24.09.62")

    assert response.status_code == 400
    body = response.content.decode()
    assert ENGAGEMENT_CANNOT_BE_FUTURE in body
    # What was typed is still on the form.
    assert "liikmed" in body
    assert not MatterEngagement.objects.filter(matter=normal_matter).exists()


def test_the_panel_records_today(signed_in, normal_matter):
    response = _post_engagement(signed_in, normal_matter, occurred_on=_day(TODAY))

    assert response.status_code == 200
    assert MatterEngagement.objects.get(matter=normal_matter).occurred_on == TODAY


def _engagement_fields(engagement, **changes) -> dict[str, str]:
    payload = {
        "kind": engagement.kind,
        "title": engagement.title,
        "response_count": "",
        "url": engagement.url,
        "smaily_url": engagement.smaily_url,
        "alchemer_url": engagement.alchemer_url,
        "note": engagement.note,
        "occurred_on": _day(engagement.occurred_on) if engagement.occurred_on else "",
        "feedback_deadline": "",
        "feedback_received": "",
        "revision": engagement_revision_token(engagement),
    }
    payload.update(changes)
    return payload


def _update_engagement_url(matter, engagement) -> str:
    return reverse(
        "matters:update_engagement", kwargs={"pk": matter.pk, "engagement_id": engagement.pk}
    )


def test_the_correction_form_refuses_moving_a_consultation_forward(
    signed_in, normal_matter, specialist
):
    engagement = _engagement(normal_matter, specialist, YESTERDAY)

    response = signed_in.post(
        _update_engagement_url(normal_matter, engagement),
        _engagement_fields(engagement, occurred_on=_day(TOMORROW), title="uus pealkiri"),
        **HX,
    )

    assert response.status_code == 400
    body = response.content.decode()
    assert ENGAGEMENT_CANNOT_BE_FUTURE in body
    assert "uus pealkiri" in body
    engagement.refresh_from_db()
    assert engagement.occurred_on == YESTERDAY
    assert engagement.title == "liikmed"


def test_the_correction_form_saves_an_old_future_row_it_does_not_move(
    signed_in, normal_matter, specialist
):
    engagement = _engagement(normal_matter, specialist, YESTERDAY)
    MatterEngagement.objects.filter(pk=engagement.pk).update(occurred_on=MISTYPED)
    engagement.refresh_from_db()

    response = signed_in.post(
        _update_engagement_url(normal_matter, engagement),
        _engagement_fields(engagement, title="parandatud"),
        **HX,
    )

    assert response.status_code == 200
    engagement.refresh_from_db()
    assert engagement.title == "parandatud"


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


@pytest.mark.parametrize("day", [TODAY, YESTERDAY])
def test_a_position_today_or_before_is_recorded_and_is_history(
    normal_matter, specialist, ministry, day
):
    position = _position(normal_matter, specialist, ministry, day)

    assert position.stated_on == day
    assert [item for item in _history(normal_matter, specialist) if item.record == position]


@pytest.mark.parametrize("day", [TOMORROW, MISTYPED])
def test_a_future_position_is_refused_with_its_file(
    normal_matter, specialist, ministry, evidence_root, day
):
    """No row, no event and no evidence: the refusal comes before any file is captured."""
    before = _counts(normal_matter)

    with pytest.raises(DomainError, match=EXTERNAL_POSITION_CANNOT_BE_FUTURE):
        _position(
            normal_matter,
            specialist,
            ministry,
            day,
            uploads=[SimpleUploadedFile("seisukoht.pdf", b"%PDF-1.4 x")],
        )

    assert _counts(normal_matter) == before


def test_a_correction_may_not_move_a_position_into_the_future(normal_matter, specialist, ministry):
    position = _position(normal_matter, specialist, ministry, YESTERDAY)

    with pytest.raises(DomainError, match=EXTERNAL_POSITION_CANNOT_BE_FUTURE):
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
    assert position.stated_on == YESTERDAY


def test_an_old_future_position_stays_correctable(normal_matter, specialist, ministry):
    position = _position(normal_matter, specialist, ministry, YESTERDAY)
    MatterExternalPosition.objects.filter(pk=position.pk).update(stated_on=MISTYPED)
    position.refresh_from_db()

    def correct(stated_on, summary):
        return services.correct_external_position(
            position=position,
            organisation=ministry,
            url="",
            stated_on=stated_on,
            stated_on_precision=DatePrecision.EXACT.value,
            summary=summary,
            engagement=None,
            actor=specialist,
        )

    correct(MISTYPED, "Toetab eelnõu osaliselt.")
    position.refresh_from_db()
    assert position.summary == "Toetab eelnõu osaliselt."

    correct(YESTERDAY, "Toetab eelnõu osaliselt.")
    position.refresh_from_db()
    assert position.stated_on == YESTERDAY
    assert [item for item in _history(normal_matter, specialist) if item.record == position]


def test_the_panel_refuses_a_future_position_and_keeps_the_text(signed_in, normal_matter, ministry):
    response = signed_in.post(
        reverse("matters:add_external_position", kwargs={"pk": normal_matter.pk}),
        {
            "organisation": str(ministry.pk),
            "stated_on": _day(MISTYPED),
            "summary": "Ministeerium toetab muudatust.",
        },
        **HX,
    )

    assert response.status_code == 400
    body = response.content.decode()
    assert EXTERNAL_POSITION_CANNOT_BE_FUTURE in body
    assert "Ministeerium toetab muudatust." in body
    assert not MatterExternalPosition.objects.filter(matter=normal_matter).exists()


def test_the_position_correction_form_refuses_moving_it_forward(
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

    assert response.status_code == 400
    assert EXTERNAL_POSITION_CANNOT_BE_FUTURE in response.content.decode()
    position.refresh_from_db()
    assert position.stated_on == YESTERDAY
    assert position.summary == "Toetab eelnõu."


# ---------------------------------------------------------------------------
# Kodulehe ülevaade / uudis
# ---------------------------------------------------------------------------

PAGE = "https://www.koda.ee/uudised/eelnou"


def test_an_undated_publication_is_still_an_ordinary_publication(normal_matter, specialist):
    """docs/adr/0089 §8: an empty date is *unknown*, never refused as the future."""
    overview = workspace.add_matter_website_overview(
        matter=normal_matter, author=specialist, url=PAGE, published_on=None
    ).record

    assert overview.is_published
    assert overview.published_on is None


@pytest.mark.parametrize("day", [TODAY, YESTERDAY])
def test_a_publication_today_or_before_is_recorded(normal_matter, specialist, day):
    overview = workspace.add_matter_website_overview(
        matter=normal_matter, author=specialist, url=PAGE, published_on=day
    ).record

    assert overview.is_published
    assert overview.published_on == day
    assert [item for item in _history(normal_matter, specialist) if item.record == overview]


@pytest.mark.parametrize("day", [TOMORROW, MISTYPED])
def test_a_future_publication_is_refused_and_leaves_no_plan_behind(normal_matter, specialist, day):
    """The add path plans then publishes, in one transaction — a refusal undoes both."""
    before = _counts(normal_matter)

    with pytest.raises(DomainError, match=WEBSITE_OVERVIEW_PUBLISHED_IN_FUTURE):
        workspace.add_matter_website_overview(
            matter=normal_matter, author=specialist, url=PAGE, published_on=day
        )

    assert _counts(normal_matter) == before


def test_publishing_a_plan_into_the_future_is_refused(normal_matter, specialist):
    plan = services.plan_website_overview(matter=normal_matter, actor=specialist)

    with pytest.raises(DomainError, match=WEBSITE_OVERVIEW_PUBLISHED_IN_FUTURE):
        services.publish_website_overview(
            overview=plan, url=PAGE, published_on=TOMORROW, actor=specialist
        )

    plan.refresh_from_db()
    assert plan.is_planned


def test_a_correction_may_not_move_a_publication_forward(normal_matter, specialist):
    overview = workspace.add_matter_website_overview(
        matter=normal_matter, author=specialist, url=PAGE, published_on=YESTERDAY
    ).record

    with pytest.raises(DomainError, match=WEBSITE_OVERVIEW_PUBLISHED_IN_FUTURE):
        services.correct_website_overview_link(
            overview=overview, url=PAGE, published_on=MISTYPED, actor=specialist
        )

    overview.refresh_from_db()
    assert overview.published_on == YESTERDAY


def test_an_old_future_publication_stays_correctable(normal_matter, specialist):
    overview = workspace.add_matter_website_overview(
        matter=normal_matter, author=specialist, url=PAGE, published_on=YESTERDAY
    ).record
    MatterWebsiteOverview.objects.filter(pk=overview.pk).update(published_on=MISTYPED)
    overview.refresh_from_db()

    services.correct_website_overview_link(
        overview=overview, url=f"{PAGE}-parandatud", published_on=MISTYPED, actor=specialist
    )
    overview.refresh_from_db()
    assert overview.url == f"{PAGE}-parandatud"

    services.correct_website_overview_link(
        overview=overview, url=overview.url, published_on=None, actor=specialist
    )
    overview.refresh_from_db()
    assert overview.published_on is None


def test_the_panel_refuses_a_future_publication(signed_in, normal_matter):
    response = signed_in.post(
        reverse("matters:add_website_overview", kwargs={"pk": normal_matter.pk}),
        {"url": PAGE, "published_on": _day(TOMORROW)},
        **HX,
    )

    assert response.status_code == 400
    body = response.content.decode()
    assert WEBSITE_OVERVIEW_PUBLISHED_IN_FUTURE in body
    assert PAGE in body
    assert not MatterWebsiteOverview.objects.filter(matter=normal_matter).exists()


def test_the_link_correction_form_judges_only_a_day_it_would_move(
    signed_in, normal_matter, specialist
):
    overview = workspace.add_matter_website_overview(
        matter=normal_matter, author=specialist, url=PAGE, published_on=YESTERDAY
    ).record
    url = reverse(
        "matters:correct_website_overview",
        kwargs={"pk": normal_matter.pk, "overview_id": overview.pk},
    )

    refused = signed_in.post(
        url,
        {
            "url": PAGE,
            "published_on": _day(TOMORROW),
            "revision": website_overview_revision(overview),
        },
        **HX,
    )
    assert refused.status_code == 400
    assert WEBSITE_OVERVIEW_PUBLISHED_IN_FUTURE in refused.content.decode()

    MatterWebsiteOverview.objects.filter(pk=overview.pk).update(published_on=MISTYPED)
    overview.refresh_from_db()
    kept = signed_in.post(
        url,
        {
            "url": f"{PAGE}-uus",
            "published_on": _day(MISTYPED),
            "revision": website_overview_revision(overview),
        },
        **HX,
    )
    assert kept.status_code == 200
    overview.refresh_from_db()
    assert overview.url == f"{PAGE}-uus"


# ---------------------------------------------------------------------------
# Nothing saved and then hidden; the rows written before are reported
# ---------------------------------------------------------------------------


def test_no_writer_can_leave_a_row_the_chronology_hides(normal_matter, specialist, ministry):
    """J. Every accepted save of the three families is on Teema käik at once."""
    for day in (TODAY, YESTERDAY, TOMORROW, MISTYPED):
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
            try:
                record = write(day)
            except DomainError:
                continue
            assert [item for item in _history(normal_matter, specialist) if item.record == record]


def test_the_invariant_report_names_rows_written_before_the_rule(
    normal_matter, specialist, ministry
):
    engagement = _engagement(normal_matter, specialist, YESTERDAY)
    position = _position(normal_matter, specialist, ministry, YESTERDAY)
    overview = workspace.add_matter_website_overview(
        matter=normal_matter, author=specialist, url=PAGE, published_on=YESTERDAY
    ).record
    assert check_domain_invariants(today=TODAY).ok

    MatterEngagement.objects.filter(pk=engagement.pk).update(occurred_on=MISTYPED)
    MatterExternalPosition.objects.filter(pk=position.pk).update(stated_on=TOMORROW)
    MatterWebsiteOverview.objects.filter(pk=overview.pk).update(published_on=MISTYPED)

    report = check_domain_invariants(today=TODAY)
    kinds = {finding.kind: finding.subject for finding in report.findings}
    assert kinds == {
        "engagement-in-future": str(engagement.pk),
        "external-position-in-future": str(position.pk),
        "website-overview-published-in-future": str(overview.pk),
    }
    # Service rules: reported, never blocking a migration.
    assert set(kinds) <= SERVICE_RULES
    assert not report.blocking


def test_the_koja_arvamus_case_was_already_closed_by_eng_043():
    """The fourth family in the audit card is not touched here, only pinned.

    `correct_sent_opinion` refuses moving a send into the future and lets a
    stored future send be corrected in its summary — both asserted in
    `tests/test_invariants_below_the_form.py` — so ENG-004 adds no second rule.
    """
    from tests import test_invariants_below_the_form as eng043

    assert callable(eng043.test_correct_sent_opinion_refuses_moving_a_send_into_the_future)
    assert callable(eng043.test_a_correction_that_keeps_the_stored_date_is_not_a_new_send_fact)
