"""`Kustuta` is offered without walking the deletion plan (AUD-002).

The header used to build the whole `plan_matter_deletion` — 105–109 queries on
a writer's every Teema, documents tab and edit page — to decide whether to draw
one link. Now the offer asks `deletion_may_be_offered`'s few `EXISTS`, and the
plan is built where it is acted on: the confirmation page and `delete_matter`.

Pinned here:

* every blocker the offer asks about withholds the link, and agrees with the
  plan (ADR 0120: an action that can only fail is not offered);
* a deletable Matter is offered the link, and the plan agrees;
* a writer's Teema page never walks the plan, and costs far fewer queries;
* the confirmation page and the route still refuse a blocked deletion.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.matters import deletion
from app.matters.deletion import deletion_may_be_offered, plan_matter_deletion
from app.matters.services import add_entry, edit_entry
from app.related_materials import services as related
from tests import factories
from tests.test_related_materials import _matter, _sent_opinion

pytestmark = pytest.mark.django_db


def _page(client, matter, name: str = "matters:matter_detail") -> str:
    response = client.get(reverse(name, kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


def _offers(client, matter) -> bool:
    return "matterhead__delete" in _page(client, matter)


def _agrees(matter, *, offered: bool) -> None:
    assert deletion_may_be_offered(matter) is offered
    assert plan_matter_deletion(matter).is_blocked is (not offered)


def test_a_deletable_matter_is_offered_kustuta(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    add_entry(matter=matter, author=specialist, body="<p>Märge</p>")

    _agrees(matter, offered=True)
    assert _offers(signed_in, matter)


def test_a_corrected_entry_withholds_the_offer(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    add_entry(matter=matter, author=specialist, body="<p>Märge</p>")
    edit_entry(entry=matter.entries.first(), body="Parandatud", actor=specialist)

    _agrees(matter, offered=False)
    assert not _offers(signed_in, matter)


def test_a_legal_hold_withholds_the_offer(signed_in, specialist):
    from app.documents.models import Document

    document = factories.DocumentFactory(matter=factories.MatterFactory(owner=specialist))
    Document.objects.filter(pk=document.pk).update(legal_hold=True)

    _agrees(document.matter, offered=False)
    assert not _offers(signed_in, document.matter)


def test_a_successor_withholds_the_offer(signed_in, specialist):
    from app.matters.models import Matter

    replaced = factories.MatterFactory(owner=specialist)
    successor = factories.MatterFactory(owner=specialist)
    Matter.objects.filter(pk=successor.pk).update(superseded_by=replaced)

    _agrees(replaced, offered=False)
    assert not _offers(signed_in, replaced)


def test_a_citation_from_another_matter_withholds_the_offer(signed_in, specialist):
    source = _matter(specialist, "Allikteema", number=4301)
    citing = _matter(specialist, "Tsiteeriv teema", number=4302)
    opinion = _sent_opinion(source, "Tsiteeritud arvamus")
    related.add_background_submission(matter=citing, submission=opinion, actor=specialist)

    _agrees(source, offered=False)
    assert not _offers(signed_in, source)


def test_a_writers_teema_page_never_walks_the_plan(signed_in, specialist, monkeypatch):
    matter = factories.MatterFactory(owner=specialist)
    for number in range(3):
        add_entry(matter=matter, author=specialist, body=f"<p>Märge {number}</p>")

    def refuse(_matter):
        raise AssertionError("the Teema page walked the deletion plan")

    monkeypatch.setattr("app.matters.views.plan_matter_deletion", refuse)
    monkeypatch.setattr(deletion, "plan_matter_deletion", refuse)
    assert _offers(signed_in, matter)
    for name in ("matters:matter_detail", "matters:matter_documents"):
        _page(signed_in, matter, name)


def test_the_offer_costs_a_handful_of_queries_not_the_walk(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    for number in range(3):
        add_entry(matter=matter, author=specialist, body=f"<p>Märge {number}</p>")

    with CaptureQueriesContext(connection) as offer:
        deletion_may_be_offered(matter)
    with CaptureQueriesContext(connection) as walk:
        plan_matter_deletion(matter)

    assert len(offer) <= 7
    assert len(walk) > 3 * len(offer)


def test_the_confirmation_page_still_builds_the_plan_and_refuses(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    add_entry(matter=matter, author=specialist, body="<p>Märge</p>")
    edit_entry(entry=matter.entries.first(), body="Parandatud", actor=specialist)

    page = _page(signed_in, matter, "matters:matter_delete")
    refused = signed_in.post(reverse("matters:matter_delete", kwargs={"pk": matter.pk}))

    assert deletion.DELETION_REFUSED.rstrip(".") in page or "ei saa kustutada" in page
    assert refused.status_code in (200, 400, 409)
    matter.refresh_from_db()
    assert matter.deleted_at is None
