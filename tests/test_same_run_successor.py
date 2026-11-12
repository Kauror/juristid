# ruff: noqa: F811 - pytest fixtures imported from the sync suites are used as arguments
"""A successor Lovable created tonight converges its predecessor tonight
(audit 2026-11-11 LS-01, the root cause of ST-007).

The reference world of `tests/test_r4_authority.py`: 2026_140 continues under
2026_259, which appears in Lovable as a new topic. The cohort is planned before
the creation, so 140 is held SUCCESSOR_NOT_IN_JURISTID; after the creation only
such plans are planned again, through `apply_matter`, under the same gates:
an APPLY night that writes, every read-back good so far, and the Teemad-written
breaker counting the re-plan. Every value is invented.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.legacy_import import lovable_sync
from app.legacy_import.lovable_sync_models import SyncOutcome, SyncRunStatus
from tests.test_lovable_pilot import pilot_on, world  # noqa: F401
from tests.test_lovable_sync import _decisions, _matter, _run
from tests.test_ls_b_engine import _at as _ls_at
from tests.test_ls_b_engine import _id, register  # noqa: F401
from tests.test_ls_b_engine import _row as _ls_row
from tests.test_r4_authority import _at, _lovable_now, reference  # noqa: F401


def test_a_failed_read_back_of_the_successor_never_supersedes_the_predecessor(
    reference, monkeypatch
):
    """Guard for LS-01 (passes before the fix too) — 2026_259 is created but does not read back as
    written: the run stops writing, and 2026_140 stays open, held as before."""
    real = lovable_sync.verify_matter

    def verify(plan: Any) -> bool:
        return False if plan.state.reference == "2026_259" else real(plan)

    monkeypatch.setattr(lovable_sync, "verify_matter", verify)
    _lovable_now(reference["source"])
    run = _run(reference, at=_at(0))
    assert run.status == SyncRunStatus.FAILED and run.error.startswith("VERIFY"), run.error
    assert run.counts["new_topics_created"] == 1
    assert _matter("2026_140").is_open
    assert _matter("2026_140").superseded_by_id is None
    found = _decisions(run, "2026_140")
    assert found["stage"].reason == lovable_sync.SUCCESSOR_MISSING


def test_the_re_plan_is_counted_once_against_the_teemad_written_breaker(reference, monkeypatch):
    """Regression: LS-01 — breaker accounting includes the re-plan: 2026_140 is
    one of the night's changed Teemad (its source changed), so its re-plan is
    counted once, never twice; at exactly the limit the night still writes."""
    _lovable_now(reference["source"])
    CHANGED = _changed_tonight(reference, _at(0))
    monkeypatch.setattr(lovable_sync, "MAX_CHANGED_MATTERS", CHANGED)
    run = _run(reference, at=_at(0))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert run.counts["changed_source"] == CHANGED
    assert not _matter("2026_140").is_open


def test_a_night_over_the_breaker_re_plans_nothing(reference, monkeypatch):
    """Guard for LS-01 (passes before the fix too) — under the same breaker: a night over it writes
    nothing, creates nothing and re-plans nothing."""
    _lovable_now(reference["source"])
    monkeypatch.setattr(
        lovable_sync, "MAX_CHANGED_MATTERS", _changed_tonight(reference, _at(0)) - 1
    )
    run = _run(reference, at=_at(0))
    assert (run.status, run.error) == (SyncRunStatus.REVIEW_ONLY, lovable_sync.BREAKER)
    assert run.counts["new_topics_created"] == 0
    assert _matter("2026_140").is_open
    again = _run(reference, at=_at(24))
    assert again.status == SyncRunStatus.REVIEW_ONLY


def _changed_tonight(reference: Any, at: Any) -> int:
    """How many Teemad tonight's reading changes at the source, planned exactly
    as the run plans them (`_execute`), reading only: the breaker's own count."""
    from app.legacy_import.lovable_source import take_snapshot

    states = list(lovable_sync.population())
    snapshot = take_snapshot(
        reference["source"],
        sorted(str(s.source_id) for s in states),
        mapping_version=lovable_sync.MAPPING_VERSION,
        pause=lambda _s: None,
    )
    resolver = lovable_sync.OwnerResolver(states)
    today = at.astimezone(lovable_sync.TALLINN).date()
    plans = [
        lovable_sync.plan_matter(
            s, lovable_sync.bundle_of(snapshot, s.source_id), resolver=resolver, today=today
        )
        for s in states
    ]
    return sum(1 for p in plans if any(d.reason != lovable_sync.RESTORED for d in p.eligible))


# ---------------------------------------------------------------------------
# QA-E-01: a continuation cycle is held, never guessed
# ---------------------------------------------------------------------------


def _link(source: Any, prev: str, nxt: str) -> None:
    source.links.append({"prev_topic_id": _id(prev), "next_topic_id": _id(nxt)})
    for ref in (prev, nxt):
        for row in source.rows.values():
            if row["nr"] == ref:
                row["version"] += 1


def _held_cycle(run: Any, reference: str) -> None:
    found = _decisions(run, reference)
    assert found["stage"].outcome == SyncOutcome.REVIEW_REQUIRED, found
    assert found["stage"].reason == lovable_sync.CONTINUATION_CYCLE


@pytest.mark.parametrize(
    "chain",
    [("2026_238", "2026_232"), ("2026_238", "2026_232", "2026_253")],
    ids=["two", "three"],
)
def test_a_continuation_cycle_holds_every_teema_on_it_and_the_rest_runs(register, chain):
    """Regression: QA-E-01 — A→B→A (or a longer ring) closed A as SUPERSEDED
    under B (a guess) and B failed, so every night ended FAILED. Every Teema on
    the cycle is held CONTINUATION_CYCLE with its lifecycle untouched; the rest
    of the night is written and the run succeeds."""
    source = register["source"]
    for prev, nxt in zip(chain, chain[1:] + chain[:1], strict=True):
        _link(source, prev, nxt)
    source.edit("2026_240", teema="Näidisteema 2026_240 parandatud")
    run = _run(register, at=_ls_at(0))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    for ref in chain:
        _held_cycle(run, ref)
        matter = _matter(ref)
        assert matter.is_open and matter.superseded_by_id is None, ref
    assert _matter("2026_240").title == "Näidisteema 2026_240 parandatud"
    again = _run(register, at=_ls_at(1))
    assert again.status == SyncRunStatus.SUCCEEDED, again.error
    assert all(_matter(r).is_open for r in chain)


def test_a_cycle_through_a_topic_created_tonight_is_held_too(register):
    """Regression: QA-E-01 — the same-run re-plan (LS-01) cannot close a Teema
    into a cycle: 2026_238 continues as the new 2026_254, which continues as
    2026_238. 254 is created; neither lifecycle is written."""
    source = register["source"]
    source.put(_id("2026_254"), _ls_row("2026_254"), version=1)
    _link(source, "2026_238", "2026_254")
    _link(source, "2026_254", "2026_238")
    run = _run(register, at=_ls_at(0))
    assert run.counts["new_topics_created"] == 1, run.counts
    _held_cycle(run, "2026_238")
    _held_cycle(run, "2026_254")
    assert _matter("2026_238").is_open and _matter("2026_238").superseded_by_id is None
    assert _matter("2026_254").superseded_by_id is None
    assert run.status == SyncRunStatus.SUCCEEDED, run.error


def test_the_cycle_finder_names_only_the_ring():
    def bundle(prev: str, nxt: str) -> dict[str, Any]:
        return {"stage_links": [{"prev": prev, "next": nxt}]}

    snapshot = {
        "cohort": {
            "a": bundle("a", "b"),
            "b": bundle("b", "c"),
            "c": bundle("c", "b"),
            "d": bundle("d", "d"),
            "e": bundle("e", "f"),
        }
    }
    assert lovable_sync.continuation_cycles(snapshot) == {"b", "c", "d"}


def test_a_topic_linked_to_itself_is_held_as_a_cycle(register):
    """Regression: QA-E-01 — a self-loop (Lovable's CHECK forbids it; a reading
    may still carry one) is a cycle of one. Before, it was SUCCESSOR_AMBIGUOUS;
    on the deploy night such a hold is re-recorded as CONTINUATION_CYCLE."""
    _link(register["source"], "2026_238", "2026_238")
    run = _run(register, at=_ls_at(0))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    _held_cycle(run, "2026_238")
    assert _matter("2026_238").is_open


def test_a_cycle_member_with_nothing_changed_is_still_visibly_held(register):
    """QA-E-01 (review) — every Teema on a cycle is held every night it stands,
    its own fields unchanged or not."""
    _link(register["source"], "2026_238", "2026_232")
    _link(register["source"], "2026_232", "2026_238")
    _run(register, at=_ls_at(0))
    again = _run(register, at=_ls_at(1))
    for ref in ("2026_238", "2026_232"):
        _held_cycle(again, ref)


def _successor_for_240(register: Any) -> None:
    source = register["source"]
    source.put(_id("2026_254"), _ls_row("2026_254"), version=1)
    _link(source, "2026_240", "2026_254")


def test_a_predecessor_over_the_breaker_is_deferred_by_name(register, monkeypatch):
    """Regression: LS-01 (review) — 2026_240 had no other change, so its re-plan
    is one more Teema written; at a limit of 0 it waits, named
    SUCCESSOR_DEFERRED_BY_BREAKER and counted, never SUCCESSOR_NOT_IN_JURISTID."""
    _successor_for_240(register)
    monkeypatch.setattr(lovable_sync, "MAX_CHANGED_MATTERS", 0)
    run = _run(register, at=_ls_at(0))
    assert run.counts["new_topics_created"] == 1, run.counts
    assert run.counts["successors_deferred"] == 1
    assert _decisions(run, "2026_240")["stage"].reason == lovable_sync.SUCCESSOR_DEFERRED
    assert _matter("2026_240").is_open


def test_one_more_teema_within_the_limit_is_converged_and_counted(register, monkeypatch):
    """Regression: LS-01 (review) — the ``more = 1`` path inside the limit."""
    _successor_for_240(register)
    monkeypatch.setattr(lovable_sync, "MAX_CHANGED_MATTERS", 1)
    run = _run(register, at=_ls_at(0))
    assert run.counts["changed_source"] == 1 and run.counts["successors_deferred"] == 0
    assert not _matter("2026_240").is_open
    assert _matter("2026_240").superseded_by_id == _matter("2026_254").pk


def test_the_re_plan_never_writes_restores_the_restore_breaker_holds(reference, monkeypatch):
    """Regression: LS-01 (review P2) — while restores wait (`RESTORE_BREAKER`),
    a re-plan whose writes are all restores waits too."""
    real = lovable_sync.plan_matter

    def plan(state: Any, *a: Any, **k: Any) -> Any:
        done = real(state, *a, **k)
        if state.reference == "2026_140":
            for decision in done.eligible:
                decision.reason = lovable_sync.RESTORED
        return done

    monkeypatch.setattr(lovable_sync, "plan_matter", plan)
    monkeypatch.setattr(lovable_sync, "MAX_RESTORED_MATTERS", -1)
    _lovable_now(reference["source"])
    run = _run(reference, at=_at(0))
    assert run.error == lovable_sync.RESTORE_BREAKER, run.error
    assert _matter("2026_140").is_open
