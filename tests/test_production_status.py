"""The read-only roll-up, and the two things that would make it dangerous.

`production_status` is convenience tooling: it runs checks that already exist
and prints one row each. Two failure modes are worth a test suite of its own,
and neither is about the printing.

**It must never write.** A status command is run at three in the morning, on a
hunch, by somebody who is not sure what is wrong — which is exactly when a
"helpful" repair does the most damage, and exactly when nobody is watching
closely enough to notice one. The zero-write tests below fingerprint the
canonical tables and the queue tables around a successful *and* a failing run.
The failing run matters more than the successful one: a repair path that only
opens when something is wrong is invisible to a test that only runs when
everything is right.

**It must never report PASS for a question it did not answer.** A roll-up that
swallowed an exception would print five green rows on a system it never
managed to ask, which is worse than the five commands it replaced. A check's own
known failure is a FAIL row; anything else is a bug and comes out as one.
"""

from __future__ import annotations

import json

import pytest
from django.core.management import call_command

from app.core import production_status as status_module

pytestmark = pytest.mark.django_db


def _stub(monkeypatch, *results: status_module.CheckResult) -> None:
    """Replace the check list with ones whose outcome the test decides.

    The real checks are each their own command's tested logic, and re-asserting
    it here would only prove that two files agree about a mock. What is under
    test is the aggregation: the exit code, the ordering, and whether every
    check runs.
    """
    monkeypatch.setattr(
        status_module,
        "CHECKS",
        tuple((lambda captured=result: captured) for result in results),
    )


def _fail(key: str, detail: str = "1 finding") -> status_module.CheckResult:
    return status_module.CheckResult(key, key.replace("_", " ").capitalize(), False, detail)


def _pass(key: str) -> status_module.CheckResult:
    return status_module.CheckResult(key, key.replace("_", " ").capitalize(), True)


# --------------------------------------------------------------------------
# The exit contract
# --------------------------------------------------------------------------


def test_every_check_passing_exits_zero(monkeypatch, capsys) -> None:
    _stub(monkeypatch, _pass("a"), _pass("b"))

    call_command("production_status")

    out = capsys.readouterr().out
    assert "Overall" in out
    assert "FAIL" not in out
    assert out.count("PASS") == 3, out


def test_one_failing_check_exits_non_zero(monkeypatch) -> None:
    _stub(monkeypatch, _pass("a"), _fail("b"), _pass("c"))

    with pytest.raises(SystemExit) as exit_status:
        call_command("production_status")

    assert exit_status.value.code == 1


def test_every_check_still_runs_after_one_fails(monkeypatch, capsys) -> None:
    """Not "stop at the first problem".

    An operator who fixes one finding and runs it again to discover the second
    is back in the loop this command exists to collapse, and a system with three
    problems reported as having one is a worse starting point than no summary.
    """
    ran: list[str] = []

    def record(key: str, ok: bool):
        def check() -> status_module.CheckResult:
            ran.append(key)
            return status_module.CheckResult(key, key, ok)

        return check

    monkeypatch.setattr(
        status_module,
        "CHECKS",
        (record("first", False), record("second", False), record("third", True)),
    )

    with pytest.raises(SystemExit):
        call_command("production_status")

    assert ran == ["first", "second", "third"]
    out = capsys.readouterr().out
    assert out.count("FAIL") == 3, "two failures and the overall verdict"


def test_the_rows_keep_their_order_between_runs(monkeypatch, capsys) -> None:
    """Deterministic output: an operator compares two runs by row position."""
    _stub(monkeypatch, _pass("a"), _fail("b"), _pass("c"))

    with pytest.raises(SystemExit):
        call_command("production_status")
    first = capsys.readouterr().out
    with pytest.raises(SystemExit):
        call_command("production_status")

    assert capsys.readouterr().out == first


def test_a_failure_is_visible_without_scrolling(monkeypatch, capsys) -> None:
    """One row per check, not the child command's whole output."""
    _stub(monkeypatch, _pass("a"), _fail("b"), _pass("c"))

    with pytest.raises(SystemExit):
        call_command("production_status")

    body = capsys.readouterr().out.strip().splitlines()
    assert len(body) <= 12, body


def test_json_carries_the_same_verdict(monkeypatch, capsys) -> None:
    _stub(monkeypatch, _pass("a"), _fail("b"))

    with pytest.raises(SystemExit):
        call_command("production_status", "--json")

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert [check["ok"] for check in payload["checks"]] == [True, False]


# --------------------------------------------------------------------------
# A bug is not a PASS
# --------------------------------------------------------------------------


def test_an_unexpected_exception_is_not_swallowed(monkeypatch) -> None:
    """A programming error surfaces as one, rather than as a green row.

    The distinction this suite is really about: a check that reports "search
    integrity is broken" is doing its job, and a check that raises
    `AttributeError` because a report function moved is a bug in this file. The
    first is a FAIL row. The second must not be reportable at all — a roll-up
    that caught everything would print PASS for a question it never asked.
    """

    def broken() -> status_module.CheckResult:
        raise AttributeError("build_report moved")

    monkeypatch.setattr(status_module, "CHECKS", (broken, lambda: _pass("b")))

    with pytest.raises(AttributeError):
        call_command("production_status")


def test_a_known_diagnostic_failure_is_a_fail_row_not_a_crash(monkeypatch, capsys) -> None:
    """The other half of the same rule, on the real check.

    `load_contracts` raising `ContractError` is the era-contract check finding
    what it looks for. It belongs in the table as FAIL, and the remaining checks
    still run.
    """
    from app.legacy_import import contracts as contracts_module

    def refuse() -> dict[int, object]:
        raise contracts_module.ContractError("era 2019 declares a column twice")

    monkeypatch.setattr(contracts_module, "load_contracts", refuse)
    monkeypatch.setattr(
        status_module, "CHECKS", (status_module._era_contracts, lambda: _pass("after"))
    )

    with pytest.raises(SystemExit) as exit_status:
        call_command("production_status")

    out = capsys.readouterr().out
    assert exit_status.value.code == 1
    assert "Era contracts" in out
    assert "After" in out, "a later check still ran"


def test_an_unreachable_database_is_reported_rather_than_raised(monkeypatch, capsys) -> None:
    """The one exception to the rule above, and it is deliberate.

    Every other finding needs the database to have answered, so a database that
    cannot be reached is the readiness check's own answer rather than a bug in
    it. It is a FAIL row and the exit code is still non-zero — what it must not
    be is a traceback that hides the verdict.
    """
    from django.db import DatabaseError

    from app.core import deployment

    def unreachable() -> object:
        raise DatabaseError("connection refused")

    monkeypatch.setattr(deployment, "readiness_report", unreachable)
    monkeypatch.setattr(status_module, "CHECKS", (status_module._deployment_readiness,))

    with pytest.raises(SystemExit) as exit_status:
        call_command("production_status")

    assert exit_status.value.code == 1
    assert "FAIL" in capsys.readouterr().out


# --------------------------------------------------------------------------
# It writes nothing
# --------------------------------------------------------------------------


#: The canonical record, and the queue and projection tables that a "helpful"
#: command could consume rather than describe. `SearchRebuildDebt` is the one
#: that would be easiest to get wrong: reporting it and draining it are one
#: line apart, and a status command that paid the debt off would make the next
#: run's clean result meaningless.
def _fingerprint() -> dict[str, object]:
    from app.audit.models import ChangeEvent
    from app.documents.models import Document
    from app.legacy_import.models import OpinionArchiveSearchDocument
    from app.matters.models import Matter
    from app.search.models import SearchDocument, SearchRebuildDebt
    from app.submissions.models import Submission

    counted = (
        Matter,
        Submission,
        Document,
        SearchDocument,
        OpinionArchiveSearchDocument,
        ChangeEvent,
        SearchRebuildDebt,
    )
    fingerprint: dict[str, object] = {}
    for model in counted:
        label = model._meta.label
        fingerprint[label] = model.objects.count()
        # A count alone would miss an update in place — a row consumed and
        # rewritten, or a `last_attempt_at` stamped by a check that "just
        # retried once". The primary keys catch a swap; the count catches the
        # rest.
        fingerprint[f"{label}:pks"] = sorted(
            str(pk) for pk in model.objects.values_list("pk", flat=True)
        )
    return fingerprint


def test_a_successful_run_writes_nothing(db) -> None:
    """A genuinely passing run, on a database where passing is deterministic.

    An empty database is the one shape where every included check has a knowable
    verdict: nothing is projected and nothing is canonical, so the counts agree,
    no debt is owed and the archive projection describes the nothing it holds.
    That makes this the success case, and the failing case below is the one that
    carries populated data.
    """
    before = _fingerprint()

    report = status_module.production_status()
    assert report.ok, [check.label for check in report.checks if not check.ok]

    call_command("production_status")

    assert _fingerprint() == before


def test_a_failing_run_writes_nothing(world, monkeypatch) -> None:
    """The run that matters, over a populated world.

    A repair path opens when something is wrong, so a zero-write test that only
    covered the healthy case would be testing the branch that was never the
    risk. The archive projection is the check made to fail because its remedy —
    `opinion_archive_search rebuild` — is the repair a status command would be
    most tempted to reach for.
    """
    from app.legacy_import import opinion_search

    monkeypatch.setattr(
        opinion_search, "archive_index_findings", lambda: ["2 rida on aegunud", "1 rida on üle"]
    )
    before = _fingerprint()
    assert before["matters.Matter"], "the fixture put nothing at risk"

    with pytest.raises(SystemExit):
        call_command("production_status")

    assert _fingerprint() == before


def test_the_search_debt_is_reported_and_not_consumed(db) -> None:
    """Named separately because it is the one a future edit could plausibly get wrong.

    `check_search_freshness` reads this queue and `run_search_refresh_worker`
    drains it. The roll-up is on the reading side and has to stay there — the
    two are one line apart, and a status command that paid the debt off would
    make the next run's clean result meaningless.
    """
    from app.search.freshness import mark_rebuild_owed, outstanding

    owed_before = outstanding().count()
    mark_rebuild_owed("test")
    assert outstanding().count() == owed_before + 1

    try:
        call_command("production_status")
    except SystemExit:
        pass

    assert outstanding().count() == owed_before + 1


# --------------------------------------------------------------------------
# It cannot reach a repair
# --------------------------------------------------------------------------

#: Commands that change something. None of them may be reachable from the
#: status module, and the check is on the module's source rather than on its
#: behaviour: a call added behind a flag would pass every test above.
WRITE_COMMANDS = (
    "rebuild_search_index",
    "refresh_matter_search",
    "opinion_archive_search",
    "run_search_refresh_worker",
    "run_extraction_worker",
    "extract_pending_documents",
    "rebuild_document_derivatives",
    "prune_orphaned_evidence",
    "refresh_current_register",
    "promote_current_register",
    "reference_data",
    "migrate",
)


def test_the_status_module_names_no_write_command() -> None:
    from pathlib import Path

    source = Path(status_module.__file__).read_text(encoding="utf-8")
    command_source = (
        Path(status_module.__file__).parent / "management" / "commands" / "production_status.py"
    ).read_text(encoding="utf-8")

    for name in WRITE_COMMANDS:
        # The docstrings name several of these deliberately, to say what this
        # does not do. What must not appear is a call.
        for text, where in ((source, "production_status.py"), (command_source, "the command")):
            assert f'call_command("{name}"' not in text, f"{name} is called from {where}"
            assert f"call_command('{name}'" not in text, f"{name} is called from {where}"


def test_the_status_module_does_not_shell_out() -> None:
    """No subprocesses, and no parsing another command's prose.

    The roll-up calls report functions. A subprocess would make the aggregate
    depend on wording nobody thinks of as an interface, and would put a second
    Django process on a production host to answer a question this one can.
    """
    from pathlib import Path

    source = Path(status_module.__file__).read_text(encoding="utf-8")

    for forbidden in ("subprocess", "os.system", "popen", "manage.py"):
        assert forbidden not in source, forbidden


def test_the_default_status_does_not_read_evidence_bytes() -> None:
    """The deep hash is excluded, and so is the cheap pass, with the reason recorded.

    `check_evidence_integrity` is *named* in this module on purpose — the
    omissions are the part an operator has to be able to see we considered — so
    the assertion is on the import and the call rather than on the name.
    """
    from pathlib import Path

    excluded = dict(status_module.EXCLUDED)
    assert "check_evidence_integrity" in excluded
    assert "migration_plan" in excluded
    assert all(reason.strip() for reason in excluded.values()), "an omission with no reason"

    source = Path(status_module.__file__).read_text(encoding="utf-8")
    assert "app.documents.integrity" not in source, "the evidence checker is imported"
    assert "check_evidence(" not in source, "the evidence checker is called"
