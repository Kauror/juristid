"""`Uus teema` answers with menus, and asks for one date (docs/adr/0094).

Four corrections from one round of lawyer feedback. This file owns the half a
browser cannot state — what the server renders, what it binds and what it writes
— and `e2e/test_uus_teema_menus.py` owns the half only a browser can: that
opening a menu overlays the form instead of pushing it down, that Escape and a
click outside close it, and that a multi-select menu survives being answered.

The two halves are deliberately separate and deliberately *both*. A class name in
the markup is not an overlay, and a measured overlay says nothing about what the
POST stores.

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


def _menu(page: str, *, single: bool) -> str:
    """One `<details class="chipmenu">`, from its opening tag to its close."""
    needle = (
        '<details class="chipmenu" data-chipmenu data-chipmenu-single>'
        if single
        else '<details class="chipmenu" data-chipmenu>'
    )
    start = page.index(needle)
    return page[start : page.index("</details>", start)]


def _trigger(menu: str) -> str:
    return menu[menu.index("<summary") : menu.index("</summary>")]


def _rendered_input(page: str, field_id: str) -> str:
    match = re.search(rf'<input[^>]*id="{field_id}"[^>]*>', page)
    assert match is not None, field_id
    return match.group(0)


# ---------------------------------------------------------------------------
# §2 — both vocabularies are menus, and neither is a fold
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("single", [False, True], ids=["valdkonnad", "hetkeseis"])
def test_each_vocabulary_is_a_menu_with_a_trigger_and_a_panel(signed_in, single):
    """The markup contract, which is what makes the overlay possible.

    Not the overlay itself — that is CSS and is measured in the browser. What is
    asserted here is that the panel is a separate element from the trigger and
    carries the class the stylesheet positions, because a panel rendered inside
    the summary could never be taken out of flow however the rule is written.
    """
    factories.StageFactory(label_et="Kooskõlastusringil")
    menu = _menu(_page(signed_in), single=single)

    assert '<summary class="chipmenu__trigger">' in menu
    assert "chipmenu__panel" in menu
    # Shut on arrival, every time.
    assert " open" not in _trigger(menu)


@pytest.mark.parametrize("single", [False, True], ids=["valdkonnad", "hetkeseis"])
def test_a_menu_is_shut_on_a_refused_render_too(signed_in, single):
    """Nothing a reader has to see is inside a panel, so nothing opens one.

    This is the rule that replaced `policy_area_disclosure_open`. The fold had
    to render itself open on a refusal because the box being refused was inside
    it; both refusals and the `Muu` box are outside the menu now, so a refused
    save comes back with the menu shut and the trigger carrying the answer
    (docs/adr/0094 §2.3).
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
    menu = _menu(response.content.decode(), single=single)
    assert " open" not in _trigger(menu)


def test_the_muu_box_and_both_refusals_are_outside_the_valdkonnad_panel(signed_in):
    """A box that vanishes when the menu shuts is a box nobody can finish."""
    response = signed_in.post(
        CREATE, {"title": "Muu ilma tekstita", "policy_area_other_selected": "on"}
    )
    page = response.content.decode()
    menu = _menu(page, single=False)

    assert response.status_code == 400
    # The reveal target and the refusal are both on the page.
    assert 'id="valdkond-muu-tekst"' in page
    assert "Kirjuta, millise valdkonnaga on tegemist." in page
    # And neither is inside the menu.
    assert 'id="valdkond-muu-tekst"' not in menu
    assert "Kirjuta, millise valdkonnaga on tegemist." not in menu
    # The checkbox that reveals it stays inside, because it is a choice.
    assert 'id="valdkond-muu"' in menu


# ---------------------------------------------------------------------------
# §2.2 — what each trigger says, and why they say it differently
# ---------------------------------------------------------------------------


def test_the_valdkonnad_trigger_counts_and_starts_at_nothing(signed_in, specialist):
    """`Valdkonnad`, then `Valdkonnad · 3`. A count, because the names do not fit."""
    form = MatterCreateForm(viewer=specialist)
    assert form.policy_area_chosen_count == 0
    assert "·" not in _trigger(_menu(_page(signed_in), single=False))


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
    assert "· 3" in _trigger(_menu(response.content.decode(), single=False))


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
    assert "· Kooskõlastusringil" in _trigger(_menu(response.content.decode(), single=True))


def test_the_hetkeseis_trigger_names_maaramata_rather_than_inventing_one(signed_in, specialist):
    """«Määramata» is a real chip and is what a fresh form arrives holding.

    The trigger reports the option the form itself has selected, whatever that
    is. Saying nothing about it would hide the state most files are in behind a
    word that looks unanswered; saying something *else* would be inventing a
    value the field is allowed not to have (docs/adr/0094 §3).
    """
    factories.StageFactory(label_et="Kooskõlastusringil")

    assert MatterCreateForm(viewer=specialist).stage_summary == "Määramata"
    assert "· Määramata" in _trigger(_menu(_page(signed_in), single=True))

    # And nothing was written to the record by the page saying so.
    signed_in.post(CREATE, {"title": "Määramata seis"})
    assert Matter.objects.get(title="Määramata seis").stage is None


def test_the_single_select_menu_is_marked_and_the_multi_select_is_not(signed_in):
    """The attribute that closes a menu once it is answered, and its absence.

    `Valdkonnad` must stay open across several ticks, so it deliberately does not
    carry it. Asserted here rather than only in the browser because the flag is
    what the browser behaviour is *read from* — a template that stopped writing
    it would make the e2e assertion vacuous rather than red.
    """
    factories.StageFactory(label_et="Kooskõlastusringil")
    page = _page(signed_in)

    assert page.count("data-chipmenu-single") == 1
    assert "data-chipmenu-single" in _menu(page, single=True)
    assert "data-chipmenu-single" not in _menu(page, single=False)


# ---------------------------------------------------------------------------
# The controls keep their cardinality and their meaning
# ---------------------------------------------------------------------------


def test_valdkonnad_are_still_checkboxes_and_hetkeseis_still_radios(signed_in):
    """A menu is a skin. What the controls *are* did not move (ADR 0025)."""
    factories.StageFactory(label_et="Kooskõlastusringil")
    page = _page(signed_in)

    valdkonnad = _menu(page, single=False)
    hetkeseis = _menu(page, single=True)

    assert 'type="checkbox" name="policy_areas"' in valdkonnad
    assert 'type="radio" name="stage"' in hetkeseis
    # And neither became a select on the way.
    assert "<select" not in valdkonnad
    assert "<select" not in hetkeseis


def test_neither_menu_is_a_native_multiple_listbox(signed_in):
    """Explicitly refused by the brief, and refused before by ADR 0025.

    A `<select multiple>` hides multi-selection behind a modifier key nobody
    uses, is unstyleable, and on a touch device is a platform sheet rather than
    the page. Scoped to the two menus, because `Failid` is legitimately a
    `<input type="file" multiple>` and always was.
    """
    factories.StageFactory(label_et="Kooskõlastusringil")
    page = _page(signed_in)

    for single in (False, True):
        menu = _menu(page, single=single)
        assert "<select" not in menu
        assert "multiple" not in menu


def test_each_menu_is_a_named_group_for_a_screen_reader(signed_in):
    """A panel of unlabelled checkboxes is a group nobody can answer.

    The legend is visually hidden because the trigger directly above already says
    the word, and printing it twice is what the menu exists to stop.
    """
    factories.StageFactory(label_et="Kooskõlastusringil")
    page = _page(signed_in)

    for single, label in ((False, "Valdkonnad"), (True, "Hetkeseis")):
        panel = _menu(page, single=single)
        panel = panel[panel.index("chipmenu__panel") :]
        assert "<fieldset" in panel
        legend = re.search(r"<legend[^>]*>([^<]*)</legend>", panel)
        assert legend is not None
        assert legend.group(1).strip() == label
        assert "visually-hidden" in panel[: panel.index("<legend") + 200]


def test_the_stage_explanations_are_still_one_bubble_per_explained_chip(signed_in):
    """Moved into the panel, and not otherwise touched.

    The description reaches a screen reader through `aria-describedby`, which is
    why it is a sibling of the label rather than inside it — putting it inside
    renamed the radio to the whole paragraph (Uus teema redesign §8).
    """
    factories.StageFactory(label_et="Kooskõlastusringil", help_text="Kasuta siis, kui…")
    hetkeseis = _menu(_page(signed_in), single=True)

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
    so this is the whole of what «the values survive» means. The menus need not
    come back open — they carry their answers on their triggers — but every
    value has to still be there.
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
    valdkonnad = _menu(page, single=False)
    for area in areas:
        chosen = valdkonnad[valdkonnad.index(f'value="{area.pk}"') :]
        assert "checked" in chosen[: chosen.index(">")]
    assert "· 2" in _trigger(valdkonnad)

    # Hetkeseis: one value, named on the trigger.
    hetkeseis = _menu(page, single=True)
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
