from __future__ import annotations

from django.db import models


class EntryKind(models.TextChoices):
    """What kind of work this chronology entry records.

    Formal outbound written advocacy is deliberately absent: that is a
    `Submission`, not a note about one (master specification 11.2, 8.3).

    **A procedural development is absent too, and for a stronger reason.**
    «Ministeerium saatis eelnõu uue versiooni» is a canonical fact with an
    optional date, a title a projection can read without parsing prose, and a
    lawyer's note that must not become the ministry's own sentence. It carried an
    `EntryKind` for one round and now has a record of its own,
    `MatterProceduralDevelopment`, whose docstring argues the three points an
    `Entry` could not hold (docs/adr/0091 §5).
    """

    NOTE = "NOTE", "Märkus"
    MEETING = "MEETING", "Kohtumine"
    CALL = "CALL", "Telefonikõne"
    HEARING = "HEARING", "Istung või kuulamine"
    WORKING_GROUP = "WORKING_GROUP", "Töörühm"
    JOINT_COORDINATION = "JOINT_COORDINATION", "Ühistegevuse koordineerimine"
    PUBLIC_STATEMENT = "PUBLIC_STATEMENT", "Avalik esinemine või kommentaar"
    OTHER = "OTHER", "Muu"
