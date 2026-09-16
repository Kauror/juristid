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
    #: `Menetluse areng` — the external procedure moved, and this is the step it
    #: took.
    #:
    #: «Ministeerium saatis uue eelnõu versiooni», «Eelnõu jõudis Riigikokku»,
    #: «Seadus võeti vastu». It is authored chronology like every other value
    #: here — a dated, attributable sentence about something that happened — and
    #: it is a value rather than a model because that is exactly what the fact
    #: is. What earns it its own name is that the panel writing it also offers
    #: `Hetkeseis` and `Järgmiseks` in the same save, so a reader can tell a
    #: procedural step apart from a `Märkus` about one (docs/adr/0088 §5).
    #:
    #: **Not a milestone and not a deadline.** It creates no
    #: `MatterImportantDate`, enters no `real_deadlines`, draws no column on the
    #: process strip and reads in no work queue of its own. What it may create is
    #: a `NextAction`, and only because somebody wrote one.
    PROCEDURAL_DEVELOPMENT = "PROCEDURAL_DEVELOPMENT", "Menetluse areng"
    OTHER = "OTHER", "Muu"
