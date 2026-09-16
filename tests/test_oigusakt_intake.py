"""Õigusakt, read from the documents `Uus teema` was given.

The rules are in `app/matters/intake_suggestions`, the record is docs/adr/0080,
and the property this file exists to hold is the one sentence that makes the
feature different from the two vocabulary fields beside it:

    An instrument is what **one document** is, so it is read from one
    document's head and never pooled across an envelope.

Everything else follows. A comparison table naming a directive in every row is
describing itself; a covering letter listing «1. eelnõu, 2. seletuskiri» is
describing what was stapled to it; an annex is real evidence that may never
fill a control on its own. Each of those is a test below, in that order.

Two halves, the same split `tests/test_assisted_intake.py` uses. The first runs
the pure analysis over invented text and touches no database. The second proves
the contract around it on the real surface — the staging routes, the real
parser stack through `parse_source`, the real form control — because a rule
that is right in a unit test and unreachable from the form is not a feature.

Every document here is invented and every PDF is written at test time. Nothing
in this file is a checked-in binary and nothing came from a real envelope.
"""

from __future__ import annotations

import pathlib

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from app.documents.enums import DocumentRole, ExtractionState
from app.matters import intake_extraction
from app.matters.intake_suggestions import (
    Confidence,
    CurrentValues,
    SuggestedField,
    analyse,
    analyse_intake,
    analyse_matter,
    prefill_controls,
    prefill_initial,
)
from app.matters.intake_suggestions import vocabulary as vocab
from app.matters.intake_suggestions.input import AnalysisInput, SourceDocument, TextBlock
from app.matters.intake_suggestions.resolvers import (
    OrganisationCatalogue,
    load_legal_instrument_types,
)
from app.matters.models import Matter
from app.matters.services import set_legal_instruments
from app.taxonomy.legal_instruments import (
    OTHER_LEGAL_INSTRUMENT_KEY,
    REFERENCE_LEGAL_INSTRUMENT_KEYS,
)
from app.taxonomy.models import LegalInstrumentType
from tests import factories
from tests import synthetic_corpus as corpus

PDF = "application/pdf"
MSG = "application/vnd.ms-outlook"

# ---------------------------------------------------------------------------
# Invented material
# ---------------------------------------------------------------------------

#: A law draft, as one arrives: the act's own heading on its own line, then the
#: body of the draft.
LAW_DRAFT = """Näidisministeerium

Pakendiseaduse muutmise seaduse eelnõu

1. peatükk
Üldsätted

§ 1. Pakendiseaduse muutmine
Pakendiseaduses tehakse järgmised muudatused.
"""

#: The comparison table that travels with it. Every row names the directive
#: being transposed, which is what a comparison table is for — and says nothing
#: about what was submitted. Its filename and its first line both mark it as an
#: annex (`vocabulary.ANNEX_MARKERS`).
COMPARISON_TABLE = """Võrdlustabel
Euroopa Parlamendi ja nõukogu direktiiv (EL) 2024/825
Direktiiv (EL) 2024/825 artikkel 1 – eelnõu § 1. Artikkel 2 – eelnõu § 2.
Direktiivi ülevõtmine on täielik. Direktiiv 2024/825 ei nõua enamat.
Direktiivi artiklid 4-9 ei vaja ülevõtmist. Direktiiv on üle võetud.
Direktiiv, direktiiv, direktiiv, direktiiv, direktiiv, direktiiv.
"""

#: A ministerial regulation draft.
REGULATION_DRAFT = """Näidisministeerium

Vabariigi Valitsuse määruse eelnõu

§ 1. Määruse reguleerimisala
Määrusega kehtestatakse pakendite käitlemise kord.
"""

#: A development plan, headed as one.
ARENGUKAVA = """Näidisministeerium

Energiamajanduse arengukava 2035

Arengukava seab pikaajalised sihid ja kirjeldab nende saavutamise teed.
"""

#: An action plan, headed as one.
TEGEVUSKAVA = """Näidisministeerium

Ettevõtluskeskkonna tegevuskava 2027-2030

Tegevuskava koondab kokkulepitud sammud ja nende tähtajad.
"""

#: The misleading shape: a covering letter whose own head says nothing about
#: what it is, and whose list of enclosures names two instruments it is not.
MISLEADING_COVER = """Näidisministeerium
Suur-Ameerika 1, Tallinn

Eesti Kaubandus-Tööstuskoda

Lugupeetud Koja esindajad

Saadame Teile tutvumiseks materjalid ja palume tagasisidet hiljemalt 20.10.2026.

Lisad:
1. Pakendiseaduse muutmise seaduse eelnõu
2. Vabariigi Valitsuse määruse eelnõu
3. Seletuskiri

Lugupidamisega
Mari Näidis
"""

#: An annexed regulation draft, with the annex's own title page.
ANNEXED_REGULATION = """Lisa 2. Määruse kavand

§ 1. Määruse reguleerimisala
Määrusega kehtestatakse tagatisraha määrad.
"""

#: ADR 0070's worked example, as a heading: the directive is what is being
#: transposed and the Act is what was submitted.
TRANSPOSING_DRAFT = """Näidisministeerium

Direktiivi (EL) 2024/825 ülevõtmise seaduse eelnõu

§ 1. Tarbijakaitseseaduse muutmine
"""

#: A draft that says it is a draft and never says of what.
UNTYPED_DRAFT = """Näidisministeerium

Pakendiaruandluse eelnõu

Eelnõuga korrastatakse aruandluse korda.
"""


# ---------------------------------------------------------------------------
# The pure half: text in, candidates out, no database
# ---------------------------------------------------------------------------


class _Instrument:
    """A `LegalInstrumentType` stand-in: key, label, pk."""

    def __init__(self, key: str, label_et: str, pk: int) -> None:
        self.key, self.label_et, self.pk = key, label_et, pk


#: The real seventeen keys, so a rule keyed on something the vocabulary does
#: not hold fails here rather than in production.
INSTRUMENTS = {
    key: _Instrument(key, key.title(), index)
    for index, key in enumerate(REFERENCE_LEGAL_INSTRUMENT_KEYS, start=1)
}


def document(
    text: str,
    filename: str = "dokument.pdf",
    role: str = DocumentRole.INCOMING_AUTHORITY,
    email: dict | None = None,
) -> SourceDocument:
    return SourceDocument(
        document_id=filename,
        version_id=f"{filename}-v1",
        filename=filename,
        role=str(role),
        extraction_state=str(ExtractionState.DONE),
        blocks=(TextBlock(ordinal=1, text=text, locator_label="lk 1"),),
        email=email,
    )


def message(
    subject: str, body: str = "Tere\n\nSaadame materjalid.", **meta: object
) -> SourceDocument:
    metadata = {"subject": subject, "from_name": "Kadri Näidis", **meta}
    return SourceDocument(
        document_id="kiri.msg",
        version_id="kiri.msg-v1",
        filename="kiri.msg",
        role=DocumentRole.ORIGINAL_EMAIL,
        extraction_state=ExtractionState.DONE,
        blocks=(
            TextBlock(1, f"{subject}\nSaatja: Kadri Näidis", "kirja päis", is_email_header=True),
            TextBlock(2, body, "kirja sisu"),
        ),
        email=metadata,
    )


def run(*documents: SourceDocument, current: CurrentValues | None = None):
    return analyse(
        AnalysisInput(documents=tuple(documents)),
        organisations=OrganisationCatalogue.empty(),
        policy_areas={},
        legal_instruments=INSTRUMENTS,  # type: ignore[arg-type]
        current=current or CurrentValues(),
    )


def offered(analysis) -> list[tuple[str, str]]:
    """``(confidence, key)`` for every Õigusakt candidate, strongest first."""
    suggestions = analysis.fields.get(SuggestedField.LEGAL_INSTRUMENTS)
    if suggestions is None:
        return []
    by_pk = {str(item.pk): key for key, item in INSTRUMENTS.items()}
    return [(c.confidence, by_pk[c.value]) for c in suggestions.candidates]


def test_a_law_draft_is_a_seadus_and_the_table_beside_it_is_not_a_direktiiv() -> None:
    """§1. The evidence model, stated as the case it was written for.

    The draft says once, in its heading, what it is. The comparison table says
    «direktiiv» eight times in its body, because naming every article it
    transposes is the entire purpose of a comparison table. Pooled scoring —
    which is right for Valdkond and for Menetlusliik — would hand the envelope
    to the annex (docs/adr/0080 §1, docs/adr/0070's ELi õiguse ülevõtmine
    worked example).
    """
    analysis = run(
        document(LAW_DRAFT, "eelnou.pdf"),
        document(COMPARISON_TABLE, "vordlustabel.pdf"),
    )
    assert offered(analysis) == [(Confidence.HIGH, "seadus")]


def test_a_regulation_draft_is_a_maarus() -> None:
    analysis = run(document(REGULATION_DRAFT, "maaruse-eelnou.pdf"))
    assert offered(analysis) == [(Confidence.HIGH, "maarus")]


def test_a_document_headed_as_an_arengukava_is_an_arengukava() -> None:
    """§2. `Arengukava` is a positive answer, not a negative control."""
    analysis = run(document(ARENGUKAVA, "arengukava.pdf"))
    assert offered(analysis) == [(Confidence.HIGH, "arengukava")]


def test_a_document_headed_as_a_tegevuskava_is_a_tegevuskava() -> None:
    analysis = run(document(TEGEVUSKAVA, "tegevuskava.pdf"))
    assert offered(analysis) == [(Confidence.HIGH, "tegevuskava")]


def test_a_covering_letter_that_merely_lists_its_enclosures_suggests_nothing() -> None:
    """§3. A letter is not the thing it encloses.

    Every instrument word in this document sits in a list of attachments. The
    head reader never reaches it: a list entry has a neighbour above it, and a
    line ending in a colon is a label.
    """
    analysis = run(document(MISLEADING_COVER, "kaaskiri.pdf"))
    assert offered(analysis) == []


def test_an_annex_on_its_own_can_offer_but_never_fill() -> None:
    """§4. `ANNEX_ONLY_HIGH_MARGIN`, applied to this field for its own reason."""
    analysis = run(document(ANNEXED_REGULATION, "lisa-2-maaruse-kavand.pdf"))
    assert offered(analysis) == [(Confidence.MEDIUM, "maarus")]

    suggestions = analysis.fields[SuggestedField.LEGAL_INSTRUMENTS]
    assert suggestions.prefill_candidates == ()
    initial, _ = prefill_initial(analysis, base={}, current=CurrentValues())
    assert "legal_instruments" not in initial


def test_an_annexed_regulation_rides_beside_the_parent_law_draft() -> None:
    """§4, the other half. Both are evidenced, so both are offered — and only
    the document that speaks for the envelope may fill a control."""
    analysis = run(
        document(LAW_DRAFT, "eelnou.pdf"),
        document(ANNEXED_REGULATION, "lisa-2-maaruse-kavand.pdf"),
    )
    assert offered(analysis) == [(Confidence.HIGH, "seadus"), (Confidence.MEDIUM, "maarus")]


def test_a_transposed_directive_is_background_and_not_the_submitted_instrument() -> None:
    """§5. ADR 0070's worked example, in one heading."""
    analysis = run(document(TRANSPOSING_DRAFT, "eelnou.pdf"))
    assert offered(analysis) == [(Confidence.HIGH, "seadus")]


def test_a_known_kind_never_carries_the_generic_eelnou_beside_it() -> None:
    """§6. «Seaduse eelnõu» answers both questions at once."""
    analysis = run(document(LAW_DRAFT, "eelnou.pdf"))
    assert [key for _, key in offered(analysis)] == ["seadus"]


def test_a_draft_whose_kind_is_not_named_is_the_generic_eelnou() -> None:
    """§6, the case the generic value exists for."""
    analysis = run(document(UNTYPED_DRAFT, "eelnou.pdf"))
    assert offered(analysis) == [(Confidence.HIGH, "eelnou")]


def test_muu_is_never_suggested_by_any_rule() -> None:
    """§7. A machine suggestion must never produce a form that cannot be saved.

    Ticking `Muu` makes `Õigusakti liik` required (`clean_legal_instrument_answer`),
    and there is nothing honest for a rule to write in it. So there is no rule
    keyed on `Muu`, and the guard that says so is data rather than a comment.
    """
    assert OTHER_LEGAL_INSTRUMENT_KEY in vocab.INSTRUMENT_NEVER_INFERRED
    assert OTHER_LEGAL_INSTRUMENT_KEY not in vocab.INSTRUMENT_RULES
    assert vocab.INSTRUMENT_DRAFT_KEY != OTHER_LEGAL_INSTRUMENT_KEY

    for text in (
        LAW_DRAFT,
        REGULATION_DRAFT,
        ARENGUKAVA,
        TEGEVUSKAVA,
        MISLEADING_COVER,
        ANNEXED_REGULATION,
        TRANSPOSING_DRAFT,
        UNTYPED_DRAFT,
        COMPARISON_TABLE,
        "Näidisministeerium\n\nMingi muu dokument\n\nSisu.",
    ):
        assert OTHER_LEGAL_INSTRUMENT_KEY not in [key for _, key in offered(run(document(text)))]


def test_two_documents_of_equal_standing_disagreeing_fill_nothing() -> None:
    """§8. The conflict rule, unchanged from the one the other fields use."""
    analysis = run(
        document(LAW_DRAFT, "seaduse-eelnou.pdf"),
        document(REGULATION_DRAFT, "maaruse-eelnou.pdf"),
    )
    suggestions = analysis.fields[SuggestedField.LEGAL_INSTRUMENTS]

    assert suggestions.conflict
    assert suggestions.note
    assert sorted(key for _, key in offered(analysis)) == ["maarus", "seadus"]
    assert all(c.confidence == Confidence.MEDIUM for c in suggestions.candidates)
    assert suggestions.prefill_candidates == ()

    initial, decided = prefill_initial(analysis, base={}, current=CurrentValues())
    assert "legal_instruments" not in initial
    assert all(name != SuggestedField.LEGAL_INSTRUMENTS for name, _ in prefill_controls(decided))


def test_a_value_the_record_already_holds_is_never_pre_filled_over() -> None:
    """§9. Manual input wins, on the surface that has a record to ask."""
    seadus = INSTRUMENTS["seadus"]
    analysis = run(
        document(LAW_DRAFT, "eelnou.pdf"),
        current=CurrentValues(legal_instrument_ids=frozenset({seadus.pk})),
    )
    suggestions = analysis.fields[SuggestedField.LEGAL_INSTRUMENTS]
    assert [c.already_current for c in suggestions.candidates] == [True]
    # `already_current` keeps it off `high`, and the field-level rule refuses
    # anyway: a control that holds something is not an empty control.
    assert suggestions.prefill_candidates == ()

    initial, _ = prefill_initial(
        analysis,
        base={"legal_instruments": [seadus.pk]},
        current=CurrentValues(legal_instrument_ids=frozenset({seadus.pk})),
    )
    assert initial["legal_instruments"] == [seadus.pk]


def test_a_different_value_is_not_written_over_one_the_record_holds() -> None:
    """§9, the sharper half: the suggestion *disagrees* and still may not win."""
    maarus = INSTRUMENTS["maarus"]
    current = CurrentValues(legal_instrument_ids=frozenset({maarus.pk}))
    analysis = run(document(LAW_DRAFT, "eelnou.pdf"), current=current)

    initial, decided = prefill_initial(
        analysis, base={"legal_instruments": [maarus.pk]}, current=current
    )
    assert initial["legal_instruments"] == [maarus.pk]
    assert SuggestedField.LEGAL_INSTRUMENTS not in decided.prefilled
    # And the person is still offered the alternative, with its evidence.
    assert [key for _, key in offered(analysis)] == ["seadus"]


def test_a_message_that_names_an_instrument_is_evidence_and_not_an_autofill() -> None:
    """§10. A subject line names something the reader has not opened."""
    analysis = run(message("VS: Pakendiseaduse muutmise seaduse eelnõu kooskõlastamiseks"))
    assert offered(analysis) == [(Confidence.MEDIUM, "seadus")]
    assert analysis.fields[SuggestedField.LEGAL_INSTRUMENTS].prefill_candidates == ()


def test_an_unopened_attachment_count_adds_no_instrument() -> None:
    """§10. `attachment_count` says how many files were *not* read.

    Staged `.msg` attachments stay opaque (docs/adr/0072), so a second
    instrument behind one of them is something the analyser has no evidence
    about — and a count is not evidence about a kind.
    """
    without = run(message("Pakendiaruandluse eelnõu", attachment_count=0))
    with_many = run(message("Pakendiaruandluse eelnõu", attachment_count=9))
    assert offered(without) == offered(with_many) == [(Confidence.MEDIUM, "eelnou")]


def test_the_evidence_under_every_candidate_is_the_head_line_it_was_read_from() -> None:
    """A suggestion a person cannot check is a suggestion they must trust."""
    analysis = run(document(LAW_DRAFT, "eelnou.pdf"))
    [candidate] = analysis.fields[SuggestedField.LEGAL_INSTRUMENTS].candidates
    assert candidate.evidence == "Pakendiseaduse muutmise seaduse eelnõu"
    assert candidate.provenance.filename == "eelnou.pdf"
    assert candidate.detail.startswith("Tunnused:")


def test_no_rule_is_keyed_on_an_instrument_the_vocabulary_does_not_hold() -> None:
    keys = {*vocab.INSTRUMENT_RULES, vocab.INSTRUMENT_DRAFT_KEY}
    assert keys <= set(REFERENCE_LEGAL_INSTRUMENT_KEYS)


def test_the_browser_island_may_fill_the_oigusakt_control() -> None:
    """The server decides; the browser applies, and only to an untouched box.

    The island refuses to write into any control it is not told about, so a
    field missing from `FILLABLE` would make every suggestion above unreachable
    on `Uus teema` without a single test failing (static/js/app.js,
    docs/adr/0064).
    """
    script = pathlib.Path("static/js/app.js").read_text(encoding="utf-8")
    fillable = script.split("var FILLABLE = [", 1)[1].split("]", 1)[0]
    assert '"legal_instruments"' in fillable


# ---------------------------------------------------------------------------
# The real surface: staged files, the real parser, the real control
# ---------------------------------------------------------------------------

CREATE = reverse("matters:matter_create")
STAGE = reverse("matters:intake_stage")
STATUS = reverse("matters:intake_status")

#: The four assertions below are about what `Uus teema` *shows*, and the
#: suggestion area is withdrawn from that page by default (docs/adr/0088). They
#: describe the capability switched on: what the reader finds in a law draft and
#: how the Õigusakt control receives it. Everything else in this module is about
#: the rules themselves and is unaffected either way.
READING_ON = override_settings(MATTER_INTAKE_SUGGESTIONS_ENABLED=True)


def stage(client, *files):
    """Upload through the real staging route, the way the browser does."""
    response = client.post(STAGE, {"files": list(files)})
    assert response.status_code in (200, 400), response.status_code
    session = response.context["intake_session"]
    assert session is not None
    return session


def upload(name: str, content: bytes, content_type: str = PDF) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type=content_type)


def instrument(key: str) -> LegalInstrumentType:
    return LegalInstrumentType.objects.get(key=key)


def suggested_keys(analysis) -> list[tuple[str, str]]:
    suggestions = analysis.fields.get(SuggestedField.LEGAL_INSTRUMENTS)
    if suggestions is None:
        return []
    by_pk = {str(item.pk): item.key for item in LegalInstrumentType.objects.all()}
    return [(c.confidence, by_pk[c.value]) for c in suggestions.candidates]


@pytest.mark.django_db
def test_the_vocabulary_the_reader_resolves_against_is_the_one_the_form_offers() -> None:
    loaded = load_legal_instrument_types()
    assert set(loaded) == set(REFERENCE_LEGAL_INSTRUMENT_KEYS)


@pytest.mark.django_db
@READING_ON
def test_a_staged_law_draft_pre_fills_the_oigusakt_control(signed_in, evidence_root) -> None:
    """The whole path, through `parse_source` and out at the browser contract.

    The thresholds this feature uses are set against the text the product's own
    parser produces, which is why this test goes through the real staging route
    rather than handing the analyser a string.
    """
    session = stage(
        signed_in,
        upload("eelnou.pdf", corpus.text_pdf([LAW_DRAFT])),
        upload("vordlustabel.pdf", corpus.text_pdf([COMPARISON_TABLE])),
    )
    intake_extraction.drain(limit=20)

    analysis = analyse_intake(session)
    assert suggested_keys(analysis) == [(Confidence.HIGH, "seadus")]

    answer = signed_in.get(f"{STATUS}?intake={session.pk}")
    proposed = dict(answer.context["intake_prefill"])
    assert proposed[SuggestedField.LEGAL_INSTRUMENTS] == str(instrument("seadus").pk)


@pytest.mark.django_db
@READING_ON
def test_a_medium_suggestion_is_offered_and_never_proposed(signed_in, evidence_root) -> None:
    """MEDIUM stays reviewable: it reaches the panel and not the pre-fill."""
    session = stage(
        signed_in,
        upload("lisa-2-maaruse-kavand.pdf", corpus.text_pdf([ANNEXED_REGULATION])),
    )
    intake_extraction.drain(limit=20)

    answer = signed_in.get(f"{STATUS}?intake={session.pk}")
    analysis = answer.context["assisted"]
    assert suggested_keys(analysis) == [(Confidence.MEDIUM, "maarus")]
    assert SuggestedField.LEGAL_INSTRUMENTS not in dict(answer.context["intake_prefill"])

    body = answer.content.decode()
    assert "Õigusakt" in body and "Määrus" in body


@pytest.mark.django_db
@READING_ON
def test_a_conflicting_envelope_proposes_nothing(signed_in, evidence_root) -> None:
    session = stage(
        signed_in,
        upload("seaduse-eelnou.pdf", corpus.text_pdf([LAW_DRAFT])),
        upload("maaruse-eelnou.pdf", corpus.text_pdf([REGULATION_DRAFT])),
    )
    intake_extraction.drain(limit=20)

    answer = signed_in.get(f"{STATUS}?intake={session.pk}")
    assert answer.context["assisted"].fields[SuggestedField.LEGAL_INSTRUMENTS].conflict
    assert SuggestedField.LEGAL_INSTRUMENTS not in dict(answer.context["intake_prefill"])


@pytest.mark.django_db
def test_a_readable_outlook_message_gives_medium_and_its_attachment_none(
    signed_in, evidence_root
) -> None:
    """docs/adr/0072, still true: a staged `.msg` is read, never unpacked.

    The synthetic message carries one PDF attachment whose text names a
    different thing entirely. The reader sees the subject and the body and
    never the attachment, so what it offers is what the subject said — at
    MEDIUM, because a subject names something nobody has opened.
    """
    session = stage(signed_in, upload("kiri.msg", corpus.outlook_msg(), MSG))
    intake_extraction.drain(limit=20)

    analysis = analyse_intake(session)
    assert suggested_keys(analysis) == [(Confidence.MEDIUM, "eelnou")]
    assert SuggestedField.LEGAL_INSTRUMENTS not in dict(
        prefill_controls(
            prefill_initial(analysis, base={}, current=CurrentValues(), allow_title=True)[1]
        )
    )


@pytest.mark.django_db
@READING_ON
def test_the_suggested_value_is_a_value_the_real_control_accepts(
    signed_in, evidence_root, specialist
) -> None:
    """The round trip. A suggestion nothing can be done with is not a feature.

    What the panel hands the browser is a control value; what the browser
    writes it into is the checkbox the create form renders; and what the save
    stores is `Matter.legal_instruments`, through the ordinary POST and the
    ordinary service. Every step is the existing one.
    """
    session = stage(signed_in, upload("eelnou.pdf", corpus.text_pdf([LAW_DRAFT])))
    intake_extraction.drain(limit=20)

    answer = signed_in.get(f"{STATUS}?intake={session.pk}")
    proposed = dict(answer.context["intake_prefill"])[SuggestedField.LEGAL_INSTRUMENTS]

    page = signed_in.get(CREATE).content.decode()
    assert f'name="legal_instruments" value="{proposed}"' in page.replace("\n", " ")

    created = signed_in.post(
        CREATE,
        {
            "title": "Pakendiseaduse muutmise seaduse eelnõu",
            "legal_instruments": [proposed],
            "intake": str(session.pk),
        },
    )
    assert created.status_code == 302, created.status_code
    matter = Matter.objects.get(title="Pakendiseaduse muutmise seaduse eelnõu")
    assert [item.key for item in matter.legal_instruments.all()] == ["seadus"]


@pytest.mark.django_db
def test_a_matter_that_already_answered_oigusakt_is_never_overwritten(
    specialist, evidence_root, capture_evidence, extract
) -> None:
    """The edit surface's own protection, end to end.

    `Muuda teemat` reads the record rather than the live form, so this is where
    «an existing value is never overwritten» is actually enforced server-side.
    """
    matter = factories.MatterFactory(owner=specialist, title="Ükskõik")
    set_legal_instruments(matter=matter, legal_instruments=[instrument("maarus")], actor=specialist)
    extract(capture_evidence(matter, corpus.text_pdf([LAW_DRAFT]), "eelnou.pdf", PDF))

    analysis = analyse_matter(matter, specialist)
    assert suggested_keys(analysis) == [(Confidence.HIGH, "seadus")]

    current = CurrentValues.of(matter)
    assert current.legal_instrument_ids == frozenset({instrument("maarus").pk})
    initial, decided = prefill_initial(
        analysis,
        base={"legal_instruments": [instrument("maarus").pk]},
        current=current,
    )
    assert initial["legal_instruments"] == [instrument("maarus").pk]
    assert SuggestedField.LEGAL_INSTRUMENTS not in decided.prefilled
    assert [item.key for item in matter.legal_instruments.all()] == ["maarus"]
