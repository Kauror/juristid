"""The `mcp-writer` process connects as a least-privilege PostgreSQL role (docs/adr/0189).

The role is created the way an operator does (`manage.py mcp_writer_db_role
--apply --password-stdin`), a second connection logs in as it, and these tests
prove what it can and cannot do: it writes exactly the allow-listed tables
(tests/test_mcp_writer_allowlist.py proves that list is what the operations
need), and never an account, an MCP grant or token, an approval, a session or
anything of Django's; it truncates, deletes and creates nothing; it cannot
read `django_session`. And the Phase-1 read role is still SELECT-only beside
it (the regression this round must not cause).

Transactional tests: a role is cluster-wide and the second connection must see
it, so it is committed — under a random name, dropped afterwards, because the
local cluster is shared by several sessions.
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
from app.mcp_server import db_role as read_role
from app.mcp_writes import db_role
from tests import factories

# -- pure: the SQL's shape and the allow-list's own rules -------------------------


def _sql(**kwargs: Any) -> list[str]:
    return db_role.role_sql(
        role="juristid_mcp_writer", owner="juristid", database="juristid", **kwargs
    )


def test_the_role_sql_has_the_attributes_and_bounds_the_contract_names():
    text = ";\n".join(_sql(statement_timeout_seconds=25))
    assert (
        'ALTER ROLE "juristid_mcp_writer" WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
        f"NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT {db_role.CONNECTION_LIMIT}"
    ) in text
    assert "ALTER ROLE \"juristid_mcp_writer\" SET statement_timeout = '25s'" in text
    assert "SET idle_in_transaction_session_timeout = '25s'" in text
    assert f"SET temp_file_limit = '{db_role.TEMP_FILE_LIMIT}'" in text
    assert 'REVOKE CREATE ON DATABASE "juristid" FROM "juristid_mcp_writer"' in text
    assert 'REVOKE CREATE ON SCHEMA public FROM "juristid_mcp_writer"' in text
    assert 'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM "juristid_mcp_writer"' in text
    assert 'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "juristid_mcp_writer"' in text
    assert 'REVOKE ALL ON TABLE "django_session" FROM "juristid_mcp_writer"' in text
    # Future tables readable, never writable by default.
    assert "REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLES" in text
    assert "GRANT SELECT ON TABLES TO" in text
    # The role is never read-only by default: it would refuse every commit.
    assert "default_transaction_read_only" not in text
    assert "PASSWORD '" not in text
    # Nothing grants membership, ownership or TRUNCATE/REFERENCES/TRIGGER.
    grants = [s for s in _sql() if s.startswith("GRANT ") or " GRANT " in s]
    assert " OWNER " not in text
    for statement in grants:
        for word in ('GRANT "', "TRUNCATE", "REFERENCES", "TRIGGER", 'DELETE ON TABLE "m'):
            assert word not in statement, statement


def test_the_grants_are_exactly_the_allow_list():
    grants = [
        statement
        for statement in _sql()
        if statement.startswith("GRANT ") and " ON TABLE " in statement
    ]
    rendered = {
        statement.split(" ON TABLE ")[1].split(" TO ")[0].strip('"'): tuple(
            statement[len("GRANT ") :].split(" ON TABLE ")[0].split(", ")
        )
        for statement in grants
    }
    assert rendered == db_role.WRITE_ALLOWLIST


def test_the_allow_list_never_names_a_denied_table_and_says_why_for_each():
    import inspect

    source = inspect.getsource(db_role)
    block = source.split("WRITE_ALLOWLIST: dict[str, tuple[str, ...]] = {", 1)[1].split("\n}", 1)[0]
    for table, privileges in db_role.WRITE_ALLOWLIST.items():
        assert not db_role.is_denied(table), table
        assert privileges and set(privileges) <= {"INSERT", "UPDATE", "DELETE"}, table
        if table not in db_role.GATEWAY_WRITES:
            line_before = block.split(f'"{table}"', 1)[0].rstrip().splitlines()[-1].strip()
            assert line_before.startswith("#"), f"{table} has no comment saying why"
    deletes = {table for table, granted in db_role.WRITE_ALLOWLIST.items() if "DELETE" in granted}
    assert deletes == {"search_searchdocument"}, "DELETE is proven necessary only there"
    assert db_role.GATEWAY_WRITES == {
        "mcp_writes_mcpwriteproposal": ("INSERT", "UPDATE"),
        "mcp_writes_mcpwriteevent": ("INSERT",),
    }


@pytest.mark.parametrize(
    "table",
    [
        "accounts_user",
        "accounts_localcredential",
        "mcp_access_mcptoken",
        "mcp_access_mcpaccessgrant",
        "mcp_access_mcpauthenticator",
        "mcp_writes_mcpwriteapproval",
        "django_session",
        "django_migrations",
        "auth_permission",
    ],
)
def test_role_sql_refuses_an_allow_list_that_names_a_denied_table(table):
    with pytest.raises(ValueError, match="never writable"):
        _sql(allowlist={table: ("INSERT",)})


@pytest.mark.parametrize(
    "allowlist",
    [{"matters_entry": ("TRUNCATE",)}, {"matters_entry": ()}, {'matters"; DROP': ("INSERT",)}],
)
def test_role_sql_refuses_other_privileges_and_names(allowlist):
    with pytest.raises(ValueError):
        _sql(allowlist=allowlist)


def test_the_writer_reuses_the_read_roles_verifier_and_quoting():
    assert db_role.scram_verifier is read_role.scram_verifier
    assert db_role.quote_identifier is read_role.quote_identifier


# -- against the real local PostgreSQL ---------------------------------------------

transactional = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def _apply(command: str, role: str, password: str | None) -> str:
    out = io.StringIO()
    args = [command, "--apply", "--role", role]
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
def writer_role() -> Iterator[tuple[str, str]]:
    role = f"juristid_mcp_writer_t{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(24)
    try:
        output = _apply("mcp_writer_db_role", role, password)
        assert password not in output
        yield role, password
    finally:
        _drop(role)


@pytest.fixture
def read_role_name() -> Iterator[tuple[str, str]]:
    role = f"juristid_mcp_t{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(24)
    try:
        _apply("mcp_db_role", role, password)
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


def _table_exists(name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s) IS NOT NULL", [f"public.{name}"])
        return bool(cursor.fetchone()[0])


#: Statements on the denied tables, each refused by privilege. The approval
#: table is agent C's (mcp_writes 0001); where it is not migrated yet those
#: cases are skipped, and they run at integration.
DENIED_WRITES = [
    ("accounts_user", "UPDATE accounts_user SET security_epoch = security_epoch + 1"),
    ("accounts_user", "UPDATE accounts_user SET is_active = true WHERE false"),
    ("accounts_user", "INSERT INTO accounts_user DEFAULT VALUES"),
    ("accounts_totpdevice", "UPDATE accounts_totpdevice SET id = id WHERE false"),
    ("accounts_recoverycode", "INSERT INTO accounts_recoverycode DEFAULT VALUES"),
    ("mcp_access_mcptoken", "UPDATE mcp_access_mcptoken SET id = id WHERE false"),
    ("mcp_access_mcptoken", "INSERT INTO mcp_access_mcptoken DEFAULT VALUES"),
    ("mcp_access_mcptoken", "DELETE FROM mcp_access_mcptoken"),
    ("mcp_access_mcpaccessgrant", "UPDATE mcp_access_mcpaccessgrant SET id = id WHERE false"),
    ("mcp_access_mcpaccessgrant", "INSERT INTO mcp_access_mcpaccessgrant DEFAULT VALUES"),
    ("mcp_access_mcpauthenticator", "UPDATE mcp_access_mcpauthenticator SET id = id WHERE false"),
    ("mcp_access_mcpaccessevent", "INSERT INTO mcp_access_mcpaccessevent DEFAULT VALUES"),
    ("mcp_writes_mcpwriteapproval", "INSERT INTO mcp_writes_mcpwriteapproval DEFAULT VALUES"),
    ("mcp_writes_mcpwriteapproval", "UPDATE mcp_writes_mcpwriteapproval SET id = id WHERE false"),
    ("mcp_writes_mcpwriteevent", "UPDATE mcp_writes_mcpwriteevent SET id = id WHERE false"),
    ("mcp_writes_mcpwriteproposal", "DELETE FROM mcp_writes_mcpwriteproposal"),
    ("django_session", "SELECT session_key FROM django_session"),
    ("django_session", "DELETE FROM django_session"),
    ("django_migrations", "INSERT INTO django_migrations DEFAULT VALUES"),
]

DDL_AND_BULK = [
    "DELETE FROM matters_matter WHERE false",
    "DELETE FROM matters_entry WHERE false",
    "DELETE FROM audit_changeevent WHERE false",
    "TRUNCATE search_searchdocument",
    "TRUNCATE matters_entry",
    "CREATE TABLE public.mcp_writer_probe (id integer)",
    "CREATE SCHEMA mcp_writer_probe",
    "ALTER TABLE matters_matter ADD COLUMN mcp_writer_probe integer",
    "DROP TABLE matters_entry",
    "CREATE TRIGGER mcp_writer_probe BEFORE INSERT ON matters_entry "
    "FOR EACH ROW EXECUTE FUNCTION suppress_redundant_updates_trigger()",
    "SELECT setval(pg_get_serial_sequence('django_migrations', 'id'), 1)",
]


@transactional
@pytest.mark.parametrize(("table", "statement"), DENIED_WRITES)
def test_the_writer_cannot_touch_accounts_tokens_grants_approvals_or_sessions(
    writer_role, table, statement
):
    if not _table_exists(table):
        pytest.skip(f"{table} is not migrated on this branch (agent C's model; integration)")
    role, password = writer_role
    with _connect(role, password) as conn, conn.cursor() as cursor:
        with pytest.raises(pg_errors.InsufficientPrivilege):
            cursor.execute(statement)


@transactional
@pytest.mark.parametrize("statement", DDL_AND_BULK)
def test_the_writer_deletes_truncates_and_creates_nothing(writer_role, statement):
    role, password = writer_role
    with _connect(role, password) as conn, conn.cursor() as cursor:
        with pytest.raises(pg_errors.InsufficientPrivilege):
            cursor.execute(statement)


@transactional
def test_the_writer_reads_what_it_verifies_tokens_with_and_writes_an_allowed_table(writer_role):
    matter = factories.MatterFactory()
    role, password = writer_role
    with _connect(role, password) as conn, conn.cursor() as cursor:
        for table in ("mcp_access_mcptoken", "mcp_access_mcpaccessgrant", "accounts_user"):
            cursor.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed names
            assert cursor.fetchone() is not None
        cursor.execute("SHOW default_transaction_read_only")
        assert cursor.fetchone()[0] == "off"
        # A permitted write, rolled back: the allow-list is a grant, not a wish.
        cursor.execute("BEGIN")
        cursor.execute(
            "UPDATE matters_matter SET title = title WHERE id = %s RETURNING id", [matter.pk]
        )
        assert cursor.fetchone() is not None
        cursor.execute("SELECT id FROM matters_matter WHERE id = %s FOR UPDATE", [matter.pk])
        assert cursor.fetchone() is not None
        cursor.execute("ROLLBACK")


@transactional
def test_the_writer_cannot_become_the_owner_or_the_read_role(writer_role, read_role_name):
    role, password = writer_role
    owner = connection.settings_dict["USER"]
    with _connect(role, password) as conn, conn.cursor() as cursor:
        for target in (owner, read_role_name[0]):
            with pytest.raises(pg_errors.InsufficientPrivilege):
                cursor.execute(f'SET ROLE "{target}"')


@transactional
def test_a_table_a_later_migration_adds_is_readable_and_not_writable(writer_role):
    role, password = writer_role
    with connection.cursor() as cursor:
        cursor.execute("CREATE TABLE public.mcp_writer_future (id integer)")
        cursor.execute("INSERT INTO public.mcp_writer_future VALUES (1)")
    try:
        with _connect(role, password) as conn, conn.cursor() as cursor:
            cursor.execute("SELECT id FROM public.mcp_writer_future")
            assert cursor.fetchall() == [(1,)]
            for statement in (
                "INSERT INTO public.mcp_writer_future VALUES (2)",
                "UPDATE public.mcp_writer_future SET id = 3",
                "DELETE FROM public.mcp_writer_future",
            ):
                with pytest.raises(pg_errors.InsufficientPrivilege):
                    cursor.execute(statement)
    finally:
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE public.mcp_writer_future")


@transactional
def test_the_read_role_is_still_select_only_beside_the_writer(writer_role, read_role_name):
    """The Phase-1 role keeps every lock, on the very tables the writer may write."""
    role, password = read_role_name
    with _connect(role, password) as conn, conn.cursor() as cursor:
        assert read_role.privilege_problems(cursor) == []
        cursor.execute("SET default_transaction_read_only = off")
        for table in sorted(db_role.WRITE_ALLOWLIST):
            if not _table_exists(table):
                continue
            with pytest.raises(pg_errors.InsufficientPrivilege):
                cursor.execute(f'INSERT INTO "{table}" DEFAULT VALUES')
            with pytest.raises(pg_errors.InsufficientPrivilege):
                cursor.execute(f'DELETE FROM "{table}" WHERE false')  # noqa: S608


@transactional
def test_the_role_the_command_made_has_no_privilege_problem(writer_role):
    role, password = writer_role
    with _connect(role, password) as conn, conn.cursor() as cursor:
        assert db_role.privilege_problems_writer(cursor, expected_role=role) == []
        cursor.execute("SHOW statement_timeout")
        assert cursor.fetchone()[0] == "30s"
        cursor.execute("SHOW temp_file_limit")
        assert cursor.fetchone()[0] == db_role.TEMP_FILE_LIMIT
        cursor.execute("SELECT rolconnlimit FROM pg_roles WHERE rolname = current_user")
        assert cursor.fetchone()[0] == db_role.CONNECTION_LIMIT


@transactional
@pytest.mark.parametrize(
    ("widen", "named"),
    [
        ('GRANT INSERT ON accounts_user TO "{role}"', "must never write"),
        ('GRANT UPDATE ON mcp_access_mcptoken TO "{role}"', "must never write"),
        ('GRANT DELETE ON matters_entry TO "{role}"', "beyond its allow-list"),
        ('GRANT INSERT ON matters_matterengagement TO "{role}"', "beyond its allow-list"),
        ('GRANT TRUNCATE ON matters_entry TO "{role}"', "TRUNCATE"),
        ('GRANT SELECT ON django_session TO "{role}"', "may SELECT django_session"),
        ('REVOKE SELECT ON accounts_user FROM "{role}"', "cannot SELECT accounts_user"),
        ('ALTER ROLE "{role}" CREATEDB', "CREATEDB"),
        ('ALTER ROLE "{role}" CREATEROLE', "CREATEROLE"),
        ('ALTER ROLE "{role}" BYPASSRLS', "BYPASSRLS"),
        ('ALTER ROLE "{role}" CONNECTION LIMIT -1', "CONNECTION LIMIT"),
        ('ALTER ROLE "{role}" RESET temp_file_limit', "temp_file_limit"),
        ("ALTER ROLE \"{role}\" SET temp_file_limit = '1GB'", "temp_file_limit"),
        ('ALTER ROLE "{role}" SET statement_timeout = 0', "statement_timeout"),
        ('ALTER ROLE "{role}" RESET idle_in_transaction_session_timeout', "idle_in_transaction"),
        ('GRANT CREATE ON SCHEMA public TO "{role}"', "CREATE in schema public"),
        ('GRANT "{owner}" TO "{role}"', "member of"),
        (
            'ALTER DEFAULT PRIVILEGES FOR ROLE "{owner}" IN SCHEMA public '
            'GRANT INSERT ON TABLES TO "{role}"',
            "on future objects",
        ),
    ],
)
def test_privilege_problems_name_each_widening_and_apply_narrows_it_again(
    writer_role, widen, named
):
    role, password = writer_role
    owner = connection.settings_dict["USER"]
    with connection.cursor() as cursor:
        cursor.execute(widen.format(role=role, owner=owner))
    with _connect(role, password) as conn, conn.cursor() as cursor:
        problems = db_role.privilege_problems_writer(cursor, expected_role=role)
    assert any(named in problem for problem in problems), problems
    assert "password unchanged" in _apply("mcp_writer_db_role", role, None)
    with _connect(role, password) as conn, conn.cursor() as cursor:
        assert db_role.privilege_problems_writer(cursor, expected_role=role) == []


@pytest.mark.django_db
def test_privilege_problems_name_what_the_owner_role_holds():
    with connection.cursor() as cursor:
        problems = db_role.privilege_problems_writer(cursor, expected_role="juristid_mcp_writer")
    joined = "; ".join(problems)
    assert "not juristid_mcp_writer" in joined
    assert "SUPERUSER" in joined or "owns" in joined
    assert "must never write" in joined
    assert "may SELECT django_session" in joined


@transactional
def test_the_password_is_a_scram_verifier_never_the_clear_text(writer_role):
    role, password = writer_role
    with connection.cursor() as cursor:
        cursor.execute("SELECT rolpassword FROM pg_authid WHERE rolname = %s", [role])
        stored = cursor.fetchone()[0]
    assert stored.startswith("SCRAM-SHA-256$4096:")
    assert password not in stored


# -- the command's guard rails ------------------------------------------------------


@pytest.mark.django_db
def test_print_sql_changes_nothing_and_names_no_password():
    out = io.StringIO()
    call_command("mcp_writer_db_role", "--print-sql", stdout=out)
    text = out.getvalue()
    assert 'GRANT INSERT ON TABLE "matters_entry" TO "juristid_mcp_writer";' in text
    assert 'GRANT INSERT, UPDATE ON TABLE "mcp_writes_mcpwriteproposal"' in text
    assert "mcp_writes_mcpwriteapproval" not in text
    assert "accounts_user" not in text
    assert "PASSWORD '" not in text
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = 'juristid_mcp_writer'")
        assert cursor.fetchone() is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "args",
    [
        ["--print-sql", "--password-stdin"],
        ["--print-sql", "--role", "Bad-Name"],
        ["--print-sql", "--role", "juristid_mcp"],
        ["--print-sql", "--role", "juristid", "--owner", "juristid"],
        ["--print-sql", "--statement-timeout-seconds", "0"],
    ],
)
def test_the_command_refuses_what_it_should(args):
    with pytest.raises(CommandError):
        call_command("mcp_writer_db_role", *args, stdout=io.StringIO(), stdin=io.StringIO("x" * 20))


@pytest.mark.django_db
def test_a_placeholder_or_short_password_changes_nothing():
    role = f"juristid_mcp_writer_t{secrets.token_hex(4)}"
    for bad in ("replace-me-with-something-long", "short"):
        with pytest.raises(CommandError, match="Nothing was changed"):
            _apply("mcp_writer_db_role", role, bad)
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [role])
        assert cursor.fetchone() is None


# -- juristid.E050 ----------------------------------------------------------------


@pytest.mark.django_db
def test_e050_is_silent_for_every_other_process(settings):
    for role in ("", "web", "mcp"):
        settings.JURISTID_PROCESS_ROLE = role
        assert checks.check_mcp_writer_database_role() == []


@pytest.mark.django_db
def test_e050_refuses_the_owner_role_on_real_data_and_warns_elsewhere(settings):
    settings.JURISTID_PROCESS_ROLE = "mcp_writer"
    settings.REAL_DATA_ALLOWED = True
    found = checks.check_mcp_writer_database_role()
    assert [type(item).__name__ for item in found] == ["Error"]
    assert found[0].id == "juristid.E050"
    assert "must never write" in found[0].msg
    settings.REAL_DATA_ALLOWED = False
    assert [type(item).__name__ for item in checks.check_mcp_writer_database_role()] == ["Warning"]
