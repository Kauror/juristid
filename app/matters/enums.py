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
