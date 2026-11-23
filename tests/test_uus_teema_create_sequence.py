"""What one `Uus teema` save writes, in what order, and what a refusal leaves (HR-13).

`matter_create` used to run the whole creation sequence itself; it now hands it
to `app.matters.intake.create_from_form`. These tests pin the sequence through
the page, so they read the same before and after that move:

* the audit trail of a save — every `ChangeEvent`, its kind, its actor, its
  Teema and which of them share one operation (the files, docs/adr/0121 §6);
* the rows a save creates — Documents, the private note, the relation, the
  sent proposal, the register-watch candidate it closes;
* the form token: claimed and recording its Teema on success, left open by
  every refusal that rolls back (ENG-074, docs/adr/0112, 0166 §2);
* each refusal — a recent duplicate, a replayed form, a failing file, a
  validation error, a late domain refusal — writes nothing at all;
* the number of queries a save costs.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.models.signals import post_save
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.documents.models import Document
from app.matters.models import Matter, MatterPersonalNote
from app.matters.staging import MatterIntakeSession
from app.organisations.models import Organisation
from app.register_watch import candidates
from app.register_watch.models import CandidateItem, CandidateStatus, TopicCandidate, WatchSource
from app.related_materials.models import MatterRelation
from app.related_materials.views import LINK_ON_CREATE_FIELD
from app.submissions.models import Submission
from app.taxonomy.models import LegalInstrumentType
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")
STAGE = reverse("matters:intake_stage")


def _client(user: Any) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _token(client: Client, query: str = "") -> str:
    page = client.get(CREATE + query).content.decode()
    match = re.search(r'name="intake" value="([^"]+)"', page)
    assert match, "the create form rendered no token"
    return match.group(1)


def _pdf(name: str, text: str = "Eelnõu tekst") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, corpus.text_pdf([text]), content_type="application/pdf")


def _et(day: dt.date) -> str:
    return f"{day.day}.{day.month}.{day.year}"


@contextmanager
def _events() -> Iterator[list[ChangeEvent]]:
    """Every ChangeEvent written inside the block, in the order it was written."""
    seen: list[ChangeEvent] = []

    def remember(sender: Any, instance: ChangeEvent, created: bool, **kwargs: Any) -> None:
        if created:
            seen.append(instance)

    post_save.connect(remember, sender=ChangeEvent, weak=False)
    try:
        yield seen
    finally:
        post_save.disconnect(remember, sender=ChangeEvent)


def _trail(events: list[ChangeEvent], names: dict[Any, str]) -> list[tuple[str, str, str, str]]:
    """(Teema, kind, actor, operation) per event; operations numbered by first use."""
    operations: dict[Any, str] = {}
    trail = []
    for event in events:
        if event.operation_id is None:
            operation = "-"
        else:
            operation = operations.setdefault(event.operation_id, f"op{len(operations) + 1}")
        trail.append(
            (
                names.get(event.matter_id, "?"),
                str(event.event_type),
                names.get(event.actor_id, "?"),
                operation,
            )
        )
    return trail


def _session(token: str) -> MatterIntakeSession:
    return MatterIntakeSession.objects.get(pk=token)


def _nothing_written(before: dict[str, int]) -> None:
    assert _counts() == before


def _counts() -> dict[str, int]:
    return {
        "matters": Matter.objects.count(),
        "events": ChangeEvent.objects.count(),
        "documents": Document.objects.count(),
        "notes": MatterPersonalNote.objects.count(),
        "relations": MatterRelation.objects.count(),
        "submissions": Submission.objects.count(),
        "organisations": Organisation.objects.count(),
    }


# ---------------------------------------------------------------------------
# What a save writes
# ---------------------------------------------------------------------------


#: The budget of the full save below, measured at 199 when the sequence moved
#: (HR-13). A ceiling, like the repo's other whole-request budgets: a change that
#: adds queries to every `Uus teema` save has to raise it and say why.
FULL_SAVE_QUERIES = 199


def test_a_full_save_writes_this_trail_in_this_order(specialist, evidence_root):
    other = Matter.objects.create(title="Varasem sarnane teema", owner=specialist)
    client = _client(specialist)
    token = _token(client)
    staged = client.post(STAGE, {"intake": token, "files": [_pdf("Ette laaditud.pdf")]})
    assert staged.status_code == 200

    with _events() as events, CaptureQueriesContext(connection) as queries:
        response = client.post(
            CREATE,
            {
                "title": "Täielik uus teema",
                "owner": str(specialist.pk),
                "intake": token,
                "sender_name": "Näidisliit",
                "response_deadline": _et(timezone.localdate() + dt.timedelta(days=20)),
                "notes": "Minu märge.",
                LINK_ON_CREATE_FIELD: [str(other.pk)],
                "files": [_pdf("Otse valitud.pdf")],
            },
        )

    assert response.status_code == 302
    matter = Matter.objects.get(title="Täielik uus teema")
    assert response["Location"] == reverse("matters:matter_detail", kwargs={"pk": matter.pk})

    names = {matter.pk: "new", other.pk: "other", specialist.pk: "specialist"}
    # Created; the files as one operation, staged first then chosen; the link
    # on both files; the standard plan last. The private note is no event.
    assert _trail(events, names) == [
        ("new", ChangeEventType.MATTER_CREATED, "specialist", "-"),
        ("new", ChangeEventType.DOCUMENT_CREATED, "specialist", "op1"),
        ("new", ChangeEventType.EVIDENCE_VERSION_ADDED, "specialist", "op1"),
        ("new", ChangeEventType.DOCUMENT_CREATED, "specialist", "op1"),
        ("new", ChangeEventType.EVIDENCE_VERSION_ADDED, "specialist", "op1"),
        ("other", ChangeEventType.MATTER_RELATION_ADDED, "specialist", "-"),
        ("new", ChangeEventType.MATTER_RELATION_ADDED, "specialist", "-"),
        ("new", ChangeEventType.PLAN_SEEDED, "specialist", "-"),
    ]

    titles = list(
        Document.objects.filter(matter=matter)
        .order_by("created_at")
        .values_list("title", flat=True)
    )
    assert titles == ["Ette laaditud.pdf", "Otse valitud.pdf"]
    assert MatterPersonalNote.objects.filter(matter=matter, author=specialist).count() == 1
    assert MatterRelation.objects.count() == 1
    assert Organisation.objects.filter(name="Näidisliit").count() == 1
    session = _session(token)
    assert session.consumed_at is not None
    assert session.matter_id == matter.pk
    assert len(queries.captured_queries) <= FULL_SAVE_QUERIES, len(queries.captured_queries)


def test_a_save_filed_already_sent_records_the_proposal_with_its_files(specialist, evidence_root):
    ministry = Organisation.objects.create(name="Näidisministeerium")
    client = _client(specialist)
    token = _token(client)
    instrument = LegalInstrumentType.objects.get(key="koja-ettepanek")

    with _events() as events:
        response = client.post(
            CREATE,
            {
                "title": "Saadetud pöördumine",
                "owner": str(specialist.pk),
                "intake": token,
                "legal_instruments": [str(instrument.pk)],
                "koda_sent": "on",
                "sent_on": _et(timezone.localdate()),
                "sent_recipients": [str(ministry.pk)],
                "files": [_pdf("Pöördumine.pdf"), _pdf("Lisa.pdf")],
            },
        )

    assert response.status_code == 302
    matter = Matter.objects.get(title="Saadetud pöördumine")
    names = {matter.pk: "new", specialist.pk: "specialist"}
    # The send is part of the files' operation: one act in `Teema käik`, with
    # the thirty-day check; no standard plan.
    assert _trail(events, names) == [
        ("new", ChangeEventType.MATTER_CREATED, "specialist", "-"),
        ("new", ChangeEventType.DOCUMENT_CREATED, "specialist", "op1"),
        ("new", ChangeEventType.EVIDENCE_VERSION_ADDED, "specialist", "op1"),
        ("new", ChangeEventType.DOCUMENT_CREATED, "specialist", "op1"),
        ("new", ChangeEventType.EVIDENCE_VERSION_ADDED, "specialist", "op1"),
        ("new", ChangeEventType.SUBMISSION_CREATED, "specialist", "op1"),
        ("new", ChangeEventType.SUBMISSION_SENT, "specialist", "op1"),
        ("new", ChangeEventType.NEXT_ACTION_SET, "specialist", "op1"),
        ("new", ChangeEventType.NEXT_ACTION_SET, "specialist", "op1"),
    ]
    assert Submission.objects.filter(matter=matter).count() == 1
    assert Document.objects.filter(matter=matter).count() == 2
    assert _session(token).matter_id == matter.pk


def test_a_save_from_a_register_candidate_closes_it_in_the_same_save(department_head):
    title = "Äriseadustiku muutmise seaduse eelnõu"
    candidate = TopicCandidate.objects.create(title=title, match_key=candidates.match_key(title))
    CandidateItem.objects.create(
        candidate=candidate,
        source=WatchSource.ADR_MKM,
        external_id=f"adr:mkm:{candidate.pk}",
        title=title,
        occurred_on=dt.date(2026, 11, 20),
        url="https://adr.rik.ee/mkm/dokument/näide",
        detail="2-3/1 · Sissetulev kiri",
    )
    client = _client(department_head)
    token = _token(client, f"?kandidaat={candidate.pk}")

    with _events() as events:
        response = client.post(
            CREATE,
            {
                "title": title,
                "owner": str(department_head.pk),
                "intake": token,
                "kandidaat": str(candidate.pk),
            },
        )

    assert response.status_code == 302
    matter = Matter.objects.get(title=title)
    names = {matter.pk: "new", department_head.pk: "head"}
    # The plan, then the candidate's register address as the Teema's link,
    # after `file_new_matter` and in the same save.
    assert _trail(events, names) == [
        ("new", ChangeEventType.MATTER_CREATED, "head", "-"),
        ("new", ChangeEventType.PLAN_SEEDED, "head", "-"),
        ("new", ChangeEventType.PROCEDURAL_LINK_RECORDED, "head", "op1"),
    ]
    candidate.refresh_from_db()
    assert candidate.status == CandidateStatus.ADDED
    assert candidate.matter == matter


# ---------------------------------------------------------------------------
# What each refusal leaves: nothing, and the token still open
# ---------------------------------------------------------------------------


def test_a_recent_duplicate_writes_nothing_and_keeps_the_token(specialist, evidence_root):
    client = _client(specialist)
    first = client.post(
        CREATE, {"title": "Kaks korda", "owner": str(specialist.pk), "intake": _token(client)}
    )
    assert first.status_code == 302
    token = _token(client)
    before = _counts()

    with _events() as events:
        response = client.post(
            CREATE,
            {
                "title": "Kaks korda",
                "owner": str(specialist.pk),
                "intake": token,
                "notes": "Ei tohi jääda.",
                "files": [_pdf("Hoitud.pdf")],
            },
        )

    assert response.status_code == 400
    assert events == []
    _nothing_written(before)
    session = _session(token)
    assert session.consumed_at is None
    assert session.matter_id is None
    page = response.content.decode()
    assert 'name="pending"' in page
    assert "loo_siiski_uus" in page


def test_a_replayed_form_writes_nothing(specialist, evidence_root):
    client = _client(specialist)
    token = _token(client)
    data = {"title": "Üks kord", "owner": str(specialist.pk), "intake": token}
    assert client.post(CREATE, data).status_code == 302
    before = _counts()

    with _events() as events:
        response = client.post(CREATE, {**data, "files": [_pdf("Teine kord.pdf")]})

    assert response.status_code == 302
    assert events == []
    _nothing_written(before)


def test_a_file_that_fails_to_store_writes_nothing(specialist, evidence_root, monkeypatch):
    from app.matters import intake

    real = intake.create_document
    calls = {"n": 0}

    def fail_on_the_second(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("evidence store unavailable")
        return real(*args, **kwargs)

    monkeypatch.setattr(intake, "create_document", fail_on_the_second)
    client = _client(specialist)
    token = _token(client)
    before = _counts()

    with _events() as events, pytest.raises(OSError):
        client.post(
            CREATE,
            {
                "title": "Katkine fail",
                "owner": str(specialist.pk),
                "intake": token,
                "sender_name": "Uus saatja",
                "files": [_pdf("Esimene.pdf"), _pdf("Teine.pdf")],
            },
        )

    assert calls["n"] == 2
    # Written and then rolled back: the events were saved inside the
    # transaction, and none of them survives it.
    assert events
    _nothing_written(before)
    assert _session(token).consumed_at is None


def test_a_validation_error_writes_nothing_and_holds_the_file(specialist, evidence_root):
    client = _client(specialist)
    token = _token(client)
    before = _counts()

    with _events() as events:
        response = client.post(
            CREATE,
            {"title": "", "owner": str(specialist.pk), "intake": token, "files": [_pdf("A.pdf")]},
        )

    assert response.status_code == 400
    assert events == []
    _nothing_written(before)
    assert _session(token).consumed_at is None
    assert 'name="pending"' in response.content.decode()


def test_a_late_domain_refusal_rolls_back_the_teema_its_files_and_its_sender(
    specialist, evidence_root
):
    """A ticked related Teema the reader cannot see refuses after the Matter and
    its files were written; everything goes, the token stays open."""
    client = _client(specialist)
    token = _token(client)
    before = _counts()

    with _events() as events:
        response = client.post(
            CREATE,
            {
                "title": "Hiline keeldumine",
                "owner": str(specialist.pk),
                "intake": token,
                "sender_name": "Ajutine saatja",
                "notes": "Ka see läheb.",
                LINK_ON_CREATE_FIELD: ["00000000-0000-0000-0000-000000000001"],
                "files": [_pdf("Hoitud.pdf")],
            },
        )

    assert response.status_code == 400
    assert events, "the refusal came after the Teema was written"
    _nothing_written(before)
    assert _session(token).consumed_at is None
    page = response.content.decode()
    assert "Valitud sarnast teemat ei leitud." in page
    assert 'name="pending"' in page
