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
(docs/adr/0021, master specification 24.2).
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
        problems: list[str] = []
        warnings: list[str] = []

        identity = deployment.runtime_identity()
        if not quiet:
            self.stdout.write("Build")
            self.stdout.write(f"  revision     {identity.revision}")
            self.stdout.write(f"  built at     {identity.built_at or '(not a container build)'}")
            self.stdout.write(f"  environment  {identity.environment}")
            self.stdout.write(f"  stage        {identity.stage}")

        if settings.REAL_DATA_ALLOWED and not identity.revision_is_known:
            problems.append(
                "The running build does not know which commit it came from. "
                "Build with --build-arg GIT_SHA=<sha>, or set APPLICATION_REVISION."
            )

        # -- database ------------------------------------------------------
        try:
            major, minor = deployment.postgresql_version()
        except DatabaseError as error:
            raise CommandError(
                f"The database is not reachable: {error.__class__.__name__}"
            ) from error

        if not quiet:
            self.stdout.write("")
            self.stdout.write("Database")
            self.stdout.write(f"  PostgreSQL   {major}.{minor}")

        if (major, 0) < settings.MINIMUM_POSTGRESQL_VERSION:
            required = ".".join(str(part) for part in settings.MINIMUM_POSTGRESQL_VERSION)
            problems.append(f"PostgreSQL {major} is older than the required {required}.")

        state = deployment.migration_state()
        if not quiet:
            self.stdout.write(f"  migrations   {len(state.leaves)} app leaves")
        if state.pending:
            problems.append(
                f"{len(state.pending)} migration(s) are not applied: "
                f"{', '.join(migration.label for migration in state.pending[:5])}"
                f"{' …' if len(state.pending) > 5 else ''}. "
                "This build is running against an older schema."
            )
        if state.unknown:
            problems.append(
                f"The database has applied {len(state.unknown)} migration(s) this build "
                f"does not have: {', '.join(state.unknown[:5])}"
                f"{' …' if len(state.unknown) > 5 else ''}. "
                "This build is older than its database."
            )

        # -- storage -------------------------------------------------------
        if not quiet:
            self.stdout.write("")
            self.stdout.write("Storage")
        for root in deployment.storage_roots():
            if not quiet:
                mount = "read-only" if not root.must_be_writable else "writable"
                self.stdout.write(f"  {root.name:<24} {root.path}  [{root.kind}, {mount}]")
            if root.problem:
                problems.append(root.problem)

        # -- configuration -------------------------------------------------
        if not quiet:
            self.stdout.write("")
            self.stdout.write("Configuration")
            self.stdout.write(f"  auth mode    {settings.AUTH_MODE}")
            self.stdout.write(f"  real data    {'yes' if settings.REAL_DATA_ALLOWED else 'no'}")
            self.stdout.write(f"  debug        {'ON' if settings.DEBUG else 'off'}")

        unparseable = deployment.unparseable_boolean_variables()
        for name, value in sorted(unparseable.items()):
            warnings.append(
                f"{name}={value!r} is neither true nor false and is being read as false."
            )

        # -- verdict -------------------------------------------------------
        for warning in warnings:
            self.stdout.write(self.style.WARNING(f"WARNING: {warning}"))

        if problems:
            raise CommandError(
                "This deployment is not ready:\n"
                + "\n".join(f"  - {problem}" for problem in problems)
            )

        if not quiet:
            self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Deployment is ready."))
