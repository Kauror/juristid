"""Switch a compromised account off at once — the operator's emergency route.

    manage.py emergency_disable_account \\
        --email <address> --confirm-email <address> \\
        --reason "<what happened, who is running this>" [--dry-run]

The continuity rule (at least two usable full administrators, docs/adr/0186)
must never make it impossible to stop a compromised administrator. This is the
independently verified way past it: somebody with host access, a mandatory
reason, an `EMERGENCY_ACCOUNT_DISABLED` row in the security audit — and no web
route, no persona and no administrator session can reach it.

It switches the account off, invalidates its one-time links and ends every
session it has (the administrator session included), and changes nothing else:
role, capabilities, password and history stay as they were, so what the account
was remains on record. It prints what it did and the administrator count before
and after. Works in every authentication mode.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from app.accounts import email_policy
from app.accounts.administration import (
    MINIMUM_FULL_ADMINISTRATORS,
    AdministrationRefused,
    emergency_disable_account,
)


class Command(BaseCommand):
    help = "Switch an account off in an emergency, past the two-administrator rule (audited)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--email", required=True, help="The account's sign-in address.")
        parser.add_argument(
            "--confirm-email",
            required=True,
            help="The same address again, typed rather than pasted.",
        )
        parser.add_argument(
            "--reason", required=True, help="What happened and who is running this. Audited."
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Say what would happen; change nothing."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        address = email_policy.normalise(options["email"])
        if address != email_policy.normalise(options["confirm_email"]):
            raise CommandError("The two addresses differ. Nothing was changed.")
        try:
            outcome = emergency_disable_account(
                email=address, reason=options["reason"], dry_run=bool(options["dry_run"])
            )
        except AdministrationRefused as error:
            raise CommandError(str(error)) from error

        user = outcome.user
        if not outcome.applied:
            self.stdout.write(
                f"DRY RUN — nothing was changed. {user.display_name} <{user.upn}> is "
                f"{'active' if outcome.was_active else 'already switched off'}."
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"{user.display_name} <{user.upn}> is switched off. "
                    f"{outcome.links_invalidated} one-time link(s) invalidated; every session of "
                    "the account ends at its next request. Role, capabilities, password and "
                    "history are unchanged. Recorded as EMERGENCY_ACCOUNT_DISABLED."
                )
            )
        self.stdout.write(
            f"Usable full administrators: {outcome.administrators_before} before, "
            f"{outcome.administrators_after} after (required: {MINIMUM_FULL_ADMINISTRATORS})."
        )
        if outcome.administrators_after < MINIMUM_FULL_ADMINISTRATORS:
            self.stdout.write(
                self.style.WARNING(
                    "Below the required number. Appoint and enrol a replacement administrator "
                    "now: Haldus → Kasutajad, then manage.py issue_administrator_link."
                )
            )
