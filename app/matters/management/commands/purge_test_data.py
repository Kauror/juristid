"""Inventory what a purge of development data would have to touch.

``--plan`` is the only mode, and it is required rather than implied. A command
called ``purge_test_data`` that did something when run with no arguments is a
command somebody will one day run with no arguments; making the mode explicit
means the reader of a shell history can see which one happened.

**There is deliberately no ``--apply``.** The plan this command produces is the
input to a decision that has not been made yet: whether the append-only audit
history of a test Matter may be physically removed under a maintenance
protocol, kept as a detached tombstone, or handled by throwing the whole
development database away instead. That decision changes an architecture
guarantee — ``ChangeEvent`` is append-only in the database, not by convention —
and it does not belong inside a utility command (Agent-C brief 41).

The output is aggregate. No Matter title, no document filename and no storage
key reaches an operator log or a CI transcript from here: an inventory of
development data is still an inventory of the same database real work lives in,
and counts are what the decision needs (brief 43).
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from app.matters.purge import build_purge_plan

SAFE = "SAFE CANDIDATE SET"
BLOCKED = "BLOCKED"


class Command(BaseCommand):
    help = (
        "Report, without writing anything, which rows and evidence objects a future "
        "purge of TEST-classified Matters would have to account for."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--plan",
            action="store_true",
            required=True,
            help="Report only. The only supported mode; no destructive mode exists.",
        )
        parser.add_argument(
            "--matter",
            action="append",
            default=[],
            metavar="UUID|YYYY_N",
            help=(
                "Narrow the plan to one test matter. Repeatable. "
                "Without it the plan covers every TEST matter."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            plan = build_purge_plan(options["matter"])
        except ValueError as error:
            raise CommandError(str(error)) from error

        self.stdout.write(f"test-matters\t{plan.test_matters}")

        if not plan.test_matters:
            self.stdout.write(
                self.style.SUCCESS("No TEST matters. Nothing to plan, and nothing was written.")
            )
            return

        self.stdout.write("")
        self.stdout.write("rows a purge would have to account for")
        for group in plan.owned:
            marker = "\tappend-only" if group.append_only else ""
            self.stdout.write(f"row\t{group.label}\t{group.count}\t{group.behaviour}{marker}")
        self.stdout.write(f"rows-total\t{plan.total_owned_rows}")

        self.stdout.write("")
        self.stdout.write("canonical evidence held by those rows")
        if plan.evidence:
            for summary in plan.evidence:
                self.stdout.write(
                    f"evidence\t{summary.label}\t{summary.objects}\t"
                    f"keys={summary.distinct_keys}\tbytes={summary.total_bytes}"
                )
        else:
            self.stdout.write("evidence\t(none)")
        # Named even at zero. The archive's binaries are the one piece of
        # evidence a reader might reasonably expect to see counted here, and
        # silence would leave them wondering whether it was checked (brief 35).
        self.stdout.write(f"derivative-objects\t{plan.derivative_objects}")
        for label in plan.unreachable_by_design:
            self.stdout.write(f"not-test-owned\t{label}\t0")

        self.stdout.write("")
        self._report_append_only(plan)
        self._report_blockers(plan)

        self.stdout.write("")
        self.stdout.write("writes\t0 rows\t0 filesystem objects")
        if plan.is_blocked:
            self.stdout.write(self.style.ERROR(f"result\t{BLOCKED}"))
            return
        self.stdout.write(self.style.SUCCESS(f"result\t{SAFE}"))
        # Said plainly, because "safe candidate set" is exactly the phrase
        # somebody will quote back as authorisation to delete.
        self.stdout.write(
            "A safe candidate set means no legal hold, no invalid classification and "
            "no real record depending on a test-owned row were found. It is not a "
            "deletion, and no deletion is implemented."
        )

    def _report_append_only(self, plan: Any) -> None:
        rows = plan.append_only_rows
        if not rows:
            self.stdout.write("deletion-dependency\tappend-only\t0")
            return
        for group in rows:
            self.stdout.write(f"deletion-dependency\tappend-only\t{group.label}\t{group.count}")
        self.stdout.write(
            self.style.WARNING(
                "Append-only history is a deletion dependency, not a defect. The database "
                "refuses to delete these rows, and whether a test matter's audit trail may "
                "be removed at all is an open architecture decision."
            )
        )

    def _report_blockers(self, plan: Any) -> None:
        if not plan.blockers:
            self.stdout.write("blocker\t(none)")
            return
        for blocker in plan.blockers:
            self.stdout.write(
                f"blocker\t{blocker.category}\t{blocker.label}\t{blocker.count}\t{blocker.detail}"
            )
