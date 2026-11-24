"""The department head's Teema page holds a query budget too (HR-12).

The health review (2026-11-23) measured the standard Teema page against the
ceiling `tests/test_teema_redesign.py` holds, and found the department head's
view — the same page plus the «Kustuta» offer — above it with no budget of its
own. Three reads were asked twice or one at a time:

* **The «Kustuta» offer** asked its seven blockers as seven `EXISTS` round
  trips (`deletion_may_be_offered`). Now one statement ORs them.
* **`Õigusakt`** was read as keys for the rail and again as rows for the
  header's line (`_legal_instrument_line`). Now prefetched once.
* **The institution catalogue** was read by the bar's forms
  (`read_organisation_choices`) and again by the header's `Saatja` checkboxes.
  Now the header is handed the rows already read.

Measured on PostgreSQL 18 with the rich synthetic Teema below (three
`Õigusakt`, six institutions, three policy areas, two collaborators, a stage
change, eight notes, four consultations, two sent opinions, two dated phases):
specialist owner 75 → 73, department head 81 → 73; the minimal Teema 63 → 61
and 69 → 61. The page rendered for each of them is the same before and after
(compared whole, ids normalised). The fragments that carry the three facts are
pinned here; the budgets are the measured count plus two.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.documents.derivatives import EmailAttachmentLink
from app.documents.services import add_evidence_version
from app.matters.deletion import deletion_may_be_offered, plan_matter_deletion
from app.matters.enums import EngagementKind
from app.matters.models import MatterTimelineStep
from app.matters.process_phases import PHASE_JOUSTUMINE, PHASE_RIIGIKOGU
from app.matters.services import add_engagement, add_entry, change_stage, create_matter
from app.taxonomy.models import LegalInstrumentType
from app.workflow.models import StageVocabulary
from tests import factories
from tests.test_related_materials import _sent_opinion

pytestmark = pytest.mark.django_db

#: The rich Teema's page, measured at **73** for its specialist owner and for a
#: department head (who is offered «Kustuta»), plus a margin of two. A head's
#: page was 81 before HR-12: seven of those were the offer's `EXISTS`, asked one
#: at a time.
RICH_TEEMA_PAGE_QUERY_CEILING = 75

#: The three instruments the rich Teema stands in, by key.
INSTRUMENT_COUNT = 3


@pytest.fixture
def rich_matter(specialist, other_specialist):
    institutions = [factories.OrganisationFactory(name=f"Asutus {n}") for n in (5, 3, 1, 4, 2, 0)]
    matter = create_matter(
        title="Pakendiseaduse muudatus",
        actor=specialist,
        owner=specialist,
        stage=StageVocabulary.objects.get(key="idea"),
    )
    matter.legal_instruments.set(list(LegalInstrumentType.objects.all()[:INSTRUMENT_COUNT]))
    matter.source_organisations.set(institutions[:3])
    matter.addressee_organisation = institutions[4]
    matter.save()
    matter.policy_areas.set([factories.PolicyAreaFactory() for _ in range(3)])
    matter.collaborators.set([other_specialist, factories.UserFactory()])
    change_stage(
        matter=matter, stage=StageVocabulary.objects.get(key="consultation"), actor=specialist
    )
    for index in range(8):
        add_entry(matter=matter, author=specialist, body=f"<p>Kirje {index}</p>")
    for index in range(4):
        add_engagement(
            matter=matter,
            kind=EngagementKind.SURVEY,
            title=f"Küsitlus {index}",
            occurred_on=dt.date(2026, 3, 1 + index),
            actor=specialist,
        )
    _sent_opinion(matter, "Arvamus 1")
    _sent_opinion(matter, "Arvamus 2")
    MatterTimelineStep.objects.create(
        matter=matter, phase_key=PHASE_JOUSTUMINE, occurs_on=dt.date(2027, 1, 1)
    )
    MatterTimelineStep.objects.create(
        matter=matter, phase_key=PHASE_RIIGIKOGU, occurs_on=dt.date(2026, 9, 1)
    )
    return matter


def _get(client, user, matter):
    client.force_login(user)
    url = reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    client.get(url)  # per-process caches warm, as on a running server
    with CaptureQueriesContext(connection) as captured:
        response = client.get(url)
    assert response.status_code == 200
    return response.content.decode(), [query["sql"] for query in captured]


@pytest.mark.parametrize("role", ["owner", "head"])
def test_the_rich_teema_page_holds_its_budget_for_both_roles(
    client, specialist, department_head, rich_matter, role
):
    viewer = specialist if role == "owner" else department_head
    body, queries = _get(client, viewer, rich_matter)

    assert len(queries) <= RICH_TEEMA_PAGE_QUERY_CEILING, len(queries)
    # «Kustuta» is the department head's (docs/adr/0165), and this Teema has no
    # blocker, so it is offered to the head and to nobody else.
    assert ("matterhead__delete" in body) is (role == "head")


@pytest.mark.parametrize("role", ["owner", "head"])
def test_the_header_reads_each_shared_fact_once(
    client, specialist, department_head, rich_matter, role
):
    viewer = specialist if role == "owner" else department_head
    body, queries = _get(client, viewer, rich_matter)

    instrument_reads = [sql for sql in queries if 'FROM "taxonomy_legalinstrumenttype"' in sql]
    assert len(instrument_reads) == 1, instrument_reads
    catalogue_reads = [
        sql
        for sql in queries
        if 'FROM "organisations_organisation"' in sql
        and "WHERE" not in sql
        and 'ORDER BY "organisations_organisation"."name"' in sql
    ]
    assert len(catalogue_reads) == 1, catalogue_reads
    offer_reads = [sql for sql in queries if 'FROM "matters_entryrevision"' in sql]
    assert len(offer_reads) == (1 if role == "head" else 0), offer_reads

    # The rail still says every `Õigusakt`, in the vocabulary's order.
    from django.utils.html import escape

    labels = [
        escape(instrument.label_et)
        for instrument in rich_matter.legal_instruments.order_by("sort_order", "label_et")
    ]
    assert len(labels) == INSTRUMENT_COUNT
    line = re.search(
        r'railcard__key">Õigusakt</span>\s*<span class="railcard__value">([^<]*)</span>', body
    )
    assert line is not None
    assert line.group(1) == ", ".join(labels)
    # And the `Saatja` checkboxes are the whole catalogue in name order, the
    # Teema's own senders ticked.
    boxes = re.findall(
        r'name="source_organisations" value="([^"]+)"\s*(checked)?', body, flags=re.S
    )
    from app.organisations.models import Organisation

    catalogue = list(Organisation.objects.order_by("name"))
    assert [pk for pk, _ in boxes] == [str(organisation.pk) for organisation in catalogue]
    ticked = {pk for pk, checked in boxes if checked}
    assert ticked == {str(pk) for pk in rich_matter.source_organisation_ids}


# ---------------------------------------------------------------------------
# The offer in one statement answers what the seven did
# ---------------------------------------------------------------------------


def _version(matter, name: str):
    document = factories.DocumentFactory(matter=matter)
    return add_evidence_version(
        document=document,
        content=b"%PDF-1.4\n" + name.encode(),
        original_filename=f"{name}.pdf",
        mime_type="application/pdf",
    )


def _attachment_from(source, target) -> None:
    EmailAttachmentLink._base_manager.create(
        parent_version=_version(source, "kiri"),
        attachment_version=_version(target, "manus"),
        ordinal=1,
        declared_filename="manus.pdf",
    )


@pytest.mark.parametrize(
    "blocker",
    [
        pytest.param(lambda here, other: _attachment_from(here, other), id="attachment-out"),
        pytest.param(lambda here, other: _attachment_from(other, here), id="attachment-in"),
    ],
)
def test_the_cross_matter_blockers_still_withhold_the_offer(specialist, blocker):
    """The e-mail links that cross Matters, both ways round, which
    `tests/test_deletion_offer_is_cheap.py` does not build. (An opinion of
    another Matter standing on a version here is refused by the database
    itself, `submissions_check_final_evidence`, so it cannot be built.)"""
    here = factories.MatterFactory(owner=specialist)
    other = factories.MatterFactory(owner=specialist)
    assert deletion_may_be_offered(here) is True

    blocker(here, other)

    assert deletion_may_be_offered(here) is False
    assert plan_matter_deletion(here).is_blocked


def test_a_link_inside_one_matter_does_not_withhold_it(specialist):
    here = factories.MatterFactory(owner=specialist)
    _attachment_from(here, here)

    assert deletion_may_be_offered(here) is True


def test_the_offer_is_one_query(specialist):
    matter = factories.MatterFactory(owner=specialist)
    add_entry(matter=matter, author=specialist, body="<p>Märge</p>")

    with CaptureQueriesContext(connection) as offer:
        deletion_may_be_offered(matter)

    assert len(offer) == 1
