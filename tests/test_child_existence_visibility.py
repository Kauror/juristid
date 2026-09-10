"""A restricted child must not decide what a reader sees about its Matter.

AUTH-003 established that a *projection* of a child may never be broader than
the child itself. This file answers the same question asked the other way round,
about a shape that survived it: a query that never renders the child at all and
only asks whether one **exists**.

    has_open_action = NextAction.objects.filter(matter=OuterRef("pk"), ...)

Read unscoped, that probe hands a restricted record a vote over what an
unauthorized reader sees. Six such probes shipped, and the argument each rested
on was the same one — an ``Exists`` that can only *remove* a row cannot widen
anything, so it cannot disclose anything.

**Removing a row is observable.** These tests are written as two worlds:

    world A: a NORMAL Matter a reader may open
    world B: world A, plus one RESTRICTED child under it

Where the product says the child is invisible, the reader's page must be the
same in both worlds. That is a stronger claim than "the secret string is
absent", and it is the claim the defect broke: nothing leaked a title, and a
Matter still quietly left a deadline list, a work-state count and a statistics
card the moment a colleague filed a restricted opinion or a restricted step on
it. Everything else went on showing the Matter, so what the reader learned was
precisely *that restricted work happened on this named file*.

One of the six also leaked outright rather than by inference: the Teema page's
`Järgmiseks` row read `workflow.services.current_next_action`, which is the
domain's reader-blind question, and printed a restricted step's text and date to
anybody who could open the Matter.

The last two tests are the other half of the contract. A fix that hid the work
from its own owner would trade one defect for a worse one.
"""

from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.accounts.enums import UserRole
from app.core.enums import Visibility
from app.matters import dashboard, selectors
from app.matters import work_items as wi
from app.submissions.enums import SubmissionStatus
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics
from tests import factories
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

PDF = "application/pdf"

#: Distinctive enough that a match can only have come from the restricted step.
HIDDEN_ACTION = "SALAJANE-SAMM-4471"


@pytest.fixture
def mixed_matter(db):
    """A NORMAL Matter with a response deadline, and a reader who may open it.

    ``response_deadline`` is today, so the Matter is in every deadline
    population there is — which is what makes it possible to watch one leave.
    """
    owner = factories.UserFactory(display_name="Peeter Paas")
    organisation = factories.OrganisationFactory(name="Näidisministeerium")
    matter = factories.MatterFactory(
        title="Segateema",
        owner=owner,
        visibility=Visibility.NORMAL,
        addressee_organisation=organisation,
        reference_year=2099,
        reference_number=4471,
        received_date=timezone.localdate(),
        response_deadline=timezone.localdate(),
    )
    matter.source_organisations.set([organisation])
    reader = factories.UserFactory(role=UserRole.READER, display_name="Lugeja Luts")
    return {
        "owner": owner,
        "organisation": organisation,
        "matter": matter,
        "reader": reader,
    }


def restricted_action(world):
    """An open DO/DEADLINE step nobody outside the Matter's participants may read."""
    return factories.NextActionFactory(
        matter=world["matter"],
        text=HIDDEN_ACTION,
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate(),
        date_precision=DatePrecision.EXACT,
        status=ActionStatus.OPEN,
        responsible=world["owner"],
        visibility_override=Visibility.RESTRICTED,
    )


def restricted_submission(world, capture_evidence, extract):
    """A sent opinion restricted below an otherwise ordinary Matter."""
    version = capture_evidence(
        world["matter"],
        corpus.text_pdf(["Salajane arvamus"]),
        "salajane.pdf",
        PDF,
        title="Salajane arvamus",
        visibility_override=Visibility.RESTRICTED,
    )
    extract(version)
    submission = factories.SubmissionFactory(
        matter=world["matter"],
        title="Salajane arvamus",
        status=SubmissionStatus.SENT,
        sent_at=timezone.now(),
        final_version=version,
        visibility_override=Visibility.RESTRICTED,
    )
    submission.recipients.set([world["organisation"]])
    return submission


def _page(client: Client, url: str) -> str:
    """One page, with the values that legitimately differ between two loads removed.

    Only the CSRF token, which is per-request by construction. Everything else
    is compared as it was rendered — the point of a two-world test is that a
    number nobody thought about is caught by the same assertion as a title.
    """
    response = client.get(url)
    assert response.status_code == 200, f"{url} answered {response.status_code}"
    # The CSV exports stream, so the body is not on `.content`.
    raw = (
        b"".join(response.streaming_content)
        if getattr(response, "streaming", False)
        else response.content
    )
    return re.sub(r'value="[A-Za-z0-9]{32,}"', "CSRF", raw.decode())


def _reader_client(world) -> Client:
    client = Client()
    client.force_login(world["reader"])
    return client


# ---------------------------------------------------------------------------
# The one that leaked outright
# ---------------------------------------------------------------------------


def test_the_matter_page_does_not_print_a_restricted_next_action(mixed_matter):
    client = _reader_client(mixed_matter)
    url = reverse("matters:matter_detail", kwargs={"pk": mixed_matter["matter"].pk})

    before = _page(client, url)
    restricted_action(mixed_matter)
    after = _page(client, url)

    assert HIDDEN_ACTION not in after
    assert before == after


def test_the_matter_page_still_shows_the_owner_their_own_step(mixed_matter):
    """The other half: scoping must not hide the work from its participants."""
    client = Client()
    client.force_login(mixed_matter["owner"])
    restricted_action(mixed_matter)

    body = _page(client, reverse("matters:matter_detail", kwargs={"pk": mixed_matter["matter"].pk}))

    assert HIDDEN_ACTION in body


# ---------------------------------------------------------------------------
# The populations that quietly dropped a visible Matter
# ---------------------------------------------------------------------------


def test_a_restricted_step_does_not_remove_a_matter_from_the_quiet_population(mixed_matter):
    reader = mixed_matter["reader"]
    matter = mixed_matter["matter"]

    assert matter in dashboard.without_next_action(reader)
    assert matter in selectors.matters_without_next_action(reader)

    restricted_action(mixed_matter)

    assert matter in dashboard.without_next_action(reader), "Osakond's column dropped it"
    assert matter in selectors.matters_without_next_action(reader), "the selector dropped it"


def test_a_restricted_step_does_not_discharge_a_response_deadline(mixed_matter):
    reader = mixed_matter["reader"]
    matter = mixed_matter["matter"]

    assert matter in wi.outstanding_response_deadlines(reader)
    assert wi.response_deadline_is_outstanding(matter, reader)

    restricted_action(mixed_matter)

    assert matter in wi.outstanding_response_deadlines(reader)
    assert wi.response_deadline_is_outstanding(matter, reader)


def test_a_restricted_opinion_does_not_discharge_a_response_deadline(
    mixed_matter, capture_evidence, extract
):
    reader = mixed_matter["reader"]
    matter = mixed_matter["matter"]

    assert matter in wi.outstanding_response_deadlines(reader)

    restricted_submission(mixed_matter, capture_evidence, extract)

    assert matter in wi.outstanding_response_deadlines(reader)


def test_a_participant_sees_their_own_work_discharge_the_deadline(mixed_matter):
    """The owner participates, so for them the step is real and the deadline is met."""
    owner = mixed_matter["owner"]
    matter = mixed_matter["matter"]

    assert matter in wi.outstanding_response_deadlines(owner)
    restricted_action(mixed_matter)
    assert matter not in wi.outstanding_response_deadlines(owner)


# ---------------------------------------------------------------------------
# The surfaces those populations are printed on
# ---------------------------------------------------------------------------


def test_the_department_page_is_unchanged_by_a_restricted_step(mixed_matter):
    client = _reader_client(mixed_matter)
    url = reverse("matters:department")

    before = _page(client, url)
    restricted_action(mixed_matter)
    after = _page(client, url)

    assert before == after


def test_the_department_page_is_unchanged_by_a_restricted_opinion(
    mixed_matter, capture_evidence, extract
):
    client = _reader_client(mixed_matter)
    url = reverse("matters:department")

    before = _page(client, url)
    restricted_submission(mixed_matter, capture_evidence, extract)
    after = _page(client, url)

    assert before == after


def test_the_data_quality_export_is_unchanged_by_a_restricted_step(mixed_matter):
    client = _reader_client(mixed_matter)
    url = reverse("reporting:export", kwargs={"slug": "andmekvaliteet"})

    before = _page(client, url)
    restricted_action(mixed_matter)
    after = _page(client, url)

    assert before == after


def test_the_metric_and_the_register_list_it_links_to_agree(mixed_matter):
    """The reconciliation half of the same defect.

    ``ACTIVE_WITHOUT_NEXT_ACTION`` links to ``?tegevus=puudub``, which has asked
    through ``NextAction.objects.visible_to`` since AUTH-003. While the card
    counted reader-blind, it dropped exactly the row the list it opened still
    showed — a card reading one less than the page behind it.
    """
    client = _reader_client(mixed_matter)
    restricted_action(mixed_matter)

    listed = _page(client, reverse("matters:matter_list") + "?olek=avatud&liik=FULL&tegevus=puudub")
    counted = _page(client, reverse("reporting:export", kwargs={"slug": "andmekvaliteet"}))

    assert "Segateema" in listed
    assert "Aktiivne teema ilma järgmise tegevuseta;1;" in counted
