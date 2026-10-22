"""Who may connect an AI client to Juristid. By an operator, audited (docs/adr/0185 §3).

    manage.py mcp_access grant --user <upn>
    manage.py mcp_access enrolment-link --user <upn>
    manage.py mcp_access revoke --user <upn> --reason "<why>"
    manage.py mcp_access revoke-all --reason "<why>"
    manage.py mcp_access unlock --user <upn>
    manage.py mcp_access list

* **grant** makes a PENDING grant for one account and prints its one-time
  enrolment link, built from `JURISTID_PUBLIC_ORIGIN`. Refused for an inactive
  account, one without a business-read role (SPECIALIST, DEPARTMENT_HEAD,
  READER), one that already has a grant, and a synthetic account wherever real
  data is allowed. Hand the link to the person yourself; it is printed once and
  stored only as a digest.
* **enrolment-link** prints a new link for a grant still waiting for its
  authenticator; the outstanding one stops working.
* **revoke** ends the grant and every token, code and link it has, at once.
* **revoke-all** is the kill switch: every grant and every token.
* **unlock** clears the lock wrong authenticator codes put on a grant, and the
  suspension the fourth consecutive lock puts on it (MCPSEC-01). The count of
  wrong codes since the last success is kept: only a success resets it.
* **list** prints every grant: account, status, whether enrolled, live tokens,
  the last token issue or refresh, the lock or suspension, and the wrong codes
  since the last success. No secret, no digest.

With `MCP_ENABLED` off, only `list`, `revoke` and `revoke-all` run: turning
access *off* must never depend on it being on. Every change records an
`McpAccessEvent` naming the command, the OS user and the host.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from app.accounts.models import User
from app.mcp_access import config, services

#: The subcommands that may run while MCP access is switched off.
WHILE_DISABLED = frozenset({"list", "revoke", "revoke-all"})


class Command(BaseCommand):
    help = "Grant, list and revoke MCP (AI client) access for individual accounts."

    def add_arguments(self, parser: Any) -> None:
        sub = parser.add_subparsers(dest="action", required=True)
        grant = sub.add_parser(
            "grant", help="Grant one account MCP access; prints its enrolment link."
        )
        grant.add_argument("--user", required=True, help="The account's UPN.")
        link = sub.add_parser("enrolment-link", help="A new enrolment link for a pending grant.")
        link.add_argument("--user", required=True, help="The account's UPN.")
        revoke = sub.add_parser("revoke", help="End one account's grant and every token.")
        revoke.add_argument("--user", required=True, help="The account's UPN.")
        revoke.add_argument("--reason", required=True, help="Why. Kept in the audit.")
        revoke_all = sub.add_parser("revoke-all", help="End every grant and every token.")
        revoke_all.add_argument("--reason", required=True, help="Why. Kept in the audit.")
        unlock = sub.add_parser("unlock", help="Clear a grant's wrong-code lock or suspension.")
        unlock.add_argument("--user", required=True, help="The account's UPN.")
        sub.add_parser("list", help="Every grant, without secrets.")

    def handle(self, *args: Any, **options: Any) -> None:
        action = options["action"]
        if action not in WHILE_DISABLED and not bool(getattr(settings, "MCP_ENABLED", False)):
            raise CommandError("MCP_ENABLED is off: only list, revoke and revoke-all run.")
        if action in ("grant", "enrolment-link") and not config.issuer():
            raise CommandError("JURISTID_PUBLIC_ORIGIN is not set: no enrolment link can be built.")
        provenance = services.operator_provenance(action)
        try:
            if action == "list":
                self._list()
            elif action == "grant":
                issued = services.grant_access(user=self._user(options), provenance=provenance)
                self._print_link("Ligipääs antud (ootab autentija seadistamist).", issued)
            elif action == "enrolment-link":
                issued = services.issue_enrolment_link(
                    user=self._user(options), provenance=provenance
                )
                self._print_link("Uus seadistuslink; eelmine ei kehti enam.", issued)
            elif action == "revoke":
                revoked = services.revoke_grant(
                    user=self._user(options), reason=options["reason"], provenance=provenance
                )
                self.stdout.write(f"Ligipääs tühistatud. Tühistatud võtmeid: {revoked}.")
            elif action == "revoke-all":
                grants, tokens = services.revoke_all(
                    reason=options["reason"], provenance=provenance
                )
                self.stdout.write(
                    f"Kõik ligipääsud tühistatud. Ligipääse: {grants}, võtmeid: {tokens}."
                )
            elif action == "unlock":
                was_locked = services.unlock(user=self._user(options), provenance=provenance)
                self.stdout.write("Lukk eemaldatud." if was_locked else "Ligipääs ei olnud lukus.")
        except services.GrantRefused as error:
            raise CommandError(f"Keeldutud: {error}. Midagi ei muudetud.") from error

    def _user(self, options: dict[str, Any]) -> User:
        upn = (options.get("user") or "").strip().lower()
        user = User.objects.filter(upn__iexact=upn).first() if upn else None
        if user is None:
            raise CommandError("Sellist kontot ei ole. Midagi ei muudetud.")
        return user

    def _print_link(self, headline: str, issued: services.IssuedEnrolment) -> None:
        self.stdout.write(headline)
        self.stdout.write(f"Konto: {issued.grant.user.upn}")
        self.stdout.write(f"Kehtib kuni: {issued.expires_at:%Y-%m-%d %H:%M} UTC")
        self.stdout.write(
            "Ühekordne seadistuslink (anna see isikule ise, see ei ole kusagil salvestatud):"
        )
        self.stdout.write(issued.url)

    def _list(self) -> None:
        rows = services.grant_rows()
        if not rows:
            self.stdout.write("MCP ligipääse ei ole.")
            return
        self.stdout.write(
            "upn\tolek\tautentija\taktiivseid võtmeid\tviimane kasutus\tlukus kuni\tvalesid koode"
        )
        for row in rows:
            last = f"{row.last_used:%Y-%m-%d %H:%M}" if row.last_used else "-"
            if row.suspended:
                locked = "PEATATUD (mcp_access unlock)"
            else:
                locked = f"{row.locked_until:%Y-%m-%d %H:%M}" if row.locked_until else "-"
            self.stdout.write(
                f"{row.upn}\t{row.status}\t{'jah' if row.enrolled else 'ei'}\t"
                f"{row.active_tokens}\t{last}\t{locked}\t{row.total_failed_attempts}"
            )
