"""One simplification of the Teema page, asserted where each part is decided.

The owner's feedback of 2026-09-27, six changes that read as one pass:

1. **`+ Märge` has no `Etapp`.** The field is gone from the form, the template
   and the use case behind it, nothing is inferred in its place, and a phase an
   older row stores is still read and still corrected (docs/adr/0105, amended);
2. **`Kuupäev` is the width of a date** under `+ Märge` and under
   `+ Arvamus / tagasiside` (`.cx-f--solo`; the width itself is measured in the
   browser lane, `e2e/test_teema_page_cleanup.py`);
3. **`+ Lõpeta teema` showed three chips and `Lõppsõna`** (retired by
   docs/adr/0131 §11), with `Kuidas lõppes` left to assistive technology and the
   archive sentence gone;
4. **`Menetluse kulg` draws one solid run** — reached, then the current phase,
   then everything ahead muted — however many opinions a file sent
   (`legal_process._one_backbone`);
5. **`Teema käik` speaks up for three outcomes only** — «Arvamus välja», a
   published `Ülevaade / uudis`, the closure — and draws every other row
   quieter (`TimelineItem.is_primary`);
6. **neither section prints its heading**, and both keep it for a screen reader.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.matters.enums import ExternalPositionProvenance
from app.matters.forms import MatterProgressForm, ProceduralDevelopmentEditForm
from app.matters.legal_process import (
    STATE_CURRENT,
    legal_process_rail,
    matter_rail,
    phase_context,
    recorded_phase_keys,
)
from app.matters.models import MatterProceduralDevelopment
from app.matters.process_phases import PHASE_KOOSKOLASTUS
from app.matters.process_timeline import process_steps
from app.matters.timeline import matter_timeline
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction
from tests import factories

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Shared world
# ---------------------------------------------------------------------------


def _teema(matter) -> str:
    return reverse("matters:matter_detail", kwargs={"pk": matter.pk})


def _add_note(matter) -> str:
    return reverse("matters:add_note", kwargs={"pk": matter.pk})


def _stage(key: str):
    from app.workflow.models import StageVocabulary

    return StageVocabulary.objects.get(key=key)


def _pdf(name: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"%PDF-1.4 synthetic evidence", content_type="application/pdf")


@pytest.fixture
def procedure_matter(specialist):
    """A `Seadus` on `Kooskõlastusringil` — a file whose procedure *has* phases.

    The one shape on which the old `Etapp` select rendered and pre-selected
    itself, so it is the shape on which its absence means something.
    """
    from app.taxonomy.models import LegalInstrumentType

    matter = factories.MatterFactory(owner=specialist, track="")
    matter.legal_instruments.set([LegalInstrumentType.objects.get(key="seadus")])
    matter.stage = _stage("consultation")
    matter.save(update_fields=["stage", "updated_at"])
    return matter


def _marge_panel(body: str) -> str:
    """The `+ Märge` form, from its first control to its own `</form>`."""
    start = body.index('id="id_marge_title"')
    start = body.rindex("<form", 0, start)
    return body[start : body.index("</form>", start)]


def _sent_opinion(matter, capture_evidence, *, days_ago: int):
    from app.submissions.enums import SubmissionStatus

    return factories.SubmissionFactory(
        matter=matter,
        status=SubmissionStatus.SENT,
        sent_at=timezone.now() - timedelta(days=days_ago),
        final_version=capture_evidence(
            matter, f"%PDF-1.4 {days_ago}".encode(), f"arvamus-{days_ago}.pdf", "application/pdf"
        ),
    )


# ---------------------------------------------------------------------------
# A — `+ Märge` has no `Etapp`
# ---------------------------------------------------------------------------


def test_the_marge_form_declares_no_phase_field(procedure_matter):
    """A1. Not hidden, not a hidden input, not pre-selected: not a field."""
    form = MatterProgressForm(phases=phase_context(matter=procedure_matter))

    assert "process_phase" not in form.fields


def test_the_marge_panel_shows_no_etapp_and_carries_no_phase_input(signed_in, procedure_matter):
    """A2, A3. On the file where the select used to render, nothing of it is left."""
    panel = _marge_panel(signed_in.get(_teema(procedure_matter)).content.decode())

    assert "Etapp" not in panel
    assert "process_phase" not in panel
    assert 'id="id_marge_process_phase"' not in panel
    # And the date is on a row of its own, not the first half of a grid whose
    # second half is now empty.
    date_label = panel[panel.rindex("<label", 0, panel.index('id="id_marge_occurred_on"')) :]
    assert date_label.startswith('<label class="cx-f cx-f--date cx-f--solo"')
    assert '<div class="cx-grid cx-grid--links">\n    <label class="cx-f cx-f--date' not in panel


def test_a_crafted_phase_on_the_marge_route_places_nothing(signed_in, procedure_matter):
    """A4. The name a crafted request would send, on a file with that phase."""
    response = signed_in.post(
        _add_note(procedure_matter),
        {"title": "Ministeerium saatis uue versiooni", "process_phase": PHASE_KOOSKOLASTUS},
    )

    assert response.status_code == 200
    development = MatterProceduralDevelopment.objects.get(matter=procedure_matter)
    assert development.process_phase == ""


def test_the_marge_use_case_has_no_phase_parameter(procedure_matter, specialist):
    """A4, one layer down. No caller of the use case can place the record."""
    from app.matters.workspace import add_procedural_development

    with pytest.raises(TypeError):
        add_procedural_development(  # type: ignore[call-arg]
            matter=procedure_matter,
            author=specialist,
            title="Midagi juhtus",
            process_phase=PHASE_KOOSKOLASTUS,
        )


def test_a_title_only_marge_saves_with_no_phase(signed_in, procedure_matter):
    """A5."""
    signed_in.post(_add_note(procedure_matter), {"title": "Rääkisin ministeeriumiga"})

    development = MatterProceduralDevelopment.objects.get(matter=procedure_matter)
    assert development.title == "Rääkisin ministeeriumiga"
    assert development.process_phase == ""


def test_a_file_only_marge_saves_with_no_phase(signed_in, procedure_matter, evidence_root):
    """A6."""
    signed_in.post(
        _add_note(procedure_matter),
        {"attachments": [_pdf("ministeeriumi_kiri.pdf")]},
    )

    development = MatterProceduralDevelopment.objects.get(matter=procedure_matter)
    assert development.title == ""
    assert development.process_phase == ""
    history = signed_in.get(_teema(procedure_matter)).content.decode()
    assert "ministeeriumi_kiri.pdf" in history[history.index('id="ajajoon"') :]


def test_a_stage_only_marge_moves_the_stage_and_places_no_phase(signed_in, procedure_matter):
    """A7, A10. The stage moves as it always did; the rail gains no `Kirjas`.

    `Valitsuses` is a phase this pattern has, so the old select would have been
    one pre-selection away from recording it. The node is `Praegu` because the
    stage says so, and no recorded phase exists anywhere.
    """
    signed_in.post(_add_note(procedure_matter), {"stage": str(_stage("government").pk)})

    procedure_matter.refresh_from_db()
    assert procedure_matter.stage.key == "government"
    development = MatterProceduralDevelopment.objects.get(matter=procedure_matter)
    assert development.process_phase == ""
    assert recorded_phase_keys(matter=procedure_matter, user=procedure_matter.owner) == set()


def test_a_next_step_only_marge_sets_the_step_and_places_no_phase(signed_in, procedure_matter):
    """A8. A planned activity made the next step places no phase either.

    Since docs/adr/0124 the step is the activity itself, dated ahead and ticked.
    """
    ahead = timezone.localdate() + timedelta(days=5)
    signed_in.post(
        _add_note(procedure_matter),
        {
            "title": "Vaatan uue versiooni üle",
            "occurred_on": f"{ahead.day}.{ahead.month}.{ahead.year}",
            "as_next_step": "on",
        },
    )

    step = NextAction.objects.get(matter=procedure_matter, status=ActionStatus.OPEN)
    assert step.text == "Vaatan uue versiooni üle"
    assert MatterProceduralDevelopment.objects.get(matter=procedure_matter).process_phase == ""


def test_a_historical_phase_is_still_read_and_still_corrected(signed_in, procedure_matter):
    """A9. The rows written before the decision keep what they say.

    It still marks its node on the rail, and `Muuda` on it still opens on the
    stored phase and can move it.
    """
    from app.matters.workspace import add_procedural_development

    development = factories.historical_phase(
        add_procedural_development(
            matter=procedure_matter, author=procedure_matter.owner, title="Eelnõu ringile"
        ).record,
        PHASE_KOOSKOLASTUS,
    )

    assert PHASE_KOOSKOLASTUS in recorded_phase_keys(
        matter=procedure_matter, user=procedure_matter.owner
    )
    body = signed_in.get(
        reverse(
            "matters:update_development",
            kwargs={"pk": procedure_matter.pk, "development_id": development.pk},
        )
    ).content.decode()
    marker = f'id="id_menetluse_areng_{development.pk}_process_phase"'
    assert marker in body
    assert f'value="{PHASE_KOOSKOLASTUS}" selected' in body[body.index(marker) :][:1500]


def test_correcting_a_marge_with_no_phase_offers_none_and_places_none(signed_in, procedure_matter):
    """A10, on the correction path. No proposal hides behind `Muuda` either.

    The correction form used to open an unplaced row on the phase `Hetkeseis`
    proposed, so correcting the *title* of a new `Märge` would have filed it.
    """
    signed_in.post(_add_note(procedure_matter), {"title": "Ministeerium saatis"})
    development = MatterProceduralDevelopment.objects.get(matter=procedure_matter)

    form = ProceduralDevelopmentEditForm(
        record=development, phases=phase_context(matter=procedure_matter)
    )
    assert "process_phase" not in form.fields

    url = reverse(
        "matters:update_development",
        kwargs={"pk": procedure_matter.pk, "development_id": development.pk},
    )
    assert "process_phase" not in signed_in.get(url).content.decode()
    signed_in.post(
        url,
        {
            "title": "Ministeerium saatis uue versiooni",
            "occurred_on": "",
            "areng_precision": "EXACT",
            "process_phase": PHASE_KOOSKOLASTUS,
            "revision": development.revision_token,
        },
    )
    development.refresh_from_db()
    assert development.process_phase == ""


# ---------------------------------------------------------------------------
# B — `Kuupäev` is the width of a date
# ---------------------------------------------------------------------------


def test_the_feedback_date_keeps_its_control_and_is_marked_compact(signed_in, normal_matter):
    """B11, B12. The field and its calendar hook, on the compact primitive.

    Both `+ Arvamus / tagasiside` children that ask for `Kuupäev` alone on a row
    carry `.cx-f--solo`; the width it resolves to is the browser lane's.
    """
    body = signed_in.get(_teema(normal_matter)).content.decode()
    panel = body[body.index('id="lisa-arvamus"') : body.index('id="arvamus-koja"')]

    labels = re.findall(r'<label class="([^"]*)" for="id_[a-z_]*stated_on"', panel)
    assert len(labels) == 2, labels
    assert all("cx-f--solo" in classes.split() for classes in labels)
    assert panel.count("data-datepicker") >= 2


def test_the_compact_date_rule_is_a_cap_not_a_width():
    """B13, B14 as far as a stylesheet can say it: capped, and free to shrink."""
    from pathlib import Path

    css = Path("static/css/app.css").read_text(encoding="utf-8")
    rule = css[css.index("#teema-vaade-wrap .cx-f--solo {") :]
    rule = rule[: rule.index("}")]
    assert "max-width: 160px" in rule
    assert re.search(r"(?<!max-)width:", rule) is None


# ---------------------------------------------------------------------------
# C — `+ Lõpeta teema`, retired by docs/adr/0131 §11
# ---------------------------------------------------------------------------


def test_there_is_no_closing_panel_and_the_stage_says_how_a_file_ends(signed_in, normal_matter):
    """C16–C21 described a panel that is gone: a file ends through `Hetkeseis`."""
    body = signed_in.get(_teema(normal_matter)).content.decode()

    assert 'id="teema-lopeta"' not in body
    assert "+ Lõpeta teema" not in body
    assert 'name="closing_words"' not in body
    assert "Jõustunud — lõpetab teema" in body
    assert "Rohkem ei tegele — lõpetab teema" in body


# ---------------------------------------------------------------------------
# D — `Menetluse kulg`: one solid run, however many opinions went out
# ---------------------------------------------------------------------------


def _rail(matter, user):
    return matter_rail(
        matter=matter,
        user=user,
        rail=legal_process_rail(matter=matter, user=user),
        milestones=process_steps(matter=matter, user=user),
    )


def _is_one_run(reaches: list[float]) -> bool:
    """Muted, then solid, then at most one part-filled connector, then muted."""
    shape = "".join("0" if r == 0 else "1" if r == 1 else "p" for r in reaches)
    return re.fullmatch(r"0*1*p?0*", shape) is not None


@pytest.fixture
def three_opinions(procedure_matter, capture_evidence):
    """The owner's case: three sent `Koja arvamus`, a current phase, a road ahead,
    and this office's own deadline still to come."""
    for days in (90, 40, 10):
        _sent_opinion(procedure_matter, capture_evidence, days_ago=days)
    procedure_matter.response_deadline = timezone.localdate() + timedelta(days=20)
    procedure_matter.save(update_fields=["response_deadline", "updated_at"])
    return procedure_matter


def test_three_opinions_draw_one_run_ending_at_the_current_phase(three_opinions, specialist):
    """D22–D25. The reported shape, as the model the template draws.

    Before: the last opinion's connector was filled a third of the way (the
    strip's measurement towards the deadline, three columns further on), and the
    current phase then started a second solid run into the deadline after it.
    """
    steps = _rail(three_opinions, specialist)

    labels = [step.label for step in steps]
    assert labels.count("Koja arvamus") == 3
    assert len({step.display_date for step in steps if step.label == "Koja arvamus"}) == 3
    assert [step.state for step in steps].count(STATE_CURRENT) == 1

    reaches = [step.reach for step in steps]
    assert _is_one_run(reaches), list(zip(labels, reaches, strict=True))
    current = next(i for i, step in enumerate(steps) if step.state == STATE_CURRENT)
    # Solid from the first opinion right up to the current phase …
    first = labels.index("Koja arvamus")
    assert reaches[first:current] == [1.0] * (current - first)
    # … and nothing solid from there on.
    assert all(reach == 0.0 for reach in reaches[current:])
    # Everything before the current phase is behind us, everything after it ahead.
    for step in steps[first:current]:
        assert step.state in ("past", "today")
    for step in steps[current + 1 :]:
        assert step.state in ("future", "possible")


def test_the_page_draws_that_run(signed_in, three_opinions):
    """D25, in the markup: one `--current`, and `--tl-reach` in one run."""
    body = signed_in.get(_teema(three_opinions)).content.decode()
    section = body[body.index('<section class="lprail"') :]
    section = section[: section.index("</section>")]

    steps = re.findall(
        r'class="tl-step tl-step--(\w+) tl-step--(\w+)"\s+style="--tl-reach: ([\d.]+)%"', section
    )
    assert [state for state, _kind, _reach in steps].count("current") == 1
    assert [kind for _state, kind, _reach in steps].count("milestone") == 4
    assert _is_one_run([float(reach) / 100 for _state, _kind, reach in steps])


def test_a_late_entry_file_draws_no_solid_run_before_its_current_phase(specialist):
    """D24. The late-entry rule is about states, and they are untouched.

    A bill first filed in the Riigikogu: every earlier phase `Teadmata`, and no
    connector drawn solid behind any of them (docs/adr/0092 §13).
    """
    from app.taxonomy.models import LegalInstrumentType

    matter = factories.MatterFactory(owner=specialist, track="")
    matter.legal_instruments.set([LegalInstrumentType.objects.get(key="seadus")])
    matter.stage = _stage("parliament")
    matter.save(update_fields=["stage", "updated_at"])

    steps = _rail(matter, specialist)
    current = next(i for i, step in enumerate(steps) if step.state == STATE_CURRENT)
    assert all(step.state == "unknown" for step in steps[:current])
    assert all(step.reach == 0.0 for step in steps)


def test_a_rail_with_no_phases_is_the_strip_exactly(normal_matter, specialist, capture_evidence):
    """D26. With nothing to merge, the connectors are the strip's own."""
    for days in (30, 5):
        _sent_opinion(normal_matter, capture_evidence, days_ago=days)
    normal_matter.response_deadline = timezone.localdate() + timedelta(days=15)
    normal_matter.save(update_fields=["response_deadline", "updated_at"])

    strip = process_steps(matter=normal_matter, user=specialist)
    rail = matter_rail(matter=normal_matter, user=specialist, rail=None, milestones=strip)

    assert [step.label for step in rail] == [step.label for step in strip]
    assert [step.reach_percent for step in rail] == [
        f"{round(step.reach * 100, 1):g}%" for step in strip
    ]


def test_a_recorded_phase_ahead_of_the_current_one_does_not_extend_the_run(specialist):
    """A file that went back keeps its `Kirjas` node and ends its run at `Praegu`."""
    from app.matters.process_phases import PHASE_VALITSUS
    from app.matters.workspace import add_procedural_development
    from app.taxonomy.models import LegalInstrumentType

    matter = factories.MatterFactory(owner=specialist, track="")
    matter.legal_instruments.set([LegalInstrumentType.objects.get(key="seadus")])
    matter.stage = _stage("consultation")
    matter.save(update_fields=["stage", "updated_at"])
    factories.historical_phase(
        add_procedural_development(matter=matter, author=specialist, title="Valitsus").record,
        PHASE_VALITSUS,
    )

    steps = _rail(matter, specialist)
    states = [step.state for step in steps]
    current = states.index(STATE_CURRENT)
    assert "recorded" in states[current + 1 :]
    assert all(step.reach == 0.0 for step in steps[current:])
    assert _is_one_run([step.reach for step in steps])


# ---------------------------------------------------------------------------
# E — `Teema käik`: three outcomes speak up, everything else is quieter
# ---------------------------------------------------------------------------


@pytest.fixture
def busy_file(normal_matter, specialist, organisation, capture_evidence):
    """One of each row the owner named, on one file."""
    from app.matters.services import record_external_position
    from app.matters.workspace import add_procedural_development

    _sent_opinion(normal_matter, capture_evidence, days_ago=3)
    add_procedural_development(matter=normal_matter, author=specialist, title="Rääkisin MKM-iga")
    record_external_position(
        matter=normal_matter,
        organisation=organisation,
        provenance=ExternalPositionProvenance.RECEIVED.value,
        summary="Liige toetab eelnõu",
        actor=specialist,
    )
    record_external_position(
        matter=normal_matter,
        organisation=organisation,
        provenance=ExternalPositionProvenance.DISCOVERED.value,
        summary="Ministeeriumi seisukoht",
        actor=specialist,
    )
    return normal_matter


def _rows(matter, user):
    items, _more = matter_timeline(matter=matter, user=user, limit=200)
    return items


def test_only_the_sent_opinion_is_primary_among_the_owners_rows(busy_file, specialist):
    """E27–E30. Decided on the record's type, never on its words."""
    from app.matters.models import MatterExternalPosition
    from app.submissions.models import Submission

    rows = _rows(busy_file, specialist)
    by_kind = {}
    for item in rows:
        if isinstance(item.record, Submission):
            by_kind["arvamus"] = item
        elif isinstance(item.record, MatterProceduralDevelopment):
            by_kind["marge"] = item
        elif isinstance(item.record, MatterExternalPosition):
            key = (
                "tagasiside"
                if item.record.provenance == ExternalPositionProvenance.RECEIVED
                else "teiste"
            )
            by_kind[key] = item

    assert set(by_kind) == {"arvamus", "marge", "tagasiside", "teiste"}
    assert by_kind["arvamus"].milestone.what == "Arvamus välja"
    assert by_kind["arvamus"].is_primary is True
    assert by_kind["marge"].is_primary is False
    assert by_kind["tagasiside"].is_primary is False
    assert by_kind["teiste"].is_primary is False
    # Nothing else on the file speaks up either — `Teema loodud` included.
    assert [item for item in rows if item.is_primary] == [by_kind["arvamus"]]


def test_a_publication_is_primary_only_once_published(normal_matter, specialist):
    """A planned or cancelled `Ülevaade / uudis` wears the same headline; only
    the published one is Koda saying something in public."""
    from app.matters.services import (
        cancel_website_overview,
        plan_website_overview,
        publish_website_overview,
    )

    published = plan_website_overview(matter=normal_matter, actor=specialist)
    publish_website_overview(
        overview=published,
        url="https://www.koda.ee/uudised/pakendid",
        published_on=timezone.localdate(),
        actor=specialist,
    )
    cancelled = plan_website_overview(matter=normal_matter, actor=specialist)
    cancel_website_overview(overview=cancelled, actor=specialist)

    rows = [item for item in _rows(normal_matter, specialist) if item.website_overview]
    assert {item.website_overview.pk: item.is_primary for item in rows} == {
        published.pk: True,
        cancelled.pk: False,
    }


def test_closing_the_file_is_primary(normal_matter, specialist):
    from app.audit.enums import ChangeEventType
    from app.matters.services import close_matter
    from app.workflow.enums import Disposition

    close_matter(matter=normal_matter, disposition=Disposition.COMPLETED.value, actor=specialist)

    rows = _rows(normal_matter, specialist)
    closed = [
        item
        for item in rows
        if item.event is not None and item.event.event_type == ChangeEventType.MATTER_CLOSED
    ]
    assert len(closed) == 1
    assert closed[0].is_primary is True
    assert [item for item in rows if item.is_primary] == closed


def test_the_rows_carry_their_level_and_keep_their_controls(signed_in, busy_file):
    """E27–E32, in the markup: one class per row, and nothing else changed."""
    body = signed_in.get(_teema(busy_file)).content.decode()
    history = body[body.index('id="ajalugu-loend"') :]

    articles = re.findall(r'<article class="uxtl__item (uxtl__item--\w+)"', history)
    assert articles.count("uxtl__item--primary") == 1
    assert articles.count("uxtl__item--secondary") == len(articles) - 1
    primary = history[history.index("uxtl__item--primary") :]
    primary = primary[: primary.index("</article>")]
    assert "Arvamus välja" in primary
    # The quieter rows still carry their own `Muuda`.
    assert history.count(">Muuda<") >= 3


# ---------------------------------------------------------------------------
# E′ — one hierarchy: the dot and the headline weight are one decision
# ---------------------------------------------------------------------------
#
# The owner's screenshot of 2026-09-27: «Meile saadetud tagasiside», «Märge»,
# «Teiste arvamus» and «Teema loodud» drew the 12 px accent dot beside a regular
# headline, because the dot was read off `is_milestone` and the weight off
# `is_primary`. Both are `is_primary` now:
#
#   PRIMARY     12 px accent dot (`uxtl__dot--primary`) + semibold
#   SECONDARY    6 px muted dot  (no modifier)          + regular


@pytest.fixture
def mixed_file(specialist, organisation, capture_evidence):
    """The screenshot's chronology: two outcomes among five supporting rows.

    Through the services, so `Teema loodud` is on it, and every row carries the
    controls it carries on the real page.
    """
    from app.matters.services import (
        add_entry,
        create_matter,
        plan_website_overview,
        publish_website_overview,
        record_external_position,
    )
    from app.matters.workspace import add_procedural_development

    matter = create_matter(title="Pakendiseaduse muutmine", actor=specialist, owner=specialist)
    _sent_opinion(matter, capture_evidence, days_ago=3)
    overview = plan_website_overview(matter=matter, actor=specialist)
    publish_website_overview(
        overview=overview,
        url="https://www.koda.ee/uudised/pakendid",
        published_on=timezone.localdate(),
        actor=specialist,
    )
    add_procedural_development(matter=matter, author=specialist, title="Rääkisin MKM-iga")
    record_external_position(
        matter=matter,
        organisation=organisation,
        provenance=ExternalPositionProvenance.RECEIVED.value,
        summary="Liige toetab eelnõu",
        actor=specialist,
    )
    record_external_position(
        matter=matter,
        organisation=organisation,
        provenance=ExternalPositionProvenance.DISCOVERED.value,
        summary="Ministeeriumi seisukoht",
        actor=specialist,
    )
    add_entry(matter=matter, author=specialist, body="<p>Helistasin ministeeriumisse.</p>")
    return matter


def _kind(item) -> str:
    """Which of the screenshot's rows this is, by record and event type only."""
    from app.audit.enums import ChangeEventType
    from app.matters.models import MatterExternalPosition
    from app.submissions.models import Submission

    if isinstance(item.record, Submission):
        return "arvamus"
    if item.website_overview is not None:
        return "ulevaade"
    if isinstance(item.record, MatterProceduralDevelopment):
        return "marge"
    if isinstance(item.record, MatterExternalPosition):
        if item.record.provenance == ExternalPositionProvenance.RECEIVED:
            return "tagasiside"
        return "teiste"
    if item.is_entry:
        return "too"
    if item.event is not None and item.event.event_type == ChangeEventType.MATTER_CREATED:
        return "loodud"
    return f"muu:{item.item_type}"


def _articles(body: str) -> list[str]:
    history = body[body.index('id="ajalugu-loend"') :]
    return re.findall(r'<article class="uxtl__item .*?</article>', history, re.S)


def _dot_classes(article: str) -> set[str]:
    match = re.search(r'<span class="(uxtl__dot[^"]*)"', article)
    assert match, article[:200]
    return set(match.group(1).split())


def test_the_dot_follows_the_same_decision_as_the_headline(signed_in, mixed_file, specialist):
    """If a row is primary it has the big accent dot; if not, the small muted one.

    Row by row against the projection, so the rendered page and
    `TimelineItem.is_primary` are compared as one relation rather than as two
    counts that could agree by accident.
    """
    rows = _rows(mixed_file, specialist)
    articles = _articles(signed_in.get(_teema(mixed_file)).content.decode())

    kinds = [_kind(item) for item in rows]
    assert sorted(kinds) == sorted(
        ["arvamus", "ulevaade", "marge", "tagasiside", "teiste", "too", "loodud"]
    ), kinds
    assert len(articles) == len(rows)

    for item, article in zip(rows, articles, strict=True):
        dot = _dot_classes(article)
        if item.is_primary:
            assert "uxtl__item--primary" in article.split(">", 1)[0], _kind(item)
            assert "uxtl__dot--primary" in dot, _kind(item)
        else:
            assert "uxtl__item--secondary" in article.split(">", 1)[0], _kind(item)
            assert dot == {"uxtl__dot"}, (_kind(item), dot)
        # The retired switch is not drawn on anything.
        assert "uxtl__dot--ms" not in article

    # And exactly the two outcomes this file put out are the primary ones.
    primary = {_kind(item) for item in rows if item.is_primary}
    assert primary == {"arvamus", "ulevaade"}


def test_a_milestone_that_is_not_an_outcome_draws_the_small_dot(signed_in, mixed_file, specialist):
    """The screenshot's four rows, by name: milestones all, and all supporting."""
    rows = _rows(mixed_file, specialist)
    articles = _articles(signed_in.get(_teema(mixed_file)).content.decode())
    by_kind = {_kind(item): (item, article) for item, article in zip(rows, articles, strict=True)}

    for kind in ("marge", "tagasiside", "teiste", "loodud"):
        item, article = by_kind[kind]
        assert item.is_milestone, kind
        assert item.is_primary is False, kind
        assert _dot_classes(article) == {"uxtl__dot"}, kind


def test_the_quieter_rows_keep_their_content_controls_and_order(signed_in, mixed_file, specialist):
    """Presentation only: nothing a secondary row said or offered went with its dot."""
    rows = _rows(mixed_file, specialist)
    articles = _articles(signed_in.get(_teema(mixed_file)).content.decode())
    by_kind = {_kind(item): (item, article) for item, article in zip(rows, articles, strict=True)}

    # Order and content at once: the page draws the projection's own order, row
    # for row, and each row still says what it said. Every marker is unique on
    # the page, so a row drawn out of place would carry another row's words.
    markers = {
        "arvamus": "Arvamus välja",
        "ulevaade": "koda.ee/uudised/pakendid",
        "marge": "Rääkisin MKM-iga",
        "tagasiside": "Liige toetab eelnõu",
        "teiste": "Ministeeriumi seisukoht",
        "too": "Helistasin ministeeriumisse.",
        "loodud": "Teema loodud",
    }
    for item, article in zip(rows, articles, strict=True):
        assert markers[_kind(item)] in article, _kind(item)
    for kind, (item, article) in by_kind.items():
        if item.milestone is not None:
            assert item.milestone.what in article, kind

    # Controls: each correctable supporting row still carries its `Muuda`, and
    # each removable one its `Kustuta`.
    for kind in ("marge", "tagasiside", "teiste", "too"):
        assert ">Muuda<" in by_kind[kind][1], kind
    for kind in ("marge", "tagasiside", "teiste"):
        assert "Kustuta" in by_kind[kind][1], kind


# ---------------------------------------------------------------------------
# F — the two headings, off the screen and still in the outline
# ---------------------------------------------------------------------------


def test_both_headings_are_visually_hidden_and_still_name_their_sections(signed_in, three_opinions):
    """F33–F35."""
    body = signed_in.get(_teema(three_opinions)).content.decode()

    assert '<section class="lprail" aria-labelledby="menetluse-kulg-heading">' in body
    assert (
        '<h2 class="lprail__head visually-hidden" id="menetluse-kulg-heading">Menetluse kulg</h2>'
    ) in body
    assert '<h2 class="accordion__title visually-hidden">Teema käik</h2>' in body
    # The count stays in the summary the section still folds by.
    summary = body[body.index('id="ajajoon"') :]
    summary = summary[: summary.index("</summary>")]
    assert 'class="uxtl__count"' in summary
    assert re.search(r"\d+ kirje", summary)
