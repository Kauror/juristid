"""Assisted intake: deterministic suggestions from extracted documents.

Two kinds of test, and the split is the design. The first half runs the pure
readers and the pure analysis against text and never opens a database: the
same sentence must always yield the same finding, and a rule is only worth
having if it can be shown on one line. The second half proves the contract
around them — a GET writes nothing, a restricted document leaks nothing, an
existing value is never overwritten, and saving is the person's ordinary edit
(docs/adr/0060).

Every document here is invented. The synthetic corpus writes PDFs at test
time; nothing is a checked-in binary.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import re
import time
from datetime import date

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.documents.enums import (
    DerivativeKind,
    DerivativeStatus,
    DocumentRole,
    ExtractionState,
    LocatorKind,
    TextSource,
)
from app.documents.models import DocumentDerivative, DocumentTextFragment
from app.matters import views as matter_views
from app.matters.intake import title_from_filename
from app.matters.intake_suggestions import (
    Confidence,
    CurrentValues,
    SuggestedField,
    analyse,
    analyse_matter,
    build_analysis_input,
    prefill_initial,
)
from app.matters.intake_suggestions import input as intake_input
from app.matters.intake_suggestions import textscan as ts
from app.matters.intake_suggestions import vocabulary as vocab
from app.matters.intake_suggestions.input import AnalysisInput, SourceDocument, TextBlock
from app.matters.intake_suggestions.resolvers import (
    OrganisationCatalogue,
    OrganisationEntry,
    load_organisation_catalogue,
    rule_diagnostics,
)
from app.matters.models import Matter
from app.organisations.models import Organisation, OrganisationAlias, OrganisationType
from app.taxonomy.models import PolicyArea, Tag
from app.workflow.enums import Track
from tests import factories
from tests import synthetic_corpus as corpus

PDF = "application/pdf"
EML = "message/rfc822"

# ---------------------------------------------------------------------------
# The synthetic corpus: fifteen shapes, all invented
# ---------------------------------------------------------------------------

#: 1. A ministry covering letter: organisation in the header, document
#:    numbers, an explicit opinion deadline, a legislative heading, decoy
#:    dates for the letter itself and for entry into force.
COVERING_LETTER = """Näidisministeerium
Suur-Ameerika 1, Tallinn

Eesti Kaubandus-Tööstuskoda
Teie 04.09.2026 nr 1-7/26/321
Meie 05.09.2026 nr 1-4/26/1234-2

Pakendiseaduse muutmise seaduse eelnõu kooskõlastamiseks

Lugupeetud Koja esindajad

Saadame Teile kooskõlastamiseks pakendiseaduse muutmise seaduse eelnõu, mis on
registreeritud eelnõude infosüsteemis EIS toimik 26-0123. Eelnõuga muudetakse pakendite
ja jäätmete käitlemise korda ning keskkonnatasu määrasid. Näidisamet on eelnõu
kooskõlastanud. Palume esitada arvamus hiljemalt 18. septembriks 2026. Seadus jõustub
1. jaanuaril 2027.

Lugupidamisega
Mari Näidis
nõunik
E-post: mari.naidis@naidisministeerium.invalid
"""

#: 2. A DOCX-shaped text: the heading comes after the address block and the
#:    deadline sits in the middle of a paragraph.
DOCX_STYLE = """Näidisministeerium
Kooskõlastuskiri

Lugupeetud partnerid

Käesolevaga edastame teile tutvumiseks dokumendi. Materjal puudutab ettevõtjaid, kes
tegelevad energiamüügiga, ja selle kohta ootame teie seisukohta kuni 30.10.2026, sest
elektrituruseaduse muutmise seaduse eelnõu menetlus jätkub novembris.

Elektrituruseaduse muutmise seaduse eelnõu

Lugupidamisega
Näidisministeerium
"""

#: 4. A date-decoy document: the document's own date, a legal effective date,
#:    and one explicit response deadline. Only the last may win.
DECOY_DOCUMENT = """Näidisministeerium

Tallinn, 05.09.2026

Määruse eelnõu kooskõlastamiseks

Määrus jõustub 1. märtsil 2027. Dokument on allkirjastatud digitaalselt 05.09.2026.
Palume vastata hiljemalt 20. septembril 2026.
"""

#: 5. Two genuine response dates. The analyser must not choose.
AMBIGUOUS_DEADLINES = """Näidisministeerium

Pakendiseaduse eelnõu kooskõlastamiseks

Palume esitada arvamus hiljemalt 18.09.2026. Ettepanekuid ootame kuni 25.09.2026.
"""

#: 6. EIS-labelled reference and an EIS link.
EIS_DOCUMENT = """Näidisministeerium

Jäätmeseaduse muutmise seaduse eelnõu kooskõlastamiseks

Eelnõu on eelnõude infosüsteemis registreeritud numbriga 26-0456/01.
Materjalid: https://eelnoud.valitsus.ee/main/mount/docList/abc-123
Vt ka https://www.example.invalid/lisad/seletuskiri
Riigikogu menetluses on seotud eelnõu 742 SE.
"""

#: 8. An EU initiative.
EU_INITIATIVE = """Näidisministeerium

Eesti seisukohad Euroopa Komisjoni ettepaneku kohta

Euroopa Komisjon avaldas ettepaneku COM(2026) 412 final Euroopa Parlamendi ja nõukogu
määruse ettepanek pakendite ja pakendijäätmete kohta. Palume seisukohti hiljemalt
30.09.2026, et kujundada Eesti seisukohad nõukogu töörühmaks.
"""

#: 9. A national transposition.
TRANSPOSITION = """Näidisministeerium

Tarbijakaitseseaduse muutmise seaduse eelnõu

Eelnõuga võetakse üle Euroopa Parlamendi ja nõukogu direktiiv (EL) 2024/825 tarbijate
teavitamise kohta. Direktiivi ülevõtmise tähtaeg on 27. märts 2026 ja ülevõtmine peab
vältima ülereguleerimist.
"""

#: 10. A strategy / development plan.
STRATEGY = """Näidisministeerium

Energiamajanduse arengukava 2035 avalik konsultatsioon

Arengukava seab pikaajalised sihid ja tegevuskava. Palume tagasisidet arengukava
eelnõule hiljemalt 15.10.2026.
"""

#: 11. A subject that belongs to several areas at once.
MULTI_AREA = """Näidisministeerium

Elektrituruseaduse ja käibemaksuseaduse muutmise seaduse eelnõu

Eelnõuga muudetakse võrgutasu arvestamist ja taastuvenergia tasu ning käibemaksu
määra elektrienergia müügil. Käibemaks ja aktsiis kohalduvad maksumäära järgi.
"""

#: 12. Several organisation names, exactly one of them the sender.
MANY_ORGANISATIONS = """Näidisministeerium
Suur-Ameerika 1

Eesti Kaubandus-Tööstuskoda

Lugupeetud Koja esindajad

Näidisamet ja Näidisliit on eelnõu kooskõlastanud. Eelnõu puudutab ka Näidisliidu
liikmeid. Palume seisukohta hiljemalt 10.10.2026.

Lugupidamisega
Näidisministeerium
"""

#: 13. A contact address in the body that is not the sender.
CONTACT_IN_BODY = """Näidisministeerium

Lugupeetud partnerid

Küsimuste korral pöörduge palun projektijuhi poole: Toomas Näidis,
toomas.naidis@naidisamet.invalid. Manused on kättesaadavad ka aadressil
info@naidisliit.invalid.

Lugupidamisega
Kadri Näidis
kadri.naidis@naidisministeerium.invalid
"""

#: 3. A message; the corpus builds the bytes, this is what its headers say.
EMAIL_META = {
    "subject": "VS: Pakendiseaduse eelnõu — kooskõlastamiseks",
    "from_name": "Kadri Näidis",
    "from_email": "kadri@naidisministeerium.invalid",
    "sent_at": "2026-08-17T09:12:00+03:00",
    "to": ["Koja õigusosakond <oigus@koda.invalid>"],
    "attachment_count": 1,
    "inline_resource_count": 0,
    "body_format": "text",
}


def document(text: str, filename: str = "kiri.pdf", **kwargs: object) -> SourceDocument:
    """A source document from one page of text, the way the PDF parser stores it."""
    return SourceDocument(
        document_id=filename,
        version_id=f"{filename}-v1",
        filename=filename,
        role=str(kwargs.pop("role", DocumentRole.INCOMING_AUTHORITY)),
        extraction_state=str(kwargs.pop("state", ExtractionState.DONE)),
        blocks=(
            TextBlock(
                ordinal=1,
                text=text,
                locator_label="lk 1",
                from_ocr=bool(kwargs.pop("ocr", False)),
            ),
        ),
        email=kwargs.pop("email", None),  # type: ignore[arg-type]
    )


def email_document(
    body: str = "Tere\n\nSaadame eelnõu. Palume seisukohta.", **meta: object
) -> SourceDocument:
    metadata = {**EMAIL_META, **meta}
    return SourceDocument(
        document_id="kiri.eml",
        version_id="kiri.eml-v1",
        filename="kiri.eml",
        role=DocumentRole.ORIGINAL_EMAIL,
        extraction_state=ExtractionState.DONE,
        blocks=(
            TextBlock(
                1,
                f"{metadata['subject']}\nSaatja: {metadata['from_name']}",
                "kirja päis",
                is_email_header=True,
            ),
            TextBlock(2, body, "kirja sisu"),
        ),
        email=metadata,
    )


class _Area:
    """A PolicyArea stand-in for the pure tests: key, name, pk, order."""

    def __init__(self, key: str, name_et: str, pk: int, sort_order: int) -> None:
        self.key, self.name_et, self.pk, self.sort_order = key, name_et, pk, sort_order


AREAS = {
    key: _Area(key, name, index, index * 10)
    for index, (key, name) in enumerate(
        [
            ("keskkond", "Keskkond"),
            ("maksud-ja-toll", "Maksud ja toll"),
            ("energeetika", "Energeetika"),
            ("eli-oiguse-ulevotmine", "ELi õiguse ülevõtmine"),
            ("arengukavad-strateegiad", "Arengukavad, strateegiad"),
            ("tarbijakaitse", "Tarbijakaitse"),
        ],
        start=1,
    )
}


def catalogue(*entries: tuple[int, str, str, tuple[str, ...]]) -> OrganisationCatalogue:
    """An organisation catalogue from ``(id, name, type, aliases)`` rows."""
    patterns = []
    known = {}
    for pk, name, kind, aliases in entries:
        known[pk] = OrganisationEntry(pk, name, kind)
        pattern = ts.organisation_pattern(
            organisation_id=pk, display=name, form=name, is_abbreviation=False
        )
        assert pattern is not None
        patterns.append(pattern)
        for alias in aliases:
            pattern = ts.organisation_pattern(
                organisation_id=pk,
                display=name,
                form=alias,
                is_abbreviation=len(alias) <= vocab.ABBREVIATION_LENGTH,
            )
            assert pattern is not None
            patterns.append(pattern)
    patterns.sort(key=lambda pattern: -len(pattern.form))
    return OrganisationCatalogue(patterns=tuple(patterns), entries=known)


MINISTRY = (1, "Näidisministeerium", OrganisationType.MINISTRY, ("NäM",))
CHAMBER = (2, "Eesti Kaubandus-Tööstuskoda", OrganisationType.CHAMBER, ("Koda",))
AGENCY = (3, "Näidisamet", OrganisationType.AUTHORITY, ())
UNION = (4, "Näidisliit", OrganisationType.ASSOCIATION, ())
CATALOGUE = catalogue(MINISTRY, CHAMBER, AGENCY, UNION)

EMPTY = CurrentValues(title="kiri")


def run(*documents: SourceDocument, current: CurrentValues = EMPTY, organisations=CATALOGUE):
    return analyse(
        AnalysisInput(documents=tuple(documents)),
        organisations=organisations,
        policy_areas=AREAS,  # type: ignore[arg-type]
        current=current,
    )


def values(analysis, field: str) -> list[tuple[str, str]]:
    suggestions = analysis.fields.get(field)
    if suggestions is None:
        return []
    return [(c.confidence, c.display) for c in suggestions.candidates]


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "written",
    ["18.09.2026", "18. september 2026", "18. septembril 2026", "2026-09-18", "18.9.2026"],
)
def test_every_supported_date_form_reads_as_the_same_day(written: str) -> None:
    found = ts.find_deadline_dates(f"Palume esitada arvamus hiljemalt {written}.")
    assert [(f.value, f.strength) for f in found] == [(date(2026, 9, 18), Confidence.HIGH)]


def test_the_translative_month_states_a_deadline_by_itself() -> None:
    """«18. septembriks 2026» is «by 18 September» — the ending is the cue."""
    found = ts.find_deadline_dates("Ootame Teie seisukohta 18. septembriks 2026.")
    assert found[0].strength == Confidence.HIGH
    assert "…ks (tähtajaks)" in found[0].cues


@pytest.mark.parametrize(
    "sentence",
    [
        "Käesolev määrus jõustub 1. jaanuaril 2027.",
        "Dokument on allkirjastatud digitaalselt 05.09.2026.",
        "Vastuseks Teie 04.09.2026 kirjale nr 1-7/26/321.",
        "Eelnõu on koostatud 03.09.2026 seisuga.",
        "Koosolek toimus 02.09.2026 Tallinnas.",
    ],
)
def test_a_date_in_a_decoy_clause_is_never_a_deadline(sentence: str) -> None:
    found = ts.find_deadline_dates(sentence)
    assert found and all(f.strength == Confidence.LOW for f in found), found


def test_the_document_dateline_is_not_a_deadline() -> None:
    found = ts.find_deadline_dates(
        "Tallinn, 05.09.2026\n\nLugupeetud partnerid", opens_document=True
    )
    assert [(f.strength, f.rule) for f in found] == [(Confidence.LOW, "dateline")]


def test_a_date_with_no_wording_at_all_is_dropped_by_the_analysis() -> None:
    analysis = run(document("Näidisministeerium\n\nKohtumine on 12.10.2026 kell 10."))
    assert SuggestedField.RESPONSE_DEADLINE not in analysis.fields


def test_the_explicit_deadline_beats_the_document_date_and_the_effective_date() -> None:
    analysis = run(document(DECOY_DOCUMENT))
    assert values(analysis, SuggestedField.RESPONSE_DEADLINE) == [(Confidence.HIGH, "20.9.2026")]
    assert not analysis.fields[SuggestedField.RESPONSE_DEADLINE].conflict


def test_two_genuine_deadlines_are_a_conflict_not_a_choice() -> None:
    analysis = run(document(AMBIGUOUS_DEADLINES))
    suggestions = analysis.fields[SuggestedField.RESPONSE_DEADLINE]
    assert suggestions.conflict
    assert {c.display for c in suggestions.high} == {"18.9.2026", "25.9.2026"}
    assert suggestions.prefill_candidate is None
    initial, _ = prefill_initial(analysis, base={}, current=EMPTY)
    assert "response_deadline" not in initial


def test_a_period_is_never_offered_as_a_day() -> None:
    """«septembris 2026» has no day; the field is a day. Nothing is invented."""
    found = ts.find_deadline_dates("Palume arvamust hiljemalt septembris 2026.")
    assert found == []


def test_a_deadline_in_an_explanatory_memorandum_is_only_a_suggestion() -> None:
    memorandum = (
        "Seletuskiri pakendiseaduse eelnõu juurde\n\nArvamused palume esitada hiljemalt 18.09.2026."
    )
    analysis = run(document(memorandum, "seletuskiri.pdf"))
    assert values(analysis, SuggestedField.RESPONSE_DEADLINE) == [(Confidence.MEDIUM, "18.9.2026")]


def test_a_relative_deadline_is_a_finding_and_never_a_date() -> None:
    analysis = run(document("Näidisministeerium\n\nPalume seisukohta kolme nädala jooksul."))
    assert SuggestedField.RESPONSE_DEADLINE not in analysis.fields
    relative = [c for c in analysis.findings if c.field == SuggestedField.RELATIVE_DEADLINE]
    assert [c.display for c in relative] == ["kolme nädala jooksul"]


def test_the_deadline_wording_is_the_evidence() -> None:
    analysis = run(document(COVERING_LETTER))
    candidate = analysis.fields[SuggestedField.RESPONSE_DEADLINE].candidates[0]
    assert "Palume esitada arvamus hiljemalt 18. septembriks 2026" in candidate.evidence
    assert candidate.provenance.source_label == "kiri.pdf · lk 1"
    assert candidate.form_value == "18.9.2026"
    assert candidate.value == "2026-09-18"


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------


def test_the_formal_heading_is_the_strong_title() -> None:
    analysis = run(document(COVERING_LETTER))
    assert values(analysis, SuggestedField.TITLE)[0] == (
        Confidence.HIGH,
        "Pakendiseaduse muutmise seaduse eelnõu",
    )


def test_prose_that_mentions_the_act_is_not_a_heading() -> None:
    offered = [c.display for c in analysis_of(COVERING_LETTER).fields[SuggestedField.TITLE].offered]
    assert all("Saadame" not in title and "registreeritud" not in title for title in offered)


def analysis_of(text: str):
    return run(document(text))


def test_a_heading_after_the_address_block_still_counts() -> None:
    analysis = run(document(DOCX_STYLE, "kiri.docx"))
    assert (
        values(analysis, SuggestedField.TITLE)[0][1] == "Elektrituruseaduse muutmise seaduse eelnõu"
    )


def test_seletuskiri_alone_is_not_a_title() -> None:
    analysis = run(document("Seletuskiri\n\nÜldosa\n\nEelnõu koostati 2026. aastal."))
    assert SuggestedField.TITLE not in analysis.fields


def test_a_memorandum_heading_yields_the_draft_it_belongs_to() -> None:
    analysis = run(
        document("Seletuskiri pakendiseaduse muutmise seaduse eelnõu juurde\n\nÜldosa", "sk.pdf")
    )
    (confidence, title), *_ = values(analysis, SuggestedField.TITLE)
    assert title == "Pakendiseaduse muutmise seaduse eelnõu"
    assert confidence == Confidence.MEDIUM


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("VS: Re: Pakendiseaduse eelnõu — kooskõlastamiseks", "Pakendiseaduse eelnõu"),
        ("FW: Fwd: Määruse eelnõu arvamuse avaldamiseks", "Määruse eelnõu"),
        ("Re: Palun arvamust", None),
    ],
)
def test_mail_prefixes_and_purpose_suffixes_are_stripped_from_a_subject(subject, expected) -> None:
    found = ts.subject_title(subject)
    assert (found.text if found else None) == expected


def test_a_formal_heading_outranks_the_subject_line() -> None:
    analysis = run(document(COVERING_LETTER), email_document())
    ranked = values(analysis, SuggestedField.TITLE)
    assert ranked[0] == (Confidence.HIGH, "Pakendiseaduse muutmise seaduse eelnõu")
    assert (Confidence.MEDIUM, "Pakendiseaduse eelnõu") in ranked


def test_two_documents_with_different_formal_headings_are_a_conflict() -> None:
    analysis = run(
        document(COVERING_LETTER, "a.pdf"),
        document(MULTI_AREA, "b.pdf"),
    )
    suggestions = analysis.fields[SuggestedField.TITLE]
    assert suggestions.conflict
    assert suggestions.prefill_candidate is None


def test_a_typed_title_is_never_replaced_but_the_heading_is_still_offered() -> None:
    typed = CurrentValues(title="Minu enda pealkiri")
    analysis = run(document(COVERING_LETTER), current=typed)
    initial, annotated = prefill_initial(
        analysis, base={"title": "Minu enda pealkiri"}, current=typed
    )
    assert initial["title"] == "Minu enda pealkiri"
    assert annotated.fields[SuggestedField.TITLE].candidates[0].prefilled is False


def test_no_title_is_ever_pre_filled_however_strong_the_heading() -> None:
    """The conservative half of the title rule, stated on its own.

    Nothing in the record separates a title a person typed from the one
    intake derived from a filename, so the classification is not attempted
    and the box is never written into. The heading is still offered, which
    is what «Kasuta» is for (docs/adr/0060).
    """
    fallback = CurrentValues(title="kiri")
    analysis = run(document(COVERING_LETTER), current=fallback)
    strongest = analysis.fields[SuggestedField.TITLE].candidates[0]
    assert strongest.confidence == Confidence.HIGH
    assert strongest.display == "Pakendiseaduse muutmise seaduse eelnõu"

    initial, annotated = prefill_initial(analysis, base={"title": "kiri"}, current=fallback)
    assert initial["title"] == "kiri"
    assert SuggestedField.TITLE not in annotated.prefilled
    assert all(not c.prefilled for c in annotated.fields[SuggestedField.TITLE].candidates)


# ---------------------------------------------------------------------------
# Senders
# ---------------------------------------------------------------------------


def test_the_letterhead_organisation_is_the_sender() -> None:
    analysis = run(document(COVERING_LETTER))
    assert values(analysis, SuggestedField.SOURCE_ORGANISATIONS)[0] == (
        Confidence.HIGH,
        "Näidisministeerium",
    )


def test_koda_itself_is_never_proposed_as_a_sender() -> None:
    analysis = run(document(COVERING_LETTER))
    assert "Eesti Kaubandus-Tööstuskoda" not in [
        d for _, d in values(analysis, SuggestedField.SOURCE_ORGANISATIONS)
    ]


def test_organisations_merely_mentioned_are_not_senders() -> None:
    analysis = run(document(MANY_ORGANISATIONS))
    offered = analysis.fields[SuggestedField.SOURCE_ORGANISATIONS].offered
    assert [(c.confidence, c.display) for c in offered] == [(Confidence.HIGH, "Näidisministeerium")]
    mentioned = {
        c.display for c in analysis.other_findings if c.field == SuggestedField.ORGANISATION_MENTION
    }
    assert mentioned == {"Näidisamet", "Näidisliit"}


def test_an_inflected_name_is_still_the_organisation_and_nothing_else_is() -> None:
    pattern = ts.organisation_pattern(
        organisation_id=1,
        display="Näidisministeerium",
        form="Näidisministeerium",
        is_abbreviation=False,
    )
    assert pattern is not None
    for form in (
        "Näidisministeerium",
        "Näidisministeeriumi",
        "Näidisministeeriumile",
        "NÄIDISMINISTEERIUMIST",
    ):
        assert pattern.regex.search(ts.fold(form)[0]), form
    for form in ("Näidisministeeriumid on", "Näidisamet", "Näidisministeeriumitaoline"):
        folded, _ = ts.fold(form)
        assert not (pattern.regex.search(folded) and form.startswith("Näidisamet")), form
    assert not pattern.regex.search(ts.fold("Näidisministeeriumitaoline")[0])


def test_a_short_abbreviation_needs_a_word_boundary_and_is_never_strong() -> None:
    text = (
        "Näidisministeerium\n\nLugupeetud\n\n"
        "NäM-i hinnangul on arm ja armee eri asjad. NäM-ile saadeti kiri."
    )
    analysis = run(document(text))
    assert values(analysis, SuggestedField.SOURCE_ORGANISATIONS)[0] == (
        Confidence.HIGH,
        "Näidisministeerium",
    )
    mentions = ts.find_organisation_mentions(document("arm armee arms"), CATALOGUE.patterns)
    assert mentions == []


def test_the_email_from_header_outranks_organisations_in_the_body() -> None:
    body = "Tere\n\nNäidisamet ja Näidisliit on eelnõu kooskõlastanud. Palume seisukohta."
    analysis = run(email_document(body, from_name="Näidisministeerium, Kadri Näidis"))
    ranked = values(analysis, SuggestedField.SOURCE_ORGANISATIONS)
    assert ranked[0] == (Confidence.HIGH, "Näidisministeerium")
    assert all(
        confidence != Confidence.HIGH for confidence, name in ranked if name != "Näidisministeerium"
    )
    candidate = analysis.fields[SuggestedField.SOURCE_ORGANISATIONS].candidates[0]
    assert candidate.rule == "email_from_display"
    assert candidate.provenance.source_label == "kiri.eml · kirja päis"


def test_an_email_domain_that_spells_a_recorded_alias_is_only_a_suggestion() -> None:
    analysis = run(email_document(from_name="Kadri Näidis", from_email="kadri@nam.invalid"))
    assert values(analysis, SuggestedField.SOURCE_ORGANISATIONS) == [
        (Confidence.MEDIUM, "Näidisministeerium")
    ]


def test_two_strong_senders_are_a_conflict() -> None:
    analysis = run(
        document(COVERING_LETTER, "a.pdf"),
        document("Näidisamet\n\nLugupeetud\n\nPalume seisukohta hiljemalt 10.10.2026.", "b.pdf"),
    )
    suggestions = analysis.fields[SuggestedField.SOURCE_ORGANISATIONS]
    assert suggestions.conflict
    assert suggestions.prefill_candidate is None


def test_an_organisation_name_inside_an_address_is_not_a_mention() -> None:
    text = (
        "Lugupeetud\n\nKirjutage aadressil info@naidisministeerium.invalid "
        "või vt naidisministeerium.ee."
    )
    assert ts.find_organisation_mentions(document(text), CATALOGUE.patterns) == []


# ---------------------------------------------------------------------------
# Track and policy areas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (COVERING_LETTER, Track.DOMESTIC),
        (EU_INITIATIVE, Track.EU_INITIATIVE),
        (TRANSPOSITION, Track.NATIONAL_TRANSPOSITION),
        (STRATEGY, Track.STRATEGY),
    ],
)
def test_the_track_is_read_from_high_signal_language(text: str, expected: str) -> None:
    analysis = run(document(text))
    top = analysis.fields[SuggestedField.TRACK].candidates[0]
    assert (top.value, top.confidence) == (expected, Confidence.HIGH), values(
        analysis, SuggestedField.TRACK
    )


def test_every_track_offered_is_a_current_track_value_and_never_other() -> None:
    for text in (
        COVERING_LETTER,
        EU_INITIATIVE,
        TRANSPOSITION,
        STRATEGY,
        MULTI_AREA,
        DECOY_DOCUMENT,
    ):
        for candidate in run(document(text)).fields.get(SuggestedField.TRACK, _none()).candidates:
            assert candidate.value in Track.values
            assert candidate.value != Track.OTHER


def _none():
    from app.matters.intake_suggestions.types import FieldSuggestions

    return FieldSuggestions(field=SuggestedField.TRACK)


def test_a_document_with_no_procedural_signal_gets_no_track() -> None:
    analysis = run(
        document("Näidisministeerium\n\nLugupeetud\n\nKohtume 12.10.2026 kell 10 Tallinnas.")
    )
    assert SuggestedField.TRACK not in analysis.fields


def test_koda_initiative_is_never_strong_from_an_incoming_document() -> None:
    analysis = run(
        document(
            "Näidisministeerium\n\nKoja ettepanek maksukorralduse muutmiseks\n\n"
            "Koja algatus on arutlusel."
        )
    )
    for candidate in analysis.fields[SuggestedField.TRACK].candidates:
        assert not (candidate.value == Track.KODA_INITIATIVE and candidate.is_high)


def test_policy_areas_are_ranked_by_specific_terms_and_capped() -> None:
    analysis = run(document(MULTI_AREA))
    ranked = analysis.fields[SuggestedField.POLICY_AREAS].candidates
    assert [c.display for c in ranked[:2]] in (
        ["Energeetika", "Maksud ja toll"],
        ["Maksud ja toll", "Energeetika"],
    )
    assert all(c.is_high for c in ranked[:2])
    assert len(ranked) <= vocab.AREA_LIMIT


def test_a_generic_word_alone_never_carries_a_policy_area() -> None:
    analysis = run(
        document("Näidisministeerium\n\nLugupeetud\n\nEnergia on oluline. Toetus on hea.")
    )
    assert SuggestedField.POLICY_AREAS not in analysis.fields


def test_policy_area_rules_resolve_through_keys_and_a_missing_key_is_a_diagnostic() -> None:
    offered = {"keskkond": AREAS["keskkond"]}
    analysis = analyse(
        AnalysisInput(documents=(document(COVERING_LETTER),)),
        organisations=CATALOGUE,
        policy_areas=offered,  # type: ignore[arg-type]
        current=EMPTY,
    )
    assert [c.value for c in analysis.fields[SuggestedField.POLICY_AREAS].candidates] == ["1"]
    assert any("maksud-ja-toll" in line for line in analysis.diagnostics)
    assert rule_diagnostics({}) and all(
        line.startswith("Valdkonna reegel") for line in rule_diagnostics({})
    )


def test_every_area_rule_names_a_key_in_the_reference_vocabulary() -> None:
    from app.taxonomy.reference_data import REFERENCE_POLICY_AREA_KEYS

    assert set(vocab.AREA_RULES) <= set(REFERENCE_POLICY_AREA_KEYS)


# ---------------------------------------------------------------------------
# Contacts and references
# ---------------------------------------------------------------------------


def test_the_email_sender_is_a_strong_finding_with_name_and_address() -> None:
    analysis = run(email_document())
    sender = next(c for c in analysis.findings if c.field == SuggestedField.SENDER_CONTACT)
    assert (sender.display, sender.detail, sender.confidence) == (
        "Kadri Näidis",
        "kadri@naidisministeerium.invalid",
        Confidence.HIGH,
    )
    assert sender.provenance.source_label == "kiri.eml · kirja päis"
    sent = next(c for c in analysis.findings if c.field == SuggestedField.EMAIL_SENT_AT)
    assert sent.display == "17.8.2026 09:12"


def test_a_labelled_contact_in_a_letter_is_a_contact_not_a_sender() -> None:
    analysis = run(document(COVERING_LETTER))
    contact = next(c for c in analysis.findings if c.field == SuggestedField.DOCUMENT_CONTACT)
    assert (contact.display, contact.detail, contact.confidence) == (
        "Mari Näidis",
        "mari.naidis@naidisministeerium.invalid",
        Confidence.MEDIUM,
    )
    assert not any(c.field == SuggestedField.SENDER_CONTACT for c in analysis.findings)


def test_an_address_in_the_middle_of_the_body_is_not_the_sender() -> None:
    analysis = run(document(CONTACT_IN_BODY))
    labelled = [c for c in analysis.findings if c.field == SuggestedField.DOCUMENT_CONTACT]
    assert {c.value for c in labelled} == {
        "toomas.naidis@naidisamet.invalid",
        "kadri.naidis@naidisministeerium.invalid",
    }
    weak = [c for c in analysis.other_findings if c.field == SuggestedField.DOCUMENT_CONTACT]
    assert [c.value for c in weak] == ["info@naidisliit.invalid"]


def test_references_keep_their_exact_source_text() -> None:
    analysis = run(document(COVERING_LETTER))
    references = {
        c.detail: c.value for c in analysis.findings if c.field == SuggestedField.EXTERNAL_REFERENCE
    }
    assert references == {
        "Viide Koja kirjale (Teie nr)": "1-7/26/321",
        "Saatja dokumendi nr": "1-4/26/1234-2",
    }
    eis = [c for c in analysis.findings if c.field == SuggestedField.EIS_REFERENCE]
    assert [(c.detail, c.value) for c in eis] == [("EIS", "26-0123")]


def test_eis_labelled_references_and_links_are_found_and_dates_are_refused() -> None:
    analysis = run(document(EIS_DOCUMENT))
    eis = {c.detail: c.value for c in analysis.findings if c.field == SuggestedField.EIS_REFERENCE}
    assert eis == {
        "EIS": "26-0456/01",
        "EIS link": "https://eelnoud.valitsus.ee/main/mount/docList/abc-123",
    }
    other = {
        c.detail: c.value for c in analysis.findings if c.field == SuggestedField.EXTERNAL_REFERENCE
    }
    assert other == {"Riigikogu menetlus": "742 SE"}
    links = [c.value for c in analysis.other_findings if c.field == SuggestedField.SOURCE_URL]
    assert links == ["https://www.example.invalid/lisad/seletuskiri"]
    refused = ts.find_references(document("EIS 04.09.2026 registreeritud."))
    assert refused == []


def test_eu_references_are_recognised() -> None:
    analysis = run(document(EU_INITIATIVE), document(TRANSPOSITION, "t.pdf"))
    kinds = {
        c.detail: c.value for c in analysis.findings if c.field == SuggestedField.EXTERNAL_REFERENCE
    }
    assert kinds["ELi viide (COM)"] == "COM(2026) 412 final"
    assert kinds["ELi õigusakti viide"] == "(EL) 2024/825"


def test_juristid_own_matter_reference_is_never_read_as_an_external_one() -> None:
    analysis = run(
        document("Näidisministeerium\n\nViide 2026_12 on Koja enda viide, dokumendi nr 1-4/26/77.")
    )
    found = [c.value for c in analysis.findings + analysis.other_findings]
    assert "2026_12" not in found
    assert "1-4/26/77" in found


# ---------------------------------------------------------------------------
# Provenance, OCR, multiple files
# ---------------------------------------------------------------------------


def test_ocr_text_is_labelled_as_ocr_on_every_candidate_it_produces() -> None:
    analysis = run(document(COVERING_LETTER, "skann.pdf", ocr=True))
    for suggestions in analysis.fields.values():
        for candidate in suggestions.candidates:
            assert candidate.provenance.is_ocr
            assert candidate.provenance.source_label == "skann.pdf · lk 1 · OCR"
    assert analysis.documents[0].from_ocr


def test_each_file_contributes_its_own_provenance() -> None:
    analysis = run(
        document(COVERING_LETTER, "kaaskiri.pdf"), email_document(), document(STRATEGY, "kava.pdf")
    )
    by_field = {
        field: {c.provenance.filename for c in suggestions.candidates}
        for field, suggestions in analysis.fields.items()
    }
    assert "kaaskiri.pdf" in by_field[SuggestedField.RESPONSE_DEADLINE]
    assert "kava.pdf" in by_field[SuggestedField.RESPONSE_DEADLINE]
    assert "kiri.eml" in by_field[SuggestedField.TITLE]
    sender = next(c for c in analysis.findings if c.field == SuggestedField.SENDER_CONTACT)
    assert sender.provenance.filename == "kiri.eml"


#: What the whole scan of one oversized block may cost. Generous — CI runners
#: are shared and this is not a benchmark — but far below the minutes a
#: backtracking pattern took before the bounds below were put on it.
SCAN_BUDGET_SECONDS = 20.0


#: Shapes that hung or crawled before the patterns were bounded. Each is
#: something a document can genuinely contain — a table of codes, a run of
#: dotted tokens, a schedule of dates — and document text is not ours to
#: trust for shape. Built by name so a failure reads as the name.
RUNAWAY_SHAPES: dict[str, str] = {
    "eis filler run": "EIS " + "numb" * 4000,
    "eis word run": "EIS " + "dokument" * 3000,
    "dotted run": "a@" + "a." * 20000,
    "long token": "x@" + "a" * 60000,
    "many dates": ("Palume esitada arvamus hiljemalt 18.09.2026. " * 9000)[:400_000],
}


@pytest.mark.parametrize("label", sorted(RUNAWAY_SHAPES))
def test_no_document_shape_makes_the_scan_run_away(label: str) -> None:
    """A page of suggestions is a web request. It has to come back."""
    text = RUNAWAY_SHAPES[label]
    source = document(text, "suur.pdf")
    started = time.perf_counter()
    ts.find_deadline_dates(text)
    ts.find_references(source)
    ts.find_contacts(source)
    ts.find_heading_titles(source)
    elapsed = time.perf_counter() - started
    assert elapsed < SCAN_BUDGET_SECONDS, f"{label}: {elapsed:.1f}s"


def test_bounding_the_patterns_did_not_cost_what_they_are_for() -> None:
    """The bounds are on repetition, not on what counts as a reference."""
    for text, expected in (
        ("Eelnõu on EISis registreeritud numbriga 26-0456/01.", "26-0456/01"),
        ("EIS toimik nr 24-1234 on avatud.", "24-1234"),
        ("EIS 26-0123 registreeritud.", "26-0123"),
        ("eelnõude infosüsteemis EIS toimik 26-0123.", "26-0123"),
    ):
        match = vocab.EIS_REFERENCE.search(text)
        assert match is not None and match.group("ref") == expected, text
    for address in (
        "mari.naidis@naidisministeerium.invalid",
        "kadri@nam.invalid",
        "a.b+c%d@x-y.co.uk",
    ):
        assert vocab.EMAIL_ADDRESS.findall(address) == [address]


def test_a_long_block_is_scanned_in_chunks_and_finds_every_date() -> None:
    """Chunking is an implementation detail; the findings are not."""
    filler = "Näidisministeerium saatis materjali.\n" * 900
    text = f"{filler}Palume esitada arvamus hiljemalt 18.09.2026.\n{filler}"
    assert len(text) > ts.SCAN_CHUNK
    found = ts.find_deadline_dates(text)
    assert [(f.value, f.strength) for f in found] == [(date(2026, 9, 18), Confidence.HIGH)]
    assert text[found[0].start : found[0].end] == "18.09.2026"


def test_the_same_input_always_gives_the_same_output() -> None:
    first = run(document(COVERING_LETTER), email_document())
    second = run(document(COVERING_LETTER), email_document())
    assert first == second


def test_documents_still_waiting_are_reported_not_analysed() -> None:
    analysis = analyse(
        AnalysisInput(
            documents=(
                SourceDocument(
                    "d", "v", "skann.pdf", DocumentRole.INCOMING_AUTHORITY, ExtractionState.PENDING
                ),
            )
        ),
        organisations=CATALOGUE,
        policy_areas=AREAS,  # type: ignore[arg-type]
        current=EMPTY,
    )
    assert analysis.is_waiting and not analysis.has_text
    assert analysis.documents[0].state_label == "Teksti töötlemine ootel"


def test_no_network_client_and_no_model_is_reachable_from_the_analyser() -> None:
    """The feature is rules over text. The import graph says so."""
    package = pathlib.Path(inspect.getfile(analyse)).parent
    forbidden = {
        "requests",
        "httpx",
        "urllib",
        "socket",
        "openai",
        "anthropic",
        "transformers",
        "torch",
    }
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in forbidden, f"{path.name} imports {name}"


def test_the_web_request_never_opens_evidence_bytes() -> None:
    """The view reads derivatives; the parser registry is the worker's."""
    source = inspect.getsource(matter_views.matter_edit_assisted)
    assert "registry" not in source and "evidence_storage" not in source and "parse(" not in source
    package = pathlib.Path(inspect.getfile(analyse)).parent
    for path in package.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "evidence_storage" not in text and "extraction.parsers" not in text, path.name
        assert "MalwareScanState" not in text, path.name


# ---------------------------------------------------------------------------
# Against the database: authorization, currency, and the GET that writes nothing
# ---------------------------------------------------------------------------

pytestmark_db = pytest.mark.django_db


def _publish_text(
    version, pages: list[tuple[str, str]], *, status=DerivativeStatus.ACTIVE
) -> DocumentDerivative:
    """Store extracted text the way the worker does, without running a parser.

    Mirrors ``orchestrator._write_derivative``: one derivative, fragments from
    ordinal 1, promoted after the old one is demoted. Used where a test needs
    a shape the synthetic corpus cannot produce on demand — OCR provenance,
    a superseded derivative — and nowhere else.
    """
    from django.utils import timezone

    derivative = DocumentDerivative.objects.create(
        version=version,
        kind=DerivativeKind.EXTRACTED_TEXT,
        generator="test",
        generator_version="1",
        status=DerivativeStatus.BUILDING,
        fragment_count=len(pages),
        character_count=sum(len(text) for text, _ in pages),
    )
    DocumentTextFragment.objects.bulk_create(
        [
            DocumentTextFragment(
                derivative=derivative,
                ordinal=ordinal,
                text=text,
                text_source=source,
                locator_kind=LocatorKind.PAGE,
                locator={"page": ordinal},
                locator_label=f"lk {ordinal}",
                character_count=len(text),
            )
            for ordinal, (text, source) in enumerate(pages, start=1)
        ]
    )
    if status == DerivativeStatus.ACTIVE:
        DocumentDerivative.objects.filter(
            version=version, kind=DerivativeKind.EXTRACTED_TEXT, status=DerivativeStatus.ACTIVE
        ).exclude(pk=derivative.pk).update(status=DerivativeStatus.SUPERSEDED)
    derivative.status = status
    derivative.built_at = timezone.now()
    derivative.save(update_fields=["status", "built_at", "updated_at"])
    version.extraction_state = ExtractionState.DONE
    version.save(update_fields=["extraction_state", "updated_at"])
    return derivative


@pytest.fixture
def ministry(db):
    organisation = Organisation.objects.create(
        name="Näidisministeerium", organisation_type=OrganisationType.MINISTRY
    )
    OrganisationAlias.objects.create(organisation=organisation, alias="NäM")
    Organisation.objects.create(name="Näidisamet", organisation_type=OrganisationType.AUTHORITY)
    return organisation


@pytest.fixture
def intake_matter(db, specialist, ministry, capture_evidence, extract):
    """A Matter filed the way intake files it: mechanical title, one letter, one message."""
    matter = factories.MatterFactory(owner=specialist, title=title_from_filename("kaaskiri.pdf"))
    letter = capture_evidence(matter, corpus.text_pdf([COVERING_LETTER]), "kaaskiri.pdf", PDF)
    message = capture_evidence(
        matter,
        corpus.consultation_eml(attachments=False, inline_logo=False),
        "kiri.eml",
        EML,
        role=DocumentRole.ORIGINAL_EMAIL,
    )
    extract(letter)
    extract(message)
    return matter


def _assisted(matter):
    return reverse("matters:matter_edit_assisted", kwargs={"pk": matter.pk})


def _checked(body: str, name: str, value: str) -> bool:
    """Whether the rendered control ``name`` has ``value`` ticked."""
    pattern = rf'<input[^>]*name="{re.escape(name)}"[^>]*value="{re.escape(value)}"[^>]*checked'
    return re.search(pattern, body) is not None


def _control_value(body: str, control_id: str) -> str:
    """The ``value`` the rendered text input ``control_id`` carries."""
    match = re.search(rf'<input[^>]*id="{re.escape(control_id)}"[^>]*>', body)
    assert match is not None, control_id
    value = re.search(r'value="([^"]*)"', match.group(0))
    return value.group(1) if value else ""


@pytestmark_db
def test_the_review_get_writes_nothing(signed_in, specialist, intake_matter) -> None:
    before = (
        Matter.objects.get(pk=intake_matter.pk).title,
        ChangeEvent.objects.count(),
        Organisation.objects.count(),
        OrganisationAlias.objects.count(),
        PolicyArea.objects.count(),
        Tag.objects.count(),
        intake_matter.response_deadline,
        intake_matter.track,
        set(intake_matter.policy_areas.values_list("pk", flat=True)),
        set(intake_matter.source_organisations.values_list("pk", flat=True)),
    )
    response = signed_in.get(_assisted(intake_matter))
    assert response.status_code == 200
    intake_matter.refresh_from_db()
    after = (
        intake_matter.title,
        ChangeEvent.objects.count(),
        Organisation.objects.count(),
        OrganisationAlias.objects.count(),
        PolicyArea.objects.count(),
        Tag.objects.count(),
        intake_matter.response_deadline,
        intake_matter.track,
        set(intake_matter.policy_areas.values_list("pk", flat=True)),
        set(intake_matter.source_organisations.values_list("pk", flat=True)),
    )
    assert after == before


@pytestmark_db
def test_high_confidence_suggestions_prefill_the_empty_unsaved_form(
    signed_in, intake_matter, ministry
) -> None:
    body = signed_in.get(_assisted(intake_matter)).content.decode()
    # The title is offered rather than filled in; every other empty field
    # takes its strong suggestion (see the title-provenance tests below).
    assert 'data-suggest-value="Pakendiseaduse muutmise seaduse eelnõu"' in body
    assert _control_value(body, "id_response_deadline") == "18.9.2026"
    assert _checked(body, "track", Track.DOMESTIC)
    keskkond = PolicyArea.objects.get(key="keskkond")
    assert _checked(body, "policy_areas", str(keskkond.pk))
    assert _checked(body, "source_organisations", str(ministry.pk))
    assert "vormil eeltäidetud" in body
    assert "Dokumendist leitud" in body
    assert "Palume esitada arvamus hiljemalt 18. septembriks 2026" in body
    assert "kaaskiri.pdf · lk 1" in body
    # Nothing was saved by showing it.
    intake_matter.refresh_from_db()
    assert intake_matter.title == "kaaskiri"
    assert intake_matter.response_deadline is None
    assert intake_matter.track == ""


@pytestmark_db
def test_an_existing_canonical_value_is_never_overwritten_by_a_suggestion(
    signed_in, intake_matter, ministry
) -> None:
    other = Organisation.objects.get(name="Näidisamet")
    intake_matter.title = "Minu enda pealkiri"
    intake_matter.response_deadline = date(2026, 12, 1)
    intake_matter.track = Track.STRATEGY
    intake_matter.save()
    intake_matter.source_organisations.set([other])
    response = signed_in.get(_assisted(intake_matter))
    body = response.content.decode()
    assert _control_value(body, "id_title") == "Minu enda pealkiri"
    assert _control_value(body, "id_response_deadline") == "1.12.2026"
    assert _checked(body, "track", Track.STRATEGY)
    assert not _checked(body, "track", Track.DOMESTIC)
    assert _checked(body, "source_organisations", str(other.pk))
    assert not _checked(body, "source_organisations", str(ministry.pk))
    # The document's answers are still on the page — as «Kasuta» offers.
    assert "Pakendiseaduse muutmise seaduse eelnõu" in body
    assert 'data-suggest-value="18.9.2026"' in body
    assert body.count("Kasuta") >= 3
    # Only the field this Matter left empty may be pre-filled. Valdkonnad was
    # never set here, so that is the one field the GET filled — the same rule
    # stated from the other side.
    keskkond = PolicyArea.objects.get(key="keskkond")
    assert _checked(body, "policy_areas", str(keskkond.pk))
    assert set(response.context["assisted"].prefilled) == {SuggestedField.POLICY_AREAS}
    assert "vormil eeltäidetud" in body


@pytestmark_db
def test_the_posted_value_wins_over_every_suggestion(
    signed_in, specialist, intake_matter, ministry
) -> None:
    """Saving is the existing edit form. What the person sends is what is stored."""
    edit = reverse("matters:matter_edit", kwargs={"pk": intake_matter.pk})
    response = signed_in.post(
        edit,
        {
            "title": "Pakendiseaduse muutmise seaduse eelnõu",
            "response_deadline": "19.9.2026",
            "track": Track.DOMESTIC,
            "visibility": Visibility.NORMAL,
            "owner": specialist.pk,
        },
    )
    assert response.status_code == 302
    intake_matter.refresh_from_db()
    assert intake_matter.response_deadline == date(2026, 9, 19)
    assert intake_matter.title == "Pakendiseaduse muutmise seaduse eelnõu"
    assert intake_matter.track == Track.DOMESTIC
    # The medium sender suggestion was not chosen, so it was not saved; the
    # audit trail names the person, not a classifier.
    assert not intake_matter.source_organisations.exists()
    saved = ChangeEvent.objects.filter(
        matter=intake_matter,
        event_type__in=(
            ChangeEventType.MATTER_TITLE_CHANGED,
            ChangeEventType.MATTER_DATE_CHANGED,
            ChangeEventType.MATTER_TRACK_CHANGED,
        ),
    )
    assert saved.count() == 3
    assert all(event.actor_id == specialist.pk for event in saved)
    assert not ChangeEvent.objects.filter(
        matter=intake_matter, event_type=ChangeEventType.MATTER_POLICY_AREAS_CHANGED
    ).exists()


@pytestmark_db
def test_a_refused_save_keeps_what_was_typed_and_does_not_re_run_suggestions(
    signed_in, specialist, intake_matter
) -> None:
    edit = reverse("matters:matter_edit", kwargs={"pk": intake_matter.pk})
    response = signed_in.post(
        edit, {"title": "", "response_deadline": "31.02.2026", "owner": specialist.pk}
    )
    assert response.status_code == 400
    body = response.content.decode()
    assert 'value="31.02.2026"' in body
    assert "Dokumendist leitud" not in body


@pytestmark_db
def test_a_restricted_document_contributes_no_evidence_to_a_reader_who_may_not_see_it(
    specialist, reader, ministry, capture_evidence, extract
) -> None:
    matter = factories.MatterFactory(owner=specialist)
    hidden = capture_evidence(
        matter,
        corpus.text_pdf([COVERING_LETTER]),
        "salajane.pdf",
        PDF,
        visibility_override=Visibility.RESTRICTED,
    )
    extract(hidden)
    for_owner = build_analysis_input(matter, specialist)
    for_reader = build_analysis_input(matter, reader)
    assert [d.filename for d in for_owner.documents] == ["salajane.pdf"]
    assert for_reader.documents == ()
    analysis = analyse_matter(matter, reader)
    assert not analysis.fields and not analysis.findings and analysis.documents == ()


@pytestmark_db
def test_only_the_current_version_and_only_active_derivatives_are_read(
    specialist, ministry, capture_evidence, extract
) -> None:
    from app.documents.services import add_evidence_version

    matter = factories.MatterFactory(owner=specialist)
    first = capture_evidence(matter, corpus.text_pdf([AMBIGUOUS_DEADLINES]), "kiri.pdf", PDF)
    extract(first)
    # A stale derivative that says something else, demoted the way an upgrade demotes.
    _publish_text(
        first,
        [("Palume vastata hiljemalt 01.01.2020.", TextSource.NATIVE)],
        status=DerivativeStatus.SUPERSEDED,
    )
    analysis = analyse_matter(matter, specialist)
    assert {c.display for c in analysis.fields[SuggestedField.RESPONSE_DEADLINE].candidates} == {
        "18.9.2026",
        "25.9.2026",
    }

    # A newer version replaces the old one entirely.
    second = add_evidence_version(
        document=first.document,
        content=corpus.text_pdf([DECOY_DOCUMENT]),
        original_filename="kiri-v2.pdf",
        mime_type=PDF,
    )
    extract(second)
    analysis = analyse_matter(matter, specialist)
    assert [c.display for c in analysis.fields[SuggestedField.RESPONSE_DEADLINE].candidates] == [
        "20.9.2026"
    ]
    assert analysis.documents[0].filename == "kiri-v2.pdf"


@pytestmark_db
def test_ocr_provenance_survives_the_database_round_trip(
    specialist, ministry, capture_evidence
) -> None:
    matter = factories.MatterFactory(owner=specialist)
    version = capture_evidence(matter, corpus.text_pdf(["placeholder"]), "skann.pdf", PDF)
    _publish_text(version, [(COVERING_LETTER, TextSource.OCR)])
    analysis = analyse_matter(matter, specialist)
    deadline = analysis.fields[SuggestedField.RESPONSE_DEADLINE].candidates[0]
    assert deadline.provenance.is_ocr
    assert deadline.provenance.source_label == "skann.pdf · lk 1 · OCR"
    assert analysis.documents[0].from_ocr


@pytestmark_db
def test_a_pending_document_is_reported_honestly(signed_in, specialist, capture_evidence) -> None:
    matter = factories.MatterFactory(owner=specialist)
    capture_evidence(matter, corpus.text_pdf([COVERING_LETTER]), "kaaskiri.pdf", PDF)
    body = signed_in.get(_assisted(matter)).content.decode()
    assert "Teksti eraldamine on ootel" in body
    assert "Teksti töötlemine ootel" in body
    assert "Kontrolli uuesti" in body
    assert "vormil eeltäidetud" not in body


@pytestmark_db
def test_email_sender_name_and_address_come_from_the_structured_headers(
    signed_in, intake_matter
) -> None:
    body = signed_in.get(_assisted(intake_matter)).content.decode()
    assert "Saatja kontakt" in body
    assert "Kadri Näidis" in body
    assert "kadri@naidisministeerium.invalid" in body
    assert "kiri.eml · kirja päis" in body
    assert "Kirja saatmise aeg" in body


@pytestmark_db
def test_the_review_is_a_business_write_surface_and_a_reader_is_refused(
    client, reader, intake_matter
) -> None:
    client.force_login(reader)
    assert client.get(_assisted(intake_matter)).status_code == 404
    client.logout()
    response = client.get(_assisted(intake_matter))
    assert response.status_code == 302 and "/konto/" in response["Location"]


@pytestmark_db
def test_a_matter_with_no_documents_offers_no_review_link_and_an_honest_panel(
    signed_in, specialist
) -> None:
    matter = factories.MatterFactory(owner=specialist)
    edit_body = signed_in.get(
        reverse("matters:matter_edit", kwargs={"pk": matter.pk})
    ).content.decode()
    assert "Kontrolli dokumendist leitud andmeid" not in edit_body
    body = signed_in.get(_assisted(matter)).content.decode()
    assert "Teemal ei ole dokumente" in body


@pytestmark_db
def test_the_edit_page_offers_the_review_when_there_is_material(signed_in, intake_matter) -> None:
    body = signed_in.get(
        reverse("matters:matter_edit", kwargs={"pk": intake_matter.pk})
    ).content.decode()
    assert _assisted(intake_matter) in body
    assert "Kontrolli dokumendist leitud andmeid" in body
    detail = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": intake_matter.pk})
    ).content.decode()
    assert _assisted(intake_matter) in detail


@pytestmark_db
def test_the_analysis_query_count_does_not_grow_with_the_number_of_fragments(
    specialist, ministry, capture_evidence, extract
) -> None:
    small = factories.MatterFactory(owner=specialist)
    extract(capture_evidence(small, corpus.text_pdf([COVERING_LETTER]), "a.pdf", PDF))
    large = factories.MatterFactory(owner=specialist)
    for name in ("a", "b", "c", "d"):
        extract(
            capture_evidence(
                large,
                corpus.text_pdf([COVERING_LETTER, STRATEGY, EU_INITIATIVE]),
                f"{name}.pdf",
                PDF,
            )
        )
    with CaptureQueriesContext(connection) as few:
        analyse_matter(small, specialist)
    with CaptureQueriesContext(connection) as many:
        analyse_matter(large, specialist)
    assert len(many.captured_queries) == len(few.captured_queries)
    # Documents, derivatives, fragments, organisations, aliases, areas, and the
    # Matter's own relations: a handful, and the same handful whatever the size.
    assert len(few.captured_queries) <= 10, [q["sql"][:80] for q in few.captured_queries]


@pytestmark_db
def test_the_catalogue_is_read_from_names_and_recorded_aliases_only(ministry) -> None:
    loaded = load_organisation_catalogue()
    forms = {pattern.form for pattern in loaded.patterns if pattern.organisation_id == ministry.pk}
    assert forms == {"Näidisministeerium", "NäM"}
    assert all(
        not pattern.is_abbreviation or pattern.form == "NäM"
        for pattern in loaded.patterns
        if pattern.organisation_id == ministry.pk
    )


# ---------------------------------------------------------------------------
# Title provenance: a human title is never mistaken for intake's fallback
# ---------------------------------------------------------------------------


@pytestmark_db
def test_a_human_title_is_not_overwritten_when_a_later_file_matches_it(
    signed_in, specialist, ministry, capture_evidence, extract
) -> None:
    """The defect this pass exists to close.

    A lawyer names the Matter, somebody attaches a file whose normalised name
    is the same string, and the old rule read that coincidence as «the title
    is intake's fallback». A different heading could then be pre-filled over
    a title a person had written.
    """
    from app.matters.services import create_matter

    human = "Pakendiseaduse muutmise seaduse eelnõu"
    matter = create_matter(
        title=human, actor=specialist, owner=specialist, reference_year=2099, reference_number=911
    )
    version = capture_evidence(
        matter, corpus.text_pdf([DOCX_STYLE]), "Pakendiseaduse_muutmise_seaduse_eelnõu.pdf", PDF
    )
    extract(version)
    # The coincidence the old rule tripped over really is present.
    assert title_from_filename("Pakendiseaduse_muutmise_seaduse_eelnõu.pdf") == human

    body = signed_in.get(_assisted(matter)).content.decode()
    assert _control_value(body, "id_title") == human
    # The document's own heading differs from the human title and is offered.
    assert 'data-suggest-for="title"' in body
    assert "Elektrituruseaduse muutmise seaduse eelnõu" in body
    matter.refresh_from_db()
    assert matter.title == human


@pytestmark_db
def test_the_human_title_decision_does_not_depend_on_what_the_viewer_can_see(
    client, specialist, reader, department_head, ministry, capture_evidence, extract
) -> None:
    """Two colleagues, one restricted attachment, one answer.

    Whether somebody else's title is protected must not be a function of which
    child documents the reader happens to be authorised to open (hardening
    §2.4). Visibility still decides which documents contribute suggestions.
    """
    from app.matters.services import create_matter

    human = "Pakendiseaduse muutmise seaduse eelnõu"
    matter = create_matter(
        title=human, actor=specialist, owner=specialist, reference_year=2099, reference_number=912
    )
    hidden = capture_evidence(
        matter,
        corpus.text_pdf([DOCX_STYLE]),
        "Pakendiseaduse_muutmise_seaduse_eelnõu.pdf",
        PDF,
        visibility_override=Visibility.RESTRICTED,
    )
    extract(hidden)

    for viewer in (specialist, department_head):
        client.force_login(viewer)
        body = client.get(_assisted(matter)).content.decode()
        assert _control_value(body, "id_title") == human, viewer
    # The reader may not reach the surface at all, so nothing about the
    # decision can differ for them either.
    client.force_login(reader)
    assert client.get(_assisted(matter)).status_code == 404


@pytestmark_db
def test_an_untouched_intake_fallback_title_is_offered_rather_than_filled(
    signed_in, intake_matter
) -> None:
    """The conservative half, against a genuine intake Matter.

    `intake_matter` is filed exactly as `register_incoming` files one and its
    title is still the mechanical filename. Even here the heading is offered
    and not written in, because no record distinguishes this title from one a
    person typed into the same box (docs/adr/0060).
    """
    assert intake_matter.title == title_from_filename("kaaskiri.pdf")
    response = signed_in.get(_assisted(intake_matter))
    body = response.content.decode()

    assert _control_value(body, "id_title") == "kaaskiri"
    assert SuggestedField.TITLE not in response.context["assisted"].prefilled
    assert 'data-suggest-value="Pakendiseaduse muutmise seaduse eelnõu"' in body
    # The other fields still pre-fill: only the title lacks provenance.
    assert _checked(body, "track", Track.DOMESTIC)
    assert _control_value(body, "id_response_deadline") == "18.9.2026"


# ---------------------------------------------------------------------------
# The Matter-level analysis budget
# ---------------------------------------------------------------------------


def _bulky(marker: str, size: int) -> str:
    """Realistic prose of a given size, carrying one findable marker."""
    filler = (
        "Eelnõuga täpsustatakse aruandluskohustuse ulatust ja korrastatakse "
        "rakendussätteid vastavalt seaduse üldisele korrale. "
    )
    return (marker + "\n" + filler * (size // len(filler) + 1))[:size]


@pytestmark_db
def test_the_matter_budget_bounds_the_text_read_however_many_documents_there_are(
    specialist, ministry, capture_evidence
) -> None:
    matter = factories.MatterFactory(owner=specialist, reference_year=2099, reference_number=921)
    per = 30_000
    for index in range(12):
        version = capture_evidence(
            matter, corpus.text_pdf(["placeholder"]), f"annex-{index:02d}.pdf", PDF
        )
        _publish_text(version, [(_bulky(f"Lisa {index}", per), TextSource.NATIVE)])

    analysis_input = build_analysis_input(matter, specialist)
    read = sum(len(block.text) for doc in analysis_input.documents for block in doc.blocks)

    assert 12 * per > intake_input.MAX_TOTAL_ANALYSIS_CHARACTERS, "the world must exceed the cap"
    assert read <= intake_input.MAX_TOTAL_ANALYSIS_CHARACTERS
    assert analysis_input.skipped_for_budget, "documents left out are recorded as left out"
    assert analysis_input.partial
    # Everything is still represented; the skipped ones simply have no text.
    assert len(analysis_input.documents) == 12
    assert all(not doc.blocks for doc in analysis_input.skipped_for_budget)


@pytestmark_db
def test_a_derivative_with_no_recorded_size_is_planned_at_the_ceiling(
    specialist, ministry, capture_evidence
) -> None:
    """A missing size must shrink the plan, never open it up.

    The orchestrator always records `character_count`, so this is the
    defensive reading of a row that somehow lacks one: planning it as free
    would let any number of documents into a single fragment load.
    """
    matter = factories.MatterFactory(owner=specialist, reference_year=2099, reference_number=930)
    for index in range(10):
        version = capture_evidence(
            matter, corpus.text_pdf(["placeholder"]), f"lisa-{index:02d}.pdf", PDF
        )
        derivative = _publish_text(version, [(_bulky(f"Lisa {index}", 50_000), TextSource.NATIVE)])
        DocumentDerivative.objects.filter(pk=derivative.pk).update(character_count=0)

    analysis_input = build_analysis_input(matter, specialist)
    read = sum(len(block.text) for doc in analysis_input.documents for block in doc.blocks)
    assert read <= intake_input.MAX_TOTAL_ANALYSIS_CHARACTERS
    assert analysis_input.skipped_for_budget


@pytestmark_db
def test_the_bounded_population_is_the_same_on_every_read(
    specialist, ministry, capture_evidence
) -> None:
    matter = factories.MatterFactory(owner=specialist, reference_year=2099, reference_number=922)
    for index in range(10):
        version = capture_evidence(
            matter, corpus.text_pdf(["placeholder"]), f"annex-{index:02d}.pdf", PDF
        )
        _publish_text(version, [(_bulky(f"Lisa {index}", 40_000), TextSource.NATIVE)])

    first = build_analysis_input(matter, specialist)
    second = build_analysis_input(matter, specialist)
    assert [(d.filename, len(d.blocks)) for d in first.documents] == [
        (d.filename, len(d.blocks)) for d in second.documents
    ]
    assert analyse_matter(matter, specialist) == analyse_matter(matter, specialist)


@pytestmark_db
def test_the_covering_material_is_read_before_the_annexes(
    specialist, ministry, capture_evidence, extract
) -> None:
    """The letter that states the deadline must not lose to a pile of annexes."""
    matter = factories.MatterFactory(owner=specialist, reference_year=2099, reference_number=923)
    # Annexes captured first, so only the priority rule can save the letter.
    for index in range(8):
        version = capture_evidence(
            matter,
            corpus.text_pdf(["placeholder"]),
            f"lisa-{index:02d}.pdf",
            PDF,
            role=DocumentRole.OTHER,
        )
        _publish_text(version, [(_bulky(f"Lisa {index}", 60_000), TextSource.NATIVE)])
    letter = capture_evidence(matter, corpus.text_pdf([COVERING_LETTER]), "kaaskiri.pdf", PDF)
    extract(letter)

    analysis = analyse_matter(matter, specialist)
    assert [c.display for c in analysis.fields[SuggestedField.RESPONSE_DEADLINE].candidates] == [
        "18.9.2026"
    ]
    assert (
        analysis.fields[SuggestedField.SOURCE_ORGANISATIONS].candidates[0].display
        == "Näidisministeerium"
    )
    assert any(c.value == "26-0123" for c in analysis.findings)
    assert analysis.is_partial


@pytestmark_db
def test_email_headers_survive_when_the_text_budget_is_gone(
    specialist, ministry, capture_evidence, extract
) -> None:
    """Structured headers cost no text budget, so they are never crowded out."""
    matter = factories.MatterFactory(owner=specialist, reference_year=2099, reference_number=924)
    for index in range(10):
        version = capture_evidence(
            matter,
            corpus.text_pdf(["placeholder"]),
            f"lisa-{index:02d}.pdf",
            PDF,
            role=DocumentRole.OTHER,
        )
        _publish_text(version, [(_bulky(f"Lisa {index}", 60_000), TextSource.NATIVE)])
    message = capture_evidence(
        matter,
        corpus.consultation_eml(attachments=False, inline_logo=False),
        "kiri.eml",
        EML,
        role=DocumentRole.ORIGINAL_EMAIL,
    )
    extract(message)

    analysis = analyse_matter(matter, specialist)
    sender = next(c for c in analysis.findings if c.field == SuggestedField.SENDER_CONTACT)
    assert sender.display == "Kadri Näidis"
    assert sender.provenance.filename == "kiri.eml"
    assert any(c.field == SuggestedField.EMAIL_SENT_AT for c in analysis.findings)


@pytestmark_db
def test_a_partial_analysis_says_so_on_the_page(signed_in, specialist, capture_evidence) -> None:
    matter = factories.MatterFactory(owner=specialist, reference_year=2099, reference_number=925)
    for index in range(9):
        version = capture_evidence(
            matter, corpus.text_pdf(["placeholder"]), f"lisa-{index:02d}.pdf", PDF
        )
        _publish_text(version, [(_bulky(f"Lisa {index}", 40_000), TextSource.NATIVE)])

    response = signed_in.get(_assisted(matter))
    body = response.content.decode()
    assert response.context["assisted"].is_partial
    assert "Analüüsiti" in body
    assert "automaatkontrollist" in body


@pytestmark_db
def test_an_over_budget_page_still_writes_nothing(signed_in, specialist, capture_evidence) -> None:
    matter = factories.MatterFactory(owner=specialist, reference_year=2099, reference_number=926)
    for index in range(9):
        version = capture_evidence(
            matter, corpus.text_pdf(["placeholder"]), f"lisa-{index:02d}.pdf", PDF
        )
        _publish_text(version, [(_bulky(f"Lisa {index}", 40_000), TextSource.NATIVE)])
    before = (matter.title, matter.track, ChangeEvent.objects.count(), Organisation.objects.count())

    assert signed_in.get(_assisted(matter)).status_code == 200

    matter.refresh_from_db()
    assert (
        matter.title,
        matter.track,
        ChangeEvent.objects.count(),
        Organisation.objects.count(),
    ) == before


@pytestmark_db
def test_a_hidden_document_spends_no_budget_and_changes_no_suggestion(
    specialist, ministry, capture_evidence, extract
) -> None:
    """Authorisation runs before budgeting, so a hidden file costs nothing."""
    matter = factories.MatterFactory(owner=specialist, reference_year=2099, reference_number=927)
    letter = capture_evidence(matter, corpus.text_pdf([COVERING_LETTER]), "kaaskiri.pdf", PDF)
    extract(letter)
    open_view = build_analysis_input(matter, specialist)
    open_read = sum(len(b.text) for d in open_view.documents for b in d.blocks)

    hidden = capture_evidence(
        matter,
        corpus.text_pdf(["placeholder"]),
        "salajane.pdf",
        PDF,
        visibility_override=Visibility.RESTRICTED,
    )
    _publish_text(hidden, [(_bulky("Salajane", 150_000), TextSource.NATIVE)])

    after = build_analysis_input(matter, specialist)
    assert sum(len(b.text) for d in after.documents for b in d.blocks) == open_read
    assert [d.filename for d in after.documents] == ["kaaskiri.pdf"]
    assert not after.skipped_for_budget
    assert not after.partial


@pytestmark_db
def test_many_documents_do_not_become_many_queries(specialist, ministry, capture_evidence) -> None:
    small = factories.MatterFactory(owner=specialist, reference_year=2099, reference_number=928)
    version = capture_evidence(small, corpus.text_pdf(["placeholder"]), "a.pdf", PDF)
    _publish_text(version, [(_bulky("Üks", 20_000), TextSource.NATIVE)])

    large = factories.MatterFactory(owner=specialist, reference_year=2099, reference_number=929)
    for index in range(15):
        version = capture_evidence(
            large, corpus.text_pdf(["placeholder"]), f"lisa-{index:02d}.pdf", PDF
        )
        _publish_text(version, [(_bulky(f"Lisa {index}", 20_000), TextSource.NATIVE)])

    with CaptureQueriesContext(connection) as few:
        build_analysis_input(small, specialist)
    with CaptureQueriesContext(connection) as many:
        build_analysis_input(large, specialist)
    assert len(many.captured_queries) == len(few.captured_queries)
    assert len(few.captured_queries) <= 3
