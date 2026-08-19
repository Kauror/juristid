"""Stage, track and closure vocabulary.

Three questions are kept apart on purpose (master specification 3.4):

* ``StageVocabulary`` — where the external process is;
* ``Track`` — what kind of process it is;
* ``Disposition`` — why Koda is no longer actively working on it.

``NextAction`` (what Koda does next) arrives in Stage 1 and lives in this
module too.
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
