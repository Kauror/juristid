"""Held OneNote readings: one audited decision per item (live-sync round 2, agent C).

Invented captures, pages, files and Teemad only. A shared page's file is one
item: associated with ONE Teema (a reason is mandatory), rejected, or kept on
hold; a held later reading with other bytes may become the next version of the
Document the Teema already has, never an overwrite. Every decision is an
append-only row; the captured rows are never altered.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, connection, transaction
from django.forms.models import model_to_dict
from django.test import Client
from django.urls import reverse

from app.accounts.enums import UserRole
from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.core.errors import ImmutableRecordError
from app.documents.models import Document, DocumentVersion
from app.legacy_import import onenote_capture as oc
from app.legacy_import import onenote_coverage as cov
from app.legacy_import import source_originals
from app.legacy_import.onenote_coverage_models import (
    HoldDecisionKind,
    HoldScope,
    OneNoteHoldDecision,
    OneNoteResourceRevision,
    PageState,
    RevisionState,
)
from app.legacy_import.source_pages import (
    LegacySourcePage,
    LegacySourceResource,
    LegacySourceResourceImport,
    ResourceImportState,
)
from tests import factories
from tests.admin_session import apply_admin_gate, choose, full_admin, technical_admin, verified
from tests.refusals import refused
from tests.test_onenote_capture_changes import GUID, OTHER_GUID, T1, build, f, run
from tests.test_onenote_capture_import import register_teema

pytestmark = pytest.mark.django_db

REASON = "Lehe tekst nimetab selle teema eelnõu"
SHARED_BYTES = b"jagatud faili sisu"
NEW_BYTES = b"jagatud faili uus sisu"


@pytest.fixture
def world(tmp_path, settings):
    settings.ONENOTE_CAPTURE_IMPORT_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    settings.HISTORICAL_SOURCE_ROOT = str(tmp_path)
    source_originals.clear_cache()
    a, b, c = (register_teema(f"2026_{n}") for n in (61, 62, 63))
    root = build(
        tmp_path / "c1",
        "cap-1",
        [
            {
                "tag": "001",
                "guid": GUID,
                "matters": [a, b],
                "files": [f("{S1}", "jagatud.pdf", T1, data=SHARED_BYTES)],
            },
            {
                "tag": "002",
                "guid": OTHER_GUID,
                "matters": [c],
                "files": [f("{E1}", "tyhi.txt", T1, data=b""), f("{C1}", "kiri.pdf", T1)],
            },
        ],
    )
    run(root)
    # Only the one-off import container runs with real data allowed; the pages do not.
    settings.REAL_DATA_ALLOWED = False
    yield {"a": a, "b": b, "c": c, "root": root, "tmp": tmp_path, "settings": settings}
    source_originals.clear_cache()


@pytest.fixture
def people():
    return {"admin": technical_admin()[0], "reader": factories.UserFactory()}


def _item(key_prefix: str, viewer) -> cov.HeldItem:
    items, _hidden, _counts = cov.held_items(viewer=viewer)
    (item,) = [i for i in items if i.key.startswith(key_prefix)]
    return cov.held_item(item.key, viewer=viewer)


def _decide(item, people, decision, *, reason=REASON, token=None, link_id=""):
    return cov.decide_hold(
        item.key,
        actor=people["admin"],
        viewer=people["reader"],
        decision=decision,
        reason=reason,
        token=token if token is not None else cov.item_token(item),
        link_id=link_id,
    )


def _link_of(item, matter) -> str:
    return next(t.link_id for t in item.teemad if t.matter_id == str(matter.pk))


def _later(world, capture_id, data):
    root = build(
        world["tmp"] / capture_id,
        capture_id,
        [
            {
                "tag": "001",
                "guid": GUID,
                "matters": [world["a"], world["b"]],
                "files": [f("{S1}", "jagatud.pdf", T1, data=data)],
            }
        ],
    )
    world["settings"].REAL_DATA_ALLOWED = True
    run(root)
    world["settings"].REAL_DATA_ALLOWED = False
    return root


# -- the list ---------------------------------------------------------------------------


def test_items_are_one_per_shared_file_and_counted_by_why_they_wait(world, people):
    items, hidden, counts = cov.held_items(viewer=people["reader"])
    by_scope = {i.scope: i for i in items}
    assert hidden == 0
    assert set(by_scope) == {HoldScope.SHARED_FILE, HoldScope.HELD_IMPORT}
    shared = by_scope[HoldScope.SHARED_FILE]
    assert len(shared.rows) == 2  # one held row per Teema, ONE item to decide
    assert {t.matter_id for t in shared.teemad} == {str(world["a"].pk), str(world["b"].pk)}
    assert by_scope[HoldScope.HELD_IMPORT].reason == oc.HOLD_EMPTY_FILE
    assert counts == {cov.HOLD_CATEGORY_OWNERSHIP: 1, cov.HOLD_CATEGORY_OTHER: 1}
    assert cov.held_counts()["open_shared_rows"] == 2


def test_an_item_shows_both_identities_and_whether_the_bytes_are_there(world, people):
    item = _item("shared:", people["reader"])
    assert item.bytes_available is True and item.bytes_note == ""
    assert item.original["sha256"] == item.resource.sha256
    assert item.options == [
        HoldDecisionKind.ASSOCIATE,
        HoldDecisionKind.REJECT,
        HoldDecisionKind.KEEP_HOLDING,
    ]
    empty = _item("import:", people["reader"])
    assert empty.options == [HoldDecisionKind.REJECT, HoldDecisionKind.KEEP_HOLDING]


def test_a_persona_that_cannot_open_the_teemad_sees_a_count_and_no_item(world, people):
    items, hidden, _counts = cov.held_items(viewer=None)
    assert items == [] and hidden == 2
    key = _item("shared:", people["reader"]).key
    with pytest.raises(cov.HoldNotFound):
        cov.held_item(key, viewer=None)


# -- associating a shared page's file -----------------------------------------------------


def test_associating_puts_the_file_on_one_teema_only_and_settles_the_item(world, people):
    item = _item("shared:", people["reader"])
    resource_before = model_to_dict(LegacySourceResource.objects.get(pk=item.resource.pk))
    page_before = model_to_dict(LegacySourcePage.objects.get(pk=item.page.pk))
    b_row = LegacySourceResourceImport.objects.get(
        resource=item.resource, matter_source_page__matter=world["b"]
    )
    b_before = model_to_dict(b_row)

    decision = _decide(item, people, HoldDecisionKind.ASSOCIATE, link_id=_link_of(item, world["a"]))

    a_row = LegacySourceResourceImport.objects.get(
        resource=item.resource, matter_source_page__matter=world["a"]
    )
    assert a_row.state == ResourceImportState.IMPORTED and a_row.error_code == ""
    document = a_row.document
    assert document.matter_id == world["a"].pk and document.created_by_id is None
    assert a_row.document_version.sha256 == item.resource.sha256
    assert a_row.document_version.uploaded_by_id is None
    # Never every Teema: B keeps its held row and gets no Document.
    assert model_to_dict(LegacySourceResourceImport.objects.get(pk=b_row.pk)) == b_before
    assert not Document.objects.filter(matter=world["b"]).exists()
    # The captured rows are untouched.
    assert model_to_dict(LegacySourceResource.objects.get(pk=item.resource.pk)) == resource_before
    assert model_to_dict(LegacySourcePage.objects.get(pk=item.page.pk)) == page_before
    # The audited record.
    assert decision.final and decision.actor == people["admin"] and decision.reason == REASON
    assert decision.document == document and str(decision.matter_source_page_id) == str(
        a_row.matter_source_page_id
    )
    assert decision.observed["rows"][0]["error_code"] == oc.HOLD_SHARED_PAGE
    assert "halduri otsusega" in document.provenance_note
    audit = SecurityAuditEvent.objects.filter(event_type=SecurityEventType.IMPORT_RUN).last()
    assert audit.detail["action"] == "ONENOTE_HOLD_DECIDED" and audit.actor == people["admin"]


def test_coverage_and_verification_reflect_the_decided_item(world, people):
    before = cov.coverage_summary()["operational"]
    assert before["attachments_held"][oc.HOLD_SHARED_PAGE] == 2
    item = _item("shared:", people["reader"])
    _decide(item, people, HoldDecisionKind.ASSOCIATE, link_id=_link_of(item, world["a"]))
    after = cov.coverage_summary()["operational"]
    assert oc.HOLD_SHARED_PAGE not in after["attachments_held"]
    assert after["attachments_held_decided"] == {oc.HOLD_SHARED_PAGE: 1}  # B's row, settled
    assert after["attachments_imported"] == before["attachments_imported"] + 1
    assert cov.page_states([GUID])[GUID]["state"] == PageState.IMPORTED
    assert cov.held_counts()["decided_by_decision"] == {HoldDecisionKind.ASSOCIATE: 1}
    report = oc.verify(oc.load_capture(world["root"]))
    assert report["problems"] == []
    assert report["counts"]["associated_by_review"] == 1


def test_an_association_needs_a_reason_and_one_of_the_pages_teemad(world, people):
    item = _item("shared:", people["reader"])
    other = _item("import:", people["reader"]).teemad[0].link_id  # Teema C's link
    with refused(cov.HOLD_REASON_REQUIRED):
        _decide(item, people, HoldDecisionKind.ASSOCIATE, reason=" lühike ", link_id=other)
    with refused(cov.HOLD_NOT_A_CANDIDATE):
        _decide(item, people, HoldDecisionKind.ASSOCIATE, link_id=other)
    with refused(cov.HOLD_NOT_A_CANDIDATE):
        _decide(item, people, HoldDecisionKind.ASSOCIATE, link_id="")
    assert not OneNoteHoldDecision.objects.exists()
    assert not Document.objects.filter(matter__in=[world["a"], world["b"]]).exists()


def test_a_decision_the_item_does_not_offer_is_refused(world, people):
    empty = _item("import:", people["reader"])
    with refused(cov.HOLD_NOT_OFFERED):
        _decide(empty, people, HoldDecisionKind.ASSOCIATE, link_id=empty.teemad[0].link_id)
    with refused(cov.HOLD_NOT_OFFERED):
        _decide(empty, people, "APPROVE_ALL")
    assert not OneNoteHoldDecision.objects.exists()


def test_missing_original_bytes_refuse_the_association_and_write_nothing(world, people):
    item = _item("shared:", people["reader"])
    world["settings"].HISTORICAL_SOURCE_ROOT = str(world["tmp"] / "tühi")
    source_originals.clear_cache()
    assert cov.held_item(item.key, viewer=people["reader"]).bytes_available is False
    with refused(cov.HOLD_BYTES_UNAVAILABLE):
        _decide(item, people, HoldDecisionKind.ASSOCIATE, link_id=_link_of(item, world["a"]))
    assert not OneNoteHoldDecision.objects.exists()
    assert (
        LegacySourceResourceImport.objects.filter(
            resource=item.resource, state=ResourceImportState.SKIPPED
        ).count()
        == 2
    )
    refusal = SecurityAuditEvent.objects.filter(event_type=SecurityEventType.IMPORT_RUN).last()
    assert refusal.detail["action"] == "ONENOTE_HOLD_DECISION_REFUSED"
    assert refusal.succeeded is False


# -- double and stale decisions -----------------------------------------------------------


def test_a_settled_item_refuses_a_second_decision(world, people):
    item = _item("shared:", people["reader"])
    _decide(item, people, HoldDecisionKind.REJECT)
    again = cov.held_item(item.key, viewer=people["reader"])
    assert again.options == []
    with refused(cov.HOLD_ALREADY_DECIDED):
        _decide(again, people, HoldDecisionKind.ASSOCIATE, link_id=_link_of(again, world["a"]))
    assert OneNoteHoldDecision.objects.count() == 1
    assert not Document.objects.filter(matter__in=[world["a"], world["b"]]).exists()


def test_the_database_holds_one_settling_decision_per_item(world, people):
    first = _decide(_item("import:", people["reader"]), people, HoldDecisionKind.REJECT)
    with pytest.raises(IntegrityError), transaction.atomic():
        OneNoteHoldDecision.objects.create(
            item_key=first.item_key,
            scope=first.scope,
            decision=HoldDecisionKind.REJECT,
            final=True,
            resource=first.resource,
            resource_import=first.resource_import,
            matter_source_page=first.matter_source_page,
            reason=REASON,
            actor=people["admin"],
            item_token="x" * 64,
        )


def test_an_item_that_changed_since_it_was_shown_is_refused(world, people):
    item = _item("shared:", people["reader"])
    shown = cov.item_token(item)
    _decide(item, people, HoldDecisionKind.KEEP_HOLDING)  # someone else, meanwhile
    with refused(cov.HOLD_STALE):
        _decide(
            item,
            people,
            HoldDecisionKind.ASSOCIATE,
            token=shown,
            link_id=_link_of(item, world["a"]),
        )
    # Keeping on hold may be recorded again, against the fresh state.
    fresh = cov.held_item(item.key, viewer=people["reader"])
    _decide(fresh, people, HoldDecisionKind.KEEP_HOLDING)
    assert OneNoteHoldDecision.objects.filter(final=False).count() == 2
    assert cov.held_counts()["open"] == 2


def test_decisions_are_written_once(world, people):
    decision = _decide(_item("import:", people["reader"]), people, HoldDecisionKind.REJECT)
    decision.reason = "muudetud põhjendus"
    with pytest.raises(ImmutableRecordError):
        decision.save()
    with pytest.raises(Exception, match="Append-only table"):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("UPDATE legacy_import_onenoteholddecision SET reason = 'x'")


def test_rejecting_a_held_import_settles_it_in_the_coverage(world, people):
    _decide(_item("import:", people["reader"]), people, HoldDecisionKind.REJECT)
    operational = cov.coverage_summary()["operational"]
    assert oc.HOLD_EMPTY_FILE not in operational["attachments_held"]
    assert operational["attachments_held_decided"] == {oc.HOLD_EMPTY_FILE: 1}
    assert cov.page_states([OTHER_GUID])[OTHER_GUID]["state"] == PageState.IMPORTED


# -- a held later reading -----------------------------------------------------------------


def test_accepting_a_held_reading_adds_the_next_version_and_never_overwrites(world, people):
    shared = _item("shared:", people["reader"])
    _decide(shared, people, HoldDecisionKind.ASSOCIATE, link_id=_link_of(shared, world["a"]))
    document = Document.objects.get(matter=world["a"])
    first = model_to_dict(DocumentVersion.objects.get(document=document))
    _later(world, "cap-2", NEW_BYTES)
    held = OneNoteResourceRevision.objects.filter(state=RevisionState.HELD)
    assert held.count() == 2  # A's and B's links: a shared page's new bytes wait
    before = cov.coverage_summary()["operational"]["revisions_held"]

    items, _hidden, _counts = cov.held_items(viewer=people["reader"])
    readings = {i.teemad[0].matter_id: i for i in items if i.scope == HoldScope.HELD_REVISION}
    on_a = cov.held_item(readings[str(world["a"].pk)].key, viewer=people["reader"])
    assert on_a.version_target == document and on_a.later["sha256"] != on_a.original["sha256"]
    decision = _decide(on_a, people, HoldDecisionKind.ACCEPT_REVISION)

    versions = list(DocumentVersion.objects.filter(document=document).order_by("created_at"))
    assert len(versions) == 2
    assert model_to_dict(versions[0]) == first  # the earlier version, exactly
    assert versions[1].sha256 == on_a.revision.sha256 == decision.document_version.sha256
    assert OneNoteResourceRevision.objects.get(pk=on_a.revision.pk).state == RevisionState.HELD
    after = cov.coverage_summary()["operational"]
    assert after["revisions_held"] == before - 1 and after["revisions_held_decided"] == 1

    # B never had the file as a Document: nothing to version.
    on_b = cov.held_item(readings[str(world["b"].pk)].key, viewer=people["reader"])
    with refused(cov.HOLD_NO_DOCUMENT):
        _decide(on_b, people, HoldDecisionKind.ACCEPT_REVISION)


def test_a_reading_with_a_later_one_beside_it_cannot_be_accepted(world, people):
    shared = _item("shared:", people["reader"])
    _decide(shared, people, HoldDecisionKind.ASSOCIATE, link_id=_link_of(shared, world["a"]))
    _later(world, "cap-2", NEW_BYTES)
    items, _hidden, _counts = cov.held_items(viewer=people["reader"])
    (older,) = [
        i
        for i in items
        if i.scope == HoldScope.HELD_REVISION and i.teemad[0].matter_id == str(world["a"].pk)
    ]
    shown = cov.held_item(older.key, viewer=people["reader"])
    _later(world, "cap-3", b"veel uuem sisu")
    with refused(cov.HOLD_STALE):  # the item changed after it was shown
        _decide(shown, people, HoldDecisionKind.ACCEPT_REVISION)
    with refused(cov.HOLD_SUPERSEDED):
        _decide(
            cov.held_item(older.key, viewer=people["reader"]),
            people,
            HoldDecisionKind.ACCEPT_REVISION,
        )
    assert DocumentVersion.objects.filter(document__matter=world["a"]).count() == 1


# -- the page -----------------------------------------------------------------------------


@pytest.fixture
def gate(settings):
    apply_admin_gate(settings)
    return settings


@pytest.fixture
def signed(client, gate, world, people):
    admin, secret = technical_admin()
    verified(client, admin, secret)
    choose(client, people["reader"])
    return {"client": client, "admin": admin}


URL = "haldus:onenote_holds"


def test_the_administrator_lists_opens_and_decides_one_item(signed, world, people):
    client = signed["client"]
    page = client.get(reverse(URL))
    assert page.status_code == 200
    body = page.content.decode()
    assert "jagatud.pdf" in body and world["a"].display_reference in body
    assert "Kuuluvus ebaselge" in body
    item = _item("shared:", people["reader"])
    detail = client.get(reverse(URL), {"kirje": item.key})
    text = detail.content.decode()
    assert (
        detail.status_code == 200 and "Seotud ühe teemaga" in text and item.resource.sha256 in text
    )
    response = client.post(
        reverse(URL),
        {
            "kirje": item.key,
            "decision": HoldDecisionKind.ASSOCIATE,
            "teema": _link_of(item, world["a"]),
            "reason": REASON,
            "token": detail.context["token"],
        },
    )
    assert response.status_code == 302
    decision = OneNoteHoldDecision.objects.get()
    assert decision.actor == signed["admin"]  # the verified administrator, not the persona
    assert Document.objects.filter(matter=world["a"]).count() == 1
    again = client.post(
        reverse(URL),
        {
            "kirje": item.key,
            "decision": HoldDecisionKind.REJECT,
            "reason": REASON,
            "token": detail.context["token"],
        },
        follow=True,
    )
    assert cov.HOLD_ALREADY_DECIDED in again.content.decode()
    assert OneNoteHoldDecision.objects.count() == 1
    assert "Otsustatud" in client.get(reverse(URL), {"otsustatud": "1"}).content.decode()


def test_reading_the_pages_writes_nothing(signed, world, people):
    client = signed["client"]
    before = (OneNoteHoldDecision.objects.count(), Document.objects.count())
    item = _item("shared:", people["reader"])
    for params in ({}, {"kirje": item.key}, {"liik": "omanik"}, {"otsustatud": "1"}):
        assert client.get(reverse(URL), params).status_code == 200
        assert client.head(reverse(URL), params).status_code == 200
    assert (OneNoteHoldDecision.objects.count(), Document.objects.count()) == before


def test_every_decision_post_needs_the_csrf_token(signed, world, people):
    strict = Client(enforce_csrf_checks=True)
    strict.cookies = signed["client"].cookies
    item = _item("import:", people["reader"])
    data = {
        "kirje": item.key,
        "decision": "REJECT",
        "reason": REASON,
        "token": cov.item_token(item),
    }
    assert strict.post(reverse(URL), data).status_code == 403
    assert not OneNoteHoldDecision.objects.exists()


def test_anyone_but_a_verified_technical_administrator_gets_nothing(client, gate, world, people):
    from tests.admin_session import pass_gate

    item = _item("import:", people["reader"])
    data = {
        "kirje": item.key,
        "decision": "REJECT",
        "reason": REASON,
        "token": cov.item_token(item),
    }
    assert client.get(reverse(URL)).status_code in (302, 404)
    assert client.post(reverse(URL), data).status_code in (302, 404)
    pass_gate(client)
    choose(client, people["reader"])  # a business persona without the step-up
    assert client.get(reverse(URL)).status_code == 404
    assert client.post(reverse(URL), data).status_code == 404
    head, secret = full_admin(role=UserRole.DEPARTMENT_HEAD)
    other = Client()
    verified(other, head, secret)  # verified, but not technical
    assert other.get(reverse(URL)).status_code == 404
    assert other.post(reverse(URL), data).status_code == 404
    assert not OneNoteHoldDecision.objects.exists()


def test_an_administrator_without_a_persona_sees_counts_and_no_item(client, gate, world, people):
    admin, secret = technical_admin()
    verified(client, admin, secret)
    body = client.get(reverse(URL)).content.decode()
    assert "jagatud.pdf" not in body and world["a"].display_reference not in body
    assert "mida sinu valitud isik ei näe" in body
    item = _item("shared:", people["reader"])
    assert client.get(reverse(URL), {"kirje": item.key}).status_code == 404
    data = {
        "kirje": item.key,
        "decision": "REJECT",
        "reason": REASON,
        "token": cov.item_token(item),
    }
    assert client.post(reverse(URL), data).status_code == 404
    assert not OneNoteHoldDecision.objects.exists()
