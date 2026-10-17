"""`Kohtumine / arutelu` — a meeting on a Teema (docs/adr/0097, amendment of 2026-10-17).

The opinion family is `+ Koja arvamus / kohtumine` and holds two sub-choices:
`Koja arvamus` (unchanged, still the default) and `Kohtumine / arutelu`. The
latter writes one `Entry(kind=MEETING)` through the canonical `add_entry` and
`capture_supporting_evidence`, in one transaction: a required `Kuupäev` (stored
as the start of that local day, nothing pre-filled), a required `Lühikokkuvõte`
(sanitised like every entry body), optional files. No organisation or
participants field. It creates no task, moves no stage, plan or deadline, opens
no wait or round and is never a Koja arvamus. Its summary is corrected through
`edit_entry`, whose revision keeps the earlier wording; its date is not
correctable (an owner decision, recorded in the ADR). The browser half is
`e2e/test_kohtumine.py`.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.dates import format_estonian_date, start_of_local_day
from app.documents.models import Document, DocumentVersion
from app.matters.entry_enums import EntryKind
from app.matters.forms import MEETING_SUMMARY_REQUIRED
from app.matters.locks import CLOSED_MATTER_REFUSAL
from app.matters.models import Entry, EntryRevision, MatterEngagement
from app.matters.services import add_entry, entry_revision_token
from app.search.services import search_documents
from app.submissions.models import Submission
from app.workflow.models import NextAction
from tests import factories
from tests.follow_ups import PDF
from tests.gate import PASSWORD, apply_shared_gate

pytestmark = pytest.mark.django_db

#: A stated past day — what an ordinary save carries.
DAY = date(2026, 10, 1)


def _pdf(name: str = "protokoll.pdf", body: bytes = b"") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, PDF + body, content_type="application/pdf")


def _post(client, matter, **data):
    payload = {"occurred_on": "1.10.2026", "summary": ""}
    payload.update({k: v for k, v in data.items() if k != "files"})
    if data.get("files"):
        payload["attachments"] = data["files"]
    return client.post(
        reverse("matters:add_meeting", kwargs={"pk": matter.pk}),
        payload,
        headers={"HX-Request": "true"},
    )


def _page(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _meetings(matter):
    return Entry.objects.filter(matter=matter, kind=EntryKind.MEETING)


def _family(page: str) -> str:
    family = page[page.index('id="lisa-arvamus"') :]
    return family[: family.index('id="lisa-marge-valik"')]


def _panel(page: str) -> str:
    start = page.index('id="arvamus-kohtumine"')
    return page[start : page.index("</form>", start)]


# -- the launcher ---------------------------------------------------------------


def test_the_family_is_renamed_and_offers_two_sub_choices(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    page = _page(signed_in, matter)

    assert '<label class="disclosure-chip" for="lisa-arvamus-valik">+ Koja arvamus / kohtumine' in (
        page
    )
    assert "+ Arvamus / tagasiside" not in page
    chips = re.findall(
        r'<label class="disclosure-chip" for="arvamus-([a-z-]+)-valik">([^<]+)</label>',
        _family(page),
    )
    assert chips == [("koja", "Koja arvamus"), ("kohtumine", "Kohtumine / arutelu")]
    # `Koja arvamus` stays the default sub-choice (docs/adr/0168 opens it).
    assert re.search(r'<input[^>]*id="arvamus-koja-valik"[^>]*checked', page)
    assert not re.search(r'<input[^>]*id="arvamus-kohtumine-valik"[^>]*checked', page)
    # Feedback did not come back into this family.
    assert "arvamus-tagasiside" not in page and "Meile saadetud tagasiside" not in page


def test_the_panel_asks_for_a_day_a_summary_and_files_and_nothing_else(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    panel = _panel(_page(signed_in, matter))

    assert reverse("matters:add_meeting", kwargs={"pk": matter.pk}) in panel
    assert "Kuupäev" in panel and "Lühikokkuvõte" in panel
    assert 'id="id_kohtumine_occurred_on"' in panel
    assert 'id="id_kohtumine_summary"' in panel
    assert 'id="id_kohtumine_failid"' in panel
    names = set(re.findall(r'name="([a-z_]+)"', panel))
    assert names == {"csrfmiddlewaretoken", "occurred_on", "summary", "attachments"}
    # No organisation picker, no participants, no link.
    assert "organisation" not in names and "url" not in names


def test_the_day_box_starts_empty(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    page = _page(signed_in, matter)

    box = re.search(r'<input[^>]*id="id_kohtumine_occurred_on"[^>]*>', page).group(0)
    assert 'value=""' in box or "value=" not in box
    assert "data-datepicker" in box


def test_both_required_labels_carry_the_mark(signed_in, specialist):
    from tests.test_ui_cleanup_teema import MARK

    panel = _panel(_page(signed_in, factories.MatterFactory(owner=specialist)))

    for required in ("Kuupäev", "Lühikokkuvõte"):
        assert re.search(re.escape(required) + r"\s*" + re.escape(MARK), panel), required


# -- validation -------------------------------------------------------------------


def test_a_missing_day_is_refused_and_keeps_the_summary(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    response = _post(signed_in, matter, occurred_on="", summary="Arutelu ilma päevata.")

    assert response.status_code == 400
    body = response.content.decode()
    assert "Sisesta kohtumise kuupäev." in body
    assert "Arutelu ilma päevata." in body
    # The refusal reopens this panel, not `Koja arvamus`.
    assert re.search(r'<input[^>]*id="arvamus-kohtumine-valik"[^>]*checked', body)
    assert not _meetings(matter).exists()


def test_a_missing_summary_is_refused_under_the_field(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    response = _post(signed_in, matter, summary="   ")

    assert response.status_code == 400
    body = response.content.decode()
    assert f'id="id_kohtumine_summary_error">{MEETING_SUMMARY_REQUIRED}<' in body
    # The typed day survives.
    box = re.search(r'<input[^>]*id="id_kohtumine_occurred_on"[^>]*>', body).group(0)
    assert "1.10.2026" in box
    assert not _meetings(matter).exists()


def test_a_summary_empty_after_sanitising_is_refused_under_the_field(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    response = _post(signed_in, matter, summary="<script>alert(1)</script><p></p>")

    assert response.status_code == 400
    assert f'id="id_kohtumine_summary_error">{MEETING_SUMMARY_REQUIRED}<' in (
        response.content.decode()
    )
    assert not _meetings(matter).exists()


def test_script_is_stripped_from_a_saved_summary(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    response = _post(signed_in, matter, summary="Arutasime eelnõu.<script>alert(1)</script>")

    assert response.status_code == 200
    entry = _meetings(matter).get()
    assert "Arutasime eelnõu." in entry.body
    assert "script" not in entry.body and "alert" not in entry.body


# -- saving ----------------------------------------------------------------------


def test_a_day_and_a_summary_save_one_meeting_on_that_day(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    response = _post(signed_in, matter, summary="Kohtumine ministeeriumiga: osalesid Mari ja Jüri.")

    assert response.status_code == 200
    entry = Entry.objects.get(matter=matter)
    assert entry.kind == EntryKind.MEETING
    assert entry.author == specialist
    assert "osalesid Mari ja Jüri" in entry.body
    assert entry.organisation_id is None and entry.url == ""
    # The stated day, as the start of that local day — not the moment of saving.
    assert entry.occurred_at == start_of_local_day(DAY)
    assert timezone.localtime(entry.occurred_at).date() == DAY
    assert ChangeEvent.objects.filter(
        event_type=ChangeEventType.ENTRY_ADDED, object_id=entry.pk
    ).exists()


def test_the_row_sits_on_its_day_with_author_label_and_summary(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    add_entry(matter=matter, body="Tänane märkus kalmaar.", author=specialist)
    past = timezone.localdate() - timedelta(days=12)
    older = timezone.localdate() - timedelta(days=30)
    add_entry(
        matter=matter,
        body="Vanem märkus haug.",
        author=specialist,
        occurred_at=start_of_local_day(older),
    )

    _post(
        signed_in,
        matter,
        summary="Arutelu tuunikala.",
        occurred_on=f"{past.day}.{past.month}.{past.year}",
    )

    entry = _meetings(matter).get()
    assert timezone.localtime(entry.occurred_at).date() == past
    page = _page(signed_in, matter)
    # Newest first: today's note, then the meeting twelve days ago, then the older note.
    assert (
        page.index("Tänane märkus kalmaar.")
        < page.index("Arutelu tuunikala.")
        < page.index("Vanem märkus haug.")
    )
    row = page[page.index(f'id="sissekanne-{entry.pk}"') :]
    row = row[: row.index("</article>")]
    assert f">{past.day}.{past.month}.{past.year}</time>" in row
    assert '<span class="uxtl__kind uxtl__kind--entry">Kohtumine</span>' in row
    assert specialist.display_name.split()[0] in row


def test_one_file_is_captured(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    _post(signed_in, matter, summary="Üks protokoll.", files=[_pdf("protokoll.pdf")])

    entry = _meetings(matter).get()
    document = Document.objects.get(matter=matter)
    assert [link.entry_id for link in document.links.all()] == [entry.pk]


def test_several_files_are_captured_and_listed_under_dokumendid(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    _post(
        signed_in,
        matter,
        summary="Kaks kohtumise dokumenti.",
        files=[_pdf("esitlus.pdf", b"1"), _pdf("memo.pdf", b"2")],
    )

    entry = _meetings(matter).get()
    documents = Document.objects.filter(matter=matter)
    assert documents.count() == 2
    assert {link.entry_id for d in documents for link in d.links.all()} == {entry.pk}
    docs_page = signed_in.get(
        reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    ).content.decode()
    assert "esitlus.pdf" in docs_page and "memo.pdf" in docs_page
    page = _page(signed_in, matter)
    assert "esitlus.pdf" in page and "memo.pdf" in page


def test_a_refused_file_leaves_no_entry_document_or_evidence_behind(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    versions = DocumentVersion.objects.count()
    not_a_pdf = SimpleUploadedFile("vale.pdf", b"MZ not a pdf", content_type="application/pdf")

    response = _post(signed_in, matter, summary="Fail lükatakse tagasi.", files=[_pdf(), not_a_pdf])

    assert response.status_code == 400
    assert "Fail lükatakse tagasi." in response.content.decode()
    assert not Entry.objects.filter(matter=matter).exists()
    assert not Document.objects.filter(matter=matter).exists()
    assert DocumentVersion.objects.count() == versions


# -- what it must not do -----------------------------------------------------------


def test_saving_changes_no_workflow_state(signed_in, specialist):
    deadline = timezone.localdate() + timedelta(days=20)
    matter = factories.MatterFactory(owner=specialist, response_deadline=deadline)
    matter.refresh_from_db()
    before = (
        matter.stage_id,
        matter.response_deadline,
        matter.is_open,
        matter.disposition,
    )
    actions = list(NextAction.objects.filter(matter=matter).values_list("pk", "status"))

    _post(signed_in, matter, summary="Ei loo tööd.", files=[_pdf()])

    matter.refresh_from_db()
    assert (
        matter.stage_id,
        matter.response_deadline,
        matter.is_open,
        matter.disposition,
    ) == before
    assert list(NextAction.objects.filter(matter=matter).values_list("pk", "status")) == actions
    assert not MatterEngagement.objects.filter(matter=matter).exists()
    assert not Submission.objects.filter(matter=matter).exists()


def test_an_open_next_action_is_neither_completed_nor_replaced(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    action = factories.NextActionFactory(matter=matter)
    status = action.status

    _post(signed_in, matter, summary="Kohtumine, samm jääb lahti.")

    action.refresh_from_db()
    assert action.status == status
    assert NextAction.objects.filter(matter=matter).count() == 1


def test_a_closed_matter_refuses_a_meeting(signed_in, specialist):
    matter = factories.MatterFactory(
        owner=specialist, is_open=False, disposition="COMPLETED", closed_at=timezone.now()
    )

    response = _post(signed_in, matter, summary="Liiga hilja.")

    assert response.status_code == 400
    assert CLOSED_MATTER_REFUSAL in response.content.decode()
    assert not _meetings(matter).exists()


def test_a_reader_cannot_add_one(client, reader, specialist):
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(reader)

    response = _post(client, matter, summary="Lugeja ei kirjuta.")

    assert response.status_code == 404
    assert not _meetings(matter).exists()


def test_a_user_without_a_business_role_cannot_add_one(client, administrator, specialist):
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(administrator)

    response = _post(client, matter, summary="Haldur ei kirjuta.")

    assert response.status_code == 404
    assert not _meetings(matter).exists()


def test_the_gate_visitor_writes_nothing(normal_matter, settings):
    apply_shared_gate(settings)
    gate = Client()
    assert gate.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302

    response = _post(gate, normal_matter, summary="Värav ei kirjuta.")

    assert response.status_code != 200
    assert not _meetings(normal_matter).exists()


# -- visibility ----------------------------------------------------------------------


def test_search_finds_the_summary_for_a_lawyer_and_never_for_a_reader_of_a_restricted_matter(
    signed_in, specialist, reader, restricted_matter
):
    _post(signed_in, restricted_matter, summary="Konfidentsiaalne arutelu kalmaarlinn.")

    assert search_documents(query="kalmaarlinn", user=specialist).exists()
    assert not search_documents(query="kalmaarlinn", user=reader).exists()


def test_search_finds_the_summary_on_a_normal_matter_for_a_reader(
    signed_in, specialist, reader, normal_matter
):
    _post(signed_in, normal_matter, summary="Avalik arutelu merihobu.")

    assert search_documents(query="merihobu", user=reader).exists()


def test_a_restricted_matters_meeting_and_file_are_not_visible_to_a_reader(
    client, signed_in, specialist, reader, restricted_matter
):
    _post(signed_in, restricted_matter, summary="Piiratud kohtumine.", files=[_pdf()])
    version = Document.objects.get(matter=restricted_matter).current_version
    client.force_login(reader)

    assert client.get(reverse("documents:download", kwargs={"pk": version.pk})).status_code == 404
    page = client.get(reverse("matters:matter_detail", kwargs={"pk": restricted_matter.pk}))
    assert page.status_code == 404


# -- correction ------------------------------------------------------------------------


def test_correcting_the_summary_keeps_the_earlier_wording_and_the_day(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _post(signed_in, matter, summary="Algne kokkuvõte.")
    entry = _meetings(matter).get()
    occurred_at = entry.occurred_at

    form_html = signed_in.get(
        reverse("matters:edit_entry", kwargs={"pk": matter.pk, "entry_id": entry.pk})
    ).content.decode()
    response = signed_in.post(
        reverse("matters:edit_entry", kwargs={"pk": matter.pk, "entry_id": entry.pk}),
        {"body": "Parandatud kokkuvõte.", "revision": entry_revision_token(entry)},
        headers={"HX-Request": "true"},
    )

    # The ordinary body-only correction: no link box on a meeting.
    assert 'name="url"' not in form_html
    assert response.status_code == 200
    entry.refresh_from_db()
    assert "Parandatud kokkuvõte." in entry.body
    assert entry.edit_count == 1
    assert entry.occurred_at == occurred_at
    assert "Algne kokkuvõte." in EntryRevision.objects.get(entry=entry).body
    assert ChangeEvent.objects.filter(
        event_type=ChangeEventType.ENTRY_EDITED, object_id=entry.pk
    ).exists()


# -- its neighbours still work -----------------------------------------------------------


def test_koja_arvamus_still_renders_and_saves(signed_in, specialist, organisation):
    matter = factories.MatterFactory(owner=specialist)
    page = _page(signed_in, matter)
    assert 'id="arvamus-koja"' in page

    response = signed_in.post(
        reverse("matters:add_koda_opinion", kwargs={"pk": matter.pk}),
        {
            "recipients": [str(organisation.pk)],
            "sent_on": format_estonian_date(timezone.localdate()),
            "title": "Koja arvamus endiselt",
            "upload": _pdf("arvamus.pdf"),
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert Submission.objects.filter(matter=matter).count() == 1
    assert not _meetings(matter).exists()


def test_lisa_tagasiside_still_renders_under_kaasamine(signed_in, specialist):
    page = _page(signed_in, factories.MatterFactory(owner=specialist))

    assert 'id="kaasamine-tagasiside"' in page
    assert "Lisa tagasiside" in page
