"""Claude says whether a new draft may touch entrepreneurs (ADR 0218 §3).

One request per `BATCH` of unjudged candidates. The model receives public facts
only: each candidate's register titles, dates, numbers and senders, a written
profile of what the Chamber follows (`PROFILE`), and the titles of the open
unrestricted Teemad so it can name one the draft seems tied to. It searches
nothing and writes nothing on a Teema: the strength and the reason are shown
beside the candidate and a person decides.

Off unless `settings.REGISTER_SWEEP_AI` is on and the SDK finds a key. Any
failure leaves that batch unjudged; the next night asks again.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from operator import itemgetter
from typing import Any

from django.conf import settings

from app.matters.models import Matter
from app.register_watch import ai_call, outbound
from app.register_watch.house_style import KODA_STYLE
from app.register_watch.models import Strength, TopicCandidate

logger = logging.getLogger("app.register_watch")

#: What the Chamber cares about. Wide on purpose: indirect effects count.
PROFILE = """The Estonian Chamber of Commerce and Industry (Eesti Kaubandus-Tööstuskoda) \
represents companies of every size and sector. Its legal department follows any \
legislation that may change the conditions for doing business in Estonia, directly or \
indirectly: company and commercial law, taxes, fees and excise, accounting and audit, \
labour law, employment and migration of workers, social contributions, competition, \
public procurement, state aid, energy and climate obligations, environmental permits and \
reporting, waste and packaging, transport and logistics, digital services, data \
protection and cybersecurity duties, consumer protection, financial services and \
lending, insolvency, courts and enforcement, licences and supervision, education and \
skills as they affect the labour supply, sector regulation (construction, health, \
food, alcohol, tobacco, gambling, media) and EU law being transposed. Administrative \
burden, reporting duties, costs, deadlines and penalties for companies are always of \
interest. Matters that touch only the internal organisation of the state, a single \
named person, a property deal of the state, internal orders, appointments or \
procurement contracts of a ministry are not."""

SYSTEM = f"""You help the head of the legal department of the Estonian Chamber of \
Commerce and Industry spot new legislative drafts early.

{PROFILE}

You receive drafts newly seen in public document registers (the Riigikogu and \
ministries). Each draft has one or more register entries: titles, dates, register \
numbers, senders. For each draft decide:

- STRONG: it clearly changes conditions for businesses or employers.
- POSSIBLE: it may affect businesses in some sector or indirectly; worth a look.
- WEAK: a legislative draft, but an effect on businesses is unlikely.
- NONE: not a legislative or policy draft at all (an order to sign, a contract, \
  a personal matter, a ministry's internal paper).

Write one line in Estonian (at most 200 characters) saying how it may touch \
entrepreneurs, or why it does not. Do not repeat the whole title. If one of the \
Chamber's open topics (given with ids) is plainly about the same act or subject, \
give its id in `related`; otherwise null. Use only the information given; do not \
guess content the titles do not show.

{KODA_STYLE}"""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "strength": {"type": "string", "enum": ["STRONG", "POSSIBLE", "WEAK", "NONE"]},
                    "reason": {"type": "string"},
                    "related": {"type": ["string", "null"]},
                },
                "required": ["id", "strength", "reason", "related"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

BATCH = 20
MAX_TOKENS = 6000
#: The open Teemad listed to the model for `related`: newest first, titles only.
TOPICS_SHOWN = 400


@dataclass
class Usage(ai_call.Usage):
    #: Open sendable Teemad not offered because of `TOPICS_SHOWN` (counted,
    #: never named), and letter titles of the period not sent because their
    #: Teema is not known to be sendable (`outbound.letter_titles`).
    topics_left_out: int = 0
    letters_left_out: int = 0


def enabled() -> bool:
    return bool(getattr(settings, "REGISTER_SWEEP_AI", False))


def open_topics(usage: Usage | None = None) -> dict[str, Matter]:
    """The open Teemad the model may name: never a restricted or a TEST one
    (ADR 0217 §4, `outbound.sendable_topics`).

    At most `TOPICS_SHOWN`, newest first. When more are open, the older ones
    cannot be proposed; that is counted on ``usage`` and logged as a number,
    so the cap is never silent (audit HR-09)."""
    topics = outbound.sendable_topics()
    rows = topics.only("pk", "title").order_by("-created_at")[:TOPICS_SHOWN]
    chosen = {str(matter.pk): matter for matter in rows}
    if len(chosen) == TOPICS_SHOWN:
        left_out = topics.count() - TOPICS_SHOWN
        if left_out > 0:
            logger.warning(
                "register sweep: %d open Teema(s) beyond the first %d not offered to the model",
                left_out,
                TOPICS_SHOWN,
            )
            if usage is not None:
                usage.topics_left_out = left_out
    return chosen


def _prompt(candidates: Sequence[TopicCandidate], topics: dict[str, Matter]) -> str:
    drafts = [
        {
            "id": str(candidate.pk),
            "entries": [
                {
                    "title": item.title,
                    "date": item.occurred_on.isoformat() if item.occurred_on else None,
                    "register": item.get_source_display(),
                    "number_and_sender": item.detail,
                }
                for item in candidate.items.all()[:6]
            ],
        }
        for candidate in candidates
    ]
    listed = [{"id": key, "title": matter.title} for key, matter in topics.items()]
    return (
        "The Chamber's open topics:\n"
        + json.dumps(listed, ensure_ascii=False)
        + "\n\nNew drafts:\n"
        + json.dumps(drafts, ensure_ascii=False, indent=1)
    )


def judge_unjudged(usage: Usage, client: Any = None) -> int:
    """Judge every candidate still waiting without a strength; the count judged."""
    if not enabled():
        return 0
    usage.model = usage.model or str(getattr(settings, "REGISTER_SWEEP_AI_MODEL", ""))
    pending = list(
        # A letter whose title names no draft gives the model nothing to read.
        TopicCandidate.objects.filter(status="NEW", strength=Strength.UNJUDGED)
        .exclude(match_key="")
        .prefetch_related("items")
        .order_by("listed_at", "pk")
    )
    if not pending:
        return 0
    topics = open_topics(usage)
    judged = 0
    for start in range(0, len(pending), BATCH):
        judged += _judge_batch(pending[start : start + BATCH], topics, usage, client)
    return judged


def _judge_batch(
    candidates: Sequence[TopicCandidate], topics: dict[str, Matter], usage: Usage, client: Any
) -> int:
    answers = ai_call.call_items(
        setting="REGISTER_SWEEP_AI_MODEL",
        system=SYSTEM,
        prompt=lambda: _prompt(candidates, topics),
        schema=SCHEMA,
        max_tokens=MAX_TOKENS,
        timeout=ai_call.SWEEP_TIMEOUT,
        key=itemgetter("id"),
        usage=usage,
        client=client,
    )
    if isinstance(answers, ai_call.Failure):
        # The list goes on without the model; the next night asks again.
        if answers.kind == "request":
            logger.warning("register sweep: model call failed: %s", answers.reason)
        usage.failed(answers.reason)
        return 0
    allowed = {Strength.STRONG, Strength.POSSIBLE, Strength.WEAK, Strength.NONE}
    judged = 0
    for candidate in candidates:
        row = answers.get(str(candidate.pk))
        if not row or row.get("strength") not in allowed:
            continue
        candidate.strength = row["strength"]
        candidate.reason = ai_call.clean_line(row.get("reason"))
        # Only an id that was offered: the model cannot point anywhere else.
        candidate.related_matter = topics.get(str(row.get("related") or ""))
        candidate.save(update_fields=["strength", "reason", "related_matter", "updated_at"])
        judged += 1
    usage.judged += judged
    return judged
