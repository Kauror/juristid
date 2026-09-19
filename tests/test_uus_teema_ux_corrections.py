"""`Uus teema` draws its vocabularies at rest, and asks for one date.

Corrections from two rounds of lawyer feedback. The date half is docs/adr/0094;
the classification half is docs/adr/0096 §2, which **reverses** §2 of that ADR —
`Valdkonnad` and `Hetkeseis` were menus for one release and the lawyers
reviewing the live page asked for the visible chips back.

This file owns the half a browser cannot state — what the server renders, what
it binds and what it writes — and `e2e/test_uus_teema_menus.py` owns the half
only a browser can: that every choice is on screen and clickable without
opening anything, and that answering one control does not move the next.

The two halves are deliberately separate and deliberately *both*. Markup is not
layout, and a measured layout says nothing about what the POST stores.

Where a rule already had an owner it stays there and is not restated:

* `tests/test_procedural_links_on_uus_teema.py` owns the link's create path;
* `tests/test_lawyer_workflow_package.py` owns `Koostan arvamuse` and the
  service that establishes it;
* `tests/test_uus_teema_manual_first.py` owns the Valdkond vocabulary and its
  withdrawals;
* `tests/test_matter_form_controls.py` owns the absence of `Järgmiseks`;
* `tests/test_simplified_next_action.py` owns the composer, which is where a
  free-text first step is stated now.
"""

from __future__ import annotations

import re
from datetime import date

import pytest
from django.urls import reverse

from app.matters.forms import MatterCreateForm
from app.matters.models import Matter
from app.taxonomy.models import PolicyArea
from app.workflow.models import NextAction
from tests import factories

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")


def _page(client) -> str:
    response = client.get(CREATE)
    assert response.status_code == 200
    return response.content.decode()


def _fieldset(page: str, *, name: str) -> str:
    """The `<fieldset>` one classification is drawn in, from `<fieldset` to close.

    Anchored on the field's own name rather than on a class, because the class
    is what three rounds have changed and the name is what has not.
    """
    at = page.index(f'name="{name}"')
    start = page.rindex("<fieldset", 0, at)
    return page[start : page.index("</fieldset>", at)]


def _rendered_input(page: str, field_id: str) -> str:
    match = re.search(rf'<input[^>]*id="{field_id}"[^>]*>', page)
    assert match is not None, field_id
    return match.group(0)


CLASSIFICATIONS = ["policy_areas", "stage", "legal_instruments"]
CLASSIFICATION_IDS = ["valdkonnad", "hetkeseis", "oigusakt"]


# ---------------------------------------------------------------------------
# docs/adr/0096 §2 — every vocabulary is drawn at rest, and none is a menu
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", CLASSIFICATIONS, ids=CLASSIFICATION_IDS)
def test_every_vocabulary_is_visible_without_opening_anything(signed_in, name):
    """No trigger, no panel, no popover: the chips are on the page on arrival.

    The reversal of docs/adr/0094 §2. `Valdkonnad` and `Hetkeseis` spent a round
    as `chipmenu`s — a pill and a panel taken out of flow — and the lawyers
    reviewing the live page asked for the visible chips back, so that the three
    classifications are one kind of control rather than two.

    Asserted as the absence of the machinery rather than as the presence of a
    class name: a `<details>` inside the block, a trigger, a panel or a
    `data-chipmenu` hook would each be a click standing between somebody and an
    answer they came to give.
    """
    factories.StageFactory(label_et="Kooskõlastusringil")
    block = _fieldset(_page(signed_in), name=name)

    assert "<details" not in block
    assert "chipmenu" not in block
    assert "<summary" not in block
    assert "<select" not in block


@pytest.mark.parametrize("name", CLASSIFICATIONS, ids=CLASSIFICATION_IDS)
def test_every_choice_is_rendered_on_the_first_load(signed_in, specialist, name):
    """Every option the form offers is in the markup the first GET returns.

    The measurable half of «no extra click»: a control that rendered its
    vocabulary only after a request would pass the test above and still cost the
    click it exists to remove.
    """
    factories.StageFactory(label_et="Kooskõlastusringil")
    block = _fieldset(_page(signed_in), name=name)

    form = MatterCreateForm(viewer=specialist)
    for value, _label in form.fields[name].choices:
        assert f'value="{value}"' in block


def test_the_muu_box_is_beside_the_vocabulary_and_opens_on_a_refusal(signed_in):
    """`Muu` reveals its box, and a refusal about it comes back legible.

    The box lives inside the `Valdkonnad` fieldset now rather than under a
    trigger, because there is no trigger — and nothing is hidden by that: it is
    rendered open whenever the chip is ticked, server-side, so a refused save
    shows the box, what was typed and why it was refused without any scripting
    having to run.
    """
    response = signed_in.post(
        CREATE, {"title": "Muu ilma tekstita", "policy_area_other_selected": "on"}
    )
    page = response.content.decode()

    assert response.status_code == 400
    assert "Kirjuta, millise valdkonnaga on tegemist." in page
    # Open, because the chip that reveals it arrived ticked.
    revealed = page[page.index('id="valdkond-muu-tekst"') :]
    assert "hidden" not in revealed[: revealed.index(">")]
    # And the chip itself is a choice among the others.
    assert 'id="valdkond-muu"' in _fieldset(page, name="policy_areas")


def test_the_muu_box_is_shut_on_a_fresh_form(signed_in):
    """Nothing ticked, nothing revealed — the other half of the rule above."""
    page = _page(signed_in)
    revealed = page[page.index('id="valdkond-muu-tekst"') :]
    assert "hidden" in revealed[: revealed.index(">")]


# ---------------------------------------------------------------------------
# The controls keep their cardinality and their meaning
# ---------------------------------------------------------------------------


def test_valdkonnad_are_still_checkboxes_and_hetkeseis_still_radios(signed_in):
    """The shape of a control is a promise about the data (ADR 0025).

    Three rounds have changed how these look and none has changed what they
    are: `Matter.policy_areas` holds several and `Matter.stage` holds one.
    """
    factories.StageFactory(label_et="Kooskõlastusringil")
    page = _page(signed_in)

    assert 'type="checkbox" name="policy_areas"' in _fieldset(page, name="policy_areas")
    assert 'type="radio" name="stage"' in _fieldset(page, name="stage")


def test_neither_vocabulary_is_a_native_multiple_listbox(signed_in):
    """Explicitly refused by the brief, and refused before by ADR 0025.

    A `<select multiple>` hides multi-selection behind a modifier key nobody
    uses, is unstyleable, and on a touch device is a platform sheet rather than
    the page. Scoped to the two blocks, because `Failid` is legitimately a
    `<input type="file" multiple>` and always was.
    """
    factories.StageFactory(label_et="Kooskõlastusringil")
    page = _page(signed_in)

    for name in ("policy_areas", "stage"):
        block = _fieldset(page, name=name)
        assert "<select" not in block
        assert "multiple" not in block


def test_each_vocabulary_is_a_named_group_for_a_screen_reader(signed_in):
    """A row of unlabelled checkboxes is a group nobody can answer.

    The legend is visible now rather than `visually-hidden`: it was hidden only
    because the menu's trigger directly above already said the word, and there
    is no trigger any more.
    """
    factories.StageFactory(label_et="Kooskõlastusringil")
    page = _page(signed_in)

    for name, label in (("policy_areas", "Valdkonnad"), ("stage", "Hetkeseis")):
        block = _fieldset(page, name=name)
        legend = re.search(r"<legend[^>]*>(.*?)</legend>", block, re.S)
        assert legend is not None
        assert label in legend.group(1)
        assert "visually-hidden" not in legend.group(0)


def test_the_stage_explanations_are_still_one_bubble_per_explained_chip(signed_in):
    """Untouched by the shape change.

    The description reaches a screen reader through `aria-describedby`, which is
    why it is a sibling of the label rather than inside it — putting it inside
    renamed the radio to the whole paragraph (Uus teema redesign §8).
    """
    factories.StageFactory(label_et="Kooskõlastusringil", help_text="Kasuta siis, kui…")
    hetkeseis = _fieldset(_page(signed_in), name="stage")

    assert 'role="tooltip"' in hetkeseis
    assert "aria-describedby" in hetkeseis
    assert "Kasuta siis, kui…" in hetkeseis
    # Beside the label, never inside it.
    assert '<span class="stagehelp"' in hetkeseis
    assert "Kasuta siis, kui…</span>\n" not in hetkeseis[: hetkeseis.index("chip__name")]


# ---------------------------------------------------------------------------
# §8 — validation redisplay
# ---------------------------------------------------------------------------


def test_a_refused_save_gives_back_every_answer_the_four_controls_hold(signed_in):
    """All four corrections at once, on the path that loses answers.

    A browser cannot put a value back into a box the server did not re-render,
    so this is the whole of what «the values survive» means. With the
    vocabularies drawn at rest there is nothing left to come back *open*: every
    chip is on the page and the ticked ones are ticked.
    """
    stage = factories.StageFactory(label_et="Kooskõlastusringil")
    areas = list(PolicyArea.objects.filter(is_active=True)[:2])

    response = signed_in.post(
        CREATE,
        {
            # Refused for the one reason `MatterCreateForm` refuses on its own.
            "title": "",
            "policy_areas": [str(area.pk) for area in areas],
            "stage": str(stage.pk),
            "menetlus-url": "https://eelnoud.valitsus.ee/main/mount/docList/abc",
            "menetlus-label": "Eelnõu 123 SE",
            "response_deadline": "18.9.2026",
        },
    )
    page = response.content.decode()

    assert response.status_code == 400
    assert not Matter.objects.exists()

    # Valdkonnad: every chip back, and the two that were ticked still ticked.
    valdkonnad = _fieldset(page, name="policy_areas")
    for area in areas:
        chosen = valdkonnad[valdkonnad.index(f'value="{area.pk}"') :]
        assert "checked" in chosen[: chosen.index(">")]

    # Hetkeseis: one value, and it is the one that was sent.
    hetkeseis = _fieldset(page, name="stage")
    chosen = hetkeseis[hetkeseis.index(f'value="{stage.pk}"') :]
    assert "checked" in chosen[: chosen.index(">")]
    assert hetkeseis.count("checked") == 1

    # Menetluse link and the deadline.
    assert 'value="https://eelnoud.valitsus.ee/main/mount/docList/abc"' in page
    assert 'value="Eelnõu 123 SE"' in page
    assert 'value="18.9.2026"' in page

    # And nothing the removed block used to post comes back.
    assert 'name="next-text"' not in page
    assert 'name="menetlus-kind"' not in page


def test_unknown_stays_unknown_across_a_refusal(signed_in):
    """No value is silently replaced by a default on the way back.

    The dangerous half of the redisplay rule: a refused save that quietly ticks
    «Määramata» as a *stored* answer, or fills the deadline with today, would be
    the page answering on somebody's behalf while telling them to fix something
    else.
    """
    factories.StageFactory(label_et="Kooskõlastusringil")

    response = signed_in.post(CREATE, {"title": ""})
    page = response.content.decode()

    assert response.status_code == 400
    # Read out of the rendered tag rather than asserted as the absence of a
    # hand-written string: `value=` precedes `id=` in what Django writes, so a
    # regex that assumed the other order would pass on any page at all.
    assert 'value=""' in _rendered_input(page, "id_response_deadline")

    # And the fresh form is where the measurement has something to measure
    # against: `Saabus` legitimately arrives holding today — `initial` fills an
    # unbound form — while `Arvamuse tähtaeg` deliberately carries no default,
    # because a commitment nobody stated is one nobody can be held to
    # (docs/adr/0078 §2). Without this pair the line above would pass on a page
    # with no dates on it at all.
    fresh = _page(signed_in)
    assert re.search(r'value="\d+\.\d+\.\d+"', _rendered_input(fresh, "id_received_date"))
    assert 'value=""' in _rendered_input(fresh, "id_response_deadline")
    # Nothing is stored, because nothing was saved.
    assert not Matter.objects.exists()


# ---------------------------------------------------------------------------
# §4, §5, §7 — order, the one date, and the step it establishes
# ---------------------------------------------------------------------------


def test_the_deadline_establishes_the_opinion_step_exactly_once(signed_in, specialist):
    """One date, one obligation, one step — and no second step beside it."""
    response = signed_in.post(
        CREATE,
        {"title": "Üks kuupäev", "owner": str(specialist.pk), "response_deadline": "18.9.2026"},
    )

    assert response.status_code == 302
    matter = Matter.objects.get(title="Üks kuupäev")
    assert matter.response_deadline == date(2026, 9, 18)

    actions = list(NextAction.objects.filter(matter=matter))
    assert len(actions) == 1
    assert actions[0].text == "Koostan arvamuse"
    assert actions[0].target_date == date(2026, 9, 18)


def test_a_blank_deadline_invents_nothing(signed_in):
    """Not today, not the arrival date, and not an undated commitment."""
    signed_in.post(CREATE, {"title": "Ilma kuupäevata", "received_date": "24.8.2026"})

    matter = Matter.objects.get(title="Ilma kuupäevata")
    assert matter.response_deadline is None
    assert not NextAction.objects.filter(matter=matter).exists()
