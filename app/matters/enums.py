from __future__ import annotations

from django.db import models


class RecordMode(models.TextChoices):
    """One Matter model covers both current work and the register archive.

    There is deliberately no second `LegacyRegisterRecord` business model
    (master specification 11.5, 19.4).
    """

    FULL = "FULL", "Täielik"
    ARCHIVE = "ARCHIVE", "Arhiiv"


class MatterOrigin(models.TextChoices):
    NATIVE = "NATIVE", "Loodud süsteemis"
    LEGACY_IMPORT = "LEGACY_IMPORT", "Imporditud registrist"
    # A Matter that exists only because somebody kept a OneNote page about it.
    # Separate from LEGACY_IMPORT because it has no Excel row behind it: it has
    # no register reference, no reviewed year contract and no row number, and
    # claiming register provenance it does not have would make the archive look
    # more authoritative than it is (Stage-2D brief 15).
    LEGACY_ONENOTE = "LEGACY_ONENOTE", "Imporditud OneNote'ist"
    PROMOTED_LEGACY = "PROMOTED_LEGACY", "Arhiivist aktiveeritud"
    OTHER = "OTHER", "Muu"


#: Origins whose ``reporting_year`` may be read as a *reporting* year.
#:
#: ``LEGACY_ONENOTE`` is deliberately absent. The historical importer fills a
#: OneNote-only Matter's ``reporting_year`` from the page's own
#: ``source_created_at``, which is the right thing for it to do — it is the only
#: date that page has. It is not a reporting year: nobody filed that matter
#: under it, and a page edited in 2021 about a 2018 draft would be reported as
#: 2021 work. So a Matter-by-year statistic places those in *Teadmata aasta*,
#: and the page timestamps are analysed separately as source history
#: (master specification 19.4, Stage-2E brief 14, 15).
REGISTER_YEAR_ORIGINS: tuple[str, ...] = (
    MatterOrigin.NATIVE.value,
    MatterOrigin.LEGACY_IMPORT.value,
    MatterOrigin.PROMOTED_LEGACY.value,
    MatterOrigin.OTHER.value,
)


class DataQualityTier(models.TextChoices):
    """How much of this record has been verified (master specification 19.6)."""

    TIER_1_VERIFIED_ACTIVE = "TIER_1", "1 — üleminekul kinnitatud aktiivne"
    TIER_2_RICH_HISTORY = "TIER_2", "2 — hiljutine sisukas ajalugu"
    TIER_3_REGISTER_ARCHIVE = "TIER_3", "3 — vanem registriarhiiv"
    TIER_4_UNVERIFIED = "TIER_4", "4 — sidumata või kontrollimata"


class TagAssignmentSource(models.TextChoices):
    MANUAL = "MANUAL", "Käsitsi"
    IMPORTED = "IMPORTED", "Imporditud"
    APPROVED_RULE = "APPROVED_RULE", "Kinnitatud reegel"


class MatterDataClass(models.TextChoices):
    """Real business data, or data made while developing Juristid.

    Deliberately its own dimension rather than a reuse of something nearby.

    - It is not ``record_mode``: an archive row is real history.
    - It is not ``origin``: a Matter created in the system is normally real work.
    - It is not ``DataQualityTier``: an unverified register row is real data
      somebody has not checked yet, which is the opposite of a record that was
      never about anything.
    - It is not a ``Tag`` or a ``PolicyArea``: those are business vocabulary a
      lawyer curates, and a taxonomy entry called TEST would appear in the
      chooser, in statistics and in the tag cloud.
    - It is not an environment flag: a development record and a real one can sit
      in the same database at the same time, which is precisely the situation
      that needs naming.

    Testness is a property of the record's own identity, so it is stored on the
    record (Agent-C brief 0, 8).
    """

    REAL = "REAL", "Pärisandmed"
    TEST = "TEST", "Testandmed"


class EngagementKind(models.TextChoices):
    """How Koda asked members and stakeholders for input on a Matter.

    **Channels, not vendors.** ``SendSmaily``, ``Alchemer`` and ``koda.ee`` are
    the tools the department happens to use this year; a stored value naming one
    of them would become wrong the day a contract changes, and every historical
    row would then describe a service nobody recognises. The channel — was this
    a public call, a mailing, a questionnaire — survives that. Which concrete
    service was used is answered by the title and the link, where a person can
    read it and correct it (Agent-F brief 9).

    ``MEETING`` was deliberately absent until the approved Teema target asked for
    it by name. The old reasoning — a meeting is authored chronology, `Entry`
    already records it with a date, an author and a body, and a second home for
    the fact would guarantee two records that disagree — held while `Kaasamine`
    was a standing section filled in separately from the composer (brief 10).
    The target folds both into one save: `+ Kaasamine` offers `Koosolek` beside
    `Küsitlus` and `Kirjade voor`, and one `Salvesta` writes whichever was
    chosen, in the same transaction as the note describing it. A meeting
    recorded through that panel is the engagement, not a copy of one
    (docs/adr/0074).

    ``WEB_CALL`` is not offered by the composer and is not retired either: rows
    carrying it are historical fact, they still read, they still filter and they
    still edit. A value the write surface stopped offering is not a value the
    database stopped accepting.
    """

    WEB_CALL = "WEB_CALL", "Kaasamiskutse veebis"
    EMAIL_CAMPAIGN = "EMAIL_CAMPAIGN", "E-kiri või kampaania"
    SURVEY = "SURVEY", "Küsitlus"
    MEETING = "MEETING", "Koosolek"
    OTHER = "OTHER", "Muu"


#: What `+ Kaasamine` offers, in the order the approved target lists it.
#:
#: Three chips, not five. `Kirjade voor` is the target's name for the mailing
#: `EMAIL_CAMPAIGN` has always stored, so the label moved and the stored value
#: did not. `WEB_CALL` and `OTHER` remain valid stored values with no chip
#: (docs/adr/0074, TEEMA_TARGET_SPEC §C.4).
COMPOSER_ENGAGEMENT_KINDS: tuple[tuple[str, str], ...] = (
    (EngagementKind.SURVEY.value, "Küsitlus"),
    (EngagementKind.MEETING.value, "Koosolek"),
    (EngagementKind.EMAIL_CAMPAIGN.value, "Kirjade voor"),
)
