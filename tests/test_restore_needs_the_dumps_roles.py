"""A restore onto a fresh cluster checks the roles the dump grants to, first.

Roles are cluster-wide and a database dump does not carry them. Since the MCP
server and the write gateway got their own least-privilege roles
(`juristid_mcp`, `juristid_mcp_writer` — docs/adr/0185, 0189), the production
dump holds `GRANT … TO juristid_mcp` and `ALTER DEFAULT PRIVILEGES … TO
juristid_mcp_writer`. On a fresh cluster — the disaster the runbook exists for
— the first of those fails, and because `juristid-restore.sh` restores in one
transaction the whole database rolls back, after the evidence has already been
copied into the data root.

Reproduced on PostgreSQL 18 before the fix (2026-10-23): a dump granting to a
role the target lacks, restored with `--exit-on-error --single-transaction`,
stops at `ERROR: role "…" does not exist` and leaves zero tables.

What these pin: `dump_roles` (scripts/deploy/lib.sh) names every role a schema
listing needs, and the restore asks before step 4 writes anything; against a
real `pg_dump` where one is on PATH.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from django.conf import settings
from django.db import connection

ROOT = Path(settings.BASE_DIR)
LIB = ROOT / "scripts" / "deploy" / "lib.sh"
RESTORE = ROOT / "scripts" / "deploy" / "juristid-restore.sh"
BASH = shutil.which("bash") or "bash"


def _dump_roles(sql: str) -> list[str]:
    result = subprocess.run(  # noqa: S603 - a fixed interpreter and a repository path
        [BASH, "-c", f'. "{LIB.as_posix()}" && dump_roles'],
        cwd=ROOT,
        input=sql,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _client_major(tool: str) -> int:
    """The major version of a PostgreSQL client tool — «pg_dump (PostgreSQL) 16.10 …» is 16."""
    out = subprocess.run(  # noqa: S603 - a fixed client tool
        [tool, "--version"], capture_output=True, text=True, timeout=30, check=True
    ).stdout
    match = re.search(r"\(PostgreSQL\)\s+(\d+)", out)
    assert match is not None, f"unexpected version line: {out!r}"
    return int(match.group(1))


def test_every_role_the_listing_names_is_found():
    sql = "\n".join(
        [
            "GRANT SELECT ON TABLE public.matters_matter TO juristid_mcp;",
            "ALTER DEFAULT PRIVILEGES FOR ROLE juristid IN SCHEMA public "
            "GRANT SELECT ON TABLES TO juristid_mcp;",
            "GRANT INSERT,UPDATE ON TABLE public.matters_note TO juristid_mcp_writer;",
            "REVOKE ALL ON TABLE public.accounts_user FROM juristid_mcp;",
            "ALTER TABLE public.matters_matter OWNER TO juristid;",
            'GRANT USAGE ON SCHEMA public TO "Quoted Role" WITH GRANT OPTION;',
            "GRANT SELECT ON TABLE public.x TO reader_role GRANTED BY grantor_role;",
        ]
    )
    assert _dump_roles(sql) == [
        "Quoted Role",
        "grantor_role",
        "juristid",
        "juristid_mcp",
        "juristid_mcp_writer",
        "reader_role",
    ]


def test_public_and_lookalikes_are_not_roles():
    sql = "\n".join(
        [
            "REVOKE ALL ON SCHEMA public FROM PUBLIC;",
            "GRANT USAGE ON SCHEMA public TO PUBLIC;",
            "COMMENT ON TABLE public.t IS 'grant select to nobody';",
            "ALTER TABLE ONLY public.p ATTACH PARTITION public.c FOR VALUES FROM (1) TO (2);",
            "CREATE TABLE public.t (x integer);",
        ]
    )
    assert _dump_roles(sql) == []


def test_crlf_from_docker_exec_is_tolerated():
    assert _dump_roles("GRANT SELECT ON TABLE public.t TO juristid_mcp;\r\n") == ["juristid_mcp"]


def test_the_restore_asks_before_it_writes_anything():
    script = RESTORE.read_text(encoding="utf-8")
    empty_check = script.index('step "Checking the target database is empty"')
    role_check = script.index("dump_roles")
    evidence = script.index("# 4. Evidence and page XML")
    restore = script.index("--single-transaction \\")
    assert empty_check < role_check < evidence < restore
    refusal = script[role_check : script.index("every role the dump names exists")]
    assert "the dump grants to role(s) this cluster does not have" in refusal
    assert "Nothing has been written." in refusal
    # One copy of the dump into the container, made before the check and reused.
    assert script.count('juristid_compose cp "$SET_DIR/database.dump"') == 1
    assert script.index('juristid_compose cp "$SET_DIR/database.dump"') < role_check


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
@pytest.mark.skipif(
    not (shutil.which("pg_dump") and shutil.which("pg_restore")),
    reason="needs the PostgreSQL client tools on PATH",
)
def test_a_real_dump_names_the_roles_it_grants_to():
    """Against the real `pg_dump` / `pg_restore --schema-only` output.

    `pg_dump` refuses a server newer than itself, and the runner's own client
    (Ubuntu's) is older than the PostgreSQL 18 service the suite runs against;
    there the listing parser is still pinned by the tests above, and this one
    runs wherever the client matches the server.
    """
    pg_dump = shutil.which("pg_dump") or "pg_dump"
    server_major: int = connection.pg_version // 10000  # type: ignore[attr-defined]
    if _client_major(pg_dump) < server_major:
        pytest.skip(f"pg_dump on PATH is older than the PostgreSQL {server_major} server")
    params = connection.get_connection_params()
    role = f"wsd_probe_{uuid.uuid4().hex[:8]}"
    table = f"wsd_probe_table_{uuid.uuid4().hex[:8]}"
    with connection.cursor() as cursor:
        cursor.execute(f"CREATE ROLE {role} NOLOGIN")
        cursor.execute(f"CREATE TABLE {table} (x integer)")
        cursor.execute(f"GRANT SELECT ON TABLE {table} TO {role}")
    try:
        env = {**os.environ, "PGPASSWORD": str(params.get("password") or "")}
        dump = subprocess.run(  # noqa: S603 - fixed client tools, generated arguments
            [
                pg_dump,
                "-h",
                str(params.get("host") or "127.0.0.1"),
                "-p",
                str(params.get("port") or 5432),
                "-U",
                str(params.get("user")),
                "-Fc",
                "-t",
                table,
                str(params.get("dbname") or params.get("database")),
            ],
            capture_output=True,
            env=env,
            timeout=120,
            check=True,
        ).stdout
        listing = subprocess.run(  # noqa: S603 - a fixed client tool
            [shutil.which("pg_restore") or "pg_restore", "--schema-only", "--file=-"],
            input=dump,
            capture_output=True,
            timeout=120,
            check=True,
        ).stdout.decode()
        assert role in _dump_roles(listing)
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE {table}")
            cursor.execute(f"DROP ROLE {role}")
