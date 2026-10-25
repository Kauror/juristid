"""`Lõpeta teema` — four reasons, and the closing's own files (docs/adr/0131 amendment).

The owner's two changes to the direct closure:

* «Vastus esitatud ja järeltegevus tehtud» is no longer offered, and a forged
  post of it is refused — by the form and by the service — while every stored
  closure with it reads as before;
* `Lisa failid`: any number of files, for any of the four reasons, stored with
  the closure and tied to **that closing** by its `operation_id` — read once,
  under its «Teema suletud» row, after a reopening and a second closing too.

And the guarantees around them: every file is validated before a byte is
stored, a refused closure (bad file, live work unconfirmed, already closed, a
second press, a reader) leaves no document and no stored object, and the files
are ordinary documents — listed on `Dokumendid`, searchable, authorized.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.audit.operations import composer_operation
from app.documents.links import DocumentLink
from app.documents.models import Document, DocumentVersion
from app.documents.services import (
    CLOSURE_EVIDENCE_NEEDS_ITS_CLOSURE,
    capture_closure_evidence,
    read_uploads,
)
from app.intelligence.models import MatterWorkVictory
from app.matters.forms import CLOSURE_CHOICES
from app.matters.models import Entry, Matter
from app.matters.services import close_matter
from app.matters.timeline import matter_timeline
from app.matters.workspace import (
    ALREADY_SAVED,
    CLOSURE_REASON_NOT_OFFERED,
    close_matter_from_workspace,
)
from app.search.services import search_documents
from app.submissions.models import Submission
from app.workflow.enums import Disposition
from app.workflow.services import set_next_action
from tests import factories
from tests.follow_ups import PDF
from tests.refusals import refused
from tests.test_lopeta_teema import (
    ALREADY_CLOSED,
    _checked,
    _closures,
    _matter,
    _page,
    _stage,
)

pytestmark = pytest.mark.django_db

#: The four reasons the panel offers, in the order it offers them.
OFFERED = (
    (Disposition.MONITORING_STOPPED.value, "Koda ei tegele edasi"),
    (Disposition.INITIATIVE_WITHDRAWN.value, "Eelnõu või algatus lõpetati"),
    (Disposition.NO_POSITION_FORMED.value, "Seisukohta ei kujundatud"),
    (Disposition.OTHER.value, "Muu"),
)


def _pdf(name: str = "ministeeriumi_kiri.pdf", body: bytes = b"") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, PDF + body, content_type="application/pdf")


def _not_a_pdf(name: str = "vale.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"MZ not a pdf", content_type="application/pdf")


def _close(client, matter, *, files=(), **data):
    payload = {"disposition": Disposition.MONITORING_STOPPED.value, **data}
    if files:
        payload["attachments"] = list(files)
    return client.post(
        reverse("matters:close_from_workspace", kwargs={"pk": matter.pk}),
        payload,
        headers={"HX-Request": "true"},
    )


def _stored_objects() -> set[str]:
    """Every object in the evidence store, as relative paths."""
    root = Path(settings.EVIDENCE_ROOT)
    if not root.exists():
        return set()
    return {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}


def _closing_rows(matter, user) -> list:
    items, _more = matter_timeline(matter=matter, user=user, limit=200)
    return [
        item
        for item in items
        if item.event is not None and item.event.event_type == ChangeEventType.MATTER_CLOSED
    ]


def _all_file_labels(matter, user) -> list[str]:
    items, _more = matter_timeline(matter=matter, user=user, limit=200)
    return [file.label for item in items for file in item.files]


def _reopen(client, matter) -> None:
    response = client.post(
        reverse("matters:reopen", kwargs={"pk": matter.pk}),
        {"stage": str(_stage("consultation").pk)},
    )
    assert response.status_code in (200, 302)
    matter.refresh_from_db()
    assert matter.is_open


# ---------------------------------------------------------------------------
# 1.1 — four reasons
# ---------------------------------------------------------------------------


def test_the_panel_offers_exactly_four_reasons_in_order_with_the_first_chosen(
    signed_in, specialist
):
    assert CLOSURE_CHOICES == OFFERED

    panel = _page(signed_in, _matter(specialist))
    panel = panel[panel.index('id="teema-lopeta"') :].split("</form>")[0]

    positions = [panel.index(f'value="{value}"') for value, _label in OFFERED]
    assert positions == sorted(positions)
    assert _checked(panel, Disposition.MONITORING_STOPPED.value)
    assert "Vastus esitatud ja järeltegevus tehtud" not in panel
    assert f'value="{Disposition.RESPONSE_COMPLETE.value}"' not in panel


def test_a_forged_response_complete_post_is_refused_and_nothing_closes(signed_in, specialist):
    matter = _matter(specialist)

    response = _close(
        signed_in, matter, disposition=Disposition.RESPONSE_COMPLETE.value, files=[_pdf()]
    )

    assert response.status_code == 400
    assert "Vali, miks teema lõpetatakse." in response.content.decode()
    matter.refresh_from_db()
    assert matter.is_open and _closures(matter) == 0
    assert not Document.objects.filter(matter=matter).exists()


@pytest.mark.parametrize(
    "disposition",
    [
        Disposition.RESPONSE_COMPLETE,
        Disposition.COMPLETED,
        Disposition.SUPERSEDED,
        Disposition.DUPLICATE,
    ],
)
def test_the_service_refuses_a_reason_the_panel_does_not_offer(specialist, disposition):
    matter = _matter(specialist)

    with refused(CLOSURE_REASON_NOT_OFFERED):
        close_matter_from_workspace(matter=matter, author=specialist, disposition=disposition)

    matter.refresh_from_db()
    assert matter.is_open and _closures(matter) == 0


def test_response_complete_stays_a_disposition_internal_callers_may_write(signed_in, specialist):
    """The enum, its words and `close_matter` are untouched: an internal caller
    still closes with it, and that file reads as it was closed."""
    matter = _matter(specialist)

    close_matter(matter=matter, disposition=Disposition.RESPONSE_COMPLETE, actor=specialist)

    matter.refresh_from_db()
    assert matter.disposition == Disposition.RESPONSE_COMPLETE
    assert matter.closure_reason_label == "Vastus esitatud ja järeltegevus tehtud"
    assert "Vastus esitatud ja järeltegevus tehtud" in _page(signed_in, matter)


# ---------------------------------------------------------------------------
# 1.2 — the upload area
# ---------------------------------------------------------------------------


def test_the_panel_has_one_optional_multi_file_drop_zone_below_the_explanation(
    signed_in, specialist
):
    panel = _page(signed_in, _matter(specialist))
    panel = panel[panel.index('id="teema-lopeta"') :].split("</form>")[0]

    assert 'hx-encoding="multipart/form-data"' in panel
    assert 'name="salvestus"' in panel
    assert panel.count('type="file"') == 1
    assert panel.count('class="cx-drop" data-filedrop>') == 1
    assert 'id="id_lopeta_failid"' in panel and "multiple" in panel
    assert "Lisa failid" in panel
    explanation = panel.index('name="explanation"')
    drop = panel.index('class="cx-drop" data-filedrop>')
    button = panel.index(">Lõpeta teema</button>")
    assert explanation < drop < button


@pytest.mark.parametrize(("disposition", "label"), OFFERED)
def test_each_reason_closes_with_a_file_tied_to_the_closing(
    signed_in, specialist, evidence_root, disposition, label
):
    matter = _matter(specialist)
    stage_before = matter.stage_id

    response = _close(
        signed_in,
        matter,
        disposition=disposition,
        explanation="Ministeerium loobus eelnõust.",
        files=[_pdf()],
    )

    assert response.status_code == 200
    matter.refresh_from_db()
    assert not matter.is_open and matter.disposition == disposition
    assert matter.stage_id == stage_before
    closing = ChangeEvent.objects.get(matter=matter, event_type=ChangeEventType.MATTER_CLOSED)
    version = DocumentVersion.objects.get(document__matter=matter)
    added = ChangeEvent.objects.get(
        event_type=ChangeEventType.EVIDENCE_VERSION_ADDED, object_id=version.pk
    )
    # The association: one explicit operation, written in one transaction.
    assert closing.operation_id is not None
    assert added.operation_id == closing.operation_id
    assert version.original_filename == "ministeeriumi_kiri.pdf"
    assert version.uploaded_by == specialist
    # Nothing invented to hold the file.
    assert not DocumentLink.objects.filter(document__matter=matter).exists()
    assert not Entry.objects.filter(matter=matter).exists()
    assert not Submission.objects.filter(matter=matter).exists()
    assert not MatterWorkVictory.objects.filter(matter=matter).exists()
    # One «Teema suletud» row: reason, explanation, the file — once.
    (row,) = _closing_rows(matter, specialist)
    assert row.milestone.sub == label
    assert row.milestone.own_note == "Ministeerium loobus eelnõust."
    assert [file.label for file in row.files] == ["ministeeriumi_kiri.pdf"]
    assert _all_file_labels(matter, specialist) == ["ministeeriumi_kiri.pdf"]


def test_several_files_read_once_each_under_the_one_closing_row(
    signed_in, specialist, evidence_root
):
    matter = _matter(specialist)
    names = ["kiri.pdf", "lisa_1.pdf", "lisa_2.pdf"]

    response = _close(
        signed_in,
        matter,
        files=[_pdf(name, body=name.encode()) for name in names],
    )

    assert response.status_code == 200
    assert Document.objects.filter(matter=matter).count() == 3
    (row,) = _closing_rows(matter, specialist)
    assert sorted(file.label for file in row.files) == names
    assert sorted(_all_file_labels(matter, specialist)) == names
    page = _page(signed_in, matter)
    history = page[page.index('id="ajalugu-loend"') :]
    for name in names:
        assert history.count(f">{name}") == 1
    assert "lisas dokumendi" not in history


def test_closing_without_files_is_unchanged(signed_in, specialist, evidence_root):
    matter = _matter(specialist)
    objects = _stored_objects()

    assert _close(signed_in, matter).status_code == 200

    assert not Document.objects.filter(matter=matter).exists()
    assert _stored_objects() == objects
    (row,) = _closing_rows(matter, specialist)
    assert row.files == ()


def test_one_unusable_file_refuses_the_closure_and_stores_nothing(
    signed_in, specialist, evidence_root
):
    matter = _matter(specialist)
    objects = _stored_objects()
    versions = DocumentVersion.objects.count()

    response = _close(
        signed_in,
        matter,
        explanation="Jääb alles.",
        files=[_pdf(), _not_a_pdf()],
    )

    assert response.status_code == 400
    assert "Jääb alles." in response.content.decode()
    matter.refresh_from_db()
    assert matter.is_open and _closures(matter) == 0
    assert not Document.objects.filter(matter=matter).exists()
    assert DocumentVersion.objects.count() == versions
    assert _stored_objects() == objects


def test_the_files_are_documents_listed_searchable_and_authorized(
    client, signed_in, specialist, reader, evidence_root
):
    matter = _matter(specialist)
    _close(signed_in, matter, files=[_pdf("kalmaarlinna_kiri.pdf")])
    version = DocumentVersion.objects.get(document__matter=matter)

    documents_page = signed_in.get(
        reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    ).content.decode()
    assert "kalmaarlinna_kiri.pdf" in documents_page
    assert search_documents(query="kalmaarlinna", user=specialist).exists()
    download = reverse("documents:download", kwargs={"pk": version.pk})
    assert signed_in.get(download).status_code == 200
    client.force_login(reader)
    assert client.get(download).status_code == 200
    client.force_login(factories.AdministratorFactory())
    assert client.get(download).status_code == 404


def test_a_restricted_matters_closure_files_reach_no_outsider(
    client, signed_in, specialist, evidence_root
):
    from app.core.enums import Visibility

    matter = _matter(specialist)
    Matter.objects.filter(pk=matter.pk).update(visibility=Visibility.RESTRICTED)
    matter.refresh_from_db()
    _close(signed_in, matter, files=[_pdf()])
    version = DocumentVersion.objects.get(document__matter=matter)

    outsider = factories.AdministratorFactory()
    client.force_login(outsider)
    assert client.get(reverse("documents:download", kwargs={"pk": version.pk})).status_code == 404
    detail = reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    assert client.get(detail).status_code == 404
    # Even asked directly, the projection names no file the reader may not open.
    assert all(row.files == () for row in _closing_rows(matter, outsider))


# ---------------------------------------------------------------------------
# Confirmation, reopening, a second closing
# ---------------------------------------------------------------------------


def test_live_work_is_confirmed_first_and_the_files_come_with_the_confirmed_press(
    signed_in, specialist, evidence_root
):
    from app.matters.views import CLOSURE_DIGEST_HEADER

    matter = _matter(specialist)
    set_next_action(matter=matter, text="Helista ministeeriumi", actor=specialist)
    objects = _stored_objects()

    first = _close(signed_in, matter, files=[_pdf()])

    assert first.status_code == 400
    page = first.content.decode()
    assert first[CLOSURE_DIGEST_HEADER]
    # The panel comes back, its drop zone still there to choose the file again.
    assert 'id="id_lopeta_failid"' in page
    matter.refresh_from_db()
    assert matter.is_open and _closures(matter) == 0
    assert not Document.objects.filter(matter=matter).exists()
    assert _stored_objects() == objects

    done = _close(
        signed_in,
        matter,
        files=[_pdf()],
        confirm_follow_up_closure=first[CLOSURE_DIGEST_HEADER],
    )

    assert done.status_code == 200
    matter.refresh_from_db()
    assert not matter.is_open
    (row,) = _closing_rows(matter, specialist)
    assert [file.label for file in row.files] == ["ministeeriumi_kiri.pdf"]


def test_each_closing_keeps_its_own_files_after_reopening_and_closing_again(
    signed_in, specialist, evidence_root
):
    matter = _matter(specialist)
    _close(signed_in, matter, explanation="Esimene", files=[_pdf("esimene.pdf", b"1")])
    _reopen(signed_in, matter)
    _close(
        signed_in,
        matter,
        disposition=Disposition.INITIATIVE_WITHDRAWN.value,
        explanation="Teine",
        files=[_pdf("teine_a.pdf", b"2a"), _pdf("teine_b.pdf", b"2b")],
    )

    rows = _closing_rows(matter, specialist)
    by_note = {row.milestone.own_note: sorted(f.label for f in row.files) for row in rows}
    assert by_note == {"Esimene": ["esimene.pdf"], "Teine": ["teine_a.pdf", "teine_b.pdf"]}
    assert sorted(_all_file_labels(matter, specialist)) == [
        "esimene.pdf",
        "teine_a.pdf",
        "teine_b.pdf",
    ]
    operations = list(
        ChangeEvent.objects.filter(
            matter=matter, event_type=ChangeEventType.MATTER_CLOSED
        ).values_list("operation_id", flat=True)
    )
    assert len(set(operations)) == 2


def test_a_closing_after_a_reopening_without_files_takes_none_of_the_earlier_ones(
    signed_in, specialist, evidence_root
):
    matter = _matter(specialist)
    _close(signed_in, matter, explanation="Esimene", files=[_pdf("esimene.pdf")])
    _reopen(signed_in, matter)
    _close(signed_in, matter, explanation="Teine")

    by_note = {
        row.milestone.own_note: [f.label for f in row.files]
        for row in _closing_rows(matter, specialist)
    }
    assert by_note == {"Esimene": ["esimene.pdf"], "Teine": []}


def test_an_older_closing_on_a_later_page_keeps_its_files(signed_in, specialist, evidence_root):
    """Paginated: the closing and its files arrive together, on its own page."""
    matter = _matter(specialist)
    _close(signed_in, matter, explanation="Esimene", files=[_pdf("esimene.pdf")])
    _reopen(signed_in, matter)
    for index in range(4):
        set_next_action(matter=matter, text=f"Samm {index}", actor=specialist)

    seen: list[str] = []
    offset = 0
    while True:
        items, more = matter_timeline(matter=matter, user=specialist, limit=2, offset=offset)
        seen.extend(file.label for item in items for file in item.files)
        for item in items:
            if item.event is not None and item.event.event_type == ChangeEventType.MATTER_CLOSED:
                assert [f.label for f in item.files] == ["esimene.pdf"]
        if not more:
            break
        offset += len(items)
    assert seen == ["esimene.pdf"]


# ---------------------------------------------------------------------------
# Duplicates, concurrency, authorization
# ---------------------------------------------------------------------------


def test_a_double_press_of_the_drawn_form_closes_and_stores_once(
    signed_in, specialist, evidence_root
):
    matter = _matter(specialist)
    page = _page(signed_in, matter)
    panel = page[page.index('id="teema-lopeta"') :]
    token = panel.split('name="salvestus" value="')[1].split('"')[0]

    first = _close(signed_in, matter, salvestus=token, files=[_pdf()])
    _reopen(signed_in, matter)
    again = _close(signed_in, matter, salvestus=token, files=[_pdf()])

    assert first.status_code == 200
    assert again.status_code == 400
    assert ALREADY_SAVED in again.content.decode()
    matter.refresh_from_db()
    assert matter.is_open
    assert _closures(matter) == 1
    assert Document.objects.filter(matter=matter).count() == 1


def test_a_second_press_on_a_closed_file_stores_nothing(signed_in, specialist, evidence_root):
    matter = _matter(specialist)
    _close(signed_in, matter, files=[_pdf()])
    objects = _stored_objects()

    again = _close(signed_in, matter, files=[_pdf("toine.pdf", b"x")])

    assert again.status_code == 400
    assert ALREADY_CLOSED in again.content.decode()
    assert _closures(matter) == 1
    assert Document.objects.filter(matter=matter).count() == 1
    assert _stored_objects() == objects


def test_a_reader_cannot_use_the_upload_path(client, specialist, reader, evidence_root):
    matter = _matter(specialist)
    client.force_login(reader)
    objects = _stored_objects()

    assert _close(client, matter, files=[_pdf()]).status_code == 404

    matter.refresh_from_db()
    assert matter.is_open and _closures(matter) == 0
    assert not Document.objects.filter(matter=matter).exists()
    assert _stored_objects() == objects


def test_closure_files_are_never_stored_outside_a_closing(specialist, evidence_root):
    matter = _matter(specialist)
    accepted = read_uploads([_pdf()])

    with refused(CLOSURE_EVIDENCE_NEEDS_ITS_CLOSURE):
        capture_closure_evidence(matter=matter, accepted=accepted, actor=specialist)
    assert not Document.objects.filter(matter=matter).exists()

    # Inside one, as the closure writes them.
    with transaction.atomic(), composer_operation():
        assert capture_closure_evidence(matter=matter, accepted=accepted, actor=specialist)


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_concurrent_closures_with_files_close_once_with_one_set_of_files(
    specialist, evidence_root
):
    """Two tabs press `Lõpeta teema` with a file each. One closes and stores its
    file; the other is refused and stores nothing."""
    from tests.test_evidence_concurrency import TIMEOUT, Runner

    matter = _matter(specialist)
    start = threading.Barrier(2, timeout=TIMEOUT)
    outcomes: dict[str, BaseException | None] = {}

    def run(tag: str) -> Callable[[], None]:
        def go() -> None:
            start.wait()
            try:
                close_matter_from_workspace(
                    matter=Matter.objects.get(pk=matter.pk),
                    author=specialist,
                    disposition=Disposition.MONITORING_STOPPED,
                    uploads=[_pdf(f"{tag}.pdf", tag.encode())],
                )
                outcomes[tag] = None
            except BaseException as error:  # recorded, not swallowed
                outcomes[tag] = error

        return go

    first = Runner(run("esimene")).start()
    second = Runner(run("teine")).start()
    assert first.join() is None and second.join() is None

    winners = [tag for tag, error in outcomes.items() if error is None]
    assert len(winners) == 1
    refusal = next(error for error in outcomes.values() if error is not None)
    assert str(refusal) == ALREADY_CLOSED
    assert _closures(matter) == 1
    (version,) = DocumentVersion.objects.filter(document__matter=matter)
    assert version.original_filename == f"{winners[0]}.pdf"


def test_a_closing_that_ends_a_feedback_wait_keeps_its_files_on_its_row(
    signed_in, specialist, reader, evidence_root
):
    """The closure ends an open `Kaasamine` wait in its own operation, which
    writes a record event there too; the files still read under «Teema
    suletud», once, for every reader — never as a «lisas dokumendi» row."""
    from django.utils import timezone

    from app.matters.enums import EngagementKind
    from app.matters.services import add_engagement
    from app.matters.views import CLOSURE_DIGEST_HEADER

    matter = _matter(specialist)
    add_engagement(
        matter=matter,
        kind=EngagementKind.values[0],
        title="Liikmete küsitlus",
        occurred_on=timezone.localdate(),
        actor=specialist,
    )
    first = _close(signed_in, matter, files=[_pdf()])
    assert first.status_code == 400
    done = _close(
        signed_in,
        matter,
        files=[_pdf()],
        confirm_follow_up_closure=first[CLOSURE_DIGEST_HEADER],
    )
    assert done.status_code == 200
    closing = ChangeEvent.objects.get(matter=matter, event_type=ChangeEventType.MATTER_CLOSED)
    assert ChangeEvent.objects.filter(
        operation_id=closing.operation_id,
        event_type=ChangeEventType.ENGAGEMENT_FEEDBACK_CLOSED,
    ).exists()

    for user in (specialist, reader):
        (row,) = _closing_rows(matter, user)
        assert [file.label for file in row.files] == ["ministeeriumi_kiri.pdf"]
        assert _all_file_labels(matter, user) == ["ministeeriumi_kiri.pdf"]


def test_the_closing_row_names_who_closed_the_file(signed_in, specialist, evidence_root):
    matter = _matter(specialist)
    _close(signed_in, matter, files=[_pdf()])

    (row,) = _closing_rows(matter, specialist)
    assert row.closed_by == specialist
    page = _page(signed_in, matter)
    history = page[page.index('id="ajalugu-loend"') :]
    assert f'title="{specialist.display_name}' in history


# ---------------------------------------------------------------------------
# What a closing's operation never takes: files some record owns
# ---------------------------------------------------------------------------


def test_a_notes_file_in_a_closing_save_stays_on_the_note(specialist, evidence_root):
    """«✓ Tehtud» with a file and «Jõustunud»: the note, its file and the
    closure are one operation, and the file is the note's (`_owned_versions`)."""
    from app.matters.timeline import TIMELINE_FILTER_ENTRIES
    from app.matters.workspace import complete_current_action

    matter = _matter(specialist)
    step = set_next_action(matter=matter, text="Viimane samm", actor=specialist)
    complete_current_action(
        matter=matter,
        author=specialist,
        action_id=step.pk,
        body="Seadus jõustus.",
        uploads=[_pdf("teade.pdf")],
        stage=_stage("in_force"),
    )
    matter.refresh_from_db()
    assert not matter.is_open

    (row,) = _closing_rows(matter, specialist)
    assert row.files == ()
    items, _more = matter_timeline(matter=matter, user=specialist, limit=200)
    holders = [item for item in items if any(f.label == "teade.pdf" for f in item.files)]
    assert len(holders) == 1 and holders[0].entry is not None
    entries, _more = matter_timeline(
        matter=matter, user=specialist, limit=200, only=TIMELINE_FILTER_ENTRIES
    )
    assert [f.label for item in entries for f in item.files] == ["teade.pdf"]


def test_an_opinions_sent_files_in_a_closing_save_are_not_closure_evidence(
    specialist, organisation, evidence_root
):
    """`+ Koja arvamus` with two sent files and «Jõustunud»: both files are the
    opinion's (`final_version`, `SubmissionSentFile`), never the closure's."""
    from django.utils import timezone

    from app.matters.workspace import add_matter_koda_opinion
    from app.workflow.follow_ups import FollowUpClosureUnconfirmed

    matter = _matter(specialist)

    def send(confirmed: str = "") -> None:
        add_matter_koda_opinion(
            matter=Matter.objects.get(pk=matter.pk),
            author=specialist,
            uploads=[_pdf("arvamus.pdf", b"a"), _pdf("Selgitus.pdf", b"s")],
            recipients=[organisation],
            sent_on=timezone.localdate(),
            stage=_stage("in_force"),
            follow_ups_confirmed=confirmed,
        )

    # The opinion plans its follow-up check, which the closing asks about once.
    with pytest.raises(FollowUpClosureUnconfirmed) as asked:
        send()
    send(asked.value.digest)
    matter.refresh_from_db()
    assert not matter.is_open

    (row,) = _closing_rows(matter, specialist)
    assert row.files == ()
    labels = _all_file_labels(matter, specialist)
    assert labels.count("Selgitus.pdf") == 1
    assert labels.count("arvamus.pdf") == 1


def test_a_closing_pdf_opens_in_a_new_tab_from_its_row(signed_in, specialist, evidence_root):
    """The closure's files go through `ChronologyFile.of_version` like every other.

    So a PDF under «Teema suletud» opens on the inline route in a new tab and
    says so to a screen reader, exactly as a file under any other row does
    (app/documents/inline.py `file_link`, PR #545).
    """
    import re

    matter = _matter(specialist)
    assert _close(signed_in, matter, files=[_pdf("sulgemise_kiri.pdf")]).status_code == 200
    version = DocumentVersion.objects.get(document__matter=matter)
    open_url = reverse("documents:open", kwargs={"pk": version.pk})

    (row,) = _closing_rows(matter, specialist)
    (file,) = row.files
    assert file.opens_inline and file.url == open_url
    assert file.download_url == reverse("documents:download", kwargs={"pk": version.pk})

    page = _page(signed_in, matter)
    history = page[page.index('id="ajalugu-loend"') :]
    closing = history[history.index("Teema suletud") :]
    anchor = re.search(r'<a class="uxtl__file"([^>]*)>sulgemise_kiri\.pdf(.*?)</a>', closing, re.S)
    assert anchor, "the closing's file is not a link on its row"
    assert f'href="{open_url}"' in anchor.group(1)
    assert 'target="_blank" rel="noopener noreferrer"' in anchor.group(1)
    assert "avaneb uues aknas" in anchor.group(2)
