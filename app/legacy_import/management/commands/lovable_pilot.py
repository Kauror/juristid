"""The Lovable pilot and the active cohort (docs/adr/0191, docs/adr/0202).

    python manage.py lovable_pilot validate --package P
    python manage.py lovable_pilot plan     --package P
    python manage.py lovable_pilot dry-run  --package P
    python manage.py lovable_pilot apply    --package P \\
        --expect-package-sha256 D --expect-plan-sha256 E \\
        --operator-intent COHORT --backup-set sets/YYYYMMDDTHHMMSSZ \\
        [--preserved-out F]
    python manage.py lovable_pilot verify   --package P
    python manage.py lovable_pilot preserved --before F
    python manage.py lovable_pilot preserved --snapshot-out F
    python manage.py lovable_pilot source-drift --package P [--config F]

`validate` checks the package with no database and prints its digest. `plan`
resolves every person, stage, instrument and organisation, checks that every
reference is free and prints the plan digest — no writes. `dry-run` writes the
whole package, verifies it record by record and rolls back; run it against an
isolated copy. `apply` writes it in one transaction, only with
``JURISTID_LOVABLE_PILOT`` and ``REAL_DATA_ALLOWED`` set, the typed intent, the
package digest, the reviewed plan digest (recomputed under a lock), a named
backup set and every reference free. Run again, it writes nothing. `verify`
reads back what was written and compares it with the package.

The package names its cohort (docs/adr/0202 §1): a version-1 package is
``lovable-pilot``, a version-2 one ``lovable-operational``; the intent typed at
`apply` is the cohort. Inside the transaction, `apply` digests every Teema
that was already there before and after the writes, everything included, and
refuses on any difference. `apply --preserved-out` (its directory checked
writable before anything is applied) writes the same moment's after-the-fact
digests, and `preserved --before` recomputes those later and fails on any
difference, naming references only. The after-the-fact digests leave out what
the nightly synchronisation and ordinary reading legitimately move: a state's
last observation (`observed*`, `updated_at`), the runs' decisions, and
notifications beyond their identities. `source-drift` reads, through the
synchronisation's GET-only transport and service login, the version and number
of every package topic and lists those Lovable changed, renumbered or no longer
shows since the snapshot — references and counts only.

A rebuild (docs/adr/0202 §5) runs in this order: stop the web and MCP services
(or pause their traffic) for the apply window; take and verify the backup set
(`preserved --snapshot-out` at it); `apply --preserved-out F`; `preserved
--before F`; only then `lovable_sync enroll`; then restart the services. The
apply refuses while a synchronisation run holds its lock.

The package holds the Chamber's content: keep it outside the repository, CI and
every ordinary development database.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.legacy_import import lovable_pilot, lovable_source
from app.legacy_import.limits import load_json_document


class Command(BaseCommand):
    help = (
        "Validate, plan, dry-run, apply or verify a Lovable package; check preserved Teemad "
        "or source drift (docs/adr/0191, docs/adr/0202). A rebuild: stop web and MCP for "
        "the apply window, then apply --preserved-out F, preserved --before F, and only then "
        "lovable_sync enroll."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "mode",
            choices=[
                "validate",
                "plan",
                "dry-run",
                "apply",
                "verify",
                "preserved",
                "source-drift",
            ],
        )
        parser.add_argument("--package", default="", help="The package file or directory.")
        parser.add_argument("--expect-package-sha256", default="")
        parser.add_argument(
            "--expect-plan-sha256",
            default="",
            help="apply: the plan digest `plan` printed and a person reviewed. Required.",
        )
        parser.add_argument("--operator-intent", default="", help="apply: the package's cohort.")
        parser.add_argument(
            "--backup-set",
            default="",
            help="apply: the verified backup set taken before the import, sets/YYYYMMDDTHHMMSSZ.",
        )
        parser.add_argument(
            "--preserved-out",
            default="",
            help=(
                "apply: write the pre-existing Teemad's after-the-fact digests (taken before the "
                "writes) here; the directory is checked writable before anything is applied."
            ),
        )
        parser.add_argument(
            "--before",
            default="",
            help=(
                "preserved: the digest file `apply --preserved-out` wrote. Compared without the "
                "synchronisation's own observations and decisions and notification states, which "
                "nightly runs and reading change; everything else must be identical."
            ),
        )
        parser.add_argument(
            "--snapshot-out",
            default="",
            help=(
                "preserved: write the current after-the-fact digests of every Teema here, in "
                "the --preserved-out format, applying nothing (e.g. at a backup, to prove a "
                "restore later with --before)."
            ),
        )
        parser.add_argument(
            "--config",
            "--credentials",
            dest="config",
            default="",
            help=(
                "source-drift: the Lovable service-login file; without it the LOVABLE_SYNC_* "
                "environment is read."
            ),
        )
        parser.add_argument("--json", action="store_true", help="Print a JSON summary.")

    def handle(self, *args: Any, **options: Any) -> None:
        if options["mode"] != "preserved" and not options["package"]:
            raise CommandError(f"{options['mode']} needs --package.")
        try:
            getattr(self, "_" + options["mode"].replace("-", "_"))(options)
        except lovable_pilot.LovablePilotError as error:
            raise CommandError(str(error)) from error
        except lovable_source.SourceError as error:
            raise CommandError(f"{error.code}: {error}") from error

    def _validate(self, options: dict[str, Any]) -> None:
        package = lovable_pilot.load_package(options["package"])
        report = lovable_pilot.validate_package(package)
        self._emit(
            options,
            {
                "package_sha256": lovable_pilot.package_digest(package),
                "cohort": lovable_pilot.package_cohort(package),
                "topics": report.topics,
                "open_topics": report.open_topics,
                "steps": report.steps,
                "commencements_ready": report.commencements_ready,
                "commencements_source_only": report.commencements_source_only,
                "problems": report.problems,
            },
        )
        if report.problems:
            raise CommandError(f"{len(report.problems)} problem(s) in the package.")

    def _build(self, options: dict[str, Any]) -> lovable_pilot.LovablePlan:
        return lovable_pilot.build_plan(options["package"])

    def _summary(self, plan: lovable_pilot.LovablePlan) -> dict[str, Any]:
        return {
            "package_sha256": plan.digest,
            "cohort": plan.cohort,
            "plan_sha256": lovable_pilot.plan_digest(plan),
            "database_state": plan.database_state,
            "existing_matters": plan.existing_matters,
            "collisions": plan.collisions,
            "expected_writes": plan.expected_writes(),
            "reserve_through": plan.package.get("reserve_through") or {},
            "warnings": {t.reference: t.warnings for t in plan.topics if t.warnings},
            "problems": plan.problems,
        }

    def _plan(self, options: dict[str, Any]) -> None:
        plan = self._build(options)
        self._emit(options, self._summary(plan))
        if plan.problems:
            raise CommandError(f"{len(plan.problems)} problem(s) block the apply.")

    def _dry_run(self, options: dict[str, Any]) -> None:
        plan = self._build(options)
        report = lovable_pilot.dry_run(plan)
        verify = report.verify
        self._emit(
            options,
            {
                **self._summary(plan),
                "rolled_back": True,
                "written": report.written,
                "matters_before": report.matters_before,
                "matters_after_inside_transaction": report.matters_after,
                "existing_unchanged": report.fingerprint_before == report.fingerprint_after,
                "preserved_checked": len(report.preserved_before),
                "verified": verify.checked,
                "discrepancies": verify.discrepancies,
                "held": verify.held,
            },
        )
        if not verify.ok:
            raise CommandError(f"{len(verify.discrepancies)} discrepancy(ies) in the dry run.")

    def _apply(self, options: dict[str, Any]) -> None:
        if options["preserved_out"]:
            _require_writable(Path(options["preserved_out"]))
        plan = self._build(options)
        report = lovable_pilot.apply_plan(
            plan,
            operator_intent=options["operator_intent"],
            expect_package_sha256=options["expect_package_sha256"],
            expect_plan_sha256=options["expect_plan_sha256"],
            backup_set=options["backup_set"],
        )
        if report.already_applied:
            self.stdout.write(
                self.style.WARNING(
                    f"Already applied: this package ({plan.cohort}) is in the database. Nothing "
                    "was written."
                )
            )
            return
        summary: dict[str, Any] = {
            "cohort": plan.cohort,
            "batch": str(report.batch_id),
            "written": report.written,
            "matters_before": report.matters_before,
            "matters_after": report.matters_after,
            "existing_unchanged": report.fingerprint_before == report.fingerprint_after,
            "preserved_checked": len(report.preserved_before),
            "rebased_local_checked": len(report.preserved_local_before),
        }
        if options["preserved_out"]:
            document = lovable_pilot.preserved_document(
                report.preserved_settled_before,
                report.preserved_references,
                local=report.preserved_local_before,
                declared=report.declared,
                created=report.created_ids,
            )
            Path(options["preserved_out"]).write_text(
                json.dumps(document, ensure_ascii=False, indent=1, sort_keys=True),
                encoding="utf-8",
            )
            summary["preserved_out"] = options["preserved_out"]
            summary["preserved_sha256"] = document["sha256"]
        self._emit(options, summary)

    def _verify(self, options: dict[str, Any]) -> None:
        plan = self._build(options)
        report = lovable_pilot.verify_applied(plan)
        self._emit(
            options,
            {
                "cohort": plan.cohort,
                "database_state": plan.database_state,
                "checked": report.checked,
                "discrepancies": report.discrepancies,
                "held": report.held,
            },
        )
        if not report.ok:
            raise CommandError(f"{len(report.discrepancies)} discrepancy(ies).")

    def _preserved(self, options: dict[str, Any]) -> None:
        if options["snapshot_out"] and options["before"]:
            raise CommandError("preserved takes --before or --snapshot-out, not both.")
        if options["snapshot_out"]:
            target = Path(options["snapshot_out"])
            _require_writable(target)
            document = lovable_pilot.preserved_snapshot()
            target.write_text(
                json.dumps(document, ensure_ascii=False, indent=1, sort_keys=True),
                encoding="utf-8",
            )
            self._emit(
                options,
                {
                    "snapshot_out": str(target),
                    "matters": len(document["matters"]),
                    "preserved_sha256": document["sha256"],
                },
            )
            return
        if not options["before"]:
            raise CommandError(
                "preserved needs --before (the file `apply --preserved-out` or "
                "`preserved --snapshot-out` wrote) or --snapshot-out."
            )
        try:
            data = Path(options["before"]).read_bytes()
        except OSError as error:
            raise CommandError(f"The digest file cannot be read: {error}") from error
        document = load_json_document(data, what="The preserved-digest file")
        result = lovable_pilot.check_preserved(document)
        self._emit(
            options,
            {
                "checked": result.checked,
                "unchanged": result.checked - len(result.changed) - len(result.missing),
                "changed": sorted(result.changed),
                "missing": sorted(result.missing),
                "rebased_checked": result.local_checked,
                "local_unchanged": result.local_checked - len(result.local_changed),
                "local_changed": sorted(result.local_changed),
                "declared_changes_only": not result.undeclared,
                "undeclared": sorted(result.undeclared),
            },
        )
        if not result.ok:
            differing = (
                len(result.changed)
                + len(result.missing)
                + len(result.local_changed)
                + len(result.undeclared)
            )
            raise CommandError(f"{differing} pre-existing Teema(s) differ.")

    def _source_drift(self, options: dict[str, Any]) -> None:
        package = lovable_pilot.load_package(options["package"])
        config = lovable_source.load_config(options["config"] or None)
        drift = lovable_pilot.source_drift(package, lovable_source.HttpTransport(config))

        def having(kind: str) -> list[str]:
            return [d["reference"] for d in drift if kind in d["kinds"]]

        self._emit(
            options,
            {
                "package_sha256": lovable_pilot.package_digest(package),
                "cohort": lovable_pilot.package_cohort(package),
                "topics": len(package.get("topics") or []),
                "drifted": len(drift),
                "changed_version": having("VERSION"),
                "renumbered": having("RENUMBERED"),
                "missing": having("MISSING"),
            },
        )

    def _emit(self, options: dict[str, Any], summary: dict[str, Any]) -> None:
        if options["json"]:
            self.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
            return
        for key, value in summary.items():
            self.stdout.write(f"{key}: {value}")


def _require_writable(target: Path) -> None:
    """Refuse before the apply, not after it: a digest file that cannot be written
    once the transaction has committed is a check nobody can run."""
    import tempfile

    if target.exists() and not target.is_file():
        raise CommandError(f"{target} is not a file.")
    directory = target.parent if str(target.parent) else Path(".")
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".preserved-", delete=True):
            pass
    except OSError as error:
        raise CommandError(
            f"{target}: {directory} is not writable ({error.strerror}); nothing was applied."
        ) from error
