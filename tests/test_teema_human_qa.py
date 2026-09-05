"""The hands-on QA correction round, defect by defect.

Eight things a lawyer hit while using the redesigned Teema workspace on real
data. Each one has a section here, and each section starts from the observation
rather than from the code, because that is what has to keep being true.

Where a defect was a *design* decision that hands-on use rejected, the test
asserts the new decision and says why the old one lost. Where it was a bug, the
test reproduces the condition that produced it.

The domain underneath is unchanged and is not re-tested here: `Järgmiseks`
semantics, Submission invariants, evidence immutability and authorization each
have their own suite (tests/test_teema_redesign.py, tests/test_work_surfaces.py).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.intelligence.forms import EffectiveDateForm
from app.matters import selectors
from app.matters.forms import (
    ComposerForm,
    EngagementForm,
    IncomingIntakeForm,
    MatterCreateForm,
    MatterEditForm,
    NextActionForm,
)
from app.matters.models import Matter
from app.matters.services import assign_matter, set_policy_areas
from app.taxonomy.models import PolicyArea
from app.workflow.enums import ActionKind, ActionStatus, DateSemantics, Disposition
from app.workflow.models import NextAction
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db


def _body(response) -> str:
    return response.content.decode()


def _detail(client, matter) -> str:
    return _body(client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})))


def _days(offset: int):
    return timezone.localdate() + timedelta(days=offset)


def _edit_url(matter) -> str:
    return reverse("matters:matter_edit", kwargs={"pk": matter.pk})


# ---------------------------------------------------------------------------
# §1 — what the Chamber produced belongs in the rail
#
# This round moved `Koja seisukoht` out of the main column and into the 300px
# one, and QA was right about the *placement*. A later product decision retired
# the concept itself: there is no separate free-text position, and what the
# Chamber produced on a Matter is the opinion it sent, which is a file. The
# placement survived the concept, so what is asserted here is the placement —
# `Koja arvamus` occupies it, and the main-column band and the duplicate
# sent-opinion strip are still gone. The block's own behaviour is in
# tests/test_teema_rail.py.
# ---------------------------------------------------------------------------


def test_what_koda_produced_is_in_the_rail_and_only_there(signed_in, specialist):
    """One block, in the 300px column, matching the approved mockup.

    The redesign put a full-width position block in the main column between the
    composer and Kaasamine, and a separate sent-opinion strip under it. On a
    real Matter that pushed everything a lawyer came for below the fold to say
    two things that fit in a rail card (Teema QA §1).
    """
    matter = factories.MatterFactory(owner=specialist)

    body = _detail(signed_in, matter)

    assert body.count('id="koja-arvamus"') == 1
    # The main-column block and the duplicate strip are gone, not moved.
    assert "positionblock" not in body
    assert "sentstrip" not in body


def test_the_rail_names_the_opinion_and_holds_no_workflow(signed_in, specialist):
    """A 300px column names what Koda sent; it holds neither form nor menu.

    It used to carry an upload disclosure and a link out to a per-Matter
    Arvamused page. Both are gone: the file list has the upload panel, and the
    page the link pointed at is retired (docs/adr/0060 §6).
    """
    matter = factories.MatterFactory(owner=specialist)
    body = _detail(signed_in, matter)

    assert 'id="koja-arvamus"' in body
    assert reverse("matters:matter_position", kwargs={"pk": matter.pk}) not in body
    # No editor in the rail either: there is no free-text position in this
    # product, and `update_position` has no native UI anywhere.
    assert "id_position_summary" not in body


def test_the_rail_travels_to_the_matter_surface_that_carries_it(signed_in, specialist):
    """One surface now. Dokumendid is deliberately full-width with no rail at
    all — browsing forty files is the task that tab exists for — and the third
    surface the rail used to reach is retired
    (templates/matters/matter_documents.html, docs/adr/0060)."""
    matter = factories.MatterFactory(owner=specialist)

    body = _body(signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})))

    assert 'id="koja-arvamus"' in body


# ---------------------------------------------------------------------------
# §2 — Muuda teemat
# ---------------------------------------------------------------------------


def test_the_matter_page_offers_a_visible_edit_action(signed_in, specialist):
    """Visible, not behind the ⋯ glyph.

    The inline controls are still the fastest way to change one fact. What was
    missing was any way at all to correct a Matter filed wrongly in several
    fields at once — and that action cannot be the one hidden behind a glyph
    (Teema QA §2.1).
    """
    matter = factories.MatterFactory(owner=specialist)
    body = _detail(signed_in, matter)

    assert _edit_url(matter) in body
    assert "Muuda teemat" in body


def test_the_edit_page_offers_every_editable_fact(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    # Sildid renders a control only when the governed vocabulary has something
    # in it; an empty vocabulary is a sentence, not an empty fieldset. Since the
    # v2 rebuild the two organisation controls are chips over the reference
    # data and behave the same way, so this world needs one of those too.
    factories.TagFactory()
    factories.OrganisationFactory()
    body = _body(signed_in.get(_edit_url(matter)))

    # The single-value fields keep their own id; the chip groups render one
    # input per option and are therefore asserted by name, which is what the
    # POST carries either way (02-EKRAANID §C).
    for field in (
        "id_title",
        "id_brief_summary",
        "id_policy_area_other",
        "id_received_date",
        "id_response_deadline",
    ):
        assert field in body, field
    for field in (
        "owner",
        "stage",
        "track",
        "policy_areas",
        "source_organisations",
        "addressee_organisation",
        "tags",
        "visibility",
    ):
        assert f'name="{field}"' in body, field


def test_one_save_changes_everything_and_audits_each_fact(
    signed_in, specialist, other_specialist, organisation
):
    """One job, one save, one transaction — and one event per fact changed."""
    matter = factories.MatterFactory(owner=specialist, title="Vale pealkiri")
    area = factories.PolicyAreaFactory(name_et="Keskkond")
    tag = factories.TagFactory(name_et="Prioriteetne")

    response = signed_in.post(
        _edit_url(matter),
        {
            "title": "Pakendiseaduse muutmise eelnõu",
            "brief_summary": "Tõstab pakendiaktsiisi.",
            "owner": str(other_specialist.pk),
            "stage": "",
            "track": "",
            "policy_areas": [str(area.pk)],
            "policy_area_other": "Ringmajandus",
            "source_organisations": [str(organisation.pk)],
            "addressee_organisation": "",
            "received_date": "3.8.2026",
            "response_deadline": "20.8.2026",
            "tags": [str(tag.pk)],
            "visibility": Visibility.NORMAL,
        },
    )
    assert response.status_code == 302

    matter.refresh_from_db()
    assert matter.title == "Pakendiseaduse muutmise eelnõu"
    assert matter.brief_summary == "Tõstab pakendiaktsiisi."
    assert matter.owner == other_specialist
    assert matter.policy_area_other == "Ringmajandus"
    assert [area.name_et for area in matter.policy_areas.all()] == ["Keskkond"]
    assert [tag.name_et for tag in matter.tags.all()] == ["Prioriteetne"]
    assert list(matter.source_organisations.all()) == [organisation]
    assert matter.received_date.isoformat() == "2026-08-03"
    assert matter.response_deadline.isoformat() == "2026-08-20"

    kinds = set(ChangeEvent.objects.filter(matter=matter).values_list("event_type", flat=True))
    assert ChangeEventType.MATTER_TITLE_CHANGED in kinds
    assert ChangeEventType.MATTER_BRIEF_SUMMARY_SET in kinds
    assert ChangeEventType.MATTER_ASSIGNED in kinds
    assert ChangeEventType.MATTER_POLICY_AREAS_CHANGED in kinds
    assert ChangeEventType.TAG_ASSIGNED in kinds


def test_an_unchanged_field_writes_no_event(signed_in, specialist):
    """Re-saving the form untouched must not manufacture a history."""
    matter = factories.MatterFactory(owner=specialist, title="Pakendiseadus")
    before = ChangeEvent.objects.filter(matter=matter).count()

    signed_in.post(
        _edit_url(matter),
        {
            "title": matter.title,
            "brief_summary": matter.brief_summary,
            "owner": str(specialist.pk),
            "stage": "",
            "track": "",
            "policy_area_other": "",
            "addressee_organisation": "",
            "received_date": "",
            "response_deadline": "",
            "visibility": matter.visibility,
        },
    )

    assert ChangeEvent.objects.filter(matter=matter).count() == before


def test_a_blank_title_is_refused_and_everything_typed_survives(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist, title="Endine pealkiri")

    response = signed_in.post(
        _edit_url(matter),
        {
            "title": "   ",
            "brief_summary": "See tekst peab alles jääma.",
            "owner": str(specialist.pk),
            "visibility": Visibility.NORMAL,
        },
    )

    assert response.status_code == 400
    body = _body(response)
    assert "See tekst peab alles jääma." in body
    matter.refresh_from_db()
    assert matter.title == "Endine pealkiri"
    assert matter.brief_summary == ""


def test_a_retired_area_is_shown_and_kept_when_something_else_changes(signed_in, specialist):
    """Correcting a title must not silently unfile an old Matter.

    A queryset limited to the governed 23 would refuse — or worse, drop — an
    area a Matter was filed under years ago. The offered list is the vocabulary
    *plus* what this Matter carries, and the retired one is labelled as retired
    rather than shown as an ordinary option (Teema redesign §7.2).
    """
    matter = factories.MatterFactory(owner=specialist)
    retired = factories.PolicyAreaFactory(name_et="Halduskoormus", is_active=False)
    set_policy_areas(matter=matter, policy_areas=[retired], actor=specialist)

    body = _body(signed_in.get(_edit_url(matter)))
    assert "Halduskoormus" in body
    assert "kasutusest väljas" in body

    signed_in.post(
        _edit_url(matter),
        {
            "title": "Uus pealkiri",
            "owner": str(specialist.pk),
            "policy_areas": [str(retired.pk)],
            "visibility": Visibility.NORMAL,
        },
    )

    matter.refresh_from_db()
    assert matter.title == "Uus pealkiri"
    assert list(matter.policy_areas.all()) == [retired]


def test_the_edit_page_never_offers_provenance(signed_in, specialist):
    """Shown, and refused. Where a record came from is not somebody's to decide."""
    matter = factories.MatterFactory(owner=specialist)
    body = _body(signed_in.get(_edit_url(matter)))

    assert "Muutumatu" in body
    assert "Päritolu" in body
    for name in ("origin", "data_class", "reference_number", "record_mode"):
        assert f'name="{name}"' not in body, name


def test_a_reader_cannot_reach_the_edit_page(client, specialist):
    """404, matching how every other unauthorised Matter surface answers.

    A reader who may not write should not learn that an edit surface exists —
    and the check is on the server, never on the hidden control.
    """
    reader = factories.ReaderFactory()
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(reader)

    assert client.get(_edit_url(matter)).status_code == 404
    assert client.post(_edit_url(matter), {"title": "Kaaperdatud"}).status_code == 404
    matter.refresh_from_db()
    assert matter.title != "Kaaperdatud"


def test_the_edit_page_is_unreachable_for_a_reader(client, specialist, reader):
    """A READER cannot open the edit page of a RESTRICTED Matter.

    The 404 now comes from the write gate rather than from
    `get_visible_matter`: `@business_write_required` runs first, and this
    actor may not write. That is not a weaker guarantee — it is an earlier
    one — but it does mean this route can no longer *demonstrate* the
    visibility rule, because every actor who fails visibility also fails the
    write gate (`ROLES_WITH_BUSINESS_WRITE` is a subset of
    `ROLES_WITH_RESTRICTED_ACCESS`). The visibility rule itself is asserted
    where it is still observable, in `tests/test_authorization.py`, and
    `tests/test_one_write_gate.py` guards the subset relation that makes this
    reasoning true.
    """
    matter = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    client.force_login(reader)

    assert client.get(_edit_url(matter)).status_code == 404


# ---------------------------------------------------------------------------
# §3 — Minu töö is one list
# ---------------------------------------------------------------------------


def test_all_three_modes_share_one_dated_list(signed_in, specialist):
    """TEEN 26.8, OOTAN 27.8, JÄLGIN 28.8 — in that order, in one list.

    The two columns made a lawyer read two lists and merge them in their head.
    The page's organising question has one answer per action and it is a date
    (Teema QA §3).
    """
    doing = factories.MatterFactory(owner=specialist, title="Teen selle ära")
    waiting = factories.MatterFactory(owner=specialist, title="Ootan ministeeriumi")
    monitoring = factories.MatterFactory(owner=specialist, title="Jälgin menetlust")

    set_next_action(matter=doing, text="Koosta arvamus", actor=specialist, target_date=_days(2))
    set_next_action(
        matter=waiting,
        text="Ootan vastust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=_days(3),
        actor=specialist,
    )
    set_next_action(
        matter=monitoring,
        text="Jälgin",
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=_days(4),
        actor=specialist,
    )

    body = _body(signed_in.get(reverse("matters:my_work")))

    # One list. The separate waiting column and its heading are gone.
    assert "Ootan ja kontrollin" not in body
    for title in ("Teen selle ära", "Ootan ministeeriumi", "Jälgin menetlust"):
        assert title in body, title
    # Ordered by date, whatever the stored kind.
    assert body.index("Teen selle ära") < body.index("Ootan ministeeriumi")
    assert body.index("Ootan ministeeriumi") < body.index("Jälgin menetlust")
    # And no row says which kind of step it is: the sentence a lawyer wrote is
    # the whole of the action cell now (ADR 0054). The three titles above are
    # the *text* of three steps, which is why they are still expected.
    for retired in ("mode--do", "mode--wait", "mode--monitor", "TEEN", "OOTAN", "JÄLGIN"):
        assert retired not in body, retired


def test_the_summary_counts_each_action_once(signed_in, specialist):
    for offset, kind, semantics in (
        (2, ActionKind.DO, DateSemantics.DEADLINE),
        (3, ActionKind.WAIT, DateSemantics.REVIEW_ON),
        (-1, ActionKind.DO, DateSemantics.DEADLINE),
    ):
        set_next_action(
            matter=factories.MatterFactory(owner=specialist),
            text="Samm",
            kind=kind,
            date_semantics=semantics,
            target_date=_days(offset),
            actor=specialist,
        )

    response = signed_in.get(reverse("matters:my_work"))

    assert response.context["work"].total == 3
    # Only the DO with a deadline is late. The passed review is not.
    assert response.context["work"].overdue == 1


def test_a_passed_review_is_never_worded_as_late(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    set_next_action(
        matter=matter,
        text="Ootan ministeeriumi vastust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=_days(-4),
        actor=specialist,
    )

    body = _body(signed_in.get(reverse("matters:my_work")))

    # The band it sits in changed — reviews are ordinary dated work now and are
    # merged into *Sel nädalal* rather than having a block of their own — and
    # the rule did not. The row states what its date means, prints a neutral
    # «N p», and nothing calls it a missed deadline (03-BACKEND §1).
    assert "Sel nädalal" in body
    assert "VAATAN ÜLE" in body
    assert "4 p" in body
    assert "4 p üle" not in body
    assert "Üle tähtaja" not in body
    assert "workrow2--overdue" not in body


def test_matters_without_a_next_step_stay_out_of_the_dated_list(signed_in, specialist):
    """An absence is not a date, and must not be given a position in time."""
    factories.MatterFactory(owner=specialist, title="Keegi ei planeerinud midagi")

    response = signed_in.get(reverse("matters:my_work"))

    assert response.context["work"].total == 0
    assert "Järgmise tegevuseta" in _body(response)


# ---------------------------------------------------------------------------
# §4 — a future TEEN reaches the person who owns the file
# ---------------------------------------------------------------------------


def test_handing_the_matter_over_takes_the_open_step_with_it(specialist, other_specialist):
    """The reported defect, exactly.

    `set_next_action` defaults `responsible` to the Matter's owner, so an action
    nobody named a person for is the *owner's* action. Reassigning the Matter
    left it pointing at the previous owner: the new owner's own file had no next
    step in `Minu töö`, and somebody who no longer owned it still carried it.
    """
    matter = factories.MatterFactory(owner=specialist)
    set_next_action(matter=matter, text="Koosta arvamus", actor=specialist, target_date=_days(5))

    assign_matter(matter=matter, owner=other_specialist, actor=specialist)

    action = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert action.responsible == other_specialist
    assert any(
        action.matter_id == matter.id
        for group in selectors.my_work_timeline(other_specialist)
        for action in group.actions
    )
    assert sum(group.count for group in selectors.my_work_timeline(specialist)) == 0


def test_a_step_someone_else_was_named_for_stays_theirs(specialist, other_specialist):
    """The limit of the fix.

    Somebody deliberately made responsible for one step on a colleague's file
    stays responsible. Moving that would be the system overruling a decision a
    person made.
    """
    third = factories.UserFactory()
    matter = factories.MatterFactory(owner=specialist)
    set_next_action(
        matter=matter,
        text="Palun vaata sina",
        actor=specialist,
        responsible=third,
        target_date=_days(5),
    )

    assign_matter(matter=matter, owner=other_specialist, actor=specialist)

    action = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert action.responsible == third


def test_the_handover_is_named_in_the_assignment_event(specialist, other_specialist):
    """One thing happened — the file changed hands — so one event says so."""
    matter = factories.MatterFactory(owner=specialist)
    set_next_action(matter=matter, text="Koosta arvamus", actor=specialist, target_date=_days(5))

    assign_matter(matter=matter, owner=other_specialist, actor=specialist)

    event = ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.MATTER_ASSIGNED
    ).latest("occurred_at")
    assert event.payload["next_action_moved"] is not None


def test_a_do_action_dated_inside_the_horizon_is_never_swallowed(specialist):
    """The second, latent half of §4.

    `overdue`, `today` and `soon` each required DEADLINE semantics while `later`
    required a date past the horizon, so a DO carrying any other semantics and
    dated inside the next week belonged to no band at all. The register's own
    parser produces exactly that for a source naming a vague month.
    """
    matter = factories.MatterFactory(owner=specialist)
    set_next_action(
        matter=matter,
        text="Eeldatavasti sel nädalal",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.EXPECTED_AROUND,
        target_date=_days(3),
        actor=specialist,
    )

    found = [
        action.matter_id
        for group in selectors.my_work_timeline(specialist)
        for action in group.actions
    ]
    assert found == [matter.id]


# ---------------------------------------------------------------------------
# §5 — every date box starts on today
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("form_class", "field"),
    [
        (MatterCreateForm, "received_date"),
        (ComposerForm, "occurred_on"),
        (IncomingIntakeForm, "received_date"),
        (EngagementForm, "occurred_on"),
    ],
)
def test_a_fresh_date_box_starts_on_today(form_class, field):
    """Today, because that is the answer nearly every time.

    Re-typing today's date on every save is the friction people actually
    complained about (Teema QA §5).
    """
    form = form_class()
    assert form[field].initial == timezone.localdate()


def test_a_posted_date_always_beats_the_default():
    """`initial` fills an unbound form only, so nothing here overwrites input."""
    form = ComposerForm({"body": "Märkus", "occurred_on": "3.8.2026"})
    assert form.is_valid(), form.errors
    assert form.cleaned_data["occurred_on"].isoformat() == "2026-08-03"


def test_today_is_recorded_as_now_and_not_as_midnight(normal_matter, specialist):
    """The box is pre-filled with today, so leaving it alone is the ordinary
    case — and stamping 00:00 on something written at half past two would be a
    small untruth on every routine save.
    """
    form = ComposerForm({"body": "Märkus"})
    assert form.is_valid(), form.errors
    assert form.as_service_kwargs()["occurred_at"] is None


@pytest.mark.parametrize(
    ("form_class", "field"),
    [
        (ComposerForm, "next_date"),
        (NextActionForm, "target_date"),
        (ComposerForm, "deadline_date"),
        (ComposerForm, "final_sent_on"),
        (EffectiveDateForm, "exact_date"),
        (MatterCreateForm, "response_deadline"),
    ],
)
def test_a_date_box_whose_emptiness_is_a_signal_never_defaults(form_class, field):
    """The limit of §5, and the browser lane is what found it.

    A date box defaults to today when the box is the only thing it says. These
    are read for *emptiness*:

    * `next_date` — a step with no date is refused, and a default answers that
      refusal with a deadline nobody chose;
    * `NextActionForm.target_date` — the same box on Uus teema, and it moved
      here from the defaulting list above. It defaulted to today while the page
      also asked for a kind and a date meaning, on the reasoning that today is
      the answer nearly every time. It is the same reasoning `next_date` was
      refused for: a blank new-Teema form silently carrying today is a factual
      next-action date nobody stated, and it turned "you forgot the date" into
      a date the form chose (ADR 0052 addendum);
    * `deadline_date` — the same control, which also offers a quarter;
    * `final_sent_on` — a send date with no chosen file is an opinion claimed
      without its evidence, so a default refuses every ordinary closure;
    * `PeriodForm.exact_date` — `Jõustub üldises korras` means the date is not
      known, and a form carrying one is refused.

    `Arvamuse tähtaeg` joined them, and by this rule rather than against it.
    ADR 0031 put it in the defaulting list because at the time nothing read its
    emptiness — the date was stored, shown on the Matter header, and that was
    all. It is now the third source of the shared work model, so an empty box
    means *no commitment* and a filled one means *a deadline exists on this
    day*. Under the old default a Matter created and left alone was due on the
    day it was entered and overdue everywhere the next morning, against a
    promise nobody had made (app/matters/work_items.py, ADR 0031 §5 amendment).

    `Saabus` stays defaulted, one field above it in the same form, and the
    contrast is the point: an arrival date is an observation, nearly everything
    does arrive on the day it is typed in, and nothing reads its emptiness.

    A default in any of these does not save typing. It states a fact nobody gave.
    """
    assert form_class()[field].initial is None


def test_a_closure_without_a_sent_opinion_is_accepted(normal_matter, specialist):
    """What the `final_sent_on` default broke, end to end."""
    form = ComposerForm(
        {
            "body": "Menetlus lõppes.",
            "disposition": Disposition.COMPLETED,
            "work_victory": "EI",
        }
    )
    assert form.is_valid(), form.errors


def test_a_next_step_without_a_date_is_still_refused(normal_matter):
    """And what the `next_date` default broke.

    The composer stopped asking for a kind (ADR 0052), so the shape of the
    refusal moved: it is no longer «a TEEN needs a date» but «this next step
    needs a date», raised on the empty control. The rule it protects is the
    same one, and the reason `next_date` keeps no default is unchanged.
    """
    form = ComposerForm({"body": "Koosta arvamus", "next_text": "Koosta arvamus"})
    assert not form.is_valid()
    assert form.errors["next_date"] == ["Vali järgmise tegevuse kuupäev."]


def test_the_edit_page_invents_no_date_for_a_matter_that_has_none(specialist):
    """The one form that must *not* default to today.

    It is always opened on a Matter that already exists, so a default would only
    ever apply where a date is genuinely empty — and there, pre-filling today
    would state a fact nobody gave.
    """
    matter = factories.MatterFactory(owner=specialist, received_date=None)
    form = MatterEditForm(initial={"received_date": matter.received_date}, matter=matter)
    assert form["received_date"].value() is None


# ---------------------------------------------------------------------------
# §7 — the mode chips are gone
# ---------------------------------------------------------------------------


def _static_css() -> str:
    from pathlib import Path

    from django.conf import settings

    return (Path(settings.BASE_DIR) / "static" / "css" / "app.css").read_text(encoding="utf-8")


def test_the_mode_chip_has_no_rules_left_to_be_legible_in():
    """§7 asked whether the three chips stayed legible selected and unselected.

    Both answers are now moot. The composer stopped asking for the
    classification (ADR 0052), `Uus teema` stopped asking for it after that, and
    the stored kind is not displayed anywhere at all (ADR 0054) — so the chips
    have no renderer and their rules are gone with them. Kept as an assertion
    rather than deleted, because a stylesheet is exactly where a retired
    component comes back: a rule nothing uses looks like a rule something is
    about to.
    """
    import re

    # Comments only. The stylesheet still explains, where the rules used to
    # live, why they are not there — which is the note the next person needs.
    css = re.sub(r"/\*.*?\*/", " ", _static_css(), flags=re.S)

    for retired in (".modeselect", ".modechip", ".mode--do", ".mode--wait", ".mode--monitor"):
        assert retired not in css, retired


# ---------------------------------------------------------------------------
# §8 — Kaasamine has exactly one path
# ---------------------------------------------------------------------------


def test_the_composer_does_not_offer_a_second_kaasamine(signed_in, normal_matter):
    """Two ways to create the same record, with different fields.

    The composer's version asked for a kind and a date; the section's version
    asks for the title, the participants and the link the record is actually
    for. Keeping both meant a Kaasamine created one way was quietly poorer than
    the same thing created the other (Teema QA §8).
    """
    body = _detail(signed_in, normal_matter)

    assert "+ Kaasamine" not in body
    assert "koostaja-kaasamine" not in body
    assert 'name="engagement_title"' not in body


def test_the_composer_form_has_no_engagement_fields():
    assert not [name for name in ComposerForm().fields if name.startswith("engagement")]

    bound = ComposerForm({"body": "Märkus"})
    assert bound.is_valid(), bound.errors
    assert "engagement" not in bound.as_service_kwargs()


def test_the_one_kaasamine_path_still_works(signed_in, normal_matter, specialist):
    """Removing the duplicate removed nothing a person could do."""
    response = signed_in.post(
        reverse("matters:add_engagement", kwargs={"pk": normal_matter.pk}),
        {"title": "Liikmete küsitlus", "kind": "SURVEY", "occurred_on": "5.8.2026"},
    )
    assert response.status_code in (200, 302)
    assert normal_matter.engagements.filter(title="Liikmete küsitlus").exists()


# ---------------------------------------------------------------------------
# The whole page still holds together
# ---------------------------------------------------------------------------


def test_the_matter_page_renders_for_a_matter_with_nothing_on_it(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    response = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    assert "Arvamust ei ole lisatud." in _body(response)


def test_the_edit_page_reaches_every_matter_it_should(signed_in, specialist):
    for matter in (
        factories.MatterFactory(owner=specialist),
        factories.ArchiveMatterFactory(owner=specialist),
    ):
        assert signed_in.get(_edit_url(matter)).status_code == 200


def test_nothing_writes_a_matter_outside_a_service(signed_in, specialist):
    """Every field the edit page changes leaves an audit row behind."""
    matter = factories.MatterFactory(owner=specialist, title="Enne")
    signed_in.post(
        _edit_url(matter),
        {"title": "Pärast", "owner": str(specialist.pk), "visibility": Visibility.NORMAL},
    )

    event = ChangeEvent.objects.get(matter=matter, event_type=ChangeEventType.MATTER_TITLE_CHANGED)
    assert event.payload == {"previous": "Enne", "current": "Pärast"}
    assert event.actor == specialist
    assert Matter.objects.get(pk=matter.pk).title == "Pärast"


def test_a_policy_area_is_never_created_by_editing(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    before = PolicyArea.objects.count()

    signed_in.post(
        _edit_url(matter),
        {
            "title": "Pealkiri",
            "owner": str(specialist.pk),
            "policy_area_other": "Miski päris uus valdkond",
            "visibility": Visibility.NORMAL,
        },
    )

    assert PolicyArea.objects.count() == before
    matter.refresh_from_db()
    assert matter.policy_area_other == "Miski päris uus valdkond"
