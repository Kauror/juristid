"""The registry of MCP write operations (docs/adr/0189; CONTRACT §4).

`REGISTRY` maps an operation's ``name`` to its singleton. The write tools the
MCP server lists, the operations the gateway accepts and the approval page's
re-check all read this one mapping, so an operation exists everywhere or
nowhere.

Each operation and the canonical service it calls is described in
docs/mcp/WRITE-OPERATIONS.md.
"""

from __future__ import annotations

from app.mcp_writes.operations.actions import COMPLETE_ACTION, SET_NEXT_ACTION
from app.mcp_writes.operations.base import Operation
from app.mcp_writes.operations.dates import ADD_IMPORTANT_DATE, RESPONSE_DEADLINE
from app.mcp_writes.operations.entries import (
    ADD_MATTER_NOTE,
    ADD_PROCEDURAL_LINK,
    RECORD_MEETING,
)
from app.mcp_writes.operations.matters import ASSIGN_MATTER, CREATE_MATTER, UPDATE_MATTER

REGISTRY: dict[str, Operation] = {
    operation.name: operation
    for operation in (
        CREATE_MATTER,
        UPDATE_MATTER,
        ASSIGN_MATTER,
        ADD_MATTER_NOTE,
        RECORD_MEETING,
        ADD_PROCEDURAL_LINK,
        SET_NEXT_ACTION,
        COMPLETE_ACTION,
        RESPONSE_DEADLINE,
        ADD_IMPORTANT_DATE,
    )
}


def get(name: str) -> Operation | None:
    return REGISTRY.get(name) if isinstance(name, str) else None
