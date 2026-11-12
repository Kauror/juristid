"""An independent oracle for «Lovable is the source of truth» (audit §D.2).

A deliberately small re-statement of the business contract the owner approved
on 11.11.2026 (`JURISTID_LOVABLE_AUTHORITATIVE_APPROVAL.md` §1, recorded in the
repository as docs/adr/0213 and the module docstring of the engine it checks):
for every synchronised Teema, every Lovable-owned field reads what Lovable says.
It is the in-repo, synthetic twin of the round-4 operator checker
(`parity_check.py`), which stays the tool for production copies.

**Independence is the point.** It imports nothing from the synchronisation
(`app.legacy_import.lovable_*` — enforced by I-6 in `test_contract_invariants.py`)
and no domain service: it reads Juristid's rows through the model registry and
restates each rule here, from the approval and `vocabulary.py`. A rule the
engine and the oracle both get wrong in the same way is the one thing this
cannot catch; a rule only the engine gets wrong, it does.

What it compares (approval §1 «Lovable-owned fields»): title, owner, lifecycle
(open/closed, reason, continuation), stage, current step (exact text, day and
kind), the opinion outcome (sent on a day / «ei saatnud»), the open opinion
request, Koja ettepanek, received date, legal act, OneNote link and the
commencements Lovable states. A value Juristid cannot represent is reported as
NOT_REPRESENTABLE, never as a difference (approval §4).
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from django.apps import apps

from tests.lovable_contract import vocabulary

MATCH = "MATCH"
DIFFERENT = "DIFFERENT"
NOT_REPRESENTABLE = "NOT_REPRESENTABLE"

#: Lovable's two finished stages and the reason each closes a Teema with
#: (approval §2: «jõustunud» closes, «rohkem ei tegele» is Koda's own stop).
FINISHED: dict[str, tuple[str, str]] = {
    "jõustunud": ("in_force", "COMPLETED"),
    "rohkem ei tegele": ("monitoring_stopped", "MONITORING_STOPPED"),
}
#: The stages at which a fixed commencement day is the law's own day (adopted):
#: anywhere else it is a plan (approval §1 «Commencement dates»).
ADOPTED = ("jõustumise ootel", "jõustunud")
#: The step-kind rule (approval §1: «the first word; if unclear, Liik määramata»).
FIRST_WORD_KIND: dict[str, str] = {
    "koostan": "DO",
    "kirjutan": "DO",
    "esitan": "DO",
    "saadan": "DO",
    "ootan": "WAIT",
    "jälgin": "MONITOR",
    "vaata": "MONITOR",
    "vaatan": "MONITOR",
}
UNCLEAR_KIND = "SOURCE"  # «Liik määramata»


@dataclass(frozen=True)
class Comparison:
    reference: str
    field: str
    verdict: str
    lovable: Any
    juristid: Any


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _day(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)[:10]
    return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None


def step_kind(text: str) -> str:
    words = re.findall(r"[\wÕÄÖÜõäöüŠšŽž]+", text.casefold())
    return FIRST_WORD_KIND.get(words[0], UNCLEAR_KIND) if words else UNCLEAR_KIND


def sent_statement(valja: Any, valja_kuupaev: Any) -> tuple[str, str | None] | None:
    """What Lovable says about the opinion, as far as the approved contract
    reads it: ``("SENT", day)``, ``("NOT_SENT", None)``, ``None`` for nothing
    said, or ``("HELD", None)`` for a form whose reading is pending (LV-03)."""
    words = _text(valja).casefold().rstrip(".")
    if words == "ei saatnud":
        return ("NOT_SENT", None)
    day = _day(valja_kuupaev) or _day(valja)
    if day is None and valja:
        found = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", _text(valja))
        if found:
            day = f"{found[3]}-{int(found[2]):02d}-{int(found[1]):02d}"
    if day:
        return ("SENT", day)
    if _text(valja):
        return ("HELD", None)
    return None


def expected(
    row: Mapping[str, Any],
    *,
    commencements: Iterable[Mapping[str, Any]],
    successor_ids: list[str],
    today: dt.date,
) -> dict[str, Any]:
    """Every Lovable-owned field as Juristid must hold it, from Lovable's row alone.

    Values are Juristid's own words (stage keys, dispositions, kinds) so that
    `observed` can be compared to it directly; a person is named by the name
    Lovable writes and resolved in `compare`."""
    status = _text(row.get("hetkeseis"))
    stage_treatment = vocabulary.TREATMENT.get(("hetkeseis", status))
    stage = stage_treatment[1] if stage_treatment and stage_treatment[0] == "MAPPED" else None
    if successor_ids:
        lifecycle: Any = {
            "open": False,
            "disposition": "SUPERSEDED",
            "successor": successor_ids[0] if len(successor_ids) == 1 else NOT_REPRESENTABLE,
        }
    elif status in FINISHED:
        lifecycle = {"open": False, "disposition": FINISHED[status][1], "successor": None}
    elif stage is not None:
        lifecycle = {"open": True, "disposition": "", "successor": None}
    else:
        lifecycle = NOT_REPRESENTABLE

    is_open = isinstance(lifecycle, dict) and lifecycle["open"]
    text = _text(row.get("jargmiseks"))
    step: Any = None
    if is_open and text:
        step = {"text": text, "day": _day(row.get("tegevuse_tahtaeg")), "kind": step_kind(text)}
    elif is_open:
        step = NOT_REPRESENTABLE  # a blank sentence: the request rule decides (LS-05)

    said = sent_statement(row.get("valja"), row.get("valja_kuupaev"))
    outcome: Any = NOT_REPRESENTABLE if said and said[0] == "HELD" else said
    request_day = _day(row.get("arvamuse_tahtaeg"))
    request: Any
    if said and said[0] in ("SENT", "NOT_SENT"):
        request = None  # a sent date or «ei saatnud» ends Lovable's request
    elif is_open and request_day and dt.date.fromisoformat(request_day) >= today:
        request = request_day
    else:
        request = NOT_REPRESENTABLE  # blank or passed: never ends, never opens

    act = _text(row.get("oigusakt"))
    act_treatment = vocabulary.TREATMENT.get(("oigusakt", act))
    legal_act: Any = (
        [act_treatment[1]] if act_treatment and act_treatment[0] == "MAPPED" else NOT_REPRESENTABLE
    )
    link = _text(row.get("onenote_link"))
    onenote: Any = link if link.startswith("https://") else NOT_REPRESENTABLE

    stated = []
    for item in commencements:
        liik = _text(item.get("liik"))
        note = _text(item.get("tapsustus"))
        day = _day(item.get("kuupaev"))
        if liik.startswith("Üldises"):
            stated.append(("MatterEffectiveDate", "GENERAL_ORDER", None, note))
        elif liik.startswith("Kindla") and status in ADOPTED:
            stated.append(("MatterEffectiveDate", "KNOWN_DATE", day, note))
        elif liik.startswith("Kindla"):
            stated.append(("MatterImportantDate", "PLANNED_COMMENCEMENT", day, note))
        elif liik.startswith("Eeldatava"):
            stated.append(("MatterImportantDate", "ESTIMATED_COMMENCEMENT", day, note))
        else:
            stated = NOT_REPRESENTABLE  # type: ignore[assignment]  # «Teadmata» (LV-02)
            break
    owner_name = _text(row.get("uus_vastutaja")) or _text(row.get("vastutaja"))
    return {
        "title": _text(row.get("teema")),
        "owner": owner_name,
        "lifecycle": lifecycle,
        "stage": stage if stage is not None else NOT_REPRESENTABLE,
        "step": step,
        "opinion_outcome": outcome,
        "opinion_request": request,
        "koda": bool(row.get("koja_ettepanek")),
        "received": _day(row.get("sisse")),
        "legal_act": legal_act,
        "onenote_link": onenote,
        "commencements": sorted(stated, key=str) if isinstance(stated, list) else stated,
    }


def observed(matter: Any, *, exclude_dates: Iterable[Any] = ()) -> dict[str, Any]:
    """The same fields as Juristid holds them, read row by row."""
    next_action = apps.get_model("workflow", "NextAction")
    effective = apps.get_model("intelligence", "MatterEffectiveDate")
    important = apps.get_model("intelligence", "MatterImportantDate")
    reference_model = apps.get_model("legacy_import", "MatterSourceReference")
    outcome_model = apps.get_model("legacy_import", "LovableOpinionOutcome")
    skip = {str(pk) for pk in exclude_dates}

    step = next_action.objects.filter(matter=matter, status="OPEN").first()
    newest = (
        outcome_model.objects.filter(matter=matter).order_by("-recorded_at", "-created_at").first()
    )
    outcome = None
    if newest is not None and newest.outcome != "CLEARED":
        outcome = (newest.outcome, newest.sent_on.isoformat() if newest.sent_on else None)
    link = (
        reference_model.objects.filter(matter=matter, source_system="LOVABLE")
        .order_by("-created_at")
        .values_list("onenote_url", flat=True)
        .first()
    )
    dates = [
        (
            "MatterEffectiveDate",
            r.kind,
            r.date_value.isoformat() if r.date_value else None,
            _text(r.description),
        )
        for r in effective.objects.filter(matter=matter, status="ACTIVE", removed_at__isnull=True)
        if str(r.pk) not in skip
    ] + [
        (
            "MatterImportantDate",
            r.kind,
            r.date_value.isoformat() if r.date_value else None,
            _text(r.note),
        )
        for r in important.objects.filter(
            matter=matter,
            status="ACTIVE",
            removed_at__isnull=True,
            kind__in=("PLANNED_COMMENCEMENT", "ESTIMATED_COMMENCEMENT"),
        )
        if str(r.pk) not in skip
    ]
    return {
        "title": _text(matter.title),
        "owner": matter.owner.display_name if matter.owner_id else None,
        "lifecycle": {
            "open": bool(matter.is_open),
            "disposition": str(matter.disposition or ""),
            "successor": str(matter.superseded_by_id) if matter.superseded_by_id else None,
        },
        "stage": matter.stage.key if matter.stage_id else None,
        "step": {
            "text": _text(step.text),
            "day": step.target_date.isoformat() if step.target_date else None,
            "kind": str(step.kind),
        }
        if step is not None
        else None,
        "opinion_outcome": outcome,
        "opinion_request": matter.response_deadline.isoformat()
        if matter.response_deadline
        else None,
        "koda": matter.track == "KODA_INITIATIVE",
        "received": matter.received_date.isoformat() if matter.received_date else None,
        "legal_act": sorted(matter.legal_instruments.values_list("key", flat=True)),
        "onenote_link": link or "",
        "commencements": sorted(dates, key=str),
    }


def compare(
    lovable: Any,
    *,
    today: dt.date,
    references: Iterable[str] | None = None,
    exclude_dates: Iterable[Any] = (),
) -> list[Comparison]:
    """Every Lovable-owned field of every synchronised Teema, Lovable against Juristid.

    ``lovable`` is the fake's live state (its ``rows``, ``commencements`` and
    ``stage_links``) — what a fresh reading would return. Identity is the
    synchronisation state's `source_id` (the one stable key both sides share)."""
    state_model = apps.get_model("legacy_import", "LovableSyncState")
    user_model = apps.get_model("accounts", "User")
    wanted = set(references) if references is not None else None
    states = {
        str(s.source_id): s
        for s in state_model.objects.select_related("matter").filter(
            matter__deleted_at__isnull=True
        )
    }
    matter_of = {source: str(state.matter_id) for source, state in states.items()}
    out: list[Comparison] = []
    for source, state in sorted(states.items(), key=lambda item: item[1].reference):
        row = lovable.rows.get(source)
        if row is None or (wanted is not None and row["nr"] not in wanted):
            continue
        successors = sorted(
            matter_of.get(link["next_topic_id"], NOT_REPRESENTABLE)
            for link in lovable.stage_links
            if link["prev_topic_id"] == source
        )
        want = expected(
            row,
            commencements=sorted(
                (c for c in lovable.commencements if c["topic_id"] == source),
                key=lambda c: c["position"],
            ),
            successor_ids=successors,
            today=today,
        )
        if NOT_REPRESENTABLE in successors and isinstance(want["lifecycle"], dict):
            want["lifecycle"] = NOT_REPRESENTABLE
        have = observed(state.matter, exclude_dates=exclude_dates)
        owners = [
            u
            for u in user_model.objects.filter(is_active=True)
            if _text(u.display_name).casefold() == want["owner"].casefold()
        ]
        if len(owners) != 1:
            want["owner"] = NOT_REPRESENTABLE
        else:
            want["owner"] = owners[0].display_name
        if not (isinstance(want["lifecycle"], dict) and want["lifecycle"]["open"]):
            # A closed Teema has no current step and no open request (approval §1).
            want["step"], want["opinion_request"] = None, None
        for name, value in want.items():
            if value == NOT_REPRESENTABLE:
                verdict = NOT_REPRESENTABLE
            else:
                verdict = MATCH if value == have[name] else DIFFERENT
            out.append(Comparison(row["nr"], name, verdict, value, have[name]))
    return out


def differences(lovable: Any, **kw: Any) -> list[Comparison]:
    return [c for c in compare(lovable, **kw) if c.verdict == DIFFERENT]
