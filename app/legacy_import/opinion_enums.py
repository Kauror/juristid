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


class OpinionCandidateState(models.TextChoices):
    PENDING = "PENDING", "Ootel"
    APPLIED = "APPLIED", "Rakendatud"
    LINKED = "LINKED", "Seotud teemaga"
    REJECTED = "REJECTED", "Tagasi lükatud"
    DUPLICATE = "DUPLICATE", "Duplikaat"
    NOT_AN_OPINION = "NOT_AN_OPINION", "Ei ole arvamus"
    DEFERRED = "DEFERRED", "Edasi lükatud"
