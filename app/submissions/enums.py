from __future__ import annotations

from django.db import models


class SubmissionKind(models.TextChoices):
    """The forms Koda's outbound written advocacy actually takes.

    One Matter routinely produces several of these — an opinion during the
    consultation round, a supplementary letter after the ministry responds, a
    submission to the Riigikogu committee later. That is why the register's
    single sent-date column could never count opinions correctly
    (master specification 6.4).
    """

    FORMAL_OPINION = "FORMAL_OPINION", "Ametlik arvamus"
    SUPPLEMENTARY_OPINION = "SUPPLEMENTARY_OPINION", "Täiendav arvamus"
    PARLIAMENTARY_SUBMISSION = "PARLIAMENTARY_SUBMISSION", "Pöördumine Riigikogule"
    JOINT_LETTER = "JOINT_LETTER", "Ühispöördumine"
    INFORMAL_WRITTEN_RESPONSE = "INFORMAL_WRITTEN_RESPONSE", "Mitteametlik kirjalik vastus"
    OTHER = "OTHER", "Muu"


class SubmissionStatus(models.TextChoices):
    DRAFT = "DRAFT", "Koostamisel"
    SENT = "SENT", "Saadetud"
    WITHDRAWN = "WITHDRAWN", "Tagasi võetud"
    SUPERSEDED = "SUPERSEDED", "Asendatud"
