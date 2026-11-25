"""A closed Teema's «Menetluse kulg» cannot be edited (docs/adr/0119 §1, coordinator 25.11).

ADR 0119: «a reader, and a closed file, still get nothing». The template used to
offer `Muuda` to a writer on a closed file that had a procedure
(`can_write and legal_process or can_write and matter.is_open`). The rule is kept
in three places, each tested here:

* the page draws neither `Muuda` nor a date shortcut on a closed file;
* the panels' GETs answer a closed file 404;
* the services re-read the closure under the Matter's row lock
  (`lock_open_matter_for_business_write`), so a panel opened before the closure,
  a crafted POST, or a caller holding a stale instance is refused with
  `CLOSED_MATTER_REFUSAL` and writes nothing — no row, no audit event.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.locks import CLOSED_MATTER_REFUSAL
from app.matters.models import Matter, MatterTimelineStep
from app.matters.process_phases import PHASE_VALITSUS
from app.matters.services import (
    close_matter,
    reopen_matter,
    set_rail_step_date,
    set_timeline_steps,
    timeline_steps_revision_token,
)
from app.workflow.enums import DatePrecision, Disposition
from tests.refusals import refused
from tests.test_rail_step_date import _file, _page, _url
from tests.test_timeline_added_steps import _PanelState

pytestmark = pytest.mark.django_db


def _panel_url(matter) -> str:
    return reverse("matters:timeline_steps", kwargs={"pk": matter.pk})


def _close(matter, actor) -> None:
    close_matter(matter=matter, disposition=Disposition.OTHER, actor=actor)


def _nothing_written(matter) -> None:
    assert not MatterTimelineStep.objects.filter(matter=matter).exists()
    assert not ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.TIMELINE_STEPS_CHANGED
    ).exists()


def _bar(html: str) -> str:
    """The rail's section, where `Muuda` and the date shortcuts live."""
    start = html.index('id="menetluse-kulg-muuda"')
    head = html.rfind("<section", 0, start)
    return html[head : html.index("</section>", start)]


def test_an_open_file_with_a_procedure_offers_muuda_and_the_date_shortcuts(signed_in, specialist):
    matter = _file(specialist)

    bar = _bar(_page(signed_in, matter))

    assert "lprail__edit" in bar
    assert "tl-step__pick" in bar


def test_a_closed_file_with_a_procedure_offers_no_muuda_and_no_date_shortcut(signed_in, specialist):
    """The defect: `can_write and legal_process` kept `Muuda` on a closed file."""
    matter = _file(specialist)
    _close(matter, specialist)

    html = _page(signed_in, matter)

    assert 'id="menetluse-kulg-muuda"' in html  # the rail section is still drawn
    bar = _bar(html)
    assert "lprail__edit" not in bar
    assert ">Muuda</button>" not in bar
    assert "tl-step__pick" not in bar
    assert reverse("matters:timeline_steps", kwargs={"pk": matter.pk}) not in bar


def test_a_closed_file_answers_both_panels_404(signed_in, specialist):
    matter = _file(specialist)
    _close(matter, specialist)

    assert signed_in.get(_panel_url(matter)).status_code == 404
    assert signed_in.get(_url(matter, PHASE_VALITSUS)).status_code == 404


def test_a_panel_opened_before_the_closure_is_refused_and_writes_nothing(signed_in, specialist):
    matter = _file(specialist)
    opened = signed_in.get(_panel_url(matter))
    assert opened.status_code == 200
    state = _PanelState()
    state.feed(opened.content.decode())
    _close(matter, specialist)

    response = signed_in.post(_panel_url(matter), {**state.data, "uus__title": "Komisjoni istung"})

    assert response.status_code == 400
    assert CLOSED_MATTER_REFUSAL.split(".")[0] in response.content.decode()
    _nothing_written(matter)


def test_a_date_shortcut_opened_before_the_closure_writes_nothing(signed_in, specialist):
    matter = _file(specialist)
    assert signed_in.get(_url(matter, PHASE_VALITSUS)).status_code == 200
    revision = timeline_steps_revision_token(matter)
    _close(matter, specialist)

    response = signed_in.post(
        _url(matter, PHASE_VALITSUS),
        {"kp_precision": "EXACT", "kp_date": "01.12.2026", "revision": revision},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 404
    _nothing_written(matter)


def test_a_crafted_post_to_a_closed_file_is_refused_and_writes_nothing(signed_in, specialist):
    matter = _file(specialist)
    _close(matter, specialist)

    response = signed_in.post(
        _panel_url(matter), {"uus__title": "Komisjoni istung", "uus__after": "algus"}
    )

    assert response.status_code == 400
    _nothing_written(matter)


def test_the_services_refuse_a_closed_file_read_under_the_lock(specialist):
    """A caller holding the instance from before the closure is still refused:
    the closure is read from the locked row, not from the instance."""
    matter = _file(specialist)
    stale = Matter.objects.get(pk=matter.pk)
    _close(matter, specialist)
    assert stale.is_open  # what the caller still believes

    with refused(CLOSED_MATTER_REFUSAL):
        set_timeline_steps(
            matter=stale,
            steps=[(PHASE_VALITSUS, False, date(2026, 12, 1), DatePrecision.EXACT)],
            actor=specialist,
        )
    with refused(CLOSED_MATTER_REFUSAL):
        set_timeline_steps(
            matter=stale,
            steps=(),
            added=[(None, "Komisjoni istung", None, "", "", False)],
            actor=specialist,
        )
    with refused(CLOSED_MATTER_REFUSAL):
        set_rail_step_date(
            matter=stale,
            rail_key=PHASE_VALITSUS,
            occurs_on=date(2026, 12, 1),
            precision=DatePrecision.EXACT,
            actor=specialist,
        )
    _nothing_written(matter)


def test_reopening_gives_the_editing_back_with_the_same_checks(signed_in, specialist):
    """Reopening is the way out (ADR 0076 §2); the revision check still holds."""
    matter = _file(specialist)
    _close(matter, specialist)
    reopen_matter(matter=Matter.objects.get(pk=matter.pk), actor=specialist)

    bar = _bar(_page(signed_in, matter))
    assert "lprail__edit" in bar
    response = signed_in.post(
        _url(matter, PHASE_VALITSUS),
        {
            "kp_precision": "EXACT",
            "kp_date": "01.12.2026",
            "revision": timeline_steps_revision_token(matter),
        },
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    row = MatterTimelineStep.objects.get(matter=matter, phase_key=PHASE_VALITSUS)
    assert row.occurs_on == date(2026, 12, 1)
    assert (
        ChangeEvent.objects.filter(
            matter=matter, event_type=ChangeEventType.TIMELINE_STEPS_CHANGED
        ).count()
        == 1
    )


# -- «Lõpetatud» reads inside the phase the file was closed in ------------------------


def _ordered_labels(matter, user) -> list[str]:
    from tests.test_timeline_added_steps import _labels

    return _labels(matter, user)


def test_lopetatud_reads_after_the_phase_the_file_was_closed_in(specialist):
    """A closed Matter stays in the period it was closed in (ADR 0131 §12), so the
    closure follows the current phase's node — and the procedure's later phases
    still read after it: the end of Koda's work is not the end of the proceedings."""
    from app.matters.services import change_stage
    from tests.test_timeline_added_steps import _matter, _stage

    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("government"), actor=specialist)
    _close(matter, specialist)

    labels = _ordered_labels(Matter.objects.get(pk=matter.pk), specialist)

    closed = labels.index("Lõpetatud")
    current = labels.index("Valitsuses")
    assert closed == current + 1, labels
    assert "Riigikogus" in labels[closed + 1 :], labels


def test_an_explicit_later_date_on_the_current_phase_is_still_believed(specialist):
    """A person dated the current phase after the day the file was closed: that
    date is an anchor (ADR 0100, amendment of 2026-09-27, rule 1), and the
    closure reads before it."""
    from app.matters.services import change_stage
    from tests.test_timeline_added_steps import _matter, _stage

    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("government"), actor=specialist)
    set_timeline_steps(
        matter=matter,
        steps=[("valitsus", False, date(2099, 1, 1), DatePrecision.EXACT)],
        actor=specialist,
    )
    _close(matter, specialist)

    labels = _ordered_labels(Matter.objects.get(pk=matter.pk), specialist)

    assert labels.index("Lõpetatud") < labels.index("Valitsuses"), labels
