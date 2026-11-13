"""The nightly Lovable synchronisation (docs/adr/0192).

    python manage.py lovable_sync status
    python manage.py lovable_sync run --trigger scheduled --credentials F [--backup-set S]
    python manage.py lovable_sync run --trigger manual --credentials F [--backup-set S]
    python manage.py lovable_sync check
    python manage.py lovable_sync access-probe --credentials F
    python manage.py lovable_sync access-sql
    python manage.py lovable_sync audit-sql
    python manage.py lovable_sync observe [--manual] [--backfill] --credentials F
    python manage.py lovable_sync observe-status
    python manage.py lovable_sync prune-observations [--manual]
    python manage.py lovable_sync report [--json]
    python manage.py lovable_sync control --observe on|off --reason R
    python manage.py lovable_sync control --mode off|compare|apply --reason R
    python manage.py lovable_sync control --cut-over \\
        --operator-intent lovable-sync-cutover --reason R
    python manage.py lovable_sync resolve --reference 2026_501 --field stage --note N
    python manage.py lovable_sync enroll --cohort lovable-operational \\
        --package-sha256 SHA --expect N --reason R [--supersede lovable-pilot]
    python manage.py lovable_sync enroll --cohort lovable-register \\
        --package-sha256 SHA --expect N --reason R --replaces lovable-operational
    python manage.py lovable_sync retire --cohort lovable-register \\
        --reference 2026_511 --reason R [--apply]
    python manage.py lovable_sync due --trigger manual --on-demand [--kind lovable|onenote]
    python manage.py lovable_sync replay [--run-key K] [--as-of YYYY-MM-DD] [--list-new]
    python manage.py lovable_sync pending
    python manage.py lovable_sync resolve-new-topic --source-id UUID --note N

`due --on-demand` (docs/adr/0213) is the host's on-demand firing: ``DUE <mode>``
when an administrator's «Sünkrooni nüüd» request is waiting
(`DataSyncRequest`, kind LOVABLE_SYNC, PENDING), else ``NOT_DUE NO_REQUEST``.
It reads only. `replay` plans tonight's rules over a stored reading inside a
read-only transaction: nothing is written, the database refuses any write.
`pending` prints the pending differences as counts by field and reason.
`resolve-new-topic` records an operator's decision that a held new topic is not
created by the machine.

`run` reads Lovable as the service login named in the credentials file and
reconciles the pilot's 100 Teemad and every enrolled cohort (docs/adr/0202). It
needs ``JURISTID_LOVABLE_SYNC`` and ``REAL_DATA_ALLOWED`` — set only on the
host scheduler's one-off container — and
obeys the control row: OFF refuses, COMPARE writes nothing, APPLY writes the
allowlisted changes when a verified backup set is named. `check` compares the
newest stored snapshot with Juristid now, without reaching Lovable.
`access-probe` shows, with GET requests only, that the service login reads what
the job needs and not the hidden history or deleted topics. It sends nothing
write-shaped (owner rule 2026-11-02), so it never PASSes: the write side is
INDETERMINATE until `access-sql` — the read-only catalogue inspection the owner
runs in Lovable's SQL editor — shows no write path for the identity.

`observe` reads the whole live register for the wider observation
(docs/adr/0201): counts only on output; refused unless `control --observe on`.
`report` is the approved diagnostic: the read service's status and readiness
(`lovable_observability.status` / `.readiness`, the same answers the pages and
the MCP tools give) reduced to counts, codes and times — no sentence, title,
reference or value. It reads only and asks as nobody, so no Teema is shown.
`audit-sql` prints the owner's *proposal* for a SELECT-only audit-ledger path —
text only, nothing runs it. `prune-observations` is the observation's retention
job (docs/adr/0201 §8): it never reads Lovable, needs no credentials, and runs
every night whether the observation is on, off, paused or failing; a second
firing on the same Tallinn date is refused (DUPLICATE, exit 2).

`enroll` (docs/adr/0202 §6) adds an imported cohort to the nightly comparison
after its import has verified: exactly the Teemad its approved provenance
names, with baselines from the import snapshot, under the run lock. It prints
counts only; the same enrolment again says so and changes nothing. With
``--supersede lovable-pilot`` (a rebuild, docs/adr/0202 §6) every pilot state
must be rebased by the package: each is archived and re-seeded in place, and
the pilot cohort is retired. Order, after the rebuild's apply and before the
next night: (1) `lovable_pilot preserved --before <file>` — it compares the
synchronisation state too, so it reports differences once enrolment has
re-seeded it; (2) `enroll --supersede`. A nightly or manual run that reconciled
after the rebuild was applied refuses the enrolment (SYNC_RAN_SINCE_REBUILD):
restore the pre-rebuild backup set, or the owner decides.

`retire` (audit 2026-11 C-L-04) records that a cohort Teema left its cohort
because it was permanently deleted (docs/adr/0165), so the nightly size check
expects one fewer. A deletion records this itself; `retire` is for a deletion
whose retirement was not recorded, and it is refused unless the Teema is a
tombstone whose MATTER_DELETED event removed a synchronisation state, a cohort
membership record names it in exactly ``--cohort`` (REL-01: NO_MEMBERSHIP_EVIDENCE
when none does, as for a Teema deleted before migration legacy_import.0024;
WRONG_COHORT when another cohort's does), nothing has retired it yet, and the
cohort is short. Dry run unless ``--apply``; it writes no business record.
`report` lists every retirement (cohort, kind, time) and each cohort's numbers.

Exit status: 0 done, 2 refused before starting (paused, duplicate, locked),
3 held for review (circuit breaker or no backup), 1 failed.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.legacy_import import lovable_observation, lovable_source, lovable_sync
from app.legacy_import.lovable_observation_models import (
    ObservationRunStatus,
    RetentionRunStatus,
)
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
                "audit-sql",
                "observe",
                "observe-status",
                "prune-observations",
                "report",
                "control",
                "resolve",
                "enroll",
                "retire",
                "replay",
                "pending",
                "resolve-new-topic",
            ],
        )
        parser.add_argument("--trigger", choices=["scheduled", "manual"], default="manual")
        parser.add_argument(
            "--credentials",
            default="",
            help="A credentials file; without it the LOVABLE_SYNC_* environment is read.",
        )
        parser.add_argument("--backup-set", default="")
        parser.add_argument(
            "--max-changed",
            type=int,
            default=None,
            help=(
                "run --trigger manual: raise the Teemad-written breaker for this one "
                "supervised run (the first source-authoritative convergence); recorded "
                "in the run. 0 is a limit of zero."
            ),
        )
        parser.add_argument(
            "--max-restored",
            type=int,
            default=None,
            help=(
                "run --trigger manual: raise the restore breaker for this one supervised "
                "run (a held backlog of restores drains at once); recorded in the run."
            ),
        )
        parser.add_argument("--mode", choices=["off", "compare", "apply"], default="")
        parser.add_argument("--cut-over", action="store_true")
        parser.add_argument("--operator-intent", default="")
        parser.add_argument("--reason", default="")
        parser.add_argument("--reference", default="")
        parser.add_argument("--field", default="")
        parser.add_argument("--note", default="")
        parser.add_argument("--json", action="store_true")
        parser.add_argument(
            "--on-demand",
            action="store_true",
            help="due: answer for the host's on-demand firing (a waiting request).",
        )
        parser.add_argument(
            "--kind",
            choices=["lovable", "onenote"],
            default="lovable",
            help="due --on-demand: which request lane to ask about (read-only).",
        )
        parser.add_argument("--run-key", default="", help="replay: the stored reading's run.")
        parser.add_argument("--as-of", default="", help="replay: the day the rules read as today.")
        parser.add_argument(
            "--list-new", action="store_true", help="replay: list new topics by reference."
        )
        parser.add_argument(
            "--reading",
            default="",
            help="replay: a full-detail inventory reading (lovable_inventory read) instead "
            "of the newest stored snapshot.",
        )
        parser.add_argument("--source-id", default="", help="resolve-new-topic: the Lovable id.")
        parser.add_argument(
            "--manual",
            action="store_true",
            help="observe / prune-observations: a manual run, not the night's.",
        )
        parser.add_argument(
            "--backfill", action="store_true", help="observe: read the audit ledger to the end."
        )
        parser.add_argument("--observe", choices=["on", "off"], default="")
        parser.add_argument("--cohort", default="", help="enroll / retire: the cohort key.")
        parser.add_argument(
            "--apply",
            action="store_true",
            help="retire: record the retirement (without it, a dry run).",
        )
        parser.add_argument(
            "--package-sha256", default="", help="enroll: the approved package digest."
        )
        parser.add_argument(
            "--expect", type=int, default=0, help="enroll: how many Teemad the cohort holds."
        )
        parser.add_argument(
            "--replaces",
            action="append",
            default=[],
            help=(
                "enroll: a cohort the source-authoritative replacement retires "
                "(docs/adr/0203 §3); repeat it or give a comma list."
            ),
        )
        parser.add_argument(
            "--supersede",
            default="",
            help=(
                "enroll: the cohort a rebuild retires (lovable-pilot). Run "
                "`lovable_pilot preserved --before` first, and enrol before the next night."
            ),
        )

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
            reads = all(
                c.status == lovable_source.PASS
                for c in result.checks
                if c.name != lovable_source.WRITE_SIDE_CHECK
            )
            raise CommandError(
                "ACCESS PROBE INDETERMINATE: "
                + ("every read check passed; " if reads else "a read check was not proven; ")
                + "the write side is never probed over HTTP (owner rule 2026-11-02). "
                "Do not enable the job (and never APPLY) until the read-only inspection "
                "(`lovable_sync access-sql`) has been run in Lovable's SQL editor and shows "
                "no write path for this identity."
            )
        self.stdout.write("ACCESS PROBE PASSED")

    def _access_sql(self, options: dict[str, Any]) -> None:
        """The read-only catalogue inspection that settles what HTTP cannot."""
        self.stdout.write(lovable_source.GRANT_INSPECTION_SQL)

    def _audit_sql(self, options: dict[str, Any]) -> None:
        """The owner's proposal for a SELECT-only audit-ledger path. Text only."""
        self.stdout.write(lovable_source.AUDIT_ACCESS_SQL)

    def _observe_status(self, options: dict[str, Any]) -> None:
        """Reads only; creates no control row (safe in a read-only smoke)."""
        self._emit(options, lovable_observation.observation_status())

    def _report(self, options: dict[str, Any]) -> None:
        """Counts, codes and times from the one read service; writes nothing.

        Asked as nobody (`AnonymousUser`): the service then names no Teema and
        lists no plan row, and `codes_only` drops every remaining sentence."""
        from django.contrib.auth.models import AnonymousUser

        from app.legacy_import import lovable_observability

        nobody = AnonymousUser()
        self._emit(
            options,
            {
                "status": codes_only(lovable_observability.status(nobody).as_dict()),
                "readiness": codes_only(lovable_observability.readiness(nobody).as_dict()),
                # Numbers, codes and times only, like the rest (audit 2026-11 C-L-04).
                "retirements": lovable_sync.retirement_report(),
            },
        )

    def _due(self, options: dict[str, Any]) -> None:
        """For the host scheduler, which has no Tallinn clock of its own: whether
        this is the 00:xx Europe/Tallinn run, and in which mode. Writes nothing."""
        trigger = SyncTrigger.SCHEDULED if options["trigger"] == "scheduled" else SyncTrigger.MANUAL
        if options["on_demand"]:
            self.stdout.write(lovable_sync.due_on_demand(options["kind"]))
            return
        self.stdout.write(lovable_sync.due(trigger=trigger))

    def _replay(self, options: dict[str, Any]) -> None:
        """Tonight's rules over a stored reading, written to nothing (read-only)."""
        import datetime as dt

        as_of = dt.date.fromisoformat(options["as_of"]) if options["as_of"] else None
        reading = None
        if options["reading"]:
            with open(options["reading"], encoding="utf-8") as handle:
                reading = json.load(handle)
        result = lovable_sync.replay(run_key=options["run_key"], today=as_of, reading=reading)
        if not options["list_new"]:
            reasons: dict[str, int] = {}
            for item in result["new_topics"]:
                reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
            result["new_topics"] = reasons
        self._emit(options, result)

    def _pending(self, options: dict[str, Any]) -> None:
        """The pending differences in counts: by field and outcome, and by reason."""
        rows = lovable_sync.pending_differences(visible_to=None)
        by_field: dict[str, int] = {}
        by_reason: dict[str, int] = {}
        for row in rows:
            key = f"{row.field}:{row.outcome}"
            by_field[key] = by_field.get(key, 0) + 1
            code = (row.reason or "-").split(":")[0]
            by_reason[code] = by_reason.get(code, 0) + 1
        self._emit(
            options,
            {
                "pending": len(rows),
                "teemad": len({r.reference for r in rows if r.kind == "FIELD"}),
                "new_topics": sum(1 for r in rows if r.kind == "NEW_TOPIC"),
                "by_field": dict(sorted(by_field.items())),
                "by_reason": dict(sorted(by_reason.items())),
            },
        )

    def _resolve_new_topic(self, options: dict[str, Any]) -> None:
        if not (options["source_id"] and options["note"].strip()):
            raise CommandError("resolve-new-topic needs --source-id and --note.")
        row = lovable_sync.resolve_new_topic(
            source_id=options["source_id"], note=options["note"].strip()
        )
        self.stdout.write(f"resolved new topic {row.reference}: {row.status}")

    # -- running ---------------------------------------------------------------

    def _run(self, options: dict[str, Any]) -> None:
        trigger = SyncTrigger.SCHEDULED if options["trigger"] == "scheduled" else SyncTrigger.MANUAL
        # A breaker override is a person's decision for one supervised run (LS-06):
        # never on the scheduled night, never negative; refused before any read.
        for flag in ("max_changed", "max_restored"):
            value = options[flag]
            if value is None:
                continue
            if trigger != SyncTrigger.MANUAL:
                raise CommandError(
                    f"--{flag.replace('_', '-')} is only for a supervised --trigger manual run; "
                    "the scheduled night keeps the approved limits."
                )
            if value < 0:
                raise CommandError(f"--{flag.replace('_', '-')} cannot be negative.")
        if not (settings.LOVABLE_SYNC_ENABLED and settings.REAL_DATA_ALLOWED):
            raise CommandError(
                "run needs JURISTID_LOVABLE_SYNC and REAL_DATA_ALLOWED on this container."
            )
        config = lovable_source.load_config(self._credentials(options))
        run = lovable_sync.run_sync(
            trigger=trigger,
            transport=lovable_source.HttpTransport(config),
            backup_set=options["backup_set"],
            max_changed=options["max_changed"],
            max_restored=options["max_restored"],
        )
        self._finish(options, run)

    def _observe(self, options: dict[str, Any]) -> None:
        if not (settings.LOVABLE_SYNC_ENABLED and settings.REAL_DATA_ALLOWED):
            raise CommandError(
                "observe needs JURISTID_LOVABLE_SYNC and REAL_DATA_ALLOWED on this container."
            )
        config = lovable_source.load_config(self._credentials(options))
        trigger = SyncTrigger.MANUAL if options["manual"] else SyncTrigger.SCHEDULED
        run = lovable_observation.run_observation(
            trigger=trigger,
            transport=lovable_source.HttpTransport(config),
            backfill=options["backfill"],
        )
        self.stdout.write(
            json.dumps(
                {
                    "run_key": run.run_key,
                    "status": run.status,
                    "audit_access": run.audit_access,
                    "audit_error": run.audit_error,
                    "error": run.error,
                    "counts": run.counts,
                },
                default=str,
            )
        )
        if run.status == ObservationRunStatus.FAILED:
            sys.exit(1)

    def _prune_observations(self, options: dict[str, Any]) -> None:
        """The retention job. Local only: never reads Lovable, no credentials."""
        trigger = SyncTrigger.MANUAL if options["manual"] else SyncTrigger.SCHEDULED
        run = lovable_observation.prune_observations(trigger=trigger)
        self.stdout.write(
            json.dumps(
                {
                    "run_key": run.run_key,
                    "status": run.status,
                    "error": run.error,
                    "counts": run.counts,
                },
                default=str,
            )
        )
        if run.status == RetentionRunStatus.FAILED:
            sys.exit(1)

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
        elif options["mode"] or options["observe"]:
            if options["mode"]:
                row = lovable_sync.set_mode(options["mode"].upper(), reason=reason)
            if options["observe"]:
                row = lovable_observation.set_observe(options["observe"] == "on", reason=reason)
        else:
            raise CommandError("control needs --mode, --observe or --cut-over.")
        self.stdout.write(f"mode={row.mode} cut_over={row.cut_over} observe={row.observe}")

    def _resolve(self, options: dict[str, Any]) -> None:
        if not (options["reference"] and options["field"] and options["note"].strip()):
            raise CommandError("resolve needs --reference, --field and --note.")
        lovable_sync.resolve_field(
            reference=options["reference"],
            field_name=options["field"],
            note=options["note"].strip(),
        )
        self.stdout.write(f"resolved {options['reference']} {options['field']}")

    def _enroll(self, options: dict[str, Any]) -> None:
        if not (options["cohort"] and options["package_sha256"] and options["expect"]):
            raise CommandError("enroll needs --cohort, --package-sha256, --expect and --reason.")
        if not options["reason"].strip():
            raise CommandError("enroll needs --reason.")
        done = lovable_sync.enroll(
            cohort=options["cohort"],
            package_sha256=options["package_sha256"],
            expect=options["expect"],
            reason=options["reason"],
            supersede=options["supersede"],
            replaces=[key for value in options["replaces"] for key in value.split(",")],
        )
        if done.already:
            self.stdout.write(
                f"already enrolled: cohort={done.cohort} expected={done.expected}"
                f" states={done.seeded}; nothing changed"
            )
            return
        self.stdout.write(
            f"enrolled: cohort={done.cohort} expected={done.expected} seeded={done.seeded}"
            f" import_held_fields={done.import_held_fields}"
            + (f" reseeded={done.reseeded} superseded={done.superseded}" if done.superseded else "")
        )

    def _retire(self, options: dict[str, Any]) -> None:
        from app.legacy_import import lovable_sync_retirement

        if not (options["cohort"] and options["reference"] and options["reason"].strip()):
            raise CommandError("retire needs --cohort, --reference and --reason.")
        plan = lovable_sync_retirement.retire(
            cohort=options["cohort"],
            reference=options["reference"],
            reason=options["reason"],
            apply=options["apply"],
        )
        verb = "retired" if plan.applied else "would retire"
        self.stdout.write(
            f"{verb}: cohort={plan.cohort} reference={plan.reference}"
            f" deleted_at={plan.deleted_at.isoformat()} deletion_event={plan.deletion_event_id}"
            f" held={plan.held} required={plan.required}->{plan.required - 1}"
        )
        if not plan.applied:
            self.stdout.write("dry run: nothing recorded; add --apply to record it.")

    # -- helpers ---------------------------------------------------------------

    def _credentials(self, options: dict[str, Any]) -> str | None:
        return str(options["credentials"]) or None

    def _emit(self, options: dict[str, Any], data: dict[str, Any]) -> None:
        self.stdout.write(json.dumps(data, indent=None if options["json"] else 2, default=str))


#: A string `report` prints: a code (``COMPARE``, ``HTTP_401``) or an ISO moment.
_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
#: A machine name (an indicator key, a field name): lower-case words.
_CODE_OR_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
_DROPPED = object()
_MOMENT = re.compile(r"\d{4}-\d{2}-\d{2}(T[0-9:.]+([+-]\d{2}:\d{2}|Z)?)?")


def codes_only(value: Any) -> Any:
    """``value`` with only numbers, booleans, None, codes and ISO times kept.

    A list of mappings with a ``key`` (indicators, coverage) or a ``field``
    (per-field readiness) becomes a mapping by that name; any other list
    becomes its length. Every other string — an Estonian sentence, a title, a
    reference, a value — is dropped, so nothing here can carry business text."""
    if isinstance(value, dict):
        kept = {str(k): codes_only(v) for k, v in value.items()}
        return {k: v for k, v in kept.items() if v is not _DROPPED}
    if isinstance(value, list):
        named = [item for item in value if isinstance(item, dict)]
        for name in ("key", "field"):
            if (
                value
                and len(named) == len(value)
                and all(
                    isinstance(item.get(name), str) and _CODE_OR_NAME.fullmatch(item[name])
                    for item in named
                )
            ):
                return {
                    item[name]: codes_only({k: v for k, v in item.items() if k != name})
                    for item in named
                }
        return len(value)
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str) and (_CODE.fullmatch(value) or _MOMENT.fullmatch(value)):
        return value
    return _DROPPED


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
