"""Create or re-assert the MCP write gateway's least-privilege database role (docs/adr/0189).

    manage.py mcp_writer_db_role --print-sql [--role juristid_mcp_writer] [--owner <role>]
    manage.py mcp_writer_db_role --apply --password-stdin [--role juristid_mcp_writer]

Run from **`web`**, whose role owns every table and may create roles — never
from `mcp-writer` or `mcp`, which can do neither:

    sed -n 's/^POSTGRES_PASSWORD=//p' \\
        /mnt/user/appdata/juristid-main/config/juristid-mcp-writer.env \\
      | docker compose -p juristid-main -f deploy/unraid-main/compose.yml \\
          exec -T web python manage.py mcp_writer_db_role --apply --password-stdin

* **--print-sql** prints the statements (`app.mcp_writes.db_role.role_sql`,
  the full allow-list) and touches nothing. No password appears in them.
* **--apply** runs them in one transaction as the connected role and, given a
  password, sets it as a SCRAM-SHA-256 verifier computed here — the server is
  never handed the password and nothing prints it. Only the allow-listed
  tables that exist get their grants (a GRANT on a missing table fails), so
  run it again **after every migration** that adds a table the operations
  write; the gateway's startup check (juristid.E050) names one it cannot
  write. Idempotent: it narrows a role somebody widened.

The role gets `statement_timeout` and `idle_in_transaction_session_timeout`
equal to ``--statement-timeout-seconds`` (default this process's
`MCP_WRITE_CALL_TIMEOUT_SECONDS`, 30), `temp_file_limit` 64 MB and a
connection limit. `temp_file_limit` is superuser-only, so the connected role
must be a superuser (the image's `POSTGRES_USER` is); otherwise nothing
changes.

The password comes from stdin or from an environment variable, never from
the command line, where `ps` and the shell history would see it.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection, transaction

from app.mcp_server import db_role as read_role
from app.mcp_writes import db_role


class Command(BaseCommand):
    help = "Print or apply the SQL that makes the MCP write gateway's database role."

    stealth_options = ("stdin",)

    def add_arguments(self, parser: Any) -> None:
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--print-sql", action="store_true", help="Print the SQL; change nothing.")
        mode.add_argument("--apply", action="store_true", help="Run the SQL as this connection.")
        parser.add_argument(
            "--role",
            default=db_role.DEFAULT_ROLE,
            help="The write gateway's role (juristid_mcp_writer).",
        )
        parser.add_argument(
            "--owner",
            default="",
            help="The role the migrations run as. Default: the connected role (web's).",
        )
        parser.add_argument(
            "--statement-timeout-seconds",
            type=int,
            default=None,
            help="The role's statement_timeout. Default: MCP_WRITE_CALL_TIMEOUT_SECONDS (30).",
        )
        source = parser.add_mutually_exclusive_group()
        source.add_argument(
            "--password-stdin", action="store_true", help="Read the role's password from stdin."
        )
        source.add_argument(
            "--password-env", default="", help="Read the role's password from this variable."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        role = (options["role"] or "").strip()
        if not db_role.valid_identifier(role):
            raise CommandError(
                "--role must be a plain lower-case identifier, e.g. juristid_mcp_writer."
            )
        if role == read_role.DEFAULT_ROLE:
            raise CommandError(
                f"{role} is the read-only MCP role; the writer is a role of its own."
            )
        database = str(settings.DATABASES["default"]["NAME"])
        owner = (options["owner"] or "").strip() or self._connected_role(options)
        if role == owner:
            raise CommandError("The writer role cannot be the role that owns the tables.")
        for name, value in (("--owner", owner), ("the database name", database)):
            if not db_role.valid_identifier(value):
                raise CommandError(f"{name} is not a plain lower-case identifier.")

        timeout = options.get("statement_timeout_seconds")
        if timeout is None:
            timeout = int(getattr(settings, "MCP_WRITE_CALL_TIMEOUT_SECONDS", 30))
        if timeout < 1:
            raise CommandError("--statement-timeout-seconds must be at least 1.")

        if options["print_sql"]:
            if options["password_stdin"] or options["password_env"]:
                raise CommandError("--print-sql never takes a password; --apply sets it.")
            for statement in db_role.role_sql(
                role=role, owner=owner, database=database, statement_timeout_seconds=timeout
            ):
                self.stdout.write(f"{statement};")
            self.stdout.write(
                "-- --apply grants only the allow-listed tables that exist; the password is "
                "set by `mcp_writer_db_role --apply --password-stdin`, never printed here."
            )
            return

        password = self._password(options)
        verifier = db_role.scram_verifier(password) if password is not None else None
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                withheld = db_role.withheld_existing(cursor)
                allowlist = db_role.existing_allowlist(cursor)
                for statement in db_role.role_sql(
                    role=role,
                    owner=owner,
                    database=database,
                    password_verifier=verifier,
                    allowlist=allowlist,
                    withheld=withheld,
                    statement_timeout_seconds=timeout,
                ):
                    cursor.execute(statement)
        except DatabaseError as error:
            # One transaction: nothing changed. The class only — a statement
            # holds the password's verifier.
            raise CommandError(
                f"PostgreSQL refused the role's statements ({type(error).__name__}); nothing "
                f"was changed. The connected role ({owner}) must be able to create roles and "
                "to set temp_file_limit (a superuser, as the image's POSTGRES_USER is)."
            ) from None
        missing = sorted(set(db_role.WRITE_ALLOWLIST) - set(allowlist))
        self.stdout.write(
            f"Role {role}: SELECT on schema public (owner {owner}) except "
            f"{len(withheld)} withheld table(s); writes on {len(allowlist)} allow-listed "
            f"table(s); statement_timeout {timeout}s, temp files at most "
            f"{db_role.TEMP_FILE_LIMIT}; "
            + ("password set." if verifier else "password unchanged.")
        )
        if missing:
            self.stdout.write(
                f"Not yet in this database (run again after migrating): {', '.join(missing)}."
            )

    def _connected_role(self, options: dict[str, Any]) -> str:
        if options["print_sql"]:
            return str(settings.DATABASES["default"].get("USER") or "")
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_user")
            return str(cursor.fetchone()[0])

    def _password(self, options: dict[str, Any]) -> str | None:
        if options["password_stdin"]:
            stream = options.get("stdin") or sys.stdin
            raw = stream.readline()
        elif options["password_env"]:
            raw = os.environ.get(options["password_env"], "")
            if not raw:
                raise CommandError(f"{options['password_env']} is empty or not set.")
        else:
            return None
        password = raw.rstrip("\r\n")
        if len(password) < 16 or password.startswith("replace-me"):
            raise CommandError(
                "The password is shorter than 16 characters or still a placeholder. "
                "Nothing was changed."
            )
        try:
            db_role.scram_verifier(password, salt=b"0" * 16)
        except ValueError as error:
            raise CommandError(f"{error}. Nothing was changed.") from error
        return password
