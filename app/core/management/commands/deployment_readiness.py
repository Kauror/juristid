"""Does the code now running match the database and the storage under it?

The deep operational check, and deliberately not a health probe. Three
different questions get confused into one word:

* **liveness** — the process is up and its database answers. That is `/healthz`,
  wired to the image HEALTHCHECK, cheap enough to run every fifteen seconds.
* **readiness** — this build's schema is applied, its storage is mounted the way
  it was meant to be, and its authenticator is the one the environment claims.
  That is this command.
* **correctness of the data** — a different question again, answered by
  `recovery_fingerprint` and by the importer's own verify phases.

A command rather than a URL for two reasons. It costs a migration-graph load
and several filesystem probes, which has no business happening on a request
path; and it reports things — mount contracts, auth mode, PostgreSQL major —
that a public endpoint has no reason to publish. `/healthz` is reachable
without authentication because a container has to be able to ask it.

Exits non-zero when the deployment is not ready. It never migrates: a health
check that repairs the thing it is checking cannot report on it
(docs/adr/0022, master specification 24.2).
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from app.core import deployment


class Command(BaseCommand):
    help = "Verify that this build, its schema and its storage agree. Changes nothing."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Report only problems. For a scripted post-deployment gate.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        quiet: bool = options["quiet"]

        # The facts and the verdict come from `deployment.readiness_report`, so
        # that `production_status` can roll the same verdict up without reading
        # this command's prose. What is printed, and the exit behaviour, stay
        # here: they are this command's contract and not the report's.
        try:
            report = deployment.readiness_report()
        except DatabaseError as error:
            raise CommandError(
                f"The database is not reachable: {error.__class__.__name__}"
            ) from error

        identity = report.identity
        if not quiet:
            self.stdout.write("Build")
            self.stdout.write(f"  revision     {identity.revision}")
            self.stdout.write(f"  built at     {identity.built_at or '(not a container build)'}")
            self.stdout.write(f"  environment  {identity.environment}")
            self.stdout.write(f"  stage        {identity.stage}")

            major, minor = report.postgresql
            self.stdout.write("")
            self.stdout.write("Database")
            self.stdout.write(f"  PostgreSQL   {major}.{minor}")
            self.stdout.write(f"  migrations   {len(report.migrations.leaves)} app leaves")

            self.stdout.write("")
            self.stdout.write("Storage")
            for root in report.storage:
                mount = "read-only" if not root.must_be_writable else "writable"
                self.stdout.write(f"  {root.name:<24} {root.path}  [{root.kind}, {mount}]")

            self.stdout.write("")
            self.stdout.write("Configuration")
            self.stdout.write(f"  auth mode    {settings.AUTH_MODE}")
            self.stdout.write(f"  real data    {'yes' if settings.REAL_DATA_ALLOWED else 'no'}")
            self.stdout.write(f"  debug        {'ON' if settings.DEBUG else 'off'}")

            baseline = report.reference
            self.stdout.write("")
            self.stdout.write("Reference data")
            self.stdout.write(
                f"  policy areas  {baseline.policy_areas_present}/{baseline.policy_areas_expected}"
            )
            self.stdout.write(
                f"  organisations {baseline.organisations_present}"
                f"/{baseline.organisations_expected}"
            )
            self.stdout.write("  tags          not managed by the reviewed baseline")

        # -- verdict -------------------------------------------------------
        for warning in report.warnings:
            self.stdout.write(self.style.WARNING(f"WARNING: {warning}"))

        if report.problems:
            raise CommandError(
                "This deployment is not ready:\n"
                + "\n".join(f"  - {problem}" for problem in report.problems)
            )

        if not quiet:
            self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Deployment is ready."))
