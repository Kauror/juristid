"""One rendered `Uus teema` form creates at most one Teema (ENG-074).

A repeated POST of the same form — a network retry, a replayed request — used
to file a second Teema and consume a second register number. Chromium does not
send a fast double click twice, but the server cannot rely on what a browser
happens to do, and a retry is not a double click.

The form's intake session is now its one-time token. `Loo teema` consumes it
with one conditional UPDATE, first thing in the creating transaction and before
a reference number is allocated. A second submission of the same form waits for
the first on that row, finds it consumed, writes nothing, and is answered with
the Teema the first one created.

Idempotency belongs to the *form*, not to its contents. A new form with the same
words creates a second Teema, which is a thing a lawyer may mean to do.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.documents.models import Document, DocumentVersion
from app.matters import intake_staging
from app.matters.deletion import delete_matter
from app.matters.models import Matter, MatterReferenceSequence
from app.matters.staging import MatterIntakeSession
from app.matters.views import CREATE_FORM_ALREADY_SUBMITTED
from tests import synthetic_corpus as corpus
from tests.test_evidence_concurrency import TIMEOUT, Runner

CREATE = reverse("matters:matter_create")
STAGE = reverse("matters:intake_stage")


def _signed_in(user: Any) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _form_token(client: Client) -> str:
    """The token a freshly rendered form carries, read from the page."""
    response = client.get(CREATE)
    assert response.status_code == 200
    match = re.search(r'name="intake" value="([^"]+)"', response.content.decode())
    assert match, "the create form rendered no token"
    return match.group(1)


def _fields(title: str, owner: Any, token: str) -> dict[str, Any]:
    return {"title": title, "owner": str(owner.pk), "intake": token}


def _numbers_used() -> int:
    return sum(MatterReferenceSequence.objects.values_list("last_number", flat=True))


def _created(title: str) -> list[Matter]:
    return list(Matter.objects.filter(title=title))


# ---------------------------------------------------------------------------
# Sequential replay
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_same_form_posted_twice_creates_one_teema(specialist):
    client = _signed_in(specialist)
    token = _form_token(client)
    data = _fields("Pakendiseaduse muudatus", specialist, token)

    first = client.post(CREATE, data)
    numbers = _numbers_used()
    second = client.post(CREATE, data, follow=True)

    created = _created("Pakendiseaduse muudatus")
    assert len(created) == 1
    matter = created[0]
    assert first.status_code == 302
    assert first["Location"] == reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    # The replay lands on the same Teema, told why, and consumed no number.
    assert second.redirect_chain[-1][0] == first["Location"]
    assert CREATE_FORM_ALREADY_SUBMITTED in second.content.decode()
    assert _numbers_used() == numbers
    assert (
        ChangeEvent.objects.filter(matter=matter, event_type=ChangeEventType.MATTER_CREATED).count()
        == 1
    )


@pytest.mark.django_db
def test_a_new_form_with_the_same_words_is_a_second_teema(specialist):
    """Idempotency per form, never deduplication by content."""
    client = _signed_in(specialist)
    title = "Sama pealkiri kaks korda"

    client.post(CREATE, _fields(title, specialist, _form_token(client)))
    client.post(CREATE, _fields(title, specialist, _form_token(client)))

    created = _created(title)
    assert len(created) == 2
    assert len({matter.reference_number for matter in created}) == 2


@pytest.mark.django_db
def test_a_replay_after_the_teema_was_deleted_creates_nothing(specialist):
    client = _signed_in(specialist)
    token = _form_token(client)
    data = _fields("Kustutatakse kohe", specialist, token)
    client.post(CREATE, data)
    delete_matter(matter=_created("Kustutatakse kohe")[0], actor=specialist)

    replay = client.post(CREATE, data)

    assert replay.status_code == 302
    assert replay["Location"] == reverse("matters:matter_list")
    assert Matter.all_objects.filter(title="Kustutatakse kohe").count() == 1


@pytest.mark.django_db
def test_a_refused_form_keeps_its_token_and_saves_once_when_corrected(specialist):
    client = _signed_in(specialist)
    token = _form_token(client)

    refused = client.post(CREATE, {"title": "", "owner": str(specialist.pk), "intake": token})
    assert refused.status_code == 400
    carried = re.search(r'name="intake" value="([^"]+)"', refused.content.decode())
    assert carried and carried.group(1) == token

    data = _fields("Parandatud vorm", specialist, token)
    client.post(CREATE, data)
    client.post(CREATE, data)
    assert len(_created("Parandatud vorm")) == 1


@pytest.mark.django_db
def test_another_persons_token_is_not_a_replay_of_theirs(specialist, other_specialist):
    """A token names one person's form. Someone else's is simply no token."""
    token = _form_token(_signed_in(specialist))
    other = _signed_in(other_specialist)

    other.post(CREATE, _fields("Kolleegi teema", other_specialist, token))
    other.post(CREATE, _fields("Kolleegi teema 2", other_specialist, token))

    assert len(_created("Kolleegi teema")) == 1
    assert len(_created("Kolleegi teema 2")) == 1
    assert MatterIntakeSession.objects.get(pk=token).consumed_at is None


# ---------------------------------------------------------------------------
# Staged files
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_replay_promotes_no_staged_file_twice(specialist, evidence_root):
    client = _signed_in(specialist)
    token = _form_token(client)
    staged = client.post(
        STAGE,
        {
            "intake": token,
            "files": [
                SimpleUploadedFile(
                    "kiri.pdf", corpus.text_pdf(["Ministeeriumi kiri"]), "application/pdf"
                )
            ],
        },
    )
    assert staged.status_code == 200
    assert str(staged.context["intake_session"].pk) == token  # the render's own session

    data = _fields("Failiga teema", specialist, token)
    client.post(CREATE, data)
    client.post(CREATE, data)

    created = _created("Failiga teema")
    assert len(created) == 1
    documents = Document.objects.filter(matter=created[0])
    assert documents.count() == 1
    assert DocumentVersion.objects.filter(document__in=documents).count() == 1
    session = MatterIntakeSession.objects.get(pk=token)
    assert session.consumed_at is not None
    assert session.matter_id == created[0].pk


# ---------------------------------------------------------------------------
# Two submissions at once
# ---------------------------------------------------------------------------


def _another_backend_waits() -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_stat_clear_snapshot()")
        cursor.execute(
            """
            SELECT count(*) FROM pg_stat_activity
             WHERE datname = current_database()
               AND wait_event_type = 'Lock'
               AND pid <> pg_backend_pid()
            """
        )
        return cursor.fetchone()[0] > 0


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_simultaneous_posts_of_one_form_create_one_teema(specialist, monkeypatch):
    """The first holds its claim open until the second is seen waiting on it."""
    token = _form_token(_signed_in(specialist))
    data = _fields("Samaaegne kordus", specialist, token)
    numbers = _numbers_used()

    real_claim = intake_staging.claim_for_create
    first_thread: dict[str, int] = {}
    claimed = threading.Event()
    second_done = threading.Event()

    def claim(**kwargs: Any) -> Any:
        result = real_claim(**kwargs)
        if threading.get_ident() == first_thread.get("ident") and not claimed.is_set():
            claimed.set()
            deadline = time.monotonic() + TIMEOUT
            while not second_done.is_set() and time.monotonic() < deadline:
                if _another_backend_waits():
                    break
                time.sleep(0.02)
        return result

    monkeypatch.setattr(intake_staging, "claim_for_create", claim)
    statuses: dict[str, int] = {}

    def first() -> None:
        first_thread["ident"] = threading.get_ident()
        statuses["first"] = _signed_in(specialist).post(CREATE, data).status_code

    def second() -> None:
        assert claimed.wait(TIMEOUT)
        try:
            statuses["second"] = _signed_in(specialist).post(CREATE, data).status_code
        finally:
            second_done.set()

    one = Runner(first).start()
    two = Runner(second).start()
    assert one.join() is None and two.join() is None

    assert statuses == {"first": 302, "second": 302}
    created = _created("Samaaegne kordus")
    assert len(created) == 1
    assert _numbers_used() == numbers + 1
    assert (
        ChangeEvent.objects.filter(
            matter=created[0], event_type=ChangeEventType.MATTER_CREATED
        ).count()
        == 1
    )
