"""Reviewer E (live-sync round 2): adversarial checks on the held-reading review path.

Synthetic data only (the same sealed-capture builder as tests/test_ls2_c_onenote_holds.py).
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.core.enums import Visibility
from app.documents.models import Document
from app.legacy_import import onenote_coverage as cov
from app.legacy_import import source_originals
from app.legacy_import.onenote_coverage_models import HoldDecisionKind, OneNoteHoldDecision
from app.matters.deletion import permanently_delete_matter, plan_matter_deletion
from app.matters.models import Matter
from tests import factories
from tests.admin_session import apply_admin_gate, choose, technical_admin, verified
from tests.test_onenote_capture_changes import GUID, OTHER_GUID, T1, build, f, run
from tests.test_onenote_capture_import import register_teema

pytestmark = pytest.mark.django_db

REASON = "Lehe tekst nimetab selle teema eelnõu"


def _world(tmp_path, settings, *, shared_files: int = 1, restricted_b: bool = False):
    settings.ONENOTE_CAPTURE_IMPORT_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    settings.HISTORICAL_SOURCE_ROOT = str(tmp_path)
    source_originals.clear_cache()
    a, b, c = (register_teema(f"2026_{n}") for n in (71, 72, 73))
    if restricted_b:
        Matter.objects.filter(pk=b.pk).update(visibility=Visibility.RESTRICTED)
    files = [
        f("{S" + str(n) + "}", f"jagatud{n}.pdf", T1, data=f"jagatud {n}".encode())
        for n in range(1, shared_files + 1)
    ]
    root = build(
        tmp_path / "c1",
        "cap-1",
        [
            {"tag": "001", "guid": GUID, "matters": [a, b], "files": files},
            {
                "tag": "002",
                "guid": OTHER_GUID,
                "matters": [c],
                "files": [f("{E1}", "tyhi.txt", T1, data=b"")],
            },
        ],
    )
    run(root)
    settings.REAL_DATA_ALLOWED = False
    return {"a": a, "b": b, "c": c}


@pytest.fixture
def reader():
    return factories.UserFactory()


@pytest.fixture
def admin():
    return technical_admin()[0]


def _shared(viewer):
    items, _h, _c = cov.held_items(viewer=viewer)
    return [cov.held_item(i.key, viewer=viewer) for i in items if i.key.startswith("shared:")]


def test_a_shared_item_on_a_restricted_teema_is_hidden_and_cannot_be_decided(
    tmp_path, settings, reader, admin
):
    world = _world(tmp_path, settings, restricted_b=True)
    # Every business role reads RESTRICTED Teemad (ADR 0181: READER included); only a
    # non-business persona (technical ADMINISTRATOR) reads none, so it is the outsider.
    from app.accounts.enums import UserRole

    assert (
        Matter.objects.visible_to(factories.UserFactory(role=UserRole.READER))
        .filter(pk=world["b"].pk)
        .exists()
    )
    reader = factories.AdministratorFactory()
    assert not Matter.objects.visible_to(reader).filter(pk=world["b"].pk).exists()
    items, hidden, _counts = cov.held_items(viewer=reader)
    assert items == []
    assert hidden == 2
    # The key is guessable from any other source (the resource id): still a 404.
    from app.legacy_import.source_pages import LegacySourceResourceImport

    row = LegacySourceResourceImport.objects.filter(matter_source_page__matter=world["a"]).first()
    key = f"shared:{row.resource_id}"
    with pytest.raises(cov.HoldNotFound):
        cov.held_item(key, viewer=reader)
    with pytest.raises(cov.HoldNotFound):
        cov.decide_hold(
            key,
            actor=admin,
            viewer=reader,
            decision=HoldDecisionKind.ASSOCIATE,
            reason=REASON,
            token="x",  # noqa: S106
            link_id=str(row.matter_source_page_id),
        )
    assert not OneNoteHoldDecision.objects.exists()
    assert not Document.objects.filter(matter=world["a"]).exists()


def test_associate_with_a_link_of_another_page_is_refused(tmp_path, settings, reader, admin):
    world = _world(tmp_path, settings)
    (item,) = _shared(reader)
    from app.legacy_import.source_pages import MatterSourcePage

    foreign = MatterSourcePage.objects.get(matter=world["c"])
    with pytest.raises(cov.HoldDecisionRefused) as caught:
        cov.decide_hold(
            item.key,
            actor=admin,
            viewer=reader,
            decision=HoldDecisionKind.ASSOCIATE,
            reason=REASON,
            token=cov.item_token(item),
            link_id=str(foreign.pk),
        )
    assert caught.value.code == "NOT_A_CANDIDATE"
    assert not Document.objects.filter(matter__in=[world["a"], world["b"], world["c"]]).exists()


def test_an_associated_file_is_dated_by_onenote_not_by_the_decision(
    tmp_path, settings, reader, admin
):
    from app.matters.timeline import imported_document_versions

    world = _world(tmp_path, settings)
    (item,) = _shared(reader)
    link = next(t.link_id for t in item.teemad if t.matter_id == str(world["a"].pk))
    decision = cov.decide_hold(
        item.key,
        actor=admin,
        viewer=reader,
        decision=HoldDecisionKind.ASSOCIATE,
        reason=REASON,
        token=cov.item_token(item),
        link_id=link,
    )
    found = imported_document_versions(world["a"])
    version = found[decision.document_version_id]
    assert version.added_at is not None
    assert version.added_at.date() == dt.date(2021, 8, 30)
    assert version.group == "day:2021-08-30"


def test_a_teema_that_received_an_associated_file_can_still_be_deleted_permanently(
    tmp_path, settings, reader, admin
):
    """Migration 0029 says a Teema's permanent deletion walks through this table."""
    world = _world(tmp_path, settings)
    (item,) = _shared(reader)
    link = next(t.link_id for t in item.teemad if t.matter_id == str(world["a"].pk))
    cov.decide_hold(
        item.key,
        actor=admin,
        viewer=reader,
        decision=HoldDecisionKind.ASSOCIATE,
        reason=REASON,
        token=cov.item_token(item),
        link_id=link,
    )
    matter = Matter.objects.get(pk=world["a"].pk)
    plan = plan_matter_deletion(matter)
    head = factories.DepartmentHeadFactory()
    permanently_delete_matter(matter=matter, actor=head, confirmation=plan.digest)
    assert not Document.objects.filter(matter_id=world["a"].pk).exists()


def test_a_teema_whose_held_item_was_rejected_can_still_be_deleted_permanently(
    tmp_path, settings, reader, admin
):
    world = _world(tmp_path, settings)
    (item,) = _shared(reader)
    cov.decide_hold(
        item.key,
        actor=admin,
        viewer=reader,
        decision=HoldDecisionKind.REJECT,
        reason=REASON,
        token=cov.item_token(item),
    )
    matter = Matter.objects.get(pk=world["b"].pk)
    plan = plan_matter_deletion(matter)
    head = factories.DepartmentHeadFactory()
    permanently_delete_matter(matter=matter, actor=head, confirmation=plan.digest)
    assert OneNoteHoldDecision.objects.count() == 1  # a shared-file REJECT names no link


@pytest.mark.parametrize("n", [3, 12])
def test_the_held_list_page_query_count_does_not_grow_with_items(
    tmp_path, settings, client, reader, n
):
    _world(tmp_path, settings, shared_files=n)
    apply_admin_gate(settings)
    admin, secret = technical_admin()
    verified(client, admin, secret)
    choose(client, reader)
    url = reverse("haldus:onenote_holds")
    client.get(url)  # warm
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url)
    assert response.status_code == 200
    body = response.content.decode()
    assert body.count("jagatud") >= n
    # Measured 8 for both n=3 and n=12 (no per-item query); a fixed bound keeps it so.
    assert len(ctx.captured_queries) <= 10, len(ctx.captured_queries)


def test_a_teema_linked_to_the_shared_page_after_a_decision_is_never_asked_about(
    tmp_path, settings, reader, admin
):
    """A shared file's final decision is keyed by the resource only. A Teema that
    links to the page later gets a held row under the same resource: is it reviewed?"""
    from app.legacy_import.source_pages import LegacySourceResourceImport

    world = _world(tmp_path, settings)
    (item,) = _shared(reader)
    cov.decide_hold(
        item.key,
        actor=admin,
        viewer=reader,
        decision=HoldDecisionKind.REJECT,
        reason=REASON,
        token=cov.item_token(item),
    )
    d = register_teema("2026_74")
    settings.REAL_DATA_ALLOWED = True
    root = build(
        tmp_path / "c2",
        "cap-2",
        [
            {
                "tag": "001",
                "guid": GUID,
                "matters": [world["a"], world["b"], d],
                "files": [f("{S1}", "jagatud1.pdf", T1, data=b"jagatud 1")],
            }
        ],
    )
    run(root)
    settings.REAL_DATA_ALLOWED = False
    rows = LegacySourceResourceImport.objects.filter(
        matter_source_page__matter=d, resource=item.resource
    )
    assert list(rows.values_list("state", "error_code")) == [
        ("SKIPPED", "SHARED_PAGE_AWAITING_REVIEW")
    ]
    decided = [
        i for i in cov.held_items(viewer=reader, decided=True)[0] if i.key.startswith("shared:")
    ]
    # The REJECT was recorded with A and B as the only candidates ...
    assert sorted(decided[0].final.observed["teemad"]) == sorted(
        [str(world["a"].pk), str(world["b"].pk)]
    )
    # ... yet D's held row now reads as settled by it and is never offered for review.
    # EXPECTED (fails today): the file is an open item for D's new link.
    open_items = [i for i in cov.held_items(viewer=reader)[0] if i.key.startswith("shared:")]
    assert any(str(d.pk) in [t.matter_id for t in i.teemad] for i in open_items), (
        "a Teema linked after the decision is never asked about"
    )
