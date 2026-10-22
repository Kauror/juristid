"""What the MCP tools answer, as plain Python over the services the pages use.

Seven readers, one per tool (docs/adr/0185 §2), each a pure function of the
authenticated `User` and the tool's arguments that returns a JSON-ready dict.
No MCP SDK here and nothing asynchronous: `app.mcp_server.server` calls these
in its worker threads, exactly as a view would call a selector.

**Nothing here decides who may read what.** Every reader first asks
`may_read_business_content` and answers `NotFound` when it is false (ADR 0181:
somebody who is not an authorized internal user sees nothing, and is not even
told that a record exists). Then every row is read through the reader's own
scope — `search_page(user=…)`, `Model.objects.visible_to(user)`,
`visible_links(user)`, `matter_timeline(user=…)`, `matter_intelligence(…,
user)` — and records that have no `visible_to` of their own (a document's
versions and text fragments) are reached only through a parent that does. An
id naming a record the reader may not see answers exactly like an id naming
nothing at all.

**Nothing here decides what a record means, either.** Deadline semantics,
lateness, work status, the commencement containment rule, which sends are
opinions — each is asked of the service that owns it. What this module adds is
labelling: every dated value says whether it is a recorded fact (``confirmed``),
somebody's plan (``planned``), an approximation (``estimated``), a state that no
longer stands (``previous``) or absent (``missing``), so a language model reading
the answer cannot mistake one for another. When the record has no next step,
the answer says so; nothing is inferred to fill the gap.

**Returned text is data.** A note, a document page or a OneNote page that says
«ignore previous instructions» is returned verbatim, as the text of that
record, and nothing here reads it for meaning. The server's instructions tell
the client the same (`app.mcp_server.instructions`).

Every string returned is bounded: a fetch by ``settings.MCP_FETCH_MAX_CHARS``
with an explicit continuation id, every list by a page size, every snippet and
summary by a character budget. Every URL is absolute and built from a verified
record id (`app.mcp_server.links`).
"""

from __future__ import annotations

import html
import re
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from django.conf import settings
from django.db.models import Count, Q, QuerySet
from django.utils import timezone
from django.utils.html import strip_tags

from app.core.authorization import may_read_business_content
from app.mcp_server import ids, links
from app.search.models import SearchSourceKind
from app.search.services import (
    MATCH_LABELS,
    MAX_QUERY_CHARACTERS,
    SOURCE_LABELS,
    SearchResult,
    clean_query,
    search_page,
)
from app.workflow.dates import format_at_precision, is_approximate

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ReaderError(Exception):
    """A refusal whose message is safe to show: it never carries business text."""


class NotFound(ReaderError):
    """Unknown, or not visible to this reader — deliberately indistinguishable."""

    def __init__(self, message: str = "No such record.") -> None:
        super().__init__(message)


class InvalidArgument(ReaderError):
    """A malformed id, date, page or filter. Says what was wrong, not what exists."""


# ---------------------------------------------------------------------------
# Vocabulary of the answers
# ---------------------------------------------------------------------------

#: A recorded fact: a sent date, a recorded deadline, a known exact date.
CONFIRMED = "confirmed"
#: Somebody's intention: a planned step, a draft's planned commencement.
PLANNED = "planned"
#: An approximation: a month/quarter/year date, an `EXPECTED_AROUND`, a source's estimate.
ESTIMATED = "estimated"
#: A value that no longer stands: cancelled, superseded, withdrawn.
PREVIOUS = "previous"
#: Not recorded.
MISSING = "missing"

#: Results per `search` call — ChatGPT's company-knowledge tool shows a handful.
SEARCH_RESULT_LIMIT = 20
#: Rows per `search_records` page, and how many pages it serves.
RECORDS_PER_PAGE = 20
MAX_RECORD_PAGES = 10
#: Items per `get_matter_activity` page.
ACTIVITY_PER_PAGE = 25
MAX_ACTIVITY_PAGES = 200
#: The most chronology rows one activity call will read to answer a date filter.
#: A filter older than this many rows answers with `scan_limited: true` rather
#: than reading a whole decade of a file.
ACTIVITY_SCAN_LIMIT = 2_000
#: Rows per `get_matter_documents` page.
DOCUMENTS_PER_PAGE = 25
#: Rows per `get_upcoming_commencements` page (each of its two lists).
COMMENCEMENTS_PER_PAGE = 25
#: The widest commencement window, in days.
MAX_COMMENCEMENT_SPAN_DAYS = 400
#: Any page number above this is past the end of anything stored.
MAX_PAGE = 10_000

#: Per-item text budgets.
ACTIVITY_ITEM_CHARS = 1_500
SNIPPET_CHARS = 600
SUMMARY_CHARS = 2_000
TITLE_CHARS = 300
LIST_LIMIT = 10


def _fetch_budget() -> int:
    return max(1_000, int(getattr(settings, "MCP_FETCH_MAX_CHARS", 20_000)))


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _require_reader(user: Any) -> None:
    """Fail closed before anything is read (docs/adr/0181 §1)."""
    if not may_read_business_content(user):
        raise NotFound()


def _today() -> date:
    """The department's day, Europe/Tallinn (`settings.TIME_ZONE`)."""
    return timezone.localdate()


def _clip(text: str | None, limit: int) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _one_line(text: str | None) -> str:
    return " ".join((text or "").split())


_BLOCK_END = re.compile(r"</(p|div|li|h[1-6]|blockquote|tr|pre|ul|ol|table)\s*>", re.IGNORECASE)
_LINE_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)
_LIST_ITEM = re.compile(r"<li[^>]*>", re.IGNORECASE)
_BLANK_LINES = re.compile(r"\n{3,}")


def html_to_text(value: str | None) -> str:
    """Sanitised entry HTML as plain text, keeping its paragraphs.

    `entry_plain_text` folds a note to one line, which is right for a row's
    headline and wrong for a fetch: a model reading a three-paragraph note as one
    run-on sentence loses where one point ends. Tags are removed, never
    interpreted, and entities decoded once.
    """
    text = _LINE_BREAK.sub("\n", value or "")
    text = _LIST_ITEM.sub("\n• ", text)
    text = _BLOCK_END.sub("\n", text)
    text = html.unescape(strip_tags(text))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def _iso(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return (
            timezone.localtime(value).date().isoformat()
            if timezone.is_aware(value)
            else value.date().isoformat()
        )
    return value.isoformat()


def _local_date(value: datetime | None) -> date | None:
    if value is None:
        return None
    return timezone.localtime(value).date() if timezone.is_aware(value) else value.date()


def _dated(
    value: date | None,
    precision: str,
    certainty: str,
    *,
    period_end: date | None = None,
    display: str | None = None,
) -> dict[str, Any]:
    """One dated value, with how exactly it is known and what kind of fact it is."""
    if value is None:
        return {
            "date": None,
            "period_end": None,
            "display": display or "",
            "precision": None,
            "certainty": MISSING,
        }
    return {
        "date": value.isoformat(),
        "period_end": (period_end or value).isoformat(),
        "display": display if display is not None else format_at_precision(value, precision),
        "precision": precision or "EXACT",
        "certainty": certainty,
    }


def _person(user: Any) -> str | None:
    if user is None:
        return None
    return getattr(user, "display_name", "") or str(user)


def _matter_ref(matter: Any) -> dict[str, Any]:
    return {
        "id": ids.format_source_id(ids.MATTER, matter.pk),
        "reference": matter.display_reference or None,
        "title": matter.title,
        "url": links.matter_url(matter.pk),
        "is_test_data": bool(matter.is_test_data),
    }


#: What starts every title built from a TEST Matter's heading (MCPSEC-16).
TEST_DATA_PREFIX = "[TEST] "


def _matter_heading(matter: Any) -> str:
    """«2026_225 · Title», or «[TEST] 2026_225 · Title» for a TEST Matter.

    `search` answers ``id``, ``title`` and ``url`` only, so the title is where
    a client learns that a hit is development data, not the department's work
    (MCPSEC-16). Every search title and every fetch title is built from here.
    """
    reference = matter.display_reference
    heading = f"{reference} · {matter.title}" if reference else matter.title
    return f"{TEST_DATA_PREFIX}{heading}" if matter.is_test_data else heading


def _page_number(page: Any, *, maximum: int = MAX_PAGE) -> int:
    if isinstance(page, bool) or not isinstance(page, int):
        raise InvalidArgument("page must be a whole number.")
    if page < 1 or page > maximum:
        raise InvalidArgument(f"page must be between 1 and {maximum}.")
    return page


def _parse_iso_date(value: Any, name: str) -> date | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise InvalidArgument(f"{name} must be an ISO date (YYYY-MM-DD).")
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise InvalidArgument(f"{name} must be an ISO date (YYYY-MM-DD).") from None


def _parse_id(raw: Any) -> ids.SourceId:
    try:
        return ids.parse_source_id(raw)
    except ids.MalformedSourceId as refusal:
        raise InvalidArgument(str(refusal)) from None


def _visible_matters(user: Any) -> QuerySet[Any]:
    from app.matters.models import Matter

    return Matter.objects.visible_to(user)


def resolve_matter(user: Any, matter: Any) -> Any:
    """A Matter by its UUID, its ``matter:`` id or its reference (``2026_225``).

    Read through ``Matter.objects.visible_to(user)``, whose manager already
    leaves deleted Matters out — so a tombstone, a Matter this reader may not
    see and a Matter that never existed are the same `NotFound`.
    """
    if not isinstance(matter, str) or not matter.strip():
        raise InvalidArgument("matter must be a Matter id or a reference such as 2026_225.")
    from app.matters.models import Matter

    value = matter.strip()
    if len(value) > ids.MAX_ID_LENGTH:
        raise InvalidArgument("matter must be a Matter id or a reference such as 2026_225.")
    queryset = _visible_matters(user).select_related("owner", "stage", "addressee_organisation")
    if value.startswith(f"{ids.MATTER}:"):
        source = _parse_id(value)
        if source.ordinal is not None:
            raise InvalidArgument("A Matter id takes no continuation here.")
        found = queryset.filter(pk=source.pk).first()
    else:
        try:
            pk = uuid.UUID(value)
        except ValueError:
            pk = None
        if pk is not None:
            found = queryset.filter(pk=pk).first()
        else:
            parsed = Matter.parse_reference(
                " ".join(value.split()).replace("-", "_").replace(" ", "_")
            )
            if parsed is None:
                raise InvalidArgument("matter must be a Matter id or a reference such as 2026_225.")
            year, number = parsed
            if not (1 <= year <= 9999 and 1 <= number <= 2_000_000_000):
                raise NotFound()
            found = queryset.filter(reference_year=year, reference_number=number).first()
    if found is None:
        raise NotFound()
    return found


# ---------------------------------------------------------------------------
# Bounded text: one rule for every fetch
# ---------------------------------------------------------------------------


def _cut_point(text: str, room: int) -> int:
    """Where to end a part of at most ``room`` characters: a paragraph, line or word end.

    Falls back to exactly ``room`` when no boundary lies in the second half, so a
    part is never absurdly short because of one long line.
    """
    if len(text) <= room:
        return len(text)
    floor = room // 2
    for separator in ("\n\n", "\n", " "):
        position = text.rfind(separator, floor, room)
        if position > 0:
            return position + len(separator)
    return room


def _bounded_text(
    text: str, *, offset: int, prefix: str, pk: Any, budget: int | None = None
) -> tuple[str, dict[str, Any]]:
    """The part of ``text`` from ``offset``, and metadata saying exactly which part.

    Never cut silently: a part shorter than the whole says ``truncated``, which
    characters it covers, and the id that fetches the next part.
    """
    total = len(text)
    if offset and offset >= total:
        raise InvalidArgument("The continuation is past the end of this text.")
    room = budget or _fetch_budget()
    rest = text[offset:]
    cut = _cut_point(rest, room)
    part = rest[:cut]
    end = offset + cut
    truncated = end < total
    metadata: dict[str, Any] = {
        "truncated": truncated,
        "covered": f"characters {offset + 1}–{end} of {total}" if total else "empty",
        "next_id": ids.format_source_id(prefix, pk, end) if truncated else None,
        "characters_total": total,
    }
    if offset:
        metadata["continues_from"] = offset
    return part, metadata


def _answer(
    *,
    source_id: str,
    title: str,
    body: str,
    url: str,
    metadata: dict[str, Any],
    prefix: str,
    pk: Any,
    offset: int,
) -> dict[str, Any]:
    text, bounds = _bounded_text(body, offset=offset, prefix=prefix, pk=pk)
    return {
        "id": source_id,
        "title": _clip(title, TITLE_CHARS),
        "text": text,
        "url": url,
        "metadata": {**metadata, **bounds},
    }


def _lines(*pairs: tuple[str, Any]) -> str:
    """``Label: value`` lines for the values that exist."""
    return "\n".join(f"{label}: {value}" for label, value in pairs if value not in (None, "", []))


# ---------------------------------------------------------------------------
# search — ChatGPT's company-knowledge contract
# ---------------------------------------------------------------------------


def _clean(query: Any) -> str:
    if not isinstance(query, str):
        raise InvalidArgument("query must be a string.")
    if len(query) > MAX_QUERY_CHARACTERS:
        raise InvalidArgument(f"query must be at most {MAX_QUERY_CHARACTERS} characters.")
    term = clean_query(query)
    if not term:
        raise InvalidArgument("query must not be empty.")
    return term


def result_source_id(result: SearchResult) -> str:
    """The `fetch` id of one search result — the record the row projects."""
    prefix = ids.SEARCH_KIND_PREFIXES.get(result.source_kind)
    if prefix is None:  # pragma: no cover - every kind is mapped; a new one must be too
        return ids.format_source_id(ids.MATTER, result.matter.pk)
    if prefix == ids.FRAGMENT:
        pk = result.fragment_id or result.source_object_id
    elif prefix == ids.DOCUMENT:
        pk = result.document_id or result.source_object_id
    elif prefix == ids.MATTER:
        pk = result.matter.pk
    else:
        pk = result.source_object_id
    if pk is None:
        return ids.format_source_id(ids.MATTER, result.matter.pk)
    return ids.format_source_id(prefix, pk)


def _source_label(kind: str) -> str:
    """The badge the search page prints, else the kind's own name.

    A fragment is «Dokumendi sisu» rather than the page's «Dokument» badge: the
    page shows the locator beside it, and a title read on its own has to say
    that it is a passage, not the file.
    """
    if kind == SearchSourceKind.DOCUMENT_FRAGMENT:
        return str(SearchSourceKind.DOCUMENT_FRAGMENT.label)
    label = SOURCE_LABELS.get(kind, "")
    if label:
        return label
    try:
        return str(SearchSourceKind(kind).label)
    except ValueError:  # pragma: no cover - every stored kind is a member
        return ""


@dataclass(frozen=True)
class RowFacts:
    """What a search row's projection does not carry, read once per page (EVAL-001, EVAL-003).

    ``submissions`` maps a submission's pk to ``(kind, status, sent_at)``;
    ``entry_days`` an entry's pk to the department's day it happened.
    """

    submissions: dict[Any, tuple[str, str, datetime | None]]
    entry_days: dict[Any, date | None]


NO_FACTS = RowFacts(submissions={}, entry_days={})


def row_facts(results: Sequence[SearchResult], user: Any) -> RowFacts:
    """One statement per kind present, never per row, through each family's ``visible_to``."""
    from app.matters.models import Entry
    from app.submissions.models import Submission

    submission_ids = {
        result.source_object_id
        for result in results
        if result.source_kind == SearchSourceKind.SUBMISSION and result.source_object_id
    }
    entry_ids = {
        result.source_object_id
        for result in results
        if result.source_kind == SearchSourceKind.ENTRY and result.source_object_id
    }
    submissions: dict[Any, tuple[str, str, datetime | None]] = {}
    if submission_ids:
        for pk, kind, status, sent_at in (
            Submission.objects.visible_to(user)
            .filter(pk__in=submission_ids)
            .values_list("pk", "kind", "status", "sent_at")
        ):
            submissions[pk] = (kind, status, sent_at)
    entry_days: dict[Any, date | None] = {}
    if entry_ids:
        for pk, moment in (
            Entry.objects.visible_to(user).filter(pk__in=entry_ids).values_list("pk", "occurred_at")
        ):
            entry_days[pk] = _local_date(moment)
    return RowFacts(submissions=submissions, entry_days=entry_days)


def _submission_kind(result: SearchResult, facts: RowFacts) -> str | None:
    if result.source_kind != SearchSourceKind.SUBMISSION:
        return None
    found = facts.submissions.get(result.source_object_id)
    return found[0] if found else None


def row_label(result: SearchResult, facts: RowFacts = NO_FACTS) -> str:
    """The row's badge: the search page's, except for a reply that is not an opinion.

    The projection has one badge for every SUBMISSION — «Arvamus» — and an
    official «no comments» reply (`NO_OBJECTION_RESPONSE`) is not an opinion
    (docs/adr/0174). Such a row takes the submission's own kind label
    («Vastus: märkusi ja ettepanekuid ei ole») instead (EVAL-001).
    """
    from app.submissions.enums import NOT_AN_OPINION_KINDS, SubmissionKind

    kind = _submission_kind(result, facts)
    if kind in NOT_AN_OPINION_KINDS:
        return str(SubmissionKind(kind).label)
    return _source_label(result.source_kind)


def result_title(result: SearchResult, facts: RowFacts = NO_FACTS) -> str:
    """«2026_225 · Pakendiseaduse muutmine — Dokumendi sisu: Seletuskiri, lk 3».

    An entry's title ends with its date: two entries of one Matter are both
    «Sissekanne: Märkus», and the date is what tells them apart (EVAL-003).
    """
    heading = _matter_heading(result.matter)
    if result.source_kind == SearchSourceKind.MATTER:
        return _clip(heading, TITLE_CHARS)
    label = row_label(result, facts)
    detail = result.source_title or result.document_title
    if result.source_locator:
        detail = f"{detail}, {result.source_locator}" if detail else result.source_locator
    if result.source_kind == SearchSourceKind.ENTRY:
        day = facts.entry_days.get(result.source_object_id)
        if day is not None:
            stamp = format_at_precision(day, "EXACT")
            detail = f"{detail}, {stamp}" if detail else stamp
    detail = _one_line(detail)
    tail = f"{label}: {detail}" if detail else label
    return _clip(f"{heading} — {tail}" if tail else heading, TITLE_CHARS)


def snippet_text(result: SearchResult) -> str:
    """The excerpt as plain text, matched words in «guillemets»."""
    text = "".join(f"«{run.text}»" if run.highlight else run.text for run in result.snippet)
    return _clip(_one_line(text), SNIPPET_CHARS)


def search_results(user: Any, query: str, *, limit: int = SEARCH_RESULT_LIMIT) -> dict[str, Any]:
    """``{"results": [{"id", "title", "url"}]}`` — at most 20, ranked by `search_page`."""
    _require_reader(user)
    term = _clean(query)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise InvalidArgument("limit must be a positive whole number.")
    limit = min(limit, SEARCH_RESULT_LIMIT)
    found = search_page(query=term, user=user, page_number=1, per_page=limit)
    facts = row_facts(found.results, user)
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for result in found.results:
        source_id = result_source_id(result)
        # One row per record. The projection has one row per source, so this
        # only ever drops a repeat — never two different pages of one file.
        if source_id in seen:
            continue
        seen.add(source_id)
        results.append(
            {
                "id": source_id,
                "title": result_title(result, facts),
                "url": links.result_url(result),
            }
        )
    return {"results": results}


# ---------------------------------------------------------------------------
# search_records — the same search, with the whole row
# ---------------------------------------------------------------------------


def _source_kinds(values: Any) -> list[str] | None:
    if values is None:
        return None
    if isinstance(values, str) or not isinstance(values, Sequence):
        raise InvalidArgument("source_kinds must be a list of source kinds.")
    allowed = set(SearchSourceKind.values)
    kinds: list[str] = []
    for value in values:
        if not isinstance(value, str) or value.strip().upper() not in allowed:
            raise InvalidArgument(
                "Unknown source kind. Allowed: " + ", ".join(sorted(allowed)) + "."
            )
        kind = value.strip().upper()
        if kind not in kinds:
            kinds.append(kind)
    if not kinds:
        raise InvalidArgument("source_kinds must name at least one kind, or be omitted.")
    return kinds


def _source_dates(
    results: list[SearchResult], user: Any, facts: RowFacts
) -> dict[str, dict[str, Any]]:
    """Each row's own date, by source id — one statement per kind present, never per row.

    Read through each family's own ``visible_to`` although the rows came from an
    authorized search a moment ago, for the reason `_present` re-scopes: "the ids
    are already checked" stops being true the day somebody reuses this.
    """
    from app.documents.models import Document
    from app.intelligence.models import MatterImportantDate, MatterWorkVictory
    from app.legacy_import.historical_views import visible_links
    from app.matters.models import (
        MatterEngagement,
        MatterExternalPosition,
        MatterProceduralDevelopment,
        MatterWebsiteOverview,
    )
    from app.submissions.enums import SubmissionStatus

    by_kind: dict[str, set[Any]] = {}
    for result in results:
        if result.source_kind in (
            SearchSourceKind.DOCUMENT.value,
            SearchSourceKind.DOCUMENT_FRAGMENT.value,
        ):
            key = result.document_id
        else:
            key = result.source_object_id
        if key is not None:
            by_kind.setdefault(result.source_kind, set()).add(key)

    dates: dict[tuple[str, Any], dict[str, Any]] = {}

    def put(kind: str, pk: Any, value: date | None, precision: str, basis: str) -> None:
        if value is None:
            return
        certainty = ESTIMATED if is_approximate(precision or "EXACT") else CONFIRMED
        dates[(kind, pk)] = {**_dated(value, precision or "EXACT", certainty), "basis": basis}

    kinds = by_kind
    # Entries and submissions were read with the page's other row facts
    # (`row_facts`), once; a submission's date is its send, so only one that
    # was sent — the `historically_sent()` rule — has one.
    for pk, day in facts.entry_days.items():
        put("ENTRY", pk, day, "EXACT", "occurred")
    sent_statuses = {SubmissionStatus.SENT, SubmissionStatus.WITHDRAWN, SubmissionStatus.SUPERSEDED}
    for pk, (_kind, status, sent_at) in facts.submissions.items():
        if status in sent_statuses and sent_at is not None:
            put("SUBMISSION", pk, _local_date(sent_at), "EXACT", "sent")
    document_ids = kinds.get("DOCUMENT", set()) | kinds.get("DOCUMENT_FRAGMENT", set())
    if document_ids:
        for pk, moment in (
            Document.objects.visible_to(user)
            .filter(pk__in=document_ids)
            .values_list("pk", "current_version__acquired_at")
        ):
            for kind in ("DOCUMENT", "DOCUMENT_FRAGMENT"):
                put(kind, pk, _local_date(moment), "EXACT", "acquired")
    if SearchSourceKind.LEGACY_SOURCE_PAGE.value in kinds:
        for pk, moment in (
            visible_links(user)
            .filter(pk__in=kinds["LEGACY_SOURCE_PAGE"])
            .values_list("pk", "source_page__source_created_at")
        ):
            put("LEGACY_SOURCE_PAGE", pk, _local_date(moment), "EXACT", "onenote_page_created")
    simple: tuple[tuple[str, Any, str, str | None, str], ...] = (
        ("ENGAGEMENT", MatterEngagement, "occurred_on", "occurred_on_precision", "occurred"),
        (
            "PROCEDURAL_DEVELOPMENT",
            MatterProceduralDevelopment,
            "occurred_on",
            "occurred_on_precision",
            "occurred",
        ),
        ("EXTERNAL_POSITION", MatterExternalPosition, "stated_on", "stated_on_precision", "stated"),
        ("WEBSITE_OVERVIEW", MatterWebsiteOverview, "published_on", None, "published"),
        ("IMPORTANT_DATE", MatterImportantDate, "date_value", "date_precision", "milestone"),
        ("WORK_VICTORY", MatterWorkVictory, "period_date", "date_precision", "period"),
    )
    for kind, model, field, precision_field, basis in simple:
        if kind not in kinds:
            continue
        columns = ["pk", field] + ([precision_field] if precision_field else [])
        for row in model.objects.visible_to(user).filter(pk__in=kinds[kind]).values_list(*columns):
            put(kind, row[0], row[1], row[2] if precision_field else "EXACT", basis)

    answer: dict[str, dict[str, Any]] = {}
    for result in results:
        if result.source_kind == SearchSourceKind.MATTER:
            received = result.matter.received_date
            if received is not None:
                answer[result_source_id(result)] = {
                    **_dated(received, "EXACT", CONFIRMED),
                    "basis": "received",
                }
            continue
        key = (
            result.document_id
            if result.source_kind in ("DOCUMENT", "DOCUMENT_FRAGMENT")
            else result.source_object_id
        )
        found = dates.get((result.source_kind, key))
        if found is not None:
            answer[result_source_id(result)] = found
    return answer


def search_records(
    user: Any,
    query: str,
    *,
    source_kinds: list[str] | None = None,
    matter: str | None = None,
    page: int = 1,
) -> dict[str, Any]:
    """One page of the ranked search, each row whole.

    ``source_kinds`` and ``matter`` narrow the authorized projection in SQL
    before ranking and slicing (`search_page`'s own keywords), so ``total`` and
    ``has_more`` describe the narrowed result set.
    """
    _require_reader(user)
    term = _clean(query)
    number = _page_number(page, maximum=MAX_RECORD_PAGES)
    kinds = _source_kinds(source_kinds)
    scoped_matter = resolve_matter(user, matter) if matter is not None else None
    found = search_page(
        query=term,
        user=user,
        page_number=number,
        per_page=RECORDS_PER_PAGE,
        source_kinds=kinds,
        matter_id=scoped_matter.pk if scoped_matter is not None else None,
    )
    total = found.total
    # `Paginator.get_page` reads a number past the end as the last page. A tool
    # asking for page 4 of 2 is told it is empty rather than handed page 2 again.
    results = found.results if found.page.number == number else []
    facts = row_facts(results, user)
    dates = _source_dates(results, user, facts)
    rows = []
    for result in results:
        source_id = result_source_id(result)
        from app.submissions.enums import NOT_AN_OPINION_KINDS, SubmissionKind

        # The search page's badge, except where it would call a reply that is
        # not an opinion «Arvamus» (EVAL-001, `row_label`).
        label = result.source_label or _source_label(result.source_kind)
        extra: dict[str, Any] = {}
        submission_kind = _submission_kind(result, facts)
        if submission_kind is not None:
            is_opinion = submission_kind not in NOT_AN_OPINION_KINDS
            if not is_opinion:
                label = row_label(result, facts)
            extra = {
                "submission_kind": submission_kind,
                "submission_kind_label": str(SubmissionKind(submission_kind).label),
                "is_opinion": is_opinion,
                "is_official_no_comments_reply": not is_opinion,
            }
        rows.append(
            {
                **extra,
                "id": source_id,
                "source_kind": result.source_kind,
                "source_label": label,
                "matter": _matter_ref(result.matter),
                "title": result_title(result, facts),
                "snippet": snippet_text(result),
                "locator": result.source_locator or None,
                "source_date": dates.get(source_id),
                "rank": round(result.rank, 6),
                "match_kind": result.match_kind,
                "match_label": MATCH_LABELS.get(result.match_kind, ""),
                "url": links.result_url(result),
            }
        )
    return {
        "query": term,
        "page": number,
        "per_page": RECORDS_PER_PAGE,
        "total": total,
        "has_more": number * RECORDS_PER_PAGE < total and number < MAX_RECORD_PAGES,
        "results": rows,
    }


# ---------------------------------------------------------------------------
# get_matter — the structured snapshot
# ---------------------------------------------------------------------------


def _action_certainty(action: Any) -> str:
    from app.workflow.enums import DateSemantics

    if action.target_date is None:
        return MISSING
    if action.date_semantics == DateSemantics.EXPECTED_AROUND or action.is_approximate:
        return ESTIMATED
    return PLANNED


def _action(action: Any, today: date) -> dict[str, Any]:
    return {
        "kind": action.kind,
        "kind_label": str(action.get_kind_display()),
        "text": _clip(action.text, SUMMARY_CHARS),
        "date": {
            **_dated(
                action.target_date,
                action.date_precision,
                _action_certainty(action),
                display=action.display_date,
            ),
            "semantics": action.date_semantics,
            "semantics_label": str(action.get_date_semantics_display()),
        },
        "responsible": _person(action.responsible),
        "status": action.status,
        # Only a DO with a DEADLINE can be missed; a WAIT whose review date has
        # passed is due for a look, not late (master specification 18.8).
        "is_overdue": bool(action.is_overdue(today)),
        "is_due_for_review": bool(action.is_due_for_review(today)),
        "is_follow_up_check": bool(action.is_follow_up),
    }


def _fact_certainty(record: Any) -> str:
    from app.intelligence.enums import FactStatus, ImportantDateKind

    if record.status != FactStatus.ACTIVE:
        return PREVIOUS
    kind = getattr(record, "kind", "")
    if kind == ImportantDateKind.PLANNED_COMMENCEMENT:
        return PLANNED
    if kind == ImportantDateKind.ESTIMATED_COMMENCEMENT:
        return ESTIMATED
    if getattr(record, "date_value", None) is None:
        return MISSING
    if is_approximate(record.date_precision):
        return ESTIMATED
    return CONFIRMED


def _important_date(record: Any, today: date) -> dict[str, Any]:
    from app.intelligence.enums import is_informational_milestone

    return {
        "id": ids.format_source_id(ids.IMPORTANT_DATE, record.pk),
        "title": _clip(record.title, TITLE_CHARS),
        "kind": record.kind,
        "kind_label": str(record.get_kind_display()),
        "informational": is_informational_milestone(record.kind),
        "status": record.status,
        "status_label": str(record.get_status_display()),
        "date": _dated(
            record.date_value,
            record.date_precision,
            _fact_certainty(record),
            period_end=record.period_end,
            display=record.display_date,
        ),
        "has_passed": record.has_passed(today),
        "note": _clip(record.note, SUMMARY_CHARS) or None,
    }


def _commencement_timing(record: Any, today: date) -> str:
    """``past``, ``in_period``, ``upcoming`` or ``undated`` — never «entered into force».

    A date behind us says the *recorded* commencement date has passed. Whether
    the act actually entered into force on it is not something Juristid records.
    """
    if record.date_value is None:
        return "undated"
    if record.has_passed(today):
        return "past"
    if record.date_value <= today:
        return "in_period"
    return "upcoming"


TIMING_NOTES = {
    "past": "The recorded commencement date has passed. Juristid records the date only, not "
    "whether the act took effect on it.",
    "in_period": "The recorded commencement period has begun and not ended; the exact day is not "
    "known.",
    "upcoming": "Recorded commencement date ahead.",
    "undated": "No commencement date is recorded.",
}


def _effective_date(record: Any, today: date) -> dict[str, Any]:
    from app.intelligence.enums import EffectiveDateKind, FactStatus

    if record.status != FactStatus.ACTIVE:
        certainty = PREVIOUS
    elif record.date_value is None:
        certainty = MISSING
    elif record.is_approximate:
        certainty = ESTIMATED
    else:
        certainty = CONFIRMED
    timing = _commencement_timing(record, today)
    return {
        "id": ids.format_source_id(ids.EFFECTIVE_DATE, record.pk),
        "what": _clip(record.description, SUMMARY_CHARS),
        "kind": record.kind,
        "kind_label": str(record.get_kind_display()),
        "is_general_order": record.kind == EffectiveDateKind.GENERAL_ORDER,
        "status": record.status,
        "status_label": str(record.get_status_display()),
        "date": _dated(
            record.date_value,
            record.date_precision,
            certainty,
            period_end=record.period_end,
            display=record.display_when,
        ),
        "timing": timing,
        "timing_note": TIMING_NOTES[timing],
        "source_url": record.source_url or None,
    }


def _submission(submission: Any) -> dict[str, Any]:
    from app.submissions.enums import NOT_AN_OPINION_KINDS, SubmissionStatus

    is_opinion = submission.kind not in NOT_AN_OPINION_KINDS
    return {
        "id": ids.format_source_id(ids.SUBMISSION, submission.pk),
        "kind": submission.kind,
        "kind_label": str(submission.get_kind_display()),
        # `NO_OBJECTION_RESPONSE` is Koda's official «no comments» answer: it ends
        # the request like any send and is never an opinion (docs/adr/0174).
        "is_opinion": is_opinion,
        "is_official_no_comments_reply": not is_opinion,
        "title": _clip(submission.title, TITLE_CHARS),
        "status": submission.status,
        "status_label": str(submission.get_status_display()),
        "still_stands": submission.status == SubmissionStatus.SENT,
        "sent": {
            **_dated(
                _local_date(submission.sent_at),
                "EXACT",
                CONFIRMED if submission.status == SubmissionStatus.SENT else PREVIOUS,
            ),
            "time_recorded": submission.sent_at_precision == "TIMESTAMP",
        },
        "recipients": [organisation.name for organisation in submission.recipients.all()],
        "channel": submission.channel or None,
        "reference": submission.reference or None,
        "summary": _clip(submission.summary, SUMMARY_CHARS) or None,
    }


def _development(development: Any, today: date) -> dict[str, Any]:
    ahead = development.occurred_on is not None and development.occurred_on > today
    if development.occurred_on is None:
        certainty = MISSING
    elif ahead:
        certainty = PLANNED
    elif development.has_approximate_date:
        certainty = ESTIMATED
    else:
        certainty = CONFIRMED
    return {
        "id": ids.format_source_id(ids.DEVELOPMENT, development.pk),
        "title": _clip(development.title, TITLE_CHARS) or None,
        "date": _dated(
            development.occurred_on,
            development.occurred_on_precision,
            certainty,
            display=development.display_date,
        ),
        "dated_ahead": ahead,
        "lawyer_note": _clip(development.note, SUMMARY_CHARS) or None,
        "url": links.matter_url(development.matter_id),
    }


def _snapshot(user: Any, matter: Any, today: date) -> dict[str, Any]:
    from app.intelligence.selectors import VISIBLE_VICTORY_STATUS, matter_intelligence
    from app.matters.models import MatterEngagement, MatterProceduralDevelopment
    from app.matters.selectors import current_action_of, planned_actions_of, response_deadline_of
    from app.submissions.models import Submission

    missing: list[str] = []
    current = current_action_of(matter, user)
    planned = planned_actions_of(matter, user)
    deadline = response_deadline_of(matter, user, today)
    facts = matter_intelligence(matter, user, today)
    sent_rows = Submission.objects.filter(matter=matter).visible_to(user).historically_sent()
    sent = list(
        sent_rows.prefetch_related("recipients").order_by("-sent_at", "-pk")[: LIST_LIMIT * 2]
    )
    # Counted in the database, never from the list above, which is cut
    # (MCPSEC-05): a Matter with 25 sends has sent 25.
    sent_counts = sent_rows.aggregate(
        total=Count("pk"),
        no_comments=Count("pk", filter=Q(kind="NO_OBJECTION_RESPONSE")),
        opinions_standing=Count("pk", filter=Q(status="SENT") & ~Q(kind="NO_OBJECTION_RESPONSE")),
    )
    development_rows = MatterProceduralDevelopment.objects.filter(matter=matter).visible_to(user)
    developments = list(development_rows.order_by("-occurred_on", "-created_at", "-pk")[:5])
    wait_rows = MatterEngagement.objects.filter(matter=matter).visible_to(user).open_waits()
    waits = list(wait_rows.order_by("feedback_deadline", "pk")[:5])
    organisations = [organisation.name for organisation in matter.source_organisations.all()]
    collaborators = [_person(person) for person in matter.collaborators.all()]

    if current is None:
        missing.append("current_next_action")
    if matter.owner is None:
        missing.append("owner")
    if matter.stage is None:
        missing.append("stage")
    if deadline is None:
        missing.append("opinion_deadline")
    if matter.received_date is None:
        missing.append("received_date")

    work_status = matter.work_status
    victories = [
        record for record in facts.work_victories if record.status == VISIBLE_VICTORY_STATUS
    ]
    snapshot = _snapshot_body(
        user=user,
        matter=matter,
        today=today,
        current=current,
        planned=planned,
        deadline=deadline,
        facts=facts,
        sent=sent,
        sent_counts=sent_counts,
        developments=developments,
        waits=waits,
        organisations=organisations,
        collaborators=collaborators,
        missing=missing,
        work_status=work_status,
        victories=victories,
    )
    # How long each bounded list is in full. Nothing in the snapshot is cut
    # without the answer saying so (MCPSEC-05). A short page is the whole list,
    # so only a full one costs a COUNT.
    full_lengths = {
        "alternate_titles": len(matter.alternate_titles),
        "planned_actions": len(planned),
        "upcoming_dates": len(facts.upcoming_dates),
        "past_dates": len(facts.past_dates),
        "commencements": len(facts.effective_dates),
        "submissions_sent": int(sent_counts["total"] or 0),
        "open_feedback_waits": len(waits) if len(waits) < 5 else int(wait_rows.count()),
        "latest_developments": (
            len(developments) if len(developments) < 5 else int(development_rows.count())
        ),
        "work_victories": len(victories),
    }
    totals = {}
    for name, total in full_lengths.items():
        shown = len(snapshot[name])
        totals[name] = {"shown": shown, "total": max(total, shown), "truncated": total > shown}
    snapshot["list_totals"] = totals
    snapshot["truncated_lists"] = [name for name, value in totals.items() if value["truncated"]]
    return snapshot


def _snapshot_body(
    *,
    user: Any,
    matter: Any,
    today: date,
    current: Any,
    planned: Any,
    deadline: Any,
    facts: Any,
    sent: list[Any],
    sent_counts: dict[str, Any],
    developments: list[Any],
    waits: list[Any],
    organisations: list[str],
    collaborators: list[str | None],
    missing: list[str],
    work_status: Any,
    victories: list[Any],
) -> dict[str, Any]:
    from app.documents.models import Document
    from app.legacy_import.historical_views import visible_links
    from app.matters.models import Entry

    return {
        **_matter_ref(matter),
        "alternate_titles": [_clip(title, TITLE_CHARS) for title in matter.alternate_titles][
            :LIST_LIMIT
        ],
        "documents_url": links.matter_documents_url(matter.pk),
        "is_open": matter.is_open,
        "work_status": {"key": work_status.key, "label": work_status.label},
        "disposition": (
            {"key": matter.disposition, "label": str(matter.get_disposition_display())}
            if matter.disposition
            else None
        ),
        "closed_on": _iso(matter.closed_at),
        "record_mode": matter.record_mode,
        "stage": (
            {"key": matter.stage.key, "label": matter.stage.label_et} if matter.stage else None
        ),
        "owner": _person(matter.owner),
        "collaborators": collaborators,
        "source_organisations": organisations,
        "addressee_organisation": (
            matter.addressee_organisation.name if matter.addressee_organisation else None
        ),
        "track": (
            {"key": matter.track, "label": str(matter.get_track_display())}
            if matter.track
            else None
        ),
        "received": _dated(matter.received_date, "EXACT", CONFIRMED),
        "brief_summary": _clip(matter.brief_summary, SUMMARY_CHARS) or None,
        "position_summary": _clip(matter.position_summary, SUMMARY_CHARS) or None,
        "current_next_action": _action(current, today) if current is not None else None,
        # There is no «waiting for» field: what Koda waits for is the text of a
        # WAIT action, and nothing else is read as one.
        "waiting_for": (
            _clip(current.text, SUMMARY_CHARS)
            if current is not None and current.kind == "WAIT"
            else None
        ),
        "planned_actions": [_action(action, today) for action in planned[:LIST_LIMIT]],
        "opinion_deadline": (
            {
                **_dated(deadline.value, "EXACT", CONFIRMED, display=deadline.display),
                "label": deadline.label,
                "is_past": deadline.is_past,
                "days_remaining": deadline.days_remaining,
                "is_overdue": deadline.is_overdue,
                "still_owed": deadline.is_active,
                "settled": deadline.settled or None,
            }
            if deadline is not None
            else None
        ),
        "upcoming_dates": [_important_date(record, today) for record in facts.upcoming_dates][
            :LIST_LIMIT
        ],
        "past_dates": [_important_date(record, today) for record in facts.past_dates][:LIST_LIMIT],
        "commencements": [_effective_date(record, today) for record in facts.effective_dates][
            :LIST_LIMIT
        ],
        "submissions_sent": [_submission(submission) for submission in sent],
        "open_feedback_waits": [
            {
                "id": ids.format_source_id(ids.ENGAGEMENT, wait.pk),
                "title": _clip(wait.title, TITLE_CHARS),
                "kind_label": str(wait.get_kind_display()),
                "feedback_until": _dated(wait.feedback_deadline, "EXACT", PLANNED),
            }
            for wait in waits
        ],
        "latest_developments": [_development(record, today) for record in developments],
        "work_victories": [
            {
                "id": ids.format_source_id(ids.WORK_VICTORY, record.pk),
                "title": _clip(record.title, TITLE_CHARS),
                "period": record.display_period or None,
            }
            for record in victories[:LIST_LIMIT]
        ],
        "counts": {
            "documents": Document.objects.filter(matter=matter).visible_to(user).count(),
            "entries": Entry.objects.filter(matter=matter).visible_to(user).count(),
            # Every send that went out (withdrawn or superseded later included),
            # counted in the database; `opinions_standing` is those still SENT.
            "opinions_sent": int(sent_counts["total"] or 0) - int(sent_counts["no_comments"] or 0),
            "no_comments_replies_sent": int(sent_counts["no_comments"] or 0),
            "opinions_standing": int(sent_counts["opinions_standing"] or 0),
            "historical_pages": visible_links(user).filter(matter=matter).count(),
            "important_dates": len(facts.upcoming_dates) + len(facts.past_dates),
            "commencements": len(facts.effective_dates),
        },
        "missing": missing,
        "as_of": today.isoformat(),
    }


def matter_snapshot(user: Any, matter: str) -> dict[str, Any]:
    """`get_matter`: where a Matter stands, every value labelled for what it is."""
    _require_reader(user)
    record = resolve_matter(user, matter)
    return _snapshot(user, record, _today())


def _deadline_line(deadline: dict[str, Any]) -> str:
    line = f"{deadline['display']} (confirmed)"
    return f"{line}; settled: {deadline['settled']}" if deadline["settled"] else line


def _sent_line(item: dict[str, Any], when: str) -> str:
    kind = item["kind_label"]
    if not item["is_opinion"]:
        kind = f"{kind} (official no-comments reply, not an opinion)"
    recipients = ", ".join(item["recipients"]) or "—"
    return f"- {kind}: {item['title']}, {when}, to {recipients}"


def _snapshot_text(snapshot: dict[str, Any]) -> str:
    """The snapshot as plain text, for `fetch` of a ``matter:`` id."""

    def when(value: dict[str, Any] | None) -> str:
        if not value or not value.get("date"):
            return "not recorded"
        return f"{value['display']} ({value['certainty']})"

    action = snapshot["current_next_action"]
    deadline = snapshot["opinion_deadline"]
    parts = [
        _lines(
            ("Reference", snapshot["reference"]),
            ("Title", snapshot["title"]),
            ("Open", "yes" if snapshot["is_open"] else "no"),
            ("Work status", snapshot["work_status"]["label"]),
            ("Disposition", (snapshot["disposition"] or {}).get("label")),
            ("Hetkeseis (stage)", (snapshot["stage"] or {}).get("label") or "not recorded"),
            ("Vastutaja (owner)", snapshot["owner"] or "not recorded"),
            ("Source organisations", ", ".join(snapshot["source_organisations"])),
            ("Addressee", snapshot["addressee_organisation"]),
            ("Received", when(snapshot["received"])),
            ("Summary", snapshot["brief_summary"]),
            ("Koda position", snapshot["position_summary"]),
        ),
        "Current next action: "
        + (
            f"{action['kind']} ({action['kind_label']}) — {action['text']}; date: "
            f"{when(action['date'])}, {action['date']['semantics_label']}"
            if action
            else "none recorded"
        ),
        "Opinion deadline: " + (_deadline_line(deadline) if deadline else "not recorded"),
    ]
    if snapshot["planned_actions"]:
        parts.append(
            "Planned actions:\n"
            + "\n".join(
                f"- {item['text']} ({when(item['date'])})" for item in snapshot["planned_actions"]
            )
        )
    if snapshot["upcoming_dates"]:
        parts.append(
            "Upcoming dates:\n"
            + "\n".join(
                f"- {item['title']}: {when(item['date'])}" for item in snapshot["upcoming_dates"]
            )
        )
    if snapshot["commencements"]:
        parts.append(
            "Commencements (jõustumine):\n"
            + "\n".join(
                f"- {item['what']}: {when(item['date'])}; {item['timing_note']}"
                for item in snapshot["commencements"]
            )
        )
    if snapshot["submissions_sent"]:
        parts.append(
            "Sent:\n"
            + "\n".join(
                _sent_line(item, when(item["sent"])) for item in snapshot["submissions_sent"]
            )
        )
    if snapshot["latest_developments"]:
        parts.append(
            "Latest developments:\n"
            + "\n".join(
                f"- {item['title'] or '(no title)'} ({when(item['date'])})"
                for item in snapshot["latest_developments"]
            )
        )
    if snapshot["missing"]:
        parts.append("Not recorded: " + ", ".join(snapshot["missing"]))
    shortened = [
        f"{name} ({snapshot['list_totals'][name]['shown']} of "
        f"{snapshot['list_totals'][name]['total']})"
        for name in snapshot.get("truncated_lists", [])
    ]
    if shortened:
        parts.append("Shortened lists: " + ", ".join(shortened))
    return "\n\n".join(part for part in parts if part)


# ---------------------------------------------------------------------------
# get_matter_activity — Teema käik
# ---------------------------------------------------------------------------

_ACTIVITY_KINDS = {
    "all": "koik",
    "koik": "koik",
    "entries": "sissekanded",
    "sissekanded": "sissekanded",
    "events": "sundmused",
    "sundmused": "sundmused",
}


def _record_date(record: Any) -> tuple[date | None, str]:
    for field, precision_field in (
        ("occurred_on", "occurred_on_precision"),
        ("stated_on", "stated_on_precision"),
        ("date_value", "date_precision"),
        ("period_date", "date_precision"),
        ("published_on", ""),
    ):
        if hasattr(record, field):
            value = getattr(record, field)
            precision = getattr(record, precision_field, "EXACT") if precision_field else "EXACT"
            return value, precision or "EXACT"
    if hasattr(record, "sent_at"):
        return _local_date(record.sent_at), "EXACT"
    return None, "EXACT"


_RECORD_PREFIXES: dict[str, str] = {
    "MatterProceduralDevelopment": ids.DEVELOPMENT,
    "MatterEngagement": ids.ENGAGEMENT,
    "MatterExternalPosition": ids.EXTERNAL_POSITION,
    "MatterWebsiteOverview": ids.WEBSITE_OVERVIEW,
    "MatterImportantDate": ids.IMPORTANT_DATE,
    "MatterEffectiveDate": ids.EFFECTIVE_DATE,
    "MatterWorkVictory": ids.WORK_VICTORY,
    "Submission": ids.SUBMISSION,
}


def _commencement_line(facts: dict[str, Any]) -> str:
    """A commencement in neutral words: scheduled, a recorded date, or a previous record.

    Never «Jõustus» / «entered into force»: Juristid records the date the act
    names, not whether it took effect (docs/adr/0185 §2, MCPSEC-04).
    """
    when = facts["date"]["display"] or "date not recorded"
    what = facts["what"] or "commencement"
    if facts["date"]["certainty"] == PREVIOUS:
        return f"Previous commencement record ({facts['status_label']}): {what}, {when}"
    timing = facts["timing"]
    if timing == "upcoming":
        return f"Scheduled to enter into force on {when}: {what}"
    if timing == "in_period":
        return (
            f"Recorded commencement period {when} has begun: {what} (the exact day is not recorded)"
        )
    if timing == "past":
        return (
            f"Recorded commencement date {when}: {what} (passed; Juristid does not record "
            "whether it actually entered into force)"
        )
    return f"Commencement, no date recorded: {what}"


def _no_longer_stands(record: Any) -> bool:
    """A record whose `FactStatus` says it was cancelled or superseded."""
    from app.intelligence.enums import FactStatus

    status = getattr(record, "status", None)
    return status in (FactStatus.CANCELLED, FactStatus.SUPERSEDED)


def _shown_in_activity(item: Any) -> bool:
    """Every chronology row but a work victory the product does not show (MCPSEC-14).

    The chronology draws a victory once it has a `confirmed_at`, which a
    NOT_REALIZED one keeps; the product shows `VISIBLE_VICTORY_STATUS` alone.
    """
    from app.intelligence.selectors import VISIBLE_VICTORY_STATUS

    record = item.record
    if record is None or type(record).__name__ != "MatterWorkVictory":
        return True
    return bool(record.status == VISIBLE_VICTORY_STATUS)


def _activity_item(item: Any, matter: Any, today: date) -> dict[str, Any]:
    """One chronology row in words, with its date, precision, kind and link."""
    day = _local_date(item.occurred_at)
    parts: list[str] = []
    kind = "event"
    source_id = None
    url = links.matter_url(matter.pk)
    precision = "EXACT"
    date_value = day
    display = ""
    certainty = CONFIRMED

    if item.entry is not None:
        kind = "entry"
        entry = item.entry
        source_id = ids.format_source_id(ids.ENTRY, entry.pk)
        url = links.entry_url(matter.pk, entry.pk)
        author = _person(entry.author)
        label = str(entry.get_kind_display())
        parts.append(f"{label}{' · ' + author if author else ''}: {html_to_text(entry.body)}")
    elif item.record is not None:
        record = item.record
        name = type(record).__name__
        kind = {
            "MatterProceduralDevelopment": "development",
            "MatterEngagement": "engagement",
            "MatterExternalPosition": "external_position",
            "MatterWebsiteOverview": "website_overview",
            "MatterImportantDate": "important_date",
            "MatterEffectiveDate": "commencement",
            "MatterWorkVictory": "work_victory",
            "Submission": "submission",
        }.get(name, "record")
        prefix = _RECORD_PREFIXES.get(name)
        if prefix is not None:
            source_id = ids.format_source_id(prefix, record.pk)
        if name == "Submission":
            url = links.submissions_url(matter)
        recorded, precision = _record_date(record)
        if recorded is not None:
            date_value = recorded
        else:
            # The chronology placed it on the day it was recorded; say so.
            precision = "RECORDED_DAY"
    elif item.event is not None:
        kind = "historical_import" if item.imported_documents else "event"

    # What a dated record *is* comes from the record, never from where the
    # chronology drew it (MCPSEC-04): the chronology's milestone carries no
    # certainty, and its lead word for a commencement whose date has passed is
    # «Jõustus» — the browser's word (docs/adr/0123), and a claim this answer
    # must not make (docs/adr/0185 §2).
    record_facts: dict[str, Any] | None = None
    record_name = type(item.record).__name__ if item.record is not None else ""
    if record_name == "MatterEffectiveDate":
        record_facts = _effective_date(item.record, today)
    elif record_name == "MatterImportantDate":
        record_facts = _important_date(item.record, today)

    milestone = item.milestone
    if milestone is not None:
        if record_name == "MatterEffectiveDate" and record_facts is not None:
            line = _commencement_line(record_facts)
        else:
            line = milestone.what
            if milestone.sub:
                line = f"{line} — {milestone.sub}"
        parts.insert(0, line)
        if milestone.own_note:
            parts.append(f"{milestone.own_note_label or 'Märkus'}: {milestone.own_note}")
        display = milestone.display_date
        if milestone.ahead:
            certainty = PLANNED
    if item.entry is None:
        sentence = item.summary_sentence
        actor = _person(item.actor)
        if sentence:
            parts.append(f"{actor + ' ' if actor else ''}{sentence}")
    elif item.besides_the_note:
        parts.append(item.besides_the_note)
    if item.stage_effect:
        parts.append(f"Hetkeseis: {item.stage_effect}")
    if item.next_step is not None:
        parts.append(
            f"Järgmine samm: {item.next_step.text}"
            + (f" ({item.next_step.date_value})" if item.next_step.date_value else "")
        )
    if item.answered_deadline:
        parts.append(f"Vastas tähtajale: {item.answered_deadline}")
    if item.files:
        names = [file.label for file in item.files[:LIST_LIMIT]]
        more = len(item.files) - len(names)
        parts.append("Failid: " + ", ".join(names) + (f" (+{more})" if more > 0 else ""))
    if is_approximate(precision) and precision != "RECORDED_DAY" and certainty == CONFIRMED:
        certainty = ESTIMATED
    if record_facts is not None:
        # The same label `get_matter` gives the same record: cancelled or
        # superseded → previous, a planned or estimated commencement milestone
        # → planned / estimated, an approximate date → estimated.
        certainty = record_facts["date"]["certainty"]
    elif _no_longer_stands(item.record):
        certainty = PREVIOUS
    elif record_name == "MatterWorkVictory":
        # A confirmed judgement; its period is a reporting label, not a plan.
        certainty = CONFIRMED
    full_text = "\n".join(part for part in parts if part)
    text = _clip(full_text, ACTIVITY_ITEM_CHARS)
    # Cut, it says so: `fetch` of the item's id returns the record whole.
    extra: dict[str, Any] = {"text_truncated": len(full_text.strip()) > ACTIVITY_ITEM_CHARS}
    if record_name == "MatterEffectiveDate" and record_facts is not None:
        extra |= {"timing": record_facts["timing"], "timing_note": record_facts["timing_note"]}
    return {
        **extra,
        "id": source_id,
        "kind": kind,
        "item_type": item.item_type,
        "date": {
            "date": date_value.isoformat() if date_value else None,
            "display": display
            or (
                format_at_precision(date_value, precision)
                if date_value and precision != "RECORDED_DAY"
                else (date_value.isoformat() if date_value else "")
            ),
            "precision": precision,
            "certainty": certainty,
        },
        "text": text,
        "url": url,
    }


def matter_activity(
    user: Any,
    matter: str,
    *,
    since: str | None = None,
    until: str | None = None,
    kind: str = "all",
    page: int = 1,
) -> dict[str, Any]:
    """`get_matter_activity`: Teema käik, newest first by event date, 25 per page.

    `matter_timeline` is the chronology the Teema page renders — its ordering,
    its day-precision placement and its scoping. Date filters apply to each
    row's event date; the chronology is read in growing pages until the
    requested page is complete, the rows are older than ``since``, or
    `ACTIVITY_SCAN_LIMIT` rows have been read.
    """
    from app.matters.timeline import matter_timeline

    _require_reader(user)
    if not isinstance(kind, str) or kind.strip().lower() not in _ACTIVITY_KINDS:
        raise InvalidArgument("kind must be one of: all, entries, events.")
    only = _ACTIVITY_KINDS[kind.strip().lower()]
    number = _page_number(page, maximum=MAX_ACTIVITY_PAGES)
    start = _parse_iso_date(since, "since")
    end = _parse_iso_date(until, "until")
    if start is not None and end is not None and start > end:
        raise InvalidArgument("since must not be after until.")
    record = resolve_matter(user, matter)
    today = _today()

    def inside(item: Any) -> bool:
        if not _shown_in_activity(item):
            return False
        day = _local_date(item.occurred_at)
        if day is None:
            return False
        return (start is None or day >= start) and (end is None or day <= end)

    wanted = number * ACTIVITY_PER_PAGE + 1
    limit = wanted if start is None and end is None else max(wanted, 100)
    scan_limited = False
    while True:
        items, more = matter_timeline(matter=record, user=user, limit=limit, only=only, today=today)
        chosen = [item for item in items if inside(item)]
        if not more or len(chosen) >= wanted:
            break
        oldest = _local_date(items[-1].occurred_at) if items else None
        if start is not None and oldest is not None and oldest < start:
            more = False
            break
        if limit >= ACTIVITY_SCAN_LIMIT:
            scan_limited = True
            break
        limit = min(limit * 4, ACTIVITY_SCAN_LIMIT)

    first = (number - 1) * ACTIVITY_PER_PAGE
    shown = chosen[first : first + ACTIVITY_PER_PAGE]
    has_more = len(chosen) > first + ACTIVITY_PER_PAGE or (more and scan_limited)
    return {
        "matter": _matter_ref(record),
        "kind": {"koik": "all", "sissekanded": "entries", "sundmused": "events"}[only],
        "since": start.isoformat() if start else None,
        "until": end.isoformat() if end else None,
        "page": number,
        "per_page": ACTIVITY_PER_PAGE,
        "has_more": has_more,
        "scan_limited": scan_limited,
        "items": [_activity_item(item, record, today) for item in shown],
    }


# ---------------------------------------------------------------------------
# get_matter_documents
# ---------------------------------------------------------------------------


def _text_derivatives(version_ids: Iterable[Any]) -> dict[Any, Any]:
    """The active text derivative of each version — extracted text, else OCR — in one read."""
    from app.documents.derivatives import DocumentDerivative
    from app.documents.enums import DerivativeKind, DerivativeStatus

    chosen: dict[Any, Any] = {}
    rows = DocumentDerivative.objects.filter(
        version_id__in=list(version_ids),
        kind__in=(DerivativeKind.EXTRACTED_TEXT, DerivativeKind.OCR_TEXT),
        status=DerivativeStatus.ACTIVE,
    ).order_by("version_id", "-built_at")
    for row in rows:
        current = chosen.get(row.version_id)
        if current is None or (
            current.kind == DerivativeKind.OCR_TEXT and row.kind == DerivativeKind.EXTRACTED_TEXT
        ):
            chosen[row.version_id] = row
    return chosen


def matter_documents(user: Any, matter: str, *, page: int = 1) -> dict[str, Any]:
    """`get_matter_documents`: metadata only, newest first, 25 per page. Never bytes."""
    from app.documents.derivatives import DocumentTextFragment
    from app.documents.models import Document
    from app.documents.preview import STATE_LABELS
    from app.submissions.opinions import opinion_document_ids

    _require_reader(user)
    number = _page_number(page)
    record = resolve_matter(user, matter)
    documents = (
        Document.objects.filter(matter=record)
        .visible_to(user)
        .select_related("current_version")
        .order_by("-created_at", "-pk")
    )
    total = documents.count()
    first = (number - 1) * DOCUMENTS_PER_PAGE
    rows = list(documents[first : first + DOCUMENTS_PER_PAGE])
    versions = [row.current_version_id for row in rows if row.current_version_id]
    derivatives = _text_derivatives(versions)
    # Counted from the fragments themselves: `DocumentDerivative.fragment_count`
    # is the parser's own figure, and a count the reader can be shown should be
    # the rows `fetch` will actually return.
    fragment_counts = dict(
        DocumentTextFragment.objects.filter(derivative__in=list(derivatives.values()))
        .values("derivative_id")
        .annotate(count=Count("pk"))
        .values_list("derivative_id", "count")
    )
    opinions = opinion_document_ids(record, viewer=user) if rows else set()
    answer = []
    for document in rows:
        version = document.current_version
        derivative = derivatives.get(document.current_version_id)
        count = fragment_counts.get(derivative.pk, 0) if derivative is not None else 0
        has_text = derivative is not None and count > 0
        answer.append(
            {
                "id": ids.format_source_id(ids.DOCUMENT, document.pk),
                "title": _clip(document.title, TITLE_CHARS),
                "role": document.role,
                "role_label": str(document.get_role_display()),
                "is_koda_opinion": document.pk in opinions,
                "original_filename": version.original_filename if version else None,
                "added_on": _iso(document.created_at),
                "acquired_on": _iso(version.acquired_at) if version else None,
                "version_number": version.version_number if version else None,
                "mime_type": version.mime_type if version else None,
                "extraction_state": version.extraction_state if version else None,
                "extraction_state_label": (
                    STATE_LABELS.get(version.extraction_state, version.extraction_state)
                    if version
                    else None
                ),
                "text_available": has_text,
                "fragment_count": count,
                "fetch_id": ids.format_source_id(ids.DOCUMENT, document.pk) if has_text else None,
                "url": links.document_url(document.pk),
            }
        )
    return {
        "matter": _matter_ref(record),
        "documents_url": links.matter_documents_url(record.pk),
        "page": number,
        "per_page": DOCUMENTS_PER_PAGE,
        "total": total,
        "has_more": first + DOCUMENTS_PER_PAGE < total,
        "documents": answer,
    }


# ---------------------------------------------------------------------------
# get_upcoming_commencements
# ---------------------------------------------------------------------------


def upcoming_commencements(
    user: Any,
    *,
    start: str | None = None,
    end: str | None = None,
    page: int = 1,
) -> dict[str, Any]:
    """Active commencements inside a window, by the register's containment rule.

    Default window: today and the 29 days after it — the register's own «Järgmise
    30 päeva jooksul» (`SOON_DAYS`, 30 calendar dates including today). At most
    400 days. A period counts only when it lies wholly inside the window
    (`commencement_window_q`); `GENERAL_ORDER` and `UNKNOWN` carry no date and so
    are in no window.

    Separately, as **information** and never as commencements: `Oluline tähtaeg`
    rows of the kinds `PLANNED_COMMENCEMENT` (a draft's own planned date) and
    `ESTIMATED_COMMENCEMENT` (somebody's estimate), by the same containment rule.
    """
    from app.intelligence.commencements import active_commencements, soon_last_day
    from app.intelligence.enums import INFORMATIONAL_IMPORTANT_DATE_KINDS, FactStatus
    from app.intelligence.models import MatterImportantDate

    _require_reader(user)
    number = _page_number(page)
    today = _today()
    first_day = _parse_iso_date(start, "from") or today
    last_day = _parse_iso_date(end, "to")
    if last_day is None:
        last_day = soon_last_day(first_day) if start is not None else soon_last_day(today)
    if last_day < first_day:
        raise InvalidArgument("to must not be before from.")
    if (last_day - first_day).days > MAX_COMMENCEMENT_SPAN_DAYS:
        raise InvalidArgument(f"The window may span at most {MAX_COMMENCEMENT_SPAN_DAYS} days.")

    matters = _visible_matters(user)
    records = active_commencements(user, matters, (first_day, last_day))
    records.sort(
        key=lambda record: (
            record.date_value,
            record.period_end,
            record.matter.reference_year or 0,
            record.matter.reference_number or 0,
            str(record.pk),
        )
    )
    offset = (number - 1) * COMMENCEMENTS_PER_PAGE
    shown = records[offset : offset + COMMENCEMENTS_PER_PAGE]

    informational_qs = (
        MatterImportantDate.objects.visible_to(user)
        .filter(
            status=FactStatus.ACTIVE,
            kind__in=sorted(INFORMATIONAL_IMPORTANT_DATE_KINDS),
            matter__in=matters.order_by().values("pk"),
            date_value__gte=first_day,
            period_end__lte=last_day,
        )
        .select_related("matter", "matter__owner")
        .order_by("date_value", "period_end", "pk")
    )
    informational_total = informational_qs.count()
    informational = list(informational_qs[offset : offset + COMMENCEMENTS_PER_PAGE])

    def owner_of(matter: Any) -> str | None:
        return _person(matter.owner)

    return {
        "from": first_day.isoformat(),
        "to": last_day.isoformat(),
        "as_of": today.isoformat(),
        "rule": "A commencement is listed when its whole date or period lies inside the window.",
        "page": number,
        "per_page": COMMENCEMENTS_PER_PAGE,
        "total": len(records),
        "has_more": offset + COMMENCEMENTS_PER_PAGE < len(records),
        "commencements": [
            {
                **_effective_date(record, today),
                "matter": _matter_ref(record.matter),
                "owner": owner_of(record.matter),
                "url": links.matter_url(record.matter_id),
            }
            for record in shown
        ],
        "informational_total": informational_total,
        "informational_has_more": offset + COMMENCEMENTS_PER_PAGE < informational_total,
        "informational_note": (
            "Planned or estimated commencement dates recorded as milestones. They are not "
            "recorded commencements and must not be reported as facts."
        ),
        "informational": [
            {
                **_important_date(record, today),
                "matter": _matter_ref(record.matter),
                "owner": owner_of(record.matter),
                "url": links.matter_url(record.matter_id),
            }
            for record in informational
        ],
    }


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------


def _live(queryset: QuerySet[Any], path: str = "matter__") -> QuerySet[Any]:
    """Children of Matters that still exist. `Matter.objects` already hides a
    tombstone; a child reached by its own manager is held to the same rule."""
    return queryset.filter(**{f"{path}deleted_at__isnull": True})


def _child_heading(matter: Any, label: str, detail: str) -> str:
    detail = _one_line(detail)
    return (
        f"{_matter_heading(matter)} — {label}: {detail}"
        if detail
        else f"{_matter_heading(matter)} — {label}"
    )


def _fetch_matter(user: Any, source: ids.SourceId) -> dict[str, Any]:
    from app.matters.models import Matter

    matter = (
        Matter.objects.visible_to(user)
        .select_related("owner", "stage", "addressee_organisation")
        .filter(pk=source.pk)
        .first()
    )
    if matter is None:
        raise NotFound()
    snapshot = _snapshot(user, matter, _today())
    return _answer(
        source_id=ids.format_source_id(ids.MATTER, matter.pk),
        title=_matter_heading(matter),
        body=_snapshot_text(snapshot),
        url=links.matter_url(matter.pk),
        metadata={"kind": "matter", "snapshot": snapshot},
        prefix=ids.MATTER,
        pk=matter.pk,
        offset=source.ordinal or 0,
    )


def _fetch_entry(user: Any, source: ids.SourceId) -> dict[str, Any]:
    from app.matters.models import Entry

    entry = (
        _live(Entry.objects.visible_to(user))
        .select_related("matter", "author")
        .filter(pk=source.pk)
        .first()
    )
    if entry is None:
        raise NotFound()
    day = _local_date(entry.occurred_at)
    label = str(entry.get_kind_display())
    body = html_to_text(entry.body)
    header = _lines(("Kind", label), ("Author", _person(entry.author)), ("Date", _iso(day)))
    return _answer(
        source_id=ids.format_source_id(ids.ENTRY, entry.pk),
        title=_child_heading(entry.matter, SearchSourceKind.ENTRY.label, f"{label}, {_iso(day)}"),
        body=f"{header}\n\n{body}",
        url=links.entry_url(entry.matter_id, entry.pk),
        metadata={
            "kind": "entry",
            "entry_kind": entry.kind,
            "matter": _matter_ref(entry.matter),
            "date": _dated(day, "EXACT", CONFIRMED),
            "author": _person(entry.author),
        },
        prefix=ids.ENTRY,
        pk=entry.pk,
        offset=source.ordinal or 0,
    )


def _fetch_submission(user: Any, source: ids.SourceId) -> dict[str, Any]:
    from app.submissions.models import Submission

    submission = (
        _live(Submission.objects.visible_to(user))
        .select_related("matter")
        .prefetch_related("recipients")
        .filter(pk=source.pk)
        .first()
    )
    if submission is None:
        raise NotFound()
    facts = _submission(submission)
    if submission.sent_at is None:
        facts["sent"] = {**_dated(None, "EXACT", MISSING), "time_recorded": False}
    body = _lines(
        ("Kind", facts["kind_label"]),
        (
            "Note",
            "This is Koda's official reply that it has no comments or proposals; it is not an "
            "opinion."
            if facts["is_official_no_comments_reply"]
            else None,
        ),
        ("Title", submission.title),
        ("Status", facts["status_label"]),
        ("Sent", facts["sent"]["display"] or "not sent"),
        ("Recipients", ", ".join(facts["recipients"])),
        ("Channel", submission.channel),
        ("Reference", submission.reference),
        ("Summary", submission.summary),
        ("Notes", submission.notes),
    )
    return _answer(
        source_id=ids.format_source_id(ids.SUBMISSION, submission.pk),
        title=_child_heading(submission.matter, facts["kind_label"], submission.title),
        body=body,
        url=links.submissions_url(submission.matter),
        metadata={"kind": "submission", "matter": _matter_ref(submission.matter), **facts},
        prefix=ids.SUBMISSION,
        pk=submission.pk,
        offset=source.ordinal or 0,
    )


def _fragment_label(row: Any, position: int) -> str:
    return row.locator_label or f"osa {position}"


def _document_text(
    *,
    document: Any,
    derivative: Any,
    start_ordinal: int | None,
    start_fragment: Any = None,
    start_offset: int = 0,
    via_current: bool,
) -> tuple[str, dict[str, Any]]:
    """Fragments in order from a starting point, up to the fetch budget.

    Never cuts a fragment silently. The part ends at a fragment boundary; only
    a single fragment longer than the whole budget is cut, and then the answer
    says exactly where and continues inside it (`frag:<uuid>@<offset>`).
    """
    fragments = derivative.fragments.order_by("ordinal")
    total = fragments.count()
    if start_fragment is not None:
        first_ordinal = start_fragment.ordinal
    else:
        first_ordinal = start_ordinal or 0
    before = fragments.filter(ordinal__lt=first_ordinal).count()
    budget = _fetch_budget()
    pieces: list[str] = []
    used = 0
    covered: list[tuple[int, str]] = []
    next_id: str | None = None
    cut_note: dict[str, Any] | None = None
    batch = 200
    position = before
    offset_in_first = start_offset
    done = False
    cursor_ordinal = first_ordinal
    while not done:
        rows = list(
            fragments.filter(ordinal__gte=cursor_ordinal).only(
                "pk", "ordinal", "text", "locator_label", "text_source"
            )[:batch]
        )
        if not rows:
            break
        for row in rows:
            position += 1
            label = _fragment_label(row, position)
            body = row.text[offset_in_first:] if offset_in_first else row.text
            header = f"[{label}]\n" if not offset_in_first else f"[{label}, continued]\n"
            separator = "\n\n" if pieces else ""
            need = len(separator) + len(header) + len(body)
            if used + need <= budget:
                pieces.append(separator + header + body)
                used += need
                covered.append((position, label))
                offset_in_first = 0
                continue
            if pieces:
                # A fragment boundary: the next part starts with this fragment.
                if via_current:
                    next_id = ids.format_source_id(ids.DOCUMENT, document.pk, row.ordinal)
                else:
                    next_id = ids.format_source_id(ids.FRAGMENT, row.pk)
            else:
                # One fragment longer than the whole budget: cut it, and say where.
                room = max(budget - len(header) - 200, budget // 2)
                cut = _cut_point(body, room)
                start_char = offset_in_first
                pieces.append(header + body[:cut])
                covered.append((position, label))
                end_char = start_char + cut
                next_id = ids.format_source_id(ids.FRAGMENT, row.pk, end_char)
                cut_note = {
                    "locator": label,
                    "characters_returned": f"{start_char + 1}–{end_char}",
                    "fragment_characters": len(row.text),
                }
                pieces.append(
                    f"\n[… {label} continues: characters {end_char + 1}–{len(row.text)} are in the "
                    f"next part]"
                )
            done = True
            break
        else:
            if len(rows) < batch:
                break
            cursor_ordinal = rows[-1].ordinal + 1
    metadata: dict[str, Any] = {
        "text_available": True,
        "truncated": next_id is not None,
        "next_id": next_id,
        "fragments_total": total,
        "fragments_returned": len(covered),
    }
    if covered:
        first_label, last_label = covered[0][1], covered[-1][1]
        span = first_label if first_label == last_label else f"{first_label}–{last_label}"
        metadata["covered"] = f"{span} (parts {covered[0][0]}–{covered[-1][0]} of {total})"
        metadata["first_locator"] = first_label
        metadata["last_locator"] = last_label
    else:
        metadata["covered"] = f"nothing (0 of {total} parts)"
    if cut_note is not None:
        metadata["fragment_cut"] = cut_note
    return "".join(pieces), metadata


def _document_metadata(document: Any, derivative: Any) -> dict[str, Any]:
    version = document.current_version
    return {
        "kind": "document",
        "matter": _matter_ref(document.matter),
        "document_id": ids.format_source_id(ids.DOCUMENT, document.pk),
        "document_title": document.title,
        "role": document.role,
        "role_label": str(document.get_role_display()),
        "original_filename": version.original_filename if version else None,
        "version_number": version.version_number if version else None,
        "acquired_on": _iso(version.acquired_at) if version else None,
        "extraction_state": version.extraction_state if version else None,
        "text_source": derivative.kind if derivative is not None else None,
        "text_is_ocr": bool(derivative is not None and derivative.kind == "OCR_TEXT"),
    }


def _no_text(document: Any, source_id: str) -> dict[str, Any]:
    from app.documents.preview import STATE_LABELS

    version = document.current_version
    state = version.extraction_state if version else None
    explanation = (
        "Juristid holds no extracted text for this document"
        + (f" (state: {STATE_LABELS.get(state, state)})" if state else "")
        + ". Open it in Juristid to read it."
    )
    return {
        "id": source_id,
        "title": _clip(_child_heading(document.matter, "Dokument", document.title), TITLE_CHARS),
        "text": explanation,
        "url": links.document_url(document.pk),
        "metadata": {
            **_document_metadata(document, None),
            "text_available": False,
            "truncated": False,
            "next_id": None,
        },
    }


def _fetch_document(user: Any, source: ids.SourceId) -> dict[str, Any]:
    from app.documents.models import Document

    document = (
        _live(Document.objects.visible_to(user))
        .select_related("matter", "current_version")
        .filter(pk=source.pk)
        .first()
    )
    if document is None:
        raise NotFound()
    source_id = ids.format_source_id(ids.DOCUMENT, document.pk, source.ordinal)
    derivative = None
    if document.current_version is not None:
        derivative = _text_derivatives([document.current_version_id]).get(
            document.current_version_id
        )
    if derivative is None or not derivative.fragments.exists():
        if source.ordinal:
            raise InvalidArgument("The continuation is past the end of this text.")
        return _no_text(document, source_id)
    if (
        source.ordinal is not None
        and not derivative.fragments.filter(ordinal__gte=source.ordinal).exists()
    ):
        raise InvalidArgument("The continuation is past the end of this text.")
    text, bounds = _document_text(
        document=document,
        derivative=derivative,
        start_ordinal=source.ordinal,
        via_current=True,
    )
    return {
        "id": source_id,
        "title": _clip(_child_heading(document.matter, "Dokument", document.title), TITLE_CHARS),
        "text": text,
        "url": links.document_url(document.pk),
        "metadata": {**_document_metadata(document, derivative), **bounds},
    }


def _fetch_fragment(user: Any, source: ids.SourceId) -> dict[str, Any]:
    from app.documents.derivatives import DocumentTextFragment
    from app.documents.enums import DerivativeStatus
    from app.documents.models import Document

    visible = _live(Document.objects.visible_to(user)).values("pk")
    fragment = (
        DocumentTextFragment.objects.filter(
            pk=source.pk,
            derivative__status=DerivativeStatus.ACTIVE,
            derivative__version__document__in=visible,
        )
        .select_related(
            "derivative",
            "derivative__version",
            "derivative__version__document",
            "derivative__version__document__matter",
            "derivative__version__document__current_version",
        )
        .first()
    )
    if fragment is None:
        raise NotFound()
    offset = source.ordinal or 0
    if offset and offset >= len(fragment.text):
        raise InvalidArgument("The continuation is past the end of this text.")
    derivative = fragment.derivative
    document = derivative.version.document
    via_current = derivative.version_id == document.current_version_id
    text, bounds = _document_text(
        document=document,
        derivative=derivative,
        start_ordinal=None,
        start_fragment=fragment,
        start_offset=offset,
        via_current=via_current,
    )
    detail = (
        f"{document.title}, {fragment.locator_label}" if fragment.locator_label else document.title
    )
    return {
        "id": ids.format_source_id(ids.FRAGMENT, fragment.pk, source.ordinal),
        "title": _clip(
            _child_heading(document.matter, SearchSourceKind.DOCUMENT_FRAGMENT.label, detail),
            TITLE_CHARS,
        ),
        "text": text,
        "url": links.document_url(document.pk),
        "metadata": {
            **_document_metadata(document, derivative),
            "locator": fragment.locator_label or None,
            "is_current_version": via_current,
            **bounds,
        },
    }


def _fetch_historical(user: Any, source: ids.SourceId) -> dict[str, Any]:
    from app.legacy_import.historical_views import visible_links

    link = _live(visible_links(user)).filter(pk=source.pk).first()
    if link is None:
        raise NotFound()
    page = link.source_page
    provenance = {
        "source": "OneNote (historical)",
        "notebook": page.source_notebook or None,
        "section": page.source_section or None,
        "location": page.display_location or None,
        "onenote_created_at": page.source_created_at.isoformat()
        if page.source_created_at
        else None,
        "onenote_modified_at": (
            page.source_modified_at.isoformat() if page.source_modified_at else None
        ),
        "dates_note": "These are the OneNote page's own created/modified timestamps from the "
        "historical source, not dates of the events the page describes.",
    }
    header = _lines(
        ("Source", "Historical OneNote page"),
        ("Notebook", page.source_notebook),
        ("Section", page.display_location or page.source_section),
        ("OneNote created (historical source date)", _iso(page.source_created_at)),
        ("OneNote modified (historical source date)", _iso(page.source_modified_at)),
    )
    return _answer(
        source_id=ids.format_source_id(ids.HISTORICAL_PAGE, link.pk),
        title=_child_heading(link.matter, SearchSourceKind.LEGACY_SOURCE_PAGE.label, page.title),
        body=f"{header}\n\n{page.derived_text or ''}".rstrip(),
        url=links.source_page_url(link.pk),
        metadata={
            "kind": "historical_page",
            "matter": _matter_ref(link.matter),
            "page_title": page.title,
            "provenance": provenance,
            "relationship": link.relationship_kind,
        },
        prefix=ids.HISTORICAL_PAGE,
        pk=link.pk,
        offset=source.ordinal or 0,
    )


def _fetch_simple(
    user: Any,
    source: ids.SourceId,
    *,
    model: Any,
    label: str,
    describe: Callable[[Any, date], tuple[str, str, dict[str, Any]]],
    narrow: Callable[[QuerySet[Any]], QuerySet[Any]] | None = None,
) -> dict[str, Any]:
    rows = _live(model.objects.visible_to(user)).select_related("matter")
    if narrow is not None:
        rows = narrow(rows)
    record = rows.filter(pk=source.pk).first()
    if record is None:
        raise NotFound()
    detail, body, extra = describe(record, _today())
    return _answer(
        source_id=ids.format_source_id(source.prefix, record.pk),
        title=_child_heading(record.matter, label, detail),
        body=body,
        url=links.matter_url(record.matter_id),
        metadata={"matter": _matter_ref(record.matter), **extra},
        prefix=source.prefix,
        pk=record.pk,
        offset=source.ordinal or 0,
    )


def _describe_development(record: Any, today: date) -> tuple[str, str, dict[str, Any]]:
    from app.matters.process_phases import phase_label

    facts = _development(record, today)
    body = _lines(
        ("Märge", record.title),
        ("Date", facts["date"]["display"] or "not recorded"),
        ("Date certainty", facts["date"]["certainty"]),
        ("Process phase", phase_label(record.process_phase) if record.process_phase else None),
        ("Juristi märkus (lawyer's own note)", record.note),
    )
    return record.title, body, {"kind": "development", **facts}


def _describe_engagement(record: Any, today: date) -> tuple[str, str, dict[str, Any]]:
    del today
    date_info = _dated(
        record.occurred_on,
        record.occurred_on_precision,
        ESTIMATED if record.has_approximate_date else CONFIRMED,
        display=record.display_date,
    )
    body = _lines(
        ("Kaasamine", record.title),
        ("Kind", str(record.get_kind_display())),
        ("Date", date_info["display"] or "not recorded"),
        ("Feedback awaited until", _iso(record.feedback_deadline)),
        ("Responses", record.response_count),
        ("Note", record.note),
        ("Feedback received", record.feedback_received),
    )
    return (
        record.title,
        body,
        {
            "kind": "engagement",
            "engagement_kind": record.kind,
            "date": date_info,
            "feedback_until": _dated(record.feedback_deadline, "EXACT", PLANNED),
            "open_feedback_wait": bool(record.has_open_feedback_wait),
        },
    )


def _describe_position(record: Any, today: date) -> tuple[str, str, dict[str, Any]]:
    del today
    date_info = _dated(
        record.stated_on,
        record.stated_on_precision,
        ESTIMATED if record.has_approximate_date else CONFIRMED,
        display=record.display_date,
    )
    author = record.author_label
    body = _lines(
        ("Kind", record.kind_label),
        ("Whose position", author),
        ("Date", date_info["display"] or "not recorded"),
        ("Position (the source's statement)", record.summary),
        ("Juristi märkus (Koda lawyer's own note, not the source's)", record.lawyer_note),
    )
    return (
        f"{record.kind_label} · {author}" if author else record.kind_label,
        body,
        {"kind": "external_position", "date": date_info, "author": author or None},
    )


def _describe_overview(record: Any, today: date) -> tuple[str, str, dict[str, Any]]:
    del today
    date_info = _dated(record.published_on, "EXACT", CONFIRMED)
    body = _lines(
        ("Ülevaade / uudis", record.title or record.headline),
        ("Status", str(record.get_status_display())),
        ("Published on", date_info["display"]),
    )
    return (
        record.title or record.headline,
        body,
        {
            "kind": "website_overview",
            "status": record.status,
            "published": date_info,
            "is_published": bool(record.is_published),
        },
    )


def _describe_important_date(record: Any, today: date) -> tuple[str, str, dict[str, Any]]:
    facts = _important_date(record, today)
    body = _lines(
        ("Oluline tähtaeg", record.title),
        ("Kind", facts["kind_label"]),
        ("Date", facts["date"]["display"]),
        ("Certainty", facts["date"]["certainty"]),
        (
            "Note",
            "Informational milestone: a planned or estimated date, not a recorded fact."
            if facts["informational"]
            else None,
        ),
        ("Status", facts["status_label"]),
        ("Explanation", record.note),
    )
    return record.title, body, {"kind": "important_date", **facts}


def _describe_effective_date(record: Any, today: date) -> tuple[str, str, dict[str, Any]]:
    facts = _effective_date(record, today)
    body = _lines(
        ("Jõustumine (commencement)", record.description),
        ("When", facts["date"]["display"]),
        ("Kind", facts["kind_label"]),
        ("Certainty", facts["date"]["certainty"]),
        ("Timing", facts["timing_note"]),
        ("Status", facts["status_label"]),
        ("Official source", record.source_url),
        ("Note", record.note),
    )
    return record.description, body, {"kind": "commencement", **facts}


def _shown_victories(rows: QuerySet[Any]) -> QuerySet[Any]:
    """The product's own rule: only a confirmed `Töövõit` is shown (MCPSEC-14).

    `VISIBLE_VICTORY_STATUS` is what the Teema page, the register, Statistika
    and search read (`app.search.child_indexing.is_shown_work_victory`). A
    candidate is a proposal read only in the review block, NOT_REALIZED a claim
    that did not hold; either answers like a record that does not exist.
    """
    from app.intelligence.selectors import VISIBLE_VICTORY_STATUS

    return rows.filter(status=VISIBLE_VICTORY_STATUS)


def _describe_victory(record: Any, today: date) -> tuple[str, str, dict[str, Any]]:
    del today
    body = _lines(
        ("Töövõit", record.title),
        ("Review status", str(record.get_status_display())),
        ("Period", record.display_period),
        ("Explanation", record.detail),
        ("Note", record.note),
    )
    return (
        record.title,
        body,
        {
            "kind": "work_victory",
            "status": record.status,
            "confirmed": bool(record.is_confirmed),
            # Only shown victories reach here (`_shown_victories`), each a
            # person's confirmed judgement.
            "certainty": CONFIRMED,
        },
    )


def fetch_source(user: Any, source_id: str) -> dict[str, Any]:
    """`fetch`: one source, bounded, with the id of the next part when there is one."""
    _require_reader(user)
    source = _parse_id(source_id)
    from app.intelligence.models import MatterEffectiveDate, MatterImportantDate, MatterWorkVictory
    from app.matters.models import (
        MatterEngagement,
        MatterExternalPosition,
        MatterProceduralDevelopment,
        MatterWebsiteOverview,
    )

    simple: dict[str, tuple[Any, str, Callable[[Any, date], tuple[str, str, dict[str, Any]]]]] = {
        ids.DEVELOPMENT: (MatterProceduralDevelopment, "Märge", _describe_development),
        ids.ENGAGEMENT: (MatterEngagement, "Kaasamine", _describe_engagement),
        ids.EXTERNAL_POSITION: (
            MatterExternalPosition,
            "Arvamus või tagasiside",
            _describe_position,
        ),
        ids.WEBSITE_OVERVIEW: (MatterWebsiteOverview, "Ülevaade / uudis", _describe_overview),
        ids.IMPORTANT_DATE: (MatterImportantDate, "Oluline tähtaeg", _describe_important_date),
        ids.EFFECTIVE_DATE: (MatterEffectiveDate, "Jõustumine", _describe_effective_date),
        ids.WORK_VICTORY: (MatterWorkVictory, "Töövõit", _describe_victory),
    }
    if source.prefix == ids.MATTER:
        return _fetch_matter(user, source)
    if source.prefix == ids.ENTRY:
        return _fetch_entry(user, source)
    if source.prefix == ids.SUBMISSION:
        return _fetch_submission(user, source)
    if source.prefix == ids.DOCUMENT:
        return _fetch_document(user, source)
    if source.prefix == ids.FRAGMENT:
        return _fetch_fragment(user, source)
    if source.prefix == ids.HISTORICAL_PAGE:
        return _fetch_historical(user, source)
    model, label, describe = simple[source.prefix]
    return _fetch_simple(
        user,
        source,
        model=model,
        label=label,
        describe=describe,
        narrow=_shown_victories if source.prefix == ids.WORK_VICTORY else None,
    )


__all__ = [
    "CONFIRMED",
    "ESTIMATED",
    "MISSING",
    "PLANNED",
    "PREVIOUS",
    "InvalidArgument",
    "NotFound",
    "ReaderError",
    "fetch_source",
    "matter_activity",
    "matter_documents",
    "matter_snapshot",
    "resolve_matter",
    "search_records",
    "search_results",
    "upcoming_commencements",
]
