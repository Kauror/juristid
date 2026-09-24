"""A correction records what moved, and a removed page gives back its address.

ENG-024 — `Muuda` on a recorded `Koja arvamus`. Pressing Salvesta on an
unchanged form wrote `SUBMISSION_CORRECTED` and `SUBMISSION_RECIPIENTS_CHANGED`
(the service compared `sent_at` as two ISO spellings of one instant), replaced
an exact TIMESTAMP send with local midnight at DATE precision, deleted and
re-created every recipient row — emptying the importers' KELLELE note — and
accepted a `Kokkuvõte` longer than recording the send allows. ADR 0103 already
said a correction that moved nothing writes nothing.

ENG-025 — `Ülevaade / uudis`. Removing a published row kept its address
reserved by the one uniqueness rule on the table, so recording the same page
again on that Matter — ADR 0102's repair for a removal made in error — was a
permanent 500, and so was every real duplicate.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.matters.enums import WebsiteOverviewStatus
from app.matters.models import EXTERNAL_POSITION_SUMMARY_MAX_LENGTH, MatterWebsiteOverview
from app.matters.removal import remove_matter_record
from app.matters.services import (
    correct_website_overview_link,
    plan_website_overview,
    website_overview_revision,
)
from app.submissions.enums import SentAtPrecision
from app.submissions.models import SubmissionRecipient
from app.submissions.services import correct_sent_opinion, sent_opinion_revision
from tests import factories
from tests.test_admin_is_not_a_back_door import _form
from tests.test_correction_round_polish import _sent

pytestmark = pytest.mark.django_db

HX = {"HTTP_HX_REQUEST": "true"}
EXACT = dt.datetime(2026, 5, 14, 16, 59, 13, tzinfo=dt.UTC)  # 19:59:13 in Tallinn
IMPORTED_NOTE = "KELLELE: Näidisministeerium (register rida 42)"


# -- ENG-024 ------------------------------------------------------------------------


@pytest.fixture
def exact_send(normal_matter, specialist, organisation, capture_evidence):
    """A send recorded to the second — what `Märgi saadetuks` writes — with an imported note."""
    submission = _sent(normal_matter, specialist, organisation, capture_evidence, when=EXACT)
    type(submission).objects.filter(pk=submission.pk).update(
        sent_at_precision=SentAtPrecision.TIMESTAMP
    )
    SubmissionRecipient.objects.filter(submission=submission).update(note=IMPORTED_NOTE)
    submission.refresh_from_db()
    return submission


def _url(submission) -> str:
    return reverse(
        "matters:update_sent_opinion",
        kwargs={"pk": submission.matter_id, "submission_id": submission.pk},
    )


def _as_opened(client, submission) -> dict:
    """The correction form exactly as the page renders it."""
    return {
        key: value[0] if len(value) == 1 else value
        for key, value in _form(client, _url(submission)).items()
    }


def _events(matter, since: int) -> list[str]:
    return list(
        ChangeEvent.objects.filter(matter=matter)
        .order_by("occurred_at")
        .values_list("event_type", flat=True)[since:]
    )


def test_an_unchanged_save_writes_no_correction(signed_in, exact_send):
    before = ChangeEvent.objects.filter(matter=exact_send.matter).count()
    row = SubmissionRecipient.objects.get(submission=exact_send)

    response = signed_in.post(_url(exact_send), _as_opened(signed_in, exact_send), **HX)

    assert response.status_code == 200
    assert _events(exact_send.matter, before) == []
    exact_send.refresh_from_db()
    assert exact_send.sent_at == EXACT
    assert exact_send.sent_at_precision == SentAtPrecision.TIMESTAMP
    same = SubmissionRecipient.objects.get(submission=exact_send)
    assert (same.pk, same.created_at, same.note) == (row.pk, row.created_at, IMPORTED_NOTE)


def test_an_unrelated_correction_keeps_the_exact_send_time(signed_in, exact_send):
    before = ChangeEvent.objects.filter(matter=exact_send.matter).count()

    signed_in.post(
        _url(exact_send), {**_as_opened(signed_in, exact_send), "summary": "Parandatud."}, **HX
    )

    exact_send.refresh_from_db()
    assert exact_send.summary == "Parandatud."
    assert exact_send.sent_at == EXACT
    assert exact_send.sent_at_precision == SentAtPrecision.TIMESTAMP
    assert _events(exact_send.matter, before) == [ChangeEventType.SUBMISSION_CORRECTED]
    event = ChangeEvent.objects.filter(matter=exact_send.matter).latest("occurred_at")
    assert event.payload == {
        "from": {"summary": "Toetame eelnõu."},
        "to": {"summary": "Parandatud."},
    }
    assert SubmissionRecipient.objects.get(submission=exact_send).note == IMPORTED_NOTE


def test_a_real_date_correction_is_recorded(signed_in, exact_send):
    before = ChangeEvent.objects.filter(matter=exact_send.matter).count()

    signed_in.post(
        _url(exact_send), {**_as_opened(signed_in, exact_send), "sent_on": "20.5.2026"}, **HX
    )

    exact_send.refresh_from_db()
    assert exact_send.sent_at_precision == SentAtPrecision.DATE
    assert exact_send.sent_at.date() in {dt.date(2026, 5, 19), dt.date(2026, 5, 20)}
    assert _events(exact_send.matter, before) == [ChangeEventType.SUBMISSION_CORRECTED]


def test_a_real_recipient_correction_is_recorded_once_and_keeps_who_stayed(
    signed_in, exact_send, organisation
):
    other = factories.OrganisationFactory()
    kept = SubmissionRecipient.objects.get(submission=exact_send)
    before = ChangeEvent.objects.filter(matter=exact_send.matter).count()

    signed_in.post(
        _url(exact_send),
        {**_as_opened(signed_in, exact_send), "recipients": [str(organisation.pk), str(other.pk)]},
        **HX,
    )

    assert _events(exact_send.matter, before) == [ChangeEventType.SUBMISSION_RECIPIENTS_CHANGED]
    rows = SubmissionRecipient.objects.filter(submission=exact_send)
    assert {row.organisation_id for row in rows} == {organisation.pk, other.pk}
    stayed = rows.get(organisation=organisation)
    assert (stayed.pk, stayed.note) == (kept.pk, IMPORTED_NOTE)


def test_the_same_instant_in_another_timezone_is_not_a_change(exact_send, specialist):
    """What the service compares is the moment, not how it is spelled."""
    import zoneinfo

    tallinn = EXACT.astimezone(zoneinfo.ZoneInfo("Europe/Tallinn"))
    assert tallinn.isoformat() != EXACT.isoformat()
    before = ChangeEvent.objects.filter(matter=exact_send.matter).count()

    correct_sent_opinion(
        submission=exact_send,
        sent_at=tallinn,
        sent_at_precision=SentAtPrecision.TIMESTAMP,
        summary=exact_send.summary,
        kind=exact_send.kind,
        addressees=None,
        actor=specialist,
        expected_revision=sent_opinion_revision(exact_send),
    )

    assert _events(exact_send.matter, before) == []


def test_a_correction_takes_no_longer_summary_than_capture(signed_in, exact_send):
    limit = EXTERNAL_POSITION_SUMMARY_MAX_LENGTH

    refused = signed_in.post(
        _url(exact_send), {**_as_opened(signed_in, exact_send), "summary": "x" * (limit + 1)}, **HX
    )
    accepted = signed_in.post(
        _url(exact_send), {**_as_opened(signed_in, exact_send), "summary": "y" * limit}, **HX
    )

    assert refused.status_code == 400
    assert accepted.status_code == 200
    exact_send.refresh_from_db()
    assert exact_send.summary == "y" * limit


def test_an_equal_recipient_set_is_a_no_op_for_every_caller(exact_send, organisation, specialist):
    """`set_recipients` is shared with capture: an equal set writes and records nothing."""
    from app.submissions.services import set_recipients

    row = SubmissionRecipient.objects.get(submission=exact_send)
    before = ChangeEvent.objects.filter(matter=exact_send.matter).count()

    set_recipients(submission=exact_send, addressees=[organisation], actor=specialist)

    assert _events(exact_send.matter, before) == []
    assert SubmissionRecipient.objects.get(submission=exact_send).pk == row.pk


# -- ENG-025 ------------------------------------------------------------------------

PAGE = "https://koda.ee/uudised/r3-ulevaade"


def _add(client, matter, url=PAGE):
    return client.post(
        reverse("matters:add_website_overview", kwargs={"pk": matter.pk}),
        {"url": url, "published_on": "14.03.2026"},
        **HX,
    )


def _live(matter):
    return MatterWebsiteOverview.objects.filter(
        matter=matter, status=WebsiteOverviewStatus.PUBLISHED, removed_at__isnull=True
    )


def test_a_removed_page_can_be_recorded_again(signed_in, normal_matter, specialist):
    assert _add(signed_in, normal_matter).status_code == 200
    first = MatterWebsiteOverview.objects.get(matter=normal_matter)
    remove_matter_record(
        matter_id=normal_matter.pk, kind_key="ulevaade", record_id=first.pk, actor=specialist
    )

    again = _add(signed_in, normal_matter)

    assert again.status_code == 200
    assert _live(normal_matter).get().url == PAGE
    first.refresh_from_db()  # the removed row is kept, as removed, for the audit trail
    assert first.removed_at is not None and first.url == PAGE


def test_a_live_duplicate_is_refused_with_a_sentence(signed_in, normal_matter):
    _add(signed_in, normal_matter)

    response = _add(signed_in, normal_matter)

    assert response.status_code == 400
    body = response.content.decode()
    assert "Selle aadressiga ülevaade või uudis on sellel teemal juba kirjas." in body
    assert PAGE in body  # what was typed comes back
    assert _live(normal_matter).count() == 1


def test_correcting_onto_an_occupied_address_is_refused(signed_in, normal_matter):
    _add(signed_in, normal_matter)
    _add(signed_in, normal_matter, url=f"{PAGE}-teine")
    second = _live(normal_matter).get(url=f"{PAGE}-teine")

    response = signed_in.post(
        reverse(
            "matters:correct_website_overview",
            kwargs={"pk": normal_matter.pk, "overview_id": second.pk},
        ),
        {"url": PAGE, "published_on": "14.03.2026", "revision": website_overview_revision(second)},
        **HX,
    )

    assert response.status_code == 400
    assert "juba kirjas" in response.content.decode()
    second.refresh_from_db()
    assert second.url == f"{PAGE}-teine"


def test_publishing_a_plan_onto_an_occupied_address_is_refused(signed_in, normal_matter):
    _add(signed_in, normal_matter)
    plan = plan_website_overview(matter=normal_matter, actor=normal_matter.owner)

    response = signed_in.post(
        reverse(
            "matters:publish_website_overview",
            kwargs={"pk": normal_matter.pk, "overview_id": plan.pk},
        ),
        {"url": PAGE, "published_on": "14.03.2026"},
        **HX,
    )

    assert response.status_code == 400
    assert "juba kirjas" in response.content.decode()
    plan.refresh_from_db()
    assert plan.status == WebsiteOverviewStatus.PLANNED


def test_the_same_address_on_another_matter_is_allowed(signed_in, normal_matter, specialist):
    other = factories.MatterFactory(owner=specialist)
    _add(signed_in, normal_matter)

    assert _add(signed_in, other).status_code == 200
    assert _live(other).get().url == PAGE


def test_the_database_refusal_is_the_same_sentence_when_the_question_raced(
    normal_matter, specialist, monkeypatch
):
    """The pre-check is a question asked before a write; the constraint is the answer.

    Simulated by letting the question say «free» while a live row holds the
    address: the constraint refuses inside a savepoint and the service says the
    same sentence, rather than a 500 out of an aborted transaction — and the
    invariant stands.
    """
    from app.matters import services

    published = plan_website_overview(matter=normal_matter, actor=specialist)
    services.publish_website_overview(
        overview=published, url=PAGE, published_on=dt.date(2026, 3, 14), actor=specialist
    )
    other = plan_website_overview(matter=normal_matter, actor=specialist)
    services.publish_website_overview(
        overview=other, url=f"{PAGE}-teine", published_on=dt.date(2026, 3, 14), actor=specialist
    )

    class Nothing:
        def exclude(self, **_kwargs):
            return self

        def exists(self):
            return False

    real_filter = MatterWebsiteOverview.objects.filter

    def raced(*args, **kwargs):
        if "removed_at__isnull" in kwargs:
            return Nothing()
        return real_filter(*args, **kwargs)

    monkeypatch.setattr(MatterWebsiteOverview.objects, "filter", raced)
    with pytest.raises(DomainError, match="juba kirjas"):
        correct_website_overview_link(
            overview=other, url=PAGE, published_on=dt.date(2026, 3, 14), actor=specialist
        )
    monkeypatch.undo()

    assert _live(normal_matter).filter(url=PAGE).count() == 1
