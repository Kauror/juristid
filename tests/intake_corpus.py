"""The evaluation corpus: ten envelopes whose right answers are written down.

Every document here is invented. No member material, no real correspondence, no
real official's name or address — the ministry is «Näidisministeerium», the
official is «Mari Näidis», and every address ends in `.invalid`, which is the
reserved TLD that cannot resolve. The shapes are real; the content is not
(assisted-intake brief §28).

**What each case is for** is written on it, because a corpus whose cases nobody
can distinguish stops being maintained. Between them they cover the ten shapes
the brief asks for: a ministry covering letter, a draft act, an explanatory
memorandum, an email with an attachment, several dates in one document, several
organisations in one document, two documents that disagree, a long annex, an
unreadable file, and an envelope whose parts agree with each other.

**The corpus is text, not bytes.** What it scores is the analysis — the
precedence, the confidence contract and the conflict rules that decide what
lands in a form control — and those read `AnalysisInput`, which is exactly what
this file builds. Whether a PDF or a DOCX *produces* that input is a different
question with its own tests (`tests/test_extraction_parsers.py` for the parsers,
`tests/test_intake_reader.py` for a real staged file going through a real
parser end to end). Keeping the two apart is what lets this file hold thirty
documents and still run in under a second.
"""

from __future__ import annotations

from app.documents.enums import DocumentRole, ExtractionState, LocatorKind
from app.matters.intake_suggestions.evaluation import Expectation
from app.matters.intake_suggestions.input import AnalysisInput, SourceDocument, TextBlock
from app.workflow.enums import Track

# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


def page(
    text: str, filename: str, *, role: str = DocumentRole.INCOMING_AUTHORITY
) -> SourceDocument:
    """One document of one page, the way the PDF parser stores it."""
    return SourceDocument(
        document_id=filename,
        version_id=f"{filename}-v1",
        filename=filename,
        role=role,
        extraction_state=ExtractionState.DONE,
        blocks=(TextBlock(ordinal=1, text=text, locator_label="lk 1"),),
    )


def message(
    *,
    subject: str,
    from_name: str,
    from_email: str,
    body: str,
    sent_at: str = "2026-09-05T09:14:00",
) -> SourceDocument:
    """A message, with its headers as the mail parser publishes them.

    The header summary is a block of its own and is flagged `is_email_header`,
    exactly as `email_common.py` writes it — so `prose_blocks` excludes it and
    `emphasis_text` includes the subject, which is the arrangement the analyser
    is written against.
    """
    metadata = {
        "subject": subject,
        "from_name": from_name,
        "from_email": from_email,
        "sent_at": sent_at,
    }
    header = f"Saatja: {from_name} <{from_email}>\nTeema: {subject}\nSaadetud: {sent_at}"
    return SourceDocument(
        document_id="kiri.eml",
        version_id="kiri.eml-v1",
        filename="kiri.eml",
        role=DocumentRole.ORIGINAL_EMAIL,
        extraction_state=ExtractionState.DONE,
        blocks=(
            TextBlock(
                ordinal=1,
                text=header,
                locator_label="kirja päis",
                is_email_header=True,
            ),
            TextBlock(ordinal=2, text=body, locator_label="kirja sisu"),
        ),
        email=metadata,
    )


def unreadable(filename: str = "katki.pdf") -> SourceDocument:
    """A file the parser could not open. Present in the envelope, contributing
    nothing, and not allowed to stop anything."""
    return SourceDocument(
        document_id=filename,
        version_id=f"{filename}-v1",
        filename=filename,
        role=DocumentRole.INCOMING_AUTHORITY,
        extraction_state=ExtractionState.FAILED,
        extraction_note="Fail ei ole loetav PDF.",
    )


# ---------------------------------------------------------------------------
# The documents
# ---------------------------------------------------------------------------

MINISTRY_LETTER = """Näidisministeerium
Suur-Ameerika 1, 10122 Tallinn

Eesti Kaubandus-Tööstuskoda
Teie 04.09.2026 nr 1-7/26/321
Meie 05.09.2026 nr 1-4/26/1234-2

Pakendiseaduse muutmise seaduse eelnõu kooskõlastamiseks

Lugupeetud Koja esindajad

Saadame Teile kooskõlastamiseks pakendiseaduse muutmise seaduse eelnõu.
Eelnõuga muudetakse pakendite ja jäätmete käitlemise korda ning keskkonnatasu
määrasid. Palume esitada arvamus hiljemalt 18. septembriks 2026.
Seadus jõustub 1. jaanuaril 2027.

Lugupidamisega
Mari Näidis
nõunik
E-post: mari.naidis@naidisministeerium.invalid
"""

DRAFT_ACT = """Pakendiseaduse muutmise seaduse eelnõu

§ 1. Pakendiseaduse muutmine

Pakendiseaduses tehakse järgmised muudatused:

1) paragrahvi 8 lõige 2 sõnastatakse järgmiselt: pakendiettevõtja tagab
   pakendijäätmete kogumise ja taaskasutamise vastavalt keskkonnatasu määrale;
2) paragrahvi 21 täiendatakse lõikega 4 ringmajanduse nõuete kohta.

§ 2. Seaduse jõustumine

Käesolev seadus jõustub 1. jaanuaril 2027.
"""

EXPLANATORY_MEMORANDUM = """Seletuskiri pakendiseaduse muutmise seaduse eelnõu juurde

1. Sissejuhatus

Eelnõu eesmärk on korrastada pakendijäätmete käitlemist ja keskkonnatasu
arvestamist. Eelnõu on koostanud Näidisministeeriumi keskkonnaosakond.

2. Eelnõu sisu

Muudatused puudutavad pakendiettevõtjate kohustusi ringmajanduses.
Kooskõlastusringil palume esitada arvamus hiljemalt 18. septembriks 2026.

3. Seaduse jõustumine

Seadus jõustub 1. jaanuaril 2027.
"""

#: A hundred-page comparison annex, in the one paragraph that matters. Its
#: vocabulary is *procurement*, which is nothing to do with what the letter is
#: about — precisely the shape that used to outscore a covering letter, because
#: the scoring counted words and not where they were (brief §16).
PROCUREMENT_ANNEX = """Lisa 2. Võrdlustabel

Käesolev tabel võrdleb kehtivat ja kavandatavat regulatsiooni ning nimetab
seosed teiste õigusaktidega.
""" + (
    """
Riigihangete seadus. Hankija peab hankemenetluses arvestama pakkuja
kvalifikatsiooniga. Hankeleping sõlmitakse riigihangete seaduses sätestatud
korras. Vaidlustuskomisjon lahendab riigihanke vaidlusi. Riigihange
korraldatakse elektroonselt.
"""
    * 12
)

#: Several dates, one of which is the answer. A dateline, a date the Chamber's
#: own earlier letter carries, an entry-into-force day, a historical reference,
#: and one response deadline in the words that make one.
MANY_DATES = """Näidisministeerium

Eesti Kaubandus-Tööstuskoda
Teie 04.09.2026 nr 1-7/26/321

12.09.2026

Energiamajanduse korralduse seaduse muutmine

Viitame 14.03.2019 kehtestatud korrale ja teie 04.09.2026 kirjale.
Palume esitada arvamus hiljemalt 02.10.2026.
Muudatused jõustuvad 01.07.2027.
Ülevaade avaldatakse 20.11.2026 toimuval seminaril.
"""

#: Two organisations in one letter: one sends it, the other is mentioned as
#: having already approved. Only the first is the Saatja.
TWO_ORGANISATIONS = """Näidisministeerium
Suur-Ameerika 1, Tallinn

Eesti Kaubandus-Tööstuskoda

Jäätmeseaduse muutmise seaduse eelnõu

Saadame kooskõlastamiseks jäätmeseaduse muutmise seaduse eelnõu.
Näidisamet on eelnõu juba kooskõlastanud ja Näidisamet esitas oma märkused
eraldi. Palume esitada arvamus hiljemalt 18. septembriks 2026.

Lugupidamisega
Mari Näidis
"""

#: The same envelope, disagreeing about the one date that matters.
SECOND_LETTER = """Näidisministeerium

Eesti Kaubandus-Tööstuskoda

Pakendiseaduse muutmise seaduse eelnõu kooskõlastamiseks

Palume esitada arvamus hiljemalt 25. septembriks 2026.
"""

#: Two documents carrying different formal headings, both strong.
OTHER_HEADING = """Näidisministeerium

Eesti Kaubandus-Tööstuskoda

Jäätmeseaduse ja teiste seaduste muutmise seaduse eelnõu

Saadame kooskõlastamiseks jäätmeseaduse muutmise seaduse eelnõu.
"""

#: An EU initiative rather than a domestic bill. Track answers *process*, and
#: this is the case that says so: the words that decide it are «Euroopa
#: Komisjoni ettepanek» and a COM reference, not the word «direktiiv».
EU_INITIATIVE_LETTER = """Näidisministeerium

Eesti Kaubandus-Tööstuskoda

Euroopa Komisjoni ettepanek pakendite ja pakendijäätmete määruse kohta

Euroopa Komisjoni ettepanek COM(2026) 214 käsitleb pakendite ja
pakendijäätmete ringmajandust. Eesti seisukohtade kujundamiseks palume
esitada arvamus hiljemalt 30. septembriks 2026.
"""

#: A weak, informal covering note. Nothing here earns an autofill on any field,
#: and the corpus needs a case whose right answer is "fill nothing".
INFORMAL_NOTE = """Tere

Saadan teile tutvumiseks materjali, millest rääkisime. Andke palun teada, mida
arvate.

Mari
"""


# ---------------------------------------------------------------------------
# The cases
# ---------------------------------------------------------------------------

#: One case is one envelope: the documents `Uus teema` received together, and
#: what a lawyer would have entered from them.
CASES: tuple[tuple[Expectation, AnalysisInput], ...] = (
    (
        Expectation(
            name="ministeeriumi-kaaskiri",
            title="Pakendiseaduse muutmise seaduse eelnõu",
            sender="Näidisministeerium",
            deadline="2026-09-18",
            track=str(Track.DOMESTIC),
            policy_areas=("keskkond",),
            about=(
                "the ordinary case — one covering letter, everything stated once. The "
                "expected title is the instrument and not the errand: the heading reads "
                "«… eelnõu kooskõlastamiseks» and what a lawyer files is the eelnõu"
            ),
        ),
        AnalysisInput(documents=(page(MINISTRY_LETTER, "kaaskiri.pdf"),)),
    ),
    (
        Expectation(
            name="eelnou-ilma-kaaskirjata",
            title="Pakendiseaduse muutmise seaduse eelnõu",
            track=str(Track.DOMESTIC),
            policy_areas=("keskkond",),
            about="a draft act on its own: a heading and a subject, but no sender and no deadline",
        ),
        AnalysisInput(documents=(page(DRAFT_ACT, "eelnou.pdf"),)),
    ),
    (
        Expectation(
            name="seletuskiri",
            sender="Näidisministeerium",
            deadline=None,
            track=str(Track.DOMESTIC),
            policy_areas=("keskkond",),
            about=(
                "an explanatory memorandum quotes the letter's deadline at best, so it may "
                "not fill Arvamuse tähtaeg on its own — but «eelnõu on koostanud X» does "
                "name the sender, and the subject is plainly stated"
            ),
        ),
        AnalysisInput(documents=(page(EXPLANATORY_MEMORANDUM, "seletuskiri.pdf"),)),
    ),
    (
        Expectation(
            name="kiri-ja-manus",
            title="Pakendiseaduse muutmise seaduse eelnõu",
            sender="Näidisministeerium",
            deadline="2026-09-18",
            track=str(Track.DOMESTIC),
            policy_areas=("keskkond",),
            about="an email and its attachment: the headers name the sender, the letter the rest",
        ),
        AnalysisInput(
            documents=(
                message(
                    subject="Pakendiseaduse muutmise seaduse eelnõu kooskõlastamiseks",
                    from_name="Mari Näidis (Näidisministeerium)",
                    from_email="mari.naidis@naidisministeerium.invalid",
                    body="Tere\n\nSaadame kooskõlastamiseks eelnõu. Vastust ootame kirjas "
                    "nimetatud tähtajaks.",
                ),
                page(MINISTRY_LETTER, "kaaskiri.pdf"),
            )
        ),
    ),
    (
        Expectation(
            name="mitu-kuupaeva",
            title="Energiamajanduse korralduse seaduse muutmine",
            sender="Näidisministeerium",
            deadline="2026-10-02",
            track=str(Track.DOMESTIC),
            policy_areas=("energeetika",),
            about=(
                "five dates in one letter: a dateline, the Chamber's own reference, a "
                "historical date, entry into force, and one real deadline"
            ),
        ),
        AnalysisInput(documents=(page(MANY_DATES, "kaaskiri.pdf"),)),
    ),
    (
        Expectation(
            name="kaks-asutust",
            title="Jäätmeseaduse muutmise seaduse eelnõu",
            sender="Näidisministeerium",
            deadline="2026-09-18",
            track=str(Track.DOMESTIC),
            policy_areas=("keskkond",),
            about="one organisation sends the letter, another is merely named in it",
        ),
        AnalysisInput(documents=(page(TWO_ORGANISATIONS, "kaaskiri.pdf"),)),
    ),
    (
        Expectation(
            name="vastuolulised-tahtajad",
            title="Pakendiseaduse muutmise seaduse eelnõu",
            sender="Näidisministeerium",
            deadline=None,
            track=str(Track.DOMESTIC),
            policy_areas=("keskkond",),
            about="two letters, two explicit deadlines: a conflict fills nothing",
        ),
        AnalysisInput(
            documents=(
                page(MINISTRY_LETTER, "kaaskiri.pdf"),
                page(SECOND_LETTER, "teine-kiri.pdf"),
            )
        ),
    ),
    (
        Expectation(
            name="vastuolulised-pealkirjad",
            title=None,
            sender="Näidisministeerium",
            deadline="2026-09-18",
            track=str(Track.DOMESTIC),
            policy_areas=("keskkond",),
            about=(
                "two documents carrying different formal headings: neither may fill "
                "Pealkiri, and the rest of the envelope is unaffected by that"
            ),
        ),
        AnalysisInput(
            documents=(
                page(MINISTRY_LETTER, "kaaskiri.pdf"),
                page(OTHER_HEADING, "teine-eelnou.pdf"),
            )
        ),
    ),
    (
        Expectation(
            name="pikk-lisa",
            title="Pakendiseaduse muutmise seaduse eelnõu",
            sender="Näidisministeerium",
            deadline="2026-09-18",
            track=str(Track.DOMESTIC),
            policy_areas=("keskkond",),
            about=(
                "a covering letter about the environment beside a long procurement annex: "
                "the annex must not decide the Valdkond"
            ),
        ),
        AnalysisInput(
            documents=(
                page(MINISTRY_LETTER, "kaaskiri.pdf"),
                page(PROCUREMENT_ANNEX, "Lisa_2_vordlustabel.pdf"),
            )
        ),
    ),
    (
        Expectation(
            name="eli-algatus",
            title="Euroopa Komisjoni ettepanek pakendite ja pakendijäätmete määruse kohta",
            sender="Näidisministeerium",
            deadline="2026-09-30",
            track=str(Track.EU_INITIATIVE),
            policy_areas=("keskkond",),
            about="Menetlusliik answers process, not instrument: a COM reference decides it",
        ),
        AnalysisInput(documents=(page(EU_INITIATIVE_LETTER, "kaaskiri.pdf"),)),
    ),
    (
        Expectation(
            name="loetamatu-fail",
            about="a file nothing could read, and an envelope that still behaves",
        ),
        AnalysisInput(documents=(unreadable(),)),
    ),
    (
        Expectation(
            name="mitteametlik-kiri",
            about="nothing here earns an autofill, and the right answer is an empty form",
        ),
        AnalysisInput(documents=(page(INFORMAL_NOTE, "markus.pdf"),)),
    ),
)

#: Locators used by the corpus, asserted against the enum so a rename here
#: cannot silently stop matching what the parsers write.
assert LocatorKind.PAGE
