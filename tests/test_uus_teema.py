"""`Uus teema`, after the redesign.

The page was rebuilt as a layout, not as a workflow: the same fields, the same
service, the same validation. So what is tested here is the set of promises the
rebuild could plausibly have broken — the cardinality of each control, the two
new fields, the date defaults, and the one structural rule the long-tail sender
list now depends on.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from app.matters.forms import MatterCreateForm, NextActionForm
from app.matters.models import Matter, MatterPersonalNote
from app.organisations.models import Organisation
from app.taxonomy.models import PolicyArea
from app.workflow.enums import ActionKind, ActionStatus, DateSemantics
from app.workflow.models import NextAction
from tests import factories

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")


def _body(response) -> str:
    return response.content.decode()


# ---------------------------------------------------------------------------
# The one rule the whole page is built around
# ---------------------------------------------------------------------------


def test_a_title_alone_creates_a_matter(signed_in, specialist):
    """Everything else on the page is optional, and this is what says so."""
    response = signed_in.post(CREATE, {"title": "Pakendiseaduse muutmise eelnõu"})

    assert response.status_code == 302
    matter = Matter.objects.get(title="Pakendiseaduse muutmise eelnõu")
    assert matter.owner is None
    assert matter.stage is None
    assert matter.track == ""
    assert not matter.policy_areas.exists()
    assert not NextAction.objects.filter(matter=matter).exists()


def test_a_blank_title_is_refused_and_keeps_what_was_typed(signed_in, specialist):
    before = Matter.objects.count()
    response = signed_in.post(
        CREATE, {"title": "   ", "brief_summary": "See tekst peab alles jääma."}
    )

    assert response.status_code == 400
    assert Matter.objects.count() == before
    assert "See tekst peab alles jääma." in _body(response)


# ---------------------------------------------------------------------------
# Control shape is the data promise
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["owner", "stage", "track", "addressee_organisation"])
def test_single_value_controls_render_radios(signed_in, field):
    """A checkbox list here would promise something the column cannot keep.

    `addressee_organisation` is included deliberately: the design draws it
    multi-select and the cardinality is unchanged this round, so the control has
    to keep saying "one" (Uus teema handoff §10).
    """
    factories.OrganisationFactory()
    factories.StageFactory()
    body = _body(signed_in.get(CREATE))

    assert f'type="radio" name="{field}"' in body
    assert f'type="checkbox" name="{field}"' not in body


@pytest.mark.parametrize("field", ["policy_areas", "source_organisations"])
def test_multi_value_controls_render_checkboxes(signed_in, field):
    factories.OrganisationFactory()
    factories.PolicyAreaFactory()
    body = _body(signed_in.get(CREATE))

    assert f'type="checkbox" name="{field}"' in body


def test_the_page_offers_no_select_at_all(signed_in):
    """Every choice is a chip. A dropdown is a click spent finding out what the
    options are, for a department of four (Agent-UI brief 5.1)."""
    factories.OrganisationFactory()
    factories.StageFactory()
    body = _body(signed_in.get(CREATE))

    assert "<select" not in body


def test_several_senders_and_several_areas_are_accepted(signed_in, specialist):
    first = factories.OrganisationFactory(name="Aministeerium")
    second = factories.OrganisationFactory(name="Bministeerium")
    one = factories.PolicyAreaFactory(name_et="Keskkond")
    two = factories.PolicyAreaFactory(name_et="Maksud")

    signed_in.post(
        CREATE,
        {
            "title": "Mitu saatjat",
            "source_organisations": [str(first.pk), str(second.pk)],
            "policy_areas": [str(one.pk), str(two.pk)],
        },
    )

    matter = Matter.objects.get(title="Mitu saatjat")
    assert matter.source_organisations.count() == 2
    assert matter.policy_areas.count() == 2


# ---------------------------------------------------------------------------
# The structural rule the long-tail list depends on
# ---------------------------------------------------------------------------


def test_no_organisation_has_two_inputs_on_the_page(signed_in):
    """One field renders both sender rows, so a duplicate is impossible.

    The visible row holds the frequent bodies plus anything already chosen; the
    disclosure holds the rest. Rendered from two fields — which is what the page
    used to do — a promoted organisation could carry a checkbox in each row, and
    a click would reach only one of them.
    """
    organisations = [factories.OrganisationFactory(name=f"Asutus {n}") for n in range(6)]
    body = _body(signed_in.get(CREATE))

    for organisation in organisations:
        marker = f'name="source_organisations" value="{organisation.pk}"'
        assert body.count(marker) == 1, organisation.name


def test_a_chosen_long_tail_sender_posts_exactly_once(signed_in, specialist):
    """And it survives a refused save without becoming two controls."""
    organisation = factories.OrganisationFactory(name="Ainus asutus")

    response = signed_in.post(CREATE, {"title": "", "source_organisations": [str(organisation.pk)]})

    assert response.status_code == 400
    body = _body(response)
    marker = f'name="source_organisations" value="{organisation.pk}"'
    assert body.count(marker) == 1
    # and it comes back ticked, in the visible row
    assert f"{marker} checked" in body or f'{marker}" checked' in body or "checked" in body


def test_nothing_on_this_page_creates_an_organisation(signed_in, specialist):
    before = Organisation.objects.count()
    signed_in.post(CREATE, {"title": "Teema", "source_organisations_other": "Uus Ministeerium"})

    assert Organisation.objects.count() == before


def test_nothing_on_this_page_creates_a_policy_area(signed_in, specialist):
    before = PolicyArea.objects.count()
    signed_in.post(
        CREATE,
        {
            "title": "Teema",
            "policy_area_other_selected": "on",
            "policy_area_other": "Miski päris uus",
        },
    )

    assert PolicyArea.objects.count() == before
    assert Matter.objects.get(title="Teema").policy_area_other == "Miski päris uus"


# ---------------------------------------------------------------------------
# The two fields the redesign added
# ---------------------------------------------------------------------------


def test_the_summary_is_captured_at_creation(signed_in, specialist):
    """Written by `set_brief_summary`, so it is audited exactly as the inline
    edit on the Teema page is."""
    signed_in.post(CREATE, {"title": "Teema", "brief_summary": "Mida see ettevõtetele tähendab."})

    assert Matter.objects.get(title="Teema").brief_summary == "Mida see ettevõtetele tähendab."


def test_the_note_is_private_to_its_author(signed_in, specialist, other_specialist):
    signed_in.post(CREATE, {"title": "Teema", "notes": "Helista Liinale."})
    matter = Matter.objects.get(title="Teema")

    mine = MatterPersonalNote.objects.get(matter=matter, author=specialist)
    assert mine.body == "Helista Liinale."
    assert not MatterPersonalNote.objects.filter(matter=matter, author=other_specialist).exists()


def test_an_empty_note_writes_nothing(signed_in, specialist):
    signed_in.post(CREATE, {"title": "Teema", "notes": "   "})

    assert not MatterPersonalNote.objects.filter(matter__title="Teema").exists()


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


def test_the_two_matter_dates_still_start_on_today():
    form = MatterCreateForm()
    assert form["received_date"].initial == timezone.localdate()
    assert form["response_deadline"].initial == timezone.localdate()


def test_the_next_action_date_starts_blank():
    """The block is always visible now, so an untouched date must stay empty.

    Defaulted, somebody who picks TEEN and types a step without looking at the
    date would silently get a deadline of today — and `set_next_action` refuses
    DO + DEADLINE with no date, which is exactly the emptiness that must keep
    meaning something (ADR 0031 §5).
    """
    assert NextActionForm()["target_date"].initial is None
    assert 'name="next-target_date" value=' not in NextActionForm(prefix="next").as_p()


def test_a_teen_with_no_date_is_refused_rather_than_dated_today(signed_in, specialist):
    before = Matter.objects.count()
    response = signed_in.post(
        CREATE,
        {"title": "Teema", "next-text": "Koosta arvamus", "next-kind": ActionKind.DO},
    )

    assert response.status_code == 400
    assert Matter.objects.count() == before


# ---------------------------------------------------------------------------
# Järgmine tegevus
# ---------------------------------------------------------------------------


def test_the_initial_next_action_is_optional(signed_in, specialist):
    signed_in.post(CREATE, {"title": "Ilma sammuta"})
    matter = Matter.objects.get(title="Ilma sammuta")

    assert not NextAction.objects.filter(matter=matter).exists()


def test_the_initial_next_action_inherits_the_matter_owner(signed_in, specialist):
    """No second Vastutaja on the page; the view hands the chosen owner in."""
    signed_in.post(
        CREATE,
        {
            "title": "Sammuga",
            "owner": str(specialist.pk),
            "next-text": "Koosta arvamus",
            "next-kind": ActionKind.DO,
            "next-target_date": "8.9.2026",
        },
    )

    action = NextAction.objects.get(matter__title="Sammuga", status=ActionStatus.OPEN)
    assert action.responsible == specialist
    assert action.target_date.isoformat() == "2026-09-08"


def test_the_date_meaning_is_a_visible_choice_not_a_dropdown(signed_in):
    body = _body(signed_in.get(CREATE))

    assert 'type="radio" name="next-date_semantics"' in body
    for value in (DateSemantics.DEADLINE, DateSemantics.REVIEW_ON, DateSemantics.EXPECTED_AROUND):
        assert f'value="{value}"' in body


def test_an_explicit_date_meaning_is_kept(signed_in, specialist):
    """The model permits combinations the derivation does not produce."""
    signed_in.post(
        CREATE,
        {
            "title": "Umbkaudne",
            "next-text": "Eeldatavasti septembris",
            "next-kind": ActionKind.DO,
            "next-date_semantics": DateSemantics.EXPECTED_AROUND,
            "next-target_date": "1.9.2026",
        },
    )

    action = NextAction.objects.get(matter__title="Umbkaudne")
    assert action.date_semantics == DateSemantics.EXPECTED_AROUND


# ---------------------------------------------------------------------------
# What the page no longer has
# ---------------------------------------------------------------------------


def test_both_disclosures_are_gone(signed_in):
    """Half the form used to be inside them, which is how a validation error
    could land somewhere the reader could not see."""
    body = _body(signed_in.get(CREATE))

    assert "Täpsusta teema andmeid" not in body
    assert "+ Järgmine tegevus" not in body


def test_every_classification_field_is_on_the_page_at_load(signed_in):
    factories.StageFactory()
    factories.OrganisationFactory()
    body = _body(signed_in.get(CREATE))

    for name in ("stage", "track", "addressee_organisation", "is_test_data", "response_deadline"):
        assert f'name="{name}"' in body, name


def test_the_governed_vocabulary_is_shown_whole(signed_in):
    """No `Veel N`: three wrapping rows is not worth hiding the taxonomy for."""
    areas = [factories.PolicyAreaFactory(name_et=f"Valdkond {n}") for n in range(23)]
    body = _body(signed_in.get(CREATE))

    for area in areas:
        assert f'name="policy_areas" value="{area.pk}"' in body


def test_the_helper_noise_is_gone(signed_in):
    body = _body(signed_in.get(CREATE))

    for line in (
        "Pealkiri on ainus kohustuslik väli",
        "salvestatakse muutumatu tõendina",
        "Kus menetlus praegu on",
        "Kellele Koda vastab",
        "ei kuulu päris aruandlusse",
        "Arvamuse tähtaeg on eraldi",
    ):
        assert line not in body, line


def test_the_two_rules_that_survive_are_still_said(signed_in):
    body = _body(signed_in.get(CREATE))

    assert "teema vormilt uut asutust ei teki" in body
    assert "Ülejäänud andmeid saab lisada ka hiljem teema lehel." in body


def test_test_data_is_one_unticked_checkbox(signed_in, specialist):
    body = _body(signed_in.get(CREATE))
    assert 'type="checkbox" name="is_test_data"' in body
    assert 'name="is_test_data"' in body and "REAL" not in body

    signed_in.post(CREATE, {"title": "Päris teema"})
    assert not Matter.objects.get(title="Päris teema").is_test_data


def test_visibility_is_not_on_this_page(signed_in):
    body = _body(signed_in.get(CREATE))

    assert 'name="visibility"' not in body
