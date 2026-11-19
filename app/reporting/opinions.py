"""Sent opinions, as one list read from Juristid's own database (owner, 2026-11-16).

Every count of opinions Koda sent — Statistika's tabs, Osakond, Minu asjad
(`reporting.selectors.sent_opinions`, docs/adr/0214 §6) — and how many went
out, when, by whom, against which deadline, reads :func:`opinion_facts` and
nothing else. It is the **one reader**: the only place that knows where a sent
opinion is recorded. The lists of `Koja arvamus` documents (recipients, kind,
file, the CSV) stay on `Submission` and say so.

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
2) within seven days — a person supplying the document for an opinion the
register says went out, or a historical letter whose own date sits a few days
from the register's (docs/adr/0216 §5). Further apart they are different
opinions.

**Authorization.** Facts are read only for Teemad the reader may see
(`Matter.objects.visible_to`), so a restricted or deleted Teema contributes
nothing and an administrator without a business role reads an empty list
(docs/adr/0181).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from django.utils import timezone

#: The legal-instrument keys that make a Teema «otseselt ELiga seotud».
#: Two records of a sent opinion on one Teema this many days apart are one act
#: (owner, 2026-11-17; docs/adr/0216 §5).
SAME_ACT_DAYS = 7

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
    #: The current owner's id, for counts per person (Osakond, Minu asjad).
    owner_id: Any = None


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
    """Every real Teema the reader may see, as :class:`MatterFacts`. Four queries.

    Real data only, as every published statistic (`Matter.real_data`,
    `reporting.selectors.base.visible_matters`): a TEST Teema is never counted.
    """
    from app.matters.models import Matter, MatterSourceOrganisation
    from app.workflow.enums import Track

    matters = list(
        Matter.objects.visible_to(user)
        .real_data()
        .select_related("owner")
        .only(
            "id",
            "title",
            "reference_year",
            "reference_number",
            "received_date",
            "track",
            "former_owner_name",
            "owner_id",
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
            owner_id=m.owner_id,
        )
    return facts


def opinion_facts(user: Any, matters: dict[Any, MatterFacts] | None = None) -> list[OpinionFact]:
    """Every opinion Koda sent on a Teema the reader may see, oldest first."""
    from app.legacy_import.lovable_sync import is_sync_ending
    from app.legacy_import.lovable_sync_models import OpinionOutcome, current_opinion_outcomes
    from app.matters.enums import ResponseDeadlineOutcome
    from app.matters.models import MatterResponseDeadline
    from app.submissions.enums import NOT_AN_OPINION_KINDS
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
        # An official «no comments» answer is a send, not an opinion: it is
        # counted on its own (docs/adr/0174 §4). A withdrawn opinion still went
        # out on its day (docs/adr/0092 §3) and stays counted.
        .exclude(kind__in=NOT_AN_OPINION_KINDS)
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
    native: dict[Any, list[int]] = {}
    for index, fact in enumerate(facts):
        native.setdefault(fact.matter.matter_id, []).append(index)
    for matter_id, outcome in current_opinion_outcomes(list(known)).items():
        if outcome.outcome != OpinionOutcome.SENT or outcome.sent_on is None:
            continue
        sent_on: date = outcome.sent_on
        same = [
            index
            for index in native.get(matter_id, [])
            if abs((facts[index].sent_on - sent_on).days) <= SAME_ACT_DAYS
        ]
        if same:
            # The same act, already counted from Juristid's own record: a
            # person supplied the document for (or answered) the opinion the
            # register says went out, or a historical letter's own date sits a
            # few days from the register's (owner, 2026-11-17: within seven
            # days it is one opinion; docs/adr/0216 §5). The native fact keeps
            # the register's request deadline when it has none of its own, so
            # on-time and days-given figures lose nothing by the merge.
            index = min(same, key=lambda i: abs((facts[i].sent_on - sent_on).days))
            if facts[index].deadline is None and outcome.request_deadline is not None:
                facts[index] = replace(facts[index], deadline=outcome.request_deadline)
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


def sent_between(
    facts: list[OpinionFact], since: date, until: date | None = None, *, owner_id: Any = None
) -> list[OpinionFact]:
    """The facts sent from ``since`` to ``until`` (inclusive), optionally on one person's Teemad."""
    return [
        fact
        for fact in facts
        if fact.sent_on >= since
        and (until is None or fact.sent_on <= until)
        and (owner_id is None or fact.matter.owner_id == owner_id)
    ]


def rows_url(since: date, until: date | None = None, *, jurist: str = "") -> str:
    """Statistika's list of exactly the opinions sent between two days.

    `reporting:opinion_rows` accepts days from :data:`opinion_stats.FIRST_YEAR`
    to today, so the window is clamped to that span.
    """
    from urllib.parse import urlencode

    from django.urls import reverse

    from app.reporting.opinion_stats import FIRST_YEAR

    today = timezone.localdate()
    start = max(since, date(FIRST_YEAR, 1, 1))
    end = min(until or today, today)
    params = {"periood": "vali", "alates": start.isoformat(), "kuni": end.isoformat()}
    if jurist:
        params["jurist"] = jurist
    return f"{reverse('reporting:opinion_rows')}?{urlencode(params)}"
