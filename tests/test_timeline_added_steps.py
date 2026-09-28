"""`+ Lisa samm` — adding the steps the pattern did not draw (docs/adr/0119).

`Menetluse kulg` drew a procedure read off the `Õigusakt`, and a lawyer could
hide a phase or date one — and nothing else. A file whose procedure has no
`VTK`, or no procedure at all, or a second consultation round, had no way to say
so on the rail. What is protected here:

* the panel adds a step on **every** file: one with only `Algus`, one with no
  phase ahead, one read against no procedure at all;
* an added step has a name, a date at the shared precisions, and a place a
  person chose — between two items, after the last, before a future one;
* adding, correcting and removing one touches nothing else on the rail, and the
  phases stay editable exactly as before;
* removing `VTK` removes `VTK` and nothing that already happened;
* a phase the file reached is never hidden by an old preference, and one it
  recorded is not lost when the file is reclassified;
* `Teema käik` and the audit trail are untouched: one audit row per save, and
  no row of the history is an added step.
"""

from __future__ import annotations

import datetime
from datetime import date, timedelta
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.legal_process import (
    KIND_MILESTONE,
    KIND_PHASE,
    KIND_STEP,
    STATE_CURRENT,
    STATE_POSSIBLE,
    STATE_RECORDED,
    legal_process_rail,
    matter_rail,
    phase_context,
)
from app.matters.models import MatterProceduralDevelopment, MatterTimelineStep
from app.matters.process_phases import (
    PHASE_KOOSKOLASTUS,
    PHASE_RIIGIKOGU,
    PHASE_VALITSUS,
    PHASE_VTK,
)
from app.matters.process_timeline import STATE_AHEAD, STATE_REACHED, process_steps
from app.matters.services import (
    change_stage,
    set_timeline_steps,
    timeline_steps_revision_token,
)
from app.matters.workspace import add_procedural_development
from app.submissions.enums import SubmissionStatus
from app.taxonomy.models import LegalInstrumentType
from app.workflow.models import StageVocabulary
from tests import factories

pytestmark = pytest.mark.django_db

TALLINN = ZoneInfo("Europe/Tallinn")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _stage(key: str) -> StageVocabulary:
    return StageVocabulary.objects.get(key=key)


def _matter(owner, *, instruments: tuple[str, ...] = (), **kwargs):
    matter = factories.MatterFactory(owner=owner, track="", **kwargs)
    if instruments:
        matter.legal_instruments.set(
            [LegalInstrumentType.objects.get(key=key) for key in instruments]
        )
    return matter


def _rail(matter, user):
    context = phase_context(matter=matter)
    return matter_rail(
        matter=matter,
        user=user,
        rail=legal_process_rail(matter=matter, user=user, context=context),
        milestones=process_steps(matter=matter, user=user),
    )


def _labels(matter, user) -> list[str]:
    return [step.label for step in _rail(matter, user)]


def _add(matter, actor, title, *, when=None, precision="EXACT", after=""):
    set_timeline_steps(
        matter=matter,
        steps=[],
        added=[(None, title, when, precision, after, False)],
        actor=actor,
    )
    return MatterTimelineStep.objects.filter(matter=matter, title=title).latest("created_at")


@pytest.fixture
def send(capture_evidence):
    """A SENT `Submission` dated ``on`` — the `Koja arvamus` on the rail."""

    def _send(matter, on: date):
        version = capture_evidence(
            matter, b"%PDF-1.4 synthetic sent opinion", "arvamus.pdf", "application/pdf"
        )
        return factories.SubmissionFactory(
            matter=matter,
            status=SubmissionStatus.SENT,
            sent_at=datetime.datetime.combine(on, datetime.time(11, 0), tzinfo=TALLINN),
            final_version=version,
        )

    return _send


class _PanelState(HTMLParser):
    """What a browser would post from the rendered `Muuda` panel, untouched."""

    def __init__(self) -> None:
        super().__init__()
        self.data: dict[str, str] = {}
        self._select: str | None = None
        self._first_option: str | None = None

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        name = attr.get("name")
        if tag == "input" and name:
            kind = attr.get("type", "text")
            if kind in ("checkbox", "radio"):
                if "checked" in attr:
                    self.data[name] = attr.get("value", "on")
            elif "disabled" not in attr:
                self.data[name] = attr.get("value", "")
        elif tag == "select" and name:
            self._select, self._first_option = name, None
        elif tag == "option" and self._select:
            value = attr.get("value", "")
            if self._first_option is None:
                self._first_option = value
                self.data.setdefault(self._select, value)
            if "selected" in attr:
                self.data[self._select] = value

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None


def _panel(client, matter) -> str:
    response = client.get(reverse("matters:timeline_steps", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


def _post_panel(client, matter, **changes):
    """Post the panel as it opened, with ``changes`` typed over it."""
    state = _PanelState()
    state.feed(_panel(client, matter))
    data = {**state.data, **changes}
    data = {name: value for name, value in data.items() if value is not None}
    return client.post(reverse("matters:timeline_steps", kwargs={"pk": matter.pk}), data)


def _page(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _rail_html(page: str) -> str:
    start = page.index('class="lprail')
    return page[start : page.index("</section>", start)]


def _prefix(row) -> str:
    return f"samm{row.pk.hex}"


# ---------------------------------------------------------------------------
# 1 — every file can add a step
# ---------------------------------------------------------------------------


def test_a_matter_with_only_algus_can_add_a_step(signed_in, specialist):
    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("idea"), actor=specialist)
    hide = [
        (step.key, True, None, "EXACT")
        for step in _rail(matter, specialist)
        if step.kind == KIND_PHASE and step.label != "Algus"
    ]
    set_timeline_steps(matter=matter, steps=hide, actor=specialist)
    assert _labels(matter, specialist) == ["Algus"]

    response = _post_panel(signed_in, matter, uus__title="Komisjoni istung", uus__after="algus")

    assert response.status_code == 200
    assert _labels(matter, specialist) == ["Algus", "Komisjoni istung"]


def test_a_matter_with_no_procedure_gets_muuda_and_can_add_a_step(signed_in, specialist):
    """No `Õigusakt`, so no pattern and no phase — the file that most needs this."""
    matter = _matter(specialist)
    assert _rail(matter, specialist) == []

    page = _page(signed_in, matter)
    assert "lprail__edit" in page
    assert "lprail--empty" in page
    panel = _panel(signed_in, matter)
    assert "+ Lisa samm" in panel
    assert "__shown" not in panel

    _post_panel(signed_in, matter, uus__title="VTK", uus_date="12.03.2026")

    rail = _rail(matter, specialist)
    assert [(step.label, step.kind, step.display_date) for step in rail] == [
        ("VTK", KIND_STEP, "12.3.2026")
    ]


def test_a_matter_with_no_future_phases_can_add_a_step(signed_in, specialist, send):
    """Only completed points on the rail: a sent opinion and a passed deadline."""
    matter = _matter(specialist, response_deadline=timezone.localdate() - timedelta(days=5))
    send(matter, timezone.localdate() - timedelta(days=10))
    before = _labels(matter, specialist)
    assert before == ["Koja arvamus", "Arvamuse tähtaeg"]
    assert "lprail__edit" in _page(signed_in, matter)

    _post_panel(signed_in, matter, uus__title="Valitsuse istung")

    assert _labels(matter, specialist) == [*before, "Valitsuse istung"]


def test_the_empty_bar_is_for_writers_on_open_files_only(signed_in, specialist, reader):
    from django.test import Client

    from app.matters.services import close_matter
    from app.workflow.enums import Disposition

    matter = _matter(specialist)
    assert 'class="lprail' in _page(signed_in, matter)
    as_reader = Client()
    as_reader.force_login(reader)
    assert 'class="lprail' not in _page(as_reader, matter)

    # A closed file takes no new work, so it gets no empty bar either.
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist)
    assert "lprail--empty" not in _page(signed_in, matter)


# ---------------------------------------------------------------------------
# 2 — persist, edit, remove
# ---------------------------------------------------------------------------


def test_an_added_step_persists_after_reload(signed_in, specialist):
    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)

    _post_panel(signed_in, matter, uus__title="Komisjoni istung", uus_date="01.12.2026")

    row = MatterTimelineStep.objects.get(matter=matter, title="Komisjoni istung")
    assert row.is_added and row.phase_key == "" and row.hidden is False
    rail = _rail_html(_page(signed_in, matter))
    assert "Komisjoni istung" in rail
    assert "1.12.2026" in rail


def test_an_added_step_can_be_corrected(signed_in, specialist):
    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    row = _add(matter, specialist, "Komisjoni istung", when=date(2026, 12, 1))
    prefix = _prefix(row)

    response = _post_panel(
        signed_in,
        matter,
        **{
            f"{prefix}__title": "Majanduskomisjoni istung",
            f"{prefix}_precision": "MONTH",
            f"{prefix}_month": "1",
            f"{prefix}_year": "2027",
            f"{prefix}_date": "",
        },
    )

    assert response.status_code == 200
    row.refresh_from_db()
    assert row.title == "Majanduskomisjoni istung"
    assert (row.occurs_on, row.occurs_on_precision) == (date(2027, 1, 1), "MONTH")
    step = next(s for s in _rail(matter, specialist) if s.kind == KIND_STEP)
    assert step.label == "Majanduskomisjoni istung"
    assert step.display_date == row.display_date


def test_an_added_step_can_be_removed_and_the_audit_keeps_its_name(signed_in, specialist):
    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    row = _add(matter, specialist, "Komisjoni istung")
    before = _labels(matter, specialist)

    _post_panel(signed_in, matter, **{f"{_prefix(row)}__remove": "on"})

    assert not MatterTimelineStep.objects.filter(pk=row.pk).exists()
    assert _labels(matter, specialist) == [label for label in before if label != "Komisjoni istung"]
    event = ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.TIMELINE_STEPS_CHANGED
    ).latest("occurred_at")
    assert event.payload["steps"] == [
        {"change": "eemaldatud", "title": "Komisjoni istung", "step": str(row.pk)}
    ]


def test_a_step_needs_a_name_and_the_panel_says_so(signed_in, specialist):
    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)

    response = _post_panel(signed_in, matter, uus__title="", uus_date="01.12.2026")

    assert response.status_code == 400
    assert "Sammul peab olema nimetus." in response.content.decode()
    assert response["HX-Retarget"] == "#menetluse-kulg-muuda"
    assert not MatterTimelineStep.objects.filter(matter=matter).exists()


def test_an_empty_new_step_adds_nothing(signed_in, specialist):
    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    events = ChangeEvent.objects.filter(matter=matter).count()

    _post_panel(signed_in, matter)

    assert not MatterTimelineStep.objects.filter(matter=matter).exists()
    assert ChangeEvent.objects.filter(matter=matter).count() == events


# ---------------------------------------------------------------------------
# 3 — the generated steps stay as they were
# ---------------------------------------------------------------------------


def _vtk_file(specialist, send):
    """Algus → Koja arvamus → Arvamuse tähtaeg → VTK → Kooskõlastusring → …

    The owner's own example: the file is on `Idee`, so `Algus` is where it
    stands; an opinion went out and its deadline passed, both read inside the
    phase that began the procedure; `VTK` and everything after it lie ahead.
    """
    today = timezone.localdate()
    matter = _matter(
        specialist, instruments=("vtk", "seadus"), response_deadline=today - timedelta(days=3)
    )
    change_stage(matter=matter, stage=_stage("idea"), actor=specialist)
    send(matter, today - timedelta(days=20))
    return matter


def test_the_generated_vtk_step_is_still_editable(signed_in, specialist, send):
    matter = _vtk_file(specialist, send)
    _add(matter, specialist, "Komisjoni istung")

    _post_panel(signed_in, matter, vtk__date="15.01.2026")

    vtk = next(step for step in _rail(matter, specialist) if step.key == PHASE_VTK)
    assert vtk.display_date == "15.1.2026"
    assert MatterTimelineStep.objects.get(matter=matter, phase_key=PHASE_VTK).occurs_on == date(
        2026, 1, 15
    )
    # The added step is untouched by a phase edit.
    assert MatterTimelineStep.objects.filter(matter=matter, title="Komisjoni istung").count() == 1


def test_removing_vtk_removes_only_vtk(signed_in, specialist, send):
    matter = _vtk_file(specialist, send)
    before = _rail(matter, specialist)
    labels = [step.label for step in before]
    assert labels[:4] == ["Algus", "Koja arvamus", "Arvamuse tähtaeg", "VTK"]

    _post_panel(signed_in, matter, vtk__shown=None)

    after = _rail(matter, specialist)
    assert [step.label for step in after] == [label for label in labels if label != "VTK"]
    # Earlier completed milestones are exactly as they were: same state, same date.
    kept = {step.key: (step.state, step.display_date) for step in after}
    for step in before:
        if step.key != PHASE_VTK:
            assert kept[step.key] == (step.state, step.display_date), step.label
    assert kept["algus"][0] == STATE_CURRENT
    assert [kept[step.key][0] for step in before if step.kind == KIND_MILESTONE] == [
        STATE_REACHED,
        STATE_REACHED,
    ]
    assert MatterTimelineStep.objects.filter(matter=matter).count() == 1


def test_adding_a_step_overwrites_nothing(signed_in, specialist, send):
    matter = _vtk_file(specialist, send)
    set_timeline_steps(
        matter=matter,
        steps=[
            (PHASE_VALITSUS, False, date(2027, 2, 1), "EXACT"),
            (PHASE_RIIGIKOGU, True, None, "EXACT"),
        ],
        actor=specialist,
    )
    stored = sorted(
        MatterTimelineStep.objects.filter(matter=matter).values_list(
            "phase_key", "hidden", "occurs_on", "updated_at"
        )
    )
    before = [(step.key, step.state, step.display_date) for step in _rail(matter, specialist)]

    _post_panel(signed_in, matter, uus__title="Komisjoni istung", uus__after="valitsus")

    assert (
        sorted(
            MatterTimelineStep.objects.filter(matter=matter)
            .exclude(phase_key="")
            .values_list("phase_key", "hidden", "occurs_on", "updated_at")
        )
        == stored
    )
    after = [(s.key, s.state, s.display_date) for s in _rail(matter, specialist)]
    assert [one for one in after if not one[0].startswith("samm:")] == before


# ---------------------------------------------------------------------------
# 4 — repetition and placement
# ---------------------------------------------------------------------------


def test_repeated_titles_are_allowed(specialist):
    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    _add(matter, specialist, "Kooskõlastusring", after=PHASE_KOOSKOLASTUS)
    _add(matter, specialist, "Kooskõlastusring", after=PHASE_KOOSKOLASTUS)
    _add(matter, specialist, "Koja arvamus")
    _add(matter, specialist, "Koja arvamus")

    labels = _labels(matter, specialist)
    assert labels.count("Kooskõlastusring") == 3
    assert labels.count("Koja arvamus") == 2
    assert MatterTimelineStep.objects.filter(matter=matter, phase_key="").count() == 4


def test_a_step_goes_exactly_between_two_existing_milestones(specialist, send):
    matter = _vtk_file(specialist, send)
    sent = next(s for s in _rail(matter, specialist) if s.label == "Koja arvamus")

    _add(matter, specialist, "Tagasiside kogutud", after=sent.key)

    labels = _labels(matter, specialist)
    index = labels.index("Tagasiside kogutud")
    assert labels[index - 1 : index + 2] == [
        "Koja arvamus",
        "Tagasiside kogutud",
        "Arvamuse tähtaeg",
    ]


def test_a_step_goes_before_a_future_phase_and_at_the_end(specialist):
    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)

    _add(matter, specialist, "Komisjoni istung", after=PHASE_VALITSUS)
    last = _rail(matter, specialist)[-1]
    _add(matter, specialist, "Järelhindamine", after=last.key)
    _add(matter, specialist, "Eelnõu idee", after="")

    labels = _labels(matter, specialist)
    assert labels[0] == "Eelnõu idee"
    assert labels[labels.index("Valitsuses") + 1] == "Komisjoni istung"
    assert labels[labels.index("Komisjoni istung") + 1] == "Riigikogus"
    assert labels[-1] == "Järelhindamine"


def test_the_second_step_after_one_anchor_reads_nearest_to_it(specialist):
    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    _add(matter, specialist, "Esimene", after=PHASE_VALITSUS)
    _add(matter, specialist, "Teine", after=PHASE_VALITSUS)

    labels = _labels(matter, specialist)
    at = labels.index("Valitsuses")
    assert labels[at : at + 3] == ["Valitsuses", "Teine", "Esimene"]


def test_a_step_after_a_hidden_phase_stays_where_the_phase_was(specialist):
    matter = _matter(specialist, instruments=("vtk", "seadus"))
    change_stage(matter=matter, stage=_stage("idea"), actor=specialist)
    _add(matter, specialist, "VTK avalik arutelu", after=PHASE_VTK)

    set_timeline_steps(matter=matter, steps=[(PHASE_VTK, True, None, "EXACT")], actor=specialist)

    labels = _labels(matter, specialist)
    assert "VTK" not in labels
    at = labels.index("VTK avalik arutelu")
    assert labels[at + 1] == "Kooskõlastusring"


def test_an_anchor_that_is_gone_places_the_step_by_its_date(specialist):
    matter = _matter(specialist, response_deadline=date(2026, 6, 1))
    _add(matter, specialist, "Istung", when=date(2026, 7, 1), after="milestone:kadunud:2026-01-01")
    _add(matter, specialist, "Varasem", when=date(2026, 5, 1), after="milestone:kadunud:2026-01-01")

    assert _labels(matter, specialist) == ["Varasem", "Arvamuse tähtaeg", "Istung"]


def test_a_step_cannot_be_placed_after_itself(signed_in, specialist):
    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    first = _add(matter, specialist, "Esimene", after=PHASE_VALITSUS)
    second = _add(matter, specialist, "Teine", after=first.rail_key)

    response = _post_panel(signed_in, matter, **{f"{_prefix(first)}__after": second.rail_key})

    assert response.status_code == 400
    assert "Samm ei saa asuda iseenda järel." in response.content.decode()
    first.refresh_from_db()
    assert first.after_key == PHASE_VALITSUS


# ---------------------------------------------------------------------------
# 5 — dates and states
# ---------------------------------------------------------------------------


def test_the_shared_date_precisions_work_on_an_added_step(signed_in, specialist):
    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    panel = _panel(signed_in, matter)
    for chip in ("Täpne päev", "Kuu", "Kvartal", "Aasta"):
        assert chip in panel

    _post_panel(
        signed_in,
        matter,
        uus__title="Jõustumise kavandatav aeg",
        uus_precision="QUARTER",
        uus_quarter="4",
        uus_year="2027",
    )

    row = MatterTimelineStep.objects.get(matter=matter, phase_key="")
    assert (row.occurs_on, row.occurs_on_precision) == (date(2027, 10, 1), "QUARTER")
    step = next(s for s in _rail(matter, specialist) if s.kind == KIND_STEP)
    assert step.display_date == row.display_date
    assert "2027" in step.display_date and "10.2027" not in step.display_date


def test_a_half_stated_period_is_refused_on_its_own_control(signed_in, specialist):
    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)

    response = _post_panel(
        signed_in, matter, uus__title="Istung", uus_precision="MONTH", uus_year="2027"
    )

    assert response.status_code == 400
    assert not MatterTimelineStep.objects.filter(matter=matter).exists()


def test_an_added_step_reads_against_today_like_a_dated_point(specialist):
    today = timezone.localdate()
    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    _add(matter, specialist, "Möödunud", when=today - timedelta(days=3), after="")
    _add(matter, specialist, "Tulev", when=today + timedelta(days=30), after=PHASE_VALITSUS)
    _add(matter, specialist, "Kuupäevata ees", after=PHASE_RIIGIKOGU)
    _add(matter, specialist, "Kuupäevata taga", after="")

    states = {step.label: step.state for step in _rail(matter, specialist)}
    assert states["Möödunud"] == STATE_REACHED
    assert states["Tulev"] == STATE_AHEAD
    assert states["Kuupäevata ees"] == STATE_POSSIBLE
    assert states["Kuupäevata taga"] == STATE_RECORDED


# ---------------------------------------------------------------------------
# 6 — reached phases stay; recorded phases survive a reclassification
# ---------------------------------------------------------------------------


def test_a_hidden_phase_is_drawn_again_once_the_file_reaches_it(specialist):
    """The domain act wins, deterministically, and the preference is not rewritten."""
    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    set_timeline_steps(
        matter=matter, steps=[(PHASE_VALITSUS, True, None, "EXACT")], actor=specialist
    )
    assert "Valitsuses" not in _labels(matter, specialist)

    change_stage(matter=matter, stage=_stage("government"), actor=specialist)
    rail = _rail(matter, specialist)
    assert next(s for s in rail if s.key == PHASE_VALITSUS).state == STATE_CURRENT

    change_stage(matter=matter, stage=_stage("parliament"), actor=specialist)
    rail = _rail(matter, specialist)
    assert next(s for s in rail if s.key == PHASE_VALITSUS).state == STATE_RECORDED
    assert MatterTimelineStep.objects.get(matter=matter, phase_key=PHASE_VALITSUS).hidden is True


def test_a_recorded_phase_survives_the_file_being_reclassified(specialist):
    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("parliament"), actor=specialist)
    add_procedural_development(matter=matter, author=specialist, title="Eelnõu jõudis Riigikokku")
    MatterProceduralDevelopment.objects.filter(matter=matter).update(process_phase=PHASE_RIIGIKOGU)
    assert "Riigikogus" in _labels(matter, specialist)

    # A regulation is not adopted by Parliament, so its pattern has no such phase.
    matter.legal_instruments.set([LegalInstrumentType.objects.get(key="maarus")])
    rail = _rail(matter, specialist)
    kept = next(step for step in rail if step.key == PHASE_RIIGIKOGU)
    assert kept.state == STATE_RECORDED

    # And with no procedure at all.
    matter.legal_instruments.clear()
    assert [s.label for s in _rail(matter, specialist) if s.kind == KIND_PHASE] == ["Riigikogus"]


def test_a_future_phase_the_new_pattern_lacks_goes_with_it(specialist):
    """Only what happened is kept; a plan for another procedure is not."""
    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    set_timeline_steps(
        matter=matter,
        steps=[(PHASE_RIIGIKOGU, False, timezone.localdate() + timedelta(days=90), "EXACT")],
        actor=specialist,
    )
    matter.legal_instruments.set([LegalInstrumentType.objects.get(key="maarus")])

    assert "Riigikogus" not in _labels(matter, specialist)


# ---------------------------------------------------------------------------
# 7 — permissions, closure, visibility
# ---------------------------------------------------------------------------


def test_a_reader_can_neither_open_nor_post_the_panel(client, specialist, reader):
    matter = _matter(specialist, instruments=("seadus",))
    client.force_login(reader)
    url = reverse("matters:timeline_steps", kwargs={"pk": matter.pk})

    assert client.get(url).status_code in (403, 404)
    response = client.post(url, {"uus__title": "Komisjoni istung"})

    assert response.status_code in (403, 404)
    assert not MatterTimelineStep.objects.filter(matter=matter).exists()
    assert "lprail__edit" not in _page(client, matter)


def test_a_closed_file_refuses_an_added_step(specialist):
    from app.core.errors import DomainError
    from app.matters.locks import CLOSED_MATTER_REFUSAL
    from app.matters.services import close_matter
    from app.workflow.enums import Disposition

    matter = _matter(specialist)
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist)

    with pytest.raises(DomainError) as refusal:
        _add(matter, specialist, "Komisjoni istung")
    assert str(refusal.value) == CLOSED_MATTER_REFUSAL
    assert not MatterTimelineStep.objects.filter(matter=matter).exists()


def test_a_crafted_id_reaches_no_other_files_step(specialist):
    mine = _matter(specialist)
    other = _matter(specialist)
    theirs = _add(other, specialist, "Võõras samm")

    set_timeline_steps(
        matter=mine,
        steps=[],
        added=[(theirs.pk, "", None, "EXACT", "", True)],
        actor=specialist,
    )

    assert MatterTimelineStep.objects.filter(pk=theirs.pk).exists()


def test_a_restricted_added_step_is_not_drawn_for_a_reader(specialist, reader):
    matter = _matter(specialist)
    row = _add(matter, specialist, "Piiratud samm")
    MatterTimelineStep.objects.filter(pk=row.pk).update(visibility_override="RESTRICTED")

    assert "Piiratud samm" in _labels(matter, specialist)
    assert "Piiratud samm" not in _labels(matter, reader)


# ---------------------------------------------------------------------------
# 8 — nothing else moves: existing files, Teema käik, the audit trail
# ---------------------------------------------------------------------------


def test_a_file_nobody_edited_draws_the_rail_it_drew_before(specialist, send):
    """No rows, no change: the same labels, states and dates as the pattern gives."""
    matter = _vtk_file(specialist, send)
    rail = _rail(matter, specialist)
    assert [(s.label, s.kind) for s in rail] == [
        ("Algus", KIND_PHASE),
        ("Koja arvamus", KIND_MILESTONE),
        ("Arvamuse tähtaeg", KIND_MILESTONE),
        ("VTK", KIND_PHASE),
        ("Kooskõlastusring", KIND_PHASE),
        ("Valitsuses", KIND_PHASE),
        ("Riigikogus", KIND_PHASE),
        ("Jõustumine", KIND_PHASE),
    ]
    assert not MatterTimelineStep.objects.filter(matter=matter).exists()


def test_the_revision_token_of_an_unedited_file_is_unchanged_by_added_steps(specialist):
    """A file with only phase rows keeps the token it had before this round."""
    import hashlib

    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    set_timeline_steps(
        matter=matter, steps=[(PHASE_RIIGIKOGU, True, None, "EXACT")], actor=specialist
    )

    expected = hashlib.sha256(f"{PHASE_RIIGIKOGU}|1||EXACT".encode()).hexdigest()
    assert timeline_steps_revision_token(matter) == expected
    _add(matter, specialist, "Komisjoni istung")
    assert timeline_steps_revision_token(matter) != expected


def test_saves_write_one_audit_row_each_and_delete_none(signed_in, specialist):
    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    ids = set(ChangeEvent.objects.filter(matter=matter).values_list("pk", flat=True))

    _post_panel(signed_in, matter, uus__title="Komisjoni istung")
    row = MatterTimelineStep.objects.get(matter=matter, phase_key="")
    _post_panel(signed_in, matter, **{f"{_prefix(row)}__title": "Komisjoni teine istung"})
    _post_panel(signed_in, matter, **{f"{_prefix(row)}__remove": "on"})

    after = ChangeEvent.objects.filter(matter=matter)
    assert ids <= set(after.values_list("pk", flat=True))
    new = list(after.exclude(pk__in=ids).order_by("occurred_at", "pk"))
    assert [event.event_type for event in new] == [ChangeEventType.TIMELINE_STEPS_CHANGED] * 3
    assert [event.payload["steps"][0]["change"] for event in new] == [
        "lisatud",
        "muudetud",
        "eemaldatud",
    ]


def test_teema_kaik_does_not_carry_an_added_step(signed_in, specialist):
    """The rail is not the record: `Teema käik` lists acts, and this is not one."""
    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    before = _page(signed_in, matter)
    history_before = before[before.index('id="ajajoon"') :]

    _post_panel(signed_in, matter, uus__title="Ainult rajal", uus_date="02.02.2026")

    page = _page(signed_in, matter)
    assert "Ainult rajal" in _rail_html(page)
    history = page[page.index('id="ajajoon"') :]
    assert MatterProceduralDevelopment.objects.filter(matter=matter).count() == 0
    assert _without_tokens(history_before) == _without_tokens(history)


def _without_tokens(html: str) -> str:
    """The page minus its per-request CSRF and revision values."""
    import re

    return re.sub(r'value="[0-9A-Za-z]{32,}"', 'value=""', html)
