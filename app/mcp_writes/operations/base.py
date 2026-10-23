"""The interface every MCP write operation implements (docs/adr/0189; CONTRACT §4).

An operation is one typed change a client may *propose*: «add a note to
2026_225», «move the opinion deadline of the same request to 20 November».
It never runs because a model asked. The life of one change:

1. `prepare` — the write gateway calls it with the token's user and the
   client's arguments. It resolves the target through the user's own
   visibility (an invisible Matter is a missing one), checks the business
   capability and every rule the canonical service will apply, and answers a
   `Prepared`: the canonical arguments (ids resolved, nothing left to
   interpret), the `state` the change was judged against, and the `Preview`
   the person will approve. **It writes nothing.**
2. The person opens Juristid's approval page, reads the stored preview and
   approves it with their personal authenticator. Nothing else approves.
3. `lock`, then `state` again, then `execute` — the gateway's commit, in one
   transaction: the canonical locks are taken, the state is recomputed under
   them and must equal the stored one (otherwise the proposal is stale and
   refused), and `execute` calls the canonical service(s) — never the ORM
   directly for business rows — passing the proposal's id as the save-once
   token where the service has one (docs/adr/0167).

Every value that crosses a step is JSON-safe (strings, numbers, booleans,
None, lists, dicts), because it is stored in the proposal and compared later.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel


class OperationRefused(Exception):
    """A change that cannot be prepared or executed, and why, safely.

    ``code`` is one of `REFUSAL_CODES`. ``message`` is shown to the client and
    may be shown to the person: it says what is wrong in plain words and never
    repeats confidential record text it was not given by the caller.
    """

    def __init__(self, code: str, message: str) -> None:
        if code not in REFUSAL_CODES:
            raise ValueError(f"unknown refusal code {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message


#: The refusals an operation may raise. The gateway adds its own (stale,
#: expired, …) around them.
REFUSAL_CODES = frozenset(
    {
        "invalid_argument",  # malformed or out-of-range input
        "not_found",  # no such target the user may see
        "forbidden",  # the user may see it but not make this change
        "missing_information",  # a fact the change needs was not given; nothing is invented
        "domain_rule",  # a canonical rule refuses it (closed Matter, date in the future, …)
        "stale",  # the record moved on since the proposal was prepared
    }
)


@dataclass(frozen=True)
class Change:
    """One field the change sets: its Estonian label, what it is now, what it becomes."""

    field: str
    label: str
    current: Any
    proposed: Any


@dataclass(frozen=True)
class Target:
    """The record a change is about, as the person will recognise it."""

    matter_id: str | None
    reference: str
    title: str
    url: str
    record_type: str = ""
    record_id: str = ""
    record_label: str = ""


@dataclass(frozen=True)
class Preview:
    """What the person approves. Estonian, exact, and invented nowhere."""

    #: One line, e.g. «Lisa teemale 2026_225 märge».
    summary: str
    target: Target | None
    changes: list[Change] = field(default_factory=list)
    #: What follows for deadlines, responsibility or the workflow.
    consequences: list[str] = field(default_factory=list)
    #: What explicitly does not change («Teemat ei suleta», «Uut vooru ei avata»).
    unchanged: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Prepared:
    """A change ready to be stored as a proposal."""

    canonical: dict[str, Any]
    state: dict[str, Any]
    preview: Preview
    target_matter_id: uuid.UUID | None = None
    target_record_type: str = ""
    target_record_id: str = ""


@dataclass(frozen=True)
class Executed:
    """What a committed change saved, for the receipt the client gets back."""

    record_type: str
    record_id: str
    matter_id: str | None
    matter_reference: str
    url: str
    #: A JSON-safe description of the saved record (dates, kind, text as saved).
    saved: dict[str, Any] = field(default_factory=dict)


class Operation(Protocol):
    """One typed MCP write. Implementations are stateless singletons in `REGISTRY`."""

    #: Stable, snake_case; the MCP tool is ``prepare_<name>``.
    name: ClassVar[str]
    #: Estonian, for the approval page and the audit view.
    label: ClassVar[str]
    #: The tool's input: a pydantic model with ``extra="forbid"`` and bounded fields.
    arguments: ClassVar[type[BaseModel]]
    #: One paragraph for the tool description: when to choose this operation.
    description: ClassVar[str]

    def prepare(self, *, user: Any, arguments: BaseModel) -> Prepared:
        """Validate and preview; write nothing. Raises `OperationRefused`."""
        ...

    def lock(self, *, canonical: dict[str, Any]) -> None:
        """Take the canonical row locks the service takes, before `state` is recomputed."""
        ...

    def state(self, *, user: Any, canonical: dict[str, Any]) -> dict[str, Any]:
        """The snapshot staleness is judged on: equal at prepare and at commit, or stale."""
        ...

    def execute(self, *, user: Any, canonical: dict[str, Any], once_token: uuid.UUID) -> Executed:
        """Run the canonical service(s) inside the caller's transaction."""
        ...
