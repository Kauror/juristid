"""The nightly Lovable synchronisation (docs/adr/0192).

    python manage.py lovable_sync status
    python manage.py lovable_sync run --trigger scheduled --credentials F [--backup-set S]
    python manage.py lovable_sync run --trigger manual --credentials F [--backup-set S]
    python manage.py lovable_sync check
    python manage.py lovable_sync access-probe --credentials F
    python manage.py lovable_sync access-sql
    python manage.py lovable_sync control --mode off|compare|apply --reason R
    python manage.py lovable_sync control --cut-over \\
        --operator-intent lovable-sync-cutover --reason R
    python manage.py lovable_sync resolve --reference 2026_501 --field stage --note N

`run` reads Lovable as the service login named in the credentials file and
reconciles the pilot's 100 Teemad. It needs ``JURISTID_LOVABLE_SYNC`` and
``REAL_DATA_ALLOWED`` — set only on the host scheduler's one-off container — and
obeys the control row: OFF refuses, COMPARE writes nothing, APPLY writes the
allowlisted changes when a verified backup set is named. `check` compares the
newest stored snapshot with Juristid now, without reaching Lovable.
`access-probe` shows the service login reads, and tries to prove it can do
nothing else: PASS, FAIL, or INDETERMINATE when HTTP cannot tell a refusal from
a write that matched nothing — then `access-sql` prints the read-only catalogue
inspection the owner runs in Lovable's SQL editor before the job may be enabled.

Exit status: 0 done, 2 refused before starting (paused, duplicate, locked),
3 held for review (circuit breaker or no backup), 1 failed.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.legacy_import import lovable_source, lovable_sync
from app.legacy_import.lovable_sync_models import LovableSyncRun, SyncRunStatus, SyncTrigger

CUTOVER_INTENT = "lovable-sync-cutover"


class Command(BaseCommand):
    help = "Run, check, pause or retire the nightly Lovable synchronisation (docs/adr/0192)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "action",
            choices=[
                "status",
                "due",
                "run",
                "check",
                "access-probe",
                "access-sql",
                "control",
                "resolve",
            ],
        )
        parser.add_argument("--trigger", choices=["scheduled", "manual"], default="manual")
        parser.add_argument(
            "--credentials",
            default="",
            help="A credentials file; without it the LOVABLE_SYNC_* environment is read.",
        )
        parser.add_argument("--backup-set", default="")
        parser.add_argument("--mode", choices=["off", "compare", "apply"], default="")
        parser.add_argument("--cut-over", action="store_true")
        parser.add_argument("--operator-intent", default="")
        parser.add_argument("--reason", default="")
        parser.add_argument("--reference", default="")
        parser.add_argument("--field", default="")
        parser.add_argument("--note", default="")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            getattr(self, "_" + options["action"].replace("-", "_"))(options)
        except lovable_sync.SyncRefused as error:
            self.stderr.write(f"REFUSED {error.code}: {error}")
            sys.exit(2)
        except lovable_source.SourceError as error:
            raise CommandError(f"{error.code}: {error}") from error

    # -- reading ---------------------------------------------------------------

    def _status(self, options: dict[str, Any]) -> None:
        ctl = lovable_sync.control()
        runs = LovableSyncRun.objects.order_by("-started_at")
        self._emit(
            options,
            {
                "mode": ctl.mode,
                "cut_over": ctl.cut_over,
                "last_run": _run_dict(runs.first()),
                "last_success": _run_dict(runs.filter(status=SyncRunStatus.SUCCEEDED).first()),
            },
        )

    def _access_probe(self, options: dict[str, Any]) -> None:
        config = lovable_source.load_config(self._credentials(options))
        result = lovable_source.access_probe(config)
        for check in result.checks:
            self.stdout.write(f"{check.status}  {check.name}  ({check.detail})")
        if result.verdict == lovable_source.FAIL:
            raise CommandError(
                "The service login is not read-only as required; do not enable the job."
            )
        if result.verdict != lovable_source.PASS:
            raise CommandError(
                "ACCESS PROBE INDETERMINATE: HTTP could not prove every write is refused. "
                "Do not enable the job (and never APPLY) until the read-only inspection "
                "(`lovable_sync access-sql`) has been run in Lovable's SQL editor and shows "
                "no write path for this identity."
            )
        self.stdout.write("ACCESS PROBE PASSED")

    def _access_sql(self, options: dict[str, Any]) -> None:
        """The read-only catalogue inspection that settles what HTTP cannot."""
        self.stdout.write(lovable_source.GRANT_INSPECTION_SQL)

    def _due(self, options: dict[str, Any]) -> None:
        """For the host scheduler, which has no Tallinn clock of its own: whether
        this is the 00:xx Europe/Tallinn run, and in which mode. Writes nothing."""
        trigger = SyncTrigger.SCHEDULED if options["trigger"] == "scheduled" else SyncTrigger.MANUAL
        self.stdout.write(lovable_sync.due(trigger=trigger))

    # -- running ---------------------------------------------------------------

    def _run(self, options: dict[str, Any]) -> None:
        if not (settings.LOVABLE_SYNC_ENABLED and settings.REAL_DATA_ALLOWED):
            raise CommandError(
                "run needs JURISTID_LOVABLE_SYNC and REAL_DATA_ALLOWED on this container."
            )
        config = lovable_source.load_config(self._credentials(options))
        trigger = SyncTrigger.SCHEDULED if options["trigger"] == "scheduled" else SyncTrigger.MANUAL
        run = lovable_sync.run_sync(
            trigger=trigger,
            transport=lovable_source.HttpTransport(config),
            backup_set=options["backup_set"],
        )
        self._finish(options, run)

    def _check(self, options: dict[str, Any]) -> None:
        self._finish(options, lovable_sync.run_sync(trigger=SyncTrigger.CHECK))

    def _finish(self, options: dict[str, Any], run: LovableSyncRun) -> None:
        if options["json"]:
            self._emit(options, _run_dict(run) or {})
        else:
            for line in lovable_sync.summary_lines(run):
                self.stdout.write(line)
            self.stdout.write(f"run {run.run_key} {run.status} verified={run.verified} {run.error}")
        if run.status == SyncRunStatus.FAILED:
            sys.exit(1)
        if run.status == SyncRunStatus.REVIEW_ONLY:
            sys.exit(3)

    # -- controlling -----------------------------------------------------------

    def _control(self, options: dict[str, Any]) -> None:
        reason = options["reason"].strip()
        if not reason:
            raise CommandError("control needs --reason.")
        if options["cut_over"]:
            if options["operator_intent"] != CUTOVER_INTENT:
                raise CommandError(f"Final cutover needs --operator-intent {CUTOVER_INTENT}.")
            row = lovable_sync.cut_over(reason=reason)
        elif options["mode"]:
            row = lovable_sync.set_mode(options["mode"].upper(), reason=reason)
        else:
            raise CommandError("control needs --mode or --cut-over.")
        self.stdout.write(f"mode={row.mode} cut_over={row.cut_over}")

    def _resolve(self, options: dict[str, Any]) -> None:
        if not (options["reference"] and options["field"] and options["note"].strip()):
            raise CommandError("resolve needs --reference, --field and --note.")
        lovable_sync.resolve_field(
            reference=options["reference"],
            field_name=options["field"],
            note=options["note"].strip(),
        )
        self.stdout.write(f"resolved {options['reference']} {options['field']}")

    # -- helpers ---------------------------------------------------------------

    def _credentials(self, options: dict[str, Any]) -> str | None:
        return str(options["credentials"]) or None

    def _emit(self, options: dict[str, Any], data: dict[str, Any]) -> None:
        self.stdout.write(json.dumps(data, indent=None if options["json"] else 2, default=str))


def _run_dict(run: LovableSyncRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "run_key": run.run_key,
        "trigger": run.trigger,
        "mode": run.mode,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "snapshot_taken_at": run.snapshot_taken_at,
        "snapshot_sha256": run.snapshot_sha256,
        "backup_set": run.backup_set,
        "verified": run.verified,
        "error": run.error,
        "counts": run.counts,
    }
