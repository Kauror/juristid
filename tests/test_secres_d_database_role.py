"""Security review 2026-10-29, assessor D — finding D-02 (P2, CONFIRMED).

`web` and the four workers connect to PostgreSQL as `POSTGRES_USER` from
`juristid.env` — the same name the `db` service is initialised with
(`POSTGRES_USER: ${POSTGRES_USER:-juristid}` in deploy/unraid-main/compose.yml),
and the role the official image creates from that variable is the cluster
**superuser** (README "Restoring": «superuser password is not specified»;
`mcp_db_role` creates the two least-privilege roles *from* it). So the request
handling process — the one that parses every upload and every form — holds
`pg_read_server_files`, `COPY … TO PROGRAM` and `ALTER SYSTEM`: a single SQL
injection or ORM mistake becomes arbitrary file access and command execution
in the database container, and the restore-time refusal of unsafe objects
(ADR 0179 round) guards a door the application itself stands inside.

The MCP roles are checked at start-up for exactly this (`check_mcp_database_role`,
`juristid.E04x`/`E050`: NOSUPERUSER, NOCREATEROLE, member of nothing). The
application's own connection is not checked at all.

Intended fix (infrastructure + a small code change; the role swap on the live
cluster is an announced maintenance step — owner decision):

1. create `juristid_app` LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS,
   `REASSIGN OWNED BY juristid TO juristid_app` (or make it the owner of the
   schema's objects), keep the superuser for `migrate`, backups and
   `mcp_db_role` only;
2. a system check in app/core/checks.py for `JURISTID_PROCESS_ROLE` unset
   (web/workers) on real data: `SELECT rolsuper, rolcreaterole, rolbypassrls
   FROM pg_roles WHERE rolname = current_user` must be all false, an Error
   like the two MCP checks already raise.

This test passed once `check_application_database_role` (juristid.W059) landed with the review.
"""

from __future__ import annotations

import re
from pathlib import Path

CHECKS = Path(__file__).resolve().parents[1] / "app" / "core" / "checks.py"


def _check_functions(source: str) -> dict[str, str]:
    """Each `def check_*` with its body, split on the next top-level def."""
    pieces = re.split(r"^(?=def check_)", source, flags=re.MULTILINE)
    found: dict[str, str] = {}
    for piece in pieces:
        match = re.match(r"def (check_\w+)", piece)
        if match:
            found[match.group(1)] = piece
    return found


def test_the_application_connection_is_checked_not_to_be_a_superuser() -> None:
    source = CHECKS.read_text(encoding="utf-8")
    checks = _check_functions(source)
    assert checks, "the scan found no checks at all"
    # The two MCP roles are checked; the test asks for the same of web/workers.
    mcp_checks = {name for name in checks if "mcp" in name}
    assert mcp_checks, "expected the MCP role checks to exist (the model for this one)"
    application_checks = [
        name for name, body in checks.items() if "mcp" not in name and "rolsuper" in body
    ]
    assert application_checks, (
        "no system check reads `rolsuper` for the application's own database role: "
        "web and the workers run as the PostgreSQL superuser on real data without "
        "anything refusing it (see docstring for the fix)"
    )
