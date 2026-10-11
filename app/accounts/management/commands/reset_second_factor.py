"""Retire one person's authenticator after the MFA key is lost. By an operator, audited.

    manage.py reset_second_factor --list-undecryptable
    manage.py reset_second_factor --email <address> --note "<who and why>" [--dry-run]

The last step of the lost-key procedure (docs/LOCAL_AUTH_ACTIVATION_RUNBOOK.md
§15), not the first. A lost or changed `LOCAL_AUTH_MFA_ENCRYPTION_KEY` leaves
every confirmed authenticator on record and unable to produce an accepted code:
the person is still asked for a code, and no code works. Before this, the
runbook sends them to a recovery code (those are not encrypted with the key and
keep working) and to an administrator's «Lähtesta teine tegur»; this is for
whoever neither can help — typically the administrators themselves, whose
devices the same key opened.

* **`--list-undecryptable`** reports which accounts' authenticators the current
  key can no longer open, and writes nothing — the blast radius, before anybody
  is reset.
* **`--email … --note …`** removes that person's device and recovery codes,
  ends their sessions and records an `MFA_REMOVED` row with `by: operator`, the
  note and whether the secret was still readable. **The second factor stays
  mandatory** for everybody it was mandatory for — administrators, staff
  accounts, everybody under `LOCAL_AUTH_MFA_REQUIRED_FOR_ALL` — whose next
  sign-in reaches nothing but the enrolment page. `--dry-run` says what would
  be removed and writes nothing.

Works in any `AUTH_MODE` (a key can be lost after a rollback too) and acts only
on an account that has something to reset. One account per run, by name: there
is no "everybody" switch, because a mass reset is a decision about the whole
department, not an operator's side effect.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from app.accounts import mfa
from app.accounts.credentials import SecondFactorResetRefused, operator_reset_second_factor


class Command(BaseCommand):
    help = (
        "Retire one person's authenticator after a lost MFA key (audited; MFA stays mandatory), "
        "or list the authenticators the current key cannot open."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--email", default="", help="The person's sign-in address.")
        parser.add_argument(
            "--note", default="", help="Who is running this, and why. Kept in the audit."
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Say what would be removed; write nothing.",
        )
        parser.add_argument(
            "--list-undecryptable",
            action="store_true",
            help="List the accounts whose authenticator the current key cannot open. Read-only.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["list_undecryptable"]:
            if options["email"]:
                raise CommandError("--list-undecryptable takes no --email. Nothing was changed.")
            self._list()
            return
        if not options["email"].strip():
            raise CommandError("--email is required (or --list-undecryptable).")

        try:
            result = operator_reset_second_factor(
                email=options["email"], note=options["note"], dry_run=options["dry_run"]
            )
        except SecondFactorResetRefused as error:
            raise CommandError(str(error)) from error

        user = result.user
        state = {
            True: "its secret is still readable under the current key",
            False: "its secret can no longer be read under the current key",
            None: "no confirmed authenticator",
        }[result.secret_was_readable]
        what = (
            f"{user.display_name} <{user.upn}>: {state}; "
            f"{result.recovery_codes_left} unused recovery code(s)."
        )
        if not result.applied:
            self.stdout.write(f"Dry run — nothing was changed. {what}")
            return
        self.stdout.write(
            self.style.SUCCESS(
                f"Second factor reset for {what} Their sessions have ended. Where a second "
                "factor is mandatory for them, the next sign-in reaches nothing but enrolling "
                "a new authenticator. Recorded in the security audit."
            )
        )

    def _list(self) -> None:
        devices = mfa.unreadable_devices()
        if not devices:
            self.stdout.write("Every confirmed authenticator opens under the current key.")
            return
        self.stdout.write(
            f"{len(devices)} authenticator(s) the current key cannot open — nothing was changed:"
        )
        for device in devices:
            self.stdout.write(f"  {device.user.upn}  ({device.user.display_name})")
