"""«Lisa märkena» brings the register's own file (owner, 2026-11-22; `register_files`)."""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from app.documents.models import DocumentLink
from app.matters.models import MatterProceduralDevelopment
from app.register_watch import register_files
from app.register_watch.models import FindingStatus, WatchFinding, WatchSource, WatchTarget
from app.register_watch.register_files import Fetched, RemoteFile
from app.register_watch.sources.http import FetchError
from tests import factories

PDF = b"%PDF-1.4 register file"


def test_the_pdr_record_names_its_one_file():
    files = register_files.pdr_files("adr.envir.ee", "b89d", {"file": "065_KLIM.asice"})
    assert files == [RemoteFile("adr.envir.ee", "/api/v1/documents/b89d/file", "065_KLIM.asice")]
    assert register_files.pdr_files("adr.envir.ee", "b89d", {"file": None}) == []


def test_the_adr_page_links_its_files():
    page = (
        '<a href="/ram/fail/19155773/13-3.1%2013.11.2026%20V%C3%A4ljaminev%20kiri.asice">x</a>'
        '<a href="/ram/fail/19155773/13-3.1%2013.11.2026%20V%C3%A4ljaminev%20kiri.asice">x</a>'
        '<a href="/jm/fail/1/other.pdf">another register</a>'
    )
    files = register_files.adr_files("ram", page)
    assert [one.name for one in files] == ["13-3.1 13.11.2026 Väljaminev kiri.asice"]
    assert files[0].host == "adr.rik.ee"


def test_only_the_riigikogus_public_files_are_taken():
    data = {
        "files": [
            {"uuid": "a", "fileName": "arvamus.pdf", "accessRestrictionType": "PUBLIC"},
            {"uuid": "b", "fileName": "salajane.pdf", "accessRestrictionType": "AK"},
        ]
    }
    assert register_files.riigikogu_files(data) == [
        RemoteFile("api.riigikogu.ee", "/api/files/a/download", "arvamus.pdf")
    ]


def test_a_sitting_and_the_bill_have_no_file():
    assert register_files.locate(WatchSource.RIIGIKOGU, "event:I:2026-11-10") == []
    assert register_files.locate(WatchSource.RIIGIKOGU, "draft:x") == []


def test_fetch_never_raises_and_names_what_did_not_come(monkeypatch):
    monkeypatch.setattr(
        register_files,
        "locate",
        lambda source, external_id: [
            RemoteFile("adr.envir.ee", "/a", "kiri.pdf"),
            RemoteFile("api.riigikogu.ee", "/b", "arvamus.html"),
            RemoteFile("adr.envir.ee", "/c", "suur.pdf"),
        ],
    )

    def get_bytes(host, path):
        if path == "/c":
            raise FetchError("liiga suur")
        return PDF

    monkeypatch.setattr(register_files, "get_bytes", get_bytes)

    fetched = register_files.fetch(WatchSource.KLIM_REGISTER, "doc:x")

    assert [upload.name for upload in fetched.uploads] == ["kiri.pdf"]
    assert fetched.missed == ["arvamus.html", "suur.pdf"]

    def unreachable(source, external_id):
        raise FetchError("ei vastanud")

    monkeypatch.setattr(register_files, "locate", unreachable)
    assert register_files.fetch(WatchSource.KLIM_REGISTER, "doc:x").unreachable


def _finding(matter):
    target = WatchTarget.objects.create(
        matter=matter,
        source=WatchSource.KLIM_REGISTER,
        identifier="Jäätmeseaduse",
        label="Jäätmeseaduse",
        origin="PHRASE",
    )
    return WatchFinding.objects.create(
        target=target,
        matter=matter,
        external_id="doc:b89d",
        title="Toiduliidu tagasiside",
        url="https://adr.envir.ee/et/document.html?id=b89d",
        detail="1-4/26/2491-15",
    )


@pytest.mark.django_db
def test_lisa_markena_attaches_the_file_and_the_comment(client, specialist, monkeypatch):
    matter = factories.MatterFactory(owner=specialist, title="Jäätmeseaduse muutmise eelnõu")
    finding = _finding(matter)
    monkeypatch.setattr(
        register_files,
        "fetch",
        lambda source, external_id: Fetched(
            uploads=[SimpleUploadedFile("tagasiside.pdf", PDF, content_type="application/pdf")]
        ),
    )
    client.force_login(specialist)

    client.post(
        reverse("register_watch:decide", kwargs={"pk": finding.pk}),
        {"otsus": "lisa", "markus": "Vaata punkti 3.", "tagasi": "teema"},
    )

    development = MatterProceduralDevelopment.objects.get(matter=matter)
    assert development.note.startswith("Vaata punkti 3.\n\n")
    assert "1-4/26/2491-15" in development.note
    links = DocumentLink.objects.filter(procedural_development=development)
    assert links.count() == 1
    finding.refresh_from_db()
    assert finding.status == FindingStatus.ADDED


@pytest.mark.django_db
def test_a_register_that_does_not_answer_still_adds_the_note(client, specialist, monkeypatch):
    matter = factories.MatterFactory(owner=specialist, title="Jäätmeseaduse muutmise eelnõu")
    finding = _finding(matter)
    monkeypatch.setattr(
        register_files, "fetch", lambda source, external_id: Fetched(unreachable=True)
    )
    client.force_login(specialist)

    response = client.post(
        reverse("register_watch:decide", kwargs={"pk": finding.pk}),
        {"otsus": "lisa", "tagasi": "teema"},
        follow=True,
    )

    development = MatterProceduralDevelopment.objects.get(matter=matter)
    assert not DocumentLink.objects.filter(procedural_development=development).exists()
    assert "Registrist ei saanud faili alla laadida" in response.content.decode()
