"""One compact answer to "does the running application look internally healthy?"

A convenience roll-up over read-only checks that already exist, and deliberately
nothing more. Each row here is another command's verdict, reached by calling
that command's own report function rather than by running the command and
reading its prose — so a wording change stays a wording change, and this file
never becomes an interface nobody knew they were maintaining.

**It is not a new authority.** The individual commands remain the ones a runbook
cites, and each of them says more than a single PASS ever can. This exists
because the deployment procedure asks an operator to run five of them one after
another, and five exit codes read one at a time is where one gets skipped.

**Three things it deliberately does not answer.**

*What the next image would do to this database.* That is `migration_plan`,
against the target image, and a command running inside the current one cannot
answer it: it can only see the migrations it was built with. The readiness row
below says whether *this* build's schema is applied, which is a different
question with a similar shape, and it is not permission to skip the target-image
preflight (docs/adr/0022).

*Whether the container is alive and serving.* That is `/healthz`, the image
HEALTHCHECK and whatever is watching the containers. Nothing here makes an HTTP
request, and nothing here looks at Docker: the runtime layer is the runbook's,
and a Django command asking its own process whether it is up would be answering
with the fact that it ran.

*Whether the evidence bytes are still what was hashed.* `check_evidence_integrity`
answers that, and it stays out of this roll-up entirely — see
:data:`EXCLUDED` for why the cheap half is still too expensive to be routine.

**It repairs nothing.** No migration, no rebuild, no refresh, no retry, no
reconciliation, and in particular it never consumes the search-rebuild debt it
reports: `run_search_refresh_worker` is the only thing that pays that off, and a
status command that quietly drained the queue it was asked to describe would
make the next run's clean result meaningless. Every function called from here is
one its own module already documents as read-only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.db import DatabaseError

#: Checks that belong to a routine status and are deliberately left out, with
#: the reason. Kept as data rather than prose because the omissions are the part
#: an operator has to be able to check we thought about.
EXCLUDED: tuple[tuple[str, str], ...] = (
    (
        "check_evidence_integrity",
        "Even its structural pass probes the filesystem once per DocumentVersion "
        "and lists the store to find orphans — tens of thousands of operations "
        "against a network mount. The deep --verify-sha pass reads every stored "
        "byte and is a maintenance window. Neither is routine; both stay their "
        "own command.",
    ),
    (
        "migration_plan",
        "Answers what the *target* image would do to this database. A command "
        "running inside the current image cannot see the next image's "
        "migrations, so this roll-up must not be read as covering it.",
    ),
    (
        "/healthz and container state",
        "Liveness and the runtime layer. External to the application, and the runbook's to check.",
    ),
)


@dataclass(frozen=True)
class CheckResult:
    """One row: what was asked, whether it passed, and a short aggregate why.

    ``detail`` is counts and classes of problem only — never a document title, a
    filename, an archive path, a SHA or anything about a person. Every check
    called from here already reports in aggregate for that reason, and this adds
    nothing to what they return.
    """

    key: str
    label: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class StatusReport:
    checks: tuple[CheckResult, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)


def _deployment_readiness() -> CheckResult:
    from app.core import deployment

    label = "Deployment readiness"
    try:
        report = deployment.readiness_report()
    except DatabaseError as error:
        # The one failure that cannot be a finding, because every other finding
        # needs the database to have answered. Reported as this check's failure
        # rather than raised, so the remaining rows still print.
        return CheckResult(
            "deployment", label, False, f"database unreachable ({type(error).__name__})"
        )
    if report.ok:
        return CheckResult("deployment", label, True)
    return CheckResult("deployment", label, False, _count(len(report.problems), "problem"))


def _search_integrity() -> CheckResult:
    from app.search.management.commands.check_search_integrity import build_report

    report = build_report()
    label = "Search integrity"
    if report.ok:
        return CheckResult("search_integrity", label, True)
    return CheckResult("search_integrity", label, False, _count(len(report.findings), "finding"))


def _search_freshness() -> CheckResult:
    """The same verdict `check_search_freshness` reaches, and for the same reasons.

    Debt that is merely *pending* is not a finding. A rename recorded four
    seconds ago is the system working, and a status command that called it a
    fault would train an operator to ignore the row. It becomes a finding when a
    rebuild has already failed — waiting will not fix that — or when the debt has
    outlived ``SEARCH_REBUILD_DEBT_STALE_SECONDS``, which means nothing is
    converging it.

    Reading the debt is all this does. `run_search_refresh_worker` is the only
    thing that consumes it.
    """
    from django.conf import settings

    from app.search.freshness import status

    state = status()
    label = "Search freshness"
    if state.is_clear:
        return CheckResult("search_freshness", label, True)

    owed = _count(state.owed, "rebuild")
    if state.failed_attempts:
        return CheckResult(
            "search_freshness",
            label,
            False,
            f"{owed} owed, {state.failed_attempts} failed attempt(s)",
        )

    age = int(state.seconds_owed())
    limit = settings.SEARCH_REBUILD_DEBT_STALE_SECONDS
    if age >= limit:
        return CheckResult(
            "search_freshness", label, False, f"{owed} owed for {age}s (max {limit}s)"
        )
    return CheckResult("search_freshness", label, True, f"{owed} owed, pending")


def _era_contracts() -> CheckResult:
    from app.legacy_import.contracts import ContractError, load_contracts

    label = "Era contracts"
    try:
        contracts = load_contracts()
    except ContractError as error:
        return CheckResult("era_contracts", label, False, str(error))
    return CheckResult("era_contracts", label, True, _count(len(contracts), "contract"))


def _archive_projection() -> CheckResult:
    from app.legacy_import.opinion_search import archive_index_findings

    findings = archive_index_findings()
    label = "Archive projection"
    if not findings:
        return CheckResult("archive_projection", label, True)
    return CheckResult("archive_projection", label, False, _count(len(findings), "finding"))


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


#: The checks, in the order they are printed. Order is part of the output
#: contract: an operator reading two runs side by side compares rows by
#: position, and a set would reorder between them.
CHECKS: tuple[Callable[[], CheckResult], ...] = (
    _deployment_readiness,
    _search_integrity,
    _search_freshness,
    _era_contracts,
    _archive_projection,
)


def production_status() -> StatusReport:
    """Run every included check and return all of their verdicts.

    Every check runs. Stopping at the first failure would report one problem on
    a system that has three, and the operator would then fix one and run it
    again — which is the loop this command exists to collapse.

    A check reports its own known failure modes as ``ok=False``. Anything else —
    an ``AttributeError``, a name that moved, a contract this file got wrong —
    propagates, because a bug in the roll-up must not be able to read as a
    healthy system. The one exception is stated where it is made: a database
    that cannot be reached at all is a finding rather than a traceback.
    """
    return StatusReport(checks=tuple(check() for check in CHECKS))


def render(report: StatusReport, *, width: int = 26) -> list[str]:
    """The report as deterministic lines. No clock, no ordering by outcome."""
    lines = [f"{check.label:<{width}}{'PASS' if check.ok else 'FAIL'}" for check in report.checks]
    lines.extend(["", f"{'Overall':<{width}}{'PASS' if report.ok else 'FAIL'}"])
    return lines


def as_dict(report: StatusReport) -> dict[str, Any]:
    """The same verdicts, structured. Used by ``--json``."""
    return {
        "ok": report.ok,
        "checks": [
            {"key": check.key, "label": check.label, "ok": check.ok, "detail": check.detail}
            for check in report.checks
        ],
    }
