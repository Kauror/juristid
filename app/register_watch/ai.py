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
from dataclasses import dataclass
from typing import Any

from django.conf import settings

from app.register_watch import house_style, outbound
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


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    judged: int = 0
    failures: int = 0
    #: The last failure's kind and HTTP status, never its message: shown in
    #: Haldus → Registrite jälgimine.
    last_failure: str = ""

    def failed(self, reason: str) -> None:
        self.failures += 1
        self.last_failure = reason[:120]


def enabled() -> bool:
    return bool(getattr(settings, "REGISTER_WATCH_AI", False))


def may_send(matter: Any) -> bool:
    """Only an unrestricted, real Teema's public facts ever leave the application
    (`outbound.may_send`: never a restricted or a TEST Teema)."""
    return outbound.may_send(matter)


def _client() -> Any:
    import anthropic  # imported here: the watch runs without it when the model is off

    return anthropic.Anthropic(timeout=120.0, max_retries=2)


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
    try:
        client = client or _client()
        response = client.beta.messages.create(
            model=getattr(settings, "REGISTER_WATCH_AI_MODEL", "claude-opus-5-5"),
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            messages=[{"role": "user", "content": _prompt(matter, findings)}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except Exception as error:  # the model is an aid; the watch goes on without it
        status = getattr(error, "status_code", None)
        reason = type(error).__name__ + (f" {status}" if status else "")
        logger.warning("register watch: model call failed: %s", reason)
        usage.failed(reason)
        return 0
    usage.calls += 1
    usage.input_tokens += getattr(response.usage, "input_tokens", 0) or 0
    usage.output_tokens += getattr(response.usage, "output_tokens", 0) or 0
    usage.model = str(getattr(response, "model", "") or usage.model)
    if response.stop_reason != "end_turn":
        logger.warning("register watch: model stopped with %s", response.stop_reason)
        usage.failed(f"stop_reason {response.stop_reason}")
        return 0
    text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        answers = {row["id"]: row for row in json.loads(text)["items"]}
    except (ValueError, KeyError, TypeError):
        usage.failed("vastus ei olnud loetav")
        return 0
    judged = 0
    allowed = {Relevance.RELEVANT, Relevance.UNSURE, Relevance.UNRELATED}
    for finding in findings:
        row = answers.get(str(finding.pk))
        if not row or row.get("verdict") not in allowed:
            continue
        finding.relevance = row["verdict"]
        finding.ai_note = house_style.fix(str(row.get("note") or "").strip())[:500]
        finding.save(update_fields=["relevance", "ai_note", "updated_at"])
        judged += 1
    usage.judged += judged
    return judged
