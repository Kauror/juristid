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
    #: What Koda sends on a Teema it started itself — a proposal or an appeal
    #: nobody asked for (docs/adr/0151 §5). Recorded and evidenced like every
    #: other send, and followed up thirty days later like every other send.
    KODA_PROPOSAL = "KODA_PROPOSAL", "Koja ettepanek või pöördumine"
    #: Koda's official answer that it has **no comments or proposals** — «Oleme
    #: eelnõuga tutvunud ning täiendavaid ettepanekuid ei ole» — sent by e-mail
    #: or letter (docs/adr/0174). An answer, not an opinion: it ends the request
    #: like any send, keeps its e-mail or letter as evidence, and is never
    #: counted as an «arvamus välja» or followed up thirty days later.
    NO_OBJECTION_RESPONSE = "NO_OBJECTION_RESPONSE", "Vastus: märkusi ja ettepanekuid ei ole"
    OTHER = "OTHER", "Muu"


#: Sends that answer a request without stating a position: counted apart from
#: opinions, never followed up (docs/adr/0174). A sent one still discharges an
#: `Arvamuse tähtaeg` like every other send.
NOT_AN_OPINION_KINDS: frozenset[str] = frozenset({SubmissionKind.NO_OBJECTION_RESPONSE.value})


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
