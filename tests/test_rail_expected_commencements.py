"""«Menetluse kulg»: acts are what happened, and an expected commencement says whose it is.

Owner, 2026-11-23, on a Teema whose rail drew four act dots under «Riigikogus»
for three future «Eeldatav jõustumine (allika hinnang)» dates and a step:

* a phase's act dots and its «N tegevust» count only what has happened — a
  watched date, a commencement and a row dated ahead are not acts;
* «Eeldatav jõustumine (allika hinnang)» reads «Eeldatav jõustumine»;
* the rail says where an expected commencement came from — Lovable, or the
  colleague who recorded it — with the source's own note, and a lawyer may
  remove their own there; one from Lovable is Lovable's, and its ✕ says to
  remove it in Lovable.
"""

from __future__ import annotations

import datetime as dt
import importlib
import re
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.urls import reverse
from django.utils import timezone

from app.intelligence.enums import FactStatus, ImportantDateKind
from app.intelligence.models import MatterImportantDate
from app.legacy_import.lovable_pilot import COMMENCEMENT_TITLES
from app.matters.rail_events import acts_by_phase
from app.matters.timeline import ChronologyMilestone
from app.workflow.enums import DatePrecision

# -- the act dots ---------------------------------------------------------------------


def _item(**given):
    base = {
        "event": None,
        "record": None,
        "entry": None,
        "procedural_development": None,
        "stage_effect": "",
        "files": (),
        "next_step": None,
        "milestone": None,
        "submission": None,
        "external_position": None,
        "is_entry": False,
        "entry_gist": "",
        "occurred_at": timezone.now() - dt.timedelta(days=3),
    }
    base.update(given)
    return SimpleNamespace(**base)


def _timeline(*items):
    group = SimpleNamespace(
        episode=SimpleNamespace(stage=SimpleNamespace(key="RIIGIKOGUS")),
        dom_id="etapp-1",
        items=list(items),
    )
    return SimpleNamespace(groups=[group])


_PATTERN = SimpleNamespace(phase_for_stage=lambda key: "riigikogus")


def test_only_what_happened_is_an_act_of_the_phase():
    sitting = _item(procedural_development=SimpleNamespace(title="Õiguskomisjoni istung"))
    estimate = _item(
        record=MatterImportantDate(kind=ImportantDateKind.ESTIMATED_COMMENCEMENT),
        milestone=ChronologyMilestone(what="Eeldatav jõustumine", display_date="1.7.2027"),
    )
    ahead = _item(
        milestone=ChronologyMilestone(what="Kaasamine", display_date="1.12.2026", ahead=True)
    )
    later = _item(
        procedural_development=SimpleNamespace(title="Teine lugemine"),
        occurred_at=timezone.now() + dt.timedelta(days=10),
    )

    acts = acts_by_phase(_timeline(sitting, estimate, ahead, later), _PATTERN)

    assert acts["riigikogus"].count == 1
    assert [what for _, what in acts["riigikogus"].dots] == ["Õiguskomisjoni istung"]


def test_a_phase_with_only_expectations_draws_no_acts():
    estimate = _item(
        record=MatterImportantDate(kind=ImportantDateKind.ESTIMATED_COMMENCEMENT),
        milestone=ChronologyMilestone(what="Eeldatav jõustumine", display_date="1.7.2027"),
    )

    assert acts_by_phase(_timeline(estimate), _PATTERN) == {}


# -- the label ------------------------------------------------------------------------


def test_the_estimate_is_called_eeldatav_joustumine():
    assert ImportantDateKind.ESTIMATED_COMMENCEMENT.label == "Eeldatav jõustumine"
    assert COMMENCEMENT_TITLES["ESTIMATED_COMMENCEMENT"] == "Eeldatav jõustumine"


@pytest.mark.django_db
def test_the_migration_renames_only_the_old_default_title(normal_matter):
    from app.search.models import SearchRebuildDebt

    def estimate(title):
        return MatterImportantDate.objects.create(
            matter=normal_matter,
            title=title,
            kind=ImportantDateKind.ESTIMATED_COMMENCEMENT,
            date_value=dt.date(2027, 7, 1),
            period_end=dt.date(2027, 7, 1),
            date_precision=DatePrecision.EXACT.value,
            status=FactStatus.ACTIVE,
        )

    old = estimate("Eeldatav jõustumine (allika hinnang)")
    own = estimate("Ministeeriumi hinnang jõustumisele")
    debts = SearchRebuildDebt.objects.count()
    migration = importlib.import_module(
        "app.intelligence.migrations.0005_estimated_commencement_title"
    )

    migration.rename(apps, None)

    old.refresh_from_db()
    own.refresh_from_db()
    assert old.title == "Eeldatav jõustumine"
    assert own.title == "Ministeeriumi hinnang jõustumisele"
    assert SearchRebuildDebt.objects.count() == debts + 1


# -- the source and the removal on the rail -------------------------------------------


def _estimate(matter, *, by=None, note=""):
    when = timezone.localdate() + dt.timedelta(days=200)
    return MatterImportantDate.objects.create(
        matter=matter,
        title="Eeldatav jõustumine",
        kind=ImportantDateKind.ESTIMATED_COMMENCEMENT,
        date_value=when,
        period_end=when,
        date_precision=DatePrecision.EXACT.value,
        status=FactStatus.ACTIVE,
        created_by=by,
        note=note,
    )


def _strip(client, matter) -> str:
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    html = response.content.decode()
    strip = html[html.index('class="tl-strip"') :]
    # Nothing inside the strip is a `div`, so its first close is the strip's own.
    return strip[: strip.index("</div>")]


def _remove_url(matter, record):
    return reverse(
        "matters:remove_record",
        kwargs={"pk": matter.pk, "kind": "tahtaeg", "record_id": record.pk},
    )


@pytest.mark.django_db
def test_a_colleagues_estimate_names_them_and_can_be_removed_on_the_rail(
    signed_in, specialist, normal_matter
):
    record = _estimate(normal_matter, by=specialist, note="osad sätted")

    strip = _strip(signed_in, normal_matter)

    assert f"Allikas: {specialist.display_name}" in strip
    assert "osad sätted" in strip
    assert _remove_url(normal_matter, record) in strip
    assert "(allika hinnang)" not in strip


@pytest.mark.django_db
def test_removing_it_from_the_rail_takes_it_off_the_file(signed_in, specialist, normal_matter):
    record = _estimate(normal_matter, by=specialist)

    response = signed_in.post(
        _remove_url(normal_matter, record),
        {"revision": record.updated_at.isoformat()},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    record.refresh_from_db()
    assert record.removed_at is not None


@pytest.mark.django_db
def test_a_lovable_estimate_says_so_and_points_to_lovable(
    signed_in, specialist, normal_matter, monkeypatch
):
    record = _estimate(normal_matter, note="Üldises korras")
    monkeypatch.setattr(
        "app.matters.views.lovable_commencement_ids",
        lambda matter, source_owned=None: frozenset({str(record.pk)}),
    )

    strip = _strip(signed_in, normal_matter)

    assert "Allikas: Lovable" in strip
    assert "Eemalda see Lovable'is" in strip
    assert _remove_url(normal_matter, record) not in strip


@pytest.mark.django_db
def test_a_reader_sees_the_source_but_no_removal(client, reader, normal_matter, specialist):
    record = _estimate(normal_matter, by=specialist)
    client.force_login(reader)

    strip = _strip(client, normal_matter)

    assert f"Allikas: {specialist.display_name}" in strip
    assert _remove_url(normal_matter, record) not in strip
    assert not re.search(r"uxtl__icon--remove", strip)
