"""Create or re-assert the `mcp` service's SELECT-only database role (MCPSEC-03).

    manage.py mcp_db_role --print-sql [--role juristid_mcp] [--owner <role>]
    manage.py mcp_db_role --apply --password-stdin [--role juristid_mcp]

Run from **`web`**, whose role owns every table and may create roles — never
from `mcp`, which can do neither (and is what this role is for):

    sed -n 's/^POSTGRES_PASSWORD=//p' /mnt/user/appdata/juristid-main/config/juristid-mcp.env \\
      | docker compose -p juristid-main -f deploy/unraid-main/compose.yml \\
          exec -T web python manage.py mcp_db_role --apply --password-stdin

* **--print-sql** prints the statements (`app.mcp_server.db_role.role_sql`)
  and touches nothing. No password appears in them.
* **--apply** runs them in one transaction as the connected role (the table
  owner) and, given a password, sets it — as a SCRAM-SHA-256 verifier computed
  here, so the server is never handed the password and nothing prints it.
  Idempotent: run it again after any doubt; it narrows a role that was widened.
  Without a password an existing role keeps its own, and a new one has none
  (it cannot log in until one is set).

The password comes from stdin (`--password-stdin`, one line) or from an
environment variable this process holds (`--password-env NAME`), never from
the command line, where `ps` and the shell history would see it.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from app.mcp_server import db_role


class Command(BaseCommand):
    help = "Print or apply the SQL that makes the MCP server's database role SELECT-only."

    #: Lets tests hand the command a stdin of their own (`call_command(..., stdin=…)`).
    stealth_options = ("stdin",)

    def add_arguments(self, parser: Any) -> None:
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--print-sql", action="store_true", help="Print the SQL; change nothing.")
        mode.add_argument("--apply", action="store_true", help="Run the SQL as this connection.")
        parser.add_argument(
            "--role", default=db_role.DEFAULT_ROLE, help="The MCP server's role (juristid_mcp)."
        )
        parser.add_argument(
            "--owner",
            default="",
            help="The role the migrations run as. Default: the connected role (web's).",
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
            raise CommandError("--role must be a plain lower-case identifier, e.g. juristid_mcp.")
        database = str(settings.DATABASES["default"]["NAME"])
        owner = (options["owner"] or "").strip() or self._connected_role(options)
        if role == owner:
            raise CommandError("The MCP role cannot be the role that owns the tables.")
        for name, value in (("--owner", owner), ("the database name", database)):
            if not db_role.valid_identifier(value):
                raise CommandError(f"{name} is not a plain lower-case identifier.")

        if options["print_sql"]:
            if options["password_stdin"] or options["password_env"]:
                raise CommandError("--print-sql never takes a password; --apply sets it.")
            for statement in db_role.role_sql(role=role, owner=owner, database=database):
                self.stdout.write(f"{statement};")
            self.stdout.write(
                "-- The password is set by `mcp_db_role --apply --password-stdin`, "
                "never printed here."
            )
            return

        password = self._password(options)
        verifier = db_role.scram_verifier(password) if password is not None else None
        with transaction.atomic(), connection.cursor() as cursor:
            withheld = db_role.withheld_existing(cursor)
            for statement in db_role.role_sql(
                role=role,
                owner=owner,
                database=database,
                password_verifier=verifier,
                withheld=withheld,
            ):
                cursor.execute(statement)
        self.stdout.write(
            f"Role {role}: SELECT only on schema public (owner {owner}), "
            f"read-only by default, {len(withheld)} table(s) withheld; "
            + ("password set." if verifier else "password unchanged.")
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
