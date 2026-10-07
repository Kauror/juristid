"""One transition, entered once — and a dated phase reads as reached (docs/adr/0128).

JUR-CASE-10 from the living-dossier QA. On the day a lawyer recorded
«Kooskõlastusring – eelnõu» with `Uus hetkeseis → Kooskõlastusringil`, the
`Hetkeseis` moved and `Menetluse kulg` stayed undated, so the same transition
had to be typed again in `Muuda kulgu`. And a `VTK` dated 1.9 on a file still on
`Idee` read «Tulevikus», with the 6.9 feedback deadline and the 8.9 opinion drawn
*before* it.

Asserted here, each where it is decided:

* **the offer** — `Märgi ka menetluse kulgu: <faas> <päev>` exists only for a
  forward move onto a phase that stage alone places the file on, while that
  phase is undated, on a day that has come and keeps the roadmap in order; it is
  ticked when it appears;
* **the write** — ticked, one save moves the stage *and* writes that day as the
  phase's roadmap date, once, under the same operation, and adds no `Teema käik`
  row; unticked, the stage moves and nothing is dated; a crafted tick where no
  offer applies writes nothing;
* **never overwritten** — a phase that already carries a date keeps it;
* **the rail** — a phase a person dated on a day that has come reads `Kirjas`
  wherever it sits, the current node stays the `Hetkeseis`, a period is reached
  only once it has ended, and dated points sort against explicit phase dates
  across the current node;
* **the neighbours** — added steps, `Muuda kulgu` and the ADR 0124 future-`Märge`
  route keep working.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.forms import MatterProgressForm
from app.matters.legal_process import (
    STATE_CURRENT,
    STATE_POSSIBLE,
    STATE_RECORDED,
    legal_process_rail,
    matter_rail,
    phase_context,
    phase_date_offers,
    timeline_step_rows,
)
from app.matters.models import MatterTimelineStep
from app.matters.process_phases import (
    CONFIRMABLE_STAGE_KEYS,
    EU_PATTERN,
    PHASE_KOOSKOLASTUS,
    PHASE_RIIGIKOGU,
    PHASE_VALITSUS,
    PHASE_VTK,
    VTK_PATTERN,
    confirmable_phase,
)
from app.matters.process_timeline import FEEDBACK_DEADLINE_LABEL, SENT_LABEL, process_steps
from app.matters.services import add_engagement, set_timeline_steps
from app.matters.timeline import matter_timeline
from app.matters.workspace import add_procedural_development
from app.submissions.enums import SubmissionStatus
from app.taxonomy.models import LegalInstrumentType
from app.workflow.enums import ActionStatus, DatePrecision
from app.workflow.models import NextAction, StageVocabulary
from tests import factories

pytestmark = pytest.mark.django_db

TALLINN = dt.timezone(dt.timedelta(hours=3))
#: The day the lawyer records the move. Pinned, so «today» and «ahead» mean the
#: same thing whenever the suite runs.
TODAY = dt.date(2026, 9, 15)
OFFER_WORDS = "Märgi ka menetluse kulgu"


@pytest.fixture(autouse=True)
def _the_lawyers_day(monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: TODAY)


def _stage(key: str) -> StageVocabulary:
    return StageVocabulary.objects.get(key=key)


def _matter(owner, *, instruments: tuple[str, ...] = ("seadus",), stage: str | None = "idea"):
    matter = factories.MatterFactory(owner=owner, track="", stage=None)
    matter.legal_instruments.set([LegalInstrumentType.objects.get(key=key) for key in instruments])
    if stage:
        matter.stage = _stage(stage)
        matter.save(update_fields=["stage", "updated_at"])
    return matter


def _add_note(matter) -> str:
    return reverse("matters:add_note", kwargs={"pk": matter.pk})


def _teema(matter) -> str:
    return reverse("matters:matter_detail", kwargs={"pk": matter.pk})


def _post(client, matter, *, stage: str, day: dt.date | None, tick: bool, title: str = ""):
    data = {
        "title": title or "Saabus eelnõu kooskõlastusringile",
        "occurred_on": day.strftime("%d.%m.%Y") if day else "",
        "stage": str(_stage(stage).pk),
    }
    if tick:
        data["date_phase"] = "on"
    return client.post(_add_note(matter), data, HTTP_HX_REQUEST="true")


def _phase_row(matter, key: str):
    return MatterTimelineStep.objects.filter(matter=matter, phase_key=key).first()


def _offers(matter, user):
    rows, _added = timeline_step_rows(matter=matter, user=user)
    return phase_date_offers(phases=phase_context(matter=matter), rows=rows)


def _rail(matter, user):
    context = phase_context(matter=matter)
    return matter_rail(
        matter=matter,
        user=user,
        rail=legal_process_rail(matter=matter, user=user, context=context),
        milestones=process_steps(matter=matter, user=user, today=TODAY),
    )


def _states(steps) -> dict[str, str]:
    return {step.label: step.state for step in steps if step.is_phase}


# ---------------------------------------------------------------------------
# Which moves qualify — a reading of the vocabulary, nothing else
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("from_stage", "to_stage", "phase"),
    [
        ("idea", "consultation", PHASE_KOOSKOLASTUS),
        ("", "consultation", PHASE_KOOSKOLASTUS),
        ("consultation", "government", PHASE_VALITSUS),
        ("idea", "parliament", PHASE_RIIGIKOGU),
    ],
)
def test_a_forward_move_onto_a_phase_it_alone_holds_qualifies(from_stage, to_stage, phase):
    assert confirmable_phase(VTK_PATTERN, from_stage=from_stage, to_stage=to_stage) == phase


@pytest.mark.parametrize(
    ("pattern", "from_stage", "to_stage"),
    [
        # The beginning, and never `VTK` (docs/adr/0098 §2).
        (VTK_PATTERN, "", "idea"),
        # Adoption is not commencement; `Jõustumine` is the commencement's.
        (VTK_PATTERN, "parliament", "awaiting_entry"),
        (VTK_PATTERN, "parliament", "in_force"),
        # Backwards, and standing still.
        (VTK_PATTERN, "government", "consultation"),
        (VTK_PATTERN, "consultation", "consultation"),
        # From a stage this pattern cannot place.
        (VTK_PATTERN, "other", "consultation"),
        # `ELi konsultatsioon` began with `idea`: the move's day is not its start.
        (EU_PATTERN, "idea", "consultation"),
        (EU_PATTERN, "", "awaiting_transposition"),
        (None, "idea", "consultation"),
    ],
)
def test_an_ambiguous_backward_or_unplaced_move_qualifies_for_nothing(
    pattern, from_stage, to_stage
):
    assert confirmable_phase(pattern, from_stage=from_stage, to_stage=to_stage) == ""


def test_the_confirmable_stages_are_exactly_the_five_reviewed():
    assert CONFIRMABLE_STAGE_KEYS == {
        "consultation",
        "government",
        "parliament",
        "estonian_eu_position",
        "eu_procedure",
    }


# ---------------------------------------------------------------------------
# The offer on the panel
# ---------------------------------------------------------------------------


def test_a_fresh_form_offers_nothing_until_a_stage_qualifies(specialist):
    """The form's rule. The panel that drew it (`+ Lisa · Tavaline`) left on
    2026-10-07 (docs/adr/0143); `add_note` still applies the rule."""
    matter = _matter(specialist)

    # A fresh form says «Jätan muutmata», so nothing is offered yet — hidden
    # and disabled until a stage that qualifies is chosen.
    offers = _offers(matter, specialist)
    form = MatterProgressForm(phases=phase_context(matter=matter), phase_offers=offers)
    assert form.phase_offer is None
    assert form.fields["date_phase"].widget.attrs.get("disabled") is True


def test_the_offer_is_ticked_and_names_the_phase_and_the_day(specialist):
    matter = _matter(specialist)
    form = MatterProgressForm(
        {
            "title": "x",
            "occurred_on": TODAY.strftime("%d.%m.%Y"),
            "stage": str(_stage("consultation").pk),
        },
        phases=phase_context(matter=matter),
        phase_offers=_offers(matter, specialist),
    )
    form.is_valid()

    assert form.fields["date_phase"].initial is True
    assert form.phase_offer is not None
    assert form.phase_offer_text == "Kooskõlastusring 15.9.2026"
    assert "disabled" not in form.fields["date_phase"].widget.attrs


def test_a_phase_already_dated_is_not_offered(specialist):
    matter = _matter(specialist)
    set_timeline_steps(
        matter=matter,
        steps=[(PHASE_KOOSKOLASTUS, False, dt.date(2026, 9, 10), DatePrecision.EXACT.value)],
        actor=specialist,
    )

    assert "consultation" not in _offers(matter, specialist)


# ---------------------------------------------------------------------------
# The write
# ---------------------------------------------------------------------------


def test_ticked_moves_the_stage_and_dates_the_phase_once(signed_in, specialist):
    matter = _matter(specialist)

    response = _post(signed_in, matter, stage="consultation", day=TODAY, tick=True)

    assert response.status_code == 200, response.content.decode()[:1500]
    matter.refresh_from_db()
    assert matter.stage.key == "consultation"
    row = _phase_row(matter, PHASE_KOOSKOLASTUS)
    assert row is not None
    assert (row.occurs_on, row.occurs_on_precision, row.hidden) == (
        TODAY,
        DatePrecision.EXACT.value,
        False,
    )
    assert MatterTimelineStep.objects.filter(matter=matter).count() == 1
    # One event for the phase date, in the same operation as the `Märge`.
    steps_events = ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.TIMELINE_STEPS_CHANGED
    )
    assert steps_events.count() == 1
    development_event = ChangeEvent.objects.get(
        matter=matter, event_type=ChangeEventType.PROCEDURAL_DEVELOPMENT_RECORDED
    )
    assert steps_events.get().operation_id == development_event.operation_id
    assert steps_events.get().payload["phases"] == [PHASE_KOOSKOLASTUS]


def test_the_rail_reads_the_date_without_a_second_entry(signed_in, specialist):
    matter = _matter(specialist)
    _post(signed_in, matter, stage="consultation", day=TODAY, tick=True)

    steps = _rail(matter, specialist)
    round_ = next(step for step in steps if step.label == "Kooskõlastusring")

    assert round_.state == STATE_CURRENT
    assert round_.display_date == "15.9.2026"


def test_unticked_moves_the_stage_and_dates_nothing(signed_in, specialist):
    matter = _matter(specialist)

    response = _post(signed_in, matter, stage="consultation", day=TODAY, tick=False)

    assert response.status_code == 200
    matter.refresh_from_db()
    assert matter.stage.key == "consultation"
    assert not MatterTimelineStep.objects.filter(matter=matter).exists()
    assert not ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.TIMELINE_STEPS_CHANGED
    ).exists()


def test_an_existing_date_is_never_overwritten(signed_in, specialist):
    matter = _matter(specialist)
    set_timeline_steps(
        matter=matter,
        steps=[(PHASE_KOOSKOLASTUS, False, dt.date(2026, 9, 10), DatePrecision.EXACT.value)],
        actor=specialist,
    )
    before = ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.TIMELINE_STEPS_CHANGED
    ).count()

    # A crafted tick — the panel would not offer it.
    _post(signed_in, matter, stage="consultation", day=TODAY, tick=True)

    assert _phase_row(matter, PHASE_KOOSKOLASTUS).occurs_on == dt.date(2026, 9, 10)
    assert (
        ChangeEvent.objects.filter(
            matter=matter, event_type=ChangeEventType.TIMELINE_STEPS_CHANGED
        ).count()
        == before
    )
    matter.refresh_from_db()
    assert matter.stage.key == "consultation"


@pytest.mark.parametrize(
    ("instruments", "from_stage", "to_stage"),
    [
        (("seadus",), "parliament", "in_force"),
        (("seadus",), "government", "consultation"),
        (("seadus",), "other", "consultation"),
        (("direktiiv",), "idea", "consultation"),
    ],
)
def test_a_crafted_tick_on_a_move_nobody_offered_writes_nothing(
    signed_in, specialist, instruments, from_stage, to_stage
):
    matter = _matter(specialist, instruments=instruments, stage=from_stage)

    response = _post(signed_in, matter, stage=to_stage, day=TODAY, tick=True)

    assert response.status_code == 200, response.content.decode()[:1500]
    matter.refresh_from_db()
    assert matter.stage.key == to_stage
    assert not MatterTimelineStep.objects.filter(matter=matter).exists()


def test_a_day_ahead_dates_no_phase(signed_in, specialist):
    """A plan is not a phase reached — the box is not offered, and a tick is inert."""
    matter = _matter(specialist)
    ahead = TODAY + dt.timedelta(days=5)

    response = _post(signed_in, matter, stage="consultation", day=ahead, tick=True)

    assert response.status_code == 200
    assert not MatterTimelineStep.objects.filter(matter=matter).exists()
    form = MatterProgressForm(
        {"title": "x", "occurred_on": ahead.strftime("%d.%m.%Y"), "stage": "1"},
        phases=phase_context(matter=matter),
        phase_offers=_offers(matter, specialist),
    )
    form.is_valid()
    assert form.phase_offer is None


def test_a_day_out_of_the_roadmaps_order_dates_no_phase(signed_in, specialist):
    """`Valitsuses 10.9` is set; `Kooskõlastusring 15.9` would read after it."""
    matter = _matter(specialist)
    set_timeline_steps(
        matter=matter,
        steps=[(PHASE_VALITSUS, False, dt.date(2026, 9, 10), DatePrecision.EXACT.value)],
        actor=specialist,
    )
    offer = _offers(matter, specialist)["consultation"]
    assert offer.latest == dt.date(2026, 9, 10)
    assert not offer.allows(TODAY, TODAY)

    _post(signed_in, matter, stage="consultation", day=TODAY, tick=True)

    assert _phase_row(matter, PHASE_KOOSKOLASTUS) is None


def test_the_use_case_decides_on_the_locked_row_without_the_form(specialist):
    """A caller that skips the form cannot date a phase on a move that does not qualify."""
    matter = _matter(specialist, stage="government")

    result = add_procedural_development(
        matter=matter,
        author=specialist,
        title="Tagasi kooskõlastusele",
        occurred_on=TODAY,
        stage=_stage("consultation"),
        date_phase=True,
    )

    assert result.phase_dated is False
    assert not MatterTimelineStep.objects.filter(matter=matter).exists()


def test_no_extra_teema_kaik_row_for_the_phase_date(signed_in, specialist):
    matter = _matter(specialist)
    _post(signed_in, matter, stage="consultation", day=TODAY, tick=True)

    items, _more = matter_timeline(matter=matter, user=specialist)
    rows = [item for item in items if item.is_milestone]

    # The `Märge` row, which carries the stage move, and nothing for the date.
    assert len([item for item in rows if item.procedural_development]) == 1
    assert not any("Menetluse kulg" in (item.milestone.what or "") for item in rows)


def test_menuuda_kulgu_still_corrects_and_clears_the_written_date(signed_in, specialist):
    matter = _matter(specialist)
    _post(signed_in, matter, stage="consultation", day=TODAY, tick=True)

    set_timeline_steps(
        matter=matter,
        steps=[(PHASE_KOOSKOLASTUS, False, None, DatePrecision.EXACT.value)],
        actor=specialist,
    )

    assert _phase_row(matter, PHASE_KOOSKOLASTUS) is None
    matter.refresh_from_db()
    assert matter.stage.key == "consultation"


def test_the_future_marge_still_makes_the_next_step(signed_in, specialist):
    """ADR 0124's route is untouched beside the new box."""
    matter = _matter(specialist)
    ahead = TODAY + dt.timedelta(days=3)

    signed_in.post(
        _add_note(matter),
        {
            "title": "Saadan ministeeriumile kirja",
            "occurred_on": ahead.strftime("%d.%m.%Y"),
            "as_next_step": "on",
        },
        HTTP_HX_REQUEST="true",
    )

    step = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert (step.text, step.target_date) == ("Saadan ministeeriumile kirja", ahead)
    assert not MatterTimelineStep.objects.filter(matter=matter).exists()


# ---------------------------------------------------------------------------
# The rail: a dated phase is reached, and dated points keep its order
# ---------------------------------------------------------------------------


@pytest.fixture
def send(capture_evidence):
    def _send(matter, on: dt.date):
        version = capture_evidence(
            matter, b"%PDF-1.4 synthetic sent opinion", "arvamus.pdf", "application/pdf"
        )
        return factories.SubmissionFactory(
            matter=matter,
            status=SubmissionStatus.SENT,
            sent_at=dt.datetime.combine(on, dt.time(11, 0), tzinfo=TALLINN),
            final_version=version,
        )

    return _send


@pytest.fixture
def vtk_file(specialist, send):
    """The QA's file: a VTK and a bill, still on `Idee`, the VTK dated 1.9."""
    matter = _matter(specialist, instruments=("vtk", "seadus"), stage="idea")
    set_timeline_steps(
        matter=matter,
        steps=[(PHASE_VTK, False, dt.date(2026, 9, 1), DatePrecision.EXACT.value)],
        actor=specialist,
    )
    add_engagement(
        matter=matter,
        kind="OTHER",
        title="Kohtumenetluses ettevõtteid esindavad juristid",
        occurred_on=dt.date(2026, 9, 3),
        feedback_deadline=dt.date(2026, 9, 6),
        actor=specialist,
    )
    send(matter, dt.date(2026, 9, 8))
    return matter


def test_a_dated_vtk_reads_as_recorded_not_ahead(vtk_file, specialist):
    states = _states(_rail(vtk_file, specialist))

    assert states["VTK"] == STATE_RECORDED
    assert states["Algus"] == STATE_CURRENT
    assert states["Kooskõlastusring"] == STATE_POSSIBLE


def test_the_exact_order_the_qa_found_broken(vtk_file, specialist):
    steps = _rail(vtk_file, specialist)

    # No `Tagasiside tähtaeg` column since docs/adr/0131 §13: the round and its
    # reply-by day read in `Teema käik`, and the rail keeps the procedure and the
    # opinion that went out.
    assert [(step.label, step.display_date) for step in steps][:4] == [
        ("Algus", ""),
        ("VTK", "1.9.2026"),
        (SENT_LABEL, "8.9.2026"),
        ("Kooskõlastusring", ""),
    ]
    assert FEEDBACK_DEADLINE_LABEL not in [step.label for step in steps]


def test_the_current_marker_stays_the_hetkeseis(vtk_file, specialist):
    steps = _rail(vtk_file, specialist)

    current = [step.label for step in steps if step.state == STATE_CURRENT]
    assert current == ["Algus"]


def test_the_rendered_rail_does_not_say_tulevikus_for_the_vtk(signed_in, vtk_file):
    body = signed_in.get(_teema(vtk_file)).content.decode()
    start = body.index("tl-step--recorded tl-step--phase")
    vtk_column = body[start : body.index("</span>\n", body.index("tl-step__what", start))]

    assert "VTK" in vtk_column
    assert "Tulevikus" not in body[start : start + 600]


def test_a_dated_phase_ahead_in_the_future_is_still_a_plan(specialist):
    matter = _matter(specialist, instruments=("vtk", "seadus"), stage="idea")
    set_timeline_steps(
        matter=matter,
        steps=[(PHASE_VTK, False, TODAY + dt.timedelta(days=10), DatePrecision.EXACT.value)],
        actor=specialist,
    )

    assert _states(_rail(matter, specialist))["VTK"] == STATE_POSSIBLE


def test_a_hidden_dated_phase_is_not_evidence(specialist):
    matter = _matter(specialist, instruments=("vtk", "seadus"), stage="idea")
    MatterTimelineStep.objects.create(
        matter=matter,
        phase_key=PHASE_VTK,
        hidden=True,
        occurs_on=dt.date(2026, 9, 1),
        occurs_on_precision=DatePrecision.EXACT.value,
    )

    assert "VTK" not in _states(_rail(matter, specialist))


@pytest.mark.parametrize(
    ("anchor", "reached"),
    [
        # «september 2026» on 15 September: the period has not ended.
        (dt.date(2026, 9, 1), False),
        # «august 2026»: ended.
        (dt.date(2026, 8, 1), True),
    ],
)
def test_a_period_is_reached_only_once_it_has_ended(specialist, anchor, reached):
    matter = _matter(specialist, instruments=("vtk", "seadus"), stage="idea")
    MatterTimelineStep.objects.create(
        matter=matter,
        phase_key=PHASE_VTK,
        occurs_on=anchor,
        occurs_on_precision=DatePrecision.MONTH.value,
    )

    state = _states(_rail(matter, specialist))["VTK"]
    assert (state == STATE_RECORDED) is reached


def test_an_added_step_still_reads_where_it_was_put(vtk_file, specialist):
    set_timeline_steps(
        matter=vtk_file,
        steps=[(PHASE_VTK, False, dt.date(2026, 9, 1), DatePrecision.EXACT.value)],
        added=[(None, "Koja arvamus VTK kohta", None, DatePrecision.EXACT.value, "vtk", False)],
        actor=specialist,
    )

    labels = [step.label for step in _rail(vtk_file, specialist)]
    assert labels.index("Koja arvamus VTK kohta") == labels.index("VTK") + 1
