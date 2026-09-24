"""Create the account a real member of the department signs in as.

    manage.py provision_user --upn <address> --display-name "<name>" [--role SPECIALIST]

The supported way to onboard a person (deploy/unraid-main/README.md, step 6).
`createsuperuser` is not: it makes an ADMINISTRATOR superuser, which by design is
never a persona and can never be given work (docs/adr/0034) — and which the
Django admin would hand everything once authentication identifies people
(ENG-013).

Exit 0 when the account now exists as asked, whether this run created it or it
was already there exactly so. Anything else — an address that is not one, a
role that is not a department role, an existing account that differs — stops
with nothing written. There is no password option, on purpose.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from app.accounts.enums import UserRole
from app.accounts.selectors import DEPARTMENT_WORK_ROLES
from app.accounts.services import ProvisioningRefused, provision_department_user


class Command(BaseCommand):
    help = "Create a department member's account: a persona, assignable, no password."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--upn", required=True, help="The person's real address, as Cloudflare asserts it."
        )
        parser.add_argument(
            "--display-name", required=True, help="Their name as colleagues read it."
        )
        parser.add_argument(
            "--role",
            default=UserRole.SPECIALIST,
            choices=sorted(DEPARTMENT_WORK_ROLES),
            help="Default SPECIALIST.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            result = provision_department_user(
                upn=options["upn"], display_name=options["display_name"], role=options["role"]
            )
        except ProvisioningRefused as error:
            raise CommandError(str(error)) from error

        user = result.user
        if result.created:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Created {user.display_name} <{user.upn}>, {user.get_role_display()}: "
                    "a persona, assignable, no password."
                )
            )
        else:
            self.stdout.write(
                f"{user.display_name} <{user.upn}> already exists exactly so. Nothing changed."
            )
