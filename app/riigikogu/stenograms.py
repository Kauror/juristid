"""Where the plenary names koda (ADR 0228 §2).

`/api/steno/verbatims` gives each sitting's agenda items and their speeches; a
speech carries its own UUID, its speaker as the stenogram heads it («Esimees
Lauri Hussar», «Majandus- ja kommunikatsiooniminister …») and its text. A
mention is found **deterministically** — koda's names (`organisation.names_koda`)
and «kaubanduskoda» on its own when it is not the Põllumajandus-Kaubanduskoda —
and kept with the exact quote, the paragraph around it, the speaker from the
stenogram's own heading, the sitting and the agenda item.

Two kinds: a mention under an agenda item about a bill is **õigusloomega seotud**
and points to the bill; anything else (an information hour, a question about
koda itself) is **muu mainimine** and is shown apart, never made into a Teema.
No sentiment is scored: the quote is the evidence, and a person reads it.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

from app.riigikogu import bills, client
from app.riigikogu.enums import MentionCategory
from app.riigikogu.models import Bill, StenogramMention

#: «kaubanduskoda», «kaubanduskoja», «Kaubandus-tööstuskoja» in speech, any case form.
_SPOKEN = re.compile(
    r"(?<![\w-])(?:eesti\s+)?kaubandus\s*[-‐‑–]?\s*(?:ja\s+)?(?:t[öo]{1,2}stus)?ko(?:da|ja)\w*",
    re.IGNORECASE,
)
_NOT_KODA = re.compile(
    r"p[õo]llumajandus\s*[-‐‑–]\s*kaubandusko|keskkonnaühenduste\s+ko", re.IGNORECASE
)
QUOTE_CHARS = 400
CONTEXT_CHARS = 1200


@dataclass
class Counts:
    sittings: int = 0
    speeches: int = 0
    mentions_new: int = 0
    legislative: int = 0
    other: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k != "errors"}


#: «Ameerika Kaubanduskoda», «Eesti-Saksa Kaubanduskoda»: bilateral chambers, not koda.
_OTHER_CHAMBER = re.compile(r"(?:^|\s)([A-ZÕÄÖÜŠŽ][\w]*(?:-[\w]+)+|(?!Eesti\b)[A-ZÕÄÖÜŠŽ]\w+)\s+$")


def find(text: str) -> list[tuple[int, int]]:
    """The spans in ``text`` that name koda (never the Põllumajandus-Kaubanduskoda,
    never a bilateral chamber such as the Ameerika Kaubanduskoda)."""
    spans = []
    for match in _SPOKEN.finditer(text or ""):
        before = text[max(0, match.start() - 30) : match.end()]
        if _NOT_KODA.search(before):
            continue
        named = match.group(0)
        if not re.search(r"t[öo]{1,2}stus", named, re.IGNORECASE) and named[:1].isupper():
            lead = text[max(0, match.start() - 30) : match.start()]
            if _OTHER_CHAMBER.search(lead):
                continue
        spans.append((match.start(), match.end()))
    return spans


def _window(text: str, start: int, end: int, width: int) -> str:
    """The sentence(s) around a span, cut at sentence ends where possible."""
    left = max(0, start - width // 2)
    right = min(len(text), end + width // 2)
    head = text.rfind(". ", left, start)
    tail = text.find(". ", end, right)
    left = head + 2 if head != -1 else left
    right = tail + 1 if tail != -1 else right
    return text[left:right].strip()


def plain_title(value: Any) -> str:
    """An agenda title as text: the API sends some wrapped in HTML («<p>…</p>»)."""
    import html

    from django.utils.html import strip_tags

    return " ".join(html.unescape(strip_tags(str(value or ""))).split())


def _bill_for(title: str) -> Bill | None:
    found = bills.mark_of(title)
    if found is None:
        return None
    return Bill.objects.filter(mark=found[0], type_code=found[1]).order_by("-membership").first()


def read_days(*, start: dt.date, end: dt.date, budget: client.Budget | None = None) -> Counts:
    counts = Counts()
    answer = client.get_json(
        "/api/steno/verbatims",
        {"startDate": start.isoformat(), "endDate": end.isoformat()},
        budget=budget,
    )
    if not isinstance(answer, list):
        raise client.FetchError("Riigikogu stenogrammi vastus on võõra kujuga")
    for sitting in answer:
        if not isinstance(sitting, dict):
            continue
        counts.sittings += 1
        try:
            day = dt.date.fromisoformat(str(sitting.get("date"))[:10])
        except ValueError:
            continue
        for item in sitting.get("agendaItems") or []:
            if not isinstance(item, dict):
                continue
            agenda_title = plain_title(item.get("title"))[:1000]
            bill = _bill_for(agenda_title)
            # A bill's reading is legislative whether or not a Teema follows the bill
            # (only followed bills are stored, and then `bill` ties it to them).
            legislative = bills.mark_of(agenda_title) is not None
            for event in item.get("events") or []:
                if not isinstance(event, dict) or event.get("type") != "SPEECH":
                    continue
                counts.speeches += 1
                text = str(event.get("text") or "")
                quotes: set[str] = set()
                for start_at, end_at in find(text):
                    quote = _window(text, start_at, end_at, QUOTE_CHARS)
                    if quote in quotes:
                        continue  # two names in one passage are one mention
                    quotes.add(quote)
                    key = f"{event.get('uuid') or day.isoformat()}:{start_at}"[:200]
                    category = MentionCategory.LEGISLATIVE if legislative else MentionCategory.OTHER
                    _row, created = StenogramMention.objects.get_or_create(
                        key=key,
                        defaults={
                            "sitting_on": day,
                            "sitting_title": str(sitting.get("title") or "")[:300],
                            "agenda_title": agenda_title,
                            "agenda_uuid": _uuid_or_none(item.get("agendaItemUuid")),
                            "speaker": str(event.get("speaker") or "")[:200],
                            "quote": quote,
                            "context": _window(text, start_at, end_at, CONTEXT_CHARS),
                            "url": str(sitting.get("link") or "")[:500],
                            "category": category,
                            "bill": bill,
                        },
                    )
                    if created:
                        counts.mentions_new += 1
                        counts.legislative += int(category == MentionCategory.LEGISLATIVE)
                        counts.other += int(category == MentionCategory.OTHER)
    return counts


def _uuid_or_none(value: Any) -> Any:
    import uuid as uuid_module

    try:
        return uuid_module.UUID(str(value))
    except (TypeError, ValueError):
        return None
