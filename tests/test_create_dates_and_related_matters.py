"""Uus teema's dates and its chosen related Teemad — the owner's corrections of 2026-10-13.

DATES (docs/adr/0130 and 0151, amendments of 2026-10-13)
* A — `Saabus` first, `Arvamuse tähtaeg` second, in one row; `Saabus` opens on
  today, the deadline empty.
* B — «Koja ettepanek või pöördumine» records no `Saabus` and keeps the optional
  deadline: empty is no deadline, a date is saved, the track is `Koja algatus`.
  Without the script as well — the server decides.
* D — on a Chamber initiative the header and `Muuda teemat` offer the deadline;
  only «Uus arvamuse küsimine» (a new request) is not offered.
* E — the already-sent fast path with a deadline: one `KODA_PROPOSAL`, one
  thirty-day check from the send day, and nothing left outstanding.

RELATED TEEMAD (docs/adr/0150 §3, amendment of 2026-10-13)
* A — the chosen list is drawn under the heading and above the search box.
* G — a refused save draws every chosen Teema back there, with its reference
  and title and a × named after it; nothing is linked.
* H — the Teemad left in the list are linked once each, and only they.

The searching, the visibility and synthetic-work refusals and the duplicate
collapse are pinned in tests/test_uus_teema_related_search.py; the browser half
is e2e/test_create_dates_and_related_matters.py.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.dashboard import drafting_matters
from app.matters.enums import ResponseDeadlineOutcome
from app.matters.models import Matter, MatterResponseDeadline
from app.matters.services import create_matter
from app.matters.work_items import response_obligation_of
from app.organisations.models import Organisation
from app.related_materials.models import MatterRelation
from app.related_materials.views import LINK_ON_CREATE_CHOSEN_FIELD
from app.submissions.enums import SubmissionKind, SubmissionStatus
from app.submissions.models import Submission
from app.workflow.enums import Track
from app.workflow.models import NextAction, OpinionFollowUp
from tests import factories
from tests.test_koda_proposal_create_flow import pdf, sent_fields

pytestmark = pytest.mark.django_db


@pytest.fixture
def ministry(db) -> Organisation:
    return Organisation.objects.create(name="Näidisministeerium")


CREATE = reverse("matters:matter_create")


def today() -> dt.date:
    return timezone.localdate()


def et(day: dt.date) -> str:
    return f"{day.day}.{day.month}.{day.year}"


def proposal() -> list[str]:
    from app.taxonomy.models import LegalInstrumentType

    return [str(LegalInstrumentType.objects.get(key="koja-ettepanek").pk)]


def date_row(body: str) -> str:
    start = body.index("createform__pair--dates")
    return body[start : body.index("</div>", start)]


# ---------------------------------------------------------------------------
# Dates — A: the order
# ---------------------------------------------------------------------------


def test_saabus_comes_first_and_the_deadline_second_in_one_row(signed_in):
    response = signed_in.get(CREATE)
    form = response.context["form"]
    row = date_row(response.content.decode())

    assert row.index('name="received_date"') < row.index('name="response_deadline"')
    # Saabus opens on today, the deadline on nothing.
    assert form["received_date"].value() == today()
    assert not form["response_deadline"].value()
    # Only `Saabus` is governed by the initiative: the deadline is not marked.
    assert row.count("data-initiative-date") == 1
    assert re.search(r'<label class="field" data-initiative-date>\s*<span[^>]*>Saabus', row)
    assert re.search(r'<label class="field" id="arvamuse-tahtaeg">', row)


# ---------------------------------------------------------------------------
# Dates — B: «Koja ettepanek või pöördumine»
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("deadline_days", [None, 12])
def test_a_proposal_records_no_saabus_and_keeps_an_optional_deadline(
    signed_in, specialist, deadline_days
):
    """A crafted POST — no script — still carrying today's `Saabus`."""
    due = None if deadline_days is None else today() + dt.timedelta(days=deadline_days)
    title = f"Pöördumine {deadline_days}"

    response = signed_in.post(
        CREATE,
        {
            "title": title,
            "legal_instruments": proposal(),
            "received_date": et(today()),
            "response_deadline": et(due) if due else "",
        },
    )

    assert response.status_code == 302
    matter = Matter.objects.get(title=title)
    assert matter.track == Track.KODA_INITIATIVE
    assert matter.received_date is None
    assert matter.response_deadline == due
    # A deadline is not a send, and no check is scheduled from it.
    assert not Submission.objects.filter(matter=matter).exists()
    assert not OpinionFollowUp.objects.filter(submission__matter=matter).exists()
    assert not NextAction.objects.filter(matter=matter, follow_up__isnull=False).exists()


def test_an_ordinary_teema_still_records_both_dates(signed_in):
    due = today() + dt.timedelta(days=9)

    signed_in.post(
        CREATE,
        {
            "title": "Tavaline kahe kuupäevaga",
            "received_date": et(today()),
            "response_deadline": et(due),
        },
    )

    matter = Matter.objects.get(title="Tavaline kahe kuupäevaga")
    assert (matter.track, matter.received_date, matter.response_deadline) == ("", today(), due)


def test_a_cleared_saabus_stays_unknown_on_an_ordinary_teema(signed_in):
    signed_in.post(CREATE, {"title": "Saabus tühjendatud", "received_date": ""})

    assert Matter.objects.get(title="Saabus tühjendatud").received_date is None


# ---------------------------------------------------------------------------
# Dates — D: the header and Muuda teemat on an initiative
# ---------------------------------------------------------------------------


def _initiative(specialist, **extra) -> Matter:
    return create_matter(
        title="Algatus tähtajaga", actor=specialist, track=Track.KODA_INITIATIVE, **extra
    )


def test_the_header_shows_an_initiative_deadline_and_no_new_request(signed_in, specialist):
    due = today() + dt.timedelta(days=6)
    matter = _initiative(specialist, response_deadline=due, response_requested_at=timezone.now())

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    meta = body.split('class="metaline"', 1)[1].split("matters/partials/summary", 1)[0]

    assert "Arvamuse tähtaeg" in meta
    assert et(due) in meta
    # Moved or cleared, never «a new request».
    assert 'value="MOVED"' in meta
    assert 'value="REPLACED"' not in meta
    assert "Uus arvamuse küsimine" not in meta


def test_an_ordinary_header_still_offers_a_new_request(signed_in, specialist):
    matter = create_matter(
        title="Tavaline tähtajaga",
        actor=specialist,
        response_deadline=today() + dt.timedelta(days=6),
        response_requested_at=timezone.now(),
    )

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()

    assert 'value="REPLACED"' in body
    assert "Uus arvamuse küsimine" in body


def test_muuda_teemat_asks_the_deadline_on_an_initiative(signed_in, specialist):
    matter = _initiative(specialist)

    body = signed_in.get(reverse("matters:matter_edit", kwargs={"pk": matter.pk})).content.decode()

    deadline = body.index('name="response_deadline"')
    label = body.rfind("<label", 0, deadline)
    assert "data-initiative-date" not in body[label:deadline]
    assert "uut saabumise kuupäeva ei lisata" in body
    assert "ega arvamuse tähtaega" not in body


# ---------------------------------------------------------------------------
# Dates — E: the already-sent fast path with a deadline
# ---------------------------------------------------------------------------


def test_a_sent_proposal_with_a_deadline_leaves_nothing_outstanding(
    signed_in, specialist, ministry, evidence_root
):
    due = today() + dt.timedelta(days=20)
    sent_on = today() - dt.timedelta(days=2)
    response = signed_in.post(
        CREATE,
        sent_fields(
            "Saadetud tähtajaga",
            ministry,
            sent_on=et(sent_on),
            response_deadline=et(due),
            files=[pdf("poordumine.pdf")],
        ),
    )

    assert response.status_code == 302
    matter = Matter.objects.get(title="Saadetud tähtajaga")
    assert (matter.track, matter.received_date) == (Track.KODA_INITIATIVE, None)
    (sent,) = Submission.objects.filter(matter=matter)
    # The deadline was given and the proposal went out: it is answered by this
    # send, through the canonical deadline history, and is current no longer.
    (ended,) = MatterResponseDeadline.objects.filter(matter=matter)
    assert (ended.deadline, ended.outcome, ended.submission_id) == (
        due,
        ResponseDeadlineOutcome.ANSWERED,
        sent.pk,
    )
    assert matter.response_deadline is None
    assert (sent.kind, sent.status) == (SubmissionKind.KODA_PROPOSAL, SubmissionStatus.SENT)
    assert timezone.localtime(sent.sent_at).date() == sent_on
    # One check, from the send, not from the deadline.
    (check,) = NextAction.objects.filter(matter=matter, follow_up__isnull=False)
    assert check.target_date == sent_on + dt.timedelta(days=30)
    assert OpinionFollowUp.objects.filter(submission__matter=matter).count() == 1
    # The send answers the deadline: no outstanding opinion work is invented.
    assert not response_obligation_of(matter, specialist).is_outstanding
    assert matter not in drafting_matters(specialist)


# ---------------------------------------------------------------------------
# Related Teemad
# ---------------------------------------------------------------------------


def earlier(owner, title: str, number: int) -> Matter:
    return factories.MatterFactory(
        owner=owner, title=title, reference_year=2099, reference_number=number
    )


def relations_of(matter: Matter) -> set:
    rows = MatterRelation.objects.filter(matter_a=matter) | MatterRelation.objects.filter(
        matter_b=matter
    )
    return {row.matter_b_id if row.matter_a_id == matter.pk else row.matter_a_id for row in rows}


def picker(body: str) -> str:
    start = body.index('id="seo-olemasoleva-teemaga"')
    return body[start : body.index("<template data-related-chosen-template", start)]


def test_the_chosen_list_is_under_the_heading_and_above_the_search(signed_in, specialist):
    first = earlier(specialist, "Esimene valitud teema", 71)
    second = earlier(specialist, "Teine valitud teema, mille pealkiri on pikk " * 3, 72)

    response = signed_in.post(
        CREATE, {"title": "", LINK_ON_CREATE_CHOSEN_FIELD: [str(first.pk), str(second.pk)]}
    )

    assert response.status_code == 400
    block = picker(response.content.decode())
    heading = block.index(">Seo olemasoleva teemaga</label>")
    chosen = block.index("data-related-chosen")
    search = block.index('id="seotud-teema-otsing"')
    results = block.index('id="seotud-teema-tulemused"')
    assert heading < chosen < search < results
    # Every chosen Teema: its reference, its title, a × named after it, in order.
    items = re.findall(r'<li class="relpick__item"[\s\S]*?</li>', block)
    assert len(items) == 2
    for item, matter in zip(items, (first, second), strict=True):
        assert f'value="{matter.pk}"' in item
        assert matter.display_reference in item
        assert matter.title.strip() in item
        assert re.search(r'<button[^>]*type="button"[^>]*data-related-remove', item)
        assert f'aria-label="Eemalda valik: {matter.title}' in item
    # Nothing is linked by a refused save.
    assert relations_of(first) == set() and relations_of(second) == set()


def test_with_nothing_chosen_the_list_holds_no_rows(signed_in):
    block = picker(signed_in.get(CREATE).content.decode())

    assert "relpick__item" not in block
    assert block.index("data-related-chosen") < block.index('id="seotud-teema-otsing"')


def test_the_teemad_left_in_the_list_are_linked_once_each(signed_in, specialist):
    """Chosen three, one taken back (the browser simply stops posting it): two links."""
    kept = [earlier(specialist, "Jäetud üks", 73), earlier(specialist, "Jäetud kaks", 74)]
    removed = earlier(specialist, "Eemaldatud", 75)

    response = signed_in.post(
        CREATE,
        {
            "title": "Kahe seosega teema",
            LINK_ON_CREATE_CHOSEN_FIELD: [str(kept[0].pk), str(kept[1].pk), str(kept[0].pk)],
        },
    )

    assert response.status_code == 302
    created = Matter.objects.get(title="Kahe seosega teema")
    assert relations_of(created) == {kept[0].pk, kept[1].pk}
    assert relations_of(removed) == set()
    assert (
        ChangeEvent.objects.filter(
            matter=created, event_type=ChangeEventType.MATTER_RELATION_ADDED
        ).count()
        == 2
    )
