"""The host runner for Haldus → Andmete sünkroonimine's requests (LS-C).

    python manage.py data_sync_requests run-next --kind lovable --credentials F [--backup-set S]
    python manage.py data_sync_requests run-next --kind onenote [--capture-root DIR]
    python manage.py data_sync_requests list [--kind lovable|onenote] [--limit N]
    python manage.py data_sync_requests poll --kind lovable|onenote [--problem CODE]

`poll` is the host script's on-demand question (LS2-D): it records the
runner's heartbeat (`DataSyncRunnerBeat`, so the page can say whether a server
runner exists at all) and prints one line — ``DUE <mode>`` / ``DUE ONENOTE``
when a request waits, ``NOT_DUE <reason>`` when none does, or
``REFUSED <problem>`` when the script named a host-side problem
(``HOST_PAUSED``, ``NO_CREDENTIALS``, ``CREDENTIALS_MODE``,
``NO_CAPTURE_SOURCE``) and a waiting request was therefore refused by name. It
needs no credentials and never reaches Lovable or a capture.

`run-next` claims the oldest PENDING request of the lane under
``select_for_update(skip_locked=True)``, marks it RUNNING, and runs it with the
engine the nightly job runs:

* **lovable** — `lovable_sync.run_sync(trigger=MANUAL)` with a fresh read of
  Lovable through `lovable_source.HttpTransport` (GET only), obeying the
  control row exactly as `lovable_sync run --trigger manual` does: OFF refuses,
  COMPARE writes no business data, APPLY writes the allowlisted changes only
  with a verified backup set. Needs ``JURISTID_LOVABLE_SYNC`` and
  ``REAL_DATA_ALLOWED``, like `lovable_sync run`; without them the waiting
  request is refused by name (``NOT_PERMITTED``, LS2-D) — never left to expire —
  and the command exits 1;
* **onenote** — the named capture under the configured source root
  (``ONENOTE_CAPTURE_ROOT`` / ``JURISTID_ONENOTE_CAPTURE_ROOT``, or
  ``--capture-root``): `onenote_capture.verify_capture` for a check, and for
  an import the same check again and then `onenote_capture.apply_capture`
  bound to the verified plan's sha256 — refused if the plan changed.

It records the request's real result (and the run it made) and one audit row.
Exit status: 0 when nothing was pending or the request finished, 1 when it
failed or was refused. `list` prints the newest requests as codes and counts.
Nothing here is reachable from the web.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.legacy_import import lovable_sync, sync_admin
from app.legacy_import.sync_requests_models import DataSyncRequest, SyncRequestLane


def lovable_transport(options: dict[str, Any]) -> Callable[[], Any]:
    """The source connection for a LOVABLE_SYNC request: built only when one is claimed."""
    from app.legacy_import import lovable_source

    def build() -> Any:
        config = lovable_source.load_config(str(options.get("credentials") or "") or None)
        return lovable_source.HttpTransport(config)

    return build


class Command(BaseCommand):
    help = "Run the next Andmete sünkroonimine request on the host, or list them."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("action", choices=["run-next", "list", "poll"])
        parser.add_argument("--kind", choices=SyncRequestLane.values, default="")
        parser.add_argument("--credentials", default="")
        parser.add_argument("--backup-set", default="")
        parser.add_argument("--capture-root", default="")
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument(
            "--problem",
            default="",
            choices=["", *sync_admin.RUNNER_PROBLEMS],
            help="poll: a host-side reason this lane cannot run now.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["action"] == "list":
            self._list(options)
            return
        lane = options["kind"]
        if not lane:
            raise CommandError(f"{options['action']} needs --kind lovable or --kind onenote.")
        if options["action"] == "poll":
            self.stdout.write(sync_admin.poll(lane, problem=options["problem"]))
            return
        transport = None
        if lane == SyncRequestLane.LOVABLE:
            if not (settings.LOVABLE_SYNC_ENABLED and settings.REAL_DATA_ALLOWED):
                refused = sync_admin.refuse_unpermitted(lane)
                raise CommandError(
                    "run-next --kind lovable needs JURISTID_LOVABLE_SYNC and "
                    "REAL_DATA_ALLOWED on this container; "
                    + (
                        f"the waiting request {refused.pk} was refused (NOT_PERMITTED)."
                        if refused is not None
                        else "nothing was waiting."
                    )
                )
            transport = lovable_transport(options)
        outcome = sync_admin.run_next(
            lane,
            transport_factory=transport,
            # The host script hands the set it took and verified for this run in
            # the environment (`lovable_sync.host_backup_set`); an explicit
            # --backup-set wins. `run_sync` accepts only a fresh set's name.
            backup_set=options["backup_set"] or lovable_sync.host_backup_set(),
            capture_root_override=options["capture_root"] or None,
        )
        item = outcome.request
        self.stdout.write(
            json.dumps(
                {
                    "lane": lane,
                    "request": str(item.pk) if item else None,
                    "kind": item.kind if item else None,
                    "status": item.status if item else sync_admin.NOTHING,
                    "code": item.outcome_code if item else "",
                    "run": str(item.sync_run_id) if item and item.sync_run_id else None,
                },
                default=str,
            )
        )
        if outcome.failed:
            sys.exit(1)

    def _list(self, options: dict[str, Any]) -> None:
        rows = DataSyncRequest.objects.order_by("-requested_at")
        if options["kind"]:
            rows = rows.filter(lane=options["kind"])
        for item in rows[: max(1, options["limit"])]:
            self.stdout.write(
                json.dumps(
                    {
                        "request": str(item.pk),
                        "kind": item.kind,
                        "status": item.status,
                        "code": item.outcome_code,
                        "requested_at": item.requested_at.isoformat(),
                        "finished_at": item.finished_at.isoformat() if item.finished_at else None,
                        "run": str(item.sync_run_id) if item.sync_run_id else None,
                        "counts": sync_admin.int_counts((item.result or {}).get("counts")),
                    }
                )
            )
