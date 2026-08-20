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
