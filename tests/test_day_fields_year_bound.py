"""A typed day outside 1990–2100 is refused with a sentence, never a 500.

JQA-008 (audit 2026-10-17). `EstonianDateField` read any day `strptime` could
read, so `01.01.0001` reached six doors that turn a day into Tallinn midnight
(`start_of_local_day`, `as_midnight`). Tallinn's local-mean-time offset of
+1:39 in year 1 puts that midnight in year 0 UTC, and PostgreSQL refused the
INSERT with `DataError: timestamp too small` — an HTTP 500 where a refusal
belonged. `02.01.0001` was stored, an implausible day nobody meant.

The range is the one `app.workflow.dates` has owned since Stage 2G and which
`app/reporting/context.py` and docs/adr/0121 §3 already said every dated form
enforced: 1990–2100 (CORR-02). It now lives in the shared day field, so every
day box refuses the same way, and the two services that turn a day into a
stored timestamp refuse it as well, so a caller that is not a form cannot
reach the database with one either.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

import pytest
from django import forms
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.core.widgets import EstonianDateField
from app.matters.entry_enums import EntryKind
from app.matters.models import Entry, Matter
from app.matters.workspace import add_matter_other
from app.organisations.models import Organisation
from app.submissions.enums import SentAtPrecision
from app.submissions.models import Submission
from app.submissions.services import register_sent_opinion
from app.workflow.dates import DAY_OUT_OF_RANGE, EARLIEST_DAY, LATEST_DAY
from tests import factories
from tests.refusals import refused
from tests.test_admin_is_not_a_back_door import _form
from tests.test_closed_matter_documents import _opinion_file
from tests.test_correction_round_polish import _sent
from tests.test_koda_proposal_create_flow import pdf, sent_fields

pytestmark = pytest.mark.django_db

HX = {"HX-Request": "true"}

#: The day that was a 500, the last day before the range, and the first day after.
OUT_OF_RANGE = ["01.01.0001", "31.12.1989", "01.01.2101"]


def _client(user) -> Client:
    client = Client(raise_request_exception=False)
    client.force_login(user)
    return client


@pytest.fixture
def ministry(db) -> Organisation:
    return Organisation.objects.create(name="Aastapiiri Näidisministeerium")


# Each door posts `day` the way its own form does and answers (response, stored),
# where `stored` is True when the post left anything behind.
Door = Callable[[str, Any, Any, Any], tuple[Any, bool]]


def _muu(day, specialist, ministry, capture_evidence):
    matter = factories.MatterFactory(owner=specialist)
    response = _client(specialist).post(
        reverse("matters:add_other", kwargs={"pk": matter.pk}),
        {"description": "Aastapiiri taustamaterjal", "occurred_on": day},
        headers=HX,
    )
    return response, Entry.objects.filter(matter=matter, kind=EntryKind.OTHER).exists()


def _kohtumine(day, specialist, ministry, capture_evidence):
    matter = factories.MatterFactory(owner=specialist)
    response = _client(specialist).post(
        reverse("matters:add_meeting", kwargs={"pk": matter.pk}),
        {"summary": "Aastapiiri kohtumine", "occurred_on": day},
        headers=HX,
    )
    return response, Entry.objects.filter(matter=matter, kind=EntryKind.MEETING).exists()


def _koja_arvamus(day, specialist, ministry, capture_evidence):
    matter = factories.MatterFactory(owner=specialist)
    response = _client(specialist).post(
        reverse("matters:add_koda_opinion", kwargs={"pk": matter.pk}),
        {
            "upload": pdf("Aastapiiri_arvamus.pdf"),
            "recipients": [str(ministry.pk)],
            "sent_on": day,
        },
    )
    return response, Submission.objects.filter(matter=matter).exists()


def _registreeri_saatmine(day, specialist, ministry, capture_evidence):
    matter = factories.MatterFactory(owner=specialist)
    document = _opinion_file(matter, name="Aastapiiri_Koja_arvamus.pdf", actor=specialist)
    response = _client(specialist).post(
        reverse("submissions:register_sent", kwargs={"matter_id": matter.pk}),
        {
            "saadetud-document": str(document.pk),
            "saadetud-title": "Aastapiiri Koja arvamus",
            "saadetud-kind": "FORMAL_OPINION",
            "saadetud-recipients": [str(ministry.pk)],
            "saadetud-sent_on": day,
        },
    )
    return response, Submission.objects.filter(matter=matter).exists()


def _muuda_saatmist(day, specialist, ministry, capture_evidence):
    matter = factories.MatterFactory(owner=specialist)
    submission = _sent(matter, specialist, ministry, capture_evidence)
    before = submission.sent_at
    client = _client(specialist)
    url = reverse(
        "matters:update_sent_opinion",
        kwargs={"pk": matter.pk, "submission_id": submission.pk},
    )
    data = {
        key: value[0] if len(value) == 1 else value for key, value in _form(client, url).items()
    }
    data["sent_on"] = day
    response = client.post(url, data, headers=HX)
    submission.refresh_from_db()
    return response, submission.sent_at != before


def _uus_teema_saadetud(day, specialist, ministry, capture_evidence):
    title = f"Aastapiiri saadetud ettepanek {day}"
    response = _client(specialist).post(
        reverse("matters:matter_create"),
        sent_fields(title, ministry, sent_on=day, files=[pdf("Aastapiiri_poordumine.pdf")]),
    )
    return response, Matter.all_objects.filter(title=title).exists()


DOORS: dict[str, Door] = {
    "muu": _muu,
    "kohtumine": _kohtumine,
    "koja_arvamus": _koja_arvamus,
    "registreeri_saatmine": _registreeri_saatmine,
    "muuda_saatmist": _muuda_saatmist,
    "uus_teema_saadetud": _uus_teema_saadetud,
}


#: Dokumendid «Registreeri saatmine» is a full-page POST that answers a refusal
#: by redirecting back to Dokumendid with the refused fields named in a message
#: (`submissions.views._refusal_detail`), which is how it refuses every invalid
#: field. The other five re-render their panel with the field's own sentence.
REDIRECTING_DOORS = {
    "registreeri_saatmine": "Saatmise registreerimine ebaõnnestus: täida Saadetud."
}


@pytest.mark.parametrize("day", OUT_OF_RANGE)
@pytest.mark.parametrize("door", sorted(DOORS))
def test_a_day_outside_the_supported_range_is_refused_on_every_door(
    door, day, specialist, ministry, capture_evidence
):
    """A refusal with a sentence, and nothing written — on all six doors of JQA-008."""
    response, stored = DOORS[door](day, specialist, ministry, capture_evidence)
    if door in REDIRECTING_DOORS:
        assert response.status_code == 302, (door, day, response.status_code)
        flashed = [str(message) for message in get_messages(response.wsgi_request)]
        assert flashed == [REDIRECTING_DOORS[door]]
    else:
        assert response.status_code == 400, (door, day, response.status_code)
        assert DAY_OUT_OF_RANGE in response.content.decode()
    assert not stored


@pytest.mark.parametrize("door", sorted(DOORS))
def test_a_day_inside_the_range_is_still_saved_on_every_door(
    door, specialist, ministry, capture_evidence
):
    """The positive control: the same payloads save with an ordinary past day."""
    day = timezone.localdate() - dt.timedelta(days=3)
    response, stored = DOORS[door](
        f"{day.day}.{day.month}.{day.year}", specialist, ministry, capture_evidence
    )
    assert response.status_code in (200, 302), (door, response.status_code)
    assert stored


def test_the_first_supported_day_is_saved_and_the_day_before_is_refused(specialist, ministry):
    """The lower bound through a real door: 1.1.1990 stores that day, 31.12.1989 does not."""
    response, stored = _muu("01.01.1990", specialist, ministry, None)
    assert response.status_code == 200
    assert stored
    (entry,) = Entry.objects.filter(kind=EntryKind.OTHER, body__contains="Aastapiiri")
    assert timezone.localtime(entry.occurred_at).date() == dt.date(1990, 1, 1)

    response, stored = _muu("31.12.1989", specialist, ministry, None)
    assert response.status_code == 400
    assert not stored


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("1.1.1990", dt.date(1990, 1, 1)),
        ("1990-01-01", dt.date(1990, 1, 1)),
        ("31.12.2100", dt.date(2100, 12, 31)),
        ("2100-12-31", dt.date(2100, 12, 31)),
    ],
)
def test_the_field_accepts_both_ends_of_the_range(typed, expected):
    assert EARLIEST_DAY == dt.date(1990, 1, 1)
    assert LATEST_DAY == dt.date(2100, 12, 31)
    assert EstonianDateField().clean(typed) == expected


@pytest.mark.parametrize(
    "typed", ["31.12.1989", "1989-12-31", "1.1.2101", "01.01.0001", "31.12.9999"]
)
def test_the_field_refuses_a_day_outside_the_range_with_the_sentence(typed):
    with pytest.raises(forms.ValidationError) as refusal:
        EstonianDateField(required=False).clean(typed)
    assert refusal.value.messages == [DAY_OUT_OF_RANGE]


def test_an_empty_optional_day_is_still_empty():
    assert EstonianDateField(required=False).clean("") is None


def test_the_entry_service_refuses_a_day_outside_the_range(normal_matter, specialist):
    """A caller that is not a form cannot store an implausible Muu either."""
    for day in (dt.date(1, 1, 1), dt.date(1989, 12, 31)):
        with refused(DAY_OUT_OF_RANGE):
            add_matter_other(
                matter=normal_matter, author=specialist, description="x", occurred_on=day
            )
    assert not Entry.objects.filter(matter=normal_matter, kind=EntryKind.OTHER).exists()


def test_the_send_service_refuses_a_day_outside_the_range(
    normal_matter, specialist, organisation, capture_evidence
):
    """Year 1 local midnight, as `as_midnight` builds it, is refused before the INSERT."""
    from app.documents.enums import DocumentRole

    version = capture_evidence(
        normal_matter,
        b"%PDF-1.4 synthetic evidence",
        "koja-arvamus.pdf",
        "application/pdf",
        title="Koja arvamus eelnõule",
        role=DocumentRole.KODA_SUBMISSION_FINAL,
    )
    for day in (dt.date(1, 1, 1), dt.date(1989, 12, 31)):
        with refused(DAY_OUT_OF_RANGE):
            register_sent_opinion(
                document=version.document,
                version=version,
                title="Koja arvamus eelnõule",
                recipients=[organisation],
                sent_at=timezone.make_aware(dt.datetime.combine(day, dt.time.min)),
                sent_at_precision=SentAtPrecision.DATE,
                actor=specialist,
            )
    assert not Submission.objects.filter(matter=normal_matter).exists()
