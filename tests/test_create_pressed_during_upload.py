"""`Loo teema` pressed while the files are still uploading files each one once.

The form carries its intake session from the first render (ENG-074), and the
browser empties the file input only when the stage answer arrives. A press in
between posted both — the session, into which the server had by then staged the
files, and the same files again in the input — and every file became two
Documents. It made main CI red once, as two «esimene.pdf» links on Dokumendid
(`e2e/test_uus_teema_files.py`).

The page now waits for the upload before it submits. These tests are the
server's half, and they reproduce the race without a browser: stage the files
into the form's session, then post the form with the session *and* the same
files, which is exactly the request the unguarded page sent.

What is **not** collapsed is as much the contract as what is. The same bytes
under another name are another file, and so is a file that was never staged.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from app.documents.models import Document
from app.matters.models import Matter
from tests import synthetic_corpus as corpus

CREATE = reverse("matters:matter_create")
STAGE = reverse("matters:intake_stage")


def _signed_in(user: Any) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _form_token(client: Client) -> str:
    response = client.get(CREATE)
    assert response.status_code == 200
    match = re.search(r'name="intake" value="([^"]+)"', response.content.decode())
    assert match, "the create form rendered no token"
    return match.group(1)


def _pdf(name: str, content: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, "application/pdf")


def _stage(client: Client, token: str, files: dict[str, bytes]) -> None:
    response = client.post(
        STAGE, {"intake": token, "files": [_pdf(name, body) for name, body in files.items()]}
    )
    assert response.status_code == 200
    assert str(response.context["intake_session"].pk) == token


def _filed(title: str) -> list[str]:
    """The Document titles on the one Teema called `title`, sorted."""
    matter = Matter.objects.get(title=title)
    return sorted(Document.objects.filter(matter=matter).values_list("title", flat=True))


@pytest.mark.django_db
def test_files_staged_and_still_chosen_are_filed_once(specialist, evidence_root):
    """The exact request the page sent: the session and the same three files.

    Identical bytes under three names, like the browser test that caught it —
    so a guard that matched on content alone would wrongly keep only one.
    """
    client = _signed_in(specialist)
    token = _form_token(client)
    content = corpus.text_pdf(["Ministeeriumi kiri"])
    names = ["esimene.pdf", "teine.pdf", "kolmas.pdf"]
    _stage(client, token, dict.fromkeys(names, content))

    response = client.post(
        CREATE,
        {
            "title": "Kolme failiga teema",
            "owner": str(specialist.pk),
            "intake": token,
            "files": [_pdf(name, content) for name in names],
        },
        follow=True,
    )

    assert response.redirect_chain, response.content.decode()[:2000]
    assert _filed("Kolme failiga teema") == sorted(names)
    assert "on loodud koos 3 failiga" in response.content.decode()


@pytest.mark.django_db
def test_the_same_bytes_under_another_name_are_a_second_file(specialist, evidence_root):
    """Not deduplication by content: a copy somebody renamed is theirs to file."""
    client = _signed_in(specialist)
    token = _form_token(client)
    content = corpus.text_pdf(["Sama sisu"])
    _stage(client, token, {"kiri.pdf": content})

    client.post(
        CREATE,
        {
            "title": "Kaks nime",
            "owner": str(specialist.pk),
            "intake": token,
            "files": [_pdf("koopia.pdf", content)],
        },
    )

    assert _filed("Kaks nime") == ["kiri.pdf", "koopia.pdf"]


@pytest.mark.django_db
def test_a_chosen_file_that_was_never_staged_still_arrives(specialist, evidence_root):
    """Only the overlap is dropped: a second file in the same post is filed."""
    client = _signed_in(specialist)
    token = _form_token(client)
    letter = corpus.text_pdf(["Kaaskiri"])
    annex = corpus.text_pdf(["Lisa"])
    _stage(client, token, {"kaaskiri.pdf": letter})

    client.post(
        CREATE,
        {
            "title": "Kiri ja lisa",
            "owner": str(specialist.pk),
            "intake": token,
            "files": [_pdf("kaaskiri.pdf", letter), _pdf("lisa.pdf", annex)],
        },
    )

    assert _filed("Kiri ja lisa") == ["kaaskiri.pdf", "lisa.pdf"]


@pytest.mark.django_db
def test_a_refused_press_during_the_upload_holds_no_second_copy(specialist, evidence_root):
    """The staged file survives a refusal in its session; holding it too doubled it.

    The re-rendered form would list it twice and the corrected save would file
    both, so a refusal holds only what the session does not already have.
    """
    client = _signed_in(specialist)
    token = _form_token(client)
    content = corpus.text_pdf(["Ministeeriumi kiri"])
    _stage(client, token, {"kiri.pdf": content})

    refused = client.post(
        CREATE,
        {
            "title": "",
            "owner": str(specialist.pk),
            "intake": token,
            "files": [_pdf("kiri.pdf", content)],
        },
    )
    assert refused.status_code == 400
    held = re.findall(r'name="pending" value="([^"]+)"', refused.content.decode())
    assert held == []

    client.post(
        CREATE,
        {"title": "Parandatud", "owner": str(specialist.pk), "intake": token, "pending": held},
    )

    assert _filed("Parandatud") == ["kiri.pdf"]
