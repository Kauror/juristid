"""The reconciliation vocabulary.

Two separate ideas, kept separate on purpose.

``RowOutcome`` is what *happened* to one source row. Every relevant row gets
exactly one, so the outcomes partition the sheet and the totals can be checked
against the row count. That is what makes "no source row disappeared silently"
a testable claim rather than a hope (master specification 19.9).

``Anomaly`` is what was *noticed* about a row. A row can carry several, or
none, and most of them do not stop the row from becoming a Matter — an
unresolvable ministry name is a mapping gap, not a broken row. A few are severe
enough that the planner refuses to act on them, and those are listed in
:data:`BLOCKING_ANOMALIES`.

Collapsing the two would force a choice between "this row has an unmapped
owner" and "this row would be created", when both are true and a reviewer needs
to see both.
"""

from __future__ import annotations

from django.db import models


class RowOutcome(models.TextChoices):
    """What the importer would do with one source row. Exactly one per row.

    Two modes emit from this enum and they use disjoint parts of it. Offline
    inspection has no database, so it can never tell a creation from a match and
    says ``IMPORTABLE`` instead of guessing. The dry run and the apply path have
    a database and never say ``IMPORTABLE``. Within either mode the values still
    partition the sheet, which is what the totals check relies on.
    """

    IMPORTABLE = "IMPORTABLE", "Imporditav"
    WOULD_CREATE = "WOULD_CREATE", "Loob uue teema"
    WOULD_MATCH = "WOULD_MATCH", "Seob olemasoleva teemaga"
    ALREADY_IMPORTED = "ALREADY_IMPORTED", "Juba imporditud"
    REVIEW_REQUIRED = "REVIEW_REQUIRED", "Vajab ülevaatust"
    NON_MATTER_ROW = "NON_MATTER_ROW", "Ei ole teemarida"
    RESERVED_REFERENCE = "RESERVED_REFERENCE", "Broneeritud viitenumber"
    BLANK_PADDING = "BLANK_PADDING", "Tühi täiterida"
    ERROR = "ERROR", "Viga"


#: Outcomes for rows that carry no matter. Counted, never imported, never
#: silently dropped — and ``RESERVED_REFERENCE`` still feeds the reference
#: sequence, because a number the register has spoken for must not be handed
#: out again (Stage-2A brief 11).
NON_IMPORTING_OUTCOMES: frozenset[str] = frozenset(
    {
        RowOutcome.BLANK_PADDING.value,
        RowOutcome.NON_MATTER_ROW.value,
        RowOutcome.RESERVED_REFERENCE.value,
    }
)

#: Outcomes only the offline inspector emits, because it has no database.
OFFLINE_ONLY_OUTCOMES: frozenset[str] = frozenset({RowOutcome.IMPORTABLE.value})

#: Outcomes only a database-aware run emits.
DATABASE_ONLY_OUTCOMES: frozenset[str] = frozenset(
    {
        RowOutcome.WOULD_CREATE.value,
        RowOutcome.WOULD_MATCH.value,
        RowOutcome.ALREADY_IMPORTED.value,
    }
)

#: Outcomes that mean the row carries a policy matter the importer understands.
MATTER_OUTCOMES: frozenset[str] = frozenset(
    {
        RowOutcome.WOULD_CREATE.value,
        RowOutcome.WOULD_MATCH.value,
        RowOutcome.ALREADY_IMPORTED.value,
    }
)


class Anomaly(models.TextChoices):
    """Something worth a human's attention about one row.

    The values a reviewer acts on. Nothing here is repaired automatically; an
    anomaly is evidence about the source, and the source is authoritative
    (master specification 19.3).
    """

    INVALID_REFERENCE = "INVALID_REFERENCE", "Viitenumber ei ole loetav"
    REFERENCE_YEAR_MISMATCH = "REFERENCE_YEAR_MISMATCH", "Viite aasta ei klapi lehega"
    DUPLICATE_REFERENCE = "DUPLICATE_REFERENCE", "Korduv viitenumber"
    MISSING_TITLE = "MISSING_TITLE", "Pealkiri puudub"
    INVALID_DATE = "INVALID_DATE", "Kuupäeva ei õnnestunud lugeda"
    NEGATIVE_RESPONSE_INTERVAL = "NEGATIVE_RESPONSE_INTERVAL", "Tähtaeg on saabumisest varem"
    UNMAPPED_STATUS = "UNMAPPED_STATUS", "Hetkeseisu vastet ei ole"
    UNMAPPED_OWNER = "UNMAPPED_OWNER", "Vastutaja vastet ei ole"
    UNMAPPED_ORGANISATION = "UNMAPPED_ORGANISATION", "Asutuse vastet ei ole"
    UNKNOWN_COLUMN_VALUE = "UNKNOWN_COLUMN_VALUE", "Tundmatu veeru väärtus"
    UNREADABLE_COUNT = "UNREADABLE_COUNT", "Arvu ei õnnestunud lugeda"
    FEEDBACK_RESPONDED_EXCEEDS_REQUESTED = (
        "FEEDBACK_RESPONDED_EXCEEDS_REQUESTED",
        "Vastanuid on rohkem kui otse küsituid",
    )
    NEXT_ACTION_NOT_CONVERTED = "NEXT_ACTION_NOT_CONVERTED", "Järgmiseks jäi teisendamata"
    REFERENCE_CONFLICTS_WITH_NATIVE = (
        "REFERENCE_CONFLICTS_WITH_NATIVE",
        "Viide kuulub süsteemis loodud teemale",
    )
    SOURCE_DISAGREES_WITH_MATTER = "SOURCE_DISAGREES_WITH_MATTER", "Allikas ja teema on eri meelt"
    PARSE_ERROR = "PARSE_ERROR", "Rida ei õnnestunud töödelda"


#: Anomalies severe enough that the planner will not create or match on this
#: row. Everything else is recorded beside a row that still imports: a Matter
#: with an unresolved ministry name is more useful, and more honest, than no
#: Matter at all.
BLOCKING_ANOMALIES: frozenset[str] = frozenset(
    {
        Anomaly.INVALID_REFERENCE.value,
        Anomaly.REFERENCE_YEAR_MISMATCH.value,
        Anomaly.DUPLICATE_REFERENCE.value,
        Anomaly.MISSING_TITLE.value,
        Anomaly.REFERENCE_CONFLICTS_WITH_NATIVE.value,
        Anomaly.SOURCE_DISAGREES_WITH_MATTER.value,
        Anomaly.PARSE_ERROR.value,
    }
)


class OneNoteContentStatus(models.TextChoices):
    """Whether the OneNote page behind a preserved hyperlink has been imported.

    The hyperlink itself is immutable source evidence. This is the mutable
    operational note about what has been done with it, which is why the two are
    different columns (Stage-2A brief 21).
    """

    NOT_APPLICABLE = "NOT_APPLICABLE", "Ei kohaldu"
    NOT_IMPORTED = "NOT_IMPORTED", "Importimata"
    IMPORTED = "IMPORTED", "Imporditud"
    UNAVAILABLE = "UNAVAILABLE", "Kättesaamatu"
    FAILED = "FAILED", "Ebaõnnestus"


class ProposedRecordMode(models.TextChoices):
    """What the planner suggests, with the reason kept alongside.

    Three values, and the difference between the first two is the whole point.

    ``FULL`` is only ever reached through an operator's reviewed override file —
    a person attesting that this matter is live. ``FULL_CANDIDATE`` is the
    planner saying "this looks live, someone should check", and it is stored as
    an ARCHIVE record with the proposal kept in the ledger. The active set at
    cutover is a human decision, not an algorithm (master specification 19.5),
    and collapsing these two would let one ambiguous status string promote a
    record nobody has looked at.
    """

    FULL = "FULL", "Täielik kirje"
    FULL_CANDIDATE = "FULL_CANDIDATE", "Täieliku kirje kandidaat"
    ARCHIVE = "ARCHIVE", "Arhiiv"
