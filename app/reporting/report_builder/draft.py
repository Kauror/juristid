"""The transient report draft: parsed, validated, merged and resolved.

**The draft is the form** (docs/adr/0199 §3). The page carries it as hidden
inputs, checkboxes and text areas, and posts all of it with every request —
preview, reorder, add, remove, refresh, export. Nothing is stored on the
server or in browser storage, and no business text travels in a URL: the
add-search GET carries its search term and nothing else. Leaving the page with
a composed draft asks first (static/js/app.js, «Leaving with text that was
never saved»), because the page is the only place the draft exists.

**What the browser may say, and what it may not.** The form contributes
*choices* — which Matters, in what order, which are included, which events,
which explanations a person typed. It never contributes *facts*: titles,
dates, kinds, recipients and source texts are re-read from the canonical
records as the requesting user on every request (`sources.collect`). A posted
identifier the reader cannot see resolves to nothing.

**The contract, field by field** (`ReportDraft`):

* the applied period (``kehtiv_algus`` / ``kehtiv_lopp``, ISO dates) — the
  period the blocks were composed for — beside the period boxes (``algus`` /
  ``lopp``, Estonian dates) the reader may be editing. They differ until
  «Uuenda ettepanekuid» is reviewed and applied, and an export waits for that;
* the ordered Matter ids (``teema``, repeated): the order *is* the posted
  order, so the up and down buttons and a drag produce the same draft;
* per block: ``kaasa`` (included), ``lisatud`` (added by hand), its event
  refs (``sundmus-<matter>``, ``S|E|W|V`` + uuid), the events left in
  (``sundmus-kaasa-<matter>``), a per-event explanation
  (``selgitus-<matter>-<event>``) with the digest of the source text it was
  rendered from (``allikas-…``), and the block's source fingerprint
  (``sormejalg-<matter>``);
* an explanation equal to the source it was rendered from means «use the
  source»; anything else is the reader's own text, kept exactly.

**Validation never discards a choice.** A malformed id or a duplicate is
dropped *with a notice*; a text over the bound is kept as typed and blocks
only the export; an impossible period keeps the old one and says why.

**Revalidation.** Every render recomputes each block's fingerprint — the
hash of the canonical facts the reader sees — and compares it with the one
posted. A preview names the blocks whose sources changed; an export refuses
while any block changed or became unavailable, and returns the composer with
the reader's draft intact. An unavailable Matter is counted, never named.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from django.utils import timezone

from app.core.dates import format_estonian_date, parse_estonian_date
from app.reporting.report_builder import sources
from app.reporting.report_builder.sources import EventRef, MatterEvents, ReportEvent
from app.workflow.enums import ESTONIAN_MONTHS

TEXT_LIMIT = sources.TEXT_LIMIT
MAX_BLOCKS = 300
MAX_EVENTS = 50
MAX_PERIOD_DAYS = 366
SEARCH_LIMIT = 10
MIN_SEARCH_CHARACTERS = 2

# -- form field names ---------------------------------------------------------
F_START = "algus"
F_END = "lopp"
F_APPLIED_START = "kehtiv_algus"
F_APPLIED_END = "kehtiv_lopp"
F_COMPOSED = "koostatud"
F_ORDER = "teema"
F_INCLUDED = "kaasa"
F_MANUAL = "lisatud"
F_ACTION = "toiming"
F_SEARCH = "otsing"


def events_field(matter_id: object) -> str:
    return f"sundmus-{matter_id}"


def event_included_field(matter_id: object) -> str:
    return f"sundmus-kaasa-{matter_id}"


def text_field(matter_id: object, key: str) -> str:
    return f"selgitus-{matter_id}-{key}"


def source_field(matter_id: object, key: str) -> str:
    return f"allikas-{matter_id}-{key}"


def fingerprint_field(matter_id: object) -> str:
    return f"sormejalg-{matter_id}"


# -- what the reader is told ----------------------------------------------------
BAD_PERIOD = "Kirjuta perioodi algus ja lõpp kujul 1.10.2026."
PERIOD_BACKWARDS = "Perioodi algus peab olema enne lõppu."
PERIOD_TOO_LONG = f"Periood võib olla kuni {MAX_PERIOD_DAYS} päeva."
BAD_APPLIED_PERIOD = "Ülevaate periood oli vigane; ettepanekud koostati uuesti."
BAD_MATTER_ID = "Üks teema viide oli vigane ja jäeti välja."
DUPLICATE_MATTER = "Sama teema oli ülevaates mitu korda; alles jäeti esimene."
TOO_MANY_BLOCKS = f"Ülevaates saab olla kuni {MAX_BLOCKS} teemat."
TOO_MANY_EVENTS = f"Ühe teema all saab olla kuni {MAX_EVENTS} sündmust."
#: A qualifying event recorded after the draft was composed. Never added by
#: itself — the reader decides, through «Uuenda ettepanekuid».
NEW_EVENTS = (
    "Teemal «{title}» on perioodis {count} uut sündmust, mida ülevaates ei ole. "
    "Vajuta «Uuenda ettepanekuid», et need üle vaadata."
)
TEXT_TOO_LONG = f"Selgitus on pikem kui {TEXT_LIMIT} märki. Lühenda seda enne allalaadimist."
ALREADY_ADDED = "See teema on juba ülevaates."
NOT_FOUND = "Seda teemat ei leitud."
UNAVAILABLE = "Mõni varem valitud teema ei ole enam kättesaadav ja jäeti ülevaatest välja."
CHANGED = "Nende teemade allikaandmed on muutunud. Vaata need üle"
PERIOD_PENDING = "Periood on muudetud, kuid ettepanekud on veel eelmise perioodi kohta."
NOTHING_SELECTED = "Ülevaates pole ühtki valitud teemat."
EXPORT_REFUSED = "Wordi faili ei koostatud. Vaata teated üle ja proovi uuesti."


class _PostLike(Protocol):
    def get(self, key: str, default: Any = ...) -> Any: ...
    def getlist(self, key: str) -> list[str]: ...


# ---------------------------------------------------------------------------
# The draft
# ---------------------------------------------------------------------------


@dataclass
class DraftEvent:
    ref: EventRef
    included: bool = True
    #: ``None`` — use the source text. Otherwise the reader's own text, kept
    #: exactly as typed (up to surrounding whitespace).
    text: str | None = None


@dataclass
class DraftBlock:
    matter_id: uuid.UUID
    included: bool = True
    manual: bool = False
    events: list[DraftEvent] = field(default_factory=list)
    #: The fingerprint the page was rendered with; ``None`` for a block built
    #: by this request, which has nothing to be compared with.
    fingerprint: str | None = None


@dataclass
class ReportDraft:
    start: date
    end: date
    start_input: str
    end_input: str
    blocks: list[DraftBlock] = field(default_factory=list)
    composed: bool = False
    search: str = ""
    problems: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)

    def matter_ids(self) -> list[uuid.UUID]:
        return [block.matter_id for block in self.blocks]

    def block(self, matter_id: uuid.UUID) -> DraftBlock | None:
        return next((block for block in self.blocks if block.matter_id == matter_id), None)


def default_period(today: date) -> tuple[date, date]:
    """The previous calendar month — what a monthly report is about."""
    first_of_this = today.replace(day=1)
    end = first_of_this - timedelta(days=1)
    return end.replace(day=1), end


def check_period(start: date | None, end: date | None) -> str:
    """The refusal for a period, or ``""`` for a usable one."""
    if start is None or end is None:
        return BAD_PERIOD
    if start > end:
        return PERIOD_BACKWARDS
    if (end - start).days + 1 > MAX_PERIOD_DAYS:
        return PERIOD_TOO_LONG
    return ""


def period_label(start: date, end: date) -> str:
    """«1.–31. oktoober 2026» for a whole month, else «1.9.2026–15.10.2026»."""
    whole_month = (
        start.day == 1
        and (start.year, start.month) == (end.year, end.month)
        and (end + timedelta(days=1)).day == 1
    )
    if whole_month:
        return f"{start.day}.–{end.day}. {ESTONIAN_MONTHS[start.month - 1]} {start.year}"
    if start == end:
        return format_estonian_date(start)
    return f"{format_estonian_date(start)}–{format_estonian_date(end)}"


def filename(start: date, end: date) -> str:
    """``Majanduspoliitika_ulevaade_2026-10.docx`` for a whole month."""
    if start.day == 1 and (end + timedelta(days=1)).day == 1 and start.month == end.month:
        if start.year == end.year:
            return f"Majanduspoliitika_ulevaade_{start:%Y-%m}.docx"
    return f"Majanduspoliitika_ulevaade_{start.isoformat()}_{end.isoformat()}.docx"


def _proposal_order(found: MatterEvents) -> tuple[date, str, str]:
    """Earliest event, then title, then id: the same order for the same facts."""
    return (found.first_day or date.max, found.title.casefold(), str(found.matter_id))


def _blocks_from(candidates: Iterable[MatterEvents], *, manual: bool = False) -> list[DraftBlock]:
    return [
        DraftBlock(
            matter_id=found.matter_id,
            manual=manual,
            events=[DraftEvent(ref=event.ref) for event in found.events[:MAX_EVENTS]],
        )
        for found in sorted(candidates, key=_proposal_order)
    ][:MAX_BLOCKS]


def fresh(
    user: object,
    start: date,
    end: date,
    *,
    now: datetime | None = None,
    start_input: str = "",
    end_input: str = "",
) -> ReportDraft:
    """The proposals for a period, in their deterministic order."""
    candidates = sources.collect(user, start, end, now=now)
    draft = ReportDraft(
        start=start,
        end=end,
        start_input=start_input or format_estonian_date(start),
        end_input=end_input or format_estonian_date(end),
        blocks=_blocks_from(candidates.values()),
    )
    _note_truncation(draft, candidates.values())
    return draft


def _note_truncation(draft: ReportDraft, candidates: Iterable[MatterEvents]) -> None:
    """Say so when the proposals were cut to the limits — never cut silently."""
    found = list(candidates)
    if len(found) > MAX_BLOCKS:
        _once(draft.notices, TOO_MANY_BLOCKS)
    if any(len(item.events) > MAX_EVENTS for item in found):
        _once(draft.notices, TOO_MANY_EVENTS)


def _iso_day(raw: object) -> date | None:
    try:
        return date.fromisoformat(str(raw or ""))
    except ValueError:
        return None


def _clean_text(raw: str) -> str:
    return raw.replace("\r\n", "\n").replace("\r", "\n").strip()


def parse(
    post: _PostLike,
    user: object,
    *,
    today: date | None = None,
    now: datetime | None = None,
) -> ReportDraft:
    """The draft a request carries — or, with none, the proposals.

    ``post`` is a ``QueryDict`` (POST, or GET for the first visit). A form
    without ``koostatud=1`` has no draft yet: the period boxes, if usable,
    choose the period, and the proposals are built.
    """
    today = today or timezone.localdate()
    start_input = str(post.get(F_START, "") or "").strip()
    end_input = str(post.get(F_END, "") or "").strip()
    search = str(post.get(F_SEARCH, "") or "").strip()[:200]

    if post.get(F_COMPOSED) != "1":
        start, end = parse_estonian_date(start_input), parse_estonian_date(end_input)
        problem = check_period(start, end) if (start_input or end_input) else BAD_PERIOD
        if problem or start is None or end is None:
            start, end = default_period(today)
            draft = fresh(user, start, end, now=now)
            if start_input or end_input:
                draft.problems.append(problem)
                draft.start_input, draft.end_input = start_input, end_input
        else:
            draft = fresh(user, start, end, now=now, start_input=start_input, end_input=end_input)
        draft.search = search
        return draft

    applied_start = _iso_day(post.get(F_APPLIED_START))
    applied_end = _iso_day(post.get(F_APPLIED_END))
    problems: list[str] = []
    if check_period(applied_start, applied_end) or applied_start is None or applied_end is None:
        # Nobody types into these; a broken one was edited. Rebuild rather
        # than guess which blocks belonged to which period.
        start, end = default_period(today)
        draft = fresh(user, start, end, now=now)
        draft.problems.append(BAD_APPLIED_PERIOD)
        draft.search = search
        return draft

    draft = ReportDraft(
        start=applied_start,
        end=applied_end,
        start_input=start_input,
        end_input=end_input,
        composed=True,
        search=search,
        problems=problems,
    )
    draft.blocks = _parse_blocks(post, problems)
    return draft


def _parse_blocks(post: _PostLike, problems: list[str]) -> list[DraftBlock]:
    raw_ids = post.getlist(F_ORDER)
    if len(raw_ids) > MAX_BLOCKS:
        problems.append(TOO_MANY_BLOCKS)
        raw_ids = raw_ids[:MAX_BLOCKS]
    included = set(post.getlist(F_INCLUDED))
    manual = set(post.getlist(F_MANUAL))

    blocks: list[DraftBlock] = []
    seen: set[uuid.UUID] = set()
    for raw in raw_ids:
        try:
            matter_id = uuid.UUID(str(raw))
        except ValueError:
            _once(problems, BAD_MATTER_ID)
            continue
        if matter_id in seen:
            _once(problems, DUPLICATE_MATTER)
            continue
        seen.add(matter_id)
        blocks.append(
            DraftBlock(
                matter_id=matter_id,
                included=str(matter_id) in included,
                manual=str(matter_id) in manual,
                events=_parse_events(post, matter_id, problems),
                fingerprint=str(post.get(fingerprint_field(matter_id), "") or ""),
            )
        )
    return blocks


def _parse_events(post: _PostLike, matter_id: uuid.UUID, problems: list[str]) -> list[DraftEvent]:
    raw_refs = post.getlist(events_field(matter_id))
    if len(raw_refs) > MAX_EVENTS:
        _once(problems, TOO_MANY_EVENTS)
        raw_refs = raw_refs[:MAX_EVENTS]
    included = set(post.getlist(event_included_field(matter_id)))
    events: list[DraftEvent] = []
    seen: set[EventRef] = set()
    for raw in raw_refs:
        ref = EventRef.parse(raw)
        if ref is None or ref in seen:
            continue
        seen.add(ref)
        posted = post.get(text_field(matter_id, ref.key))
        text: str | None = None
        if posted is not None:
            typed = _clean_text(str(posted))
            source_digest = str(post.get(source_field(matter_id, ref.key), "") or "")
            if sources.text_digest(typed) != source_digest:
                text = typed
        events.append(DraftEvent(ref=ref, included=ref.key in included, text=text))
    return events


def _once(problems: list[str], message: str) -> None:
    if message not in problems:
        problems.append(message)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


@dataclass
class RefreshReview:
    """What «Uuenda ettepanekuid» would change, shown before it is applied."""

    start: date
    end: date
    added: list[str]
    removed: list[str]
    dropped_texts: int

    @property
    def label(self) -> str:
        return period_label(self.start, self.end)

    @property
    def changes_anything(self) -> bool:
        return bool(self.added or self.removed or self.dropped_texts)


def parse_action(raw: object) -> tuple[str, uuid.UUID | None]:
    """``ules:<uuid>`` → ``("ules", uuid)``; ``eelvaade`` → ``("eelvaade", None)``."""
    verb, _, rest = str(raw or "").partition(":")
    if not rest:
        return verb, None
    try:
        return verb, uuid.UUID(rest)
    except ValueError:
        return "", None


def apply_action(
    draft: ReportDraft, raw_action: object, user: object, *, now: datetime | None = None
) -> RefreshReview | None:
    """Change ``draft`` as one button asked. Returns a review for «uuenda»."""
    verb, matter_id = parse_action(raw_action)
    if verb in ("ules", "alla") and matter_id is not None:
        move(draft, matter_id, -1 if verb == "ules" else 1)
    elif verb == "eemalda" and matter_id is not None:
        draft.blocks = [block for block in draft.blocks if block.matter_id != matter_id]
    elif verb == "lisa" and matter_id is not None:
        add(draft, matter_id, user, now=now)
        draft.search = ""
    elif verb == "uuenda":
        return review_refresh(draft, user, now=now)
    elif verb == "rakenda":
        apply_refresh(draft, user, now=now)
    elif verb == "loobu":
        draft.start_input = format_estonian_date(draft.start)
        draft.end_input = format_estonian_date(draft.end)
    return None


def move(draft: ReportDraft, matter_id: uuid.UUID, step: int) -> None:
    """One place up (``-1``) or down (``+1``); at either end, nothing."""
    ids = draft.matter_ids()
    if matter_id not in ids:
        return
    index = ids.index(matter_id)
    target = index + step
    if 0 <= target < len(draft.blocks):
        draft.blocks[index], draft.blocks[target] = draft.blocks[target], draft.blocks[index]


def add(
    draft: ReportDraft, matter_id: uuid.UUID, user: object, *, now: datetime | None = None
) -> None:
    """«Lisa teema»: a readable Matter, once, with whatever it did in the period.

    A topic with nothing in the period is still added — the reader chose it —
    and has no event rows and no date: nothing is invented to fill the row.
    """
    if draft.block(matter_id) is not None:
        draft.notices.append(ALREADY_ADDED)
        return
    if len(draft.blocks) >= MAX_BLOCKS:
        draft.notices.append(TOO_MANY_BLOCKS)
        return
    if not sources.readable_matters(user).filter(pk=matter_id).exists():
        draft.notices.append(NOT_FOUND)
        return
    found = sources.collect(user, draft.start, draft.end, now=now, matter_ids=[matter_id])
    events = found[matter_id].events if matter_id in found else []
    if len(events) > MAX_EVENTS:
        _once(draft.notices, TOO_MANY_EVENTS)
    draft.blocks.append(
        DraftBlock(
            matter_id=matter_id,
            manual=True,
            events=[DraftEvent(ref=event.ref) for event in events[:MAX_EVENTS]],
        )
    )


def _requested_period(draft: ReportDraft) -> tuple[date, date] | None:
    start = parse_estonian_date(draft.start_input)
    end = parse_estonian_date(draft.end_input)
    problem = check_period(start, end)
    if problem or start is None or end is None:
        _once(draft.problems, problem or BAD_PERIOD)
        return None
    return start, end


def review_refresh(
    draft: ReportDraft, user: object, *, now: datetime | None = None
) -> RefreshReview | None:
    """What applying the period boxes would add, remove and lose."""
    period = _requested_period(draft)
    if period is None:
        return None
    start, end = period
    candidates = sources.collect(user, start, end, now=now)
    in_draft = set(draft.matter_ids())
    titles = dict(
        sources.readable_matters(user).filter(pk__in=list(in_draft)).values_list("pk", "title")
    )
    added = [
        found.title
        for found in sorted(candidates.values(), key=_proposal_order)
        if found.matter_id not in in_draft
    ]
    removed = [
        titles[block.matter_id]
        for block in draft.blocks
        if not block.manual and block.matter_id not in candidates and block.matter_id in titles
    ]
    dropped = 0
    for block in draft.blocks:
        kept = (
            {event.ref for event in candidates[block.matter_id].events}
            if block.matter_id in candidates
            else set()
        )
        dropped += sum(
            1 for event in block.events if event.text is not None and event.ref not in kept
        )
    return RefreshReview(start=start, end=end, added=added, removed=removed, dropped_texts=dropped)


def apply_refresh(draft: ReportDraft, user: object, *, now: datetime | None = None) -> None:
    """Move the draft to the period in the boxes, keeping what the reader did.

    Hand-added topics stay, with whatever they did in the new period. Proposed
    topics stay where the reader put them while they still have events; those
    with none leave (the review listed them). New proposals join at the end,
    in the deterministic order. An explanation the reader typed stays with its
    event for as long as the event is in the period; inclusion choices too.
    """
    period = _requested_period(draft)
    if period is None:
        return
    start, end = period
    candidates = sources.collect(user, start, end, now=now)
    blocks: list[DraftBlock] = []
    for block in draft.blocks:
        found = candidates.get(block.matter_id)
        if found is None and not block.manual:
            continue
        previous = {event.ref: event for event in block.events}
        events = []
        for event in (found.events if found else [])[:MAX_EVENTS]:
            before = previous.get(event.ref)
            events.append(
                DraftEvent(
                    ref=event.ref,
                    included=before.included if before else True,
                    text=before.text if before else None,
                )
            )
        blocks.append(
            DraftBlock(
                matter_id=block.matter_id,
                included=block.included,
                manual=block.manual,
                events=events,
            )
        )
    present = {block.matter_id for block in blocks}
    blocks.extend(
        _blocks_from(found for found in candidates.values() if found.matter_id not in present)
    )
    if len(blocks) > MAX_BLOCKS:
        _once(draft.problems, TOO_MANY_BLOCKS)
    draft.blocks = blocks[:MAX_BLOCKS]
    draft.start, draft.end = start, end
    draft.start_input, draft.end_input = format_estonian_date(start), format_estonian_date(end)


# ---------------------------------------------------------------------------
# The composed report — one representation for the preview and the DOCX
# ---------------------------------------------------------------------------


@dataclass
class ComposedEvent:
    event: ReportEvent
    included: bool
    text: str
    edited: bool
    too_long: bool

    @property
    def key(self) -> str:
        return self.event.ref.key

    @property
    def source_digest(self) -> str:
        return sources.text_digest(self.event.source_text)


@dataclass
class ComposedBlock:
    matter_id: uuid.UUID
    title: str
    included: bool
    manual: bool
    events: list[ComposedEvent]
    fingerprint: str
    changed: bool

    @property
    def exported_events(self) -> list[ComposedEvent]:
        return [event for event in self.events if event.included]


@dataclass(frozen=True)
class TableRow:
    """One row of the report table, exactly as the preview and the DOCX print it."""

    topic: str
    #: How many rows the topic cell spans; 0 on a row the cell above covers.
    topic_span: int
    date_label: str
    kind_label: str
    provenance: str
    recipients: str
    text: str


@dataclass
class ComposedReport:
    start: date
    end: date
    start_input: str
    end_input: str
    blocks: list[ComposedBlock]
    problems: list[str]
    notices: list[str]
    unavailable: int
    search: str = ""
    review: RefreshReview | None = None

    @property
    def period_label(self) -> str:
        return period_label(self.start, self.end)

    @property
    def filename(self) -> str:
        return filename(self.start, self.end)

    @property
    def changed_titles(self) -> list[str]:
        return [block.title for block in self.blocks if block.changed]

    @property
    def exported_blocks(self) -> list[ComposedBlock]:
        return [block for block in self.blocks if block.included]

    @property
    def selected_count(self) -> int:
        return len(self.exported_blocks)

    @property
    def period_pending(self) -> bool:
        return (
            parse_estonian_date(self.start_input) != self.start
            or parse_estonian_date(self.end_input) != self.end
        )

    @property
    def text_too_long(self) -> bool:
        return any(event.too_long for block in self.blocks for event in block.events)

    def refusals(self) -> list[str]:
        """Why this draft may not be exported now; empty when it may."""
        reasons = list(self.problems)
        if self.unavailable:
            reasons.append(UNAVAILABLE)
        if self.changed_titles:
            reasons.append(f"{CHANGED}: {', '.join(self.changed_titles)}.")
        if self.period_pending:
            reasons.append(PERIOD_PENDING)
        if self.text_too_long:
            reasons.append(TEXT_TOO_LONG)
        if not self.exported_blocks:
            reasons.append(NOTHING_SELECTED)
        return reasons

    def table_rows(self) -> list[TableRow]:
        """The report table: a row per included event, the topic spanning them.

        A topic with no included event is one row with the title alone.
        """
        rows: list[TableRow] = []
        for block in self.exported_blocks:
            events = block.exported_events
            if not events:
                rows.append(TableRow(block.title, 1, "", "", "", "", ""))
                continue
            for index, item in enumerate(events):
                rows.append(
                    TableRow(
                        topic=block.title,
                        topic_span=len(events) if index == 0 else 0,
                        date_label=item.event.date_label,
                        kind_label=item.event.kind_label,
                        provenance=item.event.provenance,
                        recipients=item.event.recipients_label,
                        text=item.text,
                    )
                )
        return rows


def fingerprint(title: str, events: Iterable[ReportEvent], pending: Iterable[str] = ()) -> str:
    """The hash of the canonical facts a block shows, and of the ones it does not yet.

    ``pending`` are the refs of qualifying events in the period that the draft
    does not hold — recorded after the draft was composed. They are part of the
    hash so that their arrival is a change the export stops on.
    """
    payload = json.dumps(
        [title, [list(event.fingerprint_parts()) for event in events], sorted(pending)],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def compose(
    draft: ReportDraft,
    user: object,
    *,
    now: datetime | None = None,
    review: RefreshReview | None = None,
) -> ComposedReport:
    """Re-resolve every block of ``draft`` as ``user`` and assemble the report.

    A Matter the reader can no longer read leaves the report and is counted.
    An event that no longer qualifies — moved out of the period, withdrawn
    into a draft, removed, restricted — leaves its block, and the block's
    fingerprint says the block changed.
    """
    ids = draft.matter_ids()
    titles: Mapping[uuid.UUID, str] = dict(
        sources.readable_matters(user).filter(pk__in=ids).values_list("pk", "title")
    )
    candidates = sources.collect(user, draft.start, draft.end, now=now, matter_ids=list(titles))
    blocks: list[ComposedBlock] = []
    notices = list(draft.notices)
    unavailable = 0
    for block in draft.blocks:
        title = titles.get(block.matter_id)
        if title is None:
            unavailable += 1
            continue
        available = {
            event.ref: event
            for event in (
                candidates[block.matter_id].events if block.matter_id in candidates else []
            )
        }
        composed_events = []
        for item in block.events:
            event = available.get(item.ref)
            if event is None:
                continue
            text = event.source_text if item.text is None else item.text
            composed_events.append(
                ComposedEvent(
                    event=event,
                    included=item.included,
                    text=text,
                    edited=item.text is not None and item.text != event.source_text,
                    too_long=len(text) > TEXT_LIMIT,
                )
            )
        held = {item.ref for item in block.events}
        pending = [str(ref) for ref in available if ref not in held]
        if pending:
            notices.append(NEW_EVENTS.format(title=title, count=len(pending)))
        facts = fingerprint(title, [item.event for item in composed_events], pending)
        blocks.append(
            ComposedBlock(
                matter_id=block.matter_id,
                title=title,
                included=block.included,
                manual=block.manual,
                events=composed_events,
                fingerprint=facts,
                changed=block.fingerprint is not None and block.fingerprint != facts,
            )
        )
    return ComposedReport(
        start=draft.start,
        end=draft.end,
        start_input=draft.start_input or format_estonian_date(draft.start),
        end_input=draft.end_input or format_estonian_date(draft.end),
        blocks=blocks,
        problems=list(draft.problems),
        notices=notices,
        unavailable=unavailable,
        search=draft.search,
        review=review,
    )
