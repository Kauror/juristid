"""The vocabulary the opinion-archive reconciliation reasons in.

Every value here answers one question a future reader will ask about a
historical Submission: *why does Juristid believe this?* The signals and
conflicts are stored individually rather than collapsed into a score, because a
single number cannot be argued with and "0.87" is not an answer to
"which ministry did we actually write to" (Stage-2H brief 14, 46).
"""

from __future__ import annotations

from django.db import models


class OpinionSourceKind(models.TextChoices):
    """Where an archive occurrence was read from."""

    OPINIONS_ARCHIVE = "OPINIONS_ARCHIVE", "Arvamuste arhiiv"


class OpinionMetadataSystem(models.TextChoices):
    """Whose interpretation a metadata row carries.

    There is exactly one value today and it is named for what it is. KodaDash
    metadata is a *derivative*: summaries, positions and topic labels produced
    for a public membership app. Storing it under a neutral name would let a
    later reader mistake it for something a lawyer typed (Stage-2H brief 9, 33).
    """

    KODADASH = "KODADASH", "KodaDash (tuletatud rikastus)"


class OpinionMatchClass(models.TextChoices):
    """How strong the case for one Matter is, as a class rather than a score.

    The order is the order the reconciliation tries them in, and only the two
    ``EXACT``/``STRICT`` classes may be applied without a person looking
    (Stage-2H brief 15).
    """

    EXACT_BINARY_MATTER = "EXACT_BINARY_MATTER", "Täpne bait ühel teemal"
    EXACT_BINARY_MULTI_MATTER = "EXACT_BINARY_MULTI_MATTER", "Täpne bait mitmel teemal"
    EXCEL_ONENOTE_EXACT = "EXCEL_ONENOTE_EXACT", "Exceli ja OneNote'i täpne ühilduvus"
    STRICT_MULTI_SIGNAL = "STRICT_MULTI_SIGNAL", "Mitu sõltumatut täpset signaali"
    #: Produced only by the second pass, from the letter's own text. It is
    #: deliberately absent from ``AUTOMATIC_MATCH_CLASSES``, and the reason is
    #: measurement rather than caution: extraction is blocked where the real
    #: archive lives, so nothing in this class has ever been produced from the
    #: real corpus. Promoting a class on unmeasured evidence is the move every
    #: other class here was written to avoid (docs/adr/0023).
    CONTENT_MULTI_SIGNAL = "CONTENT_MULTI_SIGNAL", "Sisust mitu täpset signaali"
    REVIEW_REQUIRED = "REVIEW_REQUIRED", "Vajab ülevaatust"
    CONFLICT = "CONFLICT", "Vastuolu"
    UNMATCHED = "UNMATCHED", "Sidumata"


#: The classes an apply may act on without a human decision. Everything else
#: waits in the queue. Kept as data rather than an ``in`` test spelled out in
#: four places, because the day this list grows is the day it must grow once.
AUTOMATIC_MATCH_CLASSES: frozenset[str] = frozenset(
    {
        OpinionMatchClass.EXACT_BINARY_MATTER,
        OpinionMatchClass.EXCEL_ONENOTE_EXACT,
        OpinionMatchClass.STRICT_MULTI_SIGNAL,
    }
)


class OpinionSignal(models.TextChoices):
    """One piece of evidence that this file belongs to this Matter."""

    EXACT_BINARY_ONENOTE = "EXACT_BINARY_ONENOTE", "Täpne bait OneNote'i lehel"
    EXACT_ONENOTE_PAGE = "EXACT_ONENOTE_PAGE", "Leht on registriga täpselt seotud"
    EXACT_KODADASH_SOURCE_FILE = "EXACT_KODADASH_SOURCE_FILE", "KodaDashi lähtefaili SHA-256"
    EXACT_SENT_DATE = "EXACT_SENT_DATE", "Sama väljasaatmise kuupäev"
    EXACT_RECIPIENT = "EXACT_RECIPIENT", "Sama adressaat"
    EXACT_LAW_REFERENCE = "EXACT_LAW_REFERENCE", "Sama õigusakti viide"
    EXACT_TITLE_TOKEN = "EXACT_TITLE_TOKEN", "Sama eristav pealkirjasõna"
    # Deliberately weaker than EXACT_SENT_DATE and never sufficient on its own:
    # the corpus puts the register's VÄLJA one day after the letter's own date
    # in 227 of 767 cases, which makes a one-day window a *suggestion*.
    SENT_DATE_WITHIN_ONE_DAY = "SENT_DATE_WITHIN_ONE_DAY", "Kuupäev erineb ühe päeva"
    RELATED_KODA_NEWS_SUPPORT = "RELATED_KODA_NEWS_SUPPORT", "KodaDashi uudiselink toetab"
    POLICY_THREAD_SUPPORT = "POLICY_THREAD_SUPPORT", "KodaDashi teemalõng toetab"
    # -- read from the letter itself ---------------------------------------
    #
    # The first pass sees a filename, a register row and a OneNote page. These
    # three come from the document's own text, which is a genuinely independent
    # source: a filename is what somebody typed when saving a copy, while the
    # letter's dateline is what Koda wrote. They are named separately from
    # their filename equivalents for exactly that reason — collapsing them
    # would let one source corroborate itself.
    CONTENT_EXACT_LAW_REFERENCE = "CONTENT_EXACT_LAW_REFERENCE", "Sisus sama õigusakti viide"
    CONTENT_EXACT_ADDRESSEE = "CONTENT_EXACT_ADDRESSEE", "Sisus sama adressaat"
    CONTENT_EXACT_DATE = "CONTENT_EXACT_DATE", "Sisus sama kuupäev"


class OpinionConflict(models.TextChoices):
    """Why the evidence does not agree with itself."""

    DATE_CONFLICT = "DATE_CONFLICT", "Kuupäevad ei klapi"
    RECIPIENT_CONFLICT = "RECIPIENT_CONFLICT", "Adressaadid ei klapi"
    TITLE_CONFLICT = "TITLE_CONFLICT", "Pealkirjad ei klapi"
    YEAR_CONFLICT = "YEAR_CONFLICT", "Aastad ei klapi"
    MULTIPLE_MATTER_BINARY = "MULTIPLE_MATTER_BINARY", "Sama bait mitmel teemal"
    MULTIPLE_SOURCE_ROWS = "MULTIPLE_SOURCE_ROWS", "Mitu registri rida sobib"
    UNKNOWN_RECIPIENT = "UNKNOWN_RECIPIENT", "Adressaat tundmatu"
    EXCEL_DIRECTION_NOT_COMPARABLE = (
        "EXCEL_DIRECTION_NOT_COMPARABLE",
        "Registri vastaspool on saatja, mitte adressaat",
    )
    EXISTING_SUBMISSION_DISAGREES = (
        "EXISTING_SUBMISSION_DISAGREES",
        "Olemasolev arvamus ütleb muud",
    )
    #: Several archive files land on one Matter on one day. Found in the real
    #: corpus: `2025_44` is a letter plus `Lisa 1`, and `2024_139` is a bundle
    #: of four earlier letters resent together. Those are one sent action with
    #: attachments, not two and four (brief 41 vs 68, 70).
    SAME_DAY_BUNDLE = "SAME_DAY_BUNDLE", "Mitu faili samal päeval samal teemal"


class SentDateBasis(models.TextChoices):
    """What a historical ``sent_at`` actually rests on.

    Ordered as the precedence in Stage-2H brief 19. Nothing derived from a file
    system, a ZIP header or a database timestamp appears here, and that absence
    is the point: those describe when somebody copied a file, not when Koda
    wrote to a ministry.
    """

    OUTGOING_EMAIL = "OUTGOING_EMAIL", "Väljuva kirja ajatempel"
    EXCEL_OUT_DATE = "EXCEL_OUT_DATE", "Registri VÄLJA kuupäev"
    OPINION_DOCUMENT_DATE = "OPINION_DOCUMENT_DATE", "Dokumendi enda kuupäev"
    REVIEWED_DECISION = "REVIEWED_DECISION", "Ülevaatusel otsustatud"


class RecipientBasis(models.TextChoices):
    """What a historical recipient rests on (Stage-2H brief 21)."""

    OUTGOING_EMAIL = "OUTGOING_EMAIL", "Väljuva kirja To/CC"
    OPINION_DOCUMENT = "OPINION_DOCUMENT", "Arvamuse enda adressaat"
    KODADASH_RAW = "KODADASH_RAW", "KodaDashi töötlemata saaja"
    EXCEL_ADDRESSEE = "EXCEL_ADDRESSEE", "Registri KELLELE"
    REVIEWED_MAPPING = "REVIEWED_MAPPING", "Ülevaatusel kinnitatud vastendus"
    UNRESOLVED = "UNRESOLVED", "Lahendamata"


class ArchiveTextState(models.TextChoices):
    """What happened when a parser was pointed at an archive binary.

    The distinction between the last three is the one that matters. A scanned
    letter with no text layer, a file the safety policy declined to open, and a
    parser that broke are three different facts about the corpus, and a reader
    who sees only "no text" will read all three as the same gap in coverage.
    """

    PENDING = "PENDING", "Ootel"
    DONE = "DONE", "Tekst olemas"
    NO_TEXT_LAYER = "NO_TEXT_LAYER", "Tekstikihti ei ole"
    BLOCKED = "BLOCKED", "Turvapoliitika ei luba eraldamist"
    FAILED = "FAILED", "Eraldamine ebaõnnestus"


class ArchiveLinkBasis(models.TextChoices):
    """How an archive-to-Matter relationship came to be believed.

    A reviewed link and a link the reconciliation derived from exact evidence
    are both legitimate and are not the same thing, and the archive detail page
    says which it is looking at.
    """

    REVIEWED = "REVIEWED", "Ülevaatusel kinnitatud"
    EXACT_BINARY = "EXACT_BINARY", "Täpne bait teema juures"
    APPLIED_SUBMISSION = "APPLIED_SUBMISSION", "Kanoonilise arvamuse kaudu"


class OpinionCandidateState(models.TextChoices):
    PENDING = "PENDING", "Ootel"
    APPLIED = "APPLIED", "Rakendatud"
    LINKED = "LINKED", "Seotud teemaga"
    REJECTED = "REJECTED", "Tagasi lükatud"
    DUPLICATE = "DUPLICATE", "Duplikaat"
    NOT_AN_OPINION = "NOT_AN_OPINION", "Ei ole arvamus"
    DEFERRED = "DEFERRED", "Edasi lükatud"
    #: An automatic proposal that newer reconciliation evidence replaced.
    #:
    #: Stage 2H.1 found the gap this fills. A candidate's identity includes its
    #: match class, so when fresh evidence reclassifies the same occurrence the
    #: old row is stranded: it cannot honestly be APPLIED, because it produced
    #: nothing; it must not be REJECTED, because no person rejected it; and it
    #: must not be deleted, because it is the record of what the reconciliation
    #: believed at the time. It is superseded, and it says so.
    SUPERSEDED = "SUPERSEDED", "Asendatud uuema tõendiga"


#: The states only a person ever puts a row into. ``opinion_decide`` is the sole
#: writer of every one of them; ``PENDING`` and ``APPLIED`` are the importer's
#: own bookkeeping and nobody else's. That split is what lets an automatic rerun
#: recognise a decision without asking who made it.
#:
#: An occurrence carrying one of these is not eligible for automatic
#: application. A reviewer who rejected a file, called it a duplicate, said it
#: is not an opinion, deferred it, or linked it to a Matter *without* asserting
#: it was sent has answered the question the automatic path was about to answer
#: again — and answering it again from the same evidence that was already on the
#: screen would make the queue a suggestion box (Stage-2H brief 25, 63).
#:
#: ``LINKED`` appears here even though it is the one state that may still
#: produce a Submission: that path runs through ``_plan_reviewed_submissions``,
#: which requires ``review_approves_submission`` and files the result as a
#: reviewed decision rather than a register value.
HUMAN_DECIDED_STATES: frozenset[str] = frozenset(
    {
        OpinionCandidateState.LINKED,
        OpinionCandidateState.REJECTED,
        OpinionCandidateState.DUPLICATE,
        OpinionCandidateState.NOT_AN_OPINION,
        OpinionCandidateState.DEFERRED,
    }
)


#: The only state an automatic run may move a candidate out of.
#:
#: Everything a person decided is untouchable, and so is APPLIED — a candidate
#: that produced a canonical Submission describes something that happened, and
#: newer evidence about the archive does not un-happen it. New evidence that
#: contradicts either surfaces as a conflict for a reviewer; it never rewrites
#: the answer already on the record (brief 35).
SUPERSEDABLE_STATES: frozenset[str] = frozenset({OpinionCandidateState.PENDING})


#: States a review decision may not move a candidate out of.
#:
#: Narrower than "finished", and the two members are here for different
#: reasons. ``APPLIED`` means a canonical Submission exists and names this row
#: as its justification, so re-deciding it would leave the register pointing at
#: an explanation that now says something else. ``SUPERSEDED`` is the record of
#: what the reconciliation believed before newer evidence replaced it; a
#: decision written over it destroys the history the state was introduced to
#: keep (docs/adr/0023).
#:
#: The five human states are deliberately absent. A reviewer correcting their
#: own earlier answer is the queue working, not a regression.
IRREVERSIBLE_CANDIDATE_STATES: frozenset[str] = frozenset(
    {
        OpinionCandidateState.APPLIED,
        OpinionCandidateState.SUPERSEDED,
    }
)
