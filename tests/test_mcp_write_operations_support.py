"""The harness the MCP write-operation tests share, and the registry's own contract.

`commit` plays the gateway's commit (CONTRACT §5) without the gateway: one
transaction, `lock`, `state` recomputed under the lock and compared, then
`execute` with a fresh once-token. `no_writes` proves `prepare` wrote nothing —
by the SQL it sent, not only by row counts. `written_tables` records which
tables an `execute` inserted into, updated or deleted from, which is the
evidence the least-privilege `juristid_mcp_writer` role is derived from
(CONTRACT §1, agent E): every operation test asserts its tables are a subset of
`WRITE_TABLES`, so a canonical service that starts writing somewhere new fails
here first.
"""

from __future__ import annotations

import dataclasses
import json
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from pydantic import BaseModel

from app.mcp_writes.operations import REGISTRY, get
from app.mcp_writes.operations.base import Executed, OperationRefused, Prepared

ORIGIN = "https://juristid.example.test"

_WRITE = re.compile(r'^\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+"?([a-z0-9_]+)"?', re.IGNORECASE)

#: Every table an operation's `execute` may write, observed by `written_tables`
#: in this suite. `matters_matter` and `audit_changeevent` are everybody's: the
#: Matter row is touched by `updated_at` saves and every service records a
#: `ChangeEvent`. The search projection (`search_*`) is refreshed by signals on
#: the rows that are indexed.
COMMON_TABLES = frozenset({"audit_changeevent", "matters_matter"})
WRITE_TABLES: dict[str, frozenset[str]] = {
    "create_matter": frozenset(
        {
            "matters_matter",
            "matters_matterreferencesequence",
            "matters_matterassignmentnotice",
            "workflow_matterplanstep",
            "audit_changeevent",
            "search_searchdocument",
        }
    ),
    "update_matter": frozenset({"matters_matter", "audit_changeevent", "search_searchdocument"}),
    "assign_matter": frozenset(
        {
            "matters_matter",
            "matters_matterassignmentnotice",
            "workflow_nextaction",
            "audit_changeevent",
            "search_searchdocument",
        }
    ),
    "add_matter_note": frozenset(
        {"matters_entry", "matters_mattersaveonce", "audit_changeevent", "search_searchdocument"}
    ),
    "record_meeting": frozenset(
        {"matters_entry", "matters_mattersaveonce", "audit_changeevent", "search_searchdocument"}
    ),
    "add_procedural_link": frozenset(
        {"matters_matterprocedurallink", "audit_changeevent", "search_searchdocument"}
    ),
    "set_next_action": frozenset(
        {"workflow_nextaction", "audit_changeevent", "search_searchdocument"}
    ),
    "complete_action": frozenset(
        {
            "workflow_nextaction",
            "workflow_matterplanstep",
            "workflow_opinionfollowup",
            "matters_entry",
            "audit_changeevent",
            "search_searchdocument",
        }
    ),
    "response_deadline": frozenset(
        {
            "matters_matter",
            "matters_matterresponsedeadline",
            "audit_changeevent",
            "search_searchdocument",
        }
    ),
    "add_important_date": frozenset(
        {"intelligence_matterimportantdate", "audit_changeevent", "search_searchdocument"}
    ),
}


@pytest.fixture
def public_origin(settings: Any) -> None:
    settings.JURISTID_PUBLIC_ORIGIN = ORIGIN


def written(queries: list[dict[str, Any]]) -> set[str]:
    tables = set()
    for query in queries:
        found = _WRITE.match(query["sql"])
        if found:
            tables.add(found.group(1).lower())
    return tables


@contextmanager
def no_writes() -> Iterator[None]:
    """Fail if anything inside sent an INSERT, UPDATE or DELETE."""
    with CaptureQueriesContext(connection) as captured:
        yield
    assert written(captured.captured_queries) == set()


def prepare(name: str, user: Any, **arguments: Any) -> Prepared:
    """Validate the arguments through the model, then prepare — writing nothing."""
    operation = get(name)
    assert operation is not None
    model = operation.arguments.model_validate(arguments)
    with no_writes():
        prepared = operation.prepare(user=user, arguments=model)
    assert_json_safe(prepared)
    return prepared


def refusal(name: str, user: Any, **arguments: Any) -> OperationRefused:
    """The refusal `prepare` raises (and proves it wrote nothing on the way)."""
    operation = get(name)
    assert operation is not None
    model = operation.arguments.model_validate(arguments)
    with no_writes(), pytest.raises(OperationRefused) as caught:
        operation.prepare(user=user, arguments=model)
    return caught.value


def commit(
    name: str, user: Any, prepared: Prepared, *, once_token: uuid.UUID | None = None
) -> Executed:
    """The gateway's commit: lock, state (must be unchanged), execute — one transaction."""
    operation = get(name)
    assert operation is not None
    with transaction.atomic():
        operation.lock(canonical=prepared.canonical)
        now = operation.state(user=user, canonical=prepared.canonical)
        if now != prepared.state:
            raise OperationRefused("stale", "state changed")
        with CaptureQueriesContext(connection) as captured, transaction.atomic():
            executed = operation.execute(
                user=user, canonical=prepared.canonical, once_token=once_token or uuid.uuid4()
            )
    tables = written(captured.captured_queries)
    assert tables <= WRITE_TABLES[name], tables - WRITE_TABLES[name]
    json.dumps(dataclasses.asdict(executed))
    return executed


def execute_refusal(name: str, user: Any, prepared: Prepared) -> OperationRefused:
    """`execute` refusing on its own (no state comparison), leaving nothing behind."""
    operation = get(name)
    assert operation is not None
    with pytest.raises(OperationRefused) as caught, transaction.atomic():
        operation.execute(user=user, canonical=prepared.canonical, once_token=uuid.uuid4())
    return caught.value


def is_stale(name: str, user: Any, prepared: Prepared) -> bool:
    operation = get(name)
    assert operation is not None
    with transaction.atomic():
        operation.lock(canonical=prepared.canonical)
        return operation.state(user=user, canonical=prepared.canonical) != prepared.state


def assert_json_safe(prepared: Prepared) -> None:
    json.dumps(prepared.canonical, sort_keys=True)
    json.dumps(prepared.state, sort_keys=True)
    json.dumps(dataclasses.asdict(prepared.preview), sort_keys=True)


def preview_text(prepared: Prepared) -> str:
    return json.dumps(dataclasses.asdict(prepared.preview), ensure_ascii=False)


def change(prepared: Prepared, field: str) -> Any:
    for item in prepared.preview.changes:
        if item.field == field:
            return item
    raise AssertionError(f"no change {field!r} in {prepared.preview.changes}")


# ---------------------------------------------------------------------------
# The registry's own contract
# ---------------------------------------------------------------------------

EXPECTED = {
    "create_matter",
    "update_matter",
    "assign_matter",
    "add_matter_note",
    "record_meeting",
    "add_procedural_link",
    "set_next_action",
    "complete_action",
    "response_deadline",
    "add_important_date",
}


def test_the_registry_holds_the_ten_operations_by_name() -> None:
    assert set(REGISTRY) == EXPECTED
    for name, operation in REGISTRY.items():
        assert operation.name == name
        assert re.fullmatch(r"[a-z][a-z0-9_]*", name)
        assert len(f"prepare_{name}") <= 64
        assert operation.label.strip()
        assert len(operation.description) > 80
        assert get(name) is operation
    assert get("delete_matter") is None
    assert get(None) is None  # type: ignore[arg-type]


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_argument_model_is_closed_bounded_and_described(name: str) -> None:
    model: type[BaseModel] = REGISTRY[name].arguments
    assert model.model_config.get("extra") == "forbid"
    schema = model.model_json_schema()
    assert schema.get("additionalProperties") is False
    for field, spec in schema["properties"].items():
        assert spec.get("description"), f"{name}.{field} has no description"
        options = spec.get("anyOf", [spec])
        for option in options:
            if option.get("type") == "string" and "enum" not in option:
                assert option.get("maxLength"), f"{name}.{field} is an unbounded string"
    # Nothing names a principal: the user comes from the verified token only.
    for forbidden in ("user", "user_id", "persona", "email", "e_mail", "role", "upn", "actor"):
        assert forbidden not in schema["properties"]


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_unknown_keys_are_refused(name: str) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        REGISTRY[name].arguments.model_validate({"matter": "2026_1", "user_id": "x", "sql": "x"})
