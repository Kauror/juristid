"""Print one administrator's one-time password link, on the host, to a terminal.

    manage.py issue_administrator_link \\
        --email <address> --confirm-email <address> \\
        --note "<who is running this and why>"

Under the shared gate account e-mail cannot be switched on (`juristid.E036`), so
an administrator's decision on Haldus → Kasutajad («Kinnita ja saada kutse»)
sends nothing. This is how the decided link reaches the person — handed over
by the operator, never pasted anywhere that keeps a copy (docs/adr/0186):

* **Only for an account that may administer** — `accounts.manage` already
  stored on it by an administrator or the bootstrap, or the technical
  ADMINISTRATOR role. Never a switched-off account; never a synthetic one on
  real data. A pending account must have been approved on the page first.
* **No password is ever set here.** The link lets the person choose their own;
  an account that already has one gets a reset link.
* **To an interactive terminal only**, the address typed twice, the note kept
  in the security audit; the link itself is never stored or logged.
"""

from __future__ import annotations

import sys
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from app.accounts import email_policy
from app.accounts.administration import AdministrationRefused, issue_administrator_link


class Command(BaseCommand):
    help = "Print a one-time password link for an account administrator (terminal only)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--email", required=True, help="The administrator's sign-in address.")
        parser.add_argument(
            "--confirm-email",
            required=True,
            help="The same address again, typed rather than pasted.",
        )
        parser.add_argument(
            "--note", required=True, help="Who is running this, and why. Kept in the audit."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        address = email_policy.normalise(options["email"])
        if address != email_policy.normalise(options["confirm_email"]):
            raise CommandError("The two addresses differ. Nothing was changed.")
        if not options["note"].strip():
            raise CommandError("A note on who is running this and why is required.")
        if not sys.stdout.isatty():
            raise CommandError(
                "This command writes a credential and is refused when the output is not an "
                "interactive terminal: a redirected or captured output is a log. Nothing was "
                "changed."
            )
        try:
            issued = issue_administrator_link(email=address, operator_note=options["note"])
        except AdministrationRefused as error:
            raise CommandError(str(error)) from error

        kind = "activation" if issued.purpose == "ACTIVATION" else "password reset"
        self.stdout.write(
            self.style.SUCCESS(
                f"One-time {kind} link for {issued.user.display_name} <{issued.user.upn}>, valid "
                f"until {issued.expires_at:%Y-%m-%d %H:%M} UTC. Hand it to the person yourself; "
                "do not paste it anywhere that keeps a copy:"
            )
        )
        self.stdout.write(issued.link)
        self.stdout.write(
            "They open it, pass the shared gate, choose their own password, then sign in at "
            "/haldus/sisene/ and set up their authenticator app there."
        )
