"""Where the r93 components meet (integration branch only).

The Lovable synchronisation (#725, ADR 0213), the coordinated nightly cycle (#726,
ADR 0229), the redesigned Haldus (#721, ADR 0221) and the Riigikogu programme (#723,
ADR 0224–0228) are each tested on their own branch. These tests hold the seams
between them: a Riigikogu date is never something Lovable's synchronisation reads,
compares or counts towards its nightly limit; Haldus → Ülevaade reports the Riigikogu
cycle only once it is switched on; and the nightly cycle has no Riigikogu line of its
own until the owner activates it.
"""

from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path

import pytest
from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from app.intelligence.enums import (
    INFORMATIONAL_IMPORTANT_DATE_KINDS,
    PARLIAMENTARY_IMPORTANT_DATE_KINDS,
    ImportantDateKind,
)
from app.intelligence.services import add_important_date
from app.workflow.enums import DatePrecision
from tests import factories

ROOT = Path(settings.BASE_DIR)


def _riigikogu_date(matter, actor):
    return add_important_date(
        matter=matter,
        title="Arvamuse tähtaeg: Majanduskomisjon",
        date_value=dt.date(2030, 1, 15),
        period_end=dt.date(2030, 1, 15),
        date_precision=DatePrecision.EXACT,
        kind=ImportantDateKind.RK_OPINION_REQUEST,
        note="Riigikogu kutse kojale.",
        actor=actor,
        source_text="Palume arvamust 15. jaanuariks 2030.",
    )


# ---------------------------------------------------------------------------
# Lovable (#725) × Riigikogu (#723)
# ---------------------------------------------------------------------------


def test_no_parliamentary_kind_is_a_kind_the_lovable_synchronisation_owns():
    from app.legacy_import.lovable_sync import COMMENCEMENT_IMPORTANT_KINDS

    assert not set(COMMENCEMENT_IMPORTANT_KINDS) & set(PARLIAMENTARY_IMPORTANT_DATE_KINDS)
    assert set(PARLIAMENTARY_IMPORTANT_DATE_KINDS) <= set(INFORMATIONAL_IMPORTANT_DATE_KINDS)


@pytest.mark.django_db
def test_a_riigikogu_date_is_neither_compared_nor_counted_by_the_lovable_sync(specialist):
    """Lovable's nightly change count (limit 100, #725) reads these two readers; a
    Riigikogu date taken onto a Teema must not appear in either, or every such date
    would look like a Lovable-side difference and spend the night's limit."""
    from app.legacy_import.lovable_sync import (
        _lovable_commencement_records_bulk,
        conditional_values_bulk,
    )

    matter = factories.MatterFactory(owner=specialist)
    before = conditional_values_bulk([matter])
    _riigikogu_date(matter, specialist)
    assert conditional_values_bulk([matter]) == before
    assert _lovable_commencement_records_bulk([matter.pk]) == {}


@pytest.mark.django_db
def test_a_riigikogu_date_leaves_every_lovable_authoritative_field_alone(specialist):
    from app.legacy_import.lovable_sync import AUTHORITATIVE_FIELDS
    from app.matters.models import Matter

    matter = factories.MatterFactory(owner=specialist)
    fields = [f for f in AUTHORITATIVE_FIELDS if hasattr(Matter, f)]
    assert fields, "AUTHORITATIVE_FIELDS no longer names Matter fields; update this test"
    before = Matter.objects.values(*fields).get(pk=matter.pk)
    _riigikogu_date(matter, specialist)
    assert Matter.objects.values(*fields).get(pk=matter.pk) == before


# ---------------------------------------------------------------------------
# Haldus → Ülevaade (#721) × Riigikogu (#723)
# ---------------------------------------------------------------------------


def _stage(stage, status, *, finished=True, counts=None, since=None, cycle=None):
    from app.riigikogu.models import StageRun

    now = since or timezone.now()
    return StageRun.objects.create(
        cycle=cycle or uuid.uuid4(),
        stage=stage,
        status=status,
        started_at=now,
        finished_at=now if finished else None,
        window_end=now.date(),
        counts=counts or {},
    )


def _night(failed=None, *, counts=None, running=None, skip=(), since=None, analysis=None):
    """One cycle: every reading stage once, SUCCEEDED except the ones named."""
    from app.riigikogu.enums import RunStatus, Stage

    cycle = uuid.uuid4()
    for stage in Stage.values:
        if stage in skip:
            continue
        if stage == Stage.ANALYSIS:
            _stage(stage, analysis or RunStatus.DISABLED, cycle=cycle, since=since)
        elif stage == failed:
            _stage(stage, RunStatus.FAILED, cycle=cycle, since=since)
        elif stage == running:
            _stage(stage, RunStatus.RUNNING, finished=False, cycle=cycle, since=since)
        else:
            _stage(stage, RunStatus.SUCCEEDED, counts=counts, cycle=cycle, since=since)
    return cycle


def _failed_stage():
    _night(failed="INVITATIONS")


@pytest.mark.django_db
@override_settings(RIIGIKOGU_MONITORING=False)
def test_overview_says_nothing_about_a_switched_off_riigikogu_cycle():
    from app.haldus.overview import _riigikogu_problem

    _failed_stage()
    assert _riigikogu_problem() is None


@pytest.mark.django_db
@override_settings(RIIGIKOGU_MONITORING=True, REGISTER_WATCH_FETCH=True)
def test_overview_names_the_failed_riigikogu_stage_once_the_cycle_is_on():
    from app.haldus.overview import _riigikogu_problem

    _failed_stage()
    problem = _riigikogu_problem()
    assert problem is not None
    assert problem.startswith("Riigikogu · Kutsed kojale: ")


# ---------------------------------------------------------------------------
# The nightly cycle (#726) × Riigikogu (#723)
# ---------------------------------------------------------------------------


def test_the_riigikogu_cycle_has_no_schedule_of_its_own():
    """One coordinated nightly cycle (owner, 25.11.2026; ADR 0229): no cron file or
    example installs `watch_riigikogu` beside it, and the cycle script runs it only
    behind its opt-in `--with-riigikogu`, which no cron line passes."""
    cron = ROOT / "deploy" / "unraid-main" / "cron"
    for path in cron.glob("*"):
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.lstrip().startswith("#"):
                    continue
                assert "watch_riigikogu" not in line, f"{path.name}: {line}"
                assert "--with-riigikogu" not in line, f"{path.name}: {line}"
    script = (ROOT / "scripts" / "deploy" / "juristid-nightly-cycle.sh").read_text(encoding="utf-8")
    assert "WITH_RIIGIKOGU=0" in script


# ---------------------------------------------------------------------------
# The nightly cycle's outcome for the Riigikogu step, from `StageRun` (#726 × #723)
# ---------------------------------------------------------------------------


def _outcome(rc=0):
    from app.core import nightly_outcome

    since = timezone.now() - dt.timedelta(minutes=5)
    return nightly_outcome.classify("riigikogu", since=since, rc=rc)


@pytest.mark.django_db
@override_settings(RIIGIKOGU_MONITORING=False)
def test_a_switched_off_riigikogu_cycle_is_a_deliberate_skip():
    assert _outcome().outcome == "SKIPPED_DELIBERATE"


@pytest.mark.django_db
@override_settings(RIIGIKOGU_MONITORING=True, REGISTER_WATCH_FETCH=True)
def test_exit_zero_with_a_failed_stage_is_partial_not_success():
    _night(failed="AGENDAS")
    result = _outcome(rc=0)
    assert result.outcome == "PARTIAL"
    assert result.counts["failed"] == 1


@pytest.mark.django_db
@override_settings(RIIGIKOGU_MONITORING=True, REGISTER_WATCH_FETCH=True)
def test_a_stage_that_never_finished_is_an_interrupted_night():
    _night(running="PROTOCOLS")
    assert _outcome().outcome == "INTERRUPTED"


@pytest.mark.django_db
@override_settings(RIIGIKOGU_MONITORING=True, REGISTER_WATCH_FETCH=True)
def test_a_clean_night_with_nothing_new_is_succeeded_no_new():
    _night(counts={"seen": 4, "invitations_new": 0})
    assert _outcome().outcome == "SUCCEEDED_NO_NEW"


@pytest.mark.django_db
@override_settings(RIIGIKOGU_MONITORING=True, REGISTER_WATCH_FETCH=True)
def test_a_night_that_recorded_a_new_invitation_succeeded():
    _night(counts={"seen": 4, "invitations_new": 1})
    result = _outcome()
    assert result.outcome == "SUCCEEDED"
    assert result.counts["new"] >= 1


@pytest.mark.django_db
@override_settings(RIIGIKOGU_MONITORING=True, REGISTER_WATCH_FETCH=True)
def test_no_record_is_an_unexpected_skip_and_a_held_lock_says_so():
    assert _outcome(rc=0).outcome == "SKIPPED_UNEXPECTED"
    held = _outcome(rc=1)
    assert held.outcome == "SKIPPED_UNEXPECTED"
    assert "lock" in held.reason
    assert _outcome(rc=2).outcome == "FAILED"


def test_adr_numbers_0221_to_0229_each_have_exactly_one_record():
    adr = ROOT / "docs" / "adr"
    for number in range(221, 230):
        found = sorted(adr.glob(f"{number:04d}-*.md"))
        assert len(found) == 1, (number, [p.name for p in found])


# ---------------------------------------------------------------------------
# A page a persona may not open, on a phone (#721 text sizes × Monitooring)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_production_not_found_is_the_applications_own_phone_ready_page(client, specialist):
    """The 375px overflow reported on `/monitooring/` was Django's DEBUG 404 page
    (`table.meta`, no viewport). Without DEBUG a refused or missing page is
    `templates/404.html`, inside the application's own layout."""
    client.force_login(specialist)
    response = client.get("/monitooring/olematu-leht/")
    assert response.status_code == 404
    html = response.content.decode()
    assert 'name="viewport"' in html
    assert 'class="meta"' not in html


@pytest.mark.django_db
@override_settings(RIIGIKOGU_MONITORING=True, REGISTER_WATCH_FETCH=True)
def test_a_cycle_that_stopped_before_its_last_stages_is_interrupted():
    """Every stage a nightly cycle runs must be recorded: a cycle killed after three
    stages left no row for the rest, and «no failed row» is not success."""
    _night(skip=("PROTOCOLS", "STENOGRAMS", "RESPONSES"))
    result = _outcome()
    assert result.outcome == "INTERRUPTED"
    assert "PROTOCOLS" in result.reason and result.counts["missing"] == 3


@pytest.mark.django_db
@override_settings(RIIGIKOGU_MONITORING=True, REGISTER_WATCH_FETCH=True)
def test_only_the_steps_own_cycle_is_judged_not_older_rows():
    """A failed stage of an earlier cycle (started before the step) does not spoil
    tonight; tonight's own missing stage is not covered by an earlier success."""
    earlier = timezone.now() - dt.timedelta(hours=2)
    _night(failed="AGENDAS", since=earlier)
    _night(counts={"invitations_new": 1})
    assert _outcome().outcome == "SUCCEEDED"


@pytest.mark.django_db
@override_settings(
    RIIGIKOGU_MONITORING=True, REGISTER_WATCH_FETCH=True, RIIGIKOGU_DOCUMENT_AI=False
)
def test_document_analysis_running_while_switched_off_is_a_failure():
    _night(analysis="SUCCEEDED")
    result = _outcome()
    assert result.outcome == "FAILED"
    assert "switched off" in result.reason


def test_the_sweeps_staleness_allows_a_long_night_and_its_finish_by():
    """A 25-hour clock-change night after a fast night, plus the sweep's finish-by
    (6 h since Riigikogu joined the budget), must not read as a missed sweep."""
    from app.core import nightly_schedule
    from app.register_watch import candidates

    assert (
        candidates.STALE_AFTER
        >= dt.timedelta(hours=25) + nightly_schedule.FINISH_BY["register-sweep"]
    )
