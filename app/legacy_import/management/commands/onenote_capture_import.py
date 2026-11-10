"""The OneNote capture import (docs/adr/0205) and its coverage (live-sync round 2026-11).

    python manage.py onenote_capture_import validate --capture C
    python manage.py onenote_capture_import plan     --capture C [--out FILE]
    python manage.py onenote_capture_import apply    --capture C \\
        --expect-manifest-sha256 M --expect-plan-sha256 P \\
        --operator-intent onenote-capture-import --backup-set SET --label L \\
        (--matter UUID ... | --pages N) [--include-shared]
    python manage.py onenote_capture_import verify   --capture C [--bytes] [--out FILE]
    python manage.py onenote_capture_import digest   --capture C --out FILE
    python manage.py onenote_capture_import check    --capture C [--record] [--out FILE]
    python manage.py onenote_capture_import section-plan --capture C --section NAME [--out FILE]
    python manage.py onenote_capture_import coverage [--out FILE]
    python manage.py onenote_capture_import worklist [--since ISO] [--out FILE]
    python manage.py onenote_capture_import mark --page GUID ... --state STATE --reason TEXT

`validate` re-hashes every sealed file and touches no database. `plan` decides
every (Teema, file occurrence) and prints the plan digest, which depends on the
decisions only, never on how far an apply has got — no writes. `apply` writes
the chosen pages, one transaction per page, only with
``JURISTID_ONENOTE_CAPTURE_IMPORT`` and ``REAL_DATA_ALLOWED`` set, the typed
intent, the sealed manifest's digest, the reviewed plan digest and a named
backup set; a finished page is skipped, so a second run writes nothing.
`verify` reads back every row and reconciles it with the seal (``--bytes``
re-hashes every stored version). `digest` writes, per Teema, a digest of the
Lovable-derived business rows the import must leave untouched.

`check` is ``validate`` + ``plan`` + ``verify`` in one read (the request API's
``verify_capture``); ``--record`` appends the per-page progress marks and
nothing else. `section-plan` inventories one section's pages of a capture and
matches them only by exact identity — no writes, no Teema, no register number.
`coverage` and `worklist` read the operational OneNote coverage and the pages to
capture next (``worklist --out`` is the capture-request file the desktop
capture reads); `mark` appends a progress mark (for example a page that could
not be opened: ``--state FAILED``).

Stdout carries counts only. The capture holds the Chamber's and its members'
material: keep it, and every ``--out`` file, outside the repository and CI.
"""

from __future__ import annotations

import json
import signal
from datetime import datetime
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.legacy_import import onenote_capture

CAPTURE_MODES = {"validate", "plan", "apply", "verify", "digest", "check", "section-plan"}


class Command(BaseCommand):
    help = "Validate, plan, apply or verify a sealed OneNote desktop capture (docs/adr/0205)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "mode",
            choices=[
                "validate",
                "plan",
                "apply",
                "verify",
                "digest",
                "check",
                "section-plan",
                "coverage",
                "worklist",
                "mark",
            ],
        )
        parser.add_argument("--capture", default="", help="The sealed capture directory.")
        parser.add_argument("--expect-manifest-sha256", default="")
        parser.add_argument("--expect-plan-sha256", default="")
        parser.add_argument("--operator-intent", default="")
        parser.add_argument("--backup-set", default="")
        parser.add_argument("--label", default="", help="apply: a name for this batch.")
        parser.add_argument("--matter", action="append", default=[], help="apply: only this Teema.")
        parser.add_argument("--pages", type=int, default=0, help="apply: the next N pending pages.")
        parser.add_argument(
            "--include-shared",
            action="store_true",
            help="apply: shared pages (catalogued, every file held for review).",
        )
        parser.add_argument("--bytes", action="store_true", help="verify: re-hash stored files.")
        parser.add_argument(
            "--require-complete",
            action="store_true",
            help="verify: an item or page link not yet applied is a problem.",
        )
        parser.add_argument("--section", default="", help="section-plan: the section's name.")
        parser.add_argument(
            "--record", action="store_true", help="check/section-plan: append progress marks."
        )
        parser.add_argument("--since", default="", help="worklist: changes after this ISO time.")
        parser.add_argument("--page", action="append", default=[], help="mark: a page GUID.")
        parser.add_argument("--state", default="", help="mark: the PageState to record.")
        parser.add_argument("--reason", default="", help="mark: why.")
        parser.add_argument("--out", default="", help="Write the full JSON report here.")

    def handle(self, *args: Any, **options: Any) -> None:
        mode = options["mode"]
        try:
            if mode in CAPTURE_MODES:
                if not options["capture"]:
                    raise CommandError(f"{mode}: give --capture.")
                self._capture_mode(mode, options)
            else:
                self._coverage_mode(mode, options)
        except onenote_capture.CaptureError as error:
            raise CommandError(str(error)) from error

    def _capture_mode(self, mode: str, options: dict[str, Any]) -> None:
        if mode == "check":
            check = onenote_capture.verify_capture(options["capture"], record=options["record"])
            self._emit(
                check.as_dict(),
                options,
                quiet_keys={"problems", "page_states", "readback", "path"},
            )
            if not check.ok:
                raise CommandError("check found problems; see --out")
            return
        capture = onenote_capture.load_capture(
            Path(options["capture"]), verify_all_bytes=mode == "validate"
        )
        if mode == "validate":
            if options["record"]:
                from app.legacy_import.onenote_coverage import record_capture_marks

                record_capture_marks(
                    capture,
                    {p.page_key: "CAPTURE_PREPARED" for p in capture.pages},
                    reason="validate: the seal holds",
                )
            self._emit(
                {
                    "capture_id": capture.capture_id,
                    "manifest_sha256": capture.manifest_sha256,
                    "counts": capture.counts,
                },
                options,
            )
            return
        if mode == "section-plan":
            if not options["section"]:
                raise CommandError("section-plan: name the section with --section.")
            result = onenote_capture.section_plan(capture, options["section"])
            if options["record"]:
                from app.legacy_import.onenote_coverage import mark_section

                mark_section(
                    options["section"],
                    "VERIFIED" if not result["plan_problems"] else "FAILED",
                    reason=(
                        f"section-plan: {result['pages']} pages, "
                        f"{len(result['exceptions'])} exceptions"
                    ),
                    capture_id=capture.capture_id,
                    manifest_sha256=capture.manifest_sha256,
                )
            self._emit(result, options, quiet_keys={"rows", "exceptions", "section"})
            self.stdout.write(f"exceptions {len(result['exceptions'])}")
            return
        if mode == "digest":
            ids = sorted({pm.matter_id for p in capture.pages for pm in p.matters})
            self._emit(
                {"teemad": len(ids), "digest": onenote_capture.business_digest(ids)},
                options,
                quiet_keys={"digest"},
            )
            return
        if mode == "verify":
            result = onenote_capture.verify(
                capture,
                check_bytes=options["bytes"],
                require_complete=options["require_complete"],
            )
            self._emit(result, options, quiet_keys={"problems"})
            self.stdout.write(f"problems {len(result['problems'])}")
            if result["problems"]:
                raise CommandError("verify found problems; see --out")
            return
        plan = onenote_capture.build_plan(capture)
        if mode == "plan":
            full = plan.summary()
            full["problem_list"] = plan.problems
            full["items"] = [item.__dict__ for item in plan.items]
            full["known_pages"] = plan.known_pages
            full["earlier_resources"] = plan.earlier_resources
            self._emit(
                full,
                options,
                quiet_keys={"problem_list", "items", "known_pages", "earlier_resources"},
            )
            return
        if bool(options["matter"]) == bool(options["pages"]):
            raise CommandError("apply: give either --matter (one or more) or --pages N.")
        if not options["label"].strip():
            raise CommandError("apply: name the batch with --label.")
        pages = onenote_capture.select_pages(
            plan,
            matters=options["matter"] or None,
            limit=options["pages"] or None,
            include_shared=options["include_shared"],
        )

        # `docker stop` sends SIGTERM: end the run through the same path as Ctrl-C,
        # so the batch is recorded FAILED rather than left RUNNING.
        def _terminate(signum: int, frame: Any) -> None:
            raise SystemExit(128 + signum)

        signal.signal(signal.SIGTERM, _terminate)
        report = onenote_capture.apply(
            plan,
            pages,
            operator_intent=options["operator_intent"],
            expect_manifest_sha256=options["expect_manifest_sha256"],
            expect_plan_sha256=options["expect_plan_sha256"],
            backup_set=options["backup_set"],
            label=options["label"].strip(),
        )
        out = report.as_dict()
        out["pages_selected"] = len(pages)
        self._emit(out, options, quiet_keys={"matters", "page_outcomes"})

    def _coverage_mode(self, mode: str, options: dict[str, Any]) -> None:
        from app.legacy_import import onenote_coverage

        if mode == "coverage":
            summary = onenote_coverage.coverage_summary()
            self._emit(summary, options, quiet_keys={"sections"})
            return
        if mode == "worklist":
            since = datetime.fromisoformat(options["since"]) if options["since"] else None
            if since is not None and since.tzinfo is None:
                raise CommandError("worklist: --since needs a time zone (e.g. 2026-11-04T00:00Z).")
            items = onenote_coverage.worklist(since=since)
            request = onenote_coverage.capture_request(items)
            if options["out"]:
                Path(options["out"]).write_text(
                    json.dumps(request, ensure_ascii=False, indent=1, sort_keys=True, default=str),
                    encoding="utf-8",
                )
            reasons: dict[str, int] = {}
            for item in items:
                for reason in item.reasons:
                    reasons[reason] = reasons.get(reason, 0) + 1
            self.stdout.write(
                json.dumps(
                    {
                        "pages": len(items),
                        "by_reason": reasons,
                        "register_pages": len(request["register_map"]),
                    },
                    sort_keys=True,
                )
            )
            return
        # mark
        if not options["page"] or not options["state"] or not options["reason"].strip():
            raise CommandError("mark: give --page (one or more), --state and --reason.")
        try:
            written = onenote_coverage.mark_pages(
                options["page"], options["state"], reason=options["reason"].strip()
            )
        except ValueError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps({"marks": written}))

    def _emit(
        self,
        data: dict[str, Any],
        options: dict[str, Any],
        *,
        quiet_keys: frozenset[str] | set[str] = frozenset(),
    ) -> None:
        if options["out"]:
            Path(options["out"]).write_text(
                json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True, default=str),
                encoding="utf-8",
            )
        shown = {k: v for k, v in data.items() if k not in quiet_keys}
        self.stdout.write(json.dumps(shown, ensure_ascii=False, sort_keys=True, default=str))
