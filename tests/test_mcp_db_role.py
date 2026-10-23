"""The `mcp` process connects as a SELECT-only PostgreSQL role (MCPSEC-03, docs/adr/0185 §1).

`default_transaction_read_only=on` is a session default: anything running in
the process can switch it off. These tests create the role the way an operator
does (`manage.py mcp_db_role --apply --password-stdin`), connect as it, switch
the session default off, and prove every write, truncate and DDL statement is
still refused by the role's privileges — while SELECT works on the business
and token tables, also on a table a later migration adds.

Transactional tests: a role is cluster-wide and the second connection must see
it, so the role is committed — under a random name, dropped afterwards, because
the local cluster is shared by several sessions.
"""

from __future__ import annotations

import io
import secrets
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from psycopg import errors as pg_errors

from app.core import checks
from app.mcp_server import db_role
from tests import factories

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def _apply(role: str, password: str | None) -> str:
    out = io.StringIO()
    args = ["mcp_db_role", "--apply", "--role", role]
    kwargs: dict[str, Any] = {"stdout": out}
    if password is not None:
        args.append("--password-stdin")
        kwargs["stdin"] = io.StringIO(password + "\n")
    call_command(*args, **kwargs)
    return out.getvalue()


def _drop(role: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [role])
        if cursor.fetchone():
            cursor.execute(f'DROP OWNED BY "{role}"')
            cursor.execute(f'DROP ROLE "{role}"')


@pytest.fixture
def mcp_role() -> Iterator[tuple[str, str]]:
    role = f"juristid_mcp_t{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(24)
    try:
        output = _apply(role, password)
        assert password not in output
        yield role, password
    finally:
        _drop(role)


def _connect(role: str, password: str) -> psycopg.Connection[Any]:
    settings_dict = connection.settings_dict
    return psycopg.connect(
        host=settings_dict["HOST"] or "127.0.0.1",
        port=int(settings_dict["PORT"] or 5432),
        dbname=settings_dict["NAME"],
        user=role,
        password=password,
        autocommit=True,
    )


# -- what the role can and cannot do ----------------------------------------------


def test_the_role_reads_business_and_token_tables(mcp_role):
    matter = factories.MatterFactory()
    role, password = mcp_role
    with _connect(role, password) as conn, conn.cursor() as cursor:
        cursor.execute("SELECT id FROM matters_matter WHERE id = %s", [matter.pk])
        assert cursor.fetchone() is not None
        for table in ("mcp_access_mcptoken", "mcp_access_mcpaccessgrant", "accounts_user"):
            cursor.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed names
            assert cursor.fetchone() is not None
        cursor.execute("SHOW default_transaction_read_only")
        assert cursor.fetchone()[0] == "on"


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO mcp_access_mcpaccessgrant (id) VALUES (gen_random_uuid())",
        "UPDATE matters_matter SET title = title",
        "DELETE FROM mcp_access_mcptoken",
        "TRUNCATE mcp_access_mcpaccessgrant",
        "UPDATE accounts_user SET security_epoch = security_epoch + 1",
        "CREATE TABLE public.mcp_role_probe (id integer)",
        "CREATE SCHEMA mcp_role_probe",
        "ALTER TABLE matters_matter ADD COLUMN mcp_role_probe integer",
        "DROP TABLE mcp_access_mcpaccessevent",
        "SELECT session_key FROM django_session",
    ],
)
def test_writes_ddl_and_the_session_table_fail_even_with_read_only_switched_off(
    mcp_role, statement
):
    role, password = mcp_role
    with _connect(role, password) as conn, conn.cursor() as cursor:
        cursor.execute("SET default_transaction_read_only = off")
        cursor.execute("SHOW default_transaction_read_only")
        assert cursor.fetchone()[0] == "off"
        with pytest.raises(pg_errors.InsufficientPrivilege):
            cursor.execute(statement)


def test_begin_read_write_does_not_help_either(mcp_role):
    role, password = mcp_role
    with _connect(role, password) as conn, conn.cursor() as cursor:
        cursor.execute("BEGIN READ WRITE")
        with pytest.raises(pg_errors.InsufficientPrivilege):
            cursor.execute("DELETE FROM mcp_access_mcpaccessgrant")
        cursor.execute("ROLLBACK")


def test_the_role_cannot_become_the_owner(mcp_role):
    role, password = mcp_role
    owner = connection.settings_dict["USER"]
    with _connect(role, password) as conn, conn.cursor() as cursor:
        with pytest.raises(pg_errors.InsufficientPrivilege):
            cursor.execute(f'SET ROLE "{owner}"')


def test_a_table_a_later_migration_adds_is_readable_and_still_not_writable(mcp_role):
    role, password = mcp_role
    with connection.cursor() as cursor:
        cursor.execute("CREATE TABLE public.mcp_role_future (id integer)")
        cursor.execute("INSERT INTO public.mcp_role_future VALUES (1)")
    try:
        with _connect(role, password) as conn, conn.cursor() as cursor:
            cursor.execute("SELECT id FROM public.mcp_role_future")
            assert cursor.fetchall() == [(1,)]
            cursor.execute("SET default_transaction_read_only = off")
            with pytest.raises(pg_errors.InsufficientPrivilege):
                cursor.execute("INSERT INTO public.mcp_role_future VALUES (2)")
    finally:
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE public.mcp_role_future")


def test_applying_again_narrows_a_role_somebody_widened(mcp_role):
    role, password = mcp_role
    with connection.cursor() as cursor:
        cursor.execute(f'GRANT INSERT ON mcp_access_mcpaccessgrant TO "{role}"')
        cursor.execute(f'ALTER ROLE "{role}" CREATEDB')
    with _connect(role, password) as conn, conn.cursor() as cursor:
        assert db_role.privilege_problems(cursor)

    assert "password unchanged" in _apply(role, None)

    with _connect(role, password) as conn, conn.cursor() as cursor:
        assert db_role.privilege_problems(cursor) == []


@pytest.mark.parametrize(
    ("grant", "named"),
    [
        ('GRANT UPDATE (is_active) ON accounts_user TO "{role}"', "owns or may write"),
        ('GRANT INSERT (id) ON mcp_access_mcpaccessgrant TO "{role}"', "owns or may write"),
        ('GRANT REFERENCES (id) ON matters_matter TO "{role}"', "owns or may write"),
        ('GRANT SELECT (session_key) ON django_session TO "{role}"', "may SELECT django_session"),
    ],
    ids=["update-column", "insert-column", "references-column", "session-column"],
)
def test_a_column_level_grant_is_seen_and_applying_again_revokes_it(mcp_role, grant, named):
    """has_table_privilege misses a grant on one column; the check must not (security re-review)."""
    role, password = mcp_role
    with connection.cursor() as cursor:
        cursor.execute(grant.format(role=role))
    with _connect(role, password) as conn, conn.cursor() as cursor:
        problems = db_role.privilege_problems(cursor)
    assert any(named in problem for problem in problems), problems

    _apply(role, None)
    with _connect(role, password) as conn, conn.cursor() as cursor:
        assert db_role.privilege_problems(cursor) == []
        cursor.execute(
            "SELECT count(*) FROM information_schema.column_privileges "
            "WHERE grantee = %s AND privilege_type <> 'SELECT'",
            [role],
        )
        assert cursor.fetchone()[0] == 0, "a column-level write grant survived --apply"


# -- the password ---------------------------------------------------------------


def test_the_password_is_stored_as_a_scram_verifier_never_in_the_clear(mcp_role):
    role, password = mcp_role
    with connection.cursor() as cursor:
        cursor.execute("SELECT rolpassword FROM pg_authid WHERE rolname = %s", [role])
        stored = cursor.fetchone()[0]
    assert stored.startswith("SCRAM-SHA-256$4096:")
    assert password not in stored


def test_the_verifier_is_the_one_libpq_computes():
    password = "Aa1-" + secrets.token_urlsafe(20)
    raw = connection.cursor().connection.pgconn
    expected = raw.encrypt_password(password.encode(), b"anyone", b"scram-sha-256").decode()
    salt = expected.split("$")[1].split(":")[1]
    import base64

    assert db_role.scram_verifier(password, salt=base64.b64decode(salt)) == expected


def test_a_placeholder_or_short_password_changes_nothing():
    role = f"juristid_mcp_t{secrets.token_hex(4)}"
    try:
        for bad in ("replace-me-with-something-long", "short"):
            with pytest.raises(CommandError, match="Nothing was changed"):
                _apply(role, bad)
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [role])
            assert cursor.fetchone() is None
    finally:
        _drop(role)


# -- printing and the guard rails --------------------------------------------------


def test_print_sql_changes_nothing_and_names_no_password():
    out = io.StringIO()
    call_command("mcp_db_role", "--print-sql", "--role", "juristid_mcp", stdout=out)
    text = out.getvalue()
    assert 'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "juristid_mcp";' in text
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE" in text
    assert "NOINHERIT" in text and "default_transaction_read_only = 'on'" in text
    assert 'REVOKE ALL ON TABLE "django_session" FROM "juristid_mcp";' in text
    assert "PASSWORD '" not in text
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = 'juristid_mcp'")
        assert cursor.fetchone() is None


@pytest.mark.parametrize(
    "args",
    [
        ["--print-sql", "--password-stdin"],
        ["--print-sql", "--role", "Bad-Name"],
        ["--print-sql", "--role", "juristid", "--owner", "juristid"],
    ],
)
def test_the_command_refuses_what_it_should(args):
    with pytest.raises(CommandError):
        call_command("mcp_db_role", *args, stdout=io.StringIO(), stdin=io.StringIO("x" * 20))


# -- juristid.E047 ----------------------------------------------------------------


def test_e047_is_silent_for_every_other_process(settings):
    settings.JURISTID_PROCESS_ROLE = ""
    assert checks.check_mcp_database_role() == []


def test_e047_refuses_the_owner_role_on_real_data_and_warns_elsewhere(settings):
    settings.JURISTID_PROCESS_ROLE = "mcp"
    settings.REAL_DATA_ALLOWED = True
    found = checks.check_mcp_database_role()
    assert [type(item).__name__ for item in found] == ["Error"]
    assert found[0].id == "juristid.E047"
    assert "SUPERUSER" in found[0].msg or "owns or may write" in found[0].msg
    settings.REAL_DATA_ALLOWED = False
    assert [type(item).__name__ for item in checks.check_mcp_database_role()] == ["Warning"]


def test_e047_finds_nothing_wrong_with_the_role_the_command_made(mcp_role):
    role, password = mcp_role
    with _connect(role, password) as conn, conn.cursor() as cursor:
        assert db_role.privilege_problems(cursor) == []


def test_privilege_problems_name_what_the_owner_role_holds():
    with connection.cursor() as cursor:
        problems = db_role.privilege_problems(cursor)
    joined = "; ".join(problems)
    assert "owns or may write" in joined
    assert "may SELECT django_session" in joined


def test_run_mcp_server_refuses_to_serve_as_the_owner_role_on_real_data(settings, monkeypatch):
    import uvicorn
    from django.core.management.base import SystemCheckError

    from tests import mcp_server_fakes as fx

    fx.configure(settings)
    settings.JURISTID_PROCESS_ROLE = "mcp"
    settings.REAL_DATA_ALLOWED = True
    served: list[Any] = []
    monkeypatch.setattr(uvicorn.Server, "run", lambda self, sockets=None: served.append(self))

    with pytest.raises(SystemCheckError, match=r"juristid\.E047"):
        call_command("run_mcp_server", stdout=io.StringIO(), stderr=io.StringIO())
    assert served == []


# -- the role's resource bounds (security review NEW-02) ---------------------------


def test_every_login_of_the_role_starts_bounded(mcp_role):
    role, password = mcp_role
    with _connect(role, password) as conn, conn.cursor() as cursor:
        for setting, expected in (
            ("temp_file_limit", "256MB"),
            ("statement_timeout", "1min"),
            ("idle_in_transaction_session_timeout", "1min"),
            ("default_transaction_read_only", "on"),
        ):
            cursor.execute(f"SHOW {setting}")
            assert cursor.fetchone()[0] == expected, setting
        cursor.execute("SELECT rolconnlimit FROM pg_roles WHERE rolname = current_user")
        assert cursor.fetchone()[0] == db_role.CONNECTION_LIMIT


def test_the_role_cannot_raise_its_temporary_file_limit(mcp_role):
    role, password = mcp_role
    with _connect(role, password) as conn, conn.cursor() as cursor:
        with pytest.raises(pg_errors.InsufficientPrivilege):
            cursor.execute("SET temp_file_limit = -1")


def test_a_sort_that_spills_past_the_limit_is_refused(mcp_role):
    """Spilled sorts and hashes are what temp_file_limit bounds (lowered here to 1 MB)."""
    role, password = mcp_role
    with connection.cursor() as cursor:
        cursor.execute(f"ALTER ROLE \"{role}\" SET temp_file_limit = '1MB'")
    with _connect(role, password) as conn, conn.cursor() as cursor:
        cursor.execute("SET work_mem = '64kB'")
        with pytest.raises(pg_errors.ConfigurationLimitExceeded):
            cursor.execute(
                "SELECT count(*) FROM (SELECT g FROM generate_series(1, 2000000) AS g "
                "ORDER BY g DESC) AS sorted"
            )


@pytest.mark.parametrize(
    ("statement", "named"),
    [
        ("ALTER ROLE {role} RESET temp_file_limit", "temp_file_limit"),
        ("ALTER ROLE {role} SET temp_file_limit = -1", "temp_file_limit"),
        ("ALTER ROLE {role} SET temp_file_limit = '1GB'", "temp_file_limit"),
        ("ALTER ROLE {role} SET statement_timeout = 0", "statement_timeout"),
        ("ALTER ROLE {role} RESET idle_in_transaction_session_timeout", "idle_in_transaction"),
        ("ALTER ROLE {role} RESET default_transaction_read_only", "read-only by default"),
    ],
    ids=[
        "no-temp-limit",
        "unlimited-temp",
        "temp-limit-too-high",
        "no-statement-timeout",
        "no-idle-timeout",
        "not-read-only-by-role",
    ],
)
def test_e047_names_a_missing_role_level_bound(mcp_role, statement, named):
    """Read from the catalogue: the connection option would make `SHOW` pass."""
    role, password = mcp_role
    with connection.cursor() as cursor:
        cursor.execute(statement.format(role=f'"{role}"'))
    with _connect(role, password) as conn, conn.cursor() as cursor:
        cursor.execute("SET default_transaction_read_only = on")
        cursor.execute("SET statement_timeout = '60s'")
        problems = db_role.privilege_problems(cursor)
    assert any(named in problem for problem in problems), problems

    _apply(role, None)
    with _connect(role, password) as conn, conn.cursor() as cursor:
        assert db_role.privilege_problems(cursor) == []


def test_the_statement_timeout_follows_the_call_timeout(settings):
    settings.MCP_CALL_TIMEOUT_SECONDS = 25
    out = io.StringIO()
    call_command("mcp_db_role", "--print-sql", "--role", "juristid_mcp", stdout=out)
    text = out.getvalue()
    assert "ALTER ROLE \"juristid_mcp\" SET statement_timeout = '25s';" in text
    assert "ALTER ROLE \"juristid_mcp\" SET idle_in_transaction_session_timeout = '25s';" in text
    assert "ALTER ROLE \"juristid_mcp\" SET temp_file_limit = '256MB';" in text
    out = io.StringIO()
    call_command(
        "mcp_db_role",
        "--print-sql",
        "--role",
        "juristid_mcp",
        "--statement-timeout-seconds",
        "90",
        stdout=out,
    )
    assert "statement_timeout = '90s';" in out.getvalue()


def test_temporary_stays_with_public_so_web_is_untouched():
    """The fix bounds the role; it does not change the database `web` uses."""
    statements = db_role.role_sql(role="juristid_mcp", owner="juristid", database="juristid")
    assert not any(
        "TEMP" in statement.upper() and "REVOKE" in statement for statement in statements
    )
    assert not any("PUBLIC" in statement for statement in statements)
