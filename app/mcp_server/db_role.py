"""The `mcp` process's own PostgreSQL role: SELECT only (security review MCPSEC-03).

`default_transaction_read_only=on` is a session default, not a privilege: any
code in the process — a dependency's, a future helper's, an attacker's — can
say `SET default_transaction_read_only = off` or `BEGIN READ WRITE` and write.
Under the application's own role that would be every table, the MCP grants and
tokens included. So the `mcp` service connects as a role of its own that holds
nothing but `SELECT`, and the session default stays as the second lock.

Two halves, both pure functions of a cursor or of names:

* `role_sql` — the idempotent statements an operator runs once, from `web`
  (whose role owns the tables), through ``manage.py mcp_db_role``;
* `privilege_problems` — what the `mcp` process checks about the role it is
  connected as before it serves anything (juristid.E047): not a superuser,
  not a member of another role, owner of nothing, no write, truncate or DDL
  privilege anywhere in the schema, read-only by default, and the role-level
  bounds below in place.

Privileges stop writes; they do not stop a session from using the server's
resources (security review NEW-02). So the role also carries, *as role
settings* — applied at every login, before any code in the process runs:

* `temp_file_limit` (256 MB): any query can spill a sort or a hash to disk,
  and a long scan with a small `work_mem` can fill it. This bounds those
  files per session, and it is superuser-only: the role cannot raise it.
* `statement_timeout` (`MCP_CALL_TIMEOUT_SECONDS`) and
  `idle_in_transaction_session_timeout`: the connection option already sets
  the first; the role setting holds it for a connection opened any other way.
  Both are user-settable, so they bound mistakes, not an attacker with code
  execution in `mcp`.
* `default_transaction_read_only = on`: a session must undo it explicitly.
* `CONNECTION LIMIT`.

Stated as residual (docs/mcp/OPERATIONS.md, *Residual risks*), each needing
code execution inside `mcp`:

* explicit **temporary tables**. TEMPORARY stays with PUBLIC, as on every
  database: revoking it is a database-wide change, left to the owner. A
  session that switches read-only off can create them, and PostgreSQL does
  not count their storage against `temp_file_limit` (its documentation of
  that setting says so);
* **advisory locks** — `pg_advisory_lock` on a key `web` uses blocks its
  writers until the session ends — which no privilege prevents;
* holding connections, up to the limit.

The password never passes through here in the clear on its way to the server:
`scram_verifier` turns it into the SCRAM-SHA-256 verifier PostgreSQL stores,
so neither the statement nor a server log line carries the password itself.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from collections.abc import Sequence
from typing import Any

#: The role name the deployment uses unless told otherwise.
DEFAULT_ROLE = "juristid_mcp"

#: Tables the `mcp` process never needs, holding bearer material for the
#: *browser*: a session key read from here would be a signed-in gate session.
#: `GRANT SELECT ON ALL TABLES` is followed by a REVOKE for each.
WITHHELD_TABLES = ("django_session",)

#: What the `mcp` process must be able to read to verify a token at all.
REQUIRED_READABLE = (
    "mcp_access_mcptoken",
    "mcp_access_mcpaccessgrant",
    "accounts_user",
)

#: More simultaneous connections than MCP_MAX_CONCURRENT_CALLS and the
#: healthcheck need, few enough that a runaway cannot starve `web`.
CONNECTION_LIMIT = 16

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
SCRAM_ITERATIONS = 4096

#: What one session of the role may write to temporary files at once —
#: spilled sorts and hashes, held cursors; not explicit temporary tables,
#: which PostgreSQL does not count. Superuser-only to change.
TEMP_FILE_LIMIT = "256MB"
TEMP_FILE_LIMIT_KB = 256 * 1024

#: The tool-call timeout's default (`MCP_CALL_TIMEOUT_SECONDS`), for callers
#: that do not pass one.
DEFAULT_STATEMENT_TIMEOUT_SECONDS = 60


def role_settings(*, statement_timeout_seconds: int) -> dict[str, str]:
    """The settings `role_sql` gives the role, as PostgreSQL stores them."""
    seconds = max(1, int(statement_timeout_seconds))
    return {
        "default_transaction_read_only": "on",
        "temp_file_limit": TEMP_FILE_LIMIT,
        "statement_timeout": f"{seconds}s",
        "idle_in_transaction_session_timeout": f"{seconds}s",
    }


def valid_identifier(name: str) -> bool:
    """A plain lower-case PostgreSQL identifier: nothing that needs quoting rules."""
    return bool(_IDENTIFIER.match(name or ""))


def quote_identifier(name: str) -> str:
    if not valid_identifier(name):
        raise ValueError(f"not a plain PostgreSQL identifier: {name!r}")
    return f'"{name}"'


def quote_literal(value: str) -> str:
    """A standard-conforming string literal (``standard_conforming_strings`` is on)."""
    if "\x00" in value:
        raise ValueError("a NUL byte cannot be in a PostgreSQL literal")
    return "'" + value.replace("'", "''") + "'"


def scram_verifier(password: str, *, salt: bytes | None = None) -> str:
    """The SCRAM-SHA-256 verifier PostgreSQL stores for ``password`` (RFC 5802, RFC 7677).

    ``ALTER ROLE … PASSWORD 'SCRAM-SHA-256$…'`` stores it as given, so the
    server is never handed the password. Printable ASCII only: SASLprep is the
    identity on it, which is what makes this byte-for-byte what libpq computes
    (`openssl rand -base64` output always is).
    """
    if not password or not all(32 < ord(char) < 127 for char in password):
        raise ValueError("the password must be non-empty printable ASCII without spaces")
    salt = salt if salt is not None else secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("ascii"), salt, SCRAM_ITERATIONS)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()

    def b64(raw: bytes) -> str:
        return base64.b64encode(raw).decode("ascii")

    return f"SCRAM-SHA-256${SCRAM_ITERATIONS}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def role_sql(
    *,
    role: str,
    owner: str,
    database: str,
    password_verifier: str | None = None,
    connection_limit: int = CONNECTION_LIMIT,
    withheld: Sequence[str] = WITHHELD_TABLES,
    statement_timeout_seconds: int = DEFAULT_STATEMENT_TIMEOUT_SECONDS,
) -> list[str]:
    """The statements that make ``role`` a SELECT-only login, idempotently.

    Safe to run again after every release: nothing here fails on a role, a
    grant or a default privilege that already exists, and each run re-asserts
    the attributes — so a role somebody widened by hand is narrowed again.
    ``owner`` is the role the migrations run as (`web`'s); its *future*
    tables become readable through the default privilege, so a release that
    adds a table does not need this re-run before `mcp` can read it.
    """
    r = quote_identifier(role)
    o = quote_identifier(owner)
    d = quote_identifier(database)
    statements = [
        # CREATE ROLE has no IF NOT EXISTS. Every name in these statements is a
        # validated plain identifier, quoted; the one literal is quoted too.
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
        # The second lock and the resource bounds, on the role as well as on
        # the connection (NEW-02). Values are fixed or integers, quoted.
        *(
            f"ALTER ROLE {r} SET {name} = {quote_literal(value)}"
            for name, value in role_settings(
                statement_timeout_seconds=statement_timeout_seconds
            ).items()
        ),
        f"GRANT CONNECT ON DATABASE {d} TO {r}",
        # TEMPORARY stays with PUBLIC, as on every database: revoking it is a
        # database-wide change, left to the owner (NEW-02). A temporary table
        # is the session's own and outlives nothing; read-only by default
        # refuses one until the session undoes it.
        f"REVOKE CREATE ON DATABASE {d} FROM {r}",
        f"REVOKE CREATE ON SCHEMA public FROM {r}",
        f"GRANT USAGE ON SCHEMA public TO {r}",
        # Belt and braces for a role that once held more: everything goes,
        # then SELECT comes back.
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {r}",
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {r}",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {r}",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {o} IN SCHEMA public GRANT SELECT ON TABLES TO {r}",
    ]
    statements += [f"REVOKE ALL ON TABLE {quote_identifier(table)} FROM {r}" for table in withheld]
    if password_verifier is not None:
        statements.append(f"ALTER ROLE {r} PASSWORD {quote_literal(password_verifier)}")
    return statements


def withheld_existing(cursor: Any) -> list[str]:
    """The `WITHHELD_TABLES` that exist in this database (a REVOKE on a missing one fails)."""
    cursor.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relname = ANY(%s)",
        [list(WITHHELD_TABLES)],
    )
    return sorted(row[0] for row in cursor.fetchall())


# -- what the `mcp` process checks about itself ------------------------------------


def privilege_problems(cursor: Any) -> list[str]:
    """Why the role this cursor is connected as is not a SELECT-only role, or [].

    Each problem names a privilege or a table, never a value. Reads the
    catalogue only, so it works on a read-only connection.
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
        # A member can SET ROLE to whatever it is a member of, NOINHERIT or not.
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
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm') "
        "AND (pg_get_userbyid(c.relowner) = current_user "
        "OR has_table_privilege(current_user, c.oid, "
        "'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')) "
        "ORDER BY c.relname"
    )
    writable = [row[0] for row in cursor.fetchall()]
    if writable:
        shown = ", ".join(writable[:5]) + (" …" if len(writable) > 5 else "")
        problems.append(
            f"role {name} owns or may write {len(writable)} table(s) in public: {shown}"
        )
    problems += _readability_problems(cursor, name, REQUIRED_READABLE, must_read=True)
    problems += _readability_problems(cursor, name, WITHHELD_TABLES, must_read=False)
    cursor.execute("SHOW default_transaction_read_only")
    (read_only,) = cursor.fetchone()
    if str(read_only).lower() != "on":
        problems.append("the session is not read-only by default")
    problems += _role_setting_problems(cursor, name)
    return problems


def _role_setting_problems(cursor: Any, name: str) -> list[str]:
    """The role-level bounds `role_sql` sets, read from the catalogue (NEW-02).

    From `pg_db_role_setting`, not `SHOW`: the connection option sets
    read-only and the statement timeout for this very session, so `SHOW`
    would pass for a role that has neither. A setting for this role in this
    database overrides the role's general one, as at login.
    """
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
    if stored.get("default_transaction_read_only", "").lower() not in ("on", "true", "1"):
        problems.append(f"role {name} is not read-only by default as a role setting")
    temp_kb = _kilobytes(stored.get("temp_file_limit"))
    if temp_kb is None or temp_kb < 0 or temp_kb > TEMP_FILE_LIMIT_KB:
        problems.append(f"role {name} has no temp_file_limit of at most {TEMP_FILE_LIMIT}")
    for setting in ("statement_timeout", "idle_in_transaction_session_timeout"):
        milliseconds = _milliseconds(stored.get(setting))
        if milliseconds is None or milliseconds <= 0:
            problems.append(f"role {name} has no {setting} as a role setting")
    return problems


_QUANTITY = re.compile(r"^\s*'?(-?\d+)\s*([a-zA-Z]*)'?\s*$")
_KB_UNITS = {"": 1, "kb": 1, "mb": 1024, "gb": 1024**2, "tb": 1024**3}
_MS_UNITS = {"": 1, "ms": 1, "s": 1000, "min": 60_000, "h": 3_600_000, "d": 86_400_000}


def _kilobytes(value: str | None) -> int | None:
    """A memory setting in kB (its unit when none is given), or None."""
    found = _QUANTITY.match(value or "")
    if not found or found.group(2).lower() not in _KB_UNITS:
        return None
    return int(found.group(1)) * _KB_UNITS[found.group(2).lower()]


def _milliseconds(value: str | None) -> int | None:
    """A time setting in ms (its unit when none is given), or None."""
    found = _QUANTITY.match(value or "")
    if not found or found.group(2).lower() not in _MS_UNITS:
        return None
    return int(found.group(1)) * _MS_UNITS[found.group(2).lower()]


def _readability_problems(
    cursor: Any, name: str, tables: Sequence[str], *, must_read: bool
) -> list[str]:
    cursor.execute(
        "SELECT c.relname, has_table_privilege(current_user, c.oid, 'SELECT') "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relname = ANY(%s)",
        [list(tables)],
    )
    problems = []
    for table, readable in cursor.fetchall():
        if must_read and not readable:
            problems.append(f"role {name} cannot SELECT {table}")
        if not must_read and readable:
            problems.append(f"role {name} may SELECT {table}, which it never needs")
    return problems
