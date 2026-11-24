"""Claude reads what a title search found and says whether it belongs (ADR 0217 §4).

One request per `BATCH` of a Teema's unjudged findings. The model receives
public facts only — the Teema's title and the found documents' titles, dates,
numbers and senders — and returns a verdict and one Estonian line per document.
It searches nothing and writes nothing: the verdict is shown beside the
proposal and the lawyer decides. A restricted Teema is never sent.

Off unless `settings.REGISTER_WATCH_AI` is on and the SDK finds a key
(`ANTHROPIC_API_KEY`). Any failure leaves that batch's findings unjudged; the
next night asks again.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from operator import itemgetter
from typing import Any

from django.conf import settings

from app.register_watch import model, outbound
from app.register_watch.house_style import KODA_STYLE
from app.register_watch.models import Relevance, WatchFinding

logger = logging.getLogger("app.register_watch")

SYSTEM = f"""You help lawyers at the Estonian Chamber of Commerce and Industry follow \
legislative proceedings. A lawyer tracks one topic (a draft law or regulation). A \
search of a ministry's public document register by the bill's number or name returned \
the documents below. For each document decide whether it belongs to the tracked \
proceeding (RELEVANT), clearly does not (UNRELATED), or cannot be told from the title \
(UNSURE). Then write one short line in Estonian (at most 160 characters) saying what \
the document is and why a lawyer following the topic would care, without repeating \
the whole title. Use only the information given; do not guess dates or content.

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
                    "verdict": {"type": "string", "enum": ["RELEVANT", "UNSURE", "UNRELATED"]},
                    "note": {"type": "string"},
                },
                "required": ["id", "verdict", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


Usage = model.Usage


def enabled() -> bool:
    return bool(getattr(settings, "REGISTER_WATCH_AI", False))


def may_send(matter: Any) -> bool:
    """Only an unrestricted, real Teema's public facts ever leave the application
    (`outbound.may_send`: never a restricted or a TEST Teema)."""
    return outbound.may_send(matter)


def _prompt(matter: Any, findings: Sequence[WatchFinding]) -> str:
    documents = [
        {
            "id": str(finding.pk),
            "title": finding.title,
            "date": finding.occurred_on.isoformat() if finding.occurred_on else None,
            "register": finding.target.get_source_display(),
            "number_and_sender": finding.detail,
        }
        for finding in findings
    ]
    topic = {
        "title": matter.title,
        "searched_for": sorted({f.target.label or f.target.identifier for f in findings}),
    }
    return (
        "Tracked topic:\n"
        + json.dumps(topic, ensure_ascii=False)
        + "\n\nDocuments found:\n"
        + json.dumps(documents, ensure_ascii=False, indent=1)
    )


#: Findings per request. Each answer row is an id, a verdict and a line of at
#: most 160 characters, so 25 fit well inside `MAX_TOKENS`; one Teema with a
#: hundred findings once never fitted in a single answer, and the same set was
#: sent again every night (audit QA26-BUG-05).
BATCH = 25
MAX_TOKENS = 4000
TIMEOUT = 120.0


def judge(matter: Any, findings: Sequence[WatchFinding], usage: Usage, client: Any = None) -> int:
    """Write a verdict and a line on each finding the model answered for; the count judged.

    The findings go in batches of `BATCH`, oldest first, so a batch the model
    could not finish costs that batch only and the others are still judged.
    """
    if not findings or not enabled() or not may_send(matter):
        return 0
    ordered = sorted(findings, key=lambda finding: (finding.created_at, str(finding.pk)))
    judged = 0
    for start in range(0, len(ordered), BATCH):
        judged += _judge_batch(matter, ordered[start : start + BATCH], usage, client)
    return judged


def _judge_batch(
    matter: Any, findings: Sequence[WatchFinding], usage: Usage, client: Any = None
) -> int:
    answers = model.call_items(
        setting="REGISTER_WATCH_AI_MODEL",
        system=SYSTEM,
        prompt=lambda: _prompt(matter, findings),
        schema=SCHEMA,
        max_tokens=MAX_TOKENS,
        timeout=TIMEOUT,
        key=itemgetter("id"),
        usage=usage,
        client=client,
    )
    if isinstance(answers, model.Failure):
        # The watch goes on without the model; the next night asks again.
        if answers.kind == "request":
            logger.warning("register watch: model call failed: %s", answers.reason)
        elif answers.kind == "stop":
            logger.warning("register watch: model stopped with %s", answers.stop_reason)
        usage.failed(answers.reason)
        return 0
    judged = 0
    allowed = {Relevance.RELEVANT, Relevance.UNSURE, Relevance.UNRELATED}
    for finding in findings:
        row = answers.get(str(finding.pk))
        if not row or row.get("verdict") not in allowed:
            continue
        finding.relevance = row["verdict"]
        finding.ai_note = model.clean_line(row.get("note"))
        finding.save(update_fields=["relevance", "ai_note", "updated_at"])
        judged += 1
    usage.judged += judged
    return judged
