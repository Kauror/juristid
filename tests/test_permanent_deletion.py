"""Permanent deletion of a Teema is the department head's (owner decision D6).

docs/adr/0165. QA DC-04 found that any SPECIALIST could delete a Teema, sent
opinions and their final evidence included. The approved rule:

* only somebody holding `matter.delete_permanently` — the DEPARTMENT_HEAD by
  default, or a business writer granted it by name — may delete; a SPECIALIST
  and a technical ADMINISTRATOR do not inherit it;
* «Kustuta teema» → a confirmation dialog naming what goes → «Seda tegevust ei
  saa tagasi võtta.» → «Loobu» / «Kustuta jäädavalt»;
* server-side on every verb, never by GET, never twice, never on a stale
  confirmation;
* one transaction for the rows, bytes only after the commit and never bytes
  something else still names;
* a minimal audit trace without the deleted business content.

Every record here is synthetic.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone

from app.accounts.enums import Capability
from app.audit.enums import ChangeEventType, SecurityEventType
from app.audit.models import ChangeEvent, SecurityAuditEvent
from app.core.authorization import has_capability
from app.documents.models import Document, DocumentVersion
from app.documents.services import evidence_storage
from app.matters import deletion
from app.matters.deletion import (
    DELETION_CONFIRMATION_MISSING,
    DELETION_CONFIRMATION_STALE,
    TOMBSTONE_TITLE,
    DeletionConfirmationStale,
    delete_matter,
    may_delete_permanently,
    permanently_delete_matter,
    plan_matter_deletion,
)
from app.matters.models import Matter
from app.matters.services import add_entry
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from tests import factories
from tests.follow_ups import day, send_koja_arvamus
from tests.gate import apply_shared_gate
from tests.refusals import refused

pytestmark = pytest.mark.django_db

GRANT = {Capability.DELETE_MATTERS.value: "allow"}
DENY = {Capability.DELETE_MATTERS.value: "deny"}
TITLE = "Jäädavalt kustutatav sünteetiline teema"
SUMMARY = "Sünteetiline lühikokkuvõte, mida tombstone ei tohi hoida."


def delete_url(matter) -> str:
    return reverse("matters:matter_delete", kwargs={"pk": matter.pk})


@pytest.fixture
def head(db):
    return factories.DepartmentHeadFactory()


@pytest.fixture
def doomed(specialist, capture_evidence, pdf_bytes):
    """A Teema with a note, a working file and a sent opinion with final evidence."""
    organisation = factories.OrganisationFactory(name="Sünteetiline ministeerium")
    matter = factories.MatterFactory(owner=specialist, title=TITLE, brief_summary=SUMMARY)
    add_entry(matter=matter, body="Sünteetiline märge", author=specialist)
    capture_evidence(matter, pdf_bytes, "eelnou.pdf", "application/pdf")
    send_koja_arvamus(matter, specialist, [organisation], day(0))
    return Matter.objects.get(pk=matter.pk)


def _stored(key: str) -> bool:
    return evidence_storage().exists(key)


def _digest(matter) -> str:
    return plan_matter_deletion(matter).digest


def _post(client, matter, digest: str | None = None, **extra):
    data = {} if digest is None else {"confirm_digest": digest}
    return client.post(delete_url(matter), data, **extra)


# ---------------------------------------------------------------------------
# Who holds the capability
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("make", "overrides", "holds"),
    [
        (factories.DepartmentHeadFactory, {}, True),
        (factories.DepartmentHeadFactory, DENY, False),
        (factories.UserFactory, {}, False),  # SPECIALIST
        (factories.UserFactory, GRANT, True),  # SPECIALIST granted by name
        (factories.AdministratorFactory, {}, False),
        (factories.AdministratorFactory, GRANT, False),  # never through a grant
        (factories.ReaderFactory, {}, False),
        (factories.ReaderFactory, GRANT, False),
    ],
    ids=[
        "head",
        "head-denied",
        "specialist",
        "specialist-granted",
        "administrator",
        "administrator-granted",
        "reader",
        "reader-granted",
    ],
)
def test_who_holds_the_deletion_capability(make, overrides, holds):
    user = make(capability_overrides=overrides)

    assert may_delete_permanently(user) is holds
    assert has_capability(user, Capability.DELETE_MATTERS) is holds


def test_account_administration_does_not_imply_deletion():
    """`accounts.manage` is a different family; it adds nothing here."""
    admin = factories.UserFactory(
        capability_overrides={"accounts.manage": "allow", "accounts.delegate": "allow"}
    )
    assert not may_delete_permanently(admin)


# ---------------------------------------------------------------------------
# The route, per actor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("make", "overrides", "status"),
    [
        (factories.ReaderFactory, {}, 404),
        (factories.AdministratorFactory, {}, 404),
        (factories.AdministratorFactory, GRANT, 404),
        (factories.UserFactory, {}, 403),
        (factories.DepartmentHeadFactory, DENY, 403),
        (factories.UserFactory, GRANT, 200),
        (factories.DepartmentHeadFactory, {}, 200),
    ],
    ids=[
        "reader",
        "administrator",
        "administrator-granted",
        "specialist",
        "head-denied",
        "specialist-granted",
        "head",
    ],
)
def test_the_confirmation_is_shown_only_to_a_holder(client, doomed, make, overrides, status):
    client.force_login(make(capability_overrides=overrides))

    response = client.get(delete_url(doomed))

    assert response.status_code == status
    assert Matter.objects.filter(pk=doomed.pk).exists()


@pytest.mark.parametrize(
    ("make", "overrides", "status"),
    [
        (factories.ReaderFactory, {}, 404),
        (factories.AdministratorFactory, {}, 404),
        (factories.AdministratorFactory, GRANT, 404),
        (factories.UserFactory, {}, 403),
        (factories.DepartmentHeadFactory, DENY, 403),
    ],
    ids=["reader", "administrator", "administrator-granted", "specialist", "head-denied"],
)
def test_a_crafted_post_with_a_valid_digest_is_refused(client, doomed, make, overrides, status):
    """The digest is not a credential: the server re-authorises the POST."""
    events_before = ChangeEvent.objects.count()
    client.force_login(make(capability_overrides=overrides))

    response = _post(client, doomed, _digest(doomed))

    assert response.status_code == status
    assert Matter.objects.filter(pk=doomed.pk).exists()
    assert Submission.objects.filter(matter=doomed).exists()
    assert ChangeEvent.objects.count() == events_before
    assert not SecurityAuditEvent.objects.filter(event_type=SecurityEventType.MATTER_DELETED)


def test_a_specialists_refused_post_leaves_an_access_refused_trace(client, doomed, specialist):
    client.force_login(specialist)

    _post(client, doomed, _digest(doomed))

    trace = SecurityAuditEvent.objects.get(event_type=SecurityEventType.ACCESS_REFUSED)
    assert trace.actor == specialist
    assert not trace.succeeded
    assert trace.detail == {
        "operation": Capability.DELETE_MATTERS.value,
        "matter_id": str(doomed.pk),
    }


def test_the_service_refuses_a_specialist_too(doomed, specialist):
    """`permanently_delete_matter` is the boundary, not only the view."""
    with pytest.raises(PermissionDenied):
        permanently_delete_matter(matter=doomed, actor=specialist, confirmation=_digest(doomed))
    assert Matter.objects.filter(pk=doomed.pk).exists()


def test_a_gate_visitor_without_a_persona_cannot_reach_it(client, settings, doomed):
    apply_shared_gate(settings)
    from tests.gate import PASSWORD

    assert client.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302

    # Past the door is not somebody: the request is sent to choose a persona,
    # and nothing about the Matter is answered.
    for response in (client.get(delete_url(doomed)), _post(client, doomed, _digest(doomed))):
        assert response.status_code == 302
        assert response.url.startswith(reverse("accounts:choose_persona"))
    assert Matter.objects.filter(pk=doomed.pk).exists()


def test_a_restricted_matter_the_holder_cannot_see_is_a_404(client, specialist):
    """Granted, but not allowed to read it: the record-level gate still answers."""
    from app.core.enums import Visibility

    granted_reader_of_nothing = factories.UserFactory(capability_overrides=GRANT)
    secret = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    client.force_login(granted_reader_of_nothing)

    visible = Matter.objects.visible_to(granted_reader_of_nothing).filter(pk=secret.pk).exists()
    response = client.get(delete_url(secret))

    assert response.status_code == (200 if visible else 404)


# ---------------------------------------------------------------------------
# The dialog
# ---------------------------------------------------------------------------


def test_the_dialog_names_what_goes_and_the_two_answers(client, doomed, head):
    client.force_login(head)

    page = client.get(delete_url(doomed)).content.decode()

    assert 'role="alertdialog"' in page
    assert "Kas kustutada see teema jäädavalt?" in page
    assert "Seda tegevust ei saa tagasi võtta." in page
    # No promise of absolute erasure: the trace and earlier log rows stay.
    assert (
        "Turvalogisse jääb kustutamise kirje (kes, millal, viitenumber ja kogused); "
        "varasemad logikirjed säilivad muutmata."
    ) in page
    assert ">Loobu</a>" in page
    assert ">Kustuta jäädavalt</button>" in page
    assert "1 arvamust" in page
    assert "neist 1 välja saadetud arvamust" in page
    assert "dokumenti" in page
    assert "ka välja saadetud arvamuste ja nende lõplike failidega" in page
    assert f'name="confirm_digest" value="{_digest(doomed)}"' in page
    # It does not claim the backups disappear.
    assert "varukoop" not in page.lower()


def test_cancelling_writes_nothing(client, doomed, head):
    client.force_login(head)
    page = client.get(delete_url(doomed)).content.decode()
    detail = reverse("matters:matter_detail", kwargs={"pk": doomed.pk})
    events = ChangeEvent.objects.count()

    assert f'href="{detail}">Loobu</a>' in page
    assert client.get(detail).status_code == 200
    assert Matter.objects.filter(pk=doomed.pk).exists()
    assert ChangeEvent.objects.count() == events


def test_a_get_never_deletes_even_carrying_the_digest(client, doomed, head):
    client.force_login(head)

    response = client.get(delete_url(doomed), {"confirm_digest": _digest(doomed)})

    assert response.status_code == 200
    assert Matter.objects.filter(pk=doomed.pk).exists()


def test_a_post_without_a_confirmation_is_refused(client, doomed, head):
    client.force_login(head)

    response = _post(client, doomed)

    assert response.status_code == 400
    assert DELETION_CONFIRMATION_MISSING in response.content.decode()
    assert Matter.objects.filter(pk=doomed.pk).exists()


def test_the_header_and_edit_page_offer_deletion_only_to_a_holder(client, doomed, specialist, head):
    detail = reverse("matters:matter_detail", kwargs={"pk": doomed.pk})
    edit = reverse("matters:matter_edit", kwargs={"pk": doomed.pk})

    client.force_login(specialist)
    assert delete_url(doomed) not in client.get(detail).content.decode()
    assert delete_url(doomed) not in client.get(edit).content.decode()
    assert "Teema kustutamine" not in client.get(edit).content.decode()

    client.force_login(head)
    assert delete_url(doomed) in client.get(detail).content.decode()
    assert delete_url(doomed) in client.get(edit).content.decode()


# ---------------------------------------------------------------------------
# Stale confirmations and repeats
# ---------------------------------------------------------------------------


def test_a_confirmation_drawn_before_new_evidence_arrived_is_refused_and_re_asked(
    client, doomed, head, capture_evidence, pdf_bytes
):
    client.force_login(head)
    shown = _digest(doomed)
    capture_evidence(doomed, pdf_bytes + b"%late", "hiline.pdf", "application/pdf")

    response = _post(client, doomed, shown)

    assert response.status_code == 409
    page = response.content.decode()
    assert DELETION_CONFIRMATION_STALE in page
    fresh = _digest(doomed)
    assert fresh != shown
    assert f'name="confirm_digest" value="{fresh}"' in page
    assert "3 dokumenti" in page  # the working file, the opinion's file, the late one
    assert Matter.objects.filter(pk=doomed.pk).exists()
    assert not ChangeEvent.objects.filter(event_type=ChangeEventType.MATTER_DELETED).exists()

    # Answered again against what is there now, it goes ahead.
    assert _post(client, doomed, fresh).status_code == 302
    assert not Matter.objects.filter(pk=doomed.pk).exists()


def test_a_new_note_also_makes_the_confirmation_stale(doomed, head, specialist):
    shown = _digest(doomed)
    add_entry(matter=doomed, body="Hiljem lisatud märge", author=specialist)

    with refused(DELETION_CONFIRMATION_STALE):
        permanently_delete_matter(matter=doomed, actor=head, confirmation=shown)
    assert isinstance(DeletionConfirmationStale(""), Exception)
    assert Matter.objects.filter(pk=doomed.pk).exists()


def test_the_digest_is_stable_while_nothing_changes(doomed):
    assert _digest(doomed) == _digest(Matter.objects.get(pk=doomed.pk))


def test_a_search_refresh_while_the_dialog_is_open_does_not_make_it_stale(doomed, head):
    """Derived rows nobody wrote are not part of what the person confirmed."""
    from app.search.indexing import forget_matter, refresh_matters

    shown = _digest(doomed)
    forget_matter(doomed.pk)
    refresh_matters(Matter.objects.filter(pk=doomed.pk))

    permanently_delete_matter(matter=doomed, actor=head, confirmation=shown)
    assert not Matter.objects.filter(pk=doomed.pk).exists()


def test_a_repeated_post_deletes_and_audits_once(client, doomed, head):
    client.force_login(head)
    digest = _digest(doomed)

    first = _post(client, doomed, digest)
    second = _post(client, doomed, digest)

    assert first.status_code == 302
    assert first.url == reverse("matters:matter_list")
    assert second.status_code == 404
    assert ChangeEvent.objects.filter(event_type=ChangeEventType.MATTER_DELETED).count() == 1
    assert (
        SecurityAuditEvent.objects.filter(event_type=SecurityEventType.MATTER_DELETED).count() == 1
    )


def test_a_second_service_call_on_the_tombstone_writes_nothing(doomed, head):
    digest = _digest(doomed)
    permanently_delete_matter(matter=doomed, actor=head, confirmation=digest)
    events = ChangeEvent.objects.count()
    security = SecurityAuditEvent.objects.count()

    plan = permanently_delete_matter(matter=doomed, actor=head, confirmation=digest)

    assert not plan.owned
    assert ChangeEvent.objects.count() == events
    assert SecurityAuditEvent.objects.count() == security


# ---------------------------------------------------------------------------
# What goes, what stays
# ---------------------------------------------------------------------------


def test_a_sent_opinion_and_its_final_evidence_go(
    client, doomed, head, django_capture_on_commit_callbacks
):
    sent = Submission.objects.get(matter=doomed)
    assert sent.status == SubmissionStatus.SENT
    final_key = sent.final_version.storage_key
    keys = list(
        DocumentVersion.objects.filter(document__matter=doomed).values_list(
            "storage_key", flat=True
        )
    )
    assert final_key in keys and all(_stored(key) for key in keys)
    client.force_login(head)

    with django_capture_on_commit_callbacks(execute=True):
        response = _post(client, doomed, _digest(doomed))

    assert response.status_code == 302
    assert not Submission.objects.filter(pk=sent.pk).exists()
    assert not Document.objects.filter(matter_id=doomed.pk).exists()
    assert not any(_stored(key) for key in keys)


def test_evidence_bytes_another_record_still_names_are_not_deleted(
    doomed, head, django_capture_on_commit_callbacks
):
    """Shared bytes: a holder outside this Teema names the same object."""
    from app.legacy_import.opinion_binary import OpinionArchiveBinary

    version = DocumentVersion.objects.filter(document__matter=doomed).first()
    shared = OpinionArchiveBinary.objects.create(
        sha256=version.sha256,
        size_bytes=version.size_bytes,
        mime_type=version.mime_type,
        storage_key=version.storage_key,
        source_archive_sha256="a" * 64,
        materialized_at=timezone.now(),
    )
    others = list(
        DocumentVersion.objects.filter(document__matter=doomed)
        .exclude(pk=version.pk)
        .values_list("storage_key", flat=True)
    )

    with django_capture_on_commit_callbacks(execute=True):
        permanently_delete_matter(matter=doomed, actor=head, confirmation=_digest(doomed))

    assert OpinionArchiveBinary.objects.filter(pk=shared.pk).exists()
    assert _stored(shared.storage_key)
    assert not any(_stored(key) for key in others)


def test_an_unrelated_matter_is_untouched(
    doomed, head, specialist, capture_evidence, pdf_bytes, django_capture_on_commit_callbacks
):
    organisation = factories.OrganisationFactory()
    bystander = factories.MatterFactory(owner=specialist, title="Kõrvaline teema")
    add_entry(matter=bystander, body="Kõrvaline märge", author=specialist)
    capture_evidence(bystander, pdf_bytes, "korvaline.pdf", "application/pdf")
    send_koja_arvamus(bystander, specialist, [organisation], day(0))
    keys = list(
        DocumentVersion.objects.filter(document__matter=bystander).values_list(
            "storage_key", flat=True
        )
    )
    before = plan_matter_deletion(bystander)

    with django_capture_on_commit_callbacks(execute=True):
        permanently_delete_matter(matter=doomed, actor=head, confirmation=_digest(doomed))

    bystander = Matter.objects.get(pk=bystander.pk)
    assert bystander.title == "Kõrvaline teema"
    assert plan_matter_deletion(bystander).digest == before.digest
    assert Submission.objects.filter(matter=bystander, status=SubmissionStatus.SENT).exists()
    assert all(_stored(key) for key in keys)


def test_a_related_matter_survives_and_only_the_pair_row_goes(doomed, head, specialist):
    from app.related_materials.models import MatterRelation
    from app.related_materials.services import link_related_matters

    other = factories.MatterFactory(owner=specialist, title="Seotud teema")
    link_related_matters(matter=other, other=doomed, actor=specialist)
    assert MatterRelation.objects.exists()

    permanently_delete_matter(matter=doomed, actor=head, confirmation=_digest(doomed))

    assert Matter.objects.filter(pk=other.pk, title="Seotud teema").exists()
    assert not MatterRelation.objects.exists()


# ---------------------------------------------------------------------------
# The audit trace
# ---------------------------------------------------------------------------


def test_the_audit_records_who_when_which_and_how_much_and_nothing_else(client, doomed, head):
    reference = doomed.display_reference
    client.force_login(head)

    _post(client, doomed, _digest(doomed), HTTP_USER_AGENT="pytest-agent")

    change = ChangeEvent.objects.get(event_type=ChangeEventType.MATTER_DELETED)
    security = SecurityAuditEvent.objects.get(event_type=SecurityEventType.MATTER_DELETED)
    assert change.actor == head and change.matter_id == doomed.pk
    assert security.actor == head
    assert security.subject_type == "matters.Matter"
    assert security.subject_id == doomed.pk
    assert security.occurred_at is not None
    assert security.user_agent == "pytest-agent"
    counts = security.detail["counts"]
    assert security.detail["reference"] == reference
    assert counts["documents"] == 2  # the working file and the opinion's final file
    assert counts["opinions"] == 1
    assert counts["sent_opinions"] == 1
    assert counts["entries"] >= 1
    assert counts["evidence_objects"] == 2
    assert change.payload["counts"] == counts

    # No business content in either trace: not the title, not the summary,
    # not a file name.
    for blob in (
        change.summary,
        json.dumps(change.payload, ensure_ascii=False),
        json.dumps(security.detail, ensure_ascii=False),
    ):
        assert TITLE not in blob
        assert SUMMARY not in blob
        assert "eelnou.pdf" not in blob
        assert "Sünteetiline märge" not in blob


def test_the_tombstone_keeps_no_title_or_summary(doomed, head):
    permanently_delete_matter(matter=doomed, actor=head, confirmation=_digest(doomed))

    tombstone = Matter.all_objects.get(pk=doomed.pk)
    assert tombstone.deleted_at is not None
    assert tombstone.deleted_by == head
    assert tombstone.title == TOMBSTONE_TITLE
    for name in deletion.TOMBSTONE_BLANKED_FIELDS:
        assert getattr(tombstone, name) == ""
    # The reference stays: it is the identifier the audit names.
    assert tombstone.display_reference == doomed.display_reference


# ---------------------------------------------------------------------------
# Atomicity and failure recovery
# ---------------------------------------------------------------------------


def test_a_failure_mid_deletion_rolls_everything_back(
    doomed, head, monkeypatch, django_capture_on_commit_callbacks
):
    keys = list(
        DocumentVersion.objects.filter(document__matter=doomed).values_list(
            "storage_key", flat=True
        )
    )
    entries = doomed.entries.count()
    events = ChangeEvent.objects.count()

    def explode(_pk):
        raise RuntimeError("forced failure after the rows were deleted")

    monkeypatch.setattr(deletion, "forget_matter", explode)

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(RuntimeError):
            permanently_delete_matter(matter=doomed, actor=head, confirmation=_digest(doomed))

    assert callbacks == []
    live = Matter.objects.get(pk=doomed.pk)
    assert live.title == TITLE and live.brief_summary == SUMMARY
    assert live.entries.count() == entries
    assert Submission.objects.filter(matter=doomed, status=SubmissionStatus.SENT).exists()
    assert ChangeEvent.objects.count() == events
    assert not SecurityAuditEvent.objects.filter(event_type=SecurityEventType.MATTER_DELETED)
    assert all(_stored(key) for key in keys)


def test_a_byte_deletion_failure_after_commit_is_recorded_for_retry(
    doomed, head, monkeypatch, django_capture_on_commit_callbacks
):
    storage = evidence_storage()
    keys = sorted(
        DocumentVersion.objects.filter(document__matter=doomed).values_list(
            "storage_key", flat=True
        )
    )

    def refuse(_key):
        raise OSError("synthetic storage outage")

    monkeypatch.setattr(type(storage), "delete", lambda self, key: refuse(key))

    with django_capture_on_commit_callbacks(execute=True):
        permanently_delete_matter(matter=doomed, actor=head, confirmation=_digest(doomed))

    # The database is consistent: the Teema is gone and nothing names the bytes.
    assert not Matter.objects.filter(pk=doomed.pk).exists()
    assert not DocumentVersion.objects.filter(storage_key__in=keys).exists()
    pending = SecurityAuditEvent.objects.get(
        event_type=SecurityEventType.MATTER_DELETION_STORAGE_PENDING
    )
    assert not pending.succeeded
    assert pending.subject_id == doomed.pk
    assert sorted(pending.detail["evidence_keys"]) == keys
    assert all(_stored(key) for key in keys)


# ---------------------------------------------------------------------------
# The one door
# ---------------------------------------------------------------------------


def test_nothing_in_the_product_calls_the_unauthorised_operation():
    """`delete_matter` asks no question about the actor; only its gate may call it."""
    root = Path(__file__).resolve().parent.parent / "app"
    callers = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if re.search(r"(?<![\w.])delete_matter\(", path.read_text(encoding="utf-8"))
        and path.name != "deletion.py"
        and "migrations" not in path.parts
    ]
    assert callers == []


def test_the_internal_operation_still_refuses_a_stale_digest(doomed, head):
    with refused(DELETION_CONFIRMATION_STALE):
        delete_matter(matter=doomed, actor=head, expected_digest="0" * 64)
