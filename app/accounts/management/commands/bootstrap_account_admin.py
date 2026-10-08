"""Appoint the very first account administrator. Once, by an operator, by hand.

    manage.py bootstrap_account_admin \\
        --email <address> --confirm-email <address> \\
        --display-name "<name>" --role <ROLE> \\
        --note "<who is running this and why>" \\
        [--print-activation-link]

The one way an account administrator comes into existence without another
administrator appointing them (docs/adr/0145 §11). Everything about it is
deliberate friction, because it is the one place where nobody already trusted
vouches for the result:

* **Only under `AUTH_MODE=local_password`.** Under the shared gate an
  administrator would be a capability nobody can exercise and a persona could
  never prove; the service refuses and so does this.
* **Only when there is no administrator at all.** After the first, the
  administration page appoints administrators, with its own audit trail.
* **The address typed twice**, and a note on who ran it and why, which the
  security audit keeps.
* **No password, ever.** The account sets its own through a one-time link. With
  e-mail delivery on, the link is mailed. Without it the operator may ask for
  the link to be printed — once, to an interactive terminal only, never into a
  log — and must hand it to the person themselves.
* **Never automatic.** No migration, deployment step or seed calls this.
"""

from __future__ import annotations

import sys
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from app.accounts import email_policy
from app.accounts.administration import AdministrationRefused, bootstrap_first_administrator
from app.accounts.enums import UserRole


class Command(BaseCommand):
    help = "Appoint the first account administrator (local_password mode, once, no password)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--email", required=True, help="The administrator's sign-in address.")
        parser.add_argument(
            "--confirm-email",
            required=True,
            help="The same address again, typed rather than pasted.",
        )
        parser.add_argument(
            "--display-name", default="", help="Their name, if the account does not exist yet."
        )
        parser.add_argument(
            "--role",
            required=True,
            choices=list(UserRole.values),
            help="Their business role. Administration grants no business access of its own.",
        )
        parser.add_argument(
            "--note", required=True, help="Who is running this, and why. Kept in the audit."
        )
        parser.add_argument(
            "--print-activation-link",
            action="store_true",
            help=(
                "With e-mail delivery off: print the one-time activation link to this "
                "terminal. Refused unless the output is an interactive terminal."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        address = email_policy.normalise(options["email"])
        if address != email_policy.normalise(options["confirm_email"]):
            raise CommandError("The two addresses differ. Nothing was changed.")
        if not options["note"].strip():
            raise CommandError("A note on who is running this and why is required.")
        print_link = bool(options["print_activation_link"])
        if print_link and not sys.stdout.isatty():
            raise CommandError(
                "--print-activation-link writes a credential and is refused when the output is "
                "not an interactive terminal: a redirected or captured output is a log. "
                "Nothing was changed."
            )

        try:
            result = bootstrap_first_administrator(
                email=address,
                display_name=options["display_name"],
                role=options["role"],
                operator_note=options["note"],
                print_link=print_link,
            )
        except AdministrationRefused as error:
            raise CommandError(str(error)) from error

        user = result.user
        verb = "Created" if result.created else "Appointed existing account"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {user.display_name} <{user.upn}> as the first account administrator "
                "(accounts.manage, accounts.delegate). No password was set."
            )
        )
        if result.link is not None:
            self.stdout.write(f"Activation link: {result.link.delivery.message_et}")
        if result.printable_link:
            self.stdout.write(
                "One-time activation link — hand it to the person yourself, do not paste it "
                "anywhere that keeps a copy:"
            )
            self.stdout.write(result.printable_link)
        self.stdout.write(
            "They must enrol an authenticator app at their first sign-in before they can "
            "administer anything."
        )
