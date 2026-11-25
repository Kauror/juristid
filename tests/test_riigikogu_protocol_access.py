# ruff: noqa: F811 - the programme's fake-API fixture is imported and used as an argument
"""A protocol the Riigikogu refuses (HTTP 403) is one document's state, not the night's.

Production's first activation (25.11.2026) met one committee protocol whose
metadata the API answers 403: the PROTOCOLS stage read PARTIAL and would have stayed
so every night. Now a refusal, told apart by `FetchError.status`, is counted and
named in the run record; the other protocols are read; nothing is stored or
mentioned for the refused one; it is asked again the next night and read once it
is public. Many refusals in one night stay a problem of the source.
"""

from __future__ import annotations

import datetime as dt
import uuid as uuid_module
from typing import Any

import pytest
from django.test import override_settings

from app.register_watch.sources.http import FetchError
from app.riigikogu import cycle, protocols
from app.riigikogu.enums import DocumentKind, RunStatus, Stage, TextStatus
from app.riigikogu.models import ParliamentDocument, StageRun, StenogramMention
from tests.test_riigikogu_programme import api  # noqa: F401 - fixture

pytestmark = pytest.mark.django_db

TODAY = dt.date(2026, 11, 25)
KODA_PROTOCOL = (
    "<html><body><p>Majanduskomisjoni istungi protokoll</p>"
    "<p>Kutsutud: Eesti Kaubandus-Tööstuskoda juhatuse liige</p>"
    "<p>1. Päevakord</p></body></html>"
).encode()
PLAIN_PROTOCOL = b"<html><body><p>Protokoll</p><p>1. Paevakord</p></body></html>"


def _ids(n: int) -> list[str]:
    return [str(uuid_module.UUID(int=0xABC000 + i)) for i in range(n)]


def _sitting(api, protocol_ids: list[str]) -> None:
    api.json["/api/events"] = [
        {
            "committeeSittings": [
                {
                    "committee": {"name": "Majanduskomisjon"},
                    "sittings": [
                        {
                            "sittingDateTime": "2026-11-20T10:00:00",
                            "agendaItems": [],
                            "protocolDocuments": [{"uuid": pid} for pid in protocol_ids],
                        }
                    ],
                }
            ]
        }
    ]


def _public(api, pid: str, body: bytes = KODA_PROTOCOL, name: str = "protokoll.html") -> None:
    file_id = str(uuid_module.uuid5(uuid_module.NAMESPACE_URL, pid))
    api.fail.pop(f"/api/documents/{pid}", None)
    api.json[f"/api/documents/{pid}"] = {
        "title": "Majanduskomisjoni istungi protokoll",
        "documentType": "protocolDocument",
        "created": "2026-11-21T09:00:00",
        "files": [{"uuid": file_id, "fileName": name}],
    }
    api.files[f"/api/files/{file_id}/download"] = body


def _refused(api, pid: str) -> None:
    api.fail[f"/api/documents/{pid}"] = FetchError("api.riigikogu.ee vastas 403", status=403)


def _read() -> protocols.Counts:
    return protocols.read_recent(today=TODAY)


def test_a_refused_protocol_is_counted_and_named_and_the_others_are_read(api):
    refused, readable = _ids(2)
    _sitting(api, [refused, readable])
    _refused(api, refused)
    _public(api, readable)
    counts = _read()
    assert counts.errors == []
    assert (counts.protocols_refused, counts.protocols_new) == (1, 1)
    (entry,) = counts.refused
    assert (entry["protokoll"], entry["koht"], entry["status"]) == (refused, "metaandmed", 403)
    assert entry["nähtud"]
    assert counts.as_dict()["refused"] == counts.refused
    # Nothing stored or mentioned for the refused one; the readable one is read.
    assert not ParliamentDocument.objects.filter(uuid=refused).exists()
    assert ParliamentDocument.objects.get(uuid=readable).text_status == TextStatus.READ
    assert StenogramMention.objects.filter(key__startswith=f"protocol:{readable}").exists()
    assert not StenogramMention.objects.filter(key__startswith=f"protocol:{refused}").exists()


def test_a_refused_protocol_is_read_the_night_it_becomes_public(api):
    (pid,) = _ids(1)
    _sitting(api, [pid])
    _refused(api, pid)
    first = _read()
    assert first.protocols_refused == 1 and not first.errors
    _public(api, pid)
    second = _read()
    assert (second.protocols_refused, second.protocols_new, second.mentions_new) == (0, 1, 1)
    assert ParliamentDocument.objects.get(uuid=pid).text_status == TextStatus.READ
    # And once read it is done: the third night asks nothing about it.
    calls = len(api.calls)
    third = _read()
    assert third.protocols_asked == 0
    assert [c for c in api.calls[calls:] if c[0].startswith("/api/documents/")] == []


def test_a_refused_file_is_a_refusal_too_not_a_parse_failure(api):
    (pid,) = _ids(1)
    _sitting(api, [pid])
    _public(api, pid)
    file_path = next(p for p in api.files if p.endswith("/download"))
    del api.files[file_path]
    plain_get_bytes = api.get_bytes

    def get_bytes(host: str, path: str, **extra: Any) -> bytes:
        if path == file_path:
            api.calls.append((path, {}))
            raise FetchError("api.riigikogu.ee vastas 403", status=403)
        return plain_get_bytes(host, path, **extra)

    api.get_bytes = get_bytes
    import app.register_watch.sources.http as http

    http.get_bytes = get_bytes  # the api fixture's monkeypatch restores the original
    counts = _read()
    assert (counts.protocols_refused, counts.protocols_unreadable) == (1, 0)
    assert counts.refused[0]["koht"] == "fail"
    assert not ParliamentDocument.objects.filter(uuid=pid).exists()


def test_a_stored_unread_protocol_is_checked_again_not_excluded_forever(api):
    (pid,) = _ids(1)
    _sitting(api, [pid])
    _public(api, pid, body=b"%PDF-1.4 broken \x00\x01", name="protokoll.pdf")
    first = _read()
    assert first.protocols_unreadable == 1
    assert ParliamentDocument.objects.get(uuid=pid).text_status != TextStatus.READ
    _public(api, pid)  # the same protocol, its file now readable
    second = _read()
    assert second.protocols_asked == 1 and second.mentions_new == 1
    assert ParliamentDocument.objects.get(uuid=pid).text_status == TextStatus.READ


def test_many_refusals_in_one_night_stay_a_problem_of_the_source(api):
    ids = _ids(4)
    _sitting(api, ids)
    for pid in ids[:3]:
        _refused(api, pid)
    _public(api, ids[3])
    counts = _read()
    assert counts.protocols_refused == 3
    assert counts.errors and "allika juurdepääsu viga" in counts.errors[0]["viga"]


def test_other_failures_are_still_errors_not_refusals(api):
    (pid,) = _ids(1)
    _sitting(api, [pid])
    api.fail[f"/api/documents/{pid}"] = FetchError("api.riigikogu.ee vastas 404", status=404)
    counts = _read()
    assert counts.protocols_refused == 0 and counts.errors


@override_settings(RIIGIKOGU_MONITORING=True, REGISTER_WATCH_FETCH=True)
def test_one_refused_protocol_leaves_the_stage_succeeded_and_its_checkpoint_moving(api):
    refused, readable = _ids(2)
    _sitting(api, [refused, readable])
    _refused(api, refused)
    _public(api, readable)
    cycle.run(today=TODAY, only={Stage.PROTOCOLS})
    run = StageRun.objects.get(stage=Stage.PROTOCOLS)
    assert run.status == RunStatus.SUCCEEDED, run.errors
    assert run.counts["protocols_refused"] == 1
    assert run.counts["refused"][0]["protokoll"] == refused
    assert ParliamentDocument.objects.filter(kind=DocumentKind.PROTOCOL).count() == 1
