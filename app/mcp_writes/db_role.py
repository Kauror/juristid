"""The `mcp-writer` process's own PostgreSQL role: least privilege for approved writes.

The write gateway (`mcp-writer`, docs/adr/0189) is the one process that executes
an approved MCP proposal, through the canonical services the pages call. It
cannot be read-only — writing is its job — so what bounds it is the role it
connects as, `juristid_mcp_writer`:

* **LOGIN**, and NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOINHERIT,
  NOREPLICATION, NOBYPASSRLS; a member of no role (a member can `SET ROLE` to
  the owner, NOINHERIT or not) and the owner of nothing;
* no CREATE on the database or on schema public, no TRUNCATE, REFERENCES or
  TRIGGER anywhere: no DDL, no bulk wipe;
* SELECT on every table in public **except** `django_session` — the browser's
  bearer material, which a process answering bearer tokens never needs;
* INSERT / UPDATE / DELETE **only** as `WRITE_ALLOWLIST` states, table by
  table, each with the reason. The list is what the canonical services of the
  approved operations actually write, proven by running every one of them
  under this very role (tests/test_mcp_writer_allowlist.py) — plus the
  gateway's own two tables (`GATEWAY_WRITES`);
* **no write, ever**, on the tables `DENIED_WRITE_PREFIXES` and
  `DENIED_WRITE_TABLES` name: accounts and their credentials and second
  factors, every MCP grant, token, authenticator, code, throttle and event,
  the approvals (only `web` writes an approval — that row *is* the human's
  consent, so the process that executes must not be able to forge one), and
  Django's own tables;
* role settings applied at every login: `statement_timeout` and
  `idle_in_transaction_session_timeout` = `MCP_WRITE_CALL_TIMEOUT_SECONDS`,
  `temp_file_limit` (superuser-only to raise) and a `CONNECTION LIMIT`.

Future tables a migration adds become *readable* through the default
privilege, never writable: a release whose operations write a new table adds
it to the allow-list and re-runs ``manage.py mcp_writer_db_role --apply``.

`privilege_problems_writer` is what the `mcp-writer` process checks about the
role it is connected as before it serves anything (juristid.E050): each
problem names a privilege or a table, never a value.

The helpers that do not depend on which role this is — identifier quoting, the
SCRAM-SHA-256 verifier, setting parsers — are the Phase-1 role's
(`app.mcp_server.db_role`), imported rather than copied.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.mcp_server import db_role as read_role
from app.mcp_server.db_role import (
    quote_identifier,
    quote_literal,
    scram_verifier,
    valid_identifier,
)

__all__ = [
    "CONNECTION_LIMIT",
    "DEFAULT_ROLE",
    "DENIED_WRITE_PREFIXES",
    "DENIED_WRITE_TABLES",
    "GATEWAY_WRITES",
    "REQUIRED_READABLE",
    "TEMP_FILE_LIMIT",
    "WITHHELD_TABLES",
    "WRITE_ALLOWLIST",
    "existing_allowlist",
    "privilege_problems_writer",
    "quote_identifier",
    "quote_literal",
    "role_settings",
    "role_sql",
    "scram_verifier",
    "valid_identifier",
    "withheld_existing",
]

#: The role name the deployment uses unless told otherwise.
DEFAULT_ROLE = "juristid_mcp_writer"

#: Readable by nobody but `web`: a session key is a signed-in gate session.
WITHHELD_TABLES = ("django_session",)

#: The gateway's own bookkeeping (CONTRACT §5, agent C's models). A proposal is
#: created at prepare and moved through its statuses at commit; an event is
#: appended and never changed (append-only, so INSERT alone).
GATEWAY_WRITES: dict[str, tuple[str, ...]] = {
    "mcp_writes_mcpwriteproposal": ("INSERT", "UPDATE"),
    "mcp_writes_mcpwriteevent": ("INSERT",),
}

#: Table → the write privileges the role holds on it, and nothing else.
#:
#: Derived by running each approved operation's canonical service(s) under this
#: role and under a statement recorder (tests/test_mcp_writer_allowlist.py);
#: that test fails both when a service needs a grant missing here and when a
#: grant here is used by no operation. UPDATE is also what a row lock
#: (`SELECT … FOR UPDATE`, the Matter lock every canonical write takes) needs.
#: No DELETE anywhere unless an entry says why.
WRITE_ALLOWLIST: dict[str, tuple[str, ...]] = {
    # The Matter itself: created (`create_matter`), retitled and summarised
    # (`set_matter_title`, `set_brief_summary`), its owner and response-deadline
    # columns changed (`assign_matter`, the deadline services), and row-locked
    # by every canonical write (`lock_open_matter_for_business_write`).
    "matters_matter": ("INSERT", "UPDATE"),
    # The `YYYY_N` reference counter `create_matter` allocates from: locked and
    # incremented, and created by `get_or_create` for the first Matter of a year.
    "matters_matterreferencesequence": ("INSERT", "UPDATE"),
    # «Uus asi»: the notice an assignment raises for the new owner, and the
    # previous owner's notice it retires (`create_matter` with an owner,
    # `assign_matter`).
    "matters_matterassignmentnotice": ("INSERT", "UPDATE"),
    # Chronology entries: a note (`add_matter_other`), a meeting
    # (`add_matter_meeting`), the result of a completed step
    # (`complete_current_action`).
    "matters_entry": ("INSERT",),
    # The save-once ledger (docs/adr/0167): the proposal id passed as
    # `once_token` makes a replayed commit save nothing twice.
    "matters_mattersaveonce": ("INSERT",),
    # `record_procedural_link`: the row, and its idempotency lock on an address
    # already recorded (`select_for_update(no_key=True)`).
    "matters_matterprocedurallink": ("INSERT", "UPDATE"),
    # The opinion deadline's request history (`request_response_deadline`,
    # `change_response_deadline`): an append-only record — a moved or replaced
    # request adds a row; the current deadline itself is a Matter column.
    "matters_matterresponsedeadline": ("INSERT",),
    # The current step: a new one, the one it supersedes, the one completed
    # (`set_next_action`, `change_current_action`, `complete_current_action`).
    "workflow_nextaction": ("INSERT", "UPDATE"),
    # `add_important_date`.
    "intelligence_matterimportantdate": ("INSERT",),
    # The change history every canonical service appends to (append-only:
    # audit triggers refuse UPDATE and DELETE whatever the grant says).
    "audit_changeevent": ("INSERT",),
    # The search projection the services refresh in the same transaction
    # (app/search/indexing.py, child_indexing.py). DELETE is the one proven
    # necessary here: a child row's projection is replaced by deleting its
    # stale documents and inserting the new ones. It is derived data, rebuilt
    # by `rebuild_search_index` from the canonical tables, never a record.
    "search_searchdocument": ("INSERT", "UPDATE", "DELETE"),
    **GATEWAY_WRITES,
}

#: Never writable, whatever the allow-list is later edited to say. A prefix
#: covers every table an app has now and adds later.
DENIED_WRITE_PREFIXES = (
    # Users, roles, capabilities, local credentials, MFA secrets.
    "accounts_",
    # Grants, tokens, authenticators, authorization codes, throttles, events.
    "mcp_access_",
    # Sessions, migrations, content types, admin log, the cache table.
    "django_",
    # Django's own groups and permissions.
    "auth_",
)
DENIED_WRITE_TABLES = (
    # The human's consent. Only `web` creates it, on Juristid's own page, with
    # the person's authenticator; the gateway reads it and must not forge one.
    "mcp_writes_mcpwriteapproval",
)

#: What the gateway must read: the token, its grant and its account (it
#: verifies the bearer token itself), and the approval it executes on.
REQUIRED_READABLE = (
    *read_role.REQUIRED_READABLE,
    "mcp_writes_mcpwriteapproval",
    "mcp_writes_mcpwriteproposal",
)

#: MCP_WRITE_MAX_CONCURRENT_CALLS (2) worker threads, the healthcheck, an
#: operator's `manage.py` in the container, and headroom — few enough that a
#: runaway cannot starve `web`.
CONNECTION_LIMIT = 8

#: The writer sorts nothing large; a quarter of the read role's bound.
TEMP_FILE_LIMIT = "64MB"
TEMP_FILE_LIMIT_KB = 64 * 1024

#: `MCP_WRITE_CALL_TIMEOUT_SECONDS`' default, for callers that do not pass one.
DEFAULT_STATEMENT_TIMEOUT_SECONDS = 30

_WRITE_PRIVILEGES = ("INSERT", "UPDATE", "DELETE")


def is_denied(table: str) -> bool:
    """A table the writer must never write, by name."""
    return table in DENIED_WRITE_TABLES or table.startswith(DENIED_WRITE_PREFIXES)


def role_settings(*, statement_timeout_seconds: int) -> dict[str, str]:
    """The settings `role_sql` gives the role, as PostgreSQL stores them."""
    seconds = max(1, int(statement_timeout_seconds))
    return {
        "temp_file_limit": TEMP_FILE_LIMIT,
        "statement_timeout": f"{seconds}s",
        "idle_in_transaction_session_timeout": f"{seconds}s",
    }


def _validated_allowlist(allowlist: Mapping[str, Sequence[str]]) -> dict[str, tuple[str, ...]]:
    checked: dict[str, tuple[str, ...]] = {}
    for table, privileges in allowlist.items():
        if not valid_identifier(table):
            raise ValueError(f"not a plain PostgreSQL identifier: {table!r}")
        if is_denied(table):
            raise ValueError(f"{table} is never writable by the MCP writer role")
        unknown = set(privileges) - set(_WRITE_PRIVILEGES)
        if unknown or not privileges:
            raise ValueError(f"{table}: write privileges must be among {_WRITE_PRIVILEGES}")
        checked[table] = tuple(p for p in _WRITE_PRIVILEGES if p in privileges)
    return checked


def role_sql(
    *,
    role: str,
    owner: str,
    database: str,
    password_verifier: str | None = None,
    connection_limit: int = CONNECTION_LIMIT,
    allowlist: Mapping[str, Sequence[str]] | None = None,
    withheld: Sequence[str] = WITHHELD_TABLES,
    statement_timeout_seconds: int = DEFAULT_STATEMENT_TIMEOUT_SECONDS,
) -> list[str]:
    """The statements that make ``role`` the least-privilege writer, idempotently.

    Safe to run again after every release, and each run re-asserts everything:
    all table and sequence privileges are revoked, SELECT is granted back, then
    exactly the allow-listed writes — so a role somebody widened by hand is
    narrowed again. ``allowlist`` defaults to `WRITE_ALLOWLIST`; the command
    passes only the entries whose tables exist in this database (a GRANT on a
    missing table fails), see `existing_allowlist`.
    """
    r = quote_identifier(role)
    o = quote_identifier(owner)
    d = quote_identifier(database)
    grants = _validated_allowlist(WRITE_ALLOWLIST if allowlist is None else allowlist)
    statements = [
        (
            "DO $$ BEGIN "  # noqa: S608 - validated identifiers, see quote_identifier
            f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {quote_literal(role)}) THEN "
            f"CREATE ROLE {r} LOGIN; "
            "END IF; END $$"
        ),
        (
            f"ALTER ROLE {r} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
            f"NOREPLICATION NOBYPASSRLS CONNECTION LIMIT {int(connection_limit)}"
        ),
        # A member can SET ROLE to whatever it belongs to, NOINHERIT or not:
        # every membership somebody granted by hand is revoked.
        (
            "DO $$ DECLARE parent text; BEGIN "  # noqa: S608 - validated identifiers
            "FOR parent IN SELECT g.rolname FROM pg_auth_members m "
            "JOIN pg_roles g ON g.oid = m.roleid JOIN pg_roles u ON u.oid = m.member "
            f"WHERE u.rolname = {quote_literal(role)} LOOP "
            f"EXECUTE format('REVOKE %I FROM %I', parent, {quote_literal(role)}); "
            "END LOOP; END $$"
        ),
        # A writer that was once given read-only-by-default (or anything else
        # by hand) starts clean; then the bounds.
        f"ALTER ROLE {r} RESET ALL",
        *(
            f"ALTER ROLE {r} SET {name} = {quote_literal(value)}"
            for name, value in role_settings(
                statement_timeout_seconds=statement_timeout_seconds
            ).items()
        ),
        f"GRANT CONNECT ON DATABASE {d} TO {r}",
        f"REVOKE CREATE ON DATABASE {d} FROM {r}",
        f"REVOKE CREATE ON SCHEMA public FROM {r}",
        f"GRANT USAGE ON SCHEMA public TO {r}",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {r}",
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {r}",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {r}",
        # Future tables: readable, never writable by default.
        (
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {o} IN SCHEMA public REVOKE INSERT, UPDATE, "
            f"DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLES FROM {r}"
        ),
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {o} IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {r}",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {o} IN SCHEMA public GRANT SELECT ON TABLES TO {r}",
    ]
    statements += [f"REVOKE ALL ON TABLE {quote_identifier(table)} FROM {r}" for table in withheld]
    statements += [
        f"GRANT {', '.join(privileges)} ON TABLE {quote_identifier(table)} TO {r}"
        for table, privileges in sorted(grants.items())
    ]
    if password_verifier is not None:
        statements.append(f"ALTER ROLE {r} PASSWORD {quote_literal(password_verifier)}")
    return statements


def _existing(cursor: Any, names: Sequence[str]) -> set[str]:
    cursor.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND c.relname = ANY(%s)",
        [list(names)],
    )
    return {row[0] for row in cursor.fetchall()}


def withheld_existing(cursor: Any) -> list[str]:
    """The `WITHHELD_TABLES` that exist in this database."""
    return sorted(_existing(cursor, WITHHELD_TABLES))


def existing_allowlist(
    cursor: Any, allowlist: Mapping[str, Sequence[str]] | None = None
) -> dict[str, tuple[str, ...]]:
    """The allow-list's entries whose tables exist here (the rest wait for their migration)."""
    wanted = dict(WRITE_ALLOWLIST if allowlist is None else allowlist)
    present = _existing(cursor, list(wanted))
    return {table: tuple(wanted[table]) for table in wanted if table in present}


# -- what the `mcp-writer` process checks about itself ------------------------------


def privilege_problems_writer(cursor: Any, *, expected_role: str | None = None) -> list[str]:
    """Why the role this cursor is connected as is not the least-privilege writer, or [].

    Reads the catalogue only. ``expected_role``, when given, is the name the
    role must have (the deployment's `juristid_mcp_writer`).
    """
    problems: list[str] = []
    cursor.execute(
        "SELECT r.rolname, r.rolsuper, r.rolcreaterole, r.rolcreatedb, r.rolbypassrls, "
        "r.rolreplication, "
        "(SELECT count(*) FROM pg_auth_members m WHERE m.member = r.oid) "
        "FROM pg_roles r WHERE r.rolname = current_user"
    )
    row = cursor.fetchone()
    if row is None:  # pragma: no cover - current_user always has a pg_roles row
        return ["the connected role is not in pg_roles"]
    name, superuser, createrole, createdb, bypassrls, replication, memberships = row
    if expected_role is not None and name != expected_role:
        problems.append(f"the connected role is {name}, not {expected_role}")
    for flag, label in (
        (superuser, "SUPERUSER"),
        (createrole, "CREATEROLE"),
        (createdb, "CREATEDB"),
        (bypassrls, "BYPASSRLS"),
        (replication, "REPLICATION"),
    ):
        if flag:
            problems.append(f"role {name} has {label}")
    if memberships:
        problems.append(f"role {name} is a member of {memberships} other role(s)")

    cursor.execute(
        "SELECT has_database_privilege(current_user, current_database(), 'CREATE'), "
        "has_schema_privilege(current_user, 'public', 'CREATE')"
    )
    db_create, schema_create = cursor.fetchone()
    if db_create:
        problems.append(f"role {name} may CREATE in the database")
    if schema_create:
        problems.append(f"role {name} may CREATE in schema public")

    cursor.execute(
        "SELECT c.relname, pg_get_userbyid(c.relowner) = current_user, "
        "has_table_privilege(current_user, c.oid, 'INSERT'), "
        "has_table_privilege(current_user, c.oid, 'UPDATE'), "
        "has_table_privilege(current_user, c.oid, 'DELETE'), "
        "has_table_privilege(current_user, c.oid, 'TRUNCATE,REFERENCES,TRIGGER') "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm') "
        "ORDER BY c.relname"
    )
    owned: list[str] = []
    ddlish: list[str] = []
    denied: list[str] = []
    beyond: list[str] = []
    for table, owns, insert, update, delete, other in cursor.fetchall():
        if owns:
            owned.append(table)
        if other:
            ddlish.append(table)
        held = {
            p for p, flag in zip(_WRITE_PRIVILEGES, (insert, update, delete), strict=True) if flag
        }
        if held and is_denied(table):
            denied.append(f"{table} ({', '.join(sorted(held))})")
        elif held - set(WRITE_ALLOWLIST.get(table, ())):
            beyond.append(
                f"{table} ({', '.join(sorted(held - set(WRITE_ALLOWLIST.get(table, ()))))})"
            )
    for tables, label in (
        (owned, "owns"),
        (ddlish, "may TRUNCATE, REFERENCE or add triggers to"),
        (denied, "may write tables it must never write:"),
        (beyond, "may write beyond its allow-list:"),
    ):
        if tables:
            shown = ", ".join(tables[:5]) + (" …" if len(tables) > 5 else "")
            problems.append(f"role {name} {label} {len(tables)} table(s): {shown}")

    cursor.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'S' "
        "AND (pg_get_userbyid(c.relowner) = current_user "
        "OR has_sequence_privilege(current_user, c.oid, 'UPDATE')) ORDER BY c.relname"
    )
    sequences = [row[0] for row in cursor.fetchall()]
    if sequences:
        problems.append(
            f"role {name} owns or may set {len(sequences)} sequence(s): {', '.join(sequences[:5])}"
        )

    cursor.execute(
        "SELECT DISTINCT a.privilege_type FROM pg_default_acl d, aclexplode(d.defaclacl) a "
        "WHERE a.grantee = (SELECT oid FROM pg_roles WHERE rolname = current_user) "
        "AND a.privilege_type NOT IN ('SELECT', 'USAGE') ORDER BY 1"
    )
    defaults = [row[0] for row in cursor.fetchall()]
    if defaults:
        problems.append(
            f"role {name} is granted {', '.join(defaults)} on future objects by default"
        )

    problems += _readability_problems(cursor, name)
    problems += _required_write_problems(cursor, name)
    problems += _role_setting_problems(cursor, name)
    return problems


def _readability_problems(cursor: Any, name: str) -> list[str]:
    cursor.execute(
        "SELECT c.relname, has_table_privilege(current_user, c.oid, 'SELECT') "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relname = ANY(%s)",
        [[*REQUIRED_READABLE, *WITHHELD_TABLES]],
    )
    problems = []
    for table, readable in cursor.fetchall():
        if table in WITHHELD_TABLES and readable:
            problems.append(f"role {name} may SELECT {table}, which it never needs")
        elif table not in WITHHELD_TABLES and not readable:
            problems.append(f"role {name} cannot SELECT {table}")
    return problems


def _required_write_problems(cursor: Any, name: str) -> list[str]:
    """The gateway's own tables must be writable as listed, where they exist.

    A release that adds them and forgets to re-run the command would otherwise
    start a gateway that refuses its first prepare.
    """
    problems = []
    for table, privileges in GATEWAY_WRITES.items():
        cursor.execute(
            "SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = %s",
            [table],
        )
        found = cursor.fetchone()
        if found is None:
            continue
        for privilege in privileges:
            cursor.execute(
                "SELECT has_table_privilege(current_user, %s::oid, %s)", [found[0], privilege]
            )
            if not cursor.fetchone()[0]:
                problems.append(f"role {name} cannot {privilege} {table}")
    return problems


def _role_setting_problems(cursor: Any, name: str) -> list[str]:
    """The role-level bounds `role_sql` sets, read from the catalogue, not `SHOW`."""
    cursor.execute(
        "SELECT s.setconfig FROM pg_db_role_setting s "
        "JOIN pg_roles r ON r.oid = s.setrole "
        "WHERE r.rolname = current_user AND s.setdatabase IN "
        "(0, (SELECT oid FROM pg_database WHERE datname = current_database())) "
        "ORDER BY s.setdatabase"
    )
    stored: dict[str, str] = {}
    for (config,) in cursor.fetchall():
        for item in config or []:
            key, _, value = str(item).partition("=")
            stored[key.strip().lower()] = value.strip()
    problems = []
    temp_kb = read_role._kilobytes(stored.get("temp_file_limit"))
    if temp_kb is None or temp_kb < 0 or temp_kb > TEMP_FILE_LIMIT_KB:
        problems.append(f"role {name} has no temp_file_limit of at most {TEMP_FILE_LIMIT}")
    for setting in ("statement_timeout", "idle_in_transaction_session_timeout"):
        milliseconds = read_role._milliseconds(stored.get(setting))
        if milliseconds is None or milliseconds <= 0:
            problems.append(f"role {name} has no {setting} as a role setting")
    cursor.execute("SELECT rolconnlimit FROM pg_roles WHERE rolname = current_user")
    (limit,) = cursor.fetchone()
    if limit < 0 or limit > CONNECTION_LIMIT:
        problems.append(f"role {name} has no CONNECTION LIMIT of at most {CONNECTION_LIMIT}")
    return problems
