"""A click on a `Menetluse kulg` step dates it (owner, 2026-10-26).

`Valitsuses`, `Riigikogus`, `Jõustumine` — every phase of the file's procedure,
and every step a person added — is a button for a writer on an open file. It
opens a small `millal?` panel with the one `Täpsus` composer: a day, or a month,
quarter or year when that is all anybody knows. What is pinned here:

* every phase and added step is clickable for a writer, nothing for a reader,
  nothing on a closed file, and never an event or a sent opinion;
* the panel saves a day, a month, a quarter and a year, reopens on what it
  saved, and clears the date when emptied;
* it writes through `set_timeline_steps` — the same row, the same audit event —
  keeps the procedure's order, keeps a hidden phase hidden and an added step's
  name and place;
* `Muuda kulgu` saved afterwards does not turn a period into a day;
* a stale panel, a crafted key, a reader and an outsider are refused.
"""

from __future__ import annotations

import re
from datetime import date

import pytest
from django.urls import reverse
from django.utils.text import slugify

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.models import MatterTimelineStep
from app.matters.process_phases import PHASE_JOUSTUMINE, PHASE_RIIGIKOGU, PHASE_VALITSUS
from app.matters.services import (
    RAIL_DATE_OUT_OF_ORDER,
    RAIL_STEP_NOT_DATABLE,
    TIMELINE_STEPS_CONFLICT,
    change_stage,
    set_rail_step_date,
    set_timeline_steps,
    timeline_steps_revision_token,
)
from app.workflow.enums import DatePrecision
from tests import factories
from tests.refusals import refused
from tests.test_timeline_added_steps import _matter, _stage

pytestmark = pytest.mark.django_db


def _file(owner):
    matter = _matter(owner, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("consultation"), actor=owner)
    return matter


def _url(matter, key: str) -> str:
    return reverse("matters:rail_step_date", kwargs={"pk": matter.pk, "key": key})


def _page(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _rail(html: str) -> str:
    return html[html.index('class="tl-strip"') :].split("</section>")[0]


def _post(client, matter, key: str, **data):
    return client.post(_url(matter, key), data, headers={"HX-Request": "true"})


def _row(matter, key: str) -> MatterTimelineStep | None:
    return MatterTimelineStep.objects.filter(matter=matter, phase_key=key).first()


# ---------------------------------------------------------------------------
# What is clickable
# ---------------------------------------------------------------------------


def test_every_phase_is_a_button_for_a_writer(signed_in, specialist):
    matter = _file(specialist)

    rail = _rail(_page(signed_in, matter))

    for key, label in (
        (PHASE_VALITSUS, "Valitsuses"),
        (PHASE_RIIGIKOGU, "Riigikogus"),
        (PHASE_JOUSTUMINE, "Jõustumine"),
    ):
        button = re.search(rf'<button[^>]*id="kulg-samm-{key}"[^>]*>', rail)
        assert button is not None, key
        assert _url(matter, key) in button.group(0)
        assert f'aria-label="{label}: lisa kuupäev"' in button.group(0)


def test_a_reader_and_a_closed_file_get_no_buttons(client, signed_in, specialist, reader):
    from app.matters.services import close_matter
    from app.workflow.enums import Disposition

    matter = _file(specialist)
    client.force_login(reader)
    assert "tl-step__pick" not in _page(client, matter)

    close_matter(matter=matter, disposition=Disposition.OTHER, actor=specialist)
    assert "tl-step__pick" not in _page(signed_in, matter)


def test_the_panel_opens_on_what_the_step_holds(signed_in, specialist):
    matter = _file(specialist)
    set_rail_step_date(
        matter=matter,
        rail_key=PHASE_JOUSTUMINE,
        occurs_on=date(2026, 10, 1),
        precision=DatePrecision.QUARTER.value,
        actor=specialist,
    )

    panel = signed_in.get(_url(matter, PHASE_JOUSTUMINE), headers={"HX-Request": "true"})

    assert panel.status_code == 200
    body = panel.content.decode()
    assert "Jõustumine</strong> — millal?" in body
    assert re.search(r'value="QUARTER"[^>]*checked', body)
    assert re.search(r'<option value="4" selected>', body)
    assert 'value="2026"' in body
    assert 'data-close-slot="menetluse-kulg-muuda"' in body


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "expected", "precision", "shown"),
    [
        (
            {"kp_precision": "EXACT", "kp_date": "15.03.2027"},
            date(2027, 3, 15),
            "EXACT",
            "15.3.2027",
        ),
        (
            {"kp_precision": "MONTH", "kp_month": "3", "kp_year": "2027"},
            date(2027, 3, 1),
            "MONTH",
            "märts 2027",
        ),
        (
            {"kp_precision": "QUARTER", "kp_quarter": "2", "kp_year": "2027"},
            date(2027, 4, 1),
            "QUARTER",
            "II kvartal 2027",
        ),
        ({"kp_precision": "YEAR", "kp_year": "2027"}, date(2027, 1, 1), "YEAR", "2027"),
    ],
)
def test_a_day_a_month_a_quarter_or_a_year(signed_in, specialist, data, expected, precision, shown):
    matter = _file(specialist)

    response = _post(signed_in, matter, PHASE_JOUSTUMINE, **data)

    assert response.status_code == 200
    row = _row(matter, PHASE_JOUSTUMINE)
    assert row is not None
    assert (row.occurs_on, row.occurs_on_precision) == (expected, precision)
    assert shown in _rail(response.content.decode())
    assert (
        ChangeEvent.objects.filter(
            matter=matter, event_type=ChangeEventType.TIMELINE_STEPS_CHANGED
        ).count()
        == 1
    )


def test_emptying_the_panel_clears_the_date(signed_in, specialist):
    matter = _file(specialist)
    _post(signed_in, matter, PHASE_VALITSUS, kp_precision="EXACT", kp_date="01.12.2026")

    _post(signed_in, matter, PHASE_VALITSUS, kp_precision="EXACT", kp_date="")

    # Shown and undated is the default, which is no row at all.
    assert _row(matter, PHASE_VALITSUS) is None


def test_a_half_stated_period_is_refused_in_the_panel(signed_in, specialist):
    matter = _file(specialist)

    response = _post(signed_in, matter, PHASE_VALITSUS, kp_precision="MONTH", kp_month="3")

    assert response.status_code == 400
    assert response["HX-Retarget"] == "#menetluse-kulg-muuda"
    assert _row(matter, PHASE_VALITSUS) is None


def test_the_procedures_order_is_kept(signed_in, specialist):
    matter = _file(specialist)
    _post(signed_in, matter, PHASE_RIIGIKOGU, kp_precision="EXACT", kp_date="01.03.2027")

    response = _post(signed_in, matter, PHASE_JOUSTUMINE, kp_precision="YEAR", kp_year="2026")

    assert response.status_code == 400
    assert RAIL_DATE_OUT_OF_ORDER in response.content.decode()
    assert _row(matter, PHASE_JOUSTUMINE) is None
    with refused(RAIL_DATE_OUT_OF_ORDER):
        set_rail_step_date(
            matter=matter,
            rail_key=PHASE_VALITSUS,
            occurs_on=date(2027, 6, 1),
            precision=DatePrecision.EXACT.value,
            actor=specialist,
        )


def test_a_hidden_phase_stays_hidden(specialist):
    matter = _file(specialist)
    set_timeline_steps(matter=matter, steps=[(PHASE_VALITSUS, True, None, "EXACT")])

    set_rail_step_date(
        matter=matter,
        rail_key=PHASE_VALITSUS,
        occurs_on=date(2026, 12, 1),
        precision=DatePrecision.MONTH.value,
        actor=specialist,
    )

    row = _row(matter, PHASE_VALITSUS)
    assert row.hidden is True
    assert row.occurs_on_precision == DatePrecision.MONTH.value


def test_an_added_step_keeps_its_name_and_place(signed_in, specialist):
    matter = _file(specialist)
    set_timeline_steps(
        matter=matter,
        steps=(),
        added=[(None, "Komisjoni istung", None, "EXACT", PHASE_VALITSUS, False)],
        actor=specialist,
    )
    step = MatterTimelineStep.objects.get(matter=matter, phase_key="")
    assert f'id="kulg-samm-{slugify(step.rail_key)}"' in _rail(_page(signed_in, matter))

    response = _post(
        signed_in, matter, step.rail_key, kp_precision="MONTH", kp_month="5", kp_year="2027"
    )

    assert response.status_code == 200
    step.refresh_from_db()
    assert (step.title, step.after_key) == ("Komisjoni istung", PHASE_VALITSUS)
    assert (step.occurs_on, step.occurs_on_precision) == (date(2027, 5, 1), "MONTH")


def test_muuda_kulgu_keeps_a_period_it_did_not_change(signed_in, specialist):
    """The panel's phase boxes are a day; saving it must not turn
    *II kvartal 2027* into the first of April."""
    matter = _file(specialist)
    _post(
        signed_in, matter, PHASE_JOUSTUMINE, kp_precision="QUARTER", kp_quarter="2", kp_year="2027"
    )
    panel = signed_in.get(
        reverse("matters:timeline_steps", kwargs={"pk": matter.pk}), headers={"HX-Request": "true"}
    ).content.decode()
    data = dict.fromkeys(re.findall(r'name="([a-z_]+__shown)"', panel), "on")
    data["revision"] = re.search(r'name="revision" value="([^"]*)"', panel).group(1)
    data[f"{PHASE_JOUSTUMINE}__date"] = "01.04.2027"

    signed_in.post(reverse("matters:timeline_steps", kwargs={"pk": matter.pk}), data)

    row = _row(matter, PHASE_JOUSTUMINE)
    assert (row.occurs_on, row.occurs_on_precision) == (date(2027, 4, 1), "QUARTER")


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_a_stale_panel_is_refused(signed_in, specialist):
    matter = _file(specialist)
    stale = timeline_steps_revision_token(matter)
    set_rail_step_date(
        matter=matter,
        rail_key=PHASE_RIIGIKOGU,
        occurs_on=date(2027, 1, 1),
        precision="YEAR",
        actor=specialist,
    )

    response = _post(
        signed_in, matter, PHASE_VALITSUS, kp_precision="YEAR", kp_year="2026", revision=stale
    )

    assert response.status_code == 409
    assert TIMELINE_STEPS_CONFLICT in response.content.decode()
    assert _row(matter, PHASE_VALITSUS) is None


@pytest.mark.parametrize("key", ["arvamus", "samm:00000000-0000-0000-0000-000000000000", "bogus"])
def test_a_key_the_rail_does_not_offer_is_404(signed_in, specialist, key):
    matter = _file(specialist)

    assert signed_in.get(_url(matter, key)).status_code == 404
    assert _post(signed_in, matter, key, kp_precision="YEAR", kp_year="2026").status_code == 404
    with refused(RAIL_STEP_NOT_DATABLE):
        set_rail_step_date(
            matter=matter, rail_key=key, occurs_on=None, precision="EXACT", actor=specialist
        )


def test_a_reader_and_an_outsider_cannot_date_a_step(client, specialist, reader):
    matter = _file(specialist)
    client.force_login(reader)
    assert (
        _post(client, matter, PHASE_VALITSUS, kp_precision="YEAR", kp_year="2026").status_code
        == 404
    )
    client.force_login(factories.AdministratorFactory())
    assert (
        _post(client, matter, PHASE_VALITSUS, kp_precision="YEAR", kp_year="2026").status_code
        == 404
    )
    assert _row(matter, PHASE_VALITSUS) is None


# ---------------------------------------------------------------------------
# Review of #548: periods are compared by overlap; the edges
# ---------------------------------------------------------------------------


def test_a_year_after_a_dated_phase_in_that_year_is_in_order(signed_in, specialist):
    """`Riigikogus 1.3.2027` and then `Jõustumine 2027`: the year contains the
    day, so it is not «earlier» — only its first day was."""
    matter = _file(specialist)
    _post(signed_in, matter, PHASE_RIIGIKOGU, kp_precision="EXACT", kp_date="01.03.2027")

    response = _post(signed_in, matter, PHASE_JOUSTUMINE, kp_precision="YEAR", kp_year="2027")

    assert response.status_code == 200
    assert _row(matter, PHASE_JOUSTUMINE).occurs_on_precision == "YEAR"


@pytest.mark.parametrize("order", ["day_first", "quarter_first"])
def test_a_day_and_a_quarter_containing_it_are_in_order(signed_in, specialist, order):
    matter = _file(specialist)
    day = {"kp_precision": "EXACT", "kp_date": "15.10.2026"}
    quarter = {"kp_precision": "QUARTER", "kp_quarter": "4", "kp_year": "2026"}
    if order == "day_first":
        _post(signed_in, matter, PHASE_VALITSUS, **day)
        response = _post(signed_in, matter, PHASE_RIIGIKOGU, **quarter)
    else:
        _post(signed_in, matter, PHASE_RIIGIKOGU, **quarter)
        response = _post(signed_in, matter, PHASE_VALITSUS, **day)

    assert response.status_code == 200
    assert _row(matter, PHASE_VALITSUS) is not None and _row(matter, PHASE_RIIGIKOGU) is not None


def test_a_period_wholly_before_an_earlier_phase_is_still_refused(signed_in, specialist):
    matter = _file(specialist)
    _post(signed_in, matter, PHASE_RIIGIKOGU, kp_precision="EXACT", kp_date="05.01.2027")

    response = _post(signed_in, matter, PHASE_JOUSTUMINE, kp_precision="YEAR", kp_year="2026")

    assert response.status_code == 400
    assert RAIL_DATE_OUT_OF_ORDER in response.content.decode()


def test_muuda_kulgu_accepts_the_periods_the_rail_accepted(signed_in, specialist):
    """The panel compares the same periods, so a rail it did not draw cannot
    lock it: saved unchanged, `Riigikogus 1.3.2027` + `Jõustumine 2027` saves."""
    matter = _file(specialist)
    _post(signed_in, matter, PHASE_RIIGIKOGU, kp_precision="EXACT", kp_date="01.03.2027")
    _post(signed_in, matter, PHASE_JOUSTUMINE, kp_precision="YEAR", kp_year="2027")
    url = reverse("matters:timeline_steps", kwargs={"pk": matter.pk})
    panel = signed_in.get(url, headers={"HX-Request": "true"}).content.decode()
    assert "Kirjas: 2027" in panel
    data = dict.fromkeys(re.findall(r'name="([a-z_]+__shown)"', panel), "on")
    data["revision"] = re.search(r'name="revision" value="([^"]*)"', panel).group(1)
    data[f"{PHASE_RIIGIKOGU}__date"] = "01.03.2027"
    data[f"{PHASE_JOUSTUMINE}__date"] = "01.01.2027"

    response = signed_in.post(url, data, headers={"HX-Request": "true"})

    assert response.status_code == 200
    assert _row(matter, PHASE_JOUSTUMINE).occurs_on_precision == "YEAR"


def test_a_stored_half_year_keeps_its_own_chip_and_survives_a_save(signed_in, specialist):
    matter = _file(specialist)
    MatterTimelineStep.objects.create(
        matter=matter,
        phase_key=PHASE_JOUSTUMINE,
        occurs_on=date(2027, 7, 1),
        occurs_on_precision="HALF_YEAR",
    )

    panel = signed_in.get(_url(matter, PHASE_JOUSTUMINE)).content.decode()
    assert re.search(r'value="HALF_YEAR"[^>]*checked', panel)
    assert "Muutmata: II poolaasta 2027" in panel

    assert _post(signed_in, matter, PHASE_JOUSTUMINE, kp_precision="HALF_YEAR").status_code == 200
    row = _row(matter, PHASE_JOUSTUMINE)
    assert (row.occurs_on, row.occurs_on_precision) == (date(2027, 7, 1), "HALF_YEAR")


def test_a_conflict_comes_back_on_the_current_version_and_saves_on_retry(signed_in, specialist):
    matter = _file(specialist)
    stale = timeline_steps_revision_token(matter)
    set_rail_step_date(
        matter=matter,
        rail_key=PHASE_RIIGIKOGU,
        occurs_on=date(2027, 1, 1),
        precision="YEAR",
        actor=specialist,
    )
    first = _post(
        signed_in, matter, PHASE_VALITSUS, kp_precision="YEAR", kp_year="2026", revision=stale
    )
    assert first.status_code == 409
    fresh = re.search(r'name="revision" value="([^"]*)"', first.content.decode()).group(1)
    assert fresh == timeline_steps_revision_token(matter)

    again = _post(
        signed_in, matter, PHASE_VALITSUS, kp_precision="YEAR", kp_year="2026", revision=fresh
    )

    assert again.status_code == 200
    assert _row(matter, PHASE_VALITSUS).occurs_on == date(2026, 1, 1)


def test_jo_ustumine_with_a_recorded_commencement_is_not_a_button(signed_in, specialist):
    from app.intelligence.services import add_effective_date

    matter = _file(specialist)
    add_effective_date(
        matter=matter,
        date_value=date(2027, 9, 27),
        period_end=date(2027, 9, 27),
        description="Põhiosa",
        actor=specialist,
    )

    rail = _rail(_page(signed_in, matter))

    assert 'id="kulg-samm-joustumine"' not in rail
    assert 'id="kulg-samm-riigikogu"' in rail
    assert signed_in.get(_url(matter, PHASE_JOUSTUMINE)).status_code == 404


def test_a_closed_file_answers_the_panel_404(signed_in, specialist):
    from app.matters.services import close_matter
    from app.workflow.enums import Disposition

    matter = _file(specialist)
    close_matter(matter=matter, disposition=Disposition.OTHER, actor=specialist)

    assert signed_in.get(_url(matter, PHASE_VALITSUS)).status_code == 404


# ---------------------------------------------------------------------------
# Second review of #548: placement stays in the procedure's order
# ---------------------------------------------------------------------------


def test_a_year_phase_after_a_dated_day_keeps_the_deadline_before_it(specialist):
    """`Valitsuses 1.10.2026`, `Riigikogus 1.3.2027`, `Jõustumine 2027` and a
    deadline on 15.2.2027: the deadline reads between `Valitsuses` and
    `Riigikogus`, and the year phase sorts on its last day — so the connector
    into it is not drawn as already travelled."""
    from app.intelligence.services import add_important_date
    from app.matters.legal_process import KIND_PHASE
    from tests.test_timeline_added_steps import _rail as rail_of

    matter = _file(specialist)
    for key, when, precision in (
        (PHASE_VALITSUS, date(2026, 10, 1), "EXACT"),
        (PHASE_RIIGIKOGU, date(2027, 3, 1), "EXACT"),
        (PHASE_JOUSTUMINE, date(2027, 1, 1), "YEAR"),
    ):
        set_rail_step_date(
            matter=matter, rail_key=key, occurs_on=when, precision=precision, actor=specialist
        )
    add_important_date(
        matter=matter,
        title="Ministeeriumi vastus",
        date_value=date(2027, 2, 15),
        period_end=date(2027, 2, 15),
        actor=specialist,
    )

    steps = rail_of(matter, specialist)
    labels = [step.label for step in steps]
    phase_keys = [step.sort_on for step in steps if step.kind == KIND_PHASE and step.sort_on]
    assert phase_keys == sorted(phase_keys)
    jõustumine = next(step for step in steps if step.key == PHASE_JOUSTUMINE)
    assert jõustumine.reach_key == date(2027, 12, 31)
    assert jõustumine.display_date == "2027"
    deadline = labels.index("Ministeeriumi vastus")
    assert labels.index("Valitsuses") < deadline < labels.index("Riigikogus")


def test_a_cancelled_commencement_anchors_nothing(signed_in, specialist):
    from app.intelligence.services import add_effective_date, cancel_effective_date

    matter = _file(specialist)
    record = add_effective_date(
        matter=matter,
        date_value=date(2027, 9, 27),
        period_end=date(2027, 9, 27),
        description="Põhiosa",
        actor=specialist,
    )
    cancel_effective_date(record=record, actor=specialist)

    rail = _rail(_page(signed_in, matter))

    assert 'id="kulg-samm-joustumine"' in rail
    assert signed_in.get(_url(matter, PHASE_JOUSTUMINE)).status_code == 200


# ---------------------------------------------------------------------------
# Third review of #548: placed on the period's start, reached on its end
# ---------------------------------------------------------------------------


def _phase(key: str, state: str, start=None, end=None):
    from app.matters.legal_process import KIND_PHASE, RailStep

    return RailStep(key=key, label=key, kind=KIND_PHASE, state=state, sort_on=start, reach_on=end)


def test_a_point_inside_a_period_dated_phase_reads_inside_it():
    """`Algus 2026` current and an opinion sent 10.5.2026: the opinion reads
    after `Algus`, never before the beginning (F1)."""
    from app.matters.legal_process import STATE_CURRENT, STATE_POSSIBLE, _slot_for

    steps = [
        _phase("algus", STATE_CURRENT, date(2026, 1, 1), date(2026, 12, 31)),
        _phase("kooskolastus", STATE_POSSIBLE),
    ]

    assert _slot_for(steps, date(2026, 5, 10), (0, len(steps)), 1) == 1


def test_a_year_phase_after_a_dated_day_is_part_filled_not_travelled():
    """`Riigikogus 1.3.2027` current, `Jõustumine 2027` ahead, today 1.6.2027:
    the connector into `Jõustumine` is part-filled, not drawn as travelled."""
    from app.matters.legal_process import (
        STATE_CURRENT,
        STATE_POSSIBLE,
        _one_backbone,
        _phase_keys_in_order,
    )

    steps = [
        _phase("riigikogu", STATE_CURRENT, date(2027, 3, 1), date(2027, 3, 1)),
        _phase("joustumine", STATE_POSSIBLE, date(2027, 1, 1), date(2027, 12, 31)),
    ]
    _phase_keys_in_order(steps)

    assert steps[1].sort_on == date(2027, 3, 1)
    assert steps[1].reach_key == date(2027, 12, 31)
    drawn = _one_backbone(steps, date(2027, 6, 1))
    assert 0 < drawn[0].reach < 1


def test_both_keys_are_lifted_into_the_procedures_order():
    """`Valitsuses 2027` then `Riigikogus 1.3.2027` is in order by overlap; the
    later phase is reached no earlier than the year it follows ends."""
    from app.matters.legal_process import STATE_POSSIBLE, _phase_keys_in_order

    steps = [
        _phase("valitsus", STATE_POSSIBLE, date(2027, 1, 1), date(2027, 12, 31)),
        _phase("riigikogu", STATE_POSSIBLE, date(2027, 3, 1), date(2027, 3, 1)),
    ]
    _phase_keys_in_order(steps)

    assert [step.sort_on for step in steps] == [date(2027, 1, 1), date(2027, 3, 1)]
    assert [step.reach_key for step in steps] == [date(2027, 12, 31), date(2027, 12, 31)]
