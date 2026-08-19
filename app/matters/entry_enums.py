from __future__ import annotations

from django.db import models


class EntryKind(models.TextChoices):
    """What kind of work this chronology entry records.

    Formal outbound written advocacy is deliberately absent: that is a
    `Submission`, not a note about one (master specification 11.2, 8.3).
    """

    NOTE = "NOTE", "Märkus"
    MEETING = "MEETING", "Kohtumine"
    CALL = "CALL", "Telefonikõne"
    HEARING = "HEARING", "Istung või kuulamine"
    WORKING_GROUP = "WORKING_GROUP", "Töörühm"
    JOINT_COORDINATION = "JOINT_COORDINATION", "Ühistegevuse koordineerimine"
    PUBLIC_STATEMENT = "PUBLIC_STATEMENT", "Avalik esinemine või kommentaar"
    OTHER = "OTHER", "Muu"
