# ruff: noqa: F811 - pytest fixtures imported from the sync suites are used as arguments
"""A hand-over in Lovable has its own nightly limit (docs/adr/0220 §2), on synthetic data.

Lovable's «Teemade üleandmine» (its migration 0039) moves a lawyer's whole desk at
once: it sets «Uus vastutaja» on every chosen topic and writes one
`topic_responsible_change` row for each. A night that counted those against the
twenty-Teemad breaker would stop after every hand-over. A Teema whose only source
change is such an owner change counts against `MAX_HANDOVER_MATTERS` instead;
everything else is unchanged. Every name is invented.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from app.audit.models import ChangeEvent
from app.legacy_import import lovable_sync
from app.legacy_import.lovable_sync_models import SyncRunStatus
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction
from tests.test_lovable_pilot import pilot_on, world  # noqa: F401
from tests.test_lovable_sync import _matter, _run
from tests.test_ls_b_engine import NIGHT, _id, register  # noqa: F401

pytestmark = pytest.mark.django_db

OPEN = ("2026_238", "2026_232", "2026_253", "2026_240")


def _hand_over(source: Any, references: tuple[str, ...], to: str, *, recorded: bool = True) -> None:
    """What `handover_topics` does in Lovable: «Uus vastutaja» on each topic and,
    unless ``recorded`` is false (an ordinary edit of the cell), one
    responsible-change row dated today."""
    responsible = source.__dict__.setdefault("responsible", [])
    for reference in references:
        source.edit(reference, uus_vastutaja=to)
        if recorded:
            responsible.append(
                {
                    "id": f"rc-{reference}-{len(responsible)}",
                    "topic_id": _id(reference),
                    "vana_vastutaja": "Mari",
                    "uus_vastutaja": to,
                    "alates": "2026-11-12",
                }
            )


@pytest.fixture
def handover_source(register, monkeypatch):
    """The register world, its fake Lovable also serving responsible changes."""
    source = register["source"]
    plain = type(source).select

    def select(self: Any, table: str, columns: Any, *, filters: dict[str, str], order: str):
        if table == "topic_responsible_change":
            wanted = set(next(iter(filters.values()))[4:-1].split(","))
            return [
                {c: r.get(c) for c in columns}
                for r in self.__dict__.get("responsible", [])
                if r["topic_id"] in wanted
            ]
        return plain(self, table, columns, filters=filters, order=order)

    monkeypatch.setattr(type(source), "select", select)
    return register


def _at(hours: int) -> dt.datetime:
    return NIGHT + dt.timedelta(hours=hours)


def _owners(*references: str) -> list[str]:
    return [getattr(_matter(r).owner, "display_name", "") for r in references]


# ---------------------------------------------------------------------------
# Reading the source
# ---------------------------------------------------------------------------


def test_the_latest_responsible_change_names_the_hand_over():
    bundle = {
        "responsible_changes": [
            {"uus_vastutaja": "Jaan Näidis", "alates": "2026-11-20"},
            {"uus_vastutaja": "Mari", "alates": "2026-10-01"},
            {"uus_vastutaja": " Kati  Näidis ", "alates": "2026-11-20"},
        ]
    }
    # The newest day wins, and of two on the same day the later row.
    assert lovable_sync.recorded_handover(bundle) == "Kati Näidis"
    assert lovable_sync.recorded_handover({"responsible_changes": []}) == ""
    assert lovable_sync.recorded_handover(None) == ""


# ---------------------------------------------------------------------------
# The night
# ---------------------------------------------------------------------------


def test_a_recorded_hand_over_syncs_the_same_night_past_the_ordinary_limit(
    handover_source, world, monkeypatch
):
    monkeypatch.setattr(lovable_sync, "MAX_CHANGED_MATTERS", 1)
    _hand_over(handover_source["source"], OPEN, "Jaan Näidis")
    run = _run(handover_source, at=_at(0))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert _owners(*OPEN) == ["Jaan Näidis"] * len(OPEN)
    counts = run.counts
    assert (counts["changed_handovers"], counts["changed_source"]) == (len(OPEN), 0)
    assert counts["handover_limit"] == lovable_sync.MAX_HANDOVER_MATTERS
    # The open step follows the file to its new owner (`assign_matter`).
    for reference in OPEN:
        step = NextAction.objects.get(matter=_matter(reference), status=ActionStatus.OPEN)
        assert step.responsible_id == world["jaan"].pk
    limit = lovable_sync.MAX_HANDOVER_MATTERS
    assert (
        f"Lovable'is üle antud teemasid (ainult vastutaja muutus): {len(OPEN)}/{limit}"
        in lovable_sync.summary_lines(run)
    )


def test_an_owner_edited_without_a_responsible_change_is_an_ordinary_change(
    handover_source, monkeypatch
):
    monkeypatch.setattr(lovable_sync, "MAX_CHANGED_MATTERS", 1)
    _hand_over(handover_source["source"], OPEN[:2], "Jaan Näidis", recorded=False)
    events = ChangeEvent.objects.count()
    run = _run(handover_source, at=_at(0))
    assert (run.status, run.error) == (SyncRunStatus.REVIEW_ONLY, lovable_sync.BREAKER)
    assert ChangeEvent.objects.count() == events
    assert _owners(*OPEN[:2]) == ["Mari Näidis", "Mari Näidis"]


def test_a_hand_over_with_another_change_on_the_same_teema_is_ordinary(
    handover_source, monkeypatch
):
    monkeypatch.setattr(lovable_sync, "MAX_CHANGED_MATTERS", 1)
    source = handover_source["source"]
    _hand_over(source, OPEN[:2], "Jaan Näidis")
    source.edit(OPEN[0], jargmiseks="Uus samm", tegevuse_tahtaeg="2099-04-01")
    source.edit(OPEN[1], teema="Näidisteema, uus pealkiri")
    run = _run(handover_source, at=_at(0))
    assert (run.status, run.error) == (SyncRunStatus.REVIEW_ONLY, lovable_sync.BREAKER)
    assert run.counts.get("changed_handovers", 0) == 0


def test_a_hand_over_to_nobody_is_ordinary(handover_source, monkeypatch):
    """«Vastutaja puudub» clears the owner: not a hand-over to a lawyer."""
    monkeypatch.setattr(lovable_sync, "MAX_CHANGED_MATTERS", 1)
    _hand_over(handover_source["source"], OPEN[:2], "Vastutaja puudub")
    run = _run(handover_source, at=_at(0))
    assert run.counts.get("changed_handovers", 0) == 0


def test_hand_overs_over_their_own_limit_stop_the_night(handover_source, monkeypatch):
    monkeypatch.setattr(lovable_sync, "MAX_HANDOVER_MATTERS", 2)
    _hand_over(handover_source["source"], OPEN[:3], "Jaan Näidis")
    events = ChangeEvent.objects.count()
    run = _run(handover_source, at=_at(0))
    assert (run.status, run.error) == (SyncRunStatus.REVIEW_ONLY, lovable_sync.BREAKER)
    assert ChangeEvent.objects.count() == events
    assert _owners(*OPEN[:3]) == ["Mari Näidis"] * 3


def test_ordinary_changes_keep_their_limit_beside_a_hand_over(handover_source, monkeypatch):
    monkeypatch.setattr(lovable_sync, "MAX_CHANGED_MATTERS", 1)
    source = handover_source["source"]
    _hand_over(source, OPEN[:2], "Jaan Näidis")
    source.edit(OPEN[2], jargmiseks="Uus samm", tegevuse_tahtaeg="2099-04-01")
    source.edit(OPEN[3], jargmiseks="Teine samm", tegevuse_tahtaeg="2099-04-02")
    run = _run(handover_source, at=_at(0))
    assert (run.status, run.error) == (SyncRunStatus.REVIEW_ONLY, lovable_sync.BREAKER)


def test_a_hand_over_to_a_name_with_no_account_is_held_not_counted(handover_source, monkeypatch):
    """The owner does not resolve, so nothing is written for it and it is no
    hand-over: the hold says why (`UNRESOLVED_OWNER`)."""
    monkeypatch.setattr(lovable_sync, "MAX_CHANGED_MATTERS", 1)
    _hand_over(handover_source["source"], OPEN[:2], "Uus Jurist")
    run = _run(handover_source, at=_at(0))
    assert run.counts.get("changed_handovers", 0) == 0
    assert _owners(*OPEN[:2]) == ["Mari Näidis", "Mari Näidis"]
