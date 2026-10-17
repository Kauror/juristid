"""`+ Lisa · Muu` — supporting material on a Teema (docs/adr/0097, amendment of 2026-10-15).

One `Entry(kind=OTHER)`: a required `Kirjeldus` and `Millal?`, an optional `Link`
in the entry's own `url` column, and files through the canonical evidence
pipeline — saved together or not at all. `Millal?` is the day the material or
event occurred: `occurred_at` is the start of that local day, so the chronology
shows and orders the row there; no day is pre-filled or invented. It creates no
task, moves no stage or plan, opens no wait and is never a Koja arvamus. Its link
and description are corrected through `edit_entry`, whose revision keeps both
earlier values. A link is an `OTHER` entry's only — service and constraint. The
browser half is `e2e/test_lisa_muu.py`.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.dates import start_of_local_day
from app.documents.models import Document
from app.matters.entry_enums import EntryKind
from app.matters.locks import CLOSED_MATTER_REFUSAL
from app.matters.models import Entry, EntryRevision, MatterEngagement
from app.matters.services import (
    ENTRY_URL_ONLY_FOR_OTHER,
    add_entry,
    edit_entry,
    entry_revision_token,
)
from app.search.services import search_documents
from app.submissions.models import Submission
from app.workflow.models import NextAction
from tests import factories
from tests.follow_ups import PDF
from tests.refusals import refused

pytestmark = pytest.mark.django_db


def _pdf(name: str = "taust.pdf", body: bytes = b"") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, PDF + body, content_type="application/pdf")


#: A stated past day — what an ordinary `Muu` save carries.
DAY = date(2026, 10, 1)


def _post(client, matter, **data):
    payload = {"description": "", "url": "", "occurred_on": "1.10.2026"}
    payload.update({k: v for k, v in data.items() if k != "files"})
    if data.get("files"):
        payload["attachments"] = data["files"]
    return client.post(
        reverse("matters:add_other", kwargs={"pk": matter.pk}),
        payload,
        headers={"HX-Request": "true"},
    )


def _page(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _others(matter):
    return Entry.objects.filter(matter=matter, kind=EntryKind.OTHER)


# -- the launcher ---------------------------------------------------------------


def test_muu_is_the_fifth_choice_of_plus_lisa(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    page = _page(signed_in, matter)

    family = page[page.index('id="lisa-marge"') :]
    chips = re.findall(
        r'<label class="disclosure-chip" for="marge-([a-z-]+)-valik">([^<]+)</label>', family
    )
    assert [label for _, label in chips[:5]] == [
        "Arvamuse tähtaeg",
        "Oluline tähtaeg",
        "Jõustumine",
        "Töövõit",
        "Muu",
    ]
    assert 'id="marge-muu"' in page
    assert 'name="description"' in page and 'id="id_muu_url"' in page
    assert "Kirjelda, mida lisad ja miks see on teema jaoks oluline." in page
    # Not the retired generic task form.
    assert "marge-tavaline" not in page


# -- saving ----------------------------------------------------------------------


def test_a_description_and_a_day_save_one_other_entry_on_that_day(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    response = _post(signed_in, matter, description="Ministeeriumi taustamaterjal analüüsiks.")

    assert response.status_code == 200
    entry = _others(matter).get()
    assert entry.author == specialist
    assert "Ministeeriumi taustamaterjal" in entry.body
    assert entry.url == ""
    # The stated day, as the start of that local day — not the moment of saving.
    assert entry.occurred_at == start_of_local_day(DAY)
    assert timezone.localtime(entry.occurred_at).date() == DAY
    assert ChangeEvent.objects.filter(
        event_type=ChangeEventType.ENTRY_ADDED, object_id=entry.pk
    ).exists()


def test_millal_is_required_and_no_day_is_invented(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    response = _post(signed_in, matter, description="Päev puudub.", occurred_on="")

    assert response.status_code == 400
    body = response.content.decode()
    assert "Sisesta, millal see toimus." in body
    assert "Päev puudub." in body
    assert not _others(matter).exists()


def test_the_millal_box_starts_empty(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    page = _page(signed_in, matter)

    box = re.search(r'<input[^>]*id="id_muu_occurred_on"[^>]*>', page).group(0)
    assert 'value=""' in box or "value=" not in box
    assert "data-datepicker" in box


def test_a_past_day_places_the_row_in_the_chronology_on_that_day(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    add_entry(matter=matter, body="Tänane märkus kalmaar.", author=specialist)
    past = timezone.localdate() - timedelta(days=12)

    _post(
        signed_in,
        matter,
        description="Varasem taustamaterjal tuunikala.",
        occurred_on=f"{past.day}.{past.month}.{past.year}",
    )

    entry = _others(matter).get()
    assert timezone.localtime(entry.occurred_at).date() == past
    page = _page(signed_in, matter)
    # Newest first: today's note above the Muu dated twelve days ago.
    assert page.index("Tänane märkus kalmaar.") < page.index("Varasem taustamaterjal tuunikala.")
    row = page[page.index(f'id="sissekanne-{entry.pk}"') :]
    row = row[: row.index("</article>")]
    assert f">{past.day}.{past.month}.{past.year}</time>" in row


def test_a_link_is_normalised_stored_and_shown_by_its_host(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    _post(
        signed_in,
        matter,
        description="Liidu avalik kommentaar.",
        url="  https://www.example.ee/kommentaar?id=7  ",
    )

    entry = _others(matter).get()
    assert entry.url == "https://www.example.ee/kommentaar?id=7"
    page = _page(signed_in, matter)
    assert 'href="https://www.example.ee/kommentaar?id=7"' in page
    assert "example.ee" in page
    assert 'rel="noopener noreferrer"' in page


@pytest.mark.parametrize(
    "bad",
    [
        "javascript:alert(1)",
        "ftp://example.ee/fail",
        "https://",
        "lihtsalt tekst",
        "https://example.ee/" + "a" * 1100,
    ],
)
def test_an_unsafe_or_malformed_link_is_refused_and_nothing_is_saved(signed_in, specialist, bad):
    matter = factories.MatterFactory(owner=specialist)

    response = _post(signed_in, matter, description="Kirjeldus jääb alles.", url=bad)

    assert response.status_code == 400
    assert not _others(matter).exists()
    # What was typed is still in the form.
    assert "Kirjeldus jääb alles." in response.content.decode()


def test_a_description_is_required_even_with_a_link_or_a_file(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    response = _post(
        signed_in, matter, description="   ", url="https://example.ee/x", files=[_pdf()]
    )

    assert response.status_code == 400
    assert "Kirjelda, mida lisad." in response.content.decode()
    assert not _others(matter).exists()
    assert not Document.objects.filter(matter=matter).exists()


def test_several_files_are_captured_and_listed_under_dokumendid(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    _post(
        signed_in,
        matter,
        description="Kaks taustadokumenti.",
        files=[_pdf("esimene.pdf", b"1"), _pdf("teine.pdf", b"2")],
    )

    entry = _others(matter).get()
    documents = Document.objects.filter(matter=matter)
    assert documents.count() == 2
    assert {link.entry_id for d in documents for link in d.links.all()} == {entry.pk}
    docs_page = signed_in.get(
        reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    ).content.decode()
    assert "esimene.pdf" in docs_page and "teine.pdf" in docs_page
    # And in the chronology row.
    page = _page(signed_in, matter)
    assert "esimene.pdf" in page and "teine.pdf" in page


def test_a_refused_file_leaves_no_entry_behind(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    not_a_pdf = SimpleUploadedFile("vale.pdf", b"MZ not a pdf", content_type="application/pdf")

    response = _post(signed_in, matter, description="Fail lükatakse tagasi.", files=[not_a_pdf])

    assert response.status_code == 400
    assert not _others(matter).exists()
    assert not Document.objects.filter(matter=matter).exists()


def test_a_mixed_selection_with_one_refused_file_leaves_no_bytes_behind(
    signed_in, specialist, evidence_root
):
    """A good file beside a refused one: no entry, no `Document`, and no orphan
    object in the evidence store — every file is validated before any is written."""
    matter = factories.MatterFactory(owner=specialist)
    not_a_pdf = SimpleUploadedFile("vale.pdf", b"MZ not a pdf", content_type="application/pdf")

    response = _post(
        signed_in, matter, description="Üks hea, üks halb.", files=[_pdf("hea.pdf"), not_a_pdf]
    )

    assert response.status_code == 400
    assert not _others(matter).exists()
    assert not Document.objects.filter(matter=matter).exists()
    assert not [p for p in (evidence_root / "evidence").rglob("*") if p.is_file()]


def test_a_repeated_identical_post_writes_its_own_row(signed_in, specialist):
    """Two saves are two records, as for every other `+ Lisa` choice; nothing merges
    them silently. (The browser's double-submit guard keeps a double click to one.)"""
    matter = factories.MatterFactory(owner=specialist)

    _post(signed_in, matter, description="Sama tekst.")
    _post(signed_in, matter, description="Sama tekst.")

    assert _others(matter).count() == 2


# -- what it must not do -----------------------------------------------------------


def test_saving_changes_no_workflow_state(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    matter.refresh_from_db()
    stage, deadline, is_open = matter.stage_id, matter.response_deadline, matter.is_open
    actions = NextAction.objects.filter(matter=matter).count()

    _post(signed_in, matter, description="Ei loo tööd.", url="https://example.ee/a", files=[_pdf()])

    matter.refresh_from_db()
    assert (matter.stage_id, matter.response_deadline, matter.is_open) == (
        stage,
        deadline,
        is_open,
    )
    assert NextAction.objects.filter(matter=matter).count() == actions
    assert not MatterEngagement.objects.filter(matter=matter).exists()
    assert not Submission.objects.filter(matter=matter).exists()


def test_a_closed_matter_refuses_a_new_entry(signed_in, specialist):
    matter = factories.MatterFactory(
        owner=specialist, is_open=False, disposition="COMPLETED", closed_at=timezone.now()
    )

    response = _post(signed_in, matter, description="Liiga hilja.")

    assert response.status_code == 400
    assert CLOSED_MATTER_REFUSAL in response.content.decode()
    assert not _others(matter).exists()


def test_a_reader_cannot_add_one(client, reader, specialist):
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(reader)

    response = _post(client, matter, description="Lugeja ei kirjuta.")

    assert response.status_code == 404
    assert not _others(matter).exists()


# -- visibility ----------------------------------------------------------------------


def test_search_finds_it_for_a_lawyer_and_never_for_a_reader_of_a_restricted_matter(
    signed_in, specialist, reader, restricted_matter
):
    _post(signed_in, restricted_matter, description="Konfidentsiaalne taustamaterjal kalmaar.")

    assert search_documents(query="kalmaar", user=specialist).exists()
    assert not search_documents(query="kalmaar", user=reader).exists()


def test_a_restricted_matters_file_is_not_downloadable_by_a_reader(
    client, signed_in, specialist, reader, restricted_matter
):
    _post(signed_in, restricted_matter, description="Piiratud fail.", files=[_pdf()])
    version = Document.objects.get(matter=restricted_matter).current_version
    client.force_login(reader)

    response = client.get(reverse("documents:download", kwargs={"pk": version.pk}))

    assert response.status_code == 404


# -- correction ------------------------------------------------------------------------


def _edit(client, matter, entry, **data):
    return client.post(
        reverse("matters:edit_entry", kwargs={"pk": matter.pk, "entry_id": entry.pk}),
        data,
        headers={"HX-Request": "true"},
    )


def test_correcting_the_link_keeps_the_earlier_one_in_the_revision(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _post(signed_in, matter, description="Algne kirjeldus.", url="https://example.ee/vana")
    entry = _others(matter).get()

    response = _edit(
        signed_in,
        matter,
        entry,
        body=entry.body,
        url="https://example.ee/uus",
        revision=entry_revision_token(entry),
    )

    assert response.status_code == 200
    entry.refresh_from_db()
    assert entry.url == "https://example.ee/uus"
    revision = EntryRevision.objects.get(entry=entry)
    assert revision.url == "https://example.ee/vana"
    assert revision.body == entry.body
    assert entry.edit_count == 1


def test_a_stale_correction_is_refused_and_writes_nothing(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _post(signed_in, matter, description="Algne.", url="https://example.ee/a")
    entry = _others(matter).get()
    stale = entry_revision_token(entry)
    edit_entry(entry=entry, body="Kolleegi parandus.", actor=specialist, expected_revision=stale)

    response = _edit(
        signed_in, matter, entry, body="Minu parandus.", url="https://example.ee/b", revision=stale
    )

    assert response.status_code == 409
    entry.refresh_from_db()
    assert entry.url == "https://example.ee/a"
    assert "Kolleegi parandus." in entry.body


def test_an_unsafe_corrected_link_is_refused(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _post(signed_in, matter, description="Algne.", url="https://example.ee/a")
    entry = _others(matter).get()

    response = _edit(
        signed_in,
        matter,
        entry,
        body=entry.body,
        url="javascript:alert(1)",
        revision=entry_revision_token(entry),
    )

    assert response.status_code == 400
    entry.refresh_from_db()
    assert entry.url == "https://example.ee/a"
    assert not EntryRevision.objects.filter(entry=entry).exists()


# -- every other entry is untouched -----------------------------------------------------


def test_other_entry_kinds_keep_the_body_only_correction_and_an_empty_url(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    note = add_entry(matter=matter, body="Tavaline märkus.", author=specialist)

    form_html = signed_in.get(
        reverse("matters:edit_entry", kwargs={"pk": matter.pk, "entry_id": note.pk})
    ).content.decode()
    _edit(
        signed_in,
        matter,
        note,
        body="Parandatud märkus.",
        url="https://example.ee/eiri",
        revision=entry_revision_token(note),
    )

    assert 'name="url"' not in form_html
    note.refresh_from_db()
    assert note.url == ""
    assert "Parandatud märkus." in note.body
    assert EntryRevision.objects.get(entry=note).url == ""


# -- a link is a Muu entry's only ---------------------------------------------------


def test_add_entry_refuses_a_link_on_any_other_kind(specialist):
    matter = factories.MatterFactory(owner=specialist)

    for kind in (EntryKind.NOTE, EntryKind.MEETING):
        with refused(ENTRY_URL_ONLY_FOR_OTHER):
            add_entry(
                matter=matter, body="Märkus.", author=specialist, kind=kind, url="https://a.ee/x"
            )
    assert not Entry.objects.filter(matter=matter).exists()


def test_edit_entry_refuses_a_link_on_any_other_kind(specialist):
    matter = factories.MatterFactory(owner=specialist)
    note = add_entry(matter=matter, body="Märkus.", author=specialist)

    with refused(ENTRY_URL_ONLY_FOR_OTHER):
        edit_entry(entry=note, body="Märkus.", actor=specialist, url="https://a.ee/x")

    note.refresh_from_db()
    assert note.url == ""
    assert not EntryRevision.objects.filter(entry=note).exists()


def test_the_database_refuses_a_link_on_any_other_kind(specialist):
    from django.db import IntegrityError, transaction

    matter = factories.MatterFactory(owner=specialist)
    note = add_entry(matter=matter, body="Märkus.", author=specialist)

    with pytest.raises(IntegrityError, match="matters_entry_url_only_other"):
        with transaction.atomic():
            Entry.objects.filter(pk=note.pk).update(url="https://a.ee/x")


def test_an_other_entry_may_carry_a_link_through_the_service(specialist):
    matter = factories.MatterFactory(owner=specialist)

    entry = add_entry(
        matter=matter,
        body="Muu.",
        author=specialist,
        kind=EntryKind.OTHER,
        url="https://a.ee/x",
    )

    assert entry.url == "https://a.ee/x"


# -- a link never carries credentials (create and correct) ---------------------------

#: Userinfo the shared `normalize_web_address(reject_credentials=True)` recognises:
#: name and password, a name alone, a password alone, percent-encoded, with a port.
CREDENTIAL_LINKS = [
    "https://user:password@example.ee/document",
    "https://user@example.ee/x",
    "https://:pw@example.ee/",
    "https://%75ser:%70w@example.ee/doc",
    "http://user:pw@example.ee:8080/x",
    "HTTPS://Admin@Example.ee/",
]
CREDENTIALS_REFUSAL = "Link ei tohi sisaldada kasutajanime ega parooli."


def _field_error(body: str, field_id: str) -> str:
    match = re.search(rf'<p class="[^"]*" [^>]*id="{field_id}_error">([^<]*)</p>', body)
    assert match, f"no error under {field_id}"
    return match.group(1)


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("https://www.example.ee/kommentaar", "https://www.example.ee/kommentaar"),
        ("http://example.ee/vana", "http://example.ee/vana"),
        ("www.example.ee/taust", "https://www.example.ee/taust"),
        # An `@` after the host is part of the path or query, not userinfo.
        ("https://example.ee/kiri@koda", "https://example.ee/kiri@koda"),
        ("https://example.ee/?saatja=info@koda.ee", "https://example.ee/?saatja=info@koda.ee"),
    ],
)
def test_ordinary_links_are_accepted(signed_in, specialist, typed, stored):
    matter = factories.MatterFactory(owner=specialist)

    response = _post(signed_in, matter, description="Tavaline link.", url=typed)

    assert response.status_code == 200
    assert _others(matter).get().url == stored


@pytest.mark.parametrize("bad", ["data:text/html,<script>alert(1)</script>", "javascript:void(0)"])
def test_a_script_scheme_is_refused_under_the_link_field(signed_in, specialist, bad):
    matter = factories.MatterFactory(owner=specialist)

    response = _post(signed_in, matter, description="Skeem ei sobi.", url=bad)

    assert response.status_code == 400
    body = response.content.decode()
    assert "http:// või https://" in _field_error(body, "id_muu_url")
    assert "<script>alert(1)</script>" not in body
    assert not _others(matter).exists()


@pytest.mark.parametrize("bad", CREDENTIAL_LINKS)
def test_a_link_with_credentials_is_refused_on_creation(signed_in, specialist, bad):
    matter = factories.MatterFactory(owner=specialist)

    response = _post(signed_in, matter, description="Paroolita palun.", url=bad, files=[_pdf()])

    assert response.status_code == 400
    body = response.content.decode()
    assert _field_error(body, "id_muu_url") == CREDENTIALS_REFUSAL
    # What was typed stays in the form, so it can be corrected rather than retyped.
    assert "Paroolita palun." in body
    box = re.search(r'<input[^>]*id="id_muu_url"[^>]*>', body).group(0)
    assert f'value="{bad}"' in box
    assert not _others(matter).exists()
    assert not Document.objects.filter(matter=matter).exists()


@pytest.mark.parametrize("bad", CREDENTIAL_LINKS)
def test_the_service_refuses_credentials_on_creation_and_correction(specialist, bad):
    matter = factories.MatterFactory(owner=specialist)
    with refused(CREDENTIALS_REFUSAL):
        add_entry(matter=matter, body="Muu.", author=specialist, kind=EntryKind.OTHER, url=bad)
    entry = add_entry(
        matter=matter, body="Muu.", author=specialist, kind=EntryKind.OTHER, url="https://a.ee/x"
    )

    with refused(CREDENTIALS_REFUSAL):
        edit_entry(entry=entry, body="Muu.", actor=specialist, url=bad)

    entry.refresh_from_db()
    assert entry.url == "https://a.ee/x"
    assert not EntryRevision.objects.filter(entry=entry).exists()
    assert _others(matter).count() == 1


@pytest.mark.parametrize(
    ("bad", "words"),
    [
        *[(link, CREDENTIALS_REFUSAL) for link in CREDENTIAL_LINKS[:3]],
        ("javascript:alert(1)", "http:// või https://"),
        # The form's own length cap (`provider_link_field`) answers first.
        pytest.param("https://example.ee/" + "a" * 990, "1000", id="overlong"),
    ],
)
def test_a_refused_corrected_link_is_shown_under_the_link_box_with_what_was_typed(
    signed_in, specialist, bad, words
):
    matter = factories.MatterFactory(owner=specialist)
    _post(signed_in, matter, description="Algne.", url="https://example.ee/a")
    entry = _others(matter).get()
    token = entry_revision_token(entry)

    response = _edit(signed_in, matter, entry, body="Uus sõnastus.", url=bad, revision=token)

    assert response.status_code == 400
    body = response.content.decode()
    assert words in _field_error(body, f"id_sissekanne_{entry.pk}_url")
    assert "Uus sõnastus." in body
    assert f'value="{bad}"' in body
    entry.refresh_from_db()
    assert entry.url == "https://example.ee/a"
    assert "Algne." in entry.body
    assert entry_revision_token(entry) == token
    assert not EntryRevision.objects.filter(entry=entry).exists()


def test_other_web_address_rules_are_unchanged():
    """The engagement and external-position rules still tolerate userinfo (a
    historical register value nobody can re-issue); only `Muu` was tightened."""
    from app.matters.services import (
        normalize_engagement_url,
        normalize_entry_url,
        normalize_external_position_url,
    )

    link = "https://user:pw@example.ee/x"
    assert normalize_engagement_url(link) == link
    assert normalize_external_position_url(link) == link
    with refused(CREDENTIALS_REFUSAL):
        normalize_entry_url(link)


# -- a correction overtaken by another writer shows their link ---------------------


def test_two_people_correcting_only_the_link_the_second_sees_the_first_ones_link(
    client, signed_in, specialist, other_specialist
):
    from django.test import Client

    matter = factories.MatterFactory(owner=specialist)
    _post(signed_in, matter, description="Sama kirjeldus.", url="https://example.ee/algne")
    entry = _others(matter).get()
    # Both open `Muuda` on the same version.
    token = entry_revision_token(entry)
    colleague = Client()
    colleague.force_login(other_specialist)

    first = _edit(
        colleague, matter, entry, body=entry.body, url="https://example.ee/kolleegi", revision=token
    )
    assert first.status_code == 200

    second = _edit(
        signed_in, matter, entry, body=entry.body, url="https://example.ee/minu", revision=token
    )

    assert second.status_code == 409
    body = second.content.decode()
    panel = body[body.index('class="uxtl__conflict"') :]
    # The winning version, description and link, labelled.
    assert "Praegune sõnastus:" in panel and "Sama kirjeldus." in panel
    assert "Praegune link:" in panel
    assert "data-conflict-url>https://example.ee/kolleegi</p>" in panel
    assert "Sinu tekst ja link on ülal alles." in panel
    # Shown to compare, not offered as a control.
    assert 'href="https://example.ee/kolleegi"' not in body
    # The person's own unsaved link is still in the box, and the token is the
    # one they opened with — nothing adopted the newer version for them.
    url_box = re.search(rf'<input[^>]*id="id_sissekanne_{entry.pk}_url"[^>]*>', body).group(0)
    assert 'value="https://example.ee/minu"' in url_box
    revision_box = re.search(r'<input[^>]*name="revision"[^>]*>', body).group(0)
    assert f'value="{token}"' in revision_box
    # The database holds the first writer's link, one revision keeping the original.
    entry.refresh_from_db()
    assert entry.url == "https://example.ee/kolleegi"
    assert entry.edit_count == 1
    revision = EntryRevision.objects.get(entry=entry)
    assert revision.url == "https://example.ee/algne"
    assert revision.edited_by == other_specialist
    assert "Sama kirjeldus." in revision.body


def test_the_conflict_panel_escapes_the_current_link(signed_in, specialist, other_specialist):
    matter = factories.MatterFactory(owner=specialist)
    _post(signed_in, matter, description="Kirjeldus.", url="https://example.ee/a")
    entry = _others(matter).get()
    token = entry_revision_token(entry)
    # A valid http(s) address whose path is markup-shaped.
    odd = 'https://example.ee/"><script>x</script>'
    edit_entry(
        entry=entry, body=entry.body, actor=other_specialist, url=odd, expected_revision=token
    )

    response = _edit(
        signed_in, matter, entry, body="Minu tekst.", url="https://example.ee/b", revision=token
    )

    assert response.status_code == 409
    body = response.content.decode()
    assert "<script>x</script>" not in body
    escaped = "https://example.ee/&quot;&gt;&lt;script&gt;x&lt;/script&gt;"
    assert f"data-conflict-url>{escaped}</p>" in body


def test_the_conflict_panel_says_when_the_current_version_has_no_link(
    signed_in, specialist, other_specialist
):
    matter = factories.MatterFactory(owner=specialist)
    _post(signed_in, matter, description="Kirjeldus.", url="https://example.ee/a")
    entry = _others(matter).get()
    token = entry_revision_token(entry)
    # Another writer removes the link (an emptied box).
    edit_entry(
        entry=entry, body=entry.body, actor=other_specialist, url="", expected_revision=token
    )

    response = _edit(
        signed_in, matter, entry, body="Minu tekst.", url="https://example.ee/b", revision=token
    )

    assert response.status_code == 409
    body = response.content.decode()
    assert "data-conflict-url>link puudub</p>" in body
    assert 'value="https://example.ee/b"' in body
    entry.refresh_from_db()
    assert entry.url == ""
    assert EntryRevision.objects.get(entry=entry).url == "https://example.ee/a"


def test_a_note_conflict_panel_has_no_link_line(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    note = add_entry(matter=matter, body="Märkus.", author=specialist)
    token = entry_revision_token(note)
    edit_entry(entry=note, body="Kolleegi märkus.", actor=specialist, expected_revision=token)

    response = _edit(signed_in, matter, note, body="Minu märkus.", revision=token)

    assert response.status_code == 409
    body = response.content.decode()
    assert "Praegune sõnastus:" in body
    assert "Praegune link:" not in body
    assert "Sinu tekst on ülal alles." in body


# -- migration 0049 is safe for the release still serving ---------------------------


def test_migration_0049_is_additive_but_for_its_constraint():
    """The release still serving between `migrate` and the swap (and a code-only
    rollback) inserts entries without naming `url`; both columns therefore carry
    a database default (ENG-014). The one operation left for the release decision
    is the CHECK, which every existing row (`url = ''`) satisfies."""
    from django.db.migrations.loader import MigrationLoader

    from app.core.deployment import consequential_operations

    loader = MigrationLoader(None, ignore_no_migrations=True)
    migration = loader.disk_migrations[("matters", "0049_entry_url")]
    before = loader.project_state(("matters", migration.dependencies[0][1]))

    assert list(consequential_operations(migration, state=before)) == ["AddConstraint"]


def test_both_url_columns_default_to_empty_in_the_database():
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name, column_default, is_nullable FROM information_schema.columns"
            " WHERE column_name = 'url'"
            " AND table_name IN ('matters_entry', 'matters_entryrevision')"
        )
        columns = {name: (default, nullable) for name, default, nullable in cursor.fetchall()}

    assert set(columns) == {"matters_entry", "matters_entryrevision"}
    for default, nullable in columns.values():
        assert default.startswith("''") and nullable == "NO"
