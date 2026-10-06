"""Running work over weeks, on a controlled clock (historical-regression round, stage IV).

The fixes of this round were found by entering five years of history in one
sitting. These scenarios check them in the shape real work has: one act a day,
the clock moving between them, and every surface asked after each act. The
clock is the test's own (`timezone.now` / `timezone.localdate` patched); no
stored timestamp is rewritten.

Scenario A — one request, answered:
    arrival with a deadline → overview published → two rounds, one with a
    deadline → an answer and a file reach the first round → the opinion answers
    the deadline and ends the first round only. No `Tööplaan` is seeded or
    touched (docs/adr/0141).

Scenario B — the next request, then lateness, then a decision:
    a new request after the answer → still owed although an opinion exists →
    late when its day passes → declined → not owed; the history keeps both.

Scenario C — closure and reopening:
    closed with a deadline → not owed, settled in the header → reopened without
    carrying it → nothing old reactivates.
"""

from __future__ import annotations

import datetime as dt
import zoneinfo

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.documents.links import DocumentLink
from app.matters import work_items as wi
from app.matters.enums import ResponseDeadlineOutcome
from app.matters.models import Matter, MatterEngagement, MatterResponseDeadline
from app.matters.response_deadlines import deadline_revision
from app.matters.selectors import response_deadline_of
from app.matters.services import (
    close_matter,
    engagement_revision_token,
    record_external_position,
)
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.workflow.enums import Disposition
from app.workflow.models import MatterPlanStep, StageVocabulary
from tests import factories

pytestmark = pytest.mark.django_db

TALLINN = zoneinfo.ZoneInfo("Europe/Tallinn")
START = dt.date(2026, 3, 2)


class Clock:
    """The test's own calendar: `timezone.now` and `localdate` read from here."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.day = START
        monkeypatch.setattr(timezone, "now", self.now)
        monkeypatch.setattr(timezone, "localdate", self.localdate)

    def now(self) -> dt.datetime:
        return dt.datetime.combine(self.day, dt.time(10, 0), tzinfo=TALLINN)

    def localdate(self, value=None, timezone=None) -> dt.date:
        if value is not None:
            return value.astimezone(TALLINN).date()
        return self.day

    def go(self, days: int) -> dt.date:
        self.day = START + dt.timedelta(days=days)
        return self.day


@pytest.fixture
def clock(monkeypatch):
    return Clock(monkeypatch)


def _et(day: dt.date) -> str:
    return f"{day.day}.{day.month}.{day.year}"


def _pdf(name: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"%PDF-1.4\n%%EOF", content_type="application/pdf")


def _owed(user) -> set:
    return set(wi.response_obligations(user).values_list("pk", flat=True))


def _page(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _new_matter(client, title: str, deadline: dt.date) -> Matter:
    client.post(
        reverse("matters:matter_create"),
        {"title": title, "response_deadline": _et(deadline)},
    )
    return Matter.objects.get(title=title)


def _opinion(client, matter, organisation, **fields):
    payload = {
        "upload": _pdf("koja-arvamus.pdf"),
        "recipients": [str(organisation.pk)],
        "sent_on": _et(timezone.localdate()),
        "summary": "Sünteetiline arvamus.",
    }
    payload.update(fields)
    return client.post(
        reverse("matters:add_koda_opinion", kwargs={"pk": matter.pk}),
        payload,
        headers={"HX-Request": "true"},
    )


def test_scenario_a_one_request_answered_over_two_weeks(signed_in, specialist, clock):
    ministry = factories.OrganisationFactory(name="Sünteetiline Ministeerium A")
    member = factories.OrganisationFactory(name="Sünteetiline Liit A")

    # Day 0 — the request arrives with a deadline two weeks out.
    matter = _new_matter(signed_in, "Jooksev töö A (sünteetiline)", START + dt.timedelta(days=14))
    assert matter.response_requested_at is not None
    assert matter.pk in _owed(specialist)
    assert not MatterPlanStep.objects.filter(matter=matter).exists()

    # Day 1 — the write-up is published.
    clock.go(1)
    signed_in.post(
        reverse("matters:add_website_overview", kwargs={"pk": matter.pk}),
        {
            "url": "https://www.koda.ee/et/sunteetiline-ulevaade-a",
            "published_on": _et(timezone.localdate()),
            "overview_title": "Sünteetiline ülevaade jooksva töö kohta",
        },
        headers={"HX-Request": "true"},
    )
    assert matter.website_overviews.filter(status="PUBLISHED").count() == 1

    # Day 2 — two rounds: one with a reply-by day, one without.
    clock.go(2)
    for audience, reply_by in (("Liikmed A", _et(START + dt.timedelta(days=7))), ("Töörühm A", "")):
        signed_in.post(
            reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}),
            {
                "audience": audience,
                "occurred_on": _et(timezone.localdate()),
                "feedback_deadline": reply_by,
            },
            headers={"HX-Request": "true"},
        )
    members = MatterEngagement.objects.get(matter=matter, title="Liikmed A")
    group = MatterEngagement.objects.get(matter=matter, title="Töörühm A")
    assert members.feedback_closed_at is None and group.feedback_closed_at is None

    # Day 5 — an answer and a file reach the members' round; it stays open.
    clock.go(5)
    record_external_position(
        matter=matter,
        organisation=member,
        stated_on=timezone.localdate(),
        summary="Toetame, kui üleminekuaeg pikeneb.",
        engagement=members,
        actor=specialist,
    )
    signed_in.post(
        reverse(
            "matters:add_engagement_evidence",
            kwargs={"pk": matter.pk, "engagement_id": members.pk},
        ),
        {"attachments": _pdf("liikmete-kusitlus.pdf")},
        headers={"HX-Request": "true"},
    )
    members.refresh_from_db()
    assert members.feedback_closed_at is None
    assert DocumentLink.objects.filter(engagement=members).count() == 1
    assert "Seotud seisukohti 1" in _page(signed_in, matter)

    # Day 9 — the opinion goes out: answers the deadline and ends the members'
    # round only.
    clock.go(9)
    members.refresh_from_db()
    matter.refresh_from_db()
    _opinion(
        signed_in,
        matter,
        ministry,
        vastab_tahtajale=deadline_revision(matter),
        lopeta_kaasamine=[f"{members.pk}:{engagement_revision_token(members)}"],
    )
    matter.refresh_from_db()
    members.refresh_from_db()
    group.refresh_from_db()
    opinion = Submission.objects.get(matter=matter)
    assert opinion.status == SubmissionStatus.SENT
    assert matter.response_deadline is None
    assert matter.pk not in _owed(specialist)
    (answered,) = MatterResponseDeadline.objects.filter(matter=matter)
    assert answered.outcome == ResponseDeadlineOutcome.ANSWERED
    assert answered.submission == opinion
    assert members.feedback_closed_at is not None
    assert group.feedback_closed_at is None

    # The register no longer draws a deadline for it; search finds the write-up.
    register = signed_in.get(reverse("matters:matter_list")).content.decode()
    assert "dateline--deadline" not in register.split("Jooksev töö A")[1][:1500]
    found = signed_in.get(reverse("search:search"), {"q": "Sünteetiline ülevaade jooksva"})
    assert "Jooksev töö A" in found.content.decode()


def test_scenario_b_a_new_request_is_not_answered_by_the_old_opinion(signed_in, specialist, clock):
    ministry = factories.OrganisationFactory(name="Sünteetiline Ministeerium B")
    matter = _new_matter(signed_in, "Jooksev töö B (sünteetiline)", START + dt.timedelta(days=10))

    clock.go(8)
    _opinion(signed_in, matter, ministry, vastab_tahtajale=deadline_revision(matter))
    matter.refresh_from_db()
    assert matter.response_deadline is None

    # Day 20 — the committee asks again, by day 30.
    clock.go(20)
    signed_in.post(
        reverse("matters:response_deadline", kwargs={"pk": matter.pk}),
        {
            "tegevus": "muuda",
            "revision": deadline_revision(matter),
            "response_deadline": _et(START + dt.timedelta(days=30)),
        },
        headers={"HX-Request": "true"},
    )
    matter.refresh_from_db()
    assert matter.response_deadline == START + dt.timedelta(days=30)
    assert matter.pk in _owed(specialist), "an opinion for the old request answered the new one"

    # Day 31 — the day passed with no answer: late, everywhere.
    clock.go(31)
    header = response_deadline_of(matter, specialist)
    assert header.is_overdue and not header.settled
    assert matter.pk in _owed(specialist)

    # Day 32 — the department decides not to answer.
    clock.go(32)
    signed_in.post(
        reverse("matters:response_deadline", kwargs={"pk": matter.pk}),
        {
            "tegevus": "lopeta",
            "revision": deadline_revision(matter),
            "previous_outcome": ResponseDeadlineOutcome.NOT_ANSWERING,
            "previous_note": "Komisjonile ei vasta (sünteetiline otsus).",
        },
        headers={"HX-Request": "true"},
    )
    matter.refresh_from_db()
    assert matter.response_deadline is None
    assert matter.pk not in _owed(specialist)
    outcomes = list(
        MatterResponseDeadline.objects.filter(matter=matter)
        .order_by("ended_at")
        .values_list("outcome", flat=True)
    )
    assert outcomes == [ResponseDeadlineOutcome.ANSWERED, ResponseDeadlineOutcome.NOT_ANSWERING]
    page = _page(signed_in, matter)
    assert 'id="arvamuse-tahtajad"' in page
    # Statistics count only what was sent.
    assert Submission.objects.filter(matter=matter, status=SubmissionStatus.SENT).count() == 1


def test_scenario_c_closure_and_reopening_reactivate_nothing(signed_in, specialist, clock):
    matter = _new_matter(signed_in, "Jooksev töö C (sünteetiline)", START + dt.timedelta(days=40))

    clock.go(5)
    close_matter(matter=matter, disposition=Disposition.MONITORING_STOPPED, actor=specialist)
    matter.refresh_from_db()
    assert matter.pk not in _owed(specialist)
    assert response_deadline_of(matter, specialist).settled == "teema suletud"
    page = _page(signed_in, matter)
    assert 'id="tooplaan"' not in page
    assert "Soovitatud järgmisena" not in page

    clock.go(50)
    signed_in.post(
        reverse("matters:reopen", kwargs={"pk": matter.pk}),
        {"stage": str(StageVocabulary.objects.get(key="consultation").pk)},
    )
    matter.refresh_from_db()
    assert matter.is_open
    assert matter.response_deadline is None
    assert matter.pk not in _owed(specialist)
    (ended,) = MatterResponseDeadline.objects.filter(matter=matter)
    assert ended.outcome == ResponseDeadlineOutcome.CLOSED
    assert not MatterPlanStep.objects.filter(matter=matter).exists()
