"""MCP writes into the shared chronology: notes, meetings and procedural links."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from pydantic import ValidationError

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.entry_enums import EntryKind
from app.matters.models import Entry, MatterEngagement, MatterPersonalNote, MatterProceduralLink
from app.matters.services import close_matter, record_procedural_link
from app.matters.workspace import MEETING_ENTRY_DAY_AHEAD, OTHER_ENTRY_DAY_AHEAD
from app.mcp_writes.operations import REGISTRY
from app.mcp_writes.operations.base import OperationRefused
from app.submissions.models import Submission
from app.workflow.models import NextAction
from tests.test_mcp_write_operations_support import (
    change,
    commit,
    execute_refusal,
    is_stale,
    prepare,
    preview_text,
    public_origin,  # noqa: F401
    refusal,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_origin")]


def _today() -> str:
    return timezone.localdate().isoformat()


# ---------------------------------------------------------------------------
# add_matter_note
# ---------------------------------------------------------------------------


def test_note_prepare_writes_nothing_and_previews_a_shared_entry(specialist, normal_matter):
    entries = Entry.objects.count()
    prepared = prepare(
        "add_matter_note",
        specialist,
        matter=normal_matter.display_reference,
        text="Ministeerium saatis uue versiooni.\n\nVaata lisa 2.",
        occurred_on=_today(),
        source_url="https://example.org/eelnou?id=7",
    )
    assert Entry.objects.count() == entries
    assert prepared.canonical["body"] == (
        "<p>Ministeerium saatis uue versiooni.</p><p>Vaata lisa 2.</p>"
    )
    assert prepared.canonical["url"] == "https://example.org/eelnou?id=7"
    assert change(prepared, "url").proposed == "https://example.org/eelnou?id=7"
    text = preview_text(prepared)
    assert "See ei ole isiklik märge" in text
    assert "Linki ei avata" in text
    assert "Teemat ei suleta" in text


def test_note_executes_add_matter_other_once(specialist, normal_matter):
    day = timezone.localdate() - timedelta(days=3)
    prepared = prepare(
        "add_matter_note",
        specialist,
        matter=str(normal_matter.pk),
        text="<script>alert(1)</script> Tähtis",
        occurred_on=day.isoformat(),
        source_url="https://example.org/a",
    )
    token = uuid.uuid4()
    executed = commit("add_matter_note", specialist, prepared, once_token=token)
    entry = Entry.objects.get(pk=executed.record_id)
    assert entry.kind == EntryKind.OTHER
    assert entry.author == specialist
    assert timezone.localtime(entry.occurred_at).date() == day
    assert entry.url == "https://example.org/a"
    assert "<script>" not in entry.body and "&lt;script&gt;" in entry.body
    assert (
        ChangeEvent.objects.filter(
            object_id=entry.pk, event_type=ChangeEventType.ENTRY_ADDED
        ).count()
        == 1
    )
    # Shared chronology, not a personal note; no step, no closure.
    assert not MatterPersonalNote.objects.filter(matter=normal_matter).exists()
    assert not NextAction.objects.filter(matter=normal_matter).exists()
    normal_matter.refresh_from_db()
    assert normal_matter.is_open
    # The proposal id is the save-once token: the same token again writes nothing.
    with pytest.raises(OperationRefused) as again:
        commit("add_matter_note", specialist, prepared, once_token=token)
    assert again.value.code == "domain_rule"
    assert Entry.objects.filter(matter=normal_matter).count() == 1


def test_note_day_is_required_and_not_in_the_future(specialist, normal_matter):
    tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
    refused = refusal(
        "add_matter_note", specialist, matter=str(normal_matter.pk), text="x", occurred_on=tomorrow
    )
    assert (refused.code, refused.message) == ("domain_rule", OTHER_ENTRY_DAY_AHEAD)
    model = REGISTRY["add_matter_note"].arguments
    with pytest.raises(ValidationError):
        model.model_validate({"matter": "2026_1", "text": "x"})
    assert (
        refusal(
            "add_matter_note",
            specialist,
            matter=str(normal_matter.pk),
            text="x",
            occurred_on="1.10.2026",
        ).code
        == "invalid_argument"
    )
    assert (
        refusal(
            "add_matter_note",
            specialist,
            matter=str(normal_matter.pk),
            text="x",
            occurred_on="1890-01-01",
        ).code
        == "invalid_argument"
    )


def test_note_refuses_an_unsafe_link(specialist, normal_matter):
    for bad in ("javascript:alert(1)", "https://user:pw@example.org/", "ftp://example.org/"):
        assert (
            refusal(
                "add_matter_note",
                specialist,
                matter=str(normal_matter.pk),
                text="x",
                occurred_on=_today(),
                source_url=bad,
            ).code
            == "invalid_argument"
        )


def test_note_refuses_reader_admin_invisible_closed(
    specialist, reader, administrator, normal_matter
):
    args = {"matter": str(normal_matter.pk), "text": "x", "occurred_on": _today()}
    assert refusal("add_matter_note", reader, **args).code == "forbidden"
    assert refusal("add_matter_note", administrator, **args).code == "forbidden"
    assert refusal("add_matter_note", specialist, **{**args, "matter": str(uuid.uuid4())}).code == (
        "not_found"
    )
    prepared = prepare("add_matter_note", specialist, **args)
    close_matter(matter=normal_matter, disposition="OTHER", actor=specialist)
    assert is_stale("add_matter_note", specialist, prepared)
    assert execute_refusal("add_matter_note", specialist, prepared).code == "domain_rule"
    assert refusal("add_matter_note", specialist, **args).code == "domain_rule"
    assert not Entry.objects.filter(matter=normal_matter, kind=EntryKind.OTHER).exists()


def test_note_text_is_bounded():
    model = REGISTRY["add_matter_note"].arguments
    with pytest.raises(ValidationError):
        model.model_validate({"matter": "2026_1", "text": "x" * 8001, "occurred_on": "2026-01-01"})
    with pytest.raises(ValidationError):
        model.model_validate(
            {"matter": "2026_1", "text": "x", "occurred_on": "2026-01-01", "source_url": "h" * 1001}
        )


# ---------------------------------------------------------------------------
# record_meeting
# ---------------------------------------------------------------------------


def test_meeting_prepares_and_executes_add_matter_meeting(specialist, normal_matter):
    prepared = prepare(
        "record_meeting",
        specialist,
        matter=normal_matter.display_reference,
        summary="Kohtusime MKM-iga: Mari, Jaan.",
        held_on=_today(),
    )
    assert "Koja arvamust, pöördumist ega kaasamist ei looda" in preview_text(prepared)
    executed = commit("record_meeting", specialist, prepared)
    entry = Entry.objects.get(pk=executed.record_id)
    assert entry.kind == EntryKind.MEETING
    assert entry.url == ""
    # Not an opinion, a submission, a consultation round or a wait.
    assert not Submission.objects.filter(matter=normal_matter).exists()
    assert not MatterEngagement.objects.filter(matter=normal_matter).exists()
    assert not NextAction.objects.filter(matter=normal_matter).exists()


def test_meeting_not_in_the_future(specialist, normal_matter):
    refused = refusal(
        "record_meeting",
        specialist,
        matter=str(normal_matter.pk),
        summary="x",
        held_on=(timezone.localdate() + timedelta(days=2)).isoformat(),
    )
    assert (refused.code, refused.message) == ("domain_rule", MEETING_ENTRY_DAY_AHEAD)


def test_meeting_refusals(specialist, reader, normal_matter):
    args = {"matter": str(normal_matter.pk), "summary": "x", "held_on": _today()}
    assert refusal("record_meeting", reader, **args).code == "forbidden"
    close_matter(matter=normal_matter, disposition="OTHER", actor=specialist)
    assert refusal("record_meeting", specialist, **args).code == "domain_rule"
    with pytest.raises(ValidationError):
        REGISTRY["record_meeting"].arguments.model_validate({**args, "participants": ["x"]})


# ---------------------------------------------------------------------------
# add_procedural_link
# ---------------------------------------------------------------------------


def test_link_prepares_and_records_without_fetching(specialist, normal_matter, monkeypatch):
    import socket

    def no_network(*args, **kwargs):
        raise AssertionError("nothing may be fetched")

    monkeypatch.setattr(socket, "create_connection", no_network)
    prepared = prepare(
        "add_procedural_link",
        specialist,
        matter=str(normal_matter.pk),
        kind="eis",
        url="https://eelnoud.valitsus.ee/main/mount/docList/abc",
        label="Kooskõlastus",
    )
    assert change(prepared, "kind").proposed == "EIS"
    executed = commit("add_procedural_link", specialist, prepared)
    link = MatterProceduralLink.objects.get(pk=executed.record_id)
    assert (link.kind, link.label) == ("EIS", "Kooskõlastus")
    assert link.url == "https://eelnoud.valitsus.ee/main/mount/docList/abc"
    assert (
        ChangeEvent.objects.filter(
            matter=normal_matter, event_type=ChangeEventType.PROCEDURAL_LINK_RECORDED
        ).count()
        == 1
    )


def test_link_refuses_a_duplicate_address_and_goes_stale(specialist, normal_matter):
    url = "https://www.riigikogu.ee/tegevus/eelnoud/1"
    prepared = prepare(
        "add_procedural_link", specialist, matter=str(normal_matter.pk), kind="riigikogu", url=url
    )
    record_procedural_link(matter=normal_matter, kind="RIIGIKOGU", url=url, actor=specialist)
    assert is_stale("add_procedural_link", specialist, prepared)
    assert (
        refusal(
            "add_procedural_link",
            specialist,
            matter=str(normal_matter.pk),
            kind="riigikogu",
            url=url,
        ).code
        == "domain_rule"
    )
    assert (
        refusal(
            "add_procedural_link", specialist, matter=str(normal_matter.pk), kind="other", url=url
        ).code
        == "domain_rule"
    )


def test_link_validation(specialist, reader, normal_matter):
    assert (
        refusal(
            "add_procedural_link",
            specialist,
            matter=str(normal_matter.pk),
            kind="eis",
            url="not a url",
        ).code
        == "invalid_argument"
    )
    assert (
        refusal(
            "add_procedural_link",
            reader,
            matter=str(normal_matter.pk),
            kind="eis",
            url="https://x.ee/",
        ).code
        == "forbidden"
    )
    model = REGISTRY["add_procedural_link"].arguments
    with pytest.raises(ValidationError):
        model.model_validate({"matter": "2026_1", "kind": "website", "url": "https://x.ee/"})
    with pytest.raises(ValidationError):
        model.model_validate(
            {"matter": "2026_1", "kind": "eis", "url": "https://x.ee/", "label": "x" * 121}
        )
