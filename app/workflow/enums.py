"""Stage, track, closure and next-action vocabulary.

Four questions are kept apart on purpose (master specification 3.4):

* ``StageVocabulary`` — where the external process is;
* ``Track`` — what kind of process it is;
* ``Disposition`` — why Koda is no longer actively working on it;
* ``ActionKind`` + ``DateSemantics`` — what Koda does next, and what its date
  actually means.

The last pair is why the register's single "next" column could never be trusted:
a date meant a hard deadline, a reminder to look again, or a guess about someone
else's timing, and nothing recorded which.
"""

from __future__ import annotations

from django.db import models


class Track(models.TextChoices):
    DOMESTIC = "DOMESTIC", "Riigisisene"
    EU_INITIATIVE = "EU_INITIATIVE", "ELi algatus"
    NATIONAL_TRANSPOSITION = "NATIONAL_TRANSPOSITION", "ELi õiguse ülevõtmine"
    STRATEGY = "STRATEGY", "Strateegia või arengukava"
    KODA_INITIATIVE = "KODA_INITIATIVE", "Koja algatus"
    IMPLEMENTATION = "IMPLEMENTATION", "Rakendamine või järelevalve"
    OTHER = "OTHER", "Muu"


class Disposition(models.TextChoices):
    """Why the Matter is closed. Not a stage and not an advocacy outcome."""

    COMPLETED = "COMPLETED", "Lõpetatud või jõustunud"
    SUPERSEDED = "SUPERSEDED", "Asendatud järgnenud teemaga"
    INITIATIVE_WITHDRAWN = "INITIATIVE_WITHDRAWN", "Algataja loobus"
    MONITORING_STOPPED = "MONITORING_STOPPED", "Koda lõpetas jälgimise"
    NO_POSITION_FORMED = "NO_POSITION_FORMED", "Seisukohta ei kujundatud"
    RESPONSE_COMPLETE = "RESPONSE_COMPLETE", "Vastus esitatud ja järeltegevus tehtud"
    DUPLICATE = "DUPLICATE", "Duplikaat või liidetud"
    OTHER = "OTHER", "Muu"


class ActionKind(models.TextChoices):
    """What Koda is doing about this Matter right now.

    The distinction is the point: a WAIT is not an unfinished task, and
    describing it as overdue would turn every ordinary dependency on a ministry
    into a false alarm (master specification 11.2, 18.8).
    """

    DO = "DO", "Teen"
    WAIT = "WAIT", "Ootan"
    MONITOR = "MONITOR", "Jälgin"


class DateSemantics(models.TextChoices):
    """What the action's date means. Only DEADLINE can be missed."""

    DEADLINE = "DEADLINE", "Tähtaeg"
    REVIEW_ON = "REVIEW_ON", "Vaatan üle"
    EXPECTED_AROUND = "EXPECTED_AROUND", "Oodatav umbes"


class DatePrecision(models.TextChoices):
    """How exact the target date is.

    Historical rows and external expectations are frequently known only to the
    month or the quarter. Recording a guess as an exact date would manufacture
    certainty the source never had (master specification 3.5).
    """

    EXACT = "EXACT", "Täpne"
    MONTH = "MONTH", "Kuu täpsusega"
    QUARTER = "QUARTER", "Kvartali täpsusega"
    HALF_YEAR = "HALF_YEAR", "Poolaasta täpsusega"
    YEAR = "YEAR", "Aasta täpsusega"
    INFERRED = "INFERRED", "Tuletatud tekstist"


class ActionStatus(models.TextChoices):
    OPEN = "OPEN", "Kehtiv"
    COMPLETED = "COMPLETED", "Tehtud"
    CANCELLED = "CANCELLED", "Tühistatud"
    SUPERSEDED = "SUPERSEDED", "Asendatud"


#: Only this combination can be genuinely overdue.
OVERDUE_KIND = ActionKind.DO
OVERDUE_SEMANTICS = DateSemantics.DEADLINE

#: These are due for a look, never "missed".
REVIEW_KINDS = (ActionKind.WAIT, ActionKind.MONITOR)
