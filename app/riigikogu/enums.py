"""The vocabularies of the Riigikogu source (ADR 0222–0226)."""

from __future__ import annotations

from django.db import models


class DocumentKind(models.TextChoices):
    """What a Riigikogu document is *to koda*, decided from its metadata, never its prose."""

    #: A committee's letter asking koda (among others) for an opinion or participation.
    INVITATION = "INVITATION", "Kutse kojale"
    #: koda's own opinion or appeal, registered as received by the Riigikogu.
    KODA_OPINION = "KODA_OPINION", "Koja arvamus või pöördumine"
    #: Any other letter where koda is an addressee (a reply, a notice).
    KODA_LETTER = "KODA_LETTER", "Muu kiri kojale"
    #: Another organisation's opinion on a bill.
    OTHER_OPINION = "OTHER_OPINION", "Teise organisatsiooni arvamus"
    AMENDMENT = "AMENDMENT", "Muudatusettepanek"
    BILL_TEXT = "BILL_TEXT", "Eelnõu tekst"
    PROTOCOL = "PROTOCOL", "Komisjoni protokoll"
    OTHER = "OTHER", "Muu dokument"
    #: The metadata does not say plainly which of the above it is.
    UNCERTAIN = "UNCERTAIN", "Ebaselge"


class KodaRole(models.TextChoices):
    """How koda appears in a document's metadata (`organisation.koda_role`)."""

    ADDRESSEE = "ADDRESSEE", "Adressaat"
    AUTHOR = "AUTHOR", "Saatja"
    #: Named only in the title or text, not as author or addressee.
    MENTIONED = "MENTIONED", "Mainitud"
    NONE = "NONE", "Puudub"
    UNCERTAIN = "UNCERTAIN", "Ebaselge"


class TextStatus(models.TextChoices):
    NOT_FETCHED = "NOT_FETCHED", "Lugemata"
    READ = "READ", "Loetud"
    NO_FILE = "NO_FILE", "Faili pole"
    UNREADABLE = "UNREADABLE", "Dokumenti ei õnnestunud lugeda"


class DeadlineKind(models.TextChoices):
    """An external parliamentary date. None of these is koda's own obligation (ADR 0223 §3)."""

    #: The date a committee asks an opinion or proposals by.
    OPINION_REQUEST = "OPINION_REQUEST", "Arvamuse tähtaeg komisjonile"
    #: The date a committee asks to be told whether koda will attend.
    ATTENDANCE = "ATTENDANCE", "Osalemisest teatamise tähtaeg"
    #: The Riigikogu's own deadline for members' and factions' amendments.
    AMENDMENTS = "AMENDMENTS", "Muudatusettepanekute tähtaeg"


class ExtractionStatus(models.TextChoices):
    """Whether a deadline was read from the letter (ADR 0223 §5). Wording is the UI's."""

    FOUND = "FOUND", "Tähtaeg tuvastatud"
    CHECK = "CHECK", "Tähtaeg vajab kontrollimist"
    NOT_FOUND = "NOT_FOUND", "Tähtaega ei leitud"
    UNREADABLE = "UNREADABLE", "Dokumenti ei õnnestunud lugeda"
    #: A structured API field, not read from prose (the amendment deadline).
    STRUCTURED = "STRUCTURED", "Riigikogu andmetest"


class FactState(models.TextChoices):
    ACTIVE = "ACTIVE", "Kehtiv"
    SUPERSEDED = "SUPERSEDED", "Asendatud"
    CANCELLED = "CANCELLED", "Tühistatud"


class InvitationReview(models.TextChoices):
    """What a person did with an invitation. Nothing here writes a Lovable field."""

    NEW = "NEW", "Ootab"
    DATE_ADDED = "DATE_ADDED", "Lisatud tähtajana"
    LINKED = "LINKED", "Seotud olemasoleva tähtajaga"
    REMINDER = "REMINDER", "Lisatud meeldetuletus"
    NO_ACTION = "NO_ACTION", "Edasist tööd pole vaja"


class ResponseCheck(models.TextChoices):
    """After the deadline: was the invitation answered? (ADR 0226 §4)

    The default after the deadline is «vajab kontrollimist», never «ei vastanud»:
    no public document is not proof of no answer.
    """

    NOT_DUE = "NOT_DUE", "Tähtaeg pole möödas"
    FOUND = "FOUND", "Koja arvamus registris"
    NEEDS_CHECK = "NEEDS_CHECK", "Kutsele vastamine vajab kontrollimist"
    OPINION = "OPINION", "Seotud koja arvamusega"
    JOINT = "JOINT", "Vastati ühispöördumisega"
    PARTICIPATED = "PARTICIPATED", "Osaleti komisjoni istungil"
    DECIDED_NOT = "DECIDED_NOT", "Otsustati mitte vastata"
    OTHER = "OTHER", "Muu tegevus"


class AgendaState(models.TextChoices):
    SCHEDULED = "SCHEDULED", "Kavas"
    MOVED = "MOVED", "Aeg muutus"
    #: Missing from several complete readings of a window that still covers it.
    CANCELLED = "CANCELLED", "Päevakorrast kadunud"
    PAST = "PAST", "Toimunud"


class MentionCategory(models.TextChoices):
    LEGISLATIVE = "LEGISLATIVE", "Õigusloomega seotud mainimine"
    OTHER = "OTHER", "Muu koja mainimine"


class Stage(models.TextChoices):
    """The stages of the nightly Riigikogu cycle, in their order (ADR 0222 §5)."""

    BILLS = "BILLS", "Eelnõud ja muudatusettepanekute tähtajad"
    INVITATIONS = "INVITATIONS", "Kutsed kojale"
    AGENDAS = "AGENDAS", "Komisjonide päevakorrad"
    PROTOCOLS = "PROTOCOLS", "Komisjonide protokollid"
    STENOGRAMS = "STENOGRAMS", "Stenogrammid"
    RESPONSES = "RESPONSES", "Kutsetele vastamise kontroll"
    ANALYSIS = "ANALYSIS", "Dokumentide analüüs"


class RunStatus(models.TextChoices):
    RUNNING = "RUNNING", "Käib"
    SUCCEEDED = "SUCCEEDED", "Õnnestus"
    #: Finished, but part of the source could not be read; the checkpoint did not move.
    PARTIAL = "PARTIAL", "Osaliselt"
    FAILED = "FAILED", "Ebaõnnestus"
    DISABLED = "DISABLED", "Välja lülitatud"


class AnalysisType(models.TextChoices):
    POSITIONS = "POSITIONS", "Teiste seisukohad"
    VERSION_CHANGES = "VERSION_CHANGES", "Redaktsioonide erinevused"
    PROPOSAL_OUTCOMES = "PROPOSAL_OUTCOMES", "Koja ettepanekute arvestamine"
    MENTION_CONTEXT = "MENTION_CONTEXT", "Mainimise kontekst"


class AnalysisStatus(models.TextChoices):
    PENDING = "PENDING", "Ootab"
    DONE = "DONE", "Valmis"
    FAILED = "FAILED", "Ebaõnnestus"
    #: The model answered something that failed validation or cited passages
    #: that are not in the source; nothing of it is shown as a finding.
    REJECTED = "REJECTED", "Tagasi lükatud"
    SUPERSEDED = "SUPERSEDED", "Aegunud"
