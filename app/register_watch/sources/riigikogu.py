"""A Riigikogu bill and what happens to it, read from the open-data API.

`api.riigikogu.ee` is the Riigikogu's official REST API. A bill is found by its
number and type («973 SE»); numbers restart with each membership, so the bill
is the newest one initiated under that number. What a lawyer wants to know
about it is its steps (initiation, readings, committee decisions), the texts
it went through, and the opinions sent to it.
"""

from __future__ import annotations

import re
from typing import Any

from app.register_watch.sources.http import FetchError, get_json
from app.register_watch.sources.items import Item, parse_day

HOST = "api.riigikogu.ee"
#: «973 SE», «(1008 SE)», «973SE»: the number and the type, in a title.
BILL_MARK = re.compile(r"\b(\d{1,4})\s?(SE|OE|AE|RKE)\b")

READINGS = {
    "INITIATION": "Algatamine",
    "FIRST_READING": "I lugemine",
    "SECOND_READING": "II lugemine",
    "THIRD_READING": "III lugemine",
}
STEPS = {
    "ALGATATUD": "Eelnõu algatati",
    "MENETLUSSE_VOETUD": "Eelnõu võeti menetlusse",
    "I_LUGEMINE_LOPETATUD": "Esimene lugemine lõpetati",
    "II_LUGEMINE_LOPETATUD": "Teine lugemine lõpetati",
    "II_LUGEMINE_KATKESTATUD": "Teine lugemine katkestati",
    "III_LUGEMINE_LOPETATUD": "Kolmas lugemine lõpetati",
    "VASTU_VOETUD": "Eelnõu võeti vastu",
    "TAGASI_LYKATUD": "Eelnõu lükati tagasi",
    "TAGASI_VOETUD": "Eelnõu võeti tagasi",
    "MENETLUSEST_VALJA_LANGENUD": "Eelnõu langes menetlusest välja",
}


def bill_mark(title: str) -> tuple[int, str] | None:
    match = BILL_MARK.search(title or "")
    return (int(match.group(1)), match.group(2)) if match else None


def page_url(uuid: str) -> str:
    return f"https://www.riigikogu.ee/tegevus/eelnoud/{uuid}/"


def find_bill(mark: int, type_code: str) -> dict[str, Any] | None:
    """The bill of the current membership with this number, or ``None``."""
    data = get_json(HOST, "/api/volumes/drafts", {"mark": mark, "size": 20})
    rows = (data.get("_embedded") or {}).get("content") or [] if isinstance(data, dict) else []
    rows = [row for row in rows if row.get("draftTypeCode") == type_code and row.get("uuid")]
    if not rows:
        return None
    return max(rows, key=lambda row: (row.get("membership") or 0, row.get("initiated") or ""))


#: The kinds of bill a sweep lists: laws, decisions and statements.
SWEPT_TYPES = ("SE", "OE", "AE")
SWEEP_PAGE = 100


def recent_drafts(since: Any) -> list[Item]:
    """Every bill initiated on or after ``since``, newest first (ADR 0218 §1)."""
    data = get_json(
        HOST,
        "/api/volumes/drafts",
        {"initiatedStartDate": since.isoformat(), "size": SWEEP_PAGE, "sort": "initiated,desc"},
    )
    if not isinstance(data, dict):
        raise FetchError("Riigikogu vastus on võõra kujuga")
    rows = (data.get("_embedded") or {}).get("content") or []
    items: list[Item] = []
    for row in rows:
        key, kind, title = row.get("uuid"), row.get("draftTypeCode"), (row.get("title") or "")
        if not key or kind not in SWEPT_TYPES or not title.strip():
            continue
        mark = row.get("mark")
        committee = ((row.get("leadingCommittee") or {}).get("name") or "").strip()
        label = f"{mark} {kind}" if mark else kind
        items.append(
            Item(
                external_id=f"draft:{key}",
                title=f"{title.strip()} ({label})"[:500],
                occurred_on=parse_day(row.get("initiated")),
                url=page_url(key),
                detail=" · ".join(part for part in (label, committee) if part)[:300],
            )
        )
    return items


def _step(code: str | None) -> str:
    if not code:
        return ""
    return STEPS.get(code, code.replace("_", " ").capitalize())


def bill_items(uuid: str) -> list[Item]:
    """Every step, text and opinion the API lists for the bill."""
    data = get_json(HOST, f"/api/volumes/drafts/{uuid}")
    if not isinstance(data, dict) or data.get("uuid") != uuid:
        raise FetchError("Riigikogu ei leidnud eelnõu")
    bill = page_url(uuid)
    items: list[Item] = []

    for reading in data.get("readings") or []:
        reading_label = READINGS.get(reading.get("readingCode") or "", "")
        for event in reading.get("proceedingEvents") or []:
            day = parse_day(event.get("date"))
            if day is None:
                continue
            sitting = (event.get("sittingTitle") or "").strip()
            decisions = [
                ((d.get("decision") or {}).get("value") or "").strip()
                for d in event.get("committeeDecisions") or []
            ]
            decisions = [d for d in decisions if d]
            said = _step(event.get("status") or event.get("decisionCode"))
            if said:
                title = said
            elif decisions:
                title = "Juhtivkomisjon: " + "; ".join(decisions)
            elif sitting:
                body = "Täiskogu" if sitting.startswith("Täiskogu") else "Juhtivkomisjon"
                title = f"{body} arutas eelnõu"
            else:
                continue
            items.append(
                Item(
                    external_id=f"event:{reading.get('readingCode')}:{event.get('date')}",
                    title=title[:500],
                    occurred_on=day,
                    url=bill,
                    detail=" · ".join(part for part in (reading_label, sitting) if part)[:300],
                )
            )

    texts: dict[str, Item] = {}
    for text in data.get("texts") or []:
        document = text.get("document") or {}
        key = document.get("uuid")
        day = parse_day((text.get("file") or {}).get("created"))
        if not key:
            continue
        known = texts.get(key)
        if known is not None and (
            known.occurred_on is None or day is None or known.occurred_on <= day
        ):
            continue
        reading_label = READINGS.get(text.get("readingCode") or "", "")
        texts[key] = Item(
            external_id=f"text:{key}",
            title=f"Eelnõu tekst: {(document.get('title') or '').strip()}"[:500],
            occurred_on=day,
            url=bill,
            detail=reading_label,
        )
    items.extend(texts.values())

    for opinion in data.get("opinions") or []:
        key = opinion.get("uuid")
        if not key:
            continue
        items.append(
            Item(
                external_id=f"opinion:{key}",
                title=(opinion.get("title") or "Arvamus").strip()[:500],
                occurred_on=parse_day(opinion.get("date")),
                url=bill,
                detail=(opinion.get("reference") or "")[:300],
            )
        )
    return items
