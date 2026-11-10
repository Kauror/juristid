"""The full Lovable inventory and the historical backfill (brief §5 A1–A4).

    python manage.py lovable_inventory read   --out DIR [--credentials F] [--store]
    python manage.py lovable_inventory ledger --document FILE --out DIR [--store]
    python manage.py lovable_inventory report
    python manage.py lovable_inventory backfill --step package --document FILE --out DIR \\
        [--people FILE]
    python manage.py lovable_inventory backfill --step validate|plan|dry-run|verify --package P
    python manage.py lovable_inventory backfill --step apply --package P \\
        --expect-package-sha256 D --expect-plan-sha256 E \\
        --operator-intent lovable-history --backup-set sets/YYYYMMDDTHHMMSSZ \\
        [--preserved-out F]

`read` takes one full, consistent reading of every live Lovable topic through the
synchronisation's GET-only transport and service login (`lovable_source`), and
writes to DIR: ``reading.json`` (the verified reading), ``ledger.json``,
``ledger.csv`` and ``coverage.md``. It writes nothing to the database unless
``--store`` is given (then one `LovableInventoryRun`). `ledger` does the same
offline from a saved reading. `report` prints the newest stored run's counts.

`backfill --step package` builds, from a saved reading and the ledger it gives
now, the CREATE package of proven-absent non-operational topics
(``package.json``) and the list of every row left out with its reason
(``backfill-report.json``, which also lists the undecided ARCHIVE_UNLINKED,
POSSIBLE_DUPLICATE and LINK_ENRICHMENT rows for a person — ``review`` — and
counts what the package would create — ``distribution``: closure basis,
disposition, source status) — read-only. ``coverage.md`` has the same review
list under «Needs a person». The other steps are `lovable_pilot`'s,
with every one of its gates; `apply` creates Teemad only. Afterwards:
`lovable_pilot preserved --before F`, then `lovable_sync enroll --cohort
lovable-history`.

Every file written holds the Chamber's register: keep DIR outside the
repository, CI and every ordinary development database.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from app.legacy_import import lovable_backfill, lovable_inventory, lovable_pilot, lovable_source
from app.legacy_import.limits import SourceLimitExceeded, load_json_document
from app.legacy_import.lovable_inventory_models import InventoryTrigger
from app.legacy_import.lovable_sync import SyncRefused


class Command(BaseCommand):
    help = (
        "Read the whole Lovable register into a per-topic coverage ledger (read-only), and "
        "plan / apply the gated historical backfill of proven-absent topics."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("mode", choices=["read", "ledger", "report", "backfill"])
        parser.add_argument(
            "--step",
            choices=["package", "validate", "plan", "dry-run", "apply", "verify"],
            default="package",
        )
        parser.add_argument("--out", default="", help="read / ledger / backfill package: DIR.")
        parser.add_argument("--document", default="", help="A saved reading (reading.json).")
        parser.add_argument("--package", default="", help="backfill: the package file.")
        parser.add_argument(
            "--people",
            default="",
            help="backfill package: a JSON object of Lovable owner names → Juristid display "
            "names (default: none; a closed file's departed owner stays source text).",
        )
        parser.add_argument(
            "--store",
            action="store_true",
            help="read / ledger: also store the ledger as a LovableInventoryRun.",
        )
        parser.add_argument(
            "--config",
            "--credentials",
            dest="config",
            default="",
            help="read: the Lovable service-login file; else the LOVABLE_SYNC_* environment.",
        )
        parser.add_argument("--expect-package-sha256", default="")
        parser.add_argument("--expect-plan-sha256", default="")
        parser.add_argument("--operator-intent", default="")
        parser.add_argument("--backup-set", default="")
        parser.add_argument("--preserved-out", default="")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            getattr(self, "_" + options["mode"])(options)
        except lovable_inventory.InventoryError as error:
            raise CommandError(f"{error.code}: {error}") from error
        except lovable_source.SourceError as error:
            raise CommandError(f"{error.code}: {error}") from error
        except SyncRefused as error:
            raise CommandError(f"REFUSED {error.code}: {error}") from error
        except (lovable_pilot.LovablePilotError, SourceLimitExceeded) as error:
            raise CommandError(str(error)) from error

    # -- the inventory -----------------------------------------------------------

    def _out(self, options: dict[str, Any]) -> Path:
        if not options["out"]:
            raise CommandError(f"{options['mode']} needs --out DIR.")
        target = Path(options["out"])
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _read(self, options: dict[str, Any]) -> None:
        if not (settings.LOVABLE_SYNC_ENABLED and settings.REAL_DATA_ALLOWED):
            # The same gate as `lovable_sync observe`: reading the real register
            # happens only on the container the host runs for it.
            raise CommandError(
                "read needs JURISTID_LOVABLE_SYNC and REAL_DATA_ALLOWED on this container."
            )
        out = self._out(options)
        config = lovable_source.load_config(options["config"] or None)
        document = lovable_inventory.read_full(lovable_source.HttpTransport(config))
        _write_json(out / "reading.json", document)
        self._ledger_from(document, out, options, trigger=InventoryTrigger.READ)

    def _ledger(self, options: dict[str, Any]) -> None:
        out = self._out(options)
        self._ledger_from(_load_document(options), out, options, trigger=InventoryTrigger.LEDGER)

    def _ledger_from(
        self, document: dict[str, Any], out: Path, options: dict[str, Any], *, trigger: str
    ) -> None:
        ledger = lovable_inventory.build_ledger(document, now=timezone.now())
        _write_json(out / "ledger.json", ledger.document())
        (out / "ledger.csv").write_text(lovable_inventory.ledger_csv(ledger), encoding="utf-8")
        (out / "coverage.md").write_text(
            lovable_inventory.coverage_markdown(ledger), encoding="utf-8"
        )
        summary: dict[str, Any] = {
            "reading_sha256": ledger.source_sha256,
            "taken_at": ledger.taken_at,
            "ledger_sha256": ledger.sha256(),
            "out": str(out),
            "stored": None,
            **ledger.counts(),
        }
        if options["store"]:
            summary["stored"] = str(lovable_inventory.store_run(ledger, trigger=trigger).pk)
        self._emit(options, summary)

    def _report(self, options: dict[str, Any]) -> None:
        run = lovable_inventory.latest_inventory()
        if run is None:
            self._emit(options, {"stored": None})
            return
        self._emit(
            options,
            {
                "stored": str(run.pk),
                "trigger": run.trigger,
                "created_at": run.created_at,
                "taken_at": run.taken_at,
                "reading_sha256": run.source_sha256,
                "ledger_sha256": run.ledger_sha256,
                "reader_version": run.reader_version,
                **run.counts,
            },
        )

    # -- the backfill ------------------------------------------------------------

    def _backfill(self, options: dict[str, Any]) -> None:
        step = options["step"]
        if step == "package":
            self._backfill_package(options)
            return
        if not options["package"]:
            raise CommandError(f"backfill --step {step} needs --package.")
        package = lovable_pilot.load_package(options["package"])
        if step == "validate":
            problems = lovable_backfill.validate(package)
            self._emit(
                options,
                {
                    "package_sha256": lovable_pilot.package_digest(package),
                    "cohort": lovable_pilot.package_cohort(package),
                    "topics": len(package.get("topics") or []),
                    "problems": problems,
                },
            )
            if problems:
                raise CommandError(f"{len(problems)} problem(s) in the package.")
        elif step == "plan":
            plan = lovable_backfill.plan(package)
            self._emit(options, _plan_summary(plan))
            if plan.problems:
                raise CommandError(f"{len(plan.problems)} problem(s) block the apply.")
        elif step == "dry-run":
            plan = lovable_backfill.plan(package)
            report = lovable_pilot.dry_run(plan)
            self._emit(
                options,
                {
                    **_plan_summary(plan),
                    "rolled_back": True,
                    "written": report.written,
                    "matters_before": report.matters_before,
                    "matters_after_inside_transaction": report.matters_after,
                    "existing_unchanged": report.fingerprint_before == report.fingerprint_after,
                    "preserved_checked": len(report.preserved_before),
                    "verified": report.verify.checked,
                    "discrepancies": report.verify.discrepancies,
                },
            )
            if not report.verify.ok:
                raise CommandError(f"{len(report.verify.discrepancies)} discrepancy(ies).")
        elif step == "apply":
            self._backfill_apply(package, options)
        else:
            plan = lovable_backfill.plan(package)
            verify = lovable_pilot.verify_applied(plan)
            self._emit(
                options,
                {
                    "cohort": plan.cohort,
                    "database_state": plan.database_state,
                    "checked": verify.checked,
                    "discrepancies": verify.discrepancies,
                },
            )
            if not verify.ok:
                raise CommandError(f"{len(verify.discrepancies)} discrepancy(ies).")

    def _backfill_package(self, options: dict[str, Any]) -> None:
        out = self._out(options)
        document = _load_document(options)
        people: dict[str, str] = {}
        if options["people"]:
            loaded = load_json_document(Path(options["people"]).read_bytes(), what="The people map")
            if not isinstance(loaded, dict):
                raise CommandError("--people is a JSON object.")
            people = {str(k): str(v) for k, v in loaded.items()}
        ledger = lovable_inventory.build_ledger(document, now=timezone.now())
        selection = lovable_backfill.build_package(document, ledger, people=people)
        if selection.package is not None:
            _write_json(out / "package.json", selection.package)
        report = selection.report()
        _write_json(out / "backfill-report.json", report)
        self._emit(
            options,
            {
                "out": str(out),
                "ledger_sha256": ledger.sha256(),
                "package_sha256": report["package_sha256"],
                "reader_version": report["reader_version"],
                **selection.counts(),
                "review_by_category": report["review_by_category"],
                "closure_basis": report["distribution"]["closure_basis"],
                "disposition": report["distribution"]["disposition"],
            },
        )

    def _backfill_apply(self, package: dict[str, Any], options: dict[str, Any]) -> None:
        if options["preserved_out"]:
            from app.legacy_import.management.commands.lovable_pilot import (
                _require_writable,
            )

            _require_writable(Path(options["preserved_out"]))
        plan = lovable_backfill.plan(package)
        report = lovable_backfill.apply(
            package,
            operator_intent=options["operator_intent"],
            expect_package_sha256=options["expect_package_sha256"],
            expect_plan_sha256=options["expect_plan_sha256"],
            backup_set=options["backup_set"],
        )
        if report.already_applied:
            self.stdout.write(
                self.style.WARNING("Already applied: this backfill is in the database. Nothing.")
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
        }
        if options["preserved_out"]:
            document = lovable_pilot.preserved_document(
                report.preserved_settled_before,
                report.preserved_references,
                created=report.created_ids,
            )
            _write_json(Path(options["preserved_out"]), document)
            summary["preserved_out"] = options["preserved_out"]
            summary["preserved_sha256"] = document["sha256"]
        self._emit(options, summary)

    def _emit(self, options: dict[str, Any], summary: dict[str, Any]) -> None:
        if options["json"]:
            self.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
            return
        for key, value in summary.items():
            self.stdout.write(f"{key}: {value}")


def _plan_summary(plan: lovable_pilot.LovablePlan) -> dict[str, Any]:
    return {
        "package_sha256": plan.digest,
        "cohort": plan.cohort,
        "plan_sha256": lovable_pilot.plan_digest(plan),
        "database_state": plan.database_state,
        "existing_matters": plan.existing_matters,
        "collisions": plan.collisions,
        "identity_collisions": plan.identity_collisions,
        "expected_writes": plan.expected_writes(),
        "warnings": sum(len(t.warnings) for t in plan.topics),
        "problems": plan.problems,
    }


def _load_document(options: dict[str, Any]) -> dict[str, Any]:
    if not options["document"]:
        raise CommandError(f"{options['mode']} needs --document FILE (a saved reading).")
    try:
        data = Path(options["document"]).read_bytes()
    except OSError as error:
        raise CommandError(f"The reading cannot be read: {error}") from error
    document = load_json_document(data, what="The Lovable reading")
    if not isinstance(document, dict):
        raise CommandError("The reading is not a JSON object.")
    return document


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True, default=str),
        encoding="utf-8",
    )
