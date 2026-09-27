"""Report where the stored register state differs from what the code would derive.

    manage.py check_current_register_state
    manage.py check_current_register_state --snapshot <sha256> --sample 0

Read-only, always: it builds the projection `final_register_cutover --apply`
would write, in memory, and compares it with the table
(app/legacy_import/current_state_verify.py, ENG-144). A difference is the answer
to "is a rerun owed?" — after a release that changes how the register is read,
or after a person's name became resolvable — and the rerun stays the
operator's, because it moves what the product shows.

Exit 0 when the table matches, 1 when it does not or when the stored rows do not
name one snapshot. The output is counts, register references and column names;
never a cell's value.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from app.legacy_import.current_state_verify import (
    CHANGED,
    EXTRA,
    MISSING,
    SnapshotNotSettled,
    verify_current_state,
)

DEFAULT_SAMPLE = 20

EXPLANATIONS = {
    MISSING: "the projection has a row the table does not",
    EXTRA: "the table has a row the projection does not",
    CHANGED: "both have a row and these columns differ",
}


class Command(BaseCommand):
    help = (
        "Compare CurrentRegisterState with a fresh projection of its approved snapshot. Read-only."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--snapshot",
            default=None,
            help="The approved snapshot's SHA-256 (default: the one the stored rows name).",
        )
        parser.add_argument(
            "--sample",
            type=int,
            default=DEFAULT_SAMPLE,
            help=f"Matters printed per kind (default {DEFAULT_SAMPLE}, 0 for all).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            verification = verify_current_state(snapshot_sha256=options["snapshot"])
        except SnapshotNotSettled as error:
            self.stdout.write(self.style.ERROR(str(error)))
            raise SystemExit(1) from None

        self.stdout.write(f"Snapshot:        {verification.snapshot_sha256 or '(none stored)'}")
        self.stdout.write(f"Stored rows:     {verification.stored}")
        self.stdout.write(f"Projected rows:  {verification.projected}")

        if verification.ok:
            self.stdout.write(
                self.style.SUCCESS("Stored register state matches the current projection.")
            )
            return

        sample = options["sample"]
        for kind in (MISSING, EXTRA, CHANGED):
            rows = [d for d in verification.differences if d.kind == kind]
            if not rows:
                continue
            self.stdout.write(self.style.ERROR(f"\n{kind}: {len(rows)} — {EXPLANATIONS[kind]}"))
            shown = rows if sample <= 0 else rows[:sample]
            for difference in shown:
                columns = f"\t{', '.join(difference.fields)}" if difference.fields else ""
                self.stdout.write(f"  {difference.reference}\t{difference.matter_id}{columns}")
            if len(rows) > len(shown):
                self.stdout.write(f"  … and {len(rows) - len(shown)} more")
        raise SystemExit(1)
