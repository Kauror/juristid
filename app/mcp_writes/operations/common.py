"""What every MCP write operation shares (docs/adr/0189; CONTRACT §4).

Small, explicit helpers rather than a base class: each operation module reads
top to bottom as *resolve → check → preview → state → execute*, and the rules
that must be identical everywhere — who may write, how a Matter is found, how a
closed or archived Matter refuses, how a date is parsed at its precision — are
written once here.

Nothing in this module writes. `lock_matter` takes a row lock (the canonical
one, `lock_matter_for_write`), which is a read with intent and is released by
the caller's transaction.
"""

from __future__ import annotations

import hashlib
import html
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.authorization import may_read_business_content, may_write_business_content
from app.core.errors import DomainError
from app.mcp_writes.operations.base import OperationRefused, Target
from app.workflow.dates import (
    DAY_OUT_OF_RANGE,
    MAX_YEAR,
    MIN_YEAR,
    InvalidPeriod,
    format_at_precision,
    is_supported_day,
    month_bounds,
    quarter_bounds,
    year_bounds,
)
from app.workflow.enums import DatePrecision

#: The longest free text any operation accepts (settings, CONTRACT §2).
MAX_TEXT = int(getattr(settings, "MCP_WRITE_MAX_TEXT_CHARS", 8000))

#: How long a Matter reference or id argument may be.
MATTER_ARGUMENT_MAX = 100

#: A person named by display name.
PERSON_NAME_MAX = 200

MATTER_ARGUMENT_DESCRIPTION = (
    "The Matter (Teema) to change: its reference such as 2026_225, its UUID, or the "
    "'matter:<uuid>' id that search/fetch/get_matter returned. Only Matters you may "
    "read are found."
)

#: The refusal a user without business write gets (READER, ADMINISTRATOR, …).
NOT_A_WRITER = (
    "Your Juristid account may not change business records "
    "(only a specialist or the department head may)."
)
NOT_FOUND = "No such Matter, or it is not visible to you."
CLOSED = (
    "The Matter is closed. Juristid takes no new content on a closed Matter; "
    "reopen it in Juristid first."
)
ARCHIVED = (
    "The Matter is a historical register archive record; MCP does not change archive records."
)
DELETED = "The Matter no longer exists."


class Arguments(BaseModel):
    """Every operation's argument model: closed, stripped, and nothing implicit."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)


def coerce[ArgsT: BaseModel](model: type[ArgsT], arguments: Any) -> ArgsT:
    """The validated model, whether the caller handed the model or a mapping."""
    if isinstance(arguments, model):
        return arguments
    try:
        if isinstance(arguments, BaseModel):
            return model.model_validate(arguments.model_dump())
        return model.model_validate(arguments)
    except ValidationError as error:
        raise OperationRefused("invalid_argument", _validation_message(error)) from None


def _validation_message(error: ValidationError) -> str:
    parts = []
    for item in error.errors()[:5]:
        where = ".".join(str(part) for part in item.get("loc", ())) or "arguments"
        parts.append(f"{where}: {item.get('msg', 'invalid')}")
    return "; ".join(parts) or "Invalid arguments."


# ---------------------------------------------------------------------------
# Who, and which Matter
# ---------------------------------------------------------------------------


def require_writer(user: Any) -> None:
    """`may_write_business_content`, asked at prepare and again at execute."""
    if not may_write_business_content(user):
        raise OperationRefused("forbidden", NOT_A_WRITER)


def resolve_matter(user: Any, reference: str) -> Any:
    """A Matter through this user's own visibility: invisible == not found.

    The read tools' resolver (`app.mcp_server.readers.resolve_matter`), so a
    reference that `get_matter` answers is exactly a reference a write accepts.
    """
    from app.mcp_server.readers import InvalidArgument, NotFound
    from app.mcp_server.readers import resolve_matter as read_resolve

    if not may_read_business_content(user):
        raise OperationRefused("not_found", NOT_FOUND)
    try:
        return read_resolve(user, reference)
    except NotFound:
        raise OperationRefused("not_found", NOT_FOUND) from None
    except InvalidArgument as refusal:
        raise OperationRefused("invalid_argument", str(refusal)) from None


def visible_matter_by_id(user: Any, matter_id: Any) -> Any:
    """The Matter a stored proposal names, still through the user's visibility."""
    from app.matters.models import Matter

    if not may_read_business_content(user):
        raise OperationRefused("not_found", NOT_FOUND)
    found = Matter.objects.visible_to(user).filter(pk=matter_id).first()
    if found is None:
        raise OperationRefused("not_found", NOT_FOUND)
    return found


def require_writable_matter(matter: Any) -> None:
    """A FULL, open, live Matter — the only kind an MCP write touches."""
    from app.matters.enums import RecordMode

    if getattr(matter, "deleted_at", None) is not None:
        raise OperationRefused("not_found", DELETED)
    if matter.record_mode != RecordMode.FULL:
        raise OperationRefused("domain_rule", ARCHIVED)
    if not matter.is_open:
        raise OperationRefused("domain_rule", CLOSED)


def lock_matter(matter_id: Any) -> Any:
    """The canonical Matter row lock (`FOR NO KEY UPDATE`), or not_found."""
    from app.matters.locks import lock_matter_for_write
    from app.matters.models import Matter

    try:
        return lock_matter_for_write(matter_id)
    except Matter.DoesNotExist:
        raise OperationRefused("not_found", DELETED) from None


def matter_target(matter: Any, **record: str) -> Target:
    return Target(
        matter_id=str(matter.pk),
        reference=matter.display_reference or "",
        title=matter.title,
        url=matter_url(matter.pk),
        **record,
    )


def matter_url(matter_id: Any) -> str:
    """The absolute Teema link the read tools give; the bare path if no origin is set."""
    from app.mcp_server import links
    from app.search.links import matter_path

    try:
        return links.matter_url(matter_id)
    except ImproperlyConfigured:
        return matter_path(matter_id)


def matter_core_state(user: Any, matter: Any) -> dict[str, Any]:
    """What every Matter write is judged on: still visible, still writable, still open."""
    return {
        "matter_id": str(matter.pk),
        "visible": bool(may_read_business_content(user)),
        "may_write": bool(may_write_business_content(user)),
        "is_open": bool(matter.is_open),
        "record_mode": str(matter.record_mode),
        "deleted": getattr(matter, "deleted_at", None) is not None,
    }


def state_matter(matter_id: Any) -> Any:
    """The Matter row for a `state` recomputation, tombstones included."""
    from app.matters.models import Matter

    found = Matter.all_objects.filter(pk=matter_id).first()
    if found is None:
        raise OperationRefused("not_found", DELETED)
    return found


@contextmanager
def domain_refusals() -> Iterator[None]:
    """A canonical service's `DomainError` as `OperationRefused("domain_rule")`.

    The service's own Estonian sentence is the message: it names the rule and
    never repeats record text the caller did not give.
    """
    try:
        yield
    except OperationRefused:
        raise
    except DomainError as error:
        raise OperationRefused("domain_rule", str(error)) from None


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


def _normalised_name(value: str) -> str:
    return " ".join((value or "").split()).casefold()


def find_assignable_person(name: str) -> Any:
    """One assignable colleague by display name (or an unambiguous first name).

    Never an e-mail, an id or a role — a person is named the way colleagues name
    each other. An unknown name is `invalid_argument`; two people who answer to
    the same name are `missing_information` with both full names, so the person
    says which one rather than the system guessing.
    """
    from app.accounts.selectors import assignable_business_users

    wanted = _normalised_name(name)
    if not wanted:
        raise OperationRefused("invalid_argument", "Name the responsible person.")
    people = list(assignable_business_users())
    exact = [person for person in people if _normalised_name(person.display_name) == wanted]
    if len(exact) == 1:
        return exact[0]
    candidates = exact or [
        person for person in people if _normalised_name(person.get_short_name()) == wanted
    ]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        names = ", ".join(sorted(person.display_name for person in candidates))
        raise OperationRefused(
            "missing_information",
            f"More than one colleague answers to «{name}»: {names}. Give the full name.",
        )
    raise OperationRefused(
        "invalid_argument",
        f"No active department colleague to whom work may be assigned is called «{name}».",
    )


def person_label(person: Any) -> str | None:
    if person is None:
        return None
    return getattr(person, "display_name", "") or str(person)


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

#: The precisions a person may state for a date they record now (docs/adr/0079 §1):
#: the four the web forms offer. `HALF_YEAR` and `INFERRED` stay stored values
#: and are not offered for new input.
PRECISIONS: dict[str, str] = {
    "exact": DatePrecision.EXACT.value,
    "month": DatePrecision.MONTH.value,
    "quarter": DatePrecision.QUARTER.value,
    "year": DatePrecision.YEAR.value,
}

PERIOD_FORMATS = {
    "exact": "YYYY-MM-DD",
    "month": "YYYY-MM",
    "quarter": "YYYY-Qn (n = 1..4)",
    "year": "YYYY",
}

_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_QUARTER = re.compile(r"^(\d{4})-[Qq]([1-4])$")
_YEAR = re.compile(r"^(\d{4})$")


def parse_day(value: str, name: str) -> date:
    """An ISO calendar day, inside the range every day box accepts."""
    try:
        day = date.fromisoformat(value.strip())
    except (AttributeError, ValueError):
        raise OperationRefused(
            "invalid_argument", f"{name} must be an ISO date (YYYY-MM-DD)."
        ) from None
    if not is_supported_day(day):
        raise OperationRefused("invalid_argument", f"{name}: {DAY_OUT_OF_RANGE}")
    return day


def parse_period(value: str, precision: str, name: str) -> tuple[date, date, str]:
    """``(first day, last day, stored precision)`` for a date stated at a precision.

    The value must be written *at* that precision — ``2026-11`` for a month,
    ``2026-Q4`` for a quarter, ``2026`` for a year — so no day is ever read into
    a period nobody narrowed to a day (docs/adr/0079). The stored anchor is the
    period's first day, as the forms store it.
    """
    if precision not in PRECISIONS:
        raise OperationRefused("invalid_argument", f"Unknown precision {precision!r}.")
    text = (value or "").strip()
    try:
        if precision == "exact":
            day = parse_day(text, name)
            return day, day, DatePrecision.EXACT.value
        if precision == "month":
            found = _MONTH.match(text)
            if found:
                start, end = month_bounds(int(found.group(1)), int(found.group(2)))
                return start, end, DatePrecision.MONTH.value
        elif precision == "quarter":
            found = _QUARTER.match(text)
            if found:
                start, end = quarter_bounds(int(found.group(1)), int(found.group(2)))
                return start, end, DatePrecision.QUARTER.value
        elif precision == "year":
            found = _YEAR.match(text)
            if found:
                start, end = year_bounds(int(found.group(1)))
                return start, end, DatePrecision.YEAR.value
    except InvalidPeriod as error:
        raise OperationRefused(
            "invalid_argument", f"{name}: {error} (years {MIN_YEAR}–{MAX_YEAR})."
        ) from None
    raise OperationRefused(
        "invalid_argument",
        f"{name} must be written as {PERIOD_FORMATS[precision]} for precision {precision!r}.",
    )


def iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def from_iso(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def at_precision(value: date | None, precision: str) -> str:
    return format_at_precision(value, precision) if value is not None else ""


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


def text_to_entry_html(text: str) -> str:
    """Plain text as the small HTML an `Entry` body stores, escaped first.

    An AI client writes text, not markup. Escaping it means a ``<script>`` in
    the text is stored as the words «<script>», never as markup, and blank lines
    become paragraphs and single newlines line breaks — the shape the web
    editor produces. `add_entry` sanitises the result like any other body.
    """
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", (text or "").strip())]
    rendered = [
        "<p>" + "<br>".join(html.escape(line.strip()) for line in part.splitlines()) + "</p>"
        for part in paragraphs
        if part
    ]
    return "".join(rendered)


def digest(value: str | None) -> str:
    """A short stable fingerprint of a text, for state comparison without the text."""
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:24]


def uuid_or_refuse(value: str | None, name: str) -> uuid.UUID | None:
    if value is None:
        return None
    raw = value.strip()
    if raw.startswith("next_action:"):
        raw = raw.split(":", 1)[1]
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise OperationRefused("invalid_argument", f"{name} must be a UUID.") from None
