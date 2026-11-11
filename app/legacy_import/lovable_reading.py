"""The machine reader: one Lovable source topic → one CREATE package topic.

The reviewed packages of 0191/0202/0203 were built outside the repository: a
person read every topic and wrote the reading into the package. Two callers now
need the same reading without that person in the loop — the nightly
synchronisation's new topics (docs/adr/0213, brief 2026-11-09 §6 B2) and the
historical backfill (brief §5 A4). This module is that reading, and it is
deliberately **poorer** than a reviewer's: it writes only what an exact,
deterministic rule can read, and holds everything else for a person
(``HELD_FOR_REVIEW`` in ``field_decisions``, the reason in ``review_notes``).
An accurate minimal Teema beats an invented complete one (docs/adr/0202 §2).

The rules, each one exact and tested (`tests/test_ls_b_reading.py`):

* **No step is ever read from `jargmiseks`.** The free-text next step is held,
  with its day, on every open topic: its DO/WAIT/MONITOR kind is a person's
  call (docs/adr/0157 §3, 0192 §5). The one current step written is the
  drafting step docs/adr/0148 §10 and 0191 §3 derive from *structured* fields
  only — an open request (`arvamuse_tahtaeg`), nothing sent (`valja` and
  `valja_kuupaev` blank), the day not before `as_of`, not a Chamber
  initiative — as DO «Koostan arvamuse» on that day.
* **«ei saatnud» is never SENT** (docs/adr/0202 §2): on a live stage the topic
  stays open and `valja` is held; it never concludes anything by itself.
* **A past opinion deadline never means sent**: with nothing sent it is held
  (never an overdue obligation) and kept as source only.
* **Estimates stay estimates**: «Eeldatava …» becomes an estimated
  commencement (an important date, never work); a known commencement
  (`KNOWN_DATE`) only on an adopted act (`ADOPTED_STAGE_LABELS`).
* **Stage only through `STAGE_MAPPING`**: an explicit dict of Lovable's exact
  `hetkeseis` labels; no similarity, no case folding beyond whitespace. An
  unknown label is held and the Teema has no stage.
* **Terminal and continuing topics are created closed** exactly as 0202 §1
  creates finished ones: ARCHIVE, no `closed_at`, no closure event (the
  writer's `create_imported_matter(is_open=False, …)`), nothing current — no
  step, no open request, no written commencement.
* **A closure reason only where the source states one** (`closure_reading`,
  reader version 2): Lovable's own status in `CLOSURE_BY_STATUS` («jõustunud»
  → COMPLETED, «rohkem ei tegele» / «rohkem pole tegevusi plaanis» →
  MONITORING_STOPPED), or its structured stage link to a successor →
  SUPERSEDED. Nothing else is a reason: being outside Lovable's operational
  scope does not say Koda stopped, «ei saatnud» does not say no position was
  formed (it is kept as the source's statement). With no stated reason a
  historical topic is closed ARCHIVE with a **blank** disposition
  (`matters_closure_fields_consistent` allows exactly that); an operational
  one is refused (`CLOSURE_REASON_UNKNOWN`) and stays with a person.
* **`historical=True` is always closed** the same way, whatever the stage.

`read_topic` returns one format-2 ``action: "CREATE"`` topic that
`lovable_pilot.validate_package` accepts inside a format-2 package, or raises
`ReadingRefused` with a code when no honest topic can be written (an open topic
whose owner has no reviewed person, an empty title, a malformed reference).

Reads nothing but its arguments; writes nothing.
"""

from __future__ import annotations

import datetime as dt
import unicodedata
from collections.abc import Mapping
from typing import Any

#: Bumped whenever a reading rule changes; recorded on every topic it reads
#: (``topic["reading"]``) and in the provenance of what the synchronisation creates.
#: Version 2 (2026-11-09, LS2-A): a closure reason is read only from the source's
#: own status or stage link (`closure_reading`) — never from the operational scope
#: or «ei saatnud» — and a historical topic's stage link is read too.
READER_VERSION = "lovable-reading/2"

#: Lovable's `hetkeseis` labels, **exactly as Lovable writes them**, to the
#: Juristid stage vocabulary (`workflow.StageVocabulary.key`). Each pair is the
#: department's reviewed vocabulary (`app.workflow.vocabulary`
#: `RAW_LABEL_TO_STAGE` and `CURRENT_LABEL_TO_STAGE`, the readings 0148 §2 and
#: 0131 §9 approved), copied here so that a change there never silently changes
#: what the machine reads:
#:
#: ===================================  ========================  ====================
#: Lovable label                        stage                     ends the work?
#: ===================================  ========================  ====================
#: idee                                 idea                      no
#: kooskõlastusringil                   consultation              no
#: valitsuses                           government                no
#: Riigikogus                           parliament                no
#: ootan jõustumist                     awaiting_entry            no
#: jõustumise ootel                     awaiting_entry            no (0131 §10)
#: jõustunud                            in_force                  **yes** (0131 §10)
#: Eesti seisukoht                      estonian_eu_position      no
#: Eesti seisukoht koostamisel          estonian_eu_position      no
#: ELi menetluses                       eu_procedure              no
#: ootan ELi õiguse ülevõtmist          awaiting_transposition    no
#: ELi õiguse ülevõtmise ootel          awaiting_transposition    no
#: muu                                  other                     no
#: rohkem ei tegele                     monitoring_stopped        **yes** (0131 §10)
#: ===================================  ========================  ====================
#:
#: «rohkem pole tegevusi plaanis» is a disposition, not a stage (0131 §9), and
#: «lõpetatud» has no stage: both are held.
STAGE_MAPPING: dict[str, str] = {
    "idee": "idea",
    "kooskõlastusringil": "consultation",
    "valitsuses": "government",
    "Riigikogus": "parliament",
    "ootan jõustumist": "awaiting_entry",
    "jõustumise ootel": "awaiting_entry",
    "jõustunud": "in_force",
    "Eesti seisukoht": "estonian_eu_position",
    "Eesti seisukoht koostamisel": "estonian_eu_position",
    "ELi menetluses": "eu_procedure",
    "ootan ELi õiguse ülevõtmist": "awaiting_transposition",
    "ELi õiguse ülevõtmise ootel": "awaiting_transposition",
    "muu": "other",
    "rohkem ei tegele": "monitoring_stopped",
}
#: The labels whose stage ends the work: a Teema at them is closed in Juristid
#: (docs/adr/0131 §10), so the machine never moves a Teema to one (0202 §3).
TERMINAL_STAGE_LABELS = frozenset({"jõustunud", "rohkem ei tegele"})
#: The stage keys of those labels.
TERMINAL_STAGES = frozenset(STAGE_MAPPING[label] for label in TERMINAL_STAGE_LABELS)
#: An act is adopted at these labels: only there is a «Kindla kuupäevaga»
#: commencement a known one (docs/adr/0120).
ADOPTED_STAGE_LABELS = frozenset({"jõustumise ootel", "ootan jõustumist", "jõustunud"})

#: **Why a closed topic's work ended — only what Lovable's own `hetkeseis` says**,
#: as (lifecycle, `Disposition`). Compared after collapsing whitespace and case,
#: exactly as the importer reads a terminal status
#: (`lovable_pilot._validate_active_topic`); no diacritic folding, no similarity.
#: Each pair is a reading the product already makes:
#:
#: * «jõustunud» → COMPLETED: the stage `in_force` closes a Teema with it
#:   (`workflow.stage_flow.TERMINAL_DISPOSITIONS`); it reads «Jõustus või töö
#:   lõppes», work status «Mitteaktiivne» — no Chamber decision is asserted.
#: * «rohkem ei tegele» → MONITORING_STOPPED: the stage `monitoring_stopped`
#:   closes with it (the same table) — Koda's own words.
#: * «rohkem pole tegevusi plaanis» → MONITORING_STOPPED: the reviewed
#:   vocabulary's disposition-only label (`workflow.vocabulary`
#:   `RAW_LABEL_TO_DISPOSITION`, docs/adr/0131 §9) — Koda's own words again.
#:
#: Not here, deliberately: «lõpetatud» (who ended what is not stated; an owner
#: decision), every stage that does not end the work, the operational scope and
#: «ei saatnud». Their topics get no closure reason.
CLOSURE_BY_STATUS: dict[str, tuple[str, str]] = {
    "jõustunud": ("INACTIVE", "COMPLETED"),
    "rohkem ei tegele": ("CONCLUDED", "MONITORING_STOPPED"),
    "rohkem pole tegevusi plaanis": ("CONCLUDED", "MONITORING_STOPPED"),
}
#: Where a closed topic's reason came from (`closure_reading`); recorded in its
#: review notes (kept in `MatterSourceReference.source_row_raw`) and counted by
#: the backfill report.
CLOSURE_CONTINUATION = "CONTINUATION"
CLOSURE_SOURCE_STATUS = "SOURCE_STATUS"
CLOSURE_UNKNOWN = "UNKNOWN"
#: A closed topic with no stated reason: «Mitteaktiivne», blank disposition
#: (`app.matters.work_status.NOT_A_CHAMBER_DECISION` holds "").
UNKNOWN_CLOSURE = ("INACTIVE", "")

NOT_SENT = "ei saatnud"
#: Lovable's commencement kinds (`topic_commencement.liik`), by the word they
#: start with, and what each may become. Only a kind listed here is written.
COMMENCEMENT_GENERAL = "Üldises"  # «Üldises korras»: an effective date with no day
COMMENCEMENT_FIXED = "Kindla"  # «Kindla kuupäevaga»: planned, known only once adopted
COMMENCEMENT_ESTIMATED = "Eeldatava"  # «Eeldatava kuupäevaga»: an estimate, never work

#: Why something was held or left out: codes first, so a reader can count them.
HELD_STEP = "STEP_HELD"
HELD_STAGE = "STAGE_UNMAPPED"
HELD_NOT_SENT = "NOT_SENT_ON_LIVE_STAGE"
HELD_PAST_DEADLINE = "OPINION_DEADLINE_PASSED_NOTHING_SENT"
HELD_INSTRUMENT = "INSTRUMENT_UNMAPPED"
HELD_RECEIVED = "RECEIVED_AFTER_AS_OF"
HELD_COMMENCEMENT = "COMMENCEMENT_UNREADABLE"
HELD_LINK = "LINK_NOT_HTTPS"


class ReadingRefused(Exception):
    """No honest CREATE topic can be written for this source topic. ``code`` says why."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _text(raw: Any) -> str:
    return " ".join(unicodedata.normalize("NFC", str(raw or "")).split())


def _key(raw: Any) -> str:
    return _text(raw).lower().translate(str.maketrans("õäöüšž", "oaousz"))


def _day(raw: Any) -> dt.date | None:
    if not raw:
        return None
    try:
        return dt.date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _wording(raw: Any) -> str:
    return _text(raw).casefold().strip(" .,;:!?…\"'«»„“”")


def operative_owner(row: Mapping[str, Any]) -> str:
    """Lovable's `topic_operative_vastutaja`: the new owner if named, else the owner."""
    new = _text(row.get("uus_vastutaja"))
    if new:
        return "" if _key(new).rstrip(".") == "vastutaja puudub" else new
    return _text(row.get("vastutaja"))


def stage_for(label: Any) -> str:
    """The stage key of an exact Lovable label (whitespace collapsed), or ``""``."""
    return STAGE_MAPPING.get(_text(label), "")


def activity(bundle: Mapping[str, Any]) -> str:
    """Lovable's own «ongoing» (docs/adr/0202 §6): ONGOING, FINISHED, CONTINUES or
    NOT_OPERATIONAL. The same rule as `lovable_sync.source_activity`."""
    row = bundle.get("row") or {}
    if not row.get("operatiivne_ulatus"):
        return "NOT_OPERATIONAL"
    if _key(row.get("hetkeseis")) in {"joustunud", "rohkem ei tegele"}:
        return "FINISHED"
    me = str(row.get("id"))
    if any(str(link.get("prev")) == me for link in bundle.get("stage_links") or []):
        return "CONTINUES"
    return "ONGOING"


def commencement_reading(record: Mapping[str, Any], *, status: str) -> dict[str, Any]:
    """One Lovable commencement → what it may become, or why it is held."""
    liik = _text(record.get("liik"))
    day = _day(record.get("kuupaev"))
    if liik.startswith(COMMENCEMENT_GENERAL):
        return {"target": "MatterEffectiveDate", "kind": "GENERAL_ORDER", "date": None}
    if liik.startswith(COMMENCEMENT_FIXED) and day is not None:
        if status in ADOPTED_STAGE_LABELS:
            return {"target": "MatterEffectiveDate", "kind": "KNOWN_DATE", "date": day.isoformat()}
        return {
            "target": "MatterImportantDate",
            "kind": "PLANNED_COMMENCEMENT",
            "date": day.isoformat(),
        }
    if liik.startswith(COMMENCEMENT_ESTIMATED) and day is not None:
        return {
            "target": "MatterImportantDate",
            "kind": "ESTIMATED_COMMENCEMENT",
            "date": day.isoformat(),
        }
    return {"target": "", "kind": "", "date": None}


def closure_reading(bundle: Mapping[str, Any]) -> tuple[str, str, str]:
    """(lifecycle, disposition, basis) of a topic created closed: only what the
    source states (`CLOSURE_BY_STATUS`, then a structured stage link to a
    successor), else `UNKNOWN_CLOSURE` with basis `CLOSURE_UNKNOWN`.

    The status comes first, as in `activity`: a topic in force that also
    continues elsewhere is FINISHED there too. Never read: the operational
    scope, `valja` («ei saatnud» included), `jargmiseks`, any date."""
    row = bundle.get("row") or {}
    stated = CLOSURE_BY_STATUS.get(_text(row.get("hetkeseis")).casefold())
    if stated is not None:
        return stated[0], stated[1], CLOSURE_SOURCE_STATUS
    me = str(row.get("id"))
    if any(str(link.get("prev")) == me for link in bundle.get("stage_links") or []):
        return "CONTINUES", "SUPERSEDED", CLOSURE_CONTINUATION
    return UNKNOWN_CLOSURE[0], UNKNOWN_CLOSURE[1], CLOSURE_UNKNOWN


def _source(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """The source as read, cut to the synchronisation's own allowlist: never
    `markmed`, `mitteametlik_arvamus`, identity or raw import columns."""
    from app.legacy_import.lovable_source import TABLES, TOPIC_COLUMNS

    row = bundle.get("row") or {}

    def cut(items: Any, table: str) -> list[dict[str, Any]]:
        return [{c: item.get(c) for c in TABLES[table] if c in item} for item in items or []]

    return {
        "id": str(row.get("id")),
        "version": row.get("version"),
        "updated_at": row.get("updated_at"),
        "row": {c: row.get(c) for c in TOPIC_COLUMNS if c in row},
        "excel_source_row": None,
        "history": [],
        "commencements": cut(bundle.get("commencements"), "topic_commencement"),
        "responsible_changes": cut(bundle.get("responsible_changes"), "topic_responsible_change"),
        "stage_links": [
            {"prev": str(link.get("prev")), "next": str(link.get("next"))}
            for link in bundle.get("stage_links") or []
        ],
    }


def read_topic(
    bundle: Mapping[str, Any],
    *,
    as_of: dt.date,
    people: Mapping[str, str],
    historical: bool,
) -> dict[str, Any]:
    """One source bundle (``{"row", "commencements", "responsible_changes",
    "stage_links"}`` as `lovable_source` reads it, optionally ``"references"``:
    Lovable id → ``nr`` for its stage-link partners) → one CREATE topic.

    ``people`` maps a Lovable owner name to the display name of exactly one
    Juristid account (the package's ``people``). ``as_of`` is the reading's day
    (Europe/Tallinn): what is before it is history, never current work."""
    from app.workflow.services import OPINION_PREPARATION_TEXT

    row = bundle.get("row") or {}
    reference = _text(row.get("nr"))
    parts = reference.split("_")
    if len(parts) != 2 or not (parts[0].isdigit() and parts[1].isdigit()) or parts[1][0] == "0":
        raise ReadingRefused("BAD_REFERENCE", "The source topic's nr is not YYYY_N.")
    if not row.get("id"):
        raise ReadingRefused("NO_IDENTITY", "The source topic has no Lovable id.")
    title = _text(row.get("teema"))
    if not title:
        raise ReadingRefused("NO_TITLE", "The source topic has no title; none is invented.")

    act = activity(bundle)
    if act == "NOT_OPERATIONAL" and not historical:
        raise ReadingRefused(
            "NOT_OPERATIONAL", "A topic outside the operational scope is read only as historical."
        )
    active = act == "ONGOING" and not historical
    status = _text(row.get("hetkeseis"))
    owner_raw = operative_owner(row)
    if active and not _text(people.get(owner_raw)):
        raise ReadingRefused(
            "OWNER_UNRESOLVED",
            "An open topic needs its owner's account; the owner has no reviewed person.",
        )

    held: dict[str, str] = {}
    notes: list[str] = []
    applied: set[str] = set()

    def hold(column: str, code: str, why: str) -> None:
        held[column] = code
        notes.append(f"{code} ({column}): {why}")

    stage = stage_for(status) if status else ""
    if status and not stage:
        hold("hetkeseis", HELD_STAGE, "the stage label is not in STAGE_MAPPING; no stage")
    elif stage:
        applied.add("hetkeseis")

    instruments: list[str] = []
    if _text(row.get("oigusakt")):
        from app.taxonomy.legal_instruments import current_legal_instrument_keys

        instruments = list(current_legal_instrument_keys(_text(row.get("oigusakt"))))
        if instruments:
            applied.add("oigusakt")
        else:
            hold("oigusakt", HELD_INSTRUMENT, "the act does not map to the vocabulary")

    received = _day(row.get("sisse"))
    if received and received > as_of:
        hold("sisse", HELD_RECEIVED, "the received date is after the reading")
        received = None
    elif received:
        applied.add("sisse")

    koda = bool(row.get("koja_ettepanek"))
    valja = _wording(row.get("valja"))
    sent_day = _day(row.get("valja_kuupaev"))
    requested = _day(row.get("arvamuse_tahtaeg"))
    jargmiseks = _text(row.get("jargmiseks"))
    action_day = _day(row.get("tegevuse_tahtaeg"))

    next_action: dict[str, Any] | None = None
    deadline: dict[str, Any] = {"treatment": "NONE"}
    commencements: list[dict[str, Any]] = []
    lifecycle, disposition, successor, successor_id = "ACTIVE", "", "", ""
    basis = ""

    if active:
        if valja == NOT_SENT:
            hold("valja", HELD_NOT_SENT, "«ei saatnud» on a live stage: neither concluded nor sent")
        if requested and not valja and sent_day is None and requested >= as_of and not koda:
            # docs/adr/0148 §10, 0191 §3: the drafting step of an open request,
            # from structured fields only.
            deadline = {"treatment": "OPEN_REQUEST", "deadline": requested.isoformat()}
            next_action = {
                "review": "READY",
                "kind": "DO",
                "date_semantics": "DEADLINE",
                "target_date": requested.isoformat(),
                "date_precision": "EXACT",
                "text": OPINION_PREPARATION_TEXT,
                "source_text": jargmiseks,
                "treatment": "OPINION_DRAFTING",
                "reason": f"{READER_VERSION}: open request, nothing sent, day not passed",
            }
            applied.add("arvamuse_tahtaeg")
            if jargmiseks:
                notes.append(
                    "DRAFTING_WITH_SOURCE_WORDING: the source next step is kept as source only"
                )
        elif requested:
            nothing_sent = (not valja or valja == NOT_SENT) and sent_day is None
            if nothing_sent and requested < as_of:
                hold(
                    "arvamuse_tahtaeg",
                    HELD_PAST_DEADLINE,
                    "a passed opinion deadline with nothing sent is never an overdue obligation",
                )
            if valja == NOT_SENT:
                reason = "NOT_SENT"
            elif valja or sent_day:
                reason = "SENT"
            elif koda:
                reason = "KODA_INITIATIVE"
            else:
                reason = "PASSED_NOT_SENT"
            deadline = {
                "treatment": "SOURCE_ONLY",
                "deadline": requested.isoformat(),
                "reason": reason,
            }
        if next_action is None and jargmiseks:
            hold("jargmiseks", HELD_STEP, "a free-text next step is never read as a kind")
            if action_day is not None:
                hold("tegevuse_tahtaeg", HELD_STEP, "the day of a held next step")
        elif next_action is None and action_day is not None:
            hold("tegevuse_tahtaeg", HELD_STEP, "a next-step day with no wording")
        for index, record in enumerate(
            sorted(bundle.get("commencements") or [], key=lambda r: r.get("position") or 0), 1
        ):
            reading = commencement_reading(record, status=status)
            liik = _text(record.get("liik"))
            note = _text(record.get("tapsustus"))
            entry: dict[str, Any] = {
                "key": f"{reference}-C{index:02d}",
                "source_id": str(record.get("id") or ""),
                "liik": liik,
                "note": note,
                "source_text": liik
                + (f" {record.get('kuupaev')}" if record.get("kuupaev") else "")
                + (f" — {note}" if note else ""),
            }
            if reading["target"]:
                entry.update(
                    review="READY",
                    target=reading["target"],
                    kind=reading["kind"],
                    date=reading["date"],
                    date_precision="EXACT" if reading["date"] else None,
                )
            else:
                entry.update(review="SOURCE_ONLY", reason="commencement kind or day not readable")
                hold("joustumised", HELD_COMMENCEMENT, "a commencement cannot be written as stated")
            commencements.append(entry)
    else:
        lifecycle, disposition, basis = closure_reading(bundle)
        if basis == CLOSURE_UNKNOWN and not historical:
            # Only the historical cohort is closed without a reason; a current
            # topic Lovable calls finished in words the table does not hold
            # waits for a person.
            raise ReadingRefused(
                "CLOSURE_REASON_UNKNOWN",
                "A finished operational topic whose status states no closure reason.",
            )
        if historical:
            notes.append(f"HISTORICAL: created closed ({READER_VERSION}); nothing current")
        if basis == CLOSURE_SOURCE_STATUS:
            notes.append(f"CLOSED_BY_SOURCE_STATUS: «{status}» → {disposition}")
        elif basis == CLOSURE_CONTINUATION:
            notes.append("CLOSED_BY_CONTINUATION: the source's stage link names a successor")
        else:
            notes.append(
                "CLOSURE_REASON_UNKNOWN: the source states no reason the work ended; "
                "none is recorded (blank disposition)"
            )
        if valja == NOT_SENT:
            notes.append(
                "NOT_SENT_RECORDED: «ei saatnud» is kept as the source's statement; "
                "no closure reason is read from it"
            )
        me = str(row.get("id"))
        successors = sorted(
            {
                str(link.get("next"))
                for link in bundle.get("stage_links") or []
                if str(link.get("prev")) == me
            }
        )
        references = bundle.get("references") or {}
        if lifecycle == "CONTINUES":
            if len(successors) == 1 and references.get(successors[0]):
                successor, successor_id = str(references[successors[0]]), successors[0]
            else:
                notes.append("CONTINUES_UNLINKED: the stage link is kept as source only")
        if requested:
            # History, never an obligation: a closed Teema is written with no
            # request and no response deadline (`write_topic` stops at closed).
            deadline = {
                "treatment": "SOURCE_ONLY",
                "deadline": requested.isoformat(),
                "reason": "NOT_SENT" if valja == NOT_SENT else "CLOSED",
            }
        for index, record in enumerate(
            sorted(bundle.get("commencements") or [], key=lambda r: r.get("position") or 0), 1
        ):
            commencements.append(
                {
                    "key": f"{reference}-C{index:02d}",
                    "review": "SOURCE_ONLY",
                    "reason": "closed Matter: date services refuse a closed file",
                    "source_id": str(record.get("id") or ""),
                    "liik": _text(record.get("liik")),
                }
            )
    if active and owner_raw:
        applied.add("vastutaja")

    url = str(row.get("onenote_link") or "").strip()
    if url and not url.startswith("https://"):
        hold("onenote_link", HELD_LINK, "the OneNote link is not an https address; not linked")
        url = ""
    elif url:
        applied.add("onenote_link")

    decisions: dict[str, str] = {"nr": "APPLIED", "teema": "APPLIED", "aasta": "APPLIED"}
    for column in (
        "vastutaja",
        "oigusakt",
        "sisse",
        "hetkeseis",
        "jargmiseks",
        "tegevuse_tahtaeg",
        "arvamuse_tahtaeg",
        "valja",
        "valja_kuupaev",
        "kellele",
        "onenote_link",
    ):
        if column in held:
            decisions[column] = "HELD_FOR_REVIEW"
        elif not row.get(column):
            decisions[column] = "NOT_PRESENT"
        else:
            decisions[column] = "APPLIED" if column in applied else "PRESERVED_AS_SOURCE_ONLY"
    decisions["koja_ettepanek"] = "APPLIED" if koda else "NOT_PRESENT"
    decisions["joustumised"] = (
        "HELD_FOR_REVIEW" if "joustumised" in held else "PRESERVED_AS_SOURCE_ONLY"
    )

    source = _source(bundle)
    if successor_id:
        source["successor_id"] = successor_id
    return {
        "action": "CREATE",
        "reference": reference,
        "group": act,
        "title": title,
        "owner_raw": owner_raw,
        "status_raw": status,
        "stage": stage,
        "lifecycle": lifecycle,
        "disposition": disposition,
        "closure_basis": basis,
        "successor": successor,
        "koda_initiative": koda,
        "instruments": instruments,
        "received_date": received.isoformat() if received else None,
        "addressee_raw": _text(row.get("kellele")),
        "next_action": next_action,
        "deadline": deadline,
        "commencements": commencements,
        "onenote_url": url,
        "field_decisions": decisions,
        "review_notes": notes,
        "source": source,
        "reading": {
            "reader_version": READER_VERSION,
            "as_of": as_of.isoformat(),
            "historical": historical,
        },
    }


def held_columns(topic: Mapping[str, Any]) -> list[str]:
    """The Lovable columns a read topic holds for review, sorted."""
    return sorted(
        name
        for name, decision in (topic.get("field_decisions") or {}).items()
        if decision == "HELD_FOR_REVIEW"
    )


def package_for(
    topics: list[dict[str, Any]],
    *,
    as_of: dt.datetime | dt.date,
    snapshot_sha256: str,
    people: Mapping[str, str],
    cohort: str,
    reviewer: str = READER_VERSION,
) -> dict[str, Any]:
    """A format-2 package around read topics, for `lovable_pilot.validate_package`
    and `build_plan`. No ``replace`` and no ``reserve_through``: it appends, and it
    moves no reference sequence."""
    from app.legacy_import.lovable_pilot import ACTIVE_FORMAT_VERSION, PACKAGE_FORMAT

    owners = {str(t.get("owner_raw") or "") for t in topics}
    return {
        "format": PACKAGE_FORMAT,
        "format_version": ACTIVE_FORMAT_VERSION,
        "cohort": cohort,
        "reviewer": reviewer,
        "mapping_version": READER_VERSION,
        "snapshot": {
            "source_system": "lovable",
            "as_of": as_of.isoformat(),
            "snapshot_sha256": snapshot_sha256,
        },
        "people": {name: people[name] for name in sorted(owners) if name and people.get(name)},
        "expected_topics": len(topics),
        "topics": topics,
    }


# ---------------------------------------------------------------------------
# The synchronisation's reading of the current step (docs/adr/0213 §10)
# ---------------------------------------------------------------------------

#: Bumped whenever the step rule below changes its meaning; recorded in the
#: provenance of every step the synchronisation writes.
STEP_RULE_VERSION = "lovable-step/1"

#: The sentence-initial verb forms that state a kind, per kind. Exactly the
#: reviewed register vocabulary (`register_next_actions.DO_FORMS`, `WAIT_FORMS`,
#: `MONITOR_FORMS` and the review verbs of `REVIEW_FORMS` without «küsi», which
#: is asking, not looking), plus «kirjutan/kirjutame/kirjutada» for drafting.
#: Closed forms, never stems: «oodatavasti» is not «ootan», «vaatasin» is not
#: «vaatan».
STEP_KIND_FORMS: dict[str, tuple[str, ...]] = {
    "DO": (
        "koostan",
        "koostame",
        "koostada",
        "kirjutan",
        "kirjutame",
        "kirjutada",
        "esitan",
        "esitame",
        "esitada",
        "saadan",
        "saadame",
        "saata",
    ),
    "WAIT": ("ootan", "ootame", "oodata", "ootel"),
    "MONITOR": ("jälgin", "jälgime", "jälgida", "vaata", "vaatan", "vaatame", "vaadata"),
}
#: What each kind's day means: a DO on Lovable's `tegevuse_tahtaeg` is due that
#: day; a WAIT, a MONITOR and an unclassified sentence are looked at again that
#: day and are never late (docs/adr/0202 §2: «WAIT / MONITOR … REVIEW_ON — never
#: a deadline»).
STEP_DATE_SEMANTICS = {
    "DO": "DEADLINE",
    "WAIT": "REVIEW_ON",
    "MONITOR": "REVIEW_ON",
    "SOURCE": "REVIEW_ON",
}
#: The kind of a sentence that states none clearly (`ActionKind.SOURCE`).
UNCLASSIFIED_STEP = "SOURCE"


def step_kind(text: Any) -> str:
    """The kind Lovable's `jargmiseks` states, or `UNCLASSIFIED_STEP`.

    The rule, whole:

    1. the sentence's **first word** (lower-cased, quotes and punctuation
       around it removed) is one of `STEP_KIND_FORMS` — that is the kind;
    2. and the whole sentence names no other kind in the reviewed register
       vocabulary (`register_next_actions.instruction_kinds`: «Ootan …, vaata
       üle 10.11» is one WAIT there; «Koostan arvamuse ja ootan …» is two).

    Anything else — no kind verb first, or two kinds — is `UNCLASSIFIED_STEP`:
    the sentence still becomes the current step, verbatim, and no kind is
    invented for it. Reads nothing but its argument."""
    from app.legacy_import.register_next_actions import instruction_kinds

    sentence = _text(text)
    if not sentence:
        return UNCLASSIFIED_STEP
    first = sentence.split(" ", 1)[0].casefold().strip(" .,;:!?…\"'«»„“”()[]-–—")
    kind = next((k for k, forms in STEP_KIND_FORMS.items() if first in forms), "")
    if not kind:
        return UNCLASSIFIED_STEP
    named = instruction_kinds(sentence)
    if named and named != (kind,):
        return UNCLASSIFIED_STEP
    return kind


def step_reading(text: Any, day: Any) -> dict[str, Any] | None:
    """Lovable's `jargmiseks` and `tegevuse_tahtaeg` → the current step Juristid
    holds for them: the sentence verbatim (whitespace collapsed), `step_kind`,
    `STEP_DATE_SEMANTICS`, Lovable's day as an exact day (or none), and the
    sentence as its own source text. ``None`` for a blank sentence."""
    sentence = _text(text)
    if not sentence:
        return None
    kind = step_kind(sentence)
    when = _day(day)
    return {
        "text": sentence,
        "kind": kind,
        "date_semantics": STEP_DATE_SEMANTICS[kind],
        "target_date": when.isoformat() if when else None,
        "date_precision": "EXACT",
    }
