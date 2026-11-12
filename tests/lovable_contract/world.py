"""A synthetic two-sided world for the Lovable↔Juristid contract (audit §D.2).

* **Lovable's side** is a `ContractLovable`: invented rows, changed only the way
  Lovable's own write layer allows (`fake.py`).
* **Juristid's side** is made only through the real services: the Teemad are
  written by the Lovable importer itself (`lovable_pilot.build_plan` /
  `apply_plan`, the path production's register was built by) and enrolled in
  the register cohort by `lovable_sync.enroll`; a scenario's local edits go
  through the domain services a lawyer's edit goes through.
* **The run** is `World.run(mode=APPLY|COMPARE, **breakers)`: the control row is
  set the way `lovable_sync control --mode` sets it, then the real entry point
  `lovable_sync.run_sync` is called exactly as `manage.py lovable_sync run`
  (which `scripts/deploy/juristid-lovable-sync.sh` runs) calls it — with a
  backup set taken five minutes before, the run's own clock and the one-run
  breaker overrides (`--max-changed`, `--max-restored`).

Topic numbers use the reserved year 2099 (`YEAR`); names are invented and end
in «Näidis» (guard I-7, `test_contract_invariants.py`).
"""

from __future__ import annotations

import copy
import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from app.legacy_import import lovable_pilot, lovable_sync
from app.legacy_import.lovable_pilot import PILOT, REGISTER_COHORT, apply_plan, build_plan
from app.legacy_import.lovable_sync_models import LovableSyncRun, SyncMode, SyncTrigger
from app.legacy_import.models import EvidencePilotRecord
from app.matters.models import Matter
from tests import factories
from tests.lovable_contract.fake import ContractLovable

TALLINN = lovable_sync.TALLINN
#: The first night of every scenario; each later run is an hour on (`World.run`).
NIGHT = dt.datetime(2026, 11, 12, 0, 15, tzinfo=TALLINN)
TODAY = NIGHT.date()
HOUR = dt.timedelta(hours=1)

#: The reserved synthetic year (audit §D.1 rule 3).
YEAR = 2099
#: The two invented people, by the name Lovable writes (their display name).
AVA = "Ava Näidis"
UKU = "Uku Näidis"
PEOPLE = (AVA, UKU)
LINK = "https://example.invalid/sites/naidis/_layouts/Doc.aspx?sourcedoc={CCCC}"
STEP_TEXT = "Ootan 2. lugemist"
STEP_DAY = "2099-06-01"


def ref(number: int, year: int = YEAR) -> str:
    return f"{year}_{number}"


def source_id(reference: str) -> str:
    """An invented Lovable id that says which synthetic topic it is."""
    year, number = reference.split("_")
    return f"00000000-0000-0000-{int(year):04d}-{int(number):012d}"


def lovable_row(reference: str, **values: Any) -> dict[str, Any]:
    """A Lovable `topic` row as the synchronisation reads it."""
    return {
        "nr": reference,
        "aasta": int(reference[:4]),
        "teema": f"Kontraktiteema {reference}",
        "vastutaja": AVA,
        "uus_vastutaja": "",
        "hetkeseis": "Riigikogus",
        "jargmiseks": STEP_TEXT,
        "tegevuse_tahtaeg": STEP_DAY,
        "arvamuse_tahtaeg": None,
        "valja": "",
        "valja_kuupaev": None,
        "kellele": "",
        "koja_ettepanek": False,
        "oigusakt": "seadus",
        "sisse": "2026-01-05",
        "onenote_link": LINK,
        "operatiivne_ulatus": True,
        **values,
    }


def _step(text: str, day: str) -> dict[str, Any]:
    kind = "DO" if text.startswith(("Koostan", "Saadan")) else "WAIT"
    return {
        "review": "READY",
        "kind": kind,
        "date_semantics": "DEADLINE" if kind == "DO" else "REVIEW_ON",
        "target_date": day,
        "date_precision": "EXACT",
        "text": text,
        "source_text": text,
        "treatment": "SOURCE_NEXT_STEP",
        "reason": kind,
    }


def _package_topic(reference: str, row: dict[str, Any], *, closed: bool) -> dict[str, Any]:
    """The importer's reviewed topic for ``row``: what the register held before."""
    topic: dict[str, Any] = {
        "reference": reference,
        "group": "A",
        "title": row["teema"],
        "owner_raw": row["vastutaja"],
        "status_raw": row["hetkeseis"],
        "stage": "parliament",
        "lifecycle": "ACTIVE",
        "disposition": "",
        "successor": "",
        "koda_initiative": False,
        "instruments": ["seadus"],
        "received_date": row["sisse"],
        "addressee_raw": "",
        "next_action": _step(row["jargmiseks"], row["tegevuse_tahtaeg"]),
        "deadline": {"treatment": "NONE"},
        "commencements": [],
        "onenote_url": row["onenote_link"],
        "field_decisions": {"jargmiseks": "APPLIED", "valja": "PRESERVED_AS_SOURCE_ONLY"},
        "review_notes": [],
        "source": {
            "id": source_id(reference),
            "version": 3,
            "updated_at": "2026-10-20T10:00:00+00:00",
            "row": row,
            "excel_source_row": None,
            "history": [
                {"occurred_at": "2026-09-01T08:00:00+00:00", "action": "muutmine", "fields": []}
            ],
            "commencements": [],
            "responsible_changes": [],
        },
    }
    if closed:
        topic.update(
            status_raw="jõustunud",
            stage="in_force",
            lifecycle="INACTIVE",
            disposition="COMPLETED",
            next_action=None,
        )
    return topic


def _package(topics: list[dict[str, Any]], highest: int) -> dict[str, Any]:
    return {
        "format": lovable_pilot.PACKAGE_FORMAT,
        "format_version": lovable_pilot.PACKAGE_FORMAT_VERSION,
        "reviewer": "näidisülevaataja",
        "snapshot": {
            "as_of": "2026-10-26T17:40:16+00:00",
            "snapshot_sha256": "c" * 64,
            "project": "näidis",
            "highest_reference": {str(YEAR): highest},
        },
        "people": {name: name for name in PEOPLE},
        "reserve_through": {str(YEAR): highest},
        "expected_topics": len(topics),
        "topics": topics,
    }


@dataclass
class World:
    """Both sides of one scenario, and the runs made over them."""

    lovable: ContractLovable
    people: dict[str, Any]
    references: list[str]
    runs: list[LovableSyncRun] = field(default_factory=list)
    #: Records a scenario made on the Juristid side only (LC-J1), by kind.
    juristid_only: dict[str, list[Any]] = field(default_factory=dict)

    def run(
        self,
        mode: str = SyncMode.APPLY,
        *,
        trigger: str = SyncTrigger.MANUAL,
        backup: bool = True,
        **breakers: Any,
    ) -> LovableSyncRun:
        """One run of the real entry point, as the host script makes it."""
        lovable_sync.set_mode(mode, reason="kontraktitest")
        moment = NIGHT + len(self.runs) * HOUR
        backup_set = (
            (moment - dt.timedelta(minutes=5)).astimezone(dt.UTC).strftime("sets/%Y%m%dT%H%M%SZ")
            if backup
            else ""
        )
        run = lovable_sync.run_sync(
            trigger=trigger,
            transport=self.lovable,
            backup_set=backup_set,
            now=lambda: moment,
            pause=lambda _seconds: None,
            max_changed=breakers.pop("max_changed", None),
            max_restored=breakers.pop("max_restored", None),
        )
        assert not breakers, f"unknown breaker override(s): {sorted(breakers)}"
        run.refresh_from_db()
        self.runs.append(run)
        return run

    @property
    def today(self) -> dt.date:
        return (NIGHT + len(self.runs) * HOUR).date()

    def matter(self, reference: str) -> Matter:
        year, number = reference.split("_")
        return Matter.all_objects.get(reference_year=int(year), reference_number=int(number))

    def person(self, name: str) -> Any:
        return self.people[name]


#: The synchronisation's own bookkeeping (LC-F3): what a run may write in any
#: mode. Every other table is business data, which a COMPARE run never changes.
#: Named one by one, so a new table is business data until someone decides.
BOOKKEEPING_TABLES = frozenset(
    {
        "legacy_import.lovablesyncrun",
        "legacy_import.lovablesyncdecision",
        "legacy_import.lovablesyncsnapshot",
        "legacy_import.lovablesyncstate",
        "legacy_import.lovablesynccontrol",
        "legacy_import.lovablesyncnewtopic",
    }
)


def table_hashes(only: Any = None) -> dict[str, str]:
    """A digest of every concrete table's rows (``app_label.model``), by primary key.

    The r54 production check D made a test: a table whose digest is unchanged
    was not written. ``only`` narrows it to some ``app_label.model`` labels."""
    import hashlib

    from django.apps import apps

    out: dict[str, str] = {}
    for model in apps.get_models(include_auto_created=True):
        meta = model._meta
        if meta.proxy or not meta.managed:
            continue
        label = meta.label_lower
        if only is not None and label not in only:
            continue
        manager = model._base_manager
        digest = hashlib.sha256()
        for row in manager.order_by("pk").values_list():
            digest.update(repr(row).encode())
        out[label] = digest.hexdigest()
    return out


def changed_tables(before: dict[str, str], after: dict[str, str]) -> set[str]:
    return {label for label in after if before.get(label) != after[label]}


def decisions(run: LovableSyncRun, reference: str) -> dict[str, Any]:
    return {d.field: d for d in run.decisions.filter(state__reference=reference)}


def build_world(
    count: int = 3,
    *,
    closed: tuple[int, ...] = (),
    rows: dict[int, dict[str, Any]] | None = None,
    baseline: bool = True,
) -> World:
    """``count`` register Teemad ``2099_1..count`` on both sides, enrolled, in APPLY.

    ``closed`` numbers are «jõustunud» on both sides; ``rows`` overrides a
    topic's Lovable row before the import (both sides agree on it). With
    ``baseline`` the world's first APPLY run is made, so every scenario starts
    from a converged register and its own run is the second."""
    people = {name: factories.UserFactory(display_name=name) for name in PEOPLE}
    topics = []
    lovable = ContractLovable()
    for number in range(1, count + 1):
        reference = ref(number)
        is_closed = number in closed
        row = lovable_row(reference, **(rows or {}).get(number, {}))
        if is_closed:
            row.update(hetkeseis="jõustunud", jargmiseks="", tegevuse_tahtaeg=None)
        topic = _package_topic(reference, row, closed=is_closed)
        topics.append(topic)
        lovable.put(source_id(reference), copy.deepcopy(row), version=3)
    package = _package(topics, highest=count + 100)
    plan = build_plan(package)
    apply_plan(
        plan,
        operator_intent=lovable_pilot.OPERATOR_INTENT,
        expect_package_sha256=plan.digest,
        expect_plan_sha256=lovable_pilot.plan_digest(plan),
        backup_set="sets/20261026T160000Z",
    )
    # The import's provenance names the register cohort, as the r44 replacement's
    # did (tests/test_ls_b_engine.py builds its register the same way).
    EvidencePilotRecord.objects.filter(package_sha256=plan.digest).update(pilot=REGISTER_COHORT)
    lovable_sync.enroll(
        cohort=REGISTER_COHORT,
        package_sha256=plan.digest,
        expect=len(topics),
        reason="Lovable'i register (kontraktimaailm)",
        replaces=[PILOT],
    )
    world = World(lovable=lovable, people=people, references=[t["reference"] for t in topics])
    if baseline:
        first = world.run()
        assert first.status == "SUCCEEDED", (first.status, first.error)
    return world
