"""Sent opinions, as one list read from Juristid's own database (owner, 2026-11-16).

Statistika's opinion figures — how many went out, when, by whom, against which
deadline — all read :func:`opinion_facts` and nothing else. It is the **one
reader**: the only place that knows where a sent opinion is recorded.

Three records hold that fact today, and every one of them is a table in this
application's database. Nothing here asks Lovable anything, so the figures keep
working when Lovable is switched off (the owner's condition):

1. **A sent `Koja arvamus`** (`Submission.objects.historically_sent()`): the
   native record, and the only one written once Juristid is the register.
2. **An `Arvamuse tähtaeg` a person ended as answered without a document**
   (`MatterResponseDeadline`, ``ANSWERED``, no submission, not the
   synchronisation's own ending): a lawyer's statement that Koda answered.
3. **The imported statement «välja saadetud»** (`LovableOpinionOutcome`, the
   current row, ``SENT``): the register's history, 2011 onwards, stored here
   by the synchronisation. When Lovable goes these rows stay as they are.

One fact per sent opinion. The synchronisation ends the open request of a
Teema whose source says «saadetud» and writes row 3 for the same act, so its
own endings are left out of 2 (`lovable_sync.is_sync_ending`); a request ended
with a submission is that submission (1), not a second fact; and an imported
statement (3) is left out when the same Teema already has a native fact (1 or
2) for the same day — a person supplying the document for an opinion the
register says went out. Different days are different opinions.

**Authorization.** Facts are read only for Teemad the reader may see
(`Matter.objects.visible_to`), so a restricted or deleted Teema contributes
nothing and an administrator without a business role reads an empty list
(docs/adr/0181).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from django.utils import timezone

#: The legal-instrument keys that make a Teema «otseselt ELiga seotud».
EU_INSTRUMENT_KEYS = frozenset({"direktiiv", "el-maarus", "eli-konsultatsioon", "muu-eli-dokument"})

SOURCE_SUBMISSION = "submission"
SOURCE_ANSWERED = "answered"
SOURCE_IMPORTED = "imported"

#: Whose opinion it is when the Teema names nobody.
NO_OWNER = "Määramata"


@dataclass(frozen=True)
class MatterFacts:
    """What every statistic needs to know about one Teema, read once."""

    matter_id: Any
    reference: str
    title: str
    year: int | None
    received: date | None
    owner: str
    is_eu: bool
    koda_initiative: bool
    instrument: str
    #: The instrument's key, for links; "" when the Teema has none or several.
    instrument_key: str = ""
    #: Valdkonnad as ``(key, name)``, and senders as ``(id, name)``: the
    #: Teema's own confirmed fields, never a proposal (ADR 0215).
    areas: tuple[tuple[str, str], ...] = ()
    senders: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class DeclinedFact:
    """One request Koda decided not to answer («ei saatnud»)."""

    matter: MatterFacts
    #: The day the decision is counted on: the request's deadline, else the
    #: day the Teema arrived. ``None`` when neither is recorded.
    when: date | None
    source: str


@dataclass(frozen=True)
class OpinionFact:
    """One opinion Koda sent."""

    matter: MatterFacts
    sent_on: date
    #: The `Arvamuse tähtaeg` it answered, or ``None`` where none was recorded.
    deadline: date | None
    source: str

    @property
    def on_time(self) -> bool | None:
        """Sent by its deadline; ``None`` without a deadline."""
        if self.deadline is None:
            return None
        return self.sent_on <= self.deadline

    @property
    def days_late(self) -> int:
        """Days the opinion went out after its deadline; 0 when on time or without one."""
        if self.deadline is None:
            return 0
        return max(0, (self.sent_on - self.deadline).days)

    @property
    def days_given(self) -> int | None:
        """Days between the request arriving and its deadline; ``None`` if either is unknown."""
        if self.deadline is None or self.matter.received is None:
            return None
        return (self.deadline - self.matter.received).days


def _owner_label(owner: Any, former: str) -> str:
    if owner is not None:
        return str(owner.get_short_name() or owner.display_name or NO_OWNER)
    return (former or "").split()[0] if (former or "").strip() else NO_OWNER


def matter_facts(user: Any) -> dict[Any, MatterFacts]:
    """Every Teema the reader may see, as :class:`MatterFacts`. Four queries."""
    from app.matters.models import Matter, MatterSourceOrganisation
    from app.workflow.enums import Track

    matters = list(
        Matter.objects.visible_to(user)
        .select_related("owner")
        .only(
            "id",
            "title",
            "reference_year",
            "reference_number",
            "received_date",
            "track",
            "former_owner_name",
            "owner__display_name",
        )
    )
    instruments: dict[Any, list[tuple[str, str]]] = {}
    through = Matter.legal_instruments.through
    for matter_id, key, label in through.objects.filter(
        matter_id__in=[m.pk for m in matters]
    ).values_list("matter_id", "legalinstrumenttype__key", "legalinstrumenttype__label_et"):
        instruments.setdefault(matter_id, []).append((key, label))
    areas: dict[Any, list[tuple[str, str]]] = {}
    for matter_id, key, label in Matter.policy_areas.through.objects.filter(
        matter_id__in=[m.pk for m in matters]
    ).values_list("matter_id", "policyarea__key", "policyarea__name_et"):
        areas.setdefault(matter_id, []).append((key, label))
    senders: dict[Any, list[tuple[str, str]]] = {}
    for matter_id, org_id, name in MatterSourceOrganisation.objects.filter(
        matter_id__in=[m.pk for m in matters]
    ).values_list("matter_id", "organisation_id", "organisation__name"):
        senders.setdefault(matter_id, []).append((str(org_id), name))
    facts: dict[Any, MatterFacts] = {}
    for m in matters:
        own = sorted(instruments.get(m.pk, []))
        reference = (
            f"{m.reference_year}_{m.reference_number}"
            if m.reference_year and m.reference_number
            else ""
        )
        facts[m.pk] = MatterFacts(
            matter_id=m.pk,
            reference=reference,
            title=m.title,
            year=m.reference_year,
            received=m.received_date,
            owner=_owner_label(m.owner, m.former_owner_name),
            is_eu=any(key in EU_INSTRUMENT_KEYS for key, _ in own),
            koda_initiative=m.track == Track.KODA_INITIATIVE,
            instrument=own[0][1] if len(own) == 1 else ("Mitu liiki" if own else "Määramata"),
            instrument_key=own[0][0] if len(own) == 1 else "",
            areas=tuple(sorted(areas.get(m.pk, []))),
            senders=tuple(sorted(senders.get(m.pk, []))),
        )
    return facts


def opinion_facts(user: Any, matters: dict[Any, MatterFacts] | None = None) -> list[OpinionFact]:
    """Every opinion Koda sent on a Teema the reader may see, oldest first."""
    from app.legacy_import.lovable_sync import is_sync_ending
    from app.legacy_import.lovable_sync_models import OpinionOutcome, current_opinion_outcomes
    from app.matters.enums import ResponseDeadlineOutcome
    from app.matters.models import MatterResponseDeadline
    from app.submissions.models import Submission

    known = matters if matters is not None else matter_facts(user)
    if not known:
        return []
    facts: list[OpinionFact] = []

    # 1. Juristid's own sent opinions.
    answered_by_submission: dict[Any, date] = {}
    ended = list(
        MatterResponseDeadline.objects.filter(
            matter_id__in=known, outcome=ResponseDeadlineOutcome.ANSWERED
        ).values("matter_id", "deadline", "submission_id", "ended_by_id", "note", "ended_at")
    )
    for row in ended:
        if row["submission_id"]:
            answered_by_submission[row["submission_id"]] = row["deadline"]
    for submission in (
        Submission.objects.visible_to(user)
        .historically_sent()
        .filter(matter_id__in=known)
        .values("id", "matter_id", "sent_at")
    ):
        facts.append(
            OpinionFact(
                matter=known[submission["matter_id"]],
                sent_on=timezone.localdate(submission["sent_at"]),
                deadline=answered_by_submission.get(submission["id"]),
                source=SOURCE_SUBMISSION,
            )
        )

    # 2. A request a person ended as answered, with no document.
    for row in ended:
        if row["submission_id"] or is_sync_ending(
            ended_by_id=row["ended_by_id"], note=row["note"], submission_id=row["submission_id"]
        ):
            continue
        facts.append(
            OpinionFact(
                matter=known[row["matter_id"]],
                sent_on=timezone.localdate(row["ended_at"]),
                deadline=row["deadline"],
                source=SOURCE_ANSWERED,
            )
        )

    # 3. The imported register's «välja saadetud».
    native_days = {(fact.matter.matter_id, fact.sent_on) for fact in facts}
    for matter_id, outcome in current_opinion_outcomes(list(known)).items():
        if outcome.outcome != OpinionOutcome.SENT or outcome.sent_on is None:
            continue
        if (matter_id, outcome.sent_on) in native_days:
            # The same act, already counted from Juristid's own record: a
            # person supplied the document for (or answered) the opinion the
            # register says went out that day.
            continue
        facts.append(
            OpinionFact(
                matter=known[matter_id],
                sent_on=outcome.sent_on,
                deadline=outcome.request_deadline,
                source=SOURCE_IMPORTED,
            )
        )
    facts.sort(key=lambda fact: (fact.sent_on, fact.matter.reference, str(fact.matter.matter_id)))
    return facts


def declined_facts(user: Any, matters: dict[Any, MatterFacts] | None = None) -> list[DeclinedFact]:
    """Every request Koda decided not to answer, on a Teema the reader may see.

    Two records hold that decision, both this application's: a request a
    person ended as «otsustati mitte vastata» (`MatterResponseDeadline`,
    ``NOT_ANSWERING``, not the synchronisation's own ending), and the imported
    statement «ei saatnud» (`LovableOpinionOutcome`, current row,
    ``NOT_SENT``). One fact per Teema and day, as for sent opinions.
    """
    from app.legacy_import.lovable_sync import is_sync_ending
    from app.legacy_import.lovable_sync_models import OpinionOutcome, current_opinion_outcomes
    from app.matters.enums import ResponseDeadlineOutcome
    from app.matters.models import MatterResponseDeadline

    known = matters if matters is not None else matter_facts(user)
    if not known:
        return []
    facts: list[DeclinedFact] = []
    seen: set[tuple[Any, date | None]] = set()
    for row in MatterResponseDeadline.objects.filter(
        matter_id__in=known, outcome=ResponseDeadlineOutcome.NOT_ANSWERING
    ).values("matter_id", "deadline", "submission_id", "ended_by_id", "note"):
        if is_sync_ending(
            ended_by_id=row["ended_by_id"], note=row["note"], submission_id=row["submission_id"]
        ):
            continue
        seen.add((row["matter_id"], row["deadline"]))
        facts.append(DeclinedFact(known[row["matter_id"]], row["deadline"], SOURCE_ANSWERED))
    for matter_id, outcome in current_opinion_outcomes(list(known)).items():
        if outcome.outcome != OpinionOutcome.NOT_SENT:
            continue
        when = outcome.request_deadline or known[matter_id].received
        if (matter_id, when) in seen:
            continue
        facts.append(DeclinedFact(known[matter_id], when, SOURCE_IMPORTED))
    return facts


def matter_deadlines(user: Any, matters: dict[Any, MatterFacts]) -> dict[Any, date]:
    """Each Teema's `Arvamuse tähtaeg`: the open one, else the one last ended.

    For «how long was Koda given to answer», which is asked of every Teema
    that had a request — sent, not sent or still open. The open request is
    `Matter.response_deadline`; an ended one is the newest
    `MatterResponseDeadline`; a Teema imported already answered carries it on
    its imported statement (`request_deadline`).
    """
    from app.legacy_import.lovable_sync_models import LovableOpinionOutcome
    from app.matters.models import Matter, MatterResponseDeadline

    if not matters:
        return {}
    out: dict[Any, date] = {}
    for matter_id, deadline in (
        LovableOpinionOutcome.objects.filter(matter_id__in=matters, request_deadline__isnull=False)
        .order_by("matter_id", "recorded_at", "created_at")
        .values_list("matter_id", "request_deadline")
    ):
        if deadline is not None:
            out[matter_id] = deadline
    for matter_id, deadline in (
        MatterResponseDeadline.objects.filter(matter_id__in=matters)
        .order_by("matter_id", "ended_at")
        .values_list("matter_id", "deadline")
    ):
        out[matter_id] = deadline
    for matter_id, deadline in (
        Matter.objects.visible_to(user)
        .filter(pk__in=matters, response_deadline__isnull=False)
        .values_list("pk", "response_deadline")
    ):
        if deadline is not None:
            out[matter_id] = deadline
    return out
