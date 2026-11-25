# ruff: noqa: F811 - the contract suite's fixtures are imported and used as arguments
"""The combined r93 rehearsal: Lovable APPLY and the Riigikogu cycle on the same Teemad.

Each component is tested on its own branch; this runs them together through
their real services, over four synthetic nights:

* **Lovable** is the contract suite's world (`tests/lovable_contract/world.py`):
  register Teemad made by the real importer, every run the real
  `lovable_sync.run_sync` against an invented, read-only Lovable.
* **The Riigikogu** is the programme's fake API (`tests/test_riigikogu_programme.py`):
  the real `cycle.run`, every stage, against dictionaries.

The scenario: a new Teema from Lovable, an ordinary change and a recorded
hand-over (#725's two limits), an invitation to koda found on a followed bill,
its deadline first matching then conflicting with Lovable's «Arvamuse tähtaeg»,
a lawyer taking it onto the Teema as an informational date, Lovable closing
that Teema, and both sides run again. The seams it holds: no duplicate
obligation, no false closure hold, Lovable's fields Lovable's, and the cycle's
outcomes (`app/core/nightly_outcome.py`) right at every step.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from django.utils import timezone

from app.core import nightly_outcome
from app.intelligence.enums import ImportantDateKind
from app.intelligence.models import MatterImportantDate
from app.legacy_import import lovable_sync  # noqa: F401 - marks this module as one that runs the sync (tests/conftest.py)
from app.legacy_import.lovable_sync_models import SyncOutcome, SyncRunStatus, SyncTrigger
from app.matters.closure_work import juristid_only_work
from app.register_watch.models import TargetOrigin, WatchSource, WatchTarget
from app.riigikogu import bills, cycle, invitations, selectors
from app.riigikogu.enums import RunStatus
from app.riigikogu.models import ExternalDeadline, Invitation, StageRun
from app.workflow.models import NextAction
from tests.lovable_contract.fake import ContractLovable
from tests.lovable_contract.fixtures import pilot_on  # noqa: F401 - fixture
from tests.lovable_contract.world import (
    AVA,
    NIGHT,
    UKU,
    build_world,
    lovable_row,
    ref,
    source_id,
)
from tests.test_riigikogu_programme import (  # noqa: F401 - fixture
    BILL,
    KODA_AMONG,
    api,
    with_letter,
)

pytestmark = pytest.mark.django_db

EMPTY_PAGE = {"_embedded": {"content": []}, "page": {"totalPages": 0, "number": 0}}
ON = {"RIIGIKOGU_MONITORING": True, "REGISTER_WATCH_FETCH": True, "RIIGIKOGU_DOCUMENT_AI": False}


def _night(n: int) -> dt.datetime:
    return NIGHT + dt.timedelta(days=n)


@pytest.fixture
def responsible_changes(monkeypatch):
    """The fake Lovable also serves `topic_responsible_change`, as Lovable's
    `handover_topics` writes it (tests/test_lovable_handover_limit.py)."""
    plain = ContractLovable.select

    def select(self: Any, table: str, columns: Any, *, filters: dict[str, str], order: str):
        if table == "topic_responsible_change":
            wanted = set(next(iter(filters.values()))[4:-1].split(","))
            rows = self.__dict__.get("responsible", [])
            return [{c: r.get(c) for c in columns} for r in rows if r["topic_id"] in wanted]
        return plain(self, table, columns, filters=filters, order=order)

    monkeypatch.setattr(ContractLovable, "select", select)


def _hand_over(world, reference: str, to: str) -> None:
    world.lovable.edit(reference, uus_vastutaja=to)
    world.lovable.__dict__.setdefault("responsible", []).append(
        {
            "id": f"rc-{reference}",
            "topic_id": source_id(reference),
            "vana_vastutaja": AVA,
            "uus_vastutaja": to,
            "alates": _night(1).date().isoformat(),
        }
    )


def _every_other_path_empty(api) -> None:
    """Stages with nothing to read tonight read an empty answer of their own shape
    (the readers refuse any other shape: a malformed answer is never «nothing new»)."""
    api.json["/api/events"] = []
    api.json["/api/steno/verbatims"] = []
    plain = api.get_json

    def get_json(host: str, path: str, params: dict | None = None, **_: Any) -> Any:
        if path in api.json or path in api.fail:
            return plain(host, path, params)
        api.calls.append((path, dict(params or {})))
        return EMPTY_PAGE

    api.get_json = get_json


def _lovable_outcome(run) -> nightly_outcome.StepOutcome:
    return nightly_outcome.classify("lovable", since=run.started_at - dt.timedelta(minutes=1), rc=0)


def _riigikogu_night(settings, today: dt.date) -> tuple[list[StageRun], Any]:
    for key, value in ON.items():
        setattr(settings, key, value)
    since = timezone.now() - dt.timedelta(seconds=1)
    runs = cycle.run(today=today)
    return runs, nightly_outcome.classify("riigikogu", since=since, rc=0)


def test_lovable_and_the_riigikogu_run_together_without_stepping_on_each_other(
    pilot_on, responsible_changes, api, monkeypatch, settings
):
    import app.register_watch.sources.http as http

    _every_other_path_empty(api)
    monkeypatch.setattr(http, "get_json", api.get_json)

    # -- night 1: the converged register (build_world's own first APPLY) --------
    world = build_world(3)
    teema, other = ref(1), ref(2)
    ava = world.person(AVA)

    # -- night 2: a new Teema, an ordinary change, a recorded hand-over --------
    new = ref(4)
    world.lovable.put(source_id(new), lovable_row(new), version=1)
    world.lovable.edit(teema, arvamuse_tahtaeg="2026-11-24")
    world.lovable.edit(other, jargmiseks="Koostan arvamuse", tegevuse_tahtaeg="2099-07-01")
    _hand_over(world, ref(3), UKU)
    second = world.run(trigger=SyncTrigger.SCHEDULED, at=_night(1))
    assert second.status == SyncRunStatus.SUCCEEDED, (second.status, second.error)
    assert second.counts["new_topics_created"] == 1
    assert world.matter(ref(3)).owner == world.person(UKU)
    assert _lovable_outcome(second).outcome == nightly_outcome.SUCCEEDED
    matter = world.matter(teema)
    assert matter.response_deadline == dt.date(2026, 11, 24)

    # -- the same night, the registers lane: an invitation on the Teema's bill --
    bill = bills.upsert(bills.facts(api.json[f"/api/volumes/drafts/{BILL}"])).bill
    bills.link(matter, bill, actor=ava)
    assert WatchTarget.objects.filter(
        matter=matter, source=WatchSource.RIIGIKOGU, origin=TargetOrigin.LINKED
    ).exists()
    with_letter(api, parties=KODA_AMONG)
    runs, outcome = _riigikogu_night(settings, dt.date(2026, 11, 12))
    reading = [r for r in runs if r.stage != "ANALYSIS"]
    assert all(r.status == RunStatus.SUCCEEDED for r in reading), [
        (r.stage, r.status, r.errors) for r in reading
    ]
    assert outcome.outcome == nightly_outcome.SUCCEEDED, outcome
    invitation = Invitation.objects.get()
    (row,) = selectors.invitation_rows(ava, [invitation])
    assert row.deadline.due_on == dt.date(2026, 11, 24)
    assert row.lovable == "same"

    # -- a lawyer takes it onto the Teema: informational, no work --------------
    actions_before = set(NextAction.objects.filter(matter=matter).values_list("pk", "status"))
    held_before = juristid_only_work(matter)
    record = invitations.add_as_date(invitation, matter, actor=ava)
    assert record.kind == ImportantDateKind.RK_OPINION_REQUEST
    assert set(NextAction.objects.filter(matter=matter).values_list("pk", "status")) == (
        actions_before
    )
    assert juristid_only_work(matter) == held_before
    assert not NextAction.objects.filter(matter=matter, target_date=record.date_value).exists()

    # -- night 3: Lovable moves the deadline (a conflict, shown, never resolved) --
    world.lovable.edit(teema, arvamuse_tahtaeg="2026-11-30")
    third = world.run(trigger=SyncTrigger.SCHEDULED, at=_night(2))
    assert third.status == SyncRunStatus.SUCCEEDED, third.error
    matter.refresh_from_db()
    assert matter.response_deadline == dt.date(2026, 11, 30)
    (row,) = selectors.invitation_rows(ava, [invitation])
    assert (row.lovable, row.lovable_date) == ("differs", dt.date(2026, 11, 30))
    assert ExternalDeadline.objects.get(
        document=invitation.document, kind="OPINION_REQUEST", state="ACTIVE"
    ).due_on == (dt.date(2026, 11, 24))

    # -- night 4: Lovable closes the Teema; the Riigikogu date holds nothing -----
    world.lovable.edit(teema, hetkeseis="jõustunud", jargmiseks="", tegevuse_tahtaeg=None)
    fourth = world.run(trigger=SyncTrigger.SCHEDULED, at=_night(3))
    assert fourth.status == SyncRunStatus.SUCCEEDED, fourth.error
    matter.refresh_from_db()
    assert not matter.is_open
    assert not fourth.decisions.filter(
        state__matter=matter,
        outcome__in=[SyncOutcome.REVIEW_REQUIRED, SyncOutcome.CONFLICT, SyncOutcome.FAILED],
    ).exists()
    assert (
        MatterImportantDate.objects.filter(
            matter=matter, kind=ImportantDateKind.RK_OPINION_REQUEST
        ).count()
        == 1
    )

    # -- both sides again: nothing new, nothing doubled ---------------------------
    fifth = world.run(trigger=SyncTrigger.SCHEDULED, at=_night(4))
    assert not fifth.decisions.filter(outcome=SyncOutcome.APPLIED).exists()
    assert _lovable_outcome(fifth).outcome == nightly_outcome.SUCCEEDED_NO_NEW
    _runs, again = _riigikogu_night(settings, dt.date(2026, 11, 13))
    assert again.outcome == nightly_outcome.SUCCEEDED_NO_NEW, again
    assert Invitation.objects.count() == 1
    assert (
        ExternalDeadline.objects.filter(
            document=invitation.document, kind="OPINION_REQUEST"
        ).count()
        == 1
    )
    assert MatterImportantDate.objects.filter(matter=matter, kind__startswith="RK_").count() == 1
    assert world.lovable.writes == []
