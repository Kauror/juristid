"""«Registri järgi jätkub» links only to the Matter it names, and only if readable.

The register writes a continuation as ``YYYY_N``. The link was resolved from the
number alone, through the unfiltered manager, with an unordered ``.first()``
(ENG-057), so:

* ``2026_999`` linked to ``2024_999`` when 2026 had no such number;
* a RESTRICTED ``2026_999`` was linked — its id in the page — for a reader who
  would get a 404 behind the link.

It is now year and number together, through `Matter.objects.visible_to`, and
anything that is not exactly one readable Matter is the plain text the template
already had for a Matter this database does not hold.
"""

from __future__ import annotations

import hashlib

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
from app.legacy_import.models import MatterSourceReference
from app.legacy_import.register_display import register_facts_for
from app.legacy_import.register_semantics import OpinionSentState
from app.matters.models import Matter
from tests import factories

pytestmark = pytest.mark.django_db

DIGEST = hashlib.sha256(b"register-continuation-link").hexdigest()


def _continues_under(matter: Matter, reference: str) -> Matter:
    """A derived register row saying this Matter continues under ``reference``."""
    source = MatterSourceReference.objects.create(
        matter=matter,
        source_system="EXCEL_REGISTER",
        source_file_name="Tood eelnoudega.xlsx",
        source_snapshot_sha256=DIGEST,
        source_sheet="2026",
        source_row_number=matter.reference_number,
        source_row_raw={},
        source_title=matter.title,
        source_era="2026",
    )
    CurrentRegisterState.objects.create(
        matter=matter,
        source_reference=source,
        source_snapshot_sha256=DIGEST,
        source_sheet="2026",
        source_row_number=source.source_row_number,
        currency=RegisterCurrency.SUPERSEDED,
        status_label="Kooskolastusringil",
        opinion_sent_recorded=False,
        opinion_sent_state=OpinionSentState.BLANK,
        continues_under_reference=reference,
        observed_at=timezone.now(),
    )
    return Matter.objects.get(pk=matter.pk)


@pytest.fixture
def predecessor(specialist):
    return factories.MatterFactory(owner=specialist, reference_year=2026, reference_number=40)


def _page(client, user, matter):
    client.force_login(user)
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def test_the_named_matter_is_linked(client, specialist, predecessor):
    successor = factories.MatterFactory(owner=specialist, reference_year=2026, reference_number=999)
    matter = _continues_under(predecessor, "2026_999")

    assert register_facts_for(matter, specialist).continues_under_id == successor.pk
    assert reverse("matters:matter_detail", kwargs={"pk": successor.pk}) in _page(
        client, specialist, matter
    )


def test_the_same_number_in_another_year_is_not_the_named_matter(client, specialist, predecessor):
    other_year = factories.MatterFactory(
        owner=specialist, reference_year=2024, reference_number=999
    )
    matter = _continues_under(predecessor, "2026_999")

    facts = register_facts_for(matter, specialist)
    assert facts.continues_under_id is None
    body = _page(client, specialist, matter)
    assert "teema 2026_999" in body
    assert str(other_year.pk) not in body


def test_a_restricted_successor_is_plain_text_to_a_reader_who_cannot_open_it(
    client, specialist, reader, predecessor
):
    hidden = factories.MatterFactory(
        owner=specialist,
        reference_year=2026,
        reference_number=999,
        visibility=Visibility.RESTRICTED,
    )
    matter = _continues_under(predecessor, "2026_999")

    assert register_facts_for(matter, reader).continues_under_id is None
    body = _page(client, reader, matter)
    assert "teema 2026_999" in body
    assert str(hidden.pk) not in body

    # Its owner may open it, and gets the link.
    assert register_facts_for(matter, specialist).continues_under_id == hidden.pk


def test_a_deleted_successor_is_plain_text(specialist, predecessor):
    from app.matters.deletion import delete_matter

    gone = factories.MatterFactory(owner=specialist, reference_year=2026, reference_number=999)
    delete_matter(matter=gone, actor=specialist)
    matter = _continues_under(predecessor, "2026_999")

    assert register_facts_for(matter, specialist).continues_under_id is None


@pytest.mark.parametrize("written", ["999", "2026-999", "2026_", "_999", "abc", "2026_999_1"])
def test_a_reference_that_is_not_year_and_number_is_plain_text(specialist, predecessor, written):
    factories.MatterFactory(owner=specialist, reference_year=2026, reference_number=999)
    matter = _continues_under(predecessor, written)

    facts = register_facts_for(matter, specialist)
    assert facts.continues_under_id is None
    assert facts.continues_under_reference == written
