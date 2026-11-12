# ruff: noqa: F811 - pytest fixtures imported from the sync suites are used as arguments
"""Synchronisation robustness (audit 2026-11-11 §C.1 LS-06/08/09, §C.3 LS-07, DR-05).

* LS-08 — a created Teema that does not read back stops further creation.
* LS-09 — a search-refresh failure after the writes keeps the run's record.
* LS-06 — breaker overrides only on a manual run, never negative, ``0`` is zero.
* LS-07 — a breaker on a rise in held fields, and a guard on the live
  population, replace the inert conflict breaker.
* DR-05 — a backup set opens the mutating stage only with the proof of its
  level-2 verification, mounted from the host; a bare or old name never does.

The register world of `tests/test_ls_b_engine.py`; every value is invented.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from typing import Any

import pytest
from django.core.management import CommandError, call_command

from app.audit.models import ChangeEvent
from app.legacy_import import lovable_sync
from app.legacy_import.lovable_sync import SyncRefused, backup_proof_problem
from app.legacy_import.lovable_sync_models import (
    LovableSyncNewTopic,
    LovableSyncRun,
    NewTopicStatus,
    SyncMode,
    SyncRunStatus,
    SyncTrigger,
)
from app.matters.models import Matter
from tests.test_lovable_pilot import pilot_on, world  # noqa: F401
from tests.test_lovable_sync import _matter, _run
from tests.test_ls_b_engine import _at, _new_topics, register  # noqa: F401

# ---------------------------------------------------------------------------
# LS-08: no further new Teema after a failed read-back
# ---------------------------------------------------------------------------


def test_a_failed_read_back_of_a_created_teema_stops_further_creation(register, monkeypatch):
    """Regression: LS-08 — the first created Teema whose read-back fails is the
    last one created tonight; the rest are held VERIFY_STOPPED (ADR 0192 §7) and
    created by the next good run."""
    _new_topics(register["source"])
    real = lovable_sync.verify_matter
    monkeypatch.setattr(lovable_sync, "verify_matter", lambda _plan: False)
    run = _run(register, at=_at(0))
    assert run.status == SyncRunStatus.FAILED and run.error.startswith("VERIFY"), run.error
    created = run.counts["new_topics_created"]
    stopped = run.counts["new_topic_reasons"].get(lovable_sync.VERIFY_STOPPED, 0)
    assert created >= 1 and stopped >= 1 and created + stopped == 3, run.counts
    held = LovableSyncNewTopic.objects.filter(reason=lovable_sync.VERIFY_STOPPED)
    assert held.count() == stopped
    assert all(row.status == NewTopicStatus.HELD for row in held)
    assert not Matter.all_objects.filter(reference_year=2026, reference_number=256).exists()

    monkeypatch.setattr(lovable_sync, "verify_matter", real)
    again = _run(register, at=_at(1))
    assert again.counts["new_topics_created"] == stopped, again.error
    assert _matter("2026_256") is not None


# ---------------------------------------------------------------------------
# LS-09: the search refresh never throws away the run's record
# ---------------------------------------------------------------------------


def test_a_search_refresh_failure_keeps_the_runs_record(register, monkeypatch):
    """Regression: LS-09 — the writes are committed before the search refresh;
    its failure leaves the run's counts, decisions, baselines and new-topic
    ledger, verified, with a warning and the Teemad still to refresh."""
    import app.search.indexing as indexing

    def broken(*_a: Any, **_k: Any) -> int:
        raise RuntimeError("search projection unavailable")

    real = indexing.refresh_matters
    monkeypatch.setattr(indexing, "refresh_matters", broken)
    _new_topics(register["source"])
    register["source"].edit("2026_240", teema="Näidisteema 2026_240 parandatud")
    run = _run(register, at=_at(0))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert run.verified is True
    assert run.counts["new_topics_created"] == 3
    assert run.counts["updated"] >= 1
    assert run.counts["warnings"] == [lovable_sync.SEARCH_REFRESH_FAILED]
    assert run.counts["search_refresh_pending"] == 4
    assert run.decisions.exists()
    assert LovableSyncNewTopic.objects.filter(status=NewTopicStatus.CREATED).count() == 3
    assert _matter("2026_240").title == "Näidisteema 2026_240 parandatud"
    # The baseline advanced: the next run has nothing left to write.
    monkeypatch.setattr(indexing, "refresh_matters", real)
    again = _run(register, at=_at(1))
    assert again.status == SyncRunStatus.SUCCEEDED and again.counts["updated"] == 0


# ---------------------------------------------------------------------------
# LS-06: overrides are a person's decision for one supervised run
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("override", [{"max_changed": 50}, {"max_restored": 500}])
def test_a_scheduled_run_refuses_a_breaker_override(register, override):
    """Regression: LS-06 — the scheduled night keeps the approved limits."""
    with pytest.raises(SyncRefused) as refused:
        _run(register, SyncTrigger.SCHEDULED, at=_at(0), **override)
    assert refused.value.code == "OVERRIDE_NOT_MANUAL"
    assert not LovableSyncRun.objects.exists()


@pytest.mark.parametrize("override", [{"max_changed": -1}, {"max_restored": -5}])
def test_a_negative_override_is_refused(register, override):
    """Regression: LS-06."""
    with pytest.raises(SyncRefused) as refused:
        _run(register, at=_at(0), **override)
    assert refused.value.code == "OVERRIDE_NEGATIVE"
    assert not LovableSyncRun.objects.exists()


def test_a_zero_change_limit_means_zero(register):
    """Regression: LS-06 — ``max_changed=0`` was read as «the default» (20)."""
    register["source"].edit("2026_240", teema="Näidisteema 2026_240 parandatud")
    events = ChangeEvent.objects.count()
    run = _run(register, at=_at(0), max_changed=0)
    assert (run.status, run.error) == (SyncRunStatus.REVIEW_ONLY, lovable_sync.BREAKER)
    assert run.counts["breaker_limit"] == 0
    assert ChangeEvent.objects.count() == events


@pytest.mark.parametrize("flag", ["--max-changed", "--max-restored"])
def test_the_command_refuses_an_override_on_the_scheduled_trigger(flag):
    """Regression: LS-06 — refused before the container's gates, the
    credentials or any read."""
    with pytest.raises(CommandError, match="only for a supervised --trigger manual run"):
        call_command("lovable_sync", "run", "--trigger", "scheduled", flag, "5")


def test_the_command_refuses_a_negative_override():
    """Regression: LS-06."""
    with pytest.raises(CommandError, match="cannot be negative"):
        call_command("lovable_sync", "run", "--trigger", "manual", "--max-changed", "-1")


def test_no_override_means_the_approved_limits():
    from app.legacy_import.management.commands.lovable_sync import Command

    options = Command().create_parser("manage.py", "lovable_sync").parse_args(["run"])
    assert (options.max_changed, options.max_restored) == (None, None)
    assert (lovable_sync.MAX_CHANGED_MATTERS, lovable_sync.MAX_RESTORED_MATTERS) == (20, 100)


# ---------------------------------------------------------------------------
# LS-07: held fields and the live population
# ---------------------------------------------------------------------------


def test_a_sudden_rise_in_held_fields_holds_the_night(register, monkeypatch):
    """Regression: LS-07 — a night whose planner suddenly holds more fields than
    `MAX_NEW_HOLDS` (a broken mapping) writes nothing, its safe changes included."""
    first = _run(register, at=_at(0))
    assert first.status == SyncRunStatus.SUCCEEDED, first.error
    monkeypatch.setattr(lovable_sync, "MAX_NEW_HOLDS", 1)
    register["source"].edit("2026_240", hetkeseis="Riigikogus menetluses")
    register["source"].edit("2026_238", hetkeseis="Riigikogus menetluses")
    register["source"].edit("2026_232", teema="Näidisteema 2026_232 parandatud")
    run = _run(register, at=_at(1))
    assert (run.status, run.error) == (SyncRunStatus.REVIEW_ONLY, lovable_sync.BREAKER)
    assert run.counts["held_fields"] - run.counts["held_fields_before"] >= 2
    assert _matter("2026_232").title != "Näidisteema 2026_232 parandatud"


def test_holds_that_were_there_before_hold_nothing(register, monkeypatch):
    """The breaker is on the rise, never on the standing total."""
    register["source"].edit("2026_240", hetkeseis="Riigikogus menetluses")
    register["source"].edit("2026_238", hetkeseis="Riigikogus menetluses")
    first = _run(register, at=_at(0))
    monkeypatch.setattr(lovable_sync, "MAX_NEW_HOLDS", 0)
    register["source"].edit("2026_232", teema="Näidisteema 2026_232 parandatud")
    run = _run(register, at=_at(1))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert run.counts["held_fields"] == first.counts["held_fields"] >= 2
    assert _matter("2026_232").title == "Näidisteema 2026_232 parandatud"


def test_a_large_move_in_the_live_population_refuses_to_write(register, monkeypatch):
    """Regression: LS-07 — the live population grew past the guard since the
    previous reading: an APPLY run refuses before the reading is stored; a
    COMPARE run takes it as the new reading, and APPLY goes on from there."""
    first = _run(register, at=_at(0))
    assert first.status == SyncRunStatus.SUCCEEDED, first.error
    monkeypatch.setattr(lovable_sync, "POPULATION_MAX_DELTA", 2)
    _new_topics(register["source"])  # 5 → 9 live topics
    matters = Matter.all_objects.count()
    refused = _run(register, at=_at(1))
    assert refused.status == SyncRunStatus.FAILED
    assert refused.error.startswith("POPULATION_CHANGE"), refused.error
    assert Matter.all_objects.count() == matters
    assert not hasattr(refused, "snapshot") or not refused.snapshot

    lovable_sync.set_mode(SyncMode.COMPARE, reason="a person looked")
    accepted = _run(register, at=_at(2))
    assert accepted.status == SyncRunStatus.SUCCEEDED, accepted.error
    lovable_sync.set_mode(SyncMode.APPLY, reason="test")
    applied = _run(register, at=_at(3))
    assert applied.status == SyncRunStatus.SUCCEEDED, applied.error
    assert applied.counts["new_topics_created"] == 3


def test_the_population_guard_is_both_absolute_and_relative():
    index = [{"id": str(i)} for i in range(2600)]
    assert lovable_sync.population_change({"index": index}, {"index": index[:2560]}) == ""
    assert lovable_sync.population_change({"index": index}, {"index": index[:2400]})
    assert lovable_sync.population_change({"index": index}, {"index": index[:1000]})
    assert lovable_sync.population_change({"index": index[:20]}, {"index": index[:60]}) == ""


# ---------------------------------------------------------------------------
# DR-05: the proof of a verified backup
# ---------------------------------------------------------------------------

STARTED = dt.datetime(2099, 1, 1, 22, 15, tzinfo=dt.UTC)
SET = "sets/20990101T221000Z"


def _set_dir(
    root: Path,
    *,
    name: str = "20990101T221000Z",
    level: int = 2,
    verified_at: str = "2099-01-01T22:12:00Z",
    dump: bytes = b"PGDMP naidis",
    recorded: str | None = None,
    marker: bool = True,
) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "database.dump").write_bytes(dump)
    sha = hashlib.sha256(dump).hexdigest()
    (directory / "SHA256SUMS").write_text(
        f"{recorded or sha}  database.dump\n{'0' * 64}  manifest.json\n", encoding="utf-8"
    )
    if marker:
        (directory / f"VERIFIED-L{lovable_sync.BACKUP_PROOF_LEVEL}").write_text(
            f"set={name}\nlevel={level}\nverified_at={verified_at}\ndatabase_sha256={sha}\n",
            encoding="utf-8",
        )
    return directory


def test_a_verified_set_mounted_from_the_host_is_proven(tmp_path):
    assert backup_proof_problem(SET, STARTED, set_dir=str(_set_dir(tmp_path))) == ""


@pytest.mark.parametrize(
    ("arrange", "code"),
    [
        ({"marker": False}, "PROOF_MISSING"),
        ({"name": "20990101T220000Z"}, "PROOF_OTHER_SET"),
        ({"level": 1}, "PROOF_LEVEL"),
        ({"verified_at": "2099-01-01T22:00:00Z"}, "PROOF_TIME"),  # before the set
        ({"verified_at": "2099-01-01T22:20:00Z"}, "PROOF_TIME"),  # after the run began
        ({"verified_at": "yesterday"}, "PROOF_TIME"),
        ({"recorded": "f" * 64}, "PROOF_DUMP"),  # verified another dump
    ],
)
def test_a_set_without_a_matching_proof_is_refused(tmp_path, arrange, code):
    """Regression: DR-05 — a name and an age are not a verification."""
    directory = _set_dir(tmp_path, **arrange)
    if "name" in arrange:
        directory = directory.rename(tmp_path / "mounted")
    assert backup_proof_problem(SET, STARTED, set_dir=str(directory)) == code


def test_a_dump_changed_after_its_verification_is_refused(tmp_path):
    """Regression: DR-05 — the dump is hashed again in the run's container."""
    directory = _set_dir(tmp_path)
    (directory / "database.dump").write_bytes(b"PGDMP muudetud")
    assert backup_proof_problem(SET, STARTED, set_dir=str(directory)) == "PROOF_DUMP"


def test_no_mount_is_no_proof(tmp_path, monkeypatch):
    """Regression: DR-05 — the default: a run with no host mount proves nothing."""
    monkeypatch.delenv(lovable_sync.HOST_BACKUP_DIR_ENV, raising=False)
    assert backup_proof_problem(SET, STARTED) == "PROOF_NOT_MOUNTED"
    monkeypatch.setenv(lovable_sync.HOST_BACKUP_DIR_ENV, str(tmp_path / "absent"))
    assert backup_proof_problem(SET, STARTED) == "PROOF_NOT_MOUNTED"


def _apply_set(moment: dt.datetime) -> str:
    return (moment - dt.timedelta(minutes=5)).astimezone(dt.UTC).strftime("sets/%Y%m%dT%H%M%SZ")


def test_an_apply_run_writes_only_with_the_proof_mounted(register, tmp_path, monkeypatch):
    """Regression: DR-05 — the run itself: a fresh name with no proof writes
    nothing (REVIEW_ONLY, the reason named); with the host's mount it writes."""
    monkeypatch.setattr(lovable_sync, "backup_proof_problem", backup_proof_problem)
    monkeypatch.delenv(lovable_sync.HOST_BACKUP_DIR_ENV, raising=False)
    register["source"].edit("2026_240", teema="Näidisteema 2026_240 parandatud")
    bare = _run(register, at=_at(0))
    assert (bare.status, bare.error) == (
        SyncRunStatus.REVIEW_ONLY,
        f"{lovable_sync.NO_BACKUP}: PROOF_NOT_MOUNTED",
    )
    assert bare.backup_set == ""
    assert _matter("2026_240").title != "Näidisteema 2026_240 parandatud"

    moment = _at(1)
    backup_set = _apply_set(moment)
    taken = backup_set[5:]
    verified = (moment - dt.timedelta(minutes=3)).astimezone(dt.UTC)
    directory = _set_dir(tmp_path, name=taken, verified_at=f"{verified:%Y-%m-%dT%H:%M:%SZ}")
    monkeypatch.setenv(lovable_sync.HOST_BACKUP_DIR_ENV, str(directory))
    proven = _run(register, at=moment, backup_set=backup_set)
    assert proven.status == SyncRunStatus.SUCCEEDED, proven.error
    assert proven.backup_set == backup_set
    assert _matter("2026_240").title == "Näidisteema 2026_240 parandatud"


def test_an_old_set_with_a_proof_still_writes_nothing(register, tmp_path, monkeypatch):
    """Regression: DR-05 — a skipped backup (10.11: the nightly set was never
    taken) leaves only an older set; with its proof it is still too old."""
    monkeypatch.setattr(lovable_sync, "backup_proof_problem", backup_proof_problem)
    moment = _at(0)
    old = (moment - dt.timedelta(hours=3)).astimezone(dt.UTC)
    directory = _set_dir(
        tmp_path, name=f"{old:%Y%m%dT%H%M%SZ}", verified_at=f"{old:%Y-%m-%dT%H:%M:%SZ}"
    )
    monkeypatch.setenv(lovable_sync.HOST_BACKUP_DIR_ENV, str(directory))
    register["source"].edit("2026_240", teema="Näidisteema 2026_240 parandatud")
    run = _run(register, at=moment, backup_set=f"sets/{old:%Y%m%dT%H%M%SZ}")
    assert (run.status, run.error) == (
        SyncRunStatus.REVIEW_ONLY,
        f"{lovable_sync.NO_BACKUP}: SET_STALE",
    )
    assert _matter("2026_240").title != "Näidisteema 2026_240 parandatud"
