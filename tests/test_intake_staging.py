"""Reading the files `Uus teema` is given, before the Teema exists.

Two halves, and the split is the design.

The first half is about what staging must **not** be able to do: create a
Matter, create a Document, reach the search index, be read by somebody else, or
let a suggestion overwrite a person. Those are the properties that make it safe
to upload a file before anything has been decided, and each of them is one
assertion here.

The second half is about the promise it exists to keep: that the bytes which
become evidence are byte-for-byte the bytes the browser sent, that one file
stays one Document, and that a refusal loses none of it (docs/adr/0064).

Every document here is invented. The synthetic corpus writes PDFs at test time;
nothing is a checked-in binary.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from app.documents.enums import DocumentRole, ExtractionState, MalwareScanState
from app.documents.models import Document, DocumentVersion
from app.documents.services import evidence_storage
from app.matters import intake_extraction, intake_staging
from app.matters.intake_suggestions import SuggestedField, analyse_intake
from app.matters.models import Matter
from app.matters.staging import MatterIntakeFile, MatterIntakeSession
from app.organisations.models import Organisation, OrganisationType
from tests import factories
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")
STAGE = reverse("matters:intake_stage")
STATUS = reverse("matters:intake_status")
REMOVE = reverse("matters:intake_remove")

PDF = "application/pdf"

#: A ministry letter stating a deadline, a sender and a subject — the same
#: shapes `tests/test_assisted_intake.py` reads on the edit surface, so a
#: difference between the two surfaces would show up as a difference here.
LETTER = """Näidisministeerium
Suur-Ameerika 1, Tallinn

Eesti Kaubandus-Tööstuskoda
Meie 05.09.2026 nr 1-4/26/1234-2

Pakendiseaduse muutmise seaduse eelnõu kooskõlastamiseks

Lugupeetud Koja esindajad

Saadame Teile kooskõlastamiseks pakendiseaduse muutmise seaduse eelnõu, mis on
registreeritud eelnõude infosüsteemis EIS toimik 26-0123. Eelnõuga muudetakse
pakendite ja jäätmete käitlemise korda ning keskkonnatasu määrasid. Palume
esitada arvamus hiljemalt 18. septembriks 2026. Seadus jõustub 1. jaanuaril 2027.

Lugupidamisega
Mari Näidis
nõunik
E-post: mari.naidis@naidisministeerium.invalid
"""

#: The same envelope, disagreeing about the one date that matters. Two
#: documents each stating a deadline in the words that make one is a conflict,
#: and a conflict pre-fills nothing.
SECOND_LETTER = """Näidisministeerium
Suur-Ameerika 1, Tallinn

Eesti Kaubandus-Tööstuskoda

Pakendiseaduse muutmise seaduse eelnõu kooskõlastamiseks

Palume esitada arvamus hiljemalt 25. septembriks 2026.
"""


def upload(name: str, content: bytes, content_type: str = PDF) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type=content_type)


def letter_pdf(text: str = LETTER) -> bytes:
    return corpus.text_pdf([text])


@pytest.fixture
def ministry(db):
    organisation = Organisation.objects.create(
        name="Näidisministeerium", organisation_type=OrganisationType.MINISTRY
    )
    Organisation.objects.create(name="Näidisamet", organisation_type=OrganisationType.AUTHORITY)
    return organisation


def stage(client, *files, session_id: str = "") -> MatterIntakeSession:
    """Upload through the real route, the way the browser does."""
    payload: dict[str, object] = {"files": list(files)}
    if session_id:
        payload["intake"] = session_id
    response = client.post(STAGE, payload)
    assert response.status_code in (200, 400), response.status_code
    session = response.context["intake_session"]
    assert session is not None
    return session


def read_everything() -> list:
    """Drain the staged queue the way the worker does."""
    return intake_extraction.drain(limit=20)


# ---------------------------------------------------------------------------
# Nothing canonical exists before «Loo teema»
# ---------------------------------------------------------------------------


def test_staging_a_file_creates_no_business_data_at_all(signed_in, evidence_root, ministry):
    """The property the whole design rests on.

    A file is uploaded, validated, stored and read — and afterwards the
    application holds exactly as much business data as it did before anybody
    opened the page. Asserted as a census rather than one row at a time,
    because the failure this is guarding against is a *new* kind of write
    somebody adds later (docs/adr/0064, task §24).
    """
    from app.audit.models import ChangeEvent
    from app.search.models import SearchDocument

    before = {
        "matters": Matter.objects.count(),
        "documents": Document.objects.count(),
        "versions": DocumentVersion.objects.count(),
        "events": ChangeEvent.objects.count(),
        "search": SearchDocument.objects.count(),
        "organisations": Organisation.objects.count(),
    }

    session = stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))
    read_everything()
    signed_in.get(f"{STATUS}?intake={session.pk}")

    after = {
        "matters": Matter.objects.count(),
        "documents": Document.objects.count(),
        "versions": DocumentVersion.objects.count(),
        "events": ChangeEvent.objects.count(),
        "search": SearchDocument.objects.count(),
        "organisations": Organisation.objects.count(),
    }
    assert after == before
    # And the one thing that *does* exist is the staging itself.
    assert MatterIntakeFile.objects.filter(session=session).count() == 1


def test_the_analyser_never_creates_an_organisation_it_recognises(
    signed_in, evidence_root, ministry
):
    """A body named in a letter is matched against the catalogue, never added
    to it. A sender that resembles an organisation is a suggestion; creating
    one would be the analyser filing a fact (docs/adr/0029, docs/adr/0063)."""
    names = set(Organisation.objects.values_list("name", flat=True))

    session = stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))
    read_everything()
    analysis = analyse_intake(session)

    assert set(Organisation.objects.values_list("name", flat=True)) == names
    senders = analysis.fields[SuggestedField.SOURCE_ORGANISATIONS]
    assert [candidate.display for candidate in senders.offered] == ["Näidisministeerium"]


def test_staged_material_is_not_reachable_through_search(signed_in, evidence_root):
    """No projection row, so nothing about a staged file is findable."""
    from app.search.models import SearchDocument

    stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))
    read_everything()

    assert not SearchDocument.objects.exists()


# ---------------------------------------------------------------------------
# The reading: the same parsers, behind the same gate
# ---------------------------------------------------------------------------


def test_the_upload_request_parses_nothing(signed_in, evidence_root):
    """Staging stores and queues; it does not read.

    The security boundary ADR 0060 drew is that no parser runs in a web
    request, and moving the reading earlier must not move the parser with it.
    """
    session = stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))

    [staged] = MatterIntakeFile.objects.filter(session=session)
    assert staged.extraction_state == ExtractionState.PENDING
    assert staged.text == []
    assert staged.text_character_count == 0


def test_the_worker_reads_the_staged_file_and_the_rules_find_what_it_says(
    signed_in, evidence_root, ministry
):
    session = stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))
    read_everything()

    [staged] = MatterIntakeFile.objects.filter(session=session)
    assert staged.extraction_state == ExtractionState.DONE
    assert staged.text_character_count > 0

    analysis = analyse_intake(session)
    deadline = analysis.fields[SuggestedField.RESPONSE_DEADLINE]
    assert deadline.prefill_candidate is not None
    assert deadline.prefill_candidate.value == "2026-09-18"
    assert deadline.prefill_candidate.form_value == "18.9.2026"
    # And the evidence for it, which is what makes it checkable.
    assert "hiljemalt 18. septembriks 2026" in deadline.prefill_candidate.evidence


def test_a_title_is_offered_and_never_pre_filled(signed_in, evidence_root, ministry):
    """The one unconditional rule: no title is pre-filled anywhere, ever."""
    from app.matters.intake_suggestions import CurrentValues, prefill_controls, prefill_initial

    session = stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))
    read_everything()
    analysis = analyse_intake(session)

    titles = analysis.fields[SuggestedField.TITLE]
    assert any("Pakendiseaduse" in candidate.display for candidate in titles.offered)

    _initial, annotated = prefill_initial(analysis, base={}, current=CurrentValues())
    assert SuggestedField.TITLE not in annotated.prefilled
    assert all(name != SuggestedField.TITLE for name, _ in prefill_controls(annotated))


def test_the_panel_claims_no_pre_fill_it_cannot_know_about(signed_in, evidence_root, ministry):
    """The server proposes; the browser decides. So the server does not say it
    decided.

    On `Muuda teemat` a HIGH candidate that filled an empty control is marked
    «vormil eeltäidetud», and that is true there because the GET filled it. Here
    the control may already hold something somebody typed a second ago, which no
    GET can see — so nothing is marked, every candidate keeps its «Kasuta», and
    the button says what happened because it reads the live control
    (docs/adr/0064).
    """
    session = stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))
    read_everything()

    answer = signed_in.get(f"{STATUS}?intake={session.pk}")
    analysis = answer.context["assisted"]

    assert analysis.prefilled == {}
    assert not any(
        candidate.prefilled
        for suggestions in analysis.fields.values()
        for candidate in suggestions.candidates
    )
    body = answer.content.decode()
    assert "vormil eeltäidetud" not in body
    # The decision itself is still made, and is still what the browser is told.
    assert dict(answer.context["intake_prefill"])["response_deadline"] == "18.9.2026"


def test_no_scan_state_can_keep_a_staged_file_out_of_the_queue(signed_in, evidence_root, settings):
    """The gate is gone, and this is the test that says so (task §3, §27.10).

    Until docs/adr/0072 a staged file was offered to a parser only once a
    scanner had written ``CLEAN``, and on the deployed stack nothing ever
    could — so `Uus teema` showed «Loen faili…» for as long as anybody was
    willing to watch. The column survives as dead schema; what must be true now
    is that **no value in it changes anything**, in either environment.
    """
    session = stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))
    staged = MatterIntakeFile.objects.get(session=session)

    for real_data in (True, False):
        settings.REAL_DATA_ALLOWED = real_data
        for state in MalwareScanState.values:
            MatterIntakeFile.objects.filter(pk=staged.pk).update(malware_scan_state=state)
            assert intake_extraction.pending_intake_files().filter(pk=staged.pk).exists(), (
                f"REAL_DATA_ALLOWED={real_data}, scan={state}: the staged file was withheld"
            )


def test_a_file_that_cannot_be_read_is_still_kept_and_still_becomes_evidence(
    signed_in, evidence_root
):
    """A valid file may be unreadable, and that must not block anything.

    The page says so in words, `Loo teema` stays pressable, and the file
    becomes ordinary evidence — automatic reading is help, not a gate
    (task §21, §33).
    """
    session = stage(signed_in, upload("katki.pdf", corpus.corrupt_pdf()))
    read_everything()

    staged = MatterIntakeFile.objects.get(session=session)
    assert staged.extraction_state == ExtractionState.FAILED
    assert staged.extraction_note

    answer = signed_in.get(f"{STATUS}?intake={session.pk}")
    assert answer.context["intake_state"] == "ready"
    assert answer.context["intake_unreadable"]
    # Words a person can act on, and none of the stored vocabulary.
    body = answer.content.decode()
    assert "PENDING" not in body and "FAILED" not in body

    signed_in.post(CREATE, {"title": "Loetamatu", "intake": str(session.pk)})
    matter = Matter.objects.get(title="Loetamatu")
    document = Document.objects.get(matter=matter)
    assert document.current_version.original_filename == "katki.pdf"


def test_reading_is_reported_in_words_rather_than_states(signed_in, evidence_root):
    session = stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))

    answer = signed_in.get(f"{STATUS}?intake={session.pk}")
    body = answer.content.decode()

    assert answer.context["intake_state"] == "reading"
    assert "Loen faili" in body
    assert "PENDING" not in body
    assert "Teksti töötlemine ootel" not in body
    assert "Teksti eraldamine ei kohaldu" not in body


# ---------------------------------------------------------------------------
# «Loo teema»: the bytes, and one file one Document
# ---------------------------------------------------------------------------


def test_the_evidence_bytes_are_the_bytes_the_browser_sent(signed_in, evidence_root, ministry):
    """SHA-256 of what was staged equals SHA-256 of what became evidence.

    The whole feature turns on this. Nothing is reconstructed from extracted
    text and nothing is re-uploaded; the version that lands is the file
    (task §18A).
    """
    first, second = letter_pdf(), corpus.government_pdf()
    session = stage(signed_in, upload("kaaskiri.pdf", first), upload("eelnou.pdf", second))
    staged_digests = {
        staged.original_filename: staged.sha256
        for staged in MatterIntakeFile.objects.filter(session=session)
    }
    assert staged_digests == {
        "kaaskiri.pdf": hashlib.sha256(first).hexdigest(),
        "eelnou.pdf": hashlib.sha256(second).hexdigest(),
    }

    signed_in.post(CREATE, {"title": "Baidid", "intake": str(session.pk)})

    matter = Matter.objects.get(title="Baidid")
    storage = evidence_storage()
    for version in DocumentVersion.objects.filter(document__matter=matter):
        with storage.open(version.storage_key, "rb") as handle:
            stored = handle.read()
        assert hashlib.sha256(stored).hexdigest() == version.sha256
        assert version.sha256 == staged_digests[version.original_filename]


def test_three_staged_files_become_three_documents(signed_in, evidence_root, ministry):
    session = stage(
        signed_in,
        upload("kaaskiri.pdf", letter_pdf()),
        upload("eelnou.pdf", corpus.government_pdf()),
        upload("seletuskiri.pdf", corpus.text_pdf(["Seletuskiri eelnõu juurde."])),
    )
    read_everything()

    signed_in.post(CREATE, {"title": "Kolm faili", "intake": str(session.pk)})

    matter = Matter.objects.get(title="Kolm faili")
    documents = Document.objects.filter(matter=matter)
    assert documents.count() == 3
    assert {document.current_version.original_filename for document in documents} == {
        "kaaskiri.pdf",
        "eelnou.pdf",
        "seletuskiri.pdf",
    }
    # One file, one Document, one immutable first version — and the ordinary
    # provenance every other capture path records.
    for document in documents:
        assert document.role == DocumentRole.INCOMING_AUTHORITY
        assert document.versions.count() == 1
        assert document.current_version.version_number == 1
        assert document.current_version.malware_scan_state == MalwareScanState.PENDING
        # Read on the form, and therefore finished. Not PENDING: a corpus run
        # must never open these bytes again merely because a Matter now exists
        # to hang them off (docs/adr/0072, task §7).
        assert document.current_version.extraction_state == ExtractionState.INTAKE_READ


def test_an_email_is_filed_under_the_role_its_name_earns_it(signed_in, evidence_root):
    """The role is intake's own rule, decided at upload and carried through."""
    session = stage(
        signed_in,
        upload(
            "kiri.eml",
            corpus.consultation_eml(attachments=False, inline_logo=False),
            "message/rfc822",
        ),
    )
    signed_in.post(CREATE, {"title": "Kiri", "intake": str(session.pk)})

    matter = Matter.objects.get(title="Kiri")
    assert Document.objects.get(matter=matter).role == DocumentRole.ORIGINAL_EMAIL


def test_the_matter_is_created_by_the_ordinary_services(signed_in, evidence_root):
    """Promotion composes `create_document` and `add_evidence_version`, so the
    audit trail says what it says for every other capture."""
    from app.audit.enums import ChangeEventType
    from app.audit.models import ChangeEvent

    session = stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))
    signed_in.post(CREATE, {"title": "Tavaline", "intake": str(session.pk)})

    matter = Matter.objects.get(title="Tavaline")
    assert (
        ChangeEvent.objects.filter(matter=matter, event_type=ChangeEventType.MATTER_CREATED).count()
        == 1
    )


def test_a_consumed_session_is_a_closed_door_rather_than_an_empty_one(signed_in, evidence_root):
    """A double submit must not file the same envelope twice."""
    session = stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))
    signed_in.post(CREATE, {"title": "Esimene", "intake": str(session.pk)})

    session.refresh_from_db()
    assert session.consumed_at is not None
    assert intake_staging.get_session(owner=session.owner, session_id=str(session.pk)) is None

    signed_in.post(CREATE, {"title": "Teine", "intake": str(session.pk)})
    assert Document.objects.filter(matter__title="Teine").count() == 0
    assert Document.objects.filter(matter__title="Esimene").count() == 1


# ---------------------------------------------------------------------------
# Removing, refusing, and keeping what was typed
# ---------------------------------------------------------------------------


def test_a_removed_file_stops_suggesting_and_never_becomes_a_document(
    signed_in, evidence_root, ministry
):
    """The browser and the server must not disagree about a file half gone.

    A removed row is absent from the list, absent from the analysis and absent
    from what `Loo teema` files, and all three are the same query (task §17,
    §30).
    """
    session = stage(
        signed_in,
        upload("esimene.pdf", letter_pdf()),
        upload("teine.pdf", letter_pdf(SECOND_LETTER)),
    )
    read_everything()
    assert analyse_intake(session).fields[SuggestedField.RESPONSE_DEADLINE].conflict

    removed = MatterIntakeFile.objects.get(session=session, original_filename="esimene.pdf")
    answer = signed_in.post(REMOVE, {"intake": str(session.pk), "fail": str(removed.pk)})
    assert answer.status_code == 200
    assert [row["filename"] for row in answer.context["intake_files"]] == ["teine.pdf"]

    # The conflict goes with it, and what is left is the surviving letter's own
    # deadline rather than an arbitrary choice between the two.
    deadline = analyse_intake(session).fields[SuggestedField.RESPONSE_DEADLINE]
    assert not deadline.conflict
    assert deadline.prefill_candidate.value == "2026-09-25"

    signed_in.post(CREATE, {"title": "Ainult teine", "intake": str(session.pk)})
    matter = Matter.objects.get(title="Ainult teine")
    names = [
        document.current_version.original_filename
        for document in Document.objects.filter(matter=matter)
    ]
    assert names == ["teine.pdf"]


def test_two_documents_disagreeing_about_the_deadline_pre_fill_nothing(
    signed_in, evidence_root, ministry
):
    """Two strong answers are no answer, on this surface too (task §31)."""
    from app.matters.intake_suggestions import CurrentValues, prefill_controls, prefill_initial

    session = stage(
        signed_in,
        upload("esimene.pdf", letter_pdf()),
        upload("teine.pdf", letter_pdf(SECOND_LETTER)),
    )
    read_everything()
    analysis = analyse_intake(session)

    deadline = analysis.fields[SuggestedField.RESPONSE_DEADLINE]
    assert deadline.conflict
    assert deadline.prefill_candidate is None
    assert {candidate.value for candidate in deadline.offered} == {"2026-09-18", "2026-09-25"}
    assert deadline.note

    _initial, annotated = prefill_initial(analysis, base={}, current=CurrentValues())
    filled = dict(prefill_controls(annotated))
    assert SuggestedField.RESPONSE_DEADLINE not in filled


def test_a_refused_save_keeps_the_staged_files_the_suggestions_and_the_typing(
    signed_in, evidence_root, ministry
):
    """The whole point of staging surviving a refusal (task §20, §32)."""
    session = stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))
    read_everything()

    refused = signed_in.post(
        CREATE,
        {
            "title": "Poolik",
            "intake": str(session.pk),
            # The ordinary refusal: a valdkond ticked with nothing written.
            "policy_area_other_selected": "on",
        },
    )
    assert refused.status_code == 400
    assert not Matter.objects.filter(title="Poolik").exists()

    # The files are still there, still read, still suggesting.
    assert refused.context["intake_session"].pk == session.pk
    assert [row["filename"] for row in refused.context["intake_files"]] == ["kaaskiri.pdf"]
    assert refused.context["assisted"] is not None
    assert refused.context["assisted"].fields[SuggestedField.RESPONSE_DEADLINE].offered
    # And what was typed comes back with them.
    assert refused.context["form"]["title"].value() == "Poolik"

    # Corrected and resubmitted, with no second upload of anything.
    signed_in.post(
        CREATE,
        {
            "title": "Poolik",
            "intake": str(session.pk),
            "policy_area_other_selected": "on",
            "policy_area_other": "Ehitus",
        },
    )
    matter = Matter.objects.get(title="Poolik")
    assert Document.objects.filter(matter=matter).count() == 1


def test_staged_held_and_freshly_chosen_files_all_land_once(signed_in, evidence_root):
    """The three paths compose, in the order somebody offered them in."""
    session = stage(signed_in, upload("staged.pdf", letter_pdf()))
    refused = signed_in.post(
        CREATE,
        {
            "title": "Kolm teed",
            "intake": str(session.pk),
            "policy_area_other_selected": "on",
            "files": upload("held.pdf", corpus.government_pdf()),
        },
    )
    keys = [item.key for item in refused.context["held_files"]]
    assert keys

    signed_in.post(
        CREATE,
        {
            "title": "Kolm teed",
            "intake": str(session.pk),
            "policy_area_other_selected": "on",
            "policy_area_other": "Ehitus",
            "pending": keys,
            "files": upload("chosen.pdf", corpus.government_pdf()),
        },
    )

    matter = Matter.objects.get(title="Kolm teed")
    names = [
        document.current_version.original_filename
        for document in Document.objects.filter(matter=matter).order_by("created_at")
    ]
    assert names == ["staged.pdf", "held.pdf", "chosen.pdf"]


# ---------------------------------------------------------------------------
# Only the initial files
# ---------------------------------------------------------------------------


def test_a_document_uploaded_later_reinterprets_nothing(
    signed_in, specialist, evidence_root, ministry, capture_evidence, extract
):
    """The explicit product boundary.

    Assisted reading belongs to the files a Teema is created from. A file added
    to an existing Matter is processed and indexed and changes no field of it —
    no staging is started, no suggestion workflow runs, and the title, sender,
    deadline, track and areas are exactly what the person confirmed (task §14,
    §35).
    """
    session = stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))
    read_everything()
    signed_in.post(
        CREATE,
        {"title": "Algne pealkiri", "intake": str(session.pk), "response_deadline": "1.10.2026"},
    )
    matter = Matter.objects.get(title="Algne pealkiri")
    before = {
        "title": matter.title,
        "senders": set(matter.source_organisation_ids),
        "deadline": matter.response_deadline,
        "track": matter.track,
        "areas": set(matter.policy_areas.values_list("pk", flat=True)),
    }

    # A second letter arrives a week later, stating a different deadline and a
    # different heading, and goes through the ordinary evidence path.
    later = capture_evidence(matter, letter_pdf(SECOND_LETTER), "hiljem.pdf", PDF)
    extract(later)

    matter.refresh_from_db()
    assert {
        "title": matter.title,
        "senders": set(matter.source_organisation_ids),
        "deadline": matter.response_deadline,
        "track": matter.track,
        "areas": set(matter.policy_areas.values_list("pk", flat=True)),
    } == before
    # And no staging was started by it.
    assert not MatterIntakeSession.objects.filter(consumed_at__isnull=True).exists()


def test_the_explicit_post_create_review_is_still_there(signed_in, specialist, evidence_root):
    """`Kontrolli dokumendist leitud andmeid` remains, as the manual re-check."""
    matter = factories.MatterFactory(owner=specialist)
    answer = signed_in.get(reverse("matters:matter_edit_assisted", kwargs={"pk": matter.pk}))
    assert answer.status_code == 200


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_another_person_cannot_read_remove_or_consume_somebody_else_s_staging(
    client, signed_in, specialist, other_specialist, evidence_root, ministry
):
    """Fail closed, and silent about which of the reasons applies (task §34)."""
    session = stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))
    read_everything()
    staged = MatterIntakeFile.objects.get(session=session)

    client.force_login(other_specialist)

    assert client.get(f"{STATUS}?intake={session.pk}").status_code == 404
    assert (
        client.post(REMOVE, {"intake": str(session.pk), "fail": str(staged.pk)}).status_code == 404
    )

    # Consuming it is not a 404 — filing a Teema is something they may do — but
    # somebody else's files must not come with it.
    client.post(CREATE, {"title": "Varastatud", "intake": str(session.pk)})
    matter = Matter.objects.get(title="Varastatud")
    assert Document.objects.filter(matter=matter).count() == 0
    session.refresh_from_db()
    assert session.consumed_at is None
    assert staged.session.files.live().count() == 1


def test_a_reader_who_may_not_write_business_content_reaches_none_of_it(
    client, reader, evidence_root
):
    """The same 404 the rest of the write surfaces give (docs/adr/0037)."""
    client.force_login(reader)

    assert client.post(STAGE, {"files": upload("a.pdf", letter_pdf())}).status_code == 404
    assert client.get(f"{STATUS}?intake=whatever").status_code == 404
    assert client.post(REMOVE, {"intake": "x", "fail": "y"}).status_code == 404


def test_an_anonymous_caller_is_sent_to_sign_in(client, evidence_root):
    """Signed out is sent to sign in, not told the route does not exist.

    `@login_required` outside `@business_write_required`, which is the order
    the rest of the write surfaces compose them in (`app.core.decorators`).
    """
    for response in (client.post(STAGE, {}), client.get(STATUS), client.post(REMOVE, {})):
        assert response.status_code == 302


def test_an_identifier_that_is_not_one_answers_404_rather_than_raising(signed_in, evidence_root):
    """A malformed identifier is the same answer as a wrong one.

    A 500 is a different answer from a 404, and the difference is exactly what
    somebody probing for what exists would read.
    """
    assert signed_in.get(f"{STATUS}?intake=ei-ole-uuid").status_code == 404
    assert signed_in.post(REMOVE, {"intake": "x", "fail": "y"}).status_code == 404

    # And on the save path it is simply "no staged files", not a refusal.
    signed_in.post(CREATE, {"title": "Vigane viide", "intake": "ei-ole-uuid"})
    assert Matter.objects.filter(title="Vigane viide").exists()


def test_an_expired_session_is_unreachable_and_is_not_promoted(signed_in, evidence_root):
    from django.utils import timezone

    session = stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))
    MatterIntakeSession.objects.filter(pk=session.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )

    assert signed_in.get(f"{STATUS}?intake={session.pk}").status_code == 404
    signed_in.post(CREATE, {"title": "Aegunud", "intake": str(session.pk)})
    assert Document.objects.filter(matter__title="Aegunud").count() == 0


# ---------------------------------------------------------------------------
# Validation, budgets and lifecycle
# ---------------------------------------------------------------------------


def test_the_upload_validator_is_the_same_one_the_save_uses(signed_in, evidence_root):
    """A file staging refuses is a file `Loo teema` would have refused."""
    answer = signed_in.post(STAGE, {"files": upload("pilt.exe", b"MZ not a document")})

    assert answer.status_code == 400
    assert answer.context["intake_error"]
    assert not MatterIntakeFile.objects.exists()


def test_a_good_file_beside_a_bad_one_is_still_kept(signed_in, evidence_root):
    """The refusal names the problem; the files that were fine are not lost."""
    answer = signed_in.post(
        STAGE,
        {"files": [upload("hea.pdf", letter_pdf()), upload("halb.exe", b"MZ")]},
    )

    assert answer.status_code == 400
    assert answer.context["intake_error"]
    assert [row["filename"] for row in answer.context["intake_files"]] == ["hea.pdf"]


def test_the_analysis_budget_bounds_the_loading_and_not_only_the_reading(
    signed_in, evidence_root, ministry
):
    """Text the budget will not reach stays in the database.

    The property the Matter surface has, kept here: the plan is made from the
    recorded character count with the text deferred, so a file that is not
    admitted is never loaded (docs/adr/0060, docs/adr/0064).
    """
    from app.matters.intake_suggestions.input import build_intake_analysis_input

    session = stage(
        signed_in,
        upload("a.pdf", letter_pdf()),
        upload("b.pdf", letter_pdf(SECOND_LETTER)),
    )
    read_everything()

    analysis_input = build_intake_analysis_input(session, document_limit=1)
    read, unread = analysis_input.analysed, analysis_input.skipped_for_budget
    assert len(read) == 1
    assert len(unread) == 1
    # Said rather than silently dropped: a short answer beside unread material
    # must never read as «there was nothing in there».
    assert analysis_input.partial


def test_analysis_does_not_grow_a_query_per_file(signed_in, evidence_root, ministry):
    """One read, whatever the envelope holds (task §37)."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    one = stage(signed_in, upload("a.pdf", letter_pdf()))
    read_everything()
    with CaptureQueriesContext(connection) as single:
        analyse_intake(one)

    many = stage(
        signed_in,
        upload("b.pdf", letter_pdf()),
        upload("c.pdf", letter_pdf(SECOND_LETTER)),
        upload("d.pdf", corpus.government_pdf()),
    )
    read_everything()
    with CaptureQueriesContext(connection) as several:
        analyse_intake(many)

    assert len(several.captured_queries) == len(single.captured_queries)


def test_the_status_route_is_a_couple_of_queries_and_reads_no_bytes(
    signed_in, evidence_root, ministry
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    session = stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))
    with CaptureQueriesContext(connection) as while_reading:
        signed_in.get(f"{STATUS}?intake={session.pk}")

    # While nothing has been read there is provably nothing to suggest, so the
    # analyser is not run at all and the catalogue is not loaded.
    assert len(while_reading.captured_queries) < 12


def test_sweeping_removes_consumed_and_expired_staging_and_touches_no_evidence(
    signed_in, evidence_root
):
    from django.utils import timezone

    consumed = stage(signed_in, upload("a.pdf", letter_pdf()))
    signed_in.post(CREATE, {"title": "Kasutatud", "intake": str(consumed.pk)})
    abandoned = stage(signed_in, upload("b.pdf", letter_pdf()))
    MatterIntakeSession.objects.filter(pk=abandoned.pk).update(
        expires_at=timezone.now() - timedelta(hours=1)
    )
    live = stage(signed_in, upload("c.pdf", letter_pdf()))

    documents_before = Document.objects.count()
    versions_before = DocumentVersion.objects.count()

    report = intake_staging.sweep_stale_sessions()

    assert report.sessions == 2
    assert not MatterIntakeSession.objects.filter(pk__in=[consumed.pk, abandoned.pk]).exists()
    assert MatterIntakeSession.objects.filter(pk=live.pk).exists()
    # Canonical evidence is untouched, including the evidence the consumed
    # session produced.
    assert Document.objects.count() == documents_before
    assert DocumentVersion.objects.count() == versions_before
    for version in DocumentVersion.objects.all():
        with evidence_storage().open(version.storage_key, "rb") as handle:
            assert hashlib.sha256(handle.read()).hexdigest() == version.sha256


def test_the_held_upload_sweeper_cannot_reach_staged_bytes(signed_in, evidence_root):
    """Two lifetimes in one storage class, kept apart by a prefix.

    `pending._sweep` deletes objects at the *root* of the held-uploads store by
    age and lists names rather than walking the tree. Staged objects sit one
    directory down, which is why they survive it — and why they need a sweeper
    of their own (`app/matters/intake_staging.py`).
    """
    from app.documents import pending

    session = stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))
    staged = MatterIntakeFile.objects.get(session=session)
    assert staged.storage_key.startswith(f"{intake_staging.STORAGE_PREFIX}/")

    pending._sweep()

    with intake_staging.staging_storage().open(staged.storage_key, "rb") as handle:
        assert hashlib.sha256(handle.read()).hexdigest() == staged.sha256


def test_promotion_refuses_rather_than_files_something_that_is_not_the_file(
    signed_in, evidence_root
):
    """Silent corruption of evidence is the one failure this may not have."""
    from django.core.files.base import ContentFile

    from app.core.errors import DomainError

    session = stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))
    staged = MatterIntakeFile.objects.get(session=session)
    storage = intake_staging.staging_storage()
    storage.delete(staged.storage_key)
    storage.save(staged.storage_key, ContentFile(b"%PDF-1.4 keegi kirjutas ule"))

    matter = factories.MatterFactory()
    with pytest.raises(DomainError):
        intake_staging.promote_intake_files(session=session, matter=matter, actor=session.owner)


# ---------------------------------------------------------------------------
# One engine, two surfaces
# ---------------------------------------------------------------------------


def test_the_staged_and_matter_surfaces_read_the_same_letter_the_same_way(
    signed_in, specialist, evidence_root, ministry, capture_evidence, extract
):
    """There is one suggestion engine, and this is what that claim means.

    The same bytes, read once as a staged intake file and once as a Matter's
    document, must produce the same field, the same value and the same
    confidence. A second set of rules for `Uus teema` would show up here as a
    difference (docs/adr/0064).
    """
    from app.matters.intake_suggestions import analyse_matter

    content = letter_pdf()

    session = stage(signed_in, upload("kaaskiri.pdf", content))
    read_everything()
    staged = analyse_intake(session)

    matter = factories.MatterFactory(owner=specialist, title="Ükskõik")
    extract(capture_evidence(matter, content, "kaaskiri.pdf", PDF))
    existing = analyse_matter(matter, specialist)

    def shape(analysis):
        return {
            name: [
                (candidate.value, candidate.confidence, candidate.rule)
                for candidate in suggestions.offered
            ]
            for name, suggestions in analysis.fields.items()
        }

    assert shape(staged) == shape(existing)


# ---------------------------------------------------------------------------
# R2-03 — an answered Saatja is not an empty one
# ---------------------------------------------------------------------------
#
# QA typed a sender the catalogue does not hold, pressed `+`, and an uploaded
# document then produced a HIGH intake suggestion for a different one. The
# initial behaviour was correct — the suggestion was offered and not applied,
# because the person had answered. Then another field refused validation, and
# on the redisplay `source_organisations` was empty while `sender_name` still
# held the provisional value. Intake read that as *no sender* and applied
# `Kliimaministeerium`; the next successful save persisted both — the sender the
# person chose and the one they had visibly declined.
#
# Saatja has two controls and either of them is an answer. The rule has to hold
# across the refusal, because that is the one render where the browser's own
# record of what has been touched has gone with the old document.


def _refused_create(client, session, **fields):
    """A `Uus teema` POST that refuses on the title, carrying `fields`."""
    payload = {"title": "", "intake": str(session.pk), **fields}
    response = client.post(CREATE, payload)
    assert response.status_code == 400, response.status_code
    return response


def _proposed(response) -> dict:
    return dict(response.context["intake_prefill"])


def test_a_letter_alone_still_pre_fills_the_sender_it_names(signed_in, evidence_root, ministry):
    """A. Nothing manual, one HIGH sender — the feature still works (§24)."""
    session = stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))
    read_everything()

    answer = signed_in.get(f"{STATUS}?intake={session.pk}")

    assert _proposed(answer)[SuggestedField.SOURCE_ORGANISATIONS] == str(ministry.pk)


def test_a_chosen_sender_is_never_proposed_over(signed_in, evidence_root, ministry):
    """B. A canonical sender is an answer, and the refusal redisplay knows it."""
    other = Organisation.objects.create(
        name="Kliimakaitse Amet", organisation_type=OrganisationType.AUTHORITY
    )
    session = stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))
    read_everything()

    refused = _refused_create(signed_in, session, source_organisations=[str(other.pk)])

    assert SuggestedField.SOURCE_ORGANISATIONS not in _proposed(refused)


def test_a_provisional_sender_is_never_proposed_over(signed_in, evidence_root, ministry):
    """C. The finding itself: `+` writes `sender_name`, and that is an answer."""
    session = stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))
    read_everything()

    refused = _refused_create(signed_in, session, sender_name="Kliimakaitse Liit")

    assert SuggestedField.SOURCE_ORGANISATIONS not in _proposed(refused)
    # And it is still on the form, to be saved.
    assert refused.context["form"].data.get("sender_name") == "Kliimakaitse Liit"


def test_the_other_suggestions_survive_the_sender_rule(signed_in, evidence_root, ministry):
    """D. Narrow. A sender the person answered must not silence the deadline.

    §24: the fix is *no silent auto-application after a user answer*, not *hide
    every suggestion once a sender exists*.
    """
    session = stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))
    read_everything()

    proposed = _proposed(_refused_create(signed_in, session, sender_name="Kliimakaitse Liit"))

    assert proposed[SuggestedField.RESPONSE_DEADLINE] == "18.9.2026"
    assert SuggestedField.TITLE in proposed
    assert SuggestedField.POLICY_AREAS in proposed


def test_correcting_the_refused_field_saves_only_the_intended_sender(
    signed_in, evidence_root, ministry, specialist
):
    """E. The whole sequence, ending at what the database holds.

    The point of the finding is not the panel; it is that the next successful
    save wrote both senders. One of them the person never chose.
    """
    session = stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))
    read_everything()
    _refused_create(signed_in, session, sender_name="Kliimakaitse Liit")

    created = signed_in.post(
        CREATE,
        {
            "title": "Pakendiseaduse muutmise seaduse eelnõu",
            "sender_name": "Kliimakaitse Liit",
            "intake": str(session.pk),
        },
    )
    assert created.status_code == 302, created.status_code

    matter = Matter.objects.get(title="Pakendiseaduse muutmise seaduse eelnõu")
    names = sorted(o.name for o in matter.source_organisations.all())
    assert names == ["Kliimakaitse Liit"], names
    assert ministry.name not in names


def test_an_explicit_choice_still_applies_the_suggested_sender(
    signed_in, evidence_root, ministry, specialist
):
    """F. «Kasuta» is a user action and stays available (§24).

    The suggestion is still offered on the refused redisplay — it is simply not
    applied — so choosing it is still one click, and choosing it wins.
    """
    session = stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))
    read_everything()
    refused = _refused_create(signed_in, session, sender_name="Kliimakaitse Liit")

    offered = refused.context["assisted"].fields[SuggestedField.SOURCE_ORGANISATIONS]
    assert any(candidate.value == str(ministry.pk) for candidate in offered.offered)

    created = signed_in.post(
        CREATE,
        {
            "title": "Pakendiseaduse muutmise seaduse eelnõu",
            "source_organisations": [str(ministry.pk)],
            "intake": str(session.pk),
        },
    )
    assert created.status_code == 302, created.status_code

    matter = Matter.objects.get(title="Pakendiseaduse muutmise seaduse eelnõu")
    assert [o.name for o in matter.source_organisations.all()] == [ministry.name]


def test_text_left_in_the_search_box_is_not_an_answer(signed_in, evidence_root, ministry):
    """G. Typing is not creating, and it is not answering either.

    The find box has no `name` and posts nothing (docs/adr/0073), so a
    half-typed «Kliima» nobody committed cannot reach the server and cannot
    count. Only `+` — which writes `sender_name` — commits.
    """
    session = stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))
    read_everything()

    refused = _refused_create(signed_in, session, sender_name="")

    assert _proposed(refused)[SuggestedField.SOURCE_ORGANISATIONS] == str(ministry.pk)


def test_the_sender_to_addressee_default_still_composes(
    signed_in, evidence_root, ministry, specialist
):
    """§25. The R2-03 fix must not disturb the rule beside it (ADR 0069)."""
    session = stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))
    read_everything()

    created = signed_in.post(
        CREATE,
        {
            "title": "Pakendiseaduse muutmise seaduse eelnõu",
            "source_organisations": [str(ministry.pk)],
            "intake": str(session.pk),
        },
    )
    assert created.status_code == 302, created.status_code

    matter = Matter.objects.get(title="Pakendiseaduse muutmise seaduse eelnõu")
    assert matter.addressee_organisation == ministry


def test_a_manual_addressee_override_survives_the_sender_rule(
    signed_in, evidence_root, ministry, specialist
):
    """§25, the other half: a stated override is not taken back."""
    other = Organisation.objects.create(
        name="Kliimakaitse Amet", organisation_type=OrganisationType.AUTHORITY
    )
    session = stage(signed_in, upload("kaaskiri.pdf", letter_pdf()))
    read_everything()

    created = signed_in.post(
        CREATE,
        {
            "title": "Pakendiseaduse muutmise seaduse eelnõu",
            "source_organisations": [str(ministry.pk)],
            "addressee_organisation": str(other.pk),
            "addressee_is_manual": "1",
            "intake": str(session.pk),
        },
    )
    assert created.status_code == 302, created.status_code

    matter = Matter.objects.get(title="Pakendiseaduse muutmise seaduse eelnõu")
    assert matter.addressee_organisation == other
