"""Synthetic builders for the Lovable observability tests (docs/adr/0201).

Every title, name, number and link here is invented. A pilot Teema is built
directly through the ORM — a Matter, its open step and a `LovableSyncState`
seeded as `lovable_sync.seed_state` would seed it — and a night is the real
reconciliation (`lovable_sync.plan_matter`, which is pure) recorded the way
`run_sync` records it, over a snapshot document that verifies. The wider
observation's runs, changes and audit events are rows made directly.

None of this reaches Lovable: there is no transport anywhere in it.
"""

from __future__ import annotations

import copy
import datetime as dt
import uuid
from typing import Any

from django.apps import apps

from app.core.enums import Visibility
from app.legacy_import import lovable_sync
from app.legacy_import.integrity import canonical_sha256
from app.legacy_import.lovable_observation_models import (
    AuditAccess,
    LovableObservationRun,
    LovableObservedChange,
    LovableSourceEvent,
    LovableSourceEventField,
    MappingStatus,
    ObservationRunStatus,
    ObservedChangeKind,
)
from app.legacy_import.lovable_source import (
    LOVABLE_PROJECT,
    SNAPSHOT_FORMAT,
    SNAPSHOT_FORMAT_VERSION,
)
from app.legacy_import.lovable_sync_models import (
    LovableSyncControl,
    LovableSyncRun,
    LovableSyncSnapshot,
    LovableSyncState,
    SyncMode,
    SyncOutcome,
    SyncRunStatus,
    SyncTrigger,
)
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics
from tests import factories

#: 00:15 in Tallinn (winter): the nightly firing.
NIGHT = dt.datetime(2026, 10, 27, 22, 15, tzinfo=dt.UTC)
DAY = dt.timedelta(days=1)


def night_at(n: int) -> dt.datetime:
    return NIGHT + n * DAY


def control(mode: str = SyncMode.COMPARE, *, observe: bool = False) -> LovableSyncControl:
    row, _ = LovableSyncControl.objects.update_or_create(
        pk=1, defaults={"mode": mode, "observe": observe}
    )
    return row


def source_row(reference: str, **values: Any) -> dict[str, Any]:
    year, _, number = reference.partition("_")
    row = {
        "id": str(uuid.uuid4()),
        "nr": reference,
        "aasta": int(year),
        "teema": f"Näidisteema {reference}",
        "oigusakt": "",
        "sisse": "2026-09-01",
        "arvamuse_tahtaeg": None,
        "valja": "",
        "valja_kuupaev": None,
        "vastutaja": "Mari Näidis",
        "uus_vastutaja": "",
        "hetkeseis": "Ministeeriumis",
        "jargmiseks": "Koostada arvamus",
        "koja_ettepanek": False,
        "kellele": "",
        "tegevuse_tahtaeg": "2026-11-20",
        "onenote_link": "",
        "operatiivne_ulatus": True,
        "version": 1,
        "updated_at": "2026-10-01T10:00:00+00:00",
    }
    row.update(values)
    del number
    return row


class Pilot:
    """A synthetic pilot cohort and its source, for direct-ORM tests."""

    def __init__(self, owner: Any | None = None) -> None:
        self.owner = owner or factories.UserFactory(display_name="Mari Näidis")
        self.bundles: dict[str, dict[str, Any]] = {}  # by reference
        self.others: list[dict[str, Any]] = []  # index rows outside the pilot
        self.states: dict[str, LovableSyncState] = {}

    # -- the cohort ---------------------------------------------------------

    def add(
        self,
        number: int,
        *,
        year: int = 2026,
        visibility: str = Visibility.NORMAL,
        treatment: str = "SOURCE_NEXT_STEP",
        owner_target: str | None = None,
        **values: Any,
    ) -> LovableSyncState:
        reference = f"{year}_{number}"
        row = source_row(reference, **values)
        bundle = {"row": row, "commencements": [], "responsible_changes": [], "stage_links": []}
        matter = factories.MatterFactory(
            reference_year=year,
            reference_number=number,
            title=row["teema"],
            owner=self.owner,
            visibility=visibility,
        )
        step = factories.NextActionFactory(
            matter=matter,
            text=row["jargmiseks"],
            kind=ActionKind.DO,
            date_semantics=DateSemantics.DEADLINE,
            target_date=dt.date.fromisoformat(row["tegevuse_tahtaeg"]),
            date_precision=DatePrecision.EXACT,
        )
        source = lovable_sync.canonical_source(bundle)
        applied: dict[str, Any] = {key: {"source": value} for key, value in source.items()}
        applied["title"]["target"] = lovable_sync.text_value(matter.title)
        applied["owner"]["target"] = owner_target or str(self.owner.pk)
        applied["onenote_link"]["target"] = ""
        applied["next_action"] = {
            "target": lovable_sync._action_dict(step),
            "treatment": treatment,
        }
        state = LovableSyncState.objects.create(
            matter=matter,
            source_id=row["id"],
            reference=reference,
            applied=applied,
            applied_version=1,
            observed=source,
        )
        self.bundles[reference] = bundle
        self.states[reference] = state
        return state

    def edit(self, reference: str, *, commencements: Any = None, **values: Any) -> None:
        bundle = self.bundles[reference]
        bundle["row"].update(values)
        bundle["row"]["version"] = int(bundle["row"].get("version") or 1) + 1
        if commencements is not None:
            bundle["commencements"] = commencements

    def outside(self, reference: str, *, operational: bool = True) -> dict[str, Any]:
        """A live topic outside the pilot (only in the index)."""
        row = source_row(reference, operatiivne_ulatus=operational)
        self.others.append(row)
        return row

    def matter(self, reference: str) -> Any:
        from app.matters.models import Matter

        return Matter.all_objects.get(pk=self.states[reference].matter_id)

    # -- a night ---------------------------------------------------------------

    def document(self, at: dt.datetime) -> dict[str, Any]:
        index = [
            {
                "id": b["row"]["id"],
                "nr": b["row"]["nr"],
                "aasta": b["row"]["aasta"],
                "version": b["row"]["version"],
                "operatiivne_ulatus": b["row"]["operatiivne_ulatus"],
            }
            for b in list(self.bundles.values())
        ] + [
            {
                "id": r["id"],
                "nr": r["nr"],
                "aasta": r["aasta"],
                "version": r["version"],
                "operatiivne_ulatus": r["operatiivne_ulatus"],
            }
            for r in self.others
        ]
        document = {
            "format": SNAPSHOT_FORMAT,
            "format_version": SNAPSHOT_FORMAT_VERSION,
            "project": LOVABLE_PROJECT,
            "taken_at": at.isoformat(),
            "mapping_version": lovable_sync.MAPPING_VERSION,
            "attempts": 1,
            "index": sorted(index, key=lambda r: r["id"]),
            "cohort": {b["row"]["id"]: copy.deepcopy(b) for b in self.bundles.values()},
        }
        document["sha256"] = canonical_sha256(document)
        return document

    def night(
        self,
        at: dt.datetime,
        *,
        trigger: str = SyncTrigger.SCHEDULED,
        mode: str = SyncMode.COMPARE,
        fail: str = "",
    ) -> LovableSyncRun:
        """One run as `run_sync` records it, in COMPARE: snapshot, decisions,
        observation and converged baselines. ``fail`` = the source read failed."""
        run = LovableSyncRun.objects.create(
            run_key=lovable_sync.run_key_for(trigger, at),
            trigger=trigger,
            mode=mode,
            started_at=at,
            mapping_version=lovable_sync.MAPPING_VERSION,
        )
        if fail:
            run.status = SyncRunStatus.FAILED
            run.error = fail
            run.finished_at = at + dt.timedelta(minutes=1)
            run.save()
            return run
        document = self.document(at)
        run.snapshot_sha256 = document["sha256"]
        run.snapshot_taken_at = at
        LovableSyncSnapshot.objects.create(
            run=run, taken_at=at, sha256=document["sha256"], document=document
        )
        states = list(LovableSyncState.objects.select_related("matter").order_by("reference"))
        resolver = lovable_sync.OwnerResolver(states)
        today = at.astimezone(lovable_sync.TALLINN).date()
        counts = lovable_sync.RunCounts()
        for state in states:
            plan = lovable_sync.plan_matter(
                state, document["cohort"].get(str(state.source_id)), resolver=resolver, today=today
            )
            applied = dict(state.applied)
            for decision in plan.decisions:
                if decision.outcome == SyncOutcome.ALREADY_CONVERGED:
                    lovable_sync._advance(applied, decision)
            if applied != state.applied:
                LovableSyncState.objects.filter(pk=state.pk).update(applied=applied)
            counts.add(plan)
            lovable_sync._record(run, plan)
            if not plan.status:
                lovable_sync._observe(LovableSyncState.objects.get(pk=state.pk), plan, run)
        lovable_sync._observe_index(counts, document, {str(s.source_id) for s in states}, None)
        run.counts = counts.as_dict()
        run.status = SyncRunStatus.SUCCEEDED
        run.verified = True
        run.finished_at = at + dt.timedelta(minutes=1)
        run.save()
        for reference, state in self.states.items():
            self.states[reference] = LovableSyncState.objects.get(pk=state.pk)
        return run


# ---------------------------------------------------------------------------
# The wider observation
# ---------------------------------------------------------------------------


def observation_run(
    at: dt.datetime,
    *,
    status: str = ObservationRunStatus.SUCCEEDED,
    audit: str = AuditAccess.DISABLED,
    audit_error: str = "",
    baseline: LovableObservationRun | None = None,
    counts: dict[str, Any] | None = None,
    error: str = "",
) -> LovableObservationRun:
    return LovableObservationRun.objects.create(
        run_key=f"obs-{at:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}",
        trigger=SyncTrigger.SCHEDULED,
        status=status,
        started_at=at,
        finished_at=at + dt.timedelta(minutes=2),
        taken_at=at if status == ObservationRunStatus.SUCCEEDED else None,
        sha256="a" * 64 if status == ObservationRunStatus.SUCCEEDED else "",
        baseline=baseline,
        counts=counts if counts is not None else {"live": 400, "operational": 150, "detailed": 160},
        audit_access=audit,
        audit_error=audit_error,
        error=error,
    )


def observed(
    run: LovableObservationRun,
    reference: str,
    *,
    kind: str = ObservedChangeKind.CHANGED,
    mapping: str = MappingStatus.UNMATCHED,
    matter: Any = None,
    source_id: Any = None,
    in_pilot: bool = False,
    operational: bool = True,
    scope_changed: bool = False,
    fields: list[tuple[str, Any, Any]] | None = None,
    field_names: list[str] | None = None,
    previous_reference: str = "",
) -> LovableObservedChange:
    values = [{"field": n, "before": b, "after": a} for n, b, a in (fields or [])]
    return LovableObservedChange.objects.create(
        run=run,
        source_id=source_id or uuid.uuid4(),
        reference=reference,
        previous_reference=previous_reference,
        kind=kind,
        in_pilot=in_pilot,
        operational=operational,
        scope_changed=scope_changed,
        detailed=bool(values),
        field_names=field_names if field_names is not None else [v["field"] for v in values],
        fields=values,
        mapping=mapping,
        matter=matter,
    )


def source_event(
    topic_id: Any,
    at: dt.datetime,
    *,
    fields: list[tuple[str, str, str, bool]],
    action: str = "muutmine",
    run: LovableObservationRun | None = None,
) -> LovableSourceEvent:
    event = LovableSourceEvent.objects.create(
        source_event_id=uuid.uuid4().hex,
        topic_id=topic_id,
        action=action,
        actor_kind="kasutaja",
        version=2,
        occurred_at=at,
        run=run,
        sha256="b" * 64,
    )
    for name, old, new, withheld in fields:
        LovableSourceEventField.objects.create(
            event=event,
            field=name,
            old_value="" if withheld else old,
            new_value="" if withheld else new,
            withheld=withheld,
        )
    return event


# ---------------------------------------------------------------------------
# Who reads
# ---------------------------------------------------------------------------


def narrowed_reader(monkeypatch: Any) -> Any:
    """A business reader whose Matter scope is narrowed to NORMAL Teemad.

    Since docs/adr/0181 every real business reader sees every Teema, so no real
    role exercises the «may not open» branch any more; the builders still
    narrow a hand-built scope (`Scope` with ``sees_restricted_by_role=False``),
    and that is what this reader gets — so the RESTRICTED pilot Teema is one it
    may not open, exactly as `Matter.objects.visible_to` would say if a
    narrower policy ever returned."""
    from app.core import authorization
    from app.matters import models as matter_models

    reader = factories.UserFactory(display_name="Kitsendatud lugeja")
    real = authorization.scope_for_user

    def scope(user: Any) -> Any:
        if getattr(user, "pk", None) == reader.pk:
            return authorization.Scope(
                user=user,
                is_authenticated=True,
                sees_restricted_by_role=False,
                break_glass_grant_id=None,
            )
        return real(user)

    monkeypatch.setattr(matter_models, "scope_for_user", scope)
    return reader


def row_counts() -> dict[str, int]:
    """Rows of every legacy_import model and of the business tables a Lovable
    change must never reach."""
    from app.matters.models import Matter
    from app.submissions.models import Submission
    from app.workflow.models import NextAction

    counts = {
        model._meta.label: model._default_manager.count()
        for model in apps.get_app_config("legacy_import").get_models()
    }
    counts["Matter(all)"] = Matter.all_objects.count()
    counts["Submission"] = Submission.objects.count()
    counts["NextAction"] = NextAction.objects.count()
    return counts


def write_statements(queries: Any) -> list[str]:
    return [
        q["sql"]
        for q in queries
        if q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]
