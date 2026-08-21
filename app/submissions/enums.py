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


class RecipientRole(models.TextChoices):
    """Why an organisation is on a submission.

    The distinction is real Chamber practice: an opinion is addressed to the
    ministry running the consultation, and copied to a committee or a partner
    for information. Flattening both into one list would make "who did Koda
    actually write to" unanswerable (design handoff, recommendation 4).
    """

    ADDRESSEE = "ADDRESSEE", "Adressaat"
    FOR_INFORMATION = "FOR_INFORMATION", "Teadmiseks"


class SentAtPrecision(models.TextChoices):
    """How much of ``sent_at`` the source actually supplied.

    The register gives a date. Storing it in a ``DateTimeField`` forces an
    anchor time, and rendering that anchor as "00:00" tells a lawyer the letter
    went out at midnight — a fact no source ever supplied. The anchor stays an
    implementation detail; this field is what the UI reads before choosing a
    format (Stage-2H brief 20).
    """

    TIMESTAMP = "TIMESTAMP", "Kuupäev ja kellaaeg"
    DATE = "DATE", "Ainult kuupäev"
