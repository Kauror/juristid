"""What the register watch looks at, what it found, and each run (ADR 0217).

A `WatchTarget` is one place a Teema's proceeding can be read by a program: a
Riigikogu bill, or a title search in a ministry register. A `WatchFinding` is one
document or step found there, waiting for a lawyer. Nothing here is a fact about
the Teema until a lawyer adds it, and then the fact is the `Menetluse areng` the
addition wrote (`development`), not this row.
"""

from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.db import models
from django.utils import timezone

from app.core.models import BaseModel


class WatchSource(models.TextChoices):
    RIIGIKOGU = "RIIGIKOGU", "Riigikogu"
    HTM_REGISTER = "HTM_REGISTER", "Haridus- ja Teadusministeeriumi dokumendiregister"
    KLIM_REGISTER = "KLIM_REGISTER", "Kliimaministeeriumi dokumendiregister"
    ADR_JDM = "ADR_JDM", "Justiits- ja Digiministeeriumi dokumendiregister"
    ADR_MKM = "ADR_MKM", "Majandus- ja Kommunikatsiooniministeeriumi dokumendiregister"
    ADR_RAM = "ADR_RAM", "Rahandusministeeriumi dokumendiregister"
    ADR_SOM = "ADR_SOM", "Sotsiaalministeeriumi dokumendiregister"
    ADR_KUM = "ADR_KUM", "Kultuuriministeeriumi dokumendiregister"
    ADR_KAM = "ADR_KAM", "Kaitseministeeriumi dokumendiregister"


class TargetOrigin(models.TextChoices):
    #: The Teema's own title names it («… eelnõu (973 SE)»).
    TITLE = "TITLE", "Pealkirjast"
    #: Words taken from the Teema's title, searched in its ministry's register.
    PHRASE = "PHRASE", "Pealkirja sõnadest"
    #: Words a lawyer set for the Teema.
    PERSON = "PERSON", "Juristi otsingusõnad"


class FindingStatus(models.TextChoices):
    PENDING = "PENDING", "Ootab"
    ADDED = "ADDED", "Lisatud"
    #: Kept only as the Teema's `Menetluse link`, nothing on the timeline.
    LINKED = "LINKED", "Lisatud lingina"
    DISMISSED = "DISMISSED", "Jäetud vahele"
    #: Already in the register long before the first check; never offered.
    BASELINE = "BASELINE", "Varasem"


class Relevance(models.TextChoices):
    #: Not judged: the model is off, or the item belongs to the Teema by construction.
    UNJUDGED = "", "Hindamata"
    RELEVANT = "RELEVANT", "Puutub teemasse"
    UNSURE = "UNSURE", "Ebaselge"
    UNRELATED = "UNRELATED", "Ei puutu teemasse"


class WatchTarget(BaseModel):
    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        related_name="watch_targets",
        verbose_name="teema",
    )
    source = models.CharField(max_length=32, choices=WatchSource.choices, verbose_name="allikas")
    #: What the source is asked for: the bill's uuid, or the title search text.
    identifier = models.CharField(max_length=200, verbose_name="tunnus")
    #: How a person reads it: «973 SE».
    label = models.CharField(max_length=120, blank=True, verbose_name="nimetus")
    origin = models.CharField(max_length=16, choices=TargetOrigin.choices, verbose_name="päritolu")
    active = models.BooleanField(default=True, verbose_name="jälgitakse")
    #: The public page of the bill, when the source has one.
    url = models.URLField(max_length=1000, blank=True, verbose_name="link")
    checked_at = models.DateTimeField(null=True, blank=True, verbose_name="viimati kontrollitud")
    last_error = models.CharField(max_length=500, blank=True, verbose_name="viimane viga")

    class Meta:
        verbose_name = "jälgitav allikas"
        verbose_name_plural = "jälgitavad allikad"
        constraints = [
            models.UniqueConstraint(
                fields=["matter", "source", "identifier"], name="register_watch_target_once"
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_source_display()}: {self.label or self.identifier}"


class WatchFinding(BaseModel):
    target = models.ForeignKey(
        WatchTarget, on_delete=models.CASCADE, related_name="findings", verbose_name="allikas"
    )
    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        related_name="register_findings",
        verbose_name="teema",
    )
    #: The source's own id for the item, stable across checks.
    external_id = models.CharField(max_length=200, verbose_name="välistunnus")
    title = models.CharField(max_length=500, verbose_name="pealkiri")
    occurred_on = models.DateField(null=True, blank=True, db_index=True, verbose_name="kuupäev")
    url = models.URLField(max_length=1000, blank=True, verbose_name="link")
    #: Register number and sender, or the reading, as the source states them.
    detail = models.CharField(max_length=300, blank=True, verbose_name="täpsustus")
    status = models.CharField(
        max_length=16,
        choices=FindingStatus.choices,
        default=FindingStatus.PENDING,
        db_index=True,
        verbose_name="olek",
    )
    relevance = models.CharField(
        max_length=16, choices=Relevance.choices, blank=True, default="", verbose_name="hinnang"
    )
    #: The model's one line: what the document is.
    ai_note = models.CharField(max_length=500, blank=True, verbose_name="selgitus")
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="otsustas",
    )
    decided_at = models.DateTimeField(null=True, blank=True, verbose_name="otsustatud")
    #: The document's own files in the register, ``[{"name", "url"}]``, for the
    #: row's «Fail» links (owner, 2026-11-23): the title opens the register's
    #: page, where a file is not always shown. ``None`` = not looked up yet
    #: (`register_files.remember`), ``[]`` = the register shows none.
    files = models.JSONField(null=True, blank=True, verbose_name="failid")
    development = models.ForeignKey(
        "matters.MatterProceduralDevelopment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="märge",
    )

    class Meta:
        verbose_name = "registrist leitud dokument"
        verbose_name_plural = "registritest leitud dokumendid"
        constraints = [
            models.UniqueConstraint(
                fields=["target", "external_id"], name="register_watch_finding_once"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=[s.value for s in FindingStatus]),
                name="register_watch_finding_status",
            ),
            models.CheckConstraint(
                condition=models.Q(relevance__in=[r.value for r in Relevance]),
                name="register_watch_finding_relevance",
            ),
        ]
        indexes = [models.Index(fields=["matter", "status"], name="register_watch_by_matter")]

    def __str__(self) -> str:
        return self.title


#: A run that has not finished after this long was stopped from outside. The
#: nightly run takes about 16 minutes (18.11.2026, 335 sources).
ABANDONED_AFTER = dt.timedelta(hours=3)


class WatchRun(BaseModel):
    started_at = models.DateTimeField(verbose_name="algas")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="lõppes")
    targets_checked = models.PositiveIntegerField(default=0)
    findings_new = models.PositiveIntegerField(default=0)
    findings_offered = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=list, blank=True)
    ai_calls = models.PositiveIntegerField(default=0)
    ai_input_tokens = models.PositiveIntegerField(default=0)
    ai_output_tokens = models.PositiveIntegerField(default=0)
    #: The model the run asked, as the API named it in its answer; empty when
    #: the AI step was off for the run. Defaults in the database too, so the
    #: release before this one still records a run (rollout and rollback).
    ai_model = models.CharField(max_length=80, blank=True, default="", db_default="")
    ai_judged = models.PositiveIntegerField(default=0, db_default=0)
    ai_failures = models.PositiveIntegerField(default=0, db_default=0)

    class Meta:
        verbose_name = "registrite kontroll"
        verbose_name_plural = "registrite kontrollid"
        ordering = ["-started_at"]

    @property
    def aborted(self) -> bool:
        """The run stopped before its end: it said so, or it never finished.

        A run that raised records an error marked ``katkes`` and is closed; one
        whose process was killed (a release recreating the container, the host
        going down) never sets ``finished_at`` and is taken as stopped once it
        is older than any run takes (`ABANDONED_AFTER`).
        """
        if any(isinstance(error, dict) and error.get("katkes") for error in self.errors):
            return True
        return self.finished_at is None and self.started_at < timezone.now() - ABANDONED_AFTER

    @property
    def running(self) -> bool:
        return self.finished_at is None and not self.aborted


class CandidateStatus(models.TextChoices):
    NEW = "NEW", "Uus"
    #: «Uus teema»: a new Teema was created from it (`matter`).
    ADDED = "ADDED", "Lisatud teemaks"
    #: «Seo olemasolevaga»: its documents were linked to an existing Teema (`matter`).
    ATTACHED = "ATTACHED", "Seotud teemaga"
    DISMISSED = "DISMISSED", "Jäetud kõrvale"


class Strength(models.TextChoices):
    #: Not judged yet: the model is off, or has not answered for it.
    UNJUDGED = "", "Hindamata"
    STRONG = "STRONG", "Tugev"
    POSSIBLE = "POSSIBLE", "Võimalik"
    WEAK = "WEAK", "Nõrk"
    #: Not a legislative draft at all (an order to sign, a contract, a reply).
    NONE = "NONE", "Ei puutu"


class TopicCandidate(BaseModel):
    """A draft the registers show that is not a Teema yet (ADR 0218).

    One candidate gathers the register documents of one draft (`CandidateItem`).
    It is a proposal to the head of the department: nothing becomes a Teema
    until a person presses «Uus teema» and saves Uus teema. The model's
    strength and reason are shown, never acted on.
    """

    title = models.CharField(max_length=500, verbose_name="pealkiri")
    #: The title without the words every draft carries; what later documents are matched by.
    match_key = models.CharField(max_length=300, db_index=True, verbose_name="võrdlusvõti")
    status = models.CharField(
        max_length=16,
        choices=CandidateStatus.choices,
        default=CandidateStatus.NEW,
        db_index=True,
        verbose_name="olek",
    )
    strength = models.CharField(
        max_length=16, choices=Strength.choices, blank=True, default="", verbose_name="hinnang"
    )
    #: The model's line: how the draft may touch entrepreneurs.
    reason = models.CharField(max_length=500, blank=True, verbose_name="põhjendus")
    #: An open Teema the draft seems tied to; a suggestion for «Seo olemasolevaga».
    related_matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="seotud teema",
    )
    #: The Teema it became (ADDED) or was linked to (ATTACHED).
    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="topic_candidates",
        verbose_name="teema",
    )
    #: When it was last put on the list: first seen, or brought back after a dismissal.
    listed_at = models.DateTimeField(default=timezone.now, db_index=True, verbose_name="nimekirjas")
    #: Set when a dismissed candidate came back because the draft reached the Riigikogu.
    reopened_at = models.DateTimeField(null=True, blank=True, verbose_name="tagasi toodud")
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="otsustas",
    )
    decided_at = models.DateTimeField(null=True, blank=True, verbose_name="otsustatud")

    class Meta:
        verbose_name = "uue teema kandidaat"
        verbose_name_plural = "uute eelnõude kandidaadid"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=[s.value for s in CandidateStatus]),
                name="register_watch_candidate_status",
            ),
            models.CheckConstraint(
                condition=models.Q(strength__in=[s.value for s in Strength]),
                name="register_watch_candidate_strength",
            ),
        ]

    def __str__(self) -> str:
        return self.title


class CandidateItem(BaseModel):
    """One register document or Riigikogu bill a sweep saw, on its candidate."""

    candidate = models.ForeignKey(
        TopicCandidate, on_delete=models.CASCADE, related_name="items", verbose_name="kandidaat"
    )
    source = models.CharField(max_length=32, choices=WatchSource.choices, verbose_name="allikas")
    external_id = models.CharField(max_length=200, verbose_name="välistunnus")
    title = models.CharField(max_length=500, verbose_name="pealkiri")
    occurred_on = models.DateField(null=True, blank=True, verbose_name="kuupäev")
    url = models.URLField(max_length=1000, blank=True, verbose_name="link")
    detail = models.CharField(max_length=300, blank=True, verbose_name="täpsustus")

    class Meta:
        verbose_name = "kandidaadi dokument"
        verbose_name_plural = "kandidaatide dokumendid"
        ordering = ["-occurred_on", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "external_id"], name="register_watch_candidate_item_once"
            )
        ]

    def __str__(self) -> str:
        return self.title


class SweepRun(BaseModel):
    """One nightly sweep for new drafts (`sweep_registers`)."""

    started_at = models.DateTimeField(verbose_name="algas")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="lõppes")
    sources_read = models.PositiveIntegerField(default=0)
    items_new = models.PositiveIntegerField(default=0)
    candidates_new = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=list, blank=True)
    ai_calls = models.PositiveIntegerField(default=0)
    ai_input_tokens = models.PositiveIntegerField(default=0)
    ai_output_tokens = models.PositiveIntegerField(default=0)
    ai_model = models.CharField(max_length=80, blank=True, default="")
    ai_judged = models.PositiveIntegerField(default=0)
    ai_failures = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "uute eelnõude otsing"
        verbose_name_plural = "uute eelnõude otsingud"
        ordering = ["-started_at"]


class CandidateAudience(BaseModel):
    """Who the «Uued eelnõud» page is for, beyond the department's head (ADR 0218 §6).

    One row: the page is open to every business reader (`everyone`), or only to
    the head and the administrators. Changed in Haldus.
    """

    everyone = models.BooleanField(default=False, verbose_name="nähtav kõigile")

    class Meta:
        verbose_name = "uute eelnõude lehe nähtavus"
        verbose_name_plural = "uute eelnõude lehe nähtavus"


class GovernmentAgenda(BaseModel):
    """One week's «Valitsuse istungi kommenteeritud päevakord» from valitsus.ee (ADR 0219)."""

    url = models.URLField(max_length=500, unique=True, verbose_name="link")
    title = models.CharField(max_length=300, verbose_name="pealkiri")
    session_date = models.DateField(unique=True, verbose_name="istungi kuupäev")
    published_at = models.DateTimeField(null=True, blank=True, verbose_name="avaldatud")
    #: When the model's answer for this agenda was read (audit HR-02, 2026-11-23).
    #: One readable answer is the whole attempt: an item it said nothing about, or
    #: answered with something unusable, stays «Hindamata» and is not asked again.
    #: Empty while no answer has been read; the next run then asks again, at most
    #: `agenda.MAX_ATTEMPTS` times in all (`ai_attempts`).
    judged_at = models.DateTimeField(null=True, blank=True, verbose_name="hinnatud")
    #: Requests made to the model for this agenda, answered or failed.
    ai_attempts = models.PositiveSmallIntegerField(
        default=0,
        # In the database too, so the previous release can still insert an agenda
        # after a code-only rollback (r91 kit review).
        db_default=0,
        verbose_name="hindamiskatseid",
    )

    class Meta:
        verbose_name = "valitsuse päevakord"
        verbose_name_plural = "valitsuse päevakorrad"
        ordering = ["-session_date"]

    def __str__(self) -> str:
        return self.title


class AgendaItemStatus(models.TextChoices):
    PENDING = "PENDING", "Ootab"
    ADDED = "ADDED", "Lisatud teemale"
    DISMISSED = "DISMISSED", "Jäetud vahele"


class AgendaItem(BaseModel):
    """One numbered item of a Government agenda, as published, with the model's verdict."""

    agenda = models.ForeignKey(
        GovernmentAgenda, on_delete=models.CASCADE, related_name="items", verbose_name="päevakord"
    )
    number = models.PositiveSmallIntegerField(verbose_name="punkt")
    title = models.CharField(max_length=1000, verbose_name="pealkiri")
    presenter = models.CharField(max_length=300, blank=True, verbose_name="esitaja")
    kind = models.CharField(max_length=120, blank=True, verbose_name="tüüp")
    text = models.TextField(blank=True, verbose_name="valitsuse selgitus")
    strength = models.CharField(
        max_length=16, choices=Strength.choices, blank=True, default="", verbose_name="hinnang"
    )
    reason = models.CharField(max_length=500, blank=True, verbose_name="põhjendus")
    #: An open Teema the item is plainly about; only one the model was offered.
    related_matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="seotud teema",
    )
    #: What a person did with the item on its Teema (owner, 2026-11-22): «Lisa teemale»
    #: wrote a `Menetluse areng` (`development`), «Märgi loetuks» only recorded it.
    status = models.CharField(
        max_length=16,
        choices=AgendaItemStatus.choices,
        default=AgendaItemStatus.PENDING,
        db_default=AgendaItemStatus.PENDING,
        verbose_name="olek",
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="otsustas",
    )
    decided_at = models.DateTimeField(null=True, blank=True, verbose_name="otsustatud")
    development = models.ForeignKey(
        "matters.MatterProceduralDevelopment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="märge",
    )

    class Meta:
        verbose_name = "päevakorrapunkt"
        verbose_name_plural = "päevakorrapunktid"
        ordering = ["number"]
        constraints = [
            models.UniqueConstraint(
                fields=["agenda", "number"], name="register_watch_agenda_item_once"
            ),
            models.CheckConstraint(
                condition=models.Q(strength__in=[s.value for s in Strength]),
                name="register_watch_agenda_item_strength",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.number}. {self.title}"

    @property
    def text_paragraphs(self) -> list[str]:
        return [part for part in self.text.split(chr(10) * 2) if part.strip()]


class CandidateNotice(BaseModel):
    """«Monitooring» in the bell: one unread notice per reader (ADR 0218 §7, ADR 0219).

    It counts the new drafts a sweep listed and the Government agendas a run stored.
    """

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="candidate_notices",
        verbose_name="saaja",
    )
    candidates = models.PositiveIntegerField(default=0, verbose_name="kandidaate")
    #: New Government agendas with at least one item that may matter (ADR 0219).
    agendas = models.PositiveIntegerField(default=0, db_default=0, verbose_name="päevakordi")
    read_at = models.DateTimeField(null=True, blank=True, verbose_name="loetud")

    class Meta:
        verbose_name = "uute eelnõude teavitus"
        verbose_name_plural = "uute eelnõude teavitused"
        indexes = [models.Index(fields=["recipient", "read_at"], name="candidate_notice_inbox")]


class RegisterNotice(BaseModel):
    """«Registris uus dokument» in the bell: one per Teema for its owner (ADR 0217 §6).

    A run that offers new documents on a Teema tells the Teema's owner once; a
    later run adds to the same unread notice instead of making another. It is
    presentation state of the owner's account, like `PersonalReminder`: read and
    unread by its own person only. It drops out of the inbox when every document
    it points to has been added or skipped (`app.register_watch.notices`).
    """

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="register_notices",
        verbose_name="saaja",
    )
    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        related_name="register_notices",
        verbose_name="teema",
    )
    documents = models.PositiveIntegerField(default=0, verbose_name="dokumente")
    read_at = models.DateTimeField(null=True, blank=True, verbose_name="loetud")

    class Meta:
        verbose_name = "registriteavitus"
        verbose_name_plural = "registriteavitused"
        indexes = [models.Index(fields=["recipient", "read_at"], name="register_notice_inbox")]
