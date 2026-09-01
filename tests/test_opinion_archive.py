"""Stage 2H — the opinions archive, its matching rules and what it may assert.

The tests are organised around the two questions the stage exists to answer
honestly: *which Matter does this letter belong to*, and *may we say Koda sent
it*. Most of them assert a refusal, because the expensive failure here is not a
missing record — it is a confident wrong one that nobody can tell from a right
one three years later.

Everything is synthetic. The real corpus was read to design these rules and is
never committed (Stage-2H brief 50, 72, 75–81).
"""

from __future__ import annotations

import datetime
import zipfile

import pytest
from django.utils import timezone

from app.core.enums import Visibility
from app.documents.enums import DocumentRole
from app.documents.models import Document, DocumentVersion
from app.documents.services import add_evidence_version, create_document
from app.legacy_import.opinion_apply import apply_plan, open_batch
from app.legacy_import.opinion_archive import (
    OpinionArchiveItem,
    OpinionArchiveMetadata,
    OpinionMatchCandidate,
    OpinionSubmissionImport,
)
from app.legacy_import.opinion_enums import (
    OpinionConflict,
    OpinionMatchClass,
    OpinionSignal,
    RecipientBasis,
    SentDateBasis,
)
from app.legacy_import.opinion_plan import build_plan
from app.legacy_import.opinion_sources import (
    OpinionSourceError,
    read_kodadash_artifact,
    read_opinion_archive,
)
from app.legacy_import.parser import SOURCE_SYSTEM
from app.legacy_import.source_pages import (
    LegacySourcePage,
    LegacySourceResource,
    MatterSourcePage,
    SourceMatchMethod,
)
from app.matters.models import Matter
from app.submissions.enums import (
    RecipientRole,
    SentAtPrecision,
    SubmissionKind,
    SubmissionStatus,
)
from app.submissions.models import Submission, SubmissionRecipient
from tests import factories
from tests import synthetic_opinions as syn

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures — a small synthetic register and archive
# ---------------------------------------------------------------------------


def register_matter(
    *,
    year: int,
    number: int,
    title: str,
    sent: str | None,
    counterparty: str,
    visibility: str = Visibility.NORMAL,
) -> Matter:
    """A Matter as the Excel importer would have left it.

    ``VÄLJA`` has no canonical column, so it lives in the preserved raw row and
    is read back through the year's contract — column F for every era, and
    column G is the counterparty whose *meaning* flips in 2020.
    """
    matter = factories.ArchiveMatterFactory(
        reference_year=year, reference_number=number, title=title, visibility=visibility
    )
    factories.MatterSourceReferenceFactory(
        matter=matter,
        source_system=SOURCE_SYSTEM,
        source_sheet=str(year),
        source_row_number=number,
        source_row_raw={
            "A": f"{year}_{number}",
            "B": title,
            "F": sent or "",
            "G": counterparty,
        },
    )
    return matter


@pytest.fixture
def archive_path(tmp_path):
    def build(opinions):
        return syn.write_archive(tmp_path / "Opinions.zip", opinions)

    return build


def plan_for(archive, kodadash=None):
    return build_plan(archive_path=archive, kodadash_path=kodadash)


def proposal_for(plan, opinion):
    return next(p for p in plan.proposals if p.sha256 == opinion.sha256)


# ---------------------------------------------------------------------------
# Reading the archive
# ---------------------------------------------------------------------------


def test_entry_names_are_recovered_whether_or_not_the_utf8_flag_is_set(tmp_path):
    """91 of 767 real entries carry UTF-8 bytes without the flag."""
    flagged = syn.opinion(date="2024-03-01", recipient="Näidisamet", title="Kõrgeim tähtaeg")
    unflagged = syn.opinion(
        date="2024-03-02", recipient="Näidisamet", title="Tõõväline mõõdik", unflagged=True
    )
    path = syn.write_archive(tmp_path / "a.zip", [flagged, unflagged])

    _, occurrences = read_opinion_archive(path)
    names = {o.original_filename for o in occurrences}
    assert "2024-03-01 - Näidisamet - Kõrgeim tähtaeg.pdf" in names
    assert "2024-03-02 - Näidisamet - Tõõväline mõõdik.pdf" in names
    encodings = {o.filename_encoding for o in occurrences}
    assert encodings == {"utf-8-flag", "cp437-bytes-were-utf8"}


def test_an_entry_that_escapes_its_directory_is_refused(tmp_path):
    path = tmp_path / "evil.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("../escaped.pdf", syn.pdf_bytes("x"))
    with pytest.raises(OpinionSourceError):
        read_opinion_archive(path)


def test_two_paths_holding_one_binary_are_two_occurrences(tmp_path):
    """Occurrence and binary are different counts, and both are kept."""
    data = syn.pdf_bytes("same")
    path = tmp_path / "dupe.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Opinions/2024-01-01 - Amet - Esimene.pdf", data)
        archive.writestr("Opinions/koopia/2024-01-01 - Amet - Esimene.pdf", data)

    _, occurrences = read_opinion_archive(path)
    assert len(occurrences) == 2
    assert len({o.sha256 for o in occurrences}) == 1


# ---------------------------------------------------------------------------
# The KodaDash artefact
# ---------------------------------------------------------------------------


def test_a_producer_workbook_without_hashes_is_refused_rather_than_name_matched(tmp_path):
    """Filename matching produced collisions on the real data. Bytes or nothing."""
    book = syn.write_kodadash_workbook(
        tmp_path / "kd.xlsx",
        [{"content_id": "X-1", "source_file": "a.pdf"}],
        include_binding_sheet=False,
    )
    with pytest.raises(OpinionSourceError, match="filename"):
        read_kodadash_artifact(book)


def test_producer_rows_bind_by_hash_even_when_the_filename_disagrees(tmp_path, archive_path):
    """The real archive damaged some filenames; the recorded hash still binds."""
    item = syn.opinion(date="2024-05-06", recipient="Näidisamet", title="Kõrgendatud nõue")
    archive = archive_path([item])
    book = syn.write_kodadash_workbook(
        tmp_path / "kd.xlsx",
        [
            {
                "content_id": "X-1",
                # Deliberately not the archive's name.
                "source_file": "2024-05-06 - Naidisamet - Korgendatud nue.pdf",
                "file_sha256": item.sha256,
                "recipient_raw": "Näidisamet",
            }
        ],
    )
    plan = plan_for(archive, book)
    assert set(plan.kodadash_rows) == {item.sha256}
    assert plan.kodadash_rows[item.sha256].external_id == "X-1"


# ---------------------------------------------------------------------------
# Match classes
# ---------------------------------------------------------------------------


def attach_to_onenote(matters: list[Matter], data: bytes, *, page_title: str = "Leht") -> None:
    """Put these exact bytes on a OneNote page that the given Matters claim."""
    page = LegacySourcePage.objects.create(
        source_page_id=f"page-{syn.sha256(data)[:12]}",
        page_key=f"key-{syn.sha256(data)[:12]}",
        source_notebook="oigus",
        source_section="ARHIIV",
        title=page_title,
        capture_id="capture-1",
        first_imported_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        latest_imported_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )
    LegacySourceResource.objects.create(
        source_page=page,
        resource_key="resource-1",
        original_filename="lisatud.pdf",
        source_block_ordinal=3,
        sha256=syn.sha256(data),
        size_bytes=len(data),
        archive_relative_path="pages/x/resource-1.pdf",
    )
    for matter in matters:
        MatterSourcePage.objects.create(
            matter=matter, source_page=page, match_method=SourceMatchMethod.EXCEL_EXACT_PAGE_ID
        )


def test_exact_binary_on_a_page_claimed_by_one_matter_is_automatic(archive_path):
    matter = register_matter(
        year=2022, number=7, title="Näidisakti muutmine", sent="2022-06-01", counterparty="Amet"
    )
    item = syn.opinion(date="2022-05-31", recipient="Amet", title="Näidisakti muutmine")
    attach_to_onenote([matter], item.data)

    plan = plan_for(archive_path([item]))
    proposal = proposal_for(plan, item)
    assert proposal.match_class == OpinionMatchClass.EXACT_BINARY_MATTER
    assert OpinionSignal.EXACT_BINARY_ONENOTE in proposal.signals
    assert proposal.matter_id == matter.pk


def test_exact_binary_claimed_by_two_matters_is_never_chosen_between(archive_path):
    first = register_matter(
        year=2022, number=8, title="Esimene teema", sent="2022-06-01", counterparty="Amet"
    )
    second = register_matter(
        year=2023, number=9, title="Teine teema", sent="2023-06-01", counterparty="Amet"
    )
    item = syn.opinion(date="2022-05-31", recipient="Amet", title="Ühine kiri")
    attach_to_onenote([first, second], item.data)

    plan = plan_for(archive_path([item]))
    proposal = proposal_for(plan, item)
    assert proposal.match_class == OpinionMatchClass.EXACT_BINARY_MULTI_MATTER
    assert OpinionConflict.MULTIPLE_MATTER_BINARY in proposal.conflicts
    assert plan.submissions == []


def test_three_exact_signals_are_automatic(archive_path):
    register_matter(
        year=2024,
        number=11,
        title="Näidisregistri seaduse muutmise seadus",
        sent="2024-04-10",
        counterparty="Näidisministeerium",
    )
    item = syn.opinion(
        date="2024-04-10",
        recipient="Näidisministeerium",
        title="Arvamus näidisregistri seaduse muutmise kohta",
    )
    plan = plan_for(archive_path([item]))
    proposal = proposal_for(plan, item)
    assert proposal.match_class == OpinionMatchClass.STRICT_MULTI_SIGNAL
    assert OpinionSignal.EXACT_SENT_DATE in proposal.signals
    assert OpinionSignal.EXACT_RECIPIENT in proposal.signals
    assert OpinionSignal.EXACT_TITLE_TOKEN in proposal.signals


def test_a_shared_proceeding_number_counts_as_the_third_signal(archive_path):
    register_matter(
        year=2024,
        number=12,
        title="Eelnõu 662 SE menetlus",
        sent="2024-04-11",
        counterparty="Näidiskomisjon",
    )
    item = syn.opinion(
        date="2024-04-11", recipient="Näidiskomisjon", title="Arvamus eelnõu 662 SE kohta"
    )
    plan = plan_for(archive_path([item]))
    proposal = proposal_for(plan, item)
    assert proposal.match_class == OpinionMatchClass.STRICT_MULTI_SIGNAL
    assert OpinionSignal.EXACT_LAW_REFERENCE in proposal.signals


def test_date_and_recipient_alone_are_not_identity(archive_path):
    """The real corpus contains such a pair about entirely different subjects."""
    register_matter(
        year=2024,
        number=13,
        title="Euroopa Liidu rohelepe",
        sent="2024-04-12",
        counterparty="Näidisministeerium",
    )
    item = syn.opinion(
        date="2024-04-12", recipient="Näidisministeerium", title="Ohtlike jäätmete käitlus"
    )
    plan = plan_for(archive_path([item]))
    proposal = proposal_for(plan, item)
    assert proposal.match_class == OpinionMatchClass.REVIEW_REQUIRED
    assert OpinionConflict.TITLE_CONFLICT in proposal.conflicts
    assert plan.submissions == []


def test_a_one_day_difference_is_a_suggestion_not_a_match(archive_path):
    """227 of 767 real files sit one day before their register row."""
    register_matter(
        year=2024,
        number=14,
        title="Näidisregistri seaduse muutmine",
        sent="2024-04-14",
        counterparty="Näidisministeerium",
    )
    item = syn.opinion(
        date="2024-04-13",
        recipient="Näidisministeerium",
        title="Arvamus näidisregistri seaduse muutmise kohta",
    )
    plan = plan_for(archive_path([item]))
    proposal = proposal_for(plan, item)
    assert proposal.match_class == OpinionMatchClass.REVIEW_REQUIRED
    assert OpinionSignal.SENT_DATE_WITHIN_ONE_DAY in proposal.signals
    assert OpinionSignal.EXACT_SENT_DATE not in proposal.signals
    assert plan.submissions == []


def test_two_register_rows_on_one_day_and_recipient_go_to_review(archive_path):
    for number in (15, 16):
        register_matter(
            year=2024,
            number=number,
            title=f"Näidisregistri seaduse muutmine {number}",
            sent="2024-04-15",
            counterparty="Näidisministeerium",
        )
    item = syn.opinion(
        date="2024-04-15",
        recipient="Näidisministeerium",
        title="Arvamus näidisregistri seaduse muutmise kohta",
    )
    plan = plan_for(archive_path([item]))
    proposal = proposal_for(plan, item)
    assert proposal.match_class == OpinionMatchClass.REVIEW_REQUIRED
    assert OpinionConflict.MULTIPLE_SOURCE_ROWS in proposal.conflicts
    assert proposal.competing_matter_count == 2


def test_a_matching_title_alone_never_files_anything(archive_path):
    register_matter(
        year=2024,
        number=17,
        title="Näidisregistri seaduse muutmine",
        sent="2024-08-01",
        counterparty="Näidisministeerium",
    )
    item = syn.opinion(
        date="2024-04-20", recipient="Teine amet", title="Näidisregistri seaduse muutmine"
    )
    plan = plan_for(archive_path([item]))
    assert proposal_for(plan, item).match_class == OpinionMatchClass.UNMATCHED


def test_before_2020_the_register_counterparty_is_never_read_as_a_recipient(archive_path):
    """KELLELT is the sender. Comparing it to an addressee inverts the direction."""
    register_matter(
        year=2019,
        number=3,
        title="Näidisregistri seaduse muutmine",
        sent="2019-04-10",
        counterparty="Näidisministeerium",
    )
    item = syn.opinion(
        date="2019-04-10",
        recipient="Näidisministeerium",
        title="Arvamus näidisregistri seaduse muutmise kohta",
    )
    plan = plan_for(archive_path([item]))
    proposal = proposal_for(plan, item)
    assert proposal.match_class == OpinionMatchClass.REVIEW_REQUIRED
    assert OpinionConflict.EXCEL_DIRECTION_NOT_COMPARABLE in proposal.conflicts
    assert proposal.matter_id is None


# ---------------------------------------------------------------------------
# The Submission threshold
# ---------------------------------------------------------------------------


def strict_pair(number: int = 21, *, date: str = "2024-04-10", sent: str | None = "2024-04-10"):
    matter = register_matter(
        year=2024,
        number=number,
        title="Näidisregistri seaduse muutmise seadus",
        sent=sent,
        counterparty="Näidisministeerium",
    )
    item = syn.opinion(
        date=date,
        recipient="Näidisministeerium",
        title="Arvamus näidisregistri seaduse muutmise kohta",
        marker=f"strict-{number}",
    )
    return matter, item


def test_a_unique_matter_with_a_date_and_a_binary_becomes_a_sent_submission(archive_path):
    matter, item = strict_pair()
    plan = plan_for(archive_path([item]))
    batch = open_batch(plan)
    report = apply_plan(plan, batch=batch)

    assert report.submissions_created == 1
    submission = Submission.objects.get(matter=matter)
    assert submission.status == SubmissionStatus.SENT
    # Read back in the project's own time zone. The anchor is local midnight so
    # that the date a query groups by is the date the register wrote; a naive
    # `.date()` on the stored UTC value would report the day before.
    assert timezone.localtime(submission.sent_at).date() == datetime.date(2024, 4, 10)
    assert submission.final_version.sha256 == item.sha256
    assert submission.final_version.document.role == DocumentRole.KODA_SUBMISSION_FINAL
    record = OpinionSubmissionImport.objects.get(submission=submission)
    assert record.sent_date_basis == SentDateBasis.EXCEL_OUT_DATE
    assert record.created_submission is True


def test_a_date_only_source_never_claims_a_time(archive_path):
    _, item = strict_pair(number=22)
    plan = plan_for(archive_path([item]))
    apply_plan(plan, batch=open_batch(plan))
    submission = Submission.objects.get()
    assert submission.sent_at_precision == SentAtPrecision.DATE


def test_a_certain_matter_with_no_defensible_date_still_creates_nothing(archive_path):
    """The hardest case to refuse: the Matter is *identity*, and it is still not enough.

    Reached through the binary route, because that is the only route that can
    name a Matter without a register date — a register row with no `VÄLJA` is
    not indexed for date matching at all.
    """
    matter = register_matter(
        year=2022, number=23, title="Näidisakti muutmine", sent=None, counterparty="Amet"
    )
    item = syn.opinion(date="2022-05-31", recipient="Amet", title="Näidisakti muutmine")
    attach_to_onenote([matter], item.data)

    plan = plan_for(archive_path([item]))
    apply_plan(plan, batch=open_batch(plan))

    assert not Submission.objects.exists()
    # The evidence is still catalogued, still attached to the Matter, and the
    # candidate has been demoted to the queue with the reason on it.
    assert OpinionArchiveItem.objects.count() == 1
    proposal = proposal_for(plan, item)
    assert proposal.match_class == OpinionMatchClass.REVIEW_REQUIRED
    assert "kuupäeva ei ole" in proposal.explanation
    assert OpinionMatchCandidate.objects.filter(matter=matter).exists()


def test_a_register_row_with_no_sent_date_never_matches_on_date(archive_path):
    _, item = strict_pair(number=24, sent=None)
    plan = plan_for(archive_path([item]))
    apply_plan(plan, batch=open_batch(plan))

    assert not Submission.objects.exists()
    assert proposal_for(plan, item).match_class == OpinionMatchClass.UNMATCHED


def test_several_files_on_one_matter_and_day_are_one_letter_with_annexes(archive_path):
    """The real corpus: a letter plus `Lisa 1`, and a four-document bundle."""
    register_matter(
        year=2025,
        number=44,
        title="Näidisseaduse muutmisvajadused",
        sent="2025-04-07",
        counterparty="Näidisministeerium",
    )
    letter = syn.opinion(
        date="2025-04-07",
        recipient="Näidisministeerium",
        title="Sisend näidisseaduse muutmisvajaduste kohta",
    )
    annex = syn.opinion(
        date="2025-04-07",
        recipient="Näidisministeerium",
        title="Sisend näidisseaduse muutmisvajaduste kohta - Lisa 1",
    )
    plan = plan_for(archive_path([letter, annex]))

    assert plan.submissions == []
    for opinion_file in (letter, annex):
        proposal = proposal_for(plan, opinion_file)
        assert proposal.match_class == OpinionMatchClass.REVIEW_REQUIRED
        assert OpinionConflict.SAME_DAY_BUNDLE in proposal.conflicts


def test_two_copies_of_one_letter_produce_one_submission(tmp_path):
    matter, item = strict_pair(number=24)
    path = tmp_path / "dupe.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"Opinions/{item.name.split('/')[-1]}", item.data)
        archive.writestr(f"Opinions/koopia/{item.name.split('/')[-1]}", item.data)

    plan = build_plan(archive_path=path)
    apply_plan(plan, batch=open_batch(plan))

    assert Submission.objects.filter(matter=matter).count() == 1
    assert OpinionArchiveItem.objects.count() == 2


def test_an_existing_submission_with_the_same_binary_is_enriched_not_duplicated(archive_path):
    matter, item = strict_pair(number=25)
    document = create_document(
        matter=matter, title="Varem salvestatud", role=DocumentRole.KODA_SUBMISSION_FINAL
    )
    version = add_evidence_version(
        document=document,
        content=item.data,
        original_filename="varem.pdf",
        mime_type="application/pdf",
    )
    existing = factories.SubmissionFactory(
        matter=matter,
        status=SubmissionStatus.SENT,
        sent_at=datetime.datetime(2024, 4, 10, 9, 30, tzinfo=datetime.UTC),
        final_version=version,
    )

    plan = plan_for(archive_path([item]))
    report = apply_plan(plan, batch=open_batch(plan))

    assert report.submissions_created == 0
    assert report.submissions_linked == 1
    assert Submission.objects.count() == 1
    record = OpinionSubmissionImport.objects.get()
    assert record.submission_id == existing.pk
    assert record.created_submission is False
    # The manual record's own timestamp precision is untouched.
    existing.refresh_from_db()
    assert existing.sent_at_precision == SentAtPrecision.TIMESTAMP


def test_a_manual_submission_with_different_evidence_is_a_conflict_not_a_replacement(
    archive_path,
):
    matter, item = strict_pair(number=26)
    document = create_document(
        matter=matter, title="Käsitsi lisatud", role=DocumentRole.KODA_SUBMISSION_FINAL
    )
    version = add_evidence_version(
        document=document,
        content=syn.pdf_bytes("hoopis-teine-fail"),
        original_filename="kasitsi.pdf",
        mime_type="application/pdf",
    )
    factories.SubmissionFactory(
        matter=matter,
        status=SubmissionStatus.SENT,
        sent_at=datetime.datetime(2024, 4, 10, 9, 30, tzinfo=datetime.UTC),
        final_version=version,
    )

    plan = plan_for(archive_path([item]))
    apply_plan(plan, batch=open_batch(plan))

    assert Submission.objects.count() == 1
    proposal = proposal_for(plan, item)
    assert proposal.match_class == OpinionMatchClass.CONFLICT
    assert OpinionConflict.EXISTING_SUBMISSION_DISAGREES in proposal.conflicts


def test_bytes_already_on_the_matter_are_reused_rather_than_stored_twice(archive_path):
    """The OneNote materialisation may already hold this file."""
    matter, item = strict_pair(number=27)
    document = create_document(
        matter=matter, title="OneNote'ist", role=DocumentRole.LEGACY_MATERIAL
    )
    add_evidence_version(
        document=document,
        content=item.data,
        original_filename="onenote.pdf",
        mime_type="application/pdf",
    )
    before = DocumentVersion.objects.count()

    plan = plan_for(archive_path([item]))
    report = apply_plan(plan, batch=open_batch(plan))

    assert DocumentVersion.objects.count() == before
    assert report.versions_reused == 1
    assert report.versions_created == 0
    assert Submission.objects.get().final_version.sha256 == item.sha256


def test_the_kind_is_not_inferred_from_the_recipient(archive_path):
    register_matter(
        year=2024,
        number=28,
        title="Näidisregistri seaduse muutmise seadus",
        sent="2024-04-10",
        counterparty="Riigikogu näidiskomisjon",
    )
    item = syn.opinion(
        date="2024-04-10",
        recipient="Riigikogu näidiskomisjon",
        title="Arvamus näidisregistri seaduse muutmise kohta",
    )
    plan = plan_for(archive_path([item]))
    apply_plan(plan, batch=open_batch(plan))
    assert Submission.objects.get().kind == SubmissionKind.FORMAL_OPINION


def test_an_explicit_joint_letter_keeps_its_kind(archive_path):
    register_matter(
        year=2024,
        number=29,
        title="Näidisregistri seaduse muutmise seadus",
        sent="2024-04-10",
        counterparty="Näidisministeerium",
    )
    item = syn.opinion(
        date="2024-04-10",
        recipient="Näidisministeerium",
        title="Ühispöördumine näidisregistri seaduse muutmise asjus",
    )
    plan = plan_for(archive_path([item]))
    apply_plan(plan, batch=open_batch(plan))
    assert Submission.objects.get().kind == SubmissionKind.JOINT_LETTER


def test_no_joint_submitter_is_invented(archive_path):
    _, item = strict_pair(number=30)
    plan = plan_for(archive_path([item]))
    apply_plan(plan, batch=open_batch(plan))
    assert Submission.objects.get().joint_submitter_rows.count() == 0


# ---------------------------------------------------------------------------
# Recipients
# ---------------------------------------------------------------------------


def test_an_exactly_named_organisation_becomes_the_addressee(archive_path):
    factories.OrganisationFactory(name="Näidisministeerium")
    _, item = strict_pair(number=31)
    plan = plan_for(archive_path([item]))
    apply_plan(plan, batch=open_batch(plan))

    row = SubmissionRecipient.objects.get()
    assert row.role == RecipientRole.ADDRESSEE
    assert row.organisation.name == "Näidisministeerium"


def test_an_unknown_recipient_is_preserved_and_never_creates_an_organisation(archive_path):
    _, item = strict_pair(number=32)
    plan = plan_for(archive_path([item]))
    report = apply_plan(plan, batch=open_batch(plan))

    assert not SubmissionRecipient.objects.exists()
    assert report.recipients_unresolved == 1
    from app.organisations.models import Organisation

    assert not Organisation.objects.filter(name__icontains="Näidisministeerium").exists()
    # The raw string still survives, in the record that explains the submission.
    record = OpinionSubmissionImport.objects.get()
    assert record.recipient_basis in {RecipientBasis.EXCEL_ADDRESSEE, RecipientBasis.KODADASH_RAW}


def test_the_producers_normalised_ministry_never_replaces_the_historical_name(
    tmp_path, archive_path
):
    """KodaDash folds Keskkonnaministeerium into Kliimaministeerium. Juristid must not."""
    historical = factories.OrganisationFactory(name="Näidise vana ministeerium")
    factories.OrganisationFactory(name="Näidise uus ministeerium")
    register_matter(
        year=2024,
        number=33,
        title="Näidisregistri seaduse muutmise seadus",
        sent="2024-04-10",
        counterparty="Näidise vana ministeerium",
    )
    item = syn.opinion(
        date="2024-04-10",
        recipient="Näidise vana ministeerium",
        title="Arvamus näidisregistri seaduse muutmise kohta",
    )
    book = syn.write_kodadash_workbook(
        tmp_path / "kd.xlsx",
        [
            {
                "content_id": "X-33",
                "file_sha256": item.sha256,
                "recipient_raw": "Näidise vana ministeerium",
                "recipient_normalized": "Näidise uus ministeerium",
                "recipient_filter_group": "Näidise uus ministeerium",
            }
        ],
    )
    plan = plan_for(archive_path([item]), book)
    apply_plan(plan, batch=open_batch(plan))

    assert SubmissionRecipient.objects.get().organisation_id == historical.pk
    metadata = OpinionArchiveMetadata.objects.get()
    assert metadata.recipient_raw == "Näidise vana ministeerium"
    assert metadata.recipient_normalized == "Näidise uus ministeerium"


# ---------------------------------------------------------------------------
# KodaDash enrichment
# ---------------------------------------------------------------------------


def test_derived_metadata_is_stored_beside_the_evidence_and_marked_derived(tmp_path, archive_path):
    matter, item = strict_pair(number=34)
    book = syn.write_kodadash_workbook(
        tmp_path / "kd.xlsx",
        [
            {
                "content_id": "X-34",
                "file_sha256": item.sha256,
                "recipient_raw": "Näidisministeerium",
                "public_summary": "Sünteetiline avalik kokkuvõte.",
                "chamber_position": "Sünteetiline seisukoht.",
                "business_impact": "Sünteetiline mõju.",
                "law_tags_confirmed": "naidisseadus",
                "topic_primary": "Näidisvaldkond",
                "related_koda_news_url": "https://example.invalid/uudis",
                "related_koda_news_content_id": "NEWS-1",
                "canonical_policy_thread_id": "THREAD-1",
            }
        ],
    )
    plan = plan_for(archive_path([item]), book)
    apply_plan(plan, batch=open_batch(plan))

    metadata = OpinionArchiveMetadata.objects.get()
    assert metadata.source_system == "KODADASH"
    assert metadata.external_id == "X-34"
    assert metadata.related_koda_news_id == "NEWS-1"
    assert metadata.policy_thread_id == "THREAD-1"
    assert "Sünteetiline avalik kokkuvõte." in metadata.payload.values()

    # None of it reached the lawyer-owned fields or the canonical taxonomy.
    matter.refresh_from_db()
    assert matter.position_summary == ""
    assert matter.rationale_summary == ""
    assert matter.tag_assignments.count() == 0
    assert matter.policy_areas.count() == 0


def test_a_row_the_public_app_excluded_is_still_archive_evidence(tmp_path, archive_path):
    """Public suitability and archival value are different questions."""
    _, item = strict_pair(number=35)
    book = syn.write_kodadash_workbook(
        tmp_path / "kd.xlsx",
        [
            {
                "content_id": "X-35",
                "file_sha256": item.sha256,
                "recipient_raw": "Näidisministeerium",
                "public_import_eligible": False,
            }
        ],
        excluded=[{"content_id": "X-35", "exclusion_reason": "avalikuks mitte sobiv"}],
    )
    plan = plan_for(archive_path([item]), book)
    apply_plan(plan, batch=open_batch(plan))

    metadata = OpinionArchiveMetadata.objects.get()
    assert metadata.excluded_from_public is True
    assert metadata.public_import_eligible is False
    # And it still became a canonical historical submission.
    assert Submission.objects.filter(status=SubmissionStatus.SENT).count() == 1


def test_metadata_without_a_matching_file_and_files_without_metadata_are_both_reported(
    tmp_path, archive_path
):
    _, item = strict_pair(number=36)
    orphan = syn.pdf_bytes("koda-dash-only")
    book = syn.write_kodadash_workbook(
        tmp_path / "kd.xlsx",
        [
            {"content_id": "X-36", "file_sha256": item.sha256},
            {"content_id": "X-37", "file_sha256": syn.sha256(orphan)},
        ],
    )
    extra = syn.opinion(date="2026-01-01", recipient="Amet", title="Ilma rikastuseta")
    plan = plan_for(archive_path([item, extra]), book)

    joined = " ".join(plan.findings)
    assert "KodaDashi reas" in joined
    assert "praeguses arhiivis ei ole" in joined


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_running_the_whole_thing_twice_changes_nothing(archive_path, tmp_path):
    _matter, item = strict_pair(number=41)
    factories.OrganisationFactory(name="Näidisministeerium")
    archive = archive_path([item])

    first = plan_for(archive)
    apply_plan(first, batch=open_batch(first))
    counts = (
        OpinionArchiveItem.objects.count(),
        OpinionMatchCandidate.objects.count(),
        Submission.objects.count(),
        Document.objects.count(),
        DocumentVersion.objects.count(),
        SubmissionRecipient.objects.count(),
        OpinionSubmissionImport.objects.count(),
    )

    second = plan_for(archive)
    report = apply_plan(second, batch=open_batch(second))

    assert report.submissions_created == 0
    assert report.items_created == 0
    assert counts == (
        OpinionArchiveItem.objects.count(),
        OpinionMatchCandidate.objects.count(),
        Submission.objects.count(),
        Document.objects.count(),
        DocumentVersion.objects.count(),
        SubmissionRecipient.objects.count(),
        OpinionSubmissionImport.objects.count(),
    )


def test_the_plan_refuses_an_archive_that_is_not_the_one_it_was_told_to_read(archive_path):
    from app.legacy_import.opinion_plan import OpinionPlanError

    _, item = strict_pair(number=42)
    with pytest.raises(OpinionPlanError, match="Archive SHA-256"):
        build_plan(archive_path=archive_path([item]), expected_archive_sha256="0" * 64)


# ---------------------------------------------------------------------------
# Coverage reporting
# ---------------------------------------------------------------------------


def test_coverage_is_reported_per_year_not_as_one_average(archive_path):
    register_matter(
        year=2024,
        number=51,
        title="Näidisregistri seaduse muutmise seadus",
        sent="2024-04-10",
        counterparty="Näidisministeerium",
    )
    matched = syn.opinion(
        date="2024-04-10",
        recipient="Näidisministeerium",
        title="Arvamus näidisregistri seaduse muutmise kohta",
    )
    unmatched = syn.opinion(date="2021-02-02", recipient="Tundmatu amet", title="Ei sobi kuhugi")

    plan = plan_for(archive_path([matched, unmatched]))
    coverage = plan.coverage_by_year()
    assert coverage["2024"]["automatic"] == 1
    assert coverage["2021"]["automatic"] == 0
    assert coverage["2021"]["occurrences"] == 1


# ---------------------------------------------------------------------------
# The reconciliation round: distinctiveness, aliases and citations
#
# Every string below is invented. The *shapes* come from the real corpus and
# each is named in the report the round produced; the words are not.
# ---------------------------------------------------------------------------


def test_a_word_the_stopword_list_meant_to_exclude_is_not_a_third_signal(archive_path):
    """The defect: a stopword written in a spelling `fold` never produces.

    The list carried `poordumine` and `prdumine` for *pöördumine*, and `fold`
    produces neither — it replaces each `ö` with a space, so the real token is
    `rdumine`. Seven characters, in no stopword, and therefore accepted as
    "distinctive" on 207 register titles.

    Two letters here share the date, the addressee and the word *pöördumine*
    and nothing else. That must not be automatic: the whole job of the third
    signal is to say the two are about the same subject.
    """
    register_matter(
        year=2024,
        number=41,
        title="Pöördumine näidisloomeprotsessi lihtsustamiseks",
        sent="2024-05-02",
        counterparty="Näidisministeerium",
    )
    item = syn.opinion(
        date="2024-05-02",
        recipient="Näidisministeerium",
        title="Pöördumine seoses näidistariifidega",
    )
    proposal = proposal_for(plan_for(archive_path([item])), item)

    assert proposal.match_class == OpinionMatchClass.REVIEW_REQUIRED
    assert OpinionConflict.TITLE_CONFLICT in proposal.conflicts
    assert OpinionSignal.EXACT_TITLE_TOKEN not in proposal.signals


def test_the_stopwords_are_the_tokens_fold_actually_produces():
    """Intent and effect are one object, not two lists to keep in step.

    Asserted directly rather than through a match, because the property is
    about the list itself: every entry has to be something `title_tokens` could
    emit, or it excludes nothing and the author cannot tell.
    """
    from app.legacy_import.opinion_sources import (
        MINIMUM_TITLE_TOKEN,
        TITLE_STOPWORDS,
        fold,
        title_tokens,
    )

    assert TITLE_STOPWORDS
    for word in TITLE_STOPWORDS:
        # Reachable: `fold` leaves it alone and it is long enough to be a
        # token. An entry failing either is one that excludes nothing, which is
        # exactly the defect that let `rdumine` through.
        assert fold(word) == word, word
        assert len(word) >= MINIMUM_TITLE_TOKEN, word
        # And it is excluded rather than merely present in a list.
        assert title_tokens(word) == frozenset(), word

    assert title_tokens("pöördumine") == frozenset()
    assert title_tokens("Euroopa Komisjoni konsultatsioon") == frozenset()
    # And a genuine subject word still survives. Not "naidisregistri": `fold`
    # replaces the `ä` with a space rather than transliterating it, so the
    # token is the fragment after the split — which is the same fragment the
    # register produces from the same word, and that symmetry is the point.
    assert "idisregistri" in title_tokens("näidisregistri seaduse muutmine")


def test_an_abbreviated_addressee_matches_the_words_it_abbreviates(archive_path):
    """163 of 192 unmatched files failed on this and nothing else.

    The archive filename writes the ministry out and the register KELLELE
    writes the abbreviation. `fold` cannot converge those, and a reviewed pair
    is the only thing that may.
    """
    register_matter(
        year=2024,
        number=42,
        title="Näidisregistri seaduse muutmise seadus",
        sent="2024-05-03",
        counterparty="MKM",
    )
    item = syn.opinion(
        date="2024-05-03",
        recipient="Majandus- ja Kommunikatsiooniministeerium",
        title="Arvamus näidisregistri seaduse muutmise kohta",
    )
    proposal = proposal_for(plan_for(archive_path([item])), item)

    assert proposal.match_class == OpinionMatchClass.STRICT_MULTI_SIGNAL
    assert OpinionSignal.EXACT_RECIPIENT in proposal.signals


def test_a_letter_to_two_ministries_matches_a_row_naming_one(archive_path):
    """A recipient string is a set of bodies, not one opaque name."""
    register_matter(
        year=2024,
        number=43,
        title="Näidisregistri seaduse muutmise seadus",
        sent="2024-05-04",
        counterparty="Siseministeerium",
    )
    item = syn.opinion(
        date="2024-05-04",
        recipient="Siseministeerium, Näidisministeerium",
        title="Arvamus näidisregistri seaduse muutmise kohta",
    )
    proposal = proposal_for(plan_for(archive_path([item])), item)

    assert proposal.match_class == OpinionMatchClass.STRICT_MULTI_SIGNAL
    assert proposal.competing_matter_count == 1


def test_one_row_named_twice_by_one_letter_is_still_one_candidate(archive_path):
    """Both sides are sets now, so a row could be found once per shared name.

    Without the dedup this reports two competing rows where there is one, and
    refuses a match it should make.
    """
    register_matter(
        year=2024,
        number=44,
        title="Näidisregistri seaduse muutmise seadus",
        sent="2024-05-05",
        counterparty="Siseministeerium, MKM",
    )
    item = syn.opinion(
        date="2024-05-05",
        recipient="Siseministeerium, Majandus- ja Kommunikatsiooniministeerium",
        title="Arvamus näidisregistri seaduse muutmise kohta",
    )
    proposal = proposal_for(plan_for(archive_path([item])), item)

    assert proposal.competing_matter_count == 1
    assert proposal.match_class == OpinionMatchClass.STRICT_MULTI_SIGNAL


def test_a_citation_matches_a_matter_the_date_route_cannot_see(archive_path):
    """Koda writes twice about one proceeding; the register keeps one VÄLJA.

    The archive file is ten months from the register dispatch date, so the
    date-and-addressee route finds nothing at all. The proceeding number is the
    same identifier in both sources, and the addressee corroborates it.
    """
    register_matter(
        year=2021,
        number=45,
        title="Näidisjäätmete seaduse eelnõu 190 SE",
        sent="2021-03-16",
        counterparty="Näidiskomisjon",
    )
    item = syn.opinion(
        date="2020-05-21", recipient="Näidiskomisjon", title="Täiendav arvamus eelnõule 190 SE"
    )
    proposal = proposal_for(plan_for(archive_path([item])), item)

    assert proposal.match_class == OpinionMatchClass.EXACT_LAW_REFERENCE_MATTER
    assert OpinionSignal.EXACT_LAW_REFERENCE in proposal.signals
    # And it says the link is about the subject rather than about a dispatch.
    assert "väljasaatmise" in proposal.explanation


def test_a_citation_alone_is_not_identity(archive_path):
    """Neither the addressee nor the date agrees, so the number is a question."""
    register_matter(
        year=2021,
        number=46,
        title="Näidisjäätmete seaduse eelnõu 191 SE",
        sent="2021-03-16",
        counterparty="Näidiskomisjon",
    )
    item = syn.opinion(
        date="2020-05-21", recipient="Muu näidisamet", title="Täiendav arvamus eelnõule 191 SE"
    )
    proposal = proposal_for(plan_for(archive_path([item])), item)

    assert proposal.match_class == OpinionMatchClass.REVIEW_REQUIRED
    assert proposal.matter_id is not None


def test_a_proceeding_number_on_two_matters_refuses_rather_than_picks(archive_path):
    """25 of the register 165 distinct numbers name more than one Matter."""
    for number in (47, 48):
        register_matter(
            year=2021,
            number=number,
            title=f"Näidisseaduse eelnõu 192 SE, osa {number}",
            sent="2021-03-16",
            counterparty="Näidiskomisjon",
        )
    item = syn.opinion(
        date="2020-05-21", recipient="Näidiskomisjon", title="Arvamus eelnõule 192 SE"
    )
    proposal = proposal_for(plan_for(archive_path([item])), item)

    assert proposal.match_class == OpinionMatchClass.REVIEW_REQUIRED
    assert proposal.matter_id is None
    assert proposal.competing_matter_count == 2


def test_exact_date_and_addressee_outrank_an_ambiguous_citation(archive_path):
    """The ordering defect this round found and reversed.

    Six real files carry an exact date, an exact addressee and a proceeding
    number that names two Matters. A citation-first pass sees the ambiguous
    number, refuses, and throws away the two exact signals that resolve it —
    withdrawing six links production already holds.

    The rule is not "citations outrank dates". It is *more independent exact
    signals outrank fewer*.
    """
    register_matter(
        year=2025,
        number=49,
        title="Näidisraamatupidamise seaduse eelnõu 516 SE",
        sent="2025-04-01",
        counterparty="Näidisministeerium",
    )
    register_matter(
        year=2025,
        number=50,
        title="Muu näidisseaduse eelnõu 516 SE menetlus",
        sent="2025-09-09",
        counterparty="Muu näidisamet",
    )
    item = syn.opinion(
        date="2025-04-01",
        recipient="Näidisministeerium",
        title="Arvamus näidisraamatupidamise seaduse eelnõu 516 SE kohta",
    )
    proposal = proposal_for(plan_for(archive_path([item])), item)

    assert proposal.match_class == OpinionMatchClass.STRICT_MULTI_SIGNAL
    assert OpinionSignal.EXACT_SENT_DATE in proposal.signals
    assert OpinionSignal.EXACT_RECIPIENT in proposal.signals


def test_a_one_day_difference_is_still_not_automatic(archive_path):
    """Unchanged by this round, and asserted so it cannot drift.

    The register VÄLJA is the next day in 227 of 767 cases, which makes a
    one-day window common and therefore exactly not an identity.
    """
    register_matter(
        year=2024,
        number=51,
        title="Näidisregistri seaduse muutmise seadus",
        sent="2024-05-07",
        counterparty="Näidisministeerium",
    )
    item = syn.opinion(
        date="2024-05-06",
        recipient="Näidisministeerium",
        title="Arvamus näidisregistri seaduse muutmise kohta",
    )
    proposal = proposal_for(plan_for(archive_path([item])), item)

    assert proposal.match_class == OpinionMatchClass.REVIEW_REQUIRED
    assert OpinionSignal.SENT_DATE_WITHIN_ONE_DAY in proposal.signals


def test_the_citation_class_may_be_applied_without_a_person():
    """It is in `AUTOMATIC_MATCH_CLASSES`, which is what makes it a link.

    Stated as a property of the vocabulary rather than inferred from a plan, so
    a later change that demotes the class fails here rather than silently
    halving the coverage this round produced.
    """
    from app.legacy_import.opinion_enums import AUTOMATIC_MATCH_CLASSES

    assert OpinionMatchClass.EXACT_LAW_REFERENCE_MATTER in AUTOMATIC_MATCH_CLASSES
    assert OpinionMatchClass.REVIEW_REQUIRED not in AUTOMATIC_MATCH_CLASSES
    assert OpinionMatchClass.CONTENT_MULTI_SIGNAL not in AUTOMATIC_MATCH_CLASSES


def test_a_citation_across_a_date_gap_links_but_files_no_submission(archive_path):
    """A subject relation is not a dispatch, and the apply must not read it as one.

    `EXACT_LAW_REFERENCE_MATTER` exists to reach Matters whose VÄLJA is nowhere
    near the letter's own date — Koda writes twice about one proceeding and the
    register keeps the last dispatch. The real corpus has such a pair ten months
    apart.

    Left alone, `_sent_date_for` would take the register's VÄLJA for those and
    file a Submission saying Koda sent *this* letter that day: the link right,
    the date invented. So the link is planned and the Submission is not.
    """
    register_matter(
        year=2021,
        number=61,
        title="Näidisjäätmete seaduse eelnõu 193 SE",
        sent="2021-03-16",
        counterparty="Näidiskomisjon",
    )
    item = syn.opinion(
        date="2020-05-21",
        recipient="Näidiskomisjon",
        title="Täiendav arvamus eelnõule 193 SE",
    )
    plan = plan_for(archive_path([item]))
    proposal = proposal_for(plan, item)

    # Still an automatic class, so `derive_links` will file the relationship.
    assert proposal.match_class == OpinionMatchClass.EXACT_LAW_REFERENCE_MATTER
    assert proposal.matter_id is not None
    # And no Submission, because nothing here says when the letter went out.
    assert plan.submissions == []
    assert "saadetud arvamust ei looda" in proposal.explanation


def test_a_citation_on_the_same_day_still_files_a_submission(archive_path):
    """The other side, so the guard cannot quietly become "never".

    Where the register's VÄLJA is the letter's own day, the citation route has
    the same dispatch evidence every other automatic class has, and withholding
    the Submission would lose a real one.
    """
    register_matter(
        year=2021,
        number=62,
        title="Näidisjäätmete seaduse eelnõu 194 SE",
        sent="2021-03-16",
        counterparty="Näidiskomisjon",
    )
    item = syn.opinion(
        date="2021-03-16",
        recipient="Muu näidisamet",
        title="Arvamus eelnõule 194 SE",
    )
    plan = plan_for(archive_path([item]))
    proposal = proposal_for(plan, item)

    assert proposal.match_class == OpinionMatchClass.EXACT_LAW_REFERENCE_MATTER
    assert OpinionSignal.EXACT_SENT_DATE in proposal.signals
    assert [entry.matter_id for entry in plan.submissions] == [proposal.matter_id]
