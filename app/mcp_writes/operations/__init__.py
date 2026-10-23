"""The registry of MCP write operations (docs/adr/0189; CONTRACT §4).

`REGISTRY` maps an operation's ``name`` to its singleton. The write tools the
MCP server lists, the operations the gateway accepts and the approval page's
re-check all read this one mapping, so an operation exists everywhere or
nowhere.
"""

from __future__ import annotations

from app.mcp_writes.operations.base import Operation

REGISTRY: dict[str, Operation] = {}


def get(name: str) -> Operation | None:
    return REGISTRY.get(name) if isinstance(name, str) else None
