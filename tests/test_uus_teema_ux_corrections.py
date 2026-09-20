"""`Uus teema` draws its choices, and asks for one date (docs/adr/0096).

The menus are withdrawn. `Valdkonnad` and `Hetkeseis` are `<details open>`
sections in ordinary flow — every option on the page when it arrives, a
collapsible trigger over them, and nothing floating over anything
(docs/adr/0096 §1–§2). What the round before built, and this file used to
assert, was the opposite: a pill over an out-of-flow panel that had to be opened
before an ordinary choice could be made (docs/adr/0094 §2).

This file owns the half a browser cannot state — what the server renders, what
it binds and what it writes — and `e2e/test_uus_teema_valikud.py` owns the half
only a browser can: that the options are visible without a click, that answering
one does not move the other, and that collapsing is the reader's own act.

The two halves are deliberately separate and deliberately *both*. A class name in
the markup is not a visible chip, and a measured layout says nothing about what
the POST stores.

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


#: The two folds, named by the field each one answers. Both open tags are now
#: identical — which is the point, they are one component — so the discriminator
#: is the summary's own `data-chipsummary-for`, i.e. the field name that reaches
#: the server. Nothing here depends on the order of the two rows.
FOLDS = {"valdkonnad": "policy_areas", "hetkeseis": "stage"}


def _fold(page: str, which: str) -> str:
    """One `<details class="chipfold">`, from its opening tag to its close."""
    anchor = page.index(f'data-chipsummary-for="{FOLDS[which]}"')
    start = page.rindex('<details class="chipfold"', 0, anchor)
    return page[start : page.index("</details>", start)]


def _trigger(fold: str) -> str:
    return fold[fold.index("<summary") : fold.index("</summary>")]


def _rendered_input(page: str, field_id: str) -> str:
    match = re.search(rf'<input[^>]*id="{field_id}"[^>]*>', page)
    assert match is not None, field_id
    return match.group(0)


# ---------------------------------------------------------------------------
# §1 — both vocabularies are drawn, and neither is a menu
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("which", list(FOLDS), ids=list(FOLDS))
def test_each_vocabulary_is_a_fold_that_arrives_open(signed_in, which):
    """The markup contract, which is what makes the choices visible.

    Not the layout itself — that is CSS and is measured in the browser. What is
    asserted here is that the server renders the section *open*, because a
    `<details>` without the attribute is shut before a single stylesheet loads,
    and every option in it is then a click away on the ordinary first visit.
    """
    factories.StageFactory(label_et="Kooskõlastusringil")
    page = _page(signed_in)
    fold = _fold(page, which)

    assert '<summary class="chipfold__trigger">' in fold
    assert "chipfold__body" in fold
    # Open on arrival, every time.
    assert fold.startswith('<details class="chipfold" open>')


@pytest.mark.parametrize("which", list(FOLDS), ids=list(FOLDS))
def test_a_fold_is_open_on_a_refused_render_too(signed_in, which):
    """A refusal is the one render where a shut section would be worst.

    This is also why the page still has no `policy_area_disclosure_open`. The
    old fold had to be *computed* open on a refusal, because the box being
    refused was inside it; the menu after it was shut on every render including
    a refused one. Neither is a state any more: the section is open on every
    render, so there is nothing for the server to decide (docs/adr/0096 §1).
    """
    factories.StageFactory(label_et="Kooskõlastusringil")
    areas = list(PolicyArea.objects.filter(is_active=True)[:2])

    response = signed_in.post(
        CREATE,
        {
            "title": "",
            "policy_areas": [str(area.pk) for area in areas],
            "policy_area_other_selected": "on",
        },
    )

    assert response.status_code == 400
    fold = _fold(response.content.decode(), which)
    assert fold.startswith('<details class="chipfold" open>')


def test_the_muu_box_and_both_refusals_are_outside_the_valdkonnad_fold(signed_in):
    """A box that vanishes when the section is collapsed cannot be finished."""
    response = signed_in.post(
        CREATE, {"title": "Muu ilma tekstita", "policy_area_other_selected": "on"}
    )
    page = response.content.decode()
    fold = _fold(page, "valdkonnad")

    assert response.status_code == 400
    # The reveal target and the refusal are both on the page.
    assert 'id="valdkond-muu-tekst"' in page
    assert "Kirjuta, millise valdkonnaga on tegemist." in page
    # And neither is inside the fold.
    assert 'id="valdkond-muu-tekst"' not in fold
    assert "Kirjuta, millise valdkonnaga on tegemist." not in fold
    # The checkbox that reveals it stays inside, because it is a choice.
    assert 'id="valdkond-muu"' in fold


# ---------------------------------------------------------------------------
# §2 — what each trigger says when the section is collapsed
# ---------------------------------------------------------------------------


def test_the_valdkonnad_trigger_counts_and_starts_at_nothing(signed_in, specialist):
    """`Valdkonnad`, then `Valdkonnad · 3`. A count, because the names do not fit.

    Redundant while the section is open — the ticked chips are right there — and
    the only thing a reader who collapsed it has left, which is why it survives
    the menu that first needed it (docs/adr/0096 §2).
    """
    form = MatterCreateForm(viewer=specialist)
    assert form.policy_area_chosen_count == 0
    assert "·" not in _trigger(_fold(_page(signed_in), "valdkonnad"))


def test_the_valdkonnad_count_is_what_is_ticked_including_muu(signed_in, specialist):
    """`Muu` counts, because it *is* an answer here.

    It is the affordance that reveals the free-text box, so a trigger reading
    «Valdkonnad» over a ticked `Muu` and a sentence of typed text would be wrong
    about the one state somebody has to come back to.
    """
    areas = list(PolicyArea.objects.filter(is_active=True)[:3])
    payload = {"title": "", "policy_areas": [str(area.pk) for area in areas]}

    form = MatterCreateForm(payload, viewer=specialist)
    assert form.policy_area_chosen_count == 3

    with_muu = MatterCreateForm({**payload, "policy_area_other_selected": "on"}, viewer=specialist)
    assert with_muu.policy_area_chosen_count == 4

    response = signed_in.post(CREATE, payload)
    assert "· 3" in _trigger(_fold(response.content.decode(), "valdkonnad"))


def test_a_forged_area_is_not_counted(signed_in, specialist):
    """The count is over what is *offered*, so a made-up key adds nothing.

    A trigger reading «Valdkonnad · 4» over three ticked chips would be the page
    agreeing with a POST the form is about to refuse.
    """
    areas = list(PolicyArea.objects.filter(is_active=True)[:2])
    form = MatterCreateForm(
        {
            "title": "",
            "policy_areas": [*(str(area.pk) for area in areas), "01a00000-0000-0000-0000-00000000"],
        },
        viewer=specialist,
    )

    assert form.policy_area_chosen_count == 2


def test_the_hetkeseis_trigger_names_the_answer(signed_in, specialist):
    """`Hetkeseis · Riigikogus`. The name, because the field holds one value."""
    stage = factories.StageFactory(label_et="Kooskõlastusringil")

    chosen = MatterCreateForm({"title": "", "stage": str(stage.pk)}, viewer=specialist)
    assert chosen.stage_summary == "Kooskõlastusringil"

    response = signed_in.post(CREATE, {"title": "", "stage": str(stage.pk)})
    assert "· Kooskõlastusringil" in _trigger(_fold(response.content.decode(), "hetkeseis"))


def test_the_hetkeseis_trigger_names_maaramata_rather_than_inventing_one(signed_in, specialist):
    """«Määramata» is a real chip and is what a fresh form arrives holding.

    The trigger reports the option the form itself has selected, whatever that
    is. Saying nothing about it would hide the state most files are in behind a
    word that looks unanswered; saying something *else* would be inventing a
    value the field is allowed not to have (docs/adr/0094 §3).
    """
    factories.StageFactory(label_et="Kooskõlastusringil")

    assert MatterCreateForm(viewer=specialist).stage_summary == "Määramata"
    assert "· Määramata" in _trigger(_fold(_page(signed_in), "hetkeseis"))

    # And nothing was written to the record by the page saying so.
    signed_in.post(CREATE, {"title": "Määramata seis"})
    assert Matter.objects.get(title="Määramata seis").stage is None


def test_nothing_on_the_page_is_a_chipmenu_any_more(signed_in):
    """The retired component, asserted as absent rather than assumed gone.

    `data-chipmenu-single` was what told the script to shut `Hetkeseis` the
    instant a radio was picked, and `.chipmenu__panel` was the class the
    stylesheet took out of flow. Both are withdrawn (docs/adr/0096 §1), and
    neither the stylesheet nor the script defines either any more — so a
    template that kept one would not be *wrong* in a way a rendering test
    notices. It would be an unstyled, unscripted leftover that the next reader
    takes for a live contract, which is how a shut panel comes back.
    """
    factories.StageFactory(label_et="Kooskõlastusringil")
    page = _page(signed_in)

    assert "chipmenu" not in page
    assert page.count('<details class="chipfold" open>') == 2


# ---------------------------------------------------------------------------
# The controls keep their cardinality and their meaning
# ---------------------------------------------------------------------------


def test_valdkonnad_are_still_checkboxes_and_hetkeseis_still_radios(signed_in):
    """A fold is a skin, as the menu before it was: what the controls *are* did
    not move across either round (ADR 0025)."""
    factories.StageFactory(label_et="Kooskõlastusringil")
    page = _page(signed_in)

    valdkonnad = _fold(page, "valdkonnad")
    hetkeseis = _fold(page, "hetkeseis")

    assert 'type="checkbox" name="policy_areas"' in valdkonnad
    assert 'type="radio" name="stage"' in hetkeseis
    # And neither became a select on the way.
    assert "<select" not in valdkonnad
    assert "<select" not in hetkeseis


def test_neither_fold_is_a_native_multiple_listbox(signed_in):
    """Explicitly refused by the brief, and refused before by ADR 0025.

    A `<select multiple>` hides multi-selection behind a modifier key nobody
    uses, is unstyleable, and on a touch device is a platform sheet rather than
    the page. It is also the shape a literal reading of «rippmenüü» could have
    landed on at either end of this argument, so it is refused at both. Scoped
    to the two folds, because `Failid` is legitimately a
    `<input type="file" multiple>` and always was.
    """
    factories.StageFactory(label_et="Kooskõlastusringil")
    page = _page(signed_in)

    for which in FOLDS:
        fold = _fold(page, which)
        assert "<select" not in fold
        assert "multiple" not in fold


def test_each_fold_is_a_named_group_for_a_screen_reader(signed_in):
    """A run of unlabelled checkboxes is a group nobody can answer.

    The legend is visually hidden because the trigger directly above already says
    the word, and printing it twice is what the trigger exists to stop.

    **The `<fieldset>` *is* the fold's body**, rather than sitting inside a
    `<div>` that carries the class. `.chipfold__body` is one margin rule, and a
    wrapper existing only to hold it would put an anonymous box between the
    disclosure and the group it names — so the assertion below is that the
    element carrying the class is the fieldset itself, which is the stronger
    claim and the one that stays true if the rule ever grows.
    """
    factories.StageFactory(label_et="Kooskõlastusringil")
    page = _page(signed_in)

    for which, label in (("valdkonnad", "Valdkonnad"), ("hetkeseis", "Hetkeseis")):
        fold = _fold(page, which)
        opening = fold[fold.index("<fieldset") : fold.index(">", fold.index("<fieldset")) + 1]
        assert "chipfold__body" in opening
        body = fold[fold.index("<fieldset") :]
        legend = re.search(r"<legend[^>]*>([^<]*)</legend>", body)
        assert legend is not None
        assert legend.group(1).strip() == label
        assert "visually-hidden" in body[: body.index("<legend") + 200]


def test_the_stage_explanations_are_still_one_bubble_per_explained_chip(signed_in):
    """Carried through both rounds, and not otherwise touched.

    The description reaches a screen reader through `aria-describedby`, which is
    why it is a sibling of the label rather than inside it — putting it inside
    renamed the radio to the whole paragraph (Uus teema redesign §8). The fold
    also ends the one thing that ever threatened these: a capped, scrolling
    panel clips the bubbles hanging off its own chips, and there is no cap and
    no scroll container anywhere near them now.
    """
    factories.StageFactory(label_et="Kooskõlastusringil", help_text="Kasuta siis, kui…")
    hetkeseis = _fold(_page(signed_in), "hetkeseis")

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
    so this is the whole of what «the values survive» means. The folds come back
    open and carry their answers on their triggers as well; what is asserted
    here is the values.
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

    # Valdkonnad: ticked, and counted on the trigger.
    valdkonnad = _fold(page, "valdkonnad")
    for area in areas:
        chosen = valdkonnad[valdkonnad.index(f'value="{area.pk}"') :]
        assert "checked" in chosen[: chosen.index(">")]
    assert "· 2" in _trigger(valdkonnad)

    # Hetkeseis: one value, named on the trigger.
    hetkeseis = _fold(page, "hetkeseis")
    chosen = hetkeseis[hetkeseis.index(f'value="{stage.pk}"') :]
    assert "checked" in chosen[: chosen.index(">")]
    assert "· Kooskõlastusringil" in _trigger(hetkeseis)

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
