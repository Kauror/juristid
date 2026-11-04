"""The OneNote capture import (docs/adr/0205).

    python manage.py onenote_capture_import validate --capture C
    python manage.py onenote_capture_import plan     --capture C [--out FILE]
    python manage.py onenote_capture_import apply    --capture C \\
        --expect-manifest-sha256 M --expect-plan-sha256 P \\
        --operator-intent onenote-capture-import --backup-set SET --label L \\
        (--matter UUID ... | --pages N) [--include-shared]
    python manage.py onenote_capture_import verify   --capture C [--bytes] [--out FILE]
    python manage.py onenote_capture_import digest   --capture C --out FILE

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

Stdout carries counts only. The capture holds the Chamber's and its members'
material: keep it, and every ``--out`` file, outside the repository and CI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.legacy_import import onenote_capture


class Command(BaseCommand):
    help = "Validate, plan, apply or verify a sealed OneNote desktop capture (docs/adr/0205)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("mode", choices=["validate", "plan", "apply", "verify", "digest"])
        parser.add_argument("--capture", required=True, help="The sealed capture directory.")
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
        parser.add_argument("--out", default="", help="Write the full JSON report here.")

    def handle(self, *args: Any, **options: Any) -> None:
        mode = options["mode"]
        try:
            capture = onenote_capture.load_capture(
                Path(options["capture"]), verify_all_bytes=mode == "validate"
            )
            if mode == "validate":
                self._emit(
                    {
                        "capture_id": capture.capture_id,
                        "manifest_sha256": capture.manifest_sha256,
                        "counts": capture.counts,
                    },
                    options,
                )
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
                result = onenote_capture.verify(capture, check_bytes=options["bytes"])
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
            self._emit(out, options, quiet_keys={"matters"})
        except onenote_capture.CaptureError as error:
            raise CommandError(str(error)) from error

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
