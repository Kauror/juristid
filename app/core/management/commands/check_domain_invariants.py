"""Would the pending constraint migration fail, and what else is already wrong?

**Run it before migrating** a release that carries `workflow.0009`
(deploy/unraid-main/README.md, step 7): it reads the still-unmigrated database
through the target release's own rules and says which rows the new `CHECK`s
would refuse — before `migrate` finds out by failing half way through a
deployment (ENG-043).

    manage.py check_domain_invariants
    manage.py check_domain_invariants --sample 0    # every identifier

Read-only, always. Nothing here repairs a row: a `NextAction` with a precision
nobody can render, a reply-by date before its round and a send dated tomorrow
are each a question about what the record was meant to say, and that is answered
with the register in front of somebody, not by a deployment step
(`app.core.invariants`).

Exit 0 when nothing was found, 1 when something was. The two kinds of finding
are counted apart because only one of them stops the migration. The output is
counts, primary keys and the offending values — never a title, a summary or a
name.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from app.core.invariants import (
    BLOCKING,
    EXPLANATIONS,
    NEXT_ACTION_CONSTRAINT_MIGRATION,
    check_domain_invariants,
)

#: Identifiers printed per finding kind before the rest are counted instead —
#: the sibling `check_evidence_integrity`'s default and reasoning.
DEFAULT_SAMPLE = 20


class Command(BaseCommand):
    help = (
        "Report rows that break a domain invariant the services enforce or a pending "
        "constraint installs. Read-only; run before migrating."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--sample",
            type=int,
            default=DEFAULT_SAMPLE,
            help=f"Identifiers printed per finding kind (default {DEFAULT_SAMPLE}, 0 for all).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        report = check_domain_invariants()
        self.stdout.write(f"Business date (Europe/Tallinn): {report.today.isoformat()}")

        if report.ok:
            self.stdout.write(self.style.SUCCESS("No invariant violations found."))
            return

        sample = options["sample"]
        grouped = report.by_kind()
        for kind in sorted(grouped):
            findings = grouped[kind]
            style = self.style.ERROR if kind in BLOCKING else self.style.WARNING
            self.stdout.write(style(f"\n{kind}: {len(findings)} — {EXPLANATIONS[kind]}"))
            shown = findings if sample <= 0 else findings[:sample]
            for finding in shown:
                self.stdout.write(f"  {finding.subject}\t{finding.detail}")
            if len(findings) > len(shown):
                self.stdout.write(f"  … and {len(findings) - len(shown)} more")

        blocking = len(report.blocking)
        others = len(report.findings) - blocking
        if blocking:
            self.stdout.write(
                self.style.ERROR(
                    f"\n{blocking} row(s) would make {NEXT_ACTION_CONSTRAINT_MIGRATION} fail. "
                    "Do not migrate. Do not repair these rows automatically: which value a "
                    "step was meant to carry is a decision for somebody who knows the work."
                )
            )
        if others:
            self.stdout.write(
                self.style.WARNING(
                    f"\n{others} row(s) break a rule the services now refuse to write. These "
                    f"do not block {NEXT_ACTION_CONSTRAINT_MIGRATION}; record them for review. "
                    "Imported archive rows are reported, never rewritten."
                )
            )
        raise SystemExit(1)
