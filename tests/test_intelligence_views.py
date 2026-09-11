"""The Matter-page fact sections and their write forms, through the real views.

These assert the things a selector test cannot: that an empty section does not
render at all, that a reader is offered no controls, that an approximate period
survives the round trip through a form, and that the write forms accept what a
browser actually posts.

**The three generated reading pages that used to be asserted here are gone.**
Olulised tähtajad, Jõustuvad aktid and Töövõidud are not destinations any more:
`Töövõit` and `Jõustumine` are structured filters on Teemad and an `Oluline
tähtaeg` is its owner's own upcoming work (docs/adr/0071). What those tests
guarded — which population is a work victory, that a period is never reduced to
a fabricated day, that a restricted child leaks through neither presence nor
absence — is asserted against the surfaces that answer those questions now, in
`tests/test_teemad_consolidation.py`. The selectors themselves keep their own
tests in `tests/test_intelligence_*.py`.

Nothing about writing these facts changed, which is what remains below.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.intelligence.enums import EffectiveDateKind, FactStatus, WorkVictoryStatus
from app.intelligence.services import (
    add_effective_date,
    add_important_date,
    add_work_victory_candidate,
)
from app.workflow.dates import quarter_bounds
from app.workflow.enums import DatePrecision
from tests import factories

pytestmark = pytest.mark.django_db


def _text(response) -> str:
    return response.content.decode()


# -- the Matter page --------------------------------------------------------


def test_an_empty_section_does_not_render_at_all(signed_in, specialist):
    """The redesign's rule: no permanently visible empty sections.

    These three headings, each over a sentence reporting an absence and an add
    button, used to occupy about forty per cent of a new Matter's page. What
    replaces them is one quiet row of add affordances, and `Oluline tähtaeg` is
    not even in that — the composer offers it beside the note it belongs to
    (Teema redesign §3, §24).
    """
    matter = factories.MatterFactory(owner=specialist)
    body = _text(signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})))

    assert 'id="olulised-tahtajad"' not in body
    assert 'id="joustumine"' not in body
    assert 'id="toovoidud"' not in body
    assert "Olulisi tähtaegu pole lisatud." not in body
    assert "Jõustumise infot pole lisatud." not in body
    assert "Töövõite ega kandidaate pole lisatud." not in body
    # The one quiet row that replaces all three.
    assert "+ Jõustumine" in body
    assert "+ Töövõit" in body


def test_a_populated_section_still_renders(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    add_important_date(
        matter=matter,
        title="Kooskõlastusringi lõpp",
        date_value=timezone.localdate() + timedelta(days=10),
        period_end=timezone.localdate() + timedelta(days=10),
        actor=specialist,
    )
    # **The Teema page carries no facts panel.** A dated milestone reads on the
    # process strip and, once it has happened, in the chronology — projected
    # from this same record (docs/adr/0074 §15).
    page = _text(signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})))
    assert 'id="teema-faktid"' not in page
    assert "tl-strip" in page
    assert "Kooskõlastusringi lõpp" in page

    # The fragment route still serves the section, with its own scoped read.
    body = _text(signed_in.get(_add_effective(matter), headers={"HX-Request": "true"}))
    assert 'id="olulised-tahtajad"' in body
    assert "Kooskõlastusringi lõpp" in body
    # The two that are still empty stay away.
    assert 'id="joustumine"' not in body
    assert 'id="toovoidud"' not in body


def test_the_matter_page_marks_a_cancelled_milestone(signed_in, specialist):
    from app.intelligence.services import cancel_important_date

    matter = factories.MatterFactory(owner=specialist)
    record = add_important_date(
        matter=matter,
        title="Ärajäänud ring",
        date_value=timezone.localdate() + timedelta(days=10),
        period_end=timezone.localdate() + timedelta(days=10),
        actor=specialist,
    )
    cancel_important_date(record=record, actor=specialist)

    # Nothing is deleted when a plan changes. A called-off expectation is part
    # of the file's history, so it reads in the chronology and is marked there —
    # and it is *not* on the process strip, which says where the file is going
    # (docs/adr/0074 §12).
    body = _text(signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})))
    assert "Ärajäänud ring" in body
    assert "Tühistatud" in body
    strip = body.split("tl-strip")[1].split("</div>")[0] if "tl-strip" in body else ""
    assert "Ärajäänud ring" not in strip


def test_a_reader_sees_the_facts_and_none_of_the_controls(client, specialist):
    matter = factories.MatterFactory(owner=specialist)
    add_important_date(
        matter=matter,
        title="Nähtav tähtaeg",
        date_value=date(2030, 5, 1),
        period_end=date(2030, 5, 1),
        actor=specialist,
    )
    reader = factories.UserFactory(role="READER")
    client.force_login(reader)

    body = _text(client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})))
    assert "Nähtav tähtaeg" in body
    assert "+ Lisa oluline tähtaeg" not in body
    assert "+ Lisa töövõit" not in body


def test_a_specialist_sees_no_confirmation_control(signed_in, specialist):
    """Adding is theirs; adjudicating somebody else's candidate is not.

    The two are different acts on purpose. A specialist may state a victory
    they know about, and still not be the person who decides the fate of a
    proposal a machine or an import produced.
    """
    matter = factories.MatterFactory(owner=specialist)
    add_work_victory_candidate(matter=matter, title="Kandidaat", actor=specialist)

    # Asserted on the fragment. The Teema page's way to state a win is the
    # composer's `+ Töövõit`, and its chronology shows *confirmed* victories: a
    # candidate is a proposal awaiting somebody else's judgement, not a
    # professional fact about the file (docs/adr/0074 §8).
    page = _text(signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})))
    assert "+ Töövõit" in page
    assert "Kandidaat" not in page

    body = _text(signed_in.get(_add_effective(matter), headers={"HX-Request": "true"}))
    assert "+ Lisa töövõit" in body
    assert "Kinnita töövõiduks" not in body


def test_adding_a_victory_from_the_matter_page_confirms_it(signed_in, specialist):
    """One click, one truthful row — no waiting to be approved by somebody.

    The person filling the form has already made the judgement the review step
    exists to make, and they did it on a Matter they may write to.
    """
    matter = factories.MatterFactory(owner=specialist)
    response = signed_in.post(
        reverse("intelligence:add_work_victory", kwargs={"matter_id": matter.pk}),
        {"title": "Ettepanek võeti arvesse", "precision": "YEAR", "year": "2026"},
    )

    assert response.status_code == 302
    record = matter.work_victories.get()
    assert record.status == WorkVictoryStatus.CONFIRMED
    assert record.created_by == specialist
    assert record.confirmed_by == specialist
    assert record.confirmed_at is not None

    events = ChangeEvent.objects.filter(object_id=record.pk)
    assert [event.event_type for event in events] == [ChangeEventType.WORK_VICTORY_CONFIRMED]
    assert events.get().payload["origin"] == "MANUAL"


def test_the_add_form_speaks_only_of_a_toovoit(signed_in, specialist):
    """No candidate, and no confirmation either.

    The note used to promise that the row "salvestub kinnitatud töövõiduna" and
    that "eraldi kinnitamist ei ole vaja" — two sentences about a review step
    the writer never meets, in the vocabulary of the state machine underneath.
    """
    matter = factories.MatterFactory(owner=specialist)
    body = _text(
        signed_in.get(reverse("intelligence:add_work_victory", kwargs={"matter_id": matter.pk}))
    )
    folded = body.casefold()

    assert "Lisa töövõit" in body
    assert "Kirje lisatakse töövõiduna sinu nimel." in body
    assert "kandidaa" not in folded
    assert "kinnita" not in folded


def test_a_reader_still_cannot_add_a_victory(client, specialist):
    """The gate is the ordinary business-write permission, not a new one."""
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(factories.UserFactory(role="READER"))

    response = client.post(
        reverse("intelligence:add_work_victory", kwargs={"matter_id": matter.pk}),
        {"title": "Ei tohiks salvestuda", "precision": "YEAR", "year": "2026"},
    )
    # 404, not the 403 this module used to answer. Business-write refusals
    # are one answer across the application now, and it is the one that
    # tells a reader nothing about what exists for somebody else
    # (app/core/decorators.py, AUTH-002).
    assert response.status_code == 404
    assert matter.work_victories.count() == 0


def test_adding_a_victory_grants_no_right_over_somebody_elses_candidate(
    signed_in, specialist, department_head
):
    """The manual door must not quietly widen the review role."""
    matter = factories.MatterFactory(owner=specialist)
    signed_in.post(
        reverse("intelligence:add_work_victory", kwargs={"matter_id": matter.pk}),
        {"title": "Minu võit", "precision": "YEAR", "year": "2026"},
    )
    candidate = add_work_victory_candidate(
        matter=matter, title="Masina pakkumine", actor=department_head
    )

    refused = signed_in.post(
        reverse(
            "intelligence:confirm_work_victory",
            kwargs={"matter_id": matter.pk, "pk": candidate.pk},
        )
    )
    assert refused.status_code == 403
    candidate.refresh_from_db()
    assert candidate.status == WorkVictoryStatus.CANDIDATE


def test_the_department_head_sees_the_confirmation_control(client, specialist, department_head):
    matter = factories.MatterFactory(owner=specialist)
    add_work_victory_candidate(matter=matter, title="Kandidaat", actor=specialist)
    client.force_login(department_head)

    body = _text(client.get(_add_effective(matter), headers={"HX-Request": "true"}))
    assert "Kinnita töövõiduks" in body


# -- the write forms --------------------------------------------------------


def test_a_quarter_posted_from_the_form_stores_a_quarter(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    response = signed_in.post(
        reverse("intelligence:add_important_date", kwargs={"matter_id": matter.pk}),
        {"title": "Kooskõlastusring", "precision": "QUARTER", "quarter": "2", "year": "2027"},
    )

    assert response.status_code == 302
    record = matter.important_dates.get()
    assert record.date_precision == DatePrecision.QUARTER
    assert (record.date_value, record.period_end) == (date(2027, 4, 1), date(2027, 6, 30))
    assert record.display_date == "II kvartal 2027"


def test_a_quarter_with_no_year_is_refused_on_the_field_that_is_missing(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    response = signed_in.post(
        reverse("intelligence:add_important_date", kwargs={"matter_id": matter.pk}),
        {"title": "Kooskõlastusring", "precision": "QUARTER", "quarter": "2"},
    )

    assert response.status_code == 400
    assert matter.important_dates.count() == 0
    assert "Aasta on puudu." in _text(response)


def test_a_general_order_commencement_refuses_an_offered_date(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    response = signed_in.post(
        reverse("intelligence:add_effective_date", kwargs={"matter_id": matter.pk}),
        {
            "kind": EffectiveDateKind.GENERAL_ORDER,
            "precision": "EXACT",
            "exact_date": "2026-01-01",
            "description": "rakendusmäärus",
        },
    )

    assert response.status_code == 400
    assert matter.effective_dates.count() == 0
    assert "ainult teadaoleva jõustumise" in _text(response)


def test_a_general_order_commencement_saves_with_no_date(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    response = signed_in.post(
        reverse("intelligence:add_effective_date", kwargs={"matter_id": matter.pk}),
        {"kind": EffectiveDateKind.GENERAL_ORDER, "description": "rakendusmäärus"},
    )

    assert response.status_code == 302
    record = matter.effective_dates.get()
    assert record.date_value is None
    assert record.display_when == "Jõustub üldises korras"


def test_a_work_victory_form_saves_a_confirmed_victory_with_its_period(signed_in, specialist):
    """The period is entered; the status is what the person's act was."""
    matter = factories.MatterFactory(owner=specialist)
    signed_in.post(
        reverse("intelligence:add_work_victory", kwargs={"matter_id": matter.pk}),
        {"title": "Ettepanek arvestati", "precision": "YEAR", "year": "2026"},
    )

    record = matter.work_victories.get()
    assert record.status == WorkVictoryStatus.CONFIRMED
    assert record.display_period == "2026"


def test_a_work_victory_may_be_saved_with_no_period(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    signed_in.post(
        reverse("intelligence:add_work_victory", kwargs={"matter_id": matter.pk}),
        {"title": "Millalgi varem", "precision": ""},
    )

    record = matter.work_victories.get()
    assert record.period_date is None


# -- the inline add forms on the Matter page --------------------------------
#
# `+ Jõustumine` and `+ Töövõit` open under the section their record lands in
# rather than on a page of their own. One route answers both shapes: an ordinary
# request gets the standalone page it always got, an HTMX request gets the fact
# block (docs/adr/0065).


#: What htmx sends, and the only thing that switches these views into the
#: fragment branch. A query parameter would let a pasted link produce a bare
#: fragment with no shell around it.
HTMX = {"hx-request": "true"}

EFFECTIVE_FORM = "faktivorm-joustumine"
VICTORY_FORM = "faktivorm-toovoit"


def _add_effective(matter):
    return reverse("intelligence:add_effective_date", kwargs={"matter_id": matter.pk})


def _add_victory(matter):
    return reverse("intelligence:add_work_victory", kwargs={"matter_id": matter.pk})


def test_the_matter_page_records_both_facts_from_the_composer(signed_in, specialist):
    """`+ Jõustumine` and `+ Töövõit` are composer panels, not links to a
    fragment.

    They opened a form into `#teema-faktid` while the facts panel existed; the
    panel is gone and both are now closed chips beside the other three, saved by
    the composer's one `Salvesta` in one transaction (docs/adr/0074 §7, §8).

    The fragment route and its inline triggers are untouched and still tested
    below — they are simply not what this page reaches for.
    """
    matter = factories.MatterFactory(owner=specialist)
    body = _text(signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})))

    assert 'id="teema-faktid"' not in body
    assert 'id="cx-joustumine"' in body
    assert 'id="cx-toovoit"' in body
    assert "+ Jõustumine" in body
    assert "+ Töövõit" in body


# The «two chips on the empty state» test retired with the surface it measured.
#
# It read the *Matter page*, where an empty facts panel offered `+ Jõustumine`
# and `+ Töövõit` as inline triggers into `#teema-faktid`. The approved target
# has no facts panel: both are composer panels now, asserted in
# `test_the_matter_page_records_both_facts_from_the_composer` above
# (docs/adr/0074 §7, §8).
#
# What the test actually protected — an add control is an inline trigger with an
# `href` a browser without scripting can follow — is the next test's subject, on
# the surface that still renders one.


def test_the_populated_section_control_opens_in_place_too(signed_in, specialist):
    """Not only the empty state. A Matter that already carries a commencement
    offers `+ Lisa jõustumine` beside the list, and that one inlines as well."""
    matter = factories.MatterFactory(owner=specialist)
    add_effective_date(matter=matter, kind=EffectiveDateKind.GENERAL_ORDER, actor=specialist)
    add_work_victory_candidate(matter=matter, title="Kandidaat", actor=specialist)

    body = _text(signed_in.get(_add_effective(matter), headers={"HX-Request": "true"}))
    commencements = body.split('id="joustumine"')[1].split("</section>")[0]

    assert "+ Lisa jõustumine" in commencements
    assert f'hx-get="{_add_effective(matter)}"' in commencements
    # The `href` stays beside it: the control is still a link a browser without
    # scripting can follow to the standalone form (docs/adr/0065).
    assert f'href="{_add_effective(matter)}"' in commencements
    assert 'hx-target="#teema-faktid"' in commencements
    victories = body.split('id="toovoidud"')[1]
    assert f'hx-get="{_add_victory(matter)}"' in victories
    assert f'href="{_add_victory(matter)}"' in victories


def test_opening_the_commencement_form_answers_with_the_fact_block(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    response = signed_in.get(_add_effective(matter), headers=HTMX)
    body = _text(response)

    assert response.status_code == 200
    assert 'id="teema-faktid"' in body
    assert EFFECTIVE_FORM in body
    assert "Lisa jõustumine" in body
    # A fragment, not a page: no shell, no navigation, nothing to leave.
    assert "<html" not in body


def test_opening_one_form_closes_the_other(signed_in, specialist):
    """The accordion, and it is the whole of it.

    The block renders with at most one open form, so asking for Töövõit *is*
    closing Jõustumine. Nothing is stored to remember which was open, and
    opening one writes nothing at all.
    """
    matter = factories.MatterFactory(owner=specialist)

    opened = _text(signed_in.get(_add_victory(matter), headers=HTMX))
    assert VICTORY_FORM in opened
    assert EFFECTIVE_FORM not in opened

    assert matter.effective_dates.count() == 0
    assert matter.work_victories.count() == 0


def test_closing_the_form_leaves_the_block_and_writes_nothing(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    body = _text(signed_in.get(f"{_add_effective(matter)}?vorm=sulge", headers=HTMX))

    assert 'id="teema-faktid"' in body
    assert EFFECTIVE_FORM not in body
    assert matter.effective_dates.count() == 0


def test_a_refused_commencement_stays_inline_with_what_was_typed(signed_in, specialist):
    """The property this whole interaction is for.

    A refused save must come back where the person was, with their words still
    in the boxes and the reason beside them — not on another screen they then
    have to leave again.
    """
    matter = factories.MatterFactory(owner=specialist)
    response = signed_in.post(
        _add_effective(matter),
        {
            "kind": EffectiveDateKind.GENERAL_ORDER,
            "precision": "EXACT",
            "exact_date": "2026-01-01",
            "description": "rakendusmäärus",
        },
        headers=HTMX,
    )
    body = _text(response)

    assert response.status_code == 400
    assert matter.effective_dates.count() == 0
    assert EFFECTIVE_FORM in body
    assert "ainult teadaoleva jõustumise" in body
    assert "rakendusmäärus" in body


def test_a_saved_commencement_comes_back_as_the_section_it_joined(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    response = signed_in.post(
        _add_effective(matter),
        {"kind": EffectiveDateKind.GENERAL_ORDER, "description": "hilisemad sätted"},
        headers=HTMX,
    )
    body = _text(response)

    assert response.status_code == 200
    record = matter.effective_dates.get()
    assert record.date_value is None
    # The record is on the screen, and the form that wrote it has closed.
    assert 'id="joustumine"' in body
    assert "hilisemad sätted" in body
    assert EFFECTIVE_FORM not in body


def test_a_refused_work_victory_stays_inline_with_what_was_typed(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    response = signed_in.post(
        _add_victory(matter),
        {"title": "Erisus jäi sisse", "precision": "QUARTER", "quarter": "2"},
        headers=HTMX,
    )
    body = _text(response)

    assert response.status_code == 400
    assert matter.work_victories.count() == 0
    assert VICTORY_FORM in body
    assert "Erisus jäi sisse" in body


def test_a_saved_work_victory_is_confirmed_and_comes_back_on_the_matter(signed_in, specialist):
    """Same service, same meaning. Inlining the form did not redefine what a
    Töövõit is, or who gets to say one happened."""
    matter = factories.MatterFactory(owner=specialist)
    response = signed_in.post(
        _add_victory(matter),
        {"title": "Ettepanek arvestati", "precision": "YEAR", "year": "2026"},
        headers=HTMX,
    )
    body = _text(response)

    assert response.status_code == 200
    record = matter.work_victories.get()
    assert record.status == WorkVictoryStatus.CONFIRMED
    assert record.display_period == "2026"
    assert 'id="toovoidud"' in body
    assert "Ettepanek arvestati" in body
    assert VICTORY_FORM not in body


def test_the_standalone_form_still_answers_an_ordinary_request(signed_in, specialist):
    """The deep link, the bookmark and the browser with scripting off.

    Nothing about the old path was removed: the address still resolves to a
    page, and posting to it still redirects back to the Matter's section anchor.
    """
    matter = factories.MatterFactory(owner=specialist)
    page = _text(signed_in.get(_add_effective(matter)))

    assert "<html" in page
    assert "Lisa jõustumine" in page

    response = signed_in.post(
        _add_effective(matter),
        {"kind": EffectiveDateKind.GENERAL_ORDER, "description": "vanas korras"},
    )
    assert response.status_code == 302
    assert response["Location"].endswith("#joustumine")
    assert matter.effective_dates.count() == 1


def test_a_reader_gets_no_inline_form_and_no_new_route(client, specialist, reader):
    """The write gate is the same gate, asked before anything else happens.

    404 rather than 403, like every other refusal here: somebody who may not
    write is not told which surfaces exist for those who may.
    """
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(reader)

    for url in (_add_effective(matter), _add_victory(matter)):
        assert client.get(url, headers=HTMX).status_code == 404
        assert client.post(url, {"title": "Loata"}, headers=HTMX).status_code == 404

    body = _text(client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})))
    assert "+ Jõustumine" not in body
    assert "+ Töövõit" not in body
    assert matter.effective_dates.count() == 0
    assert matter.work_victories.count() == 0


def test_the_inline_fragment_tells_a_stranger_nothing_about_a_restricted_matter(client, specialist):
    """The same 404 the standalone route gives, for the same reason."""
    from app.core.enums import Visibility

    matter = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    outsider = factories.ReaderFactory()
    client.force_login(outsider)

    assert client.get(_add_effective(matter), headers=HTMX).status_code == 404


def test_the_edit_form_reopens_a_quarter_as_a_quarter(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    start, end = quarter_bounds(2027, 3)
    record = add_important_date(
        matter=matter,
        title="Kooskõlastusring",
        date_value=start,
        period_end=end,
        date_precision=DatePrecision.QUARTER,
        actor=specialist,
    )

    response = signed_in.get(
        reverse(
            "intelligence:edit_important_date",
            kwargs={"matter_id": matter.pk, "pk": record.pk},
        )
    )
    initial = response.context["form"].initial
    assert initial["precision"] == DatePrecision.QUARTER
    assert initial["quarter"] == "3"
    assert initial["year"] == 2027
    assert "exact_date" not in initial


def test_cancelling_through_the_form_keeps_the_record(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    record = add_important_date(
        matter=matter,
        title="Ärajäänud",
        date_value=date(2030, 5, 1),
        period_end=date(2030, 5, 1),
        actor=specialist,
    )

    response = signed_in.post(
        reverse(
            "intelligence:cancel_important_date",
            kwargs={"matter_id": matter.pk, "pk": record.pk},
        ),
        {"reason": "Ministeerium loobus"},
    )

    assert response.status_code == 302
    record.refresh_from_db()
    assert record.status == FactStatus.CANCELLED


def test_the_confirmation_page_states_what_is_being_claimed(client, specialist, department_head):
    matter = factories.MatterFactory(owner=specialist)
    record = add_work_victory_candidate(
        matter=matter, title="Ettepanek arvestati", actor=specialist
    )
    client.force_login(department_head)

    body = _text(
        client.get(
            reverse(
                "intelligence:confirm_work_victory",
                kwargs={"matter_id": matter.pk, "pk": record.pk},
            )
        )
    )
    assert "Ettepanek arvestati" in body
    assert "Teadmata periood" in body
    assert "Kinnita töövõiduks" in body
