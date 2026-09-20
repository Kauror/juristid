"""`Uus teema`, made manual again — the lawyers' first feedback round.

Three changes, one argument. The creation form is the page a lawyer opens every
day, and it had grown three things that each asked for attention before a
question had been answered: a panel of machine proposals over the fields, a row
of eight institutions under an empty Saatja box, and the whole Valdkond
vocabulary permanently open. None of them is wrong in itself; all three are
wrong on the same screen at the same time (docs/adr/0088).

What this file pins is the *withdrawal*, and in particular its boundaries:

* the reading is switched off, not deleted, and nothing about *files* moved with
  it. `tests/test_intake_staging.py` proves the other half — that the capability
  still works when it is on;
* Saatja starts empty and reaches the same catalogue through the same resolver.
  The rules about what a typed name means are `tests/test_sender_free_entry.py`'s
  and are not restated here;
* Valdkond keeps every value it had, including the two the same round withdrew
  from *new* selection. `tests/test_reference_data_foundation.py` owns the
  vocabulary manifest.

The shape around it has been through three rounds and this file's assertions
have followed each. docs/adr/0088 put the vocabulary behind a shut `<details>`;
docs/adr/0094 §2 replaced that with a menu whose panel was out of flow, because
*opening* the fold re-laid out the form underneath the reader; docs/adr/0096 §1
draws the chips again and opens the section on the server, because a control you
have to open before making an ordinary choice is a click in front of the
question. The withdrawal this file is about is unaffected by all three, so what
changed here is only which markup the assertions read.

What is deliberately not here: `Hetkeseis`, `Menetlusliik`, `Õigusakt` and the
Adressaat/Saatja duplication. Those are later packages and this round inspected
them without moving them.
"""

from __future__ import annotations

import re

import pytest
from django.test import override_settings
from django.urls import reverse

from app.matters.forms import MatterCreateForm, MatterEditForm
from app.matters.models import Matter
from app.organisations.models import Organisation, OrganisationType
from app.taxonomy.models import PolicyArea
from app.workflow.enums import Track
from tests import factories

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")

#: The two labels the feedback round withdrew from new selection.
WITHDRAWN = ["koalitsioonilepped", "eli-oiguse-ulevotmine"]


def scripted(page: str) -> str:
    """The document a scripted browser builds — `<noscript>` is skipped text."""
    return re.sub(r"<noscript>.*?</noscript>", "", page, flags=re.DOTALL)


def valdkond_block(page: str) -> str:
    """The Valdkonnad fold: its trigger and the chips under it.

    Bounded by the `<details>` that carries both, which it has been through all
    three shapes of this control — fold, menu, and fold again
    (docs/adr/0096 §1). The `Muu` free-text box is deliberately *outside* it: a
    box that vanished with the section would be a box somebody could not finish
    (docs/adr/0094 §2.3, kept).

    `rindex` from the summary that names the field, because both folds on the
    page now open with the same tag and `Hetkeseis` is the one below.
    """
    anchor = page.index('data-chipsummary-for="policy_areas"')
    start = page.rindex('<details class="chipfold"', 0, anchor)
    return page[start : page.index("</details>", start)]


def valdkond_trigger(page: str) -> str:
    block = valdkond_block(page)
    return block[: block.index("</summary>")]


def saatja_block(page: str) -> str:
    """The Saatja picker alone.

    Bounded by its own `</fieldset>` rather than by the next picker: Valdkonnad
    sits between the two counterparty fields, and a slice that ran to Adressaat
    would be reading a vocabulary while claiming to read a catalogue.
    """
    start = page.index('id="saatja-valik"')
    return page[start : page.index("</fieldset>", start)]


@pytest.fixture
def ministry():
    return Organisation.objects.create(
        name="Kliimaministeerium", organisation_type=OrganisationType.MINISTRY
    )


# ---------------------------------------------------------------------------
# 1 — the document reading is withdrawn from this page
# ---------------------------------------------------------------------------


def test_the_ordinary_form_offers_no_document_suggestions(signed_in):
    """Nothing on the page proposes, waits for, or reports a reading."""
    page = signed_in.get(CREATE).content.decode()

    assert "Failist leitud" not in page
    assert "data-prefill-for" not in page
    assert "Loen faili…" not in page
    assert "Automaatne lugemine" not in page
    assert "Jätka ilma automaatse lugemiseta" not in page


def test_no_hidden_form_state_about_suggestions_is_posted(signed_in):
    """The half of the withdrawal that a screenshot cannot show.

    `suggestion_state` is the browser's record of which proposals are in use. It
    is invisible by design, so a withdrawal that removed the panel and left this
    behind would look complete and would not be — which is exactly the
    "invisible automatic form mutation behind a hidden UI" the decision forbids.
    """
    page = signed_in.get(CREATE).content.decode()

    assert 'name="suggestion_state"' not in page


def test_the_file_control_is_untouched_by_the_withdrawal(signed_in):
    """Ordinary attachment is a different feature that shared a screen."""
    page = scripted(signed_in.get(CREATE).content.decode())

    assert 'name="files"' in page
    assert "Vali failid" in page
    assert "data-intake-url" in page, "staging still uploads ahead of the save"


@override_settings(MATTER_INTAKE_SUGGESTIONS_ENABLED=True)
def test_the_capability_is_switched_off_rather_than_removed(signed_in):
    """The reinstatement path, proved rather than promised.

    A feature "preserved in code" that nothing exercises is a feature that stops
    working quietly. One setting brings the whole surface back, and this is what
    says so.
    """
    page = signed_in.get(CREATE).content.decode()

    assert 'name="suggestion_state"' in page


def test_the_matter_review_surface_still_reads_its_documents(signed_in, specialist):
    """The withdrawal is about `Uus teema`, and only about `Uus teema`.

    `Muuda teemat` reviews what a *saved* Matter's documents say, on a page
    somebody opened in order to look at the reading. Nothing in the feedback was
    about that surface, so nothing here may switch it off with the other one.
    """
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.get(reverse("matters:matter_edit", kwargs={"pk": matter.pk}))

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# 2 — Saatja is an empty, searchable box
# ---------------------------------------------------------------------------


def test_saatja_starts_empty(signed_in, ministry):
    """No institution is drawn under the box until it is searched or chosen."""
    saatja = saatja_block(scripted(signed_in.get(CREATE).content.decode()))

    chips = re.findall(r"<label class=\"chip\"[^>]*>", saatja)

    assert chips, "the catalogue is still rendered, as controls"
    assert all("hidden" in chip for chip in chips), "…and none of it is on screen"
    assert "Otsi või lisa asutus…" in saatja


def test_a_typed_sender_survives_a_refused_save_and_is_visible(signed_in):
    """A refusal must not cost the answer, and a quiet field must still show it.

    The chip for a body being named for the first time is `checked` and is never
    `hidden` — «an answer is always on screen» is the rule the quiet field is
    built on, and the state that would break it is this one.
    """
    response = signed_in.post(CREATE, {"title": "", "sender_name": "Uus Amet"})

    page = scripted(response.content.decode())
    assert response.status_code == 400
    assert 'value="Uus Amet"' in page
    assert "data-orgfind-provisional" in page


def test_a_chosen_sender_comes_back_visible_after_a_refusal(signed_in, ministry):
    """The same rule for a body chosen from the catalogue rather than typed."""
    response = signed_in.post(CREATE, {"title": "", "source_organisations": [str(ministry.pk)]})

    saatja = saatja_block(scripted(response.content.decode()))
    match = re.search(
        r"<label class=\"chip\"[^>]*>\s*<input[^>]*value=\"" + str(ministry.pk) + r"\"[^>]*>",
        saatja,
    )
    assert match, "the chosen ministry should still be rendered"
    assert "checked" in match.group(0)
    assert "hidden" not in match.group(0)


def test_choosing_a_sender_still_reuses_the_one_catalogue(signed_in, ministry):
    """The quiet field posts what the loud one posted, into the same relation."""
    signed_in.post(
        CREATE,
        {"title": "Pakendiseadus", "source_organisations": [str(ministry.pk)]},
    )

    matter = Matter.objects.get(title="Pakendiseadus")
    assert list(matter.source_organisations.all()) == [ministry]
    assert Organisation.objects.count() == 1, "choosing an existing body creates none"


# ---------------------------------------------------------------------------
# 3 — Valdkond is a compact, multi-valued selector
# ---------------------------------------------------------------------------


def test_valdkond_is_an_open_fold_and_says_so(signed_in):
    """A trigger, open, with the vocabulary drawn under it rather than behind it.

    The inversion of what this test asserted for one round. A shut control put a
    click in front of an ordinary choice and a floating panel stood over the two
    answers below it; the section is drawn on arrival now and may be collapsed
    by the person who wants the room (docs/adr/0096 §1).
    """
    page = signed_in.get(CREATE).content.decode()
    block = valdkond_block(page)

    assert "chipfold" in block
    assert block.startswith('<details class="chipfold" open>')
    assert "Valdkonnad" in block
    # In flow under the trigger, which is what `e2e/test_uus_teema_valikud.py`
    # measures — a class name is not a visible chip.
    assert "chipfold__body" in block
    # And the component it replaced is not still here under another name.
    assert "chipmenu" not in page


def test_the_whole_vocabulary_is_still_offered_under_the_trigger(signed_in):
    """Drawn, not shortened. Nothing was dropped at either end of the argument."""
    block = valdkond_block(signed_in.get(CREATE).content.decode())

    for area in PolicyArea.objects.filter(is_active=True):
        assert f'value="{area.pk}"' in block, area.name_et
    assert 'name="policy_area_other_selected"' in block


def test_several_areas_can_still_be_chosen(signed_in):
    """`Matter.policy_areas` holds several and the control still says so."""
    areas = list(PolicyArea.objects.filter(is_active=True)[:2])

    signed_in.post(
        CREATE,
        {"title": "Kaks valdkonda", "policy_areas": [str(area.pk) for area in areas]},
    )

    matter = Matter.objects.get(title="Kaks valdkonda")
    assert set(matter.policy_areas.all()) == set(areas)


def test_a_refused_save_says_how_many_areas_it_holds(signed_in):
    """The trigger's count, which is what makes the section safe to collapse.

    Redundant while the chips are on the screen. It earns its place for the
    reader who has collapsed the section: a fold that said nothing about its
    answer would have to be reopened every time to find out whether it had one.
    A count rather than the names, because the trigger is a pill on one line and
    three Estonian policy areas do not fit on it (docs/adr/0094 §2.2, kept).
    """
    areas = list(PolicyArea.objects.filter(is_active=True)[:2])

    response = signed_in.post(
        CREATE, {"title": "", "policy_areas": [str(area.pk) for area in areas]}
    )
    page = response.content.decode()

    assert response.status_code == 400
    trigger = valdkond_trigger(page)
    assert "· 2" in trigger
    # And the answers themselves are still ticked inside, so nothing was lost.
    for area in areas:
        chip = valdkond_block(page)
        chosen = chip[chip.index(f'value="{area.pk}"') :]
        assert "checked" in chosen[: chosen.index(">")]


def test_the_muu_refusal_is_readable_even_if_the_fold_is_collapsed(signed_in):
    """A message nobody can see is a form that refused for no stated reason.

    The original fold rendered itself open for this, because the box and its
    refusal were inside it. Both are outside the section — which is what makes
    the refusal survive a reader who collapsed it, and the reason that placement
    outlived the menu it was written for (docs/adr/0094 §2.3, kept).
    """
    response = signed_in.post(
        CREATE, {"title": "Muu ilma tekstita", "policy_area_other_selected": "on"}
    )
    page = response.content.decode()

    assert response.status_code == 400
    assert "Kirjuta, millise valdkonnaga on tegemist." in page
    # Outside the fold.
    assert "Kirjuta, millise valdkonnaga on tegemist." not in valdkond_block(page)
    # The box somebody has to fill in is on the page and not inside the fold.
    assert 'id="valdkond-muu-tekst"' in page
    assert 'id="valdkond-muu-tekst"' not in valdkond_block(page)


def test_the_two_withdrawn_areas_are_not_offered(signed_in, specialist):
    """Withdrawn from *new* selection, by `is_active` and nothing else."""
    offered = {
        str(label)
        for _value, label in MatterCreateForm(viewer=specialist).fields["policy_areas"].choices
    }

    assert "Koalitsioonilepped" not in offered
    assert "ELi õiguse ülevõtmine" not in offered
    assert PolicyArea.objects.filter(key__in=WITHDRAWN).count() == 2


def test_the_withdrawn_area_is_absent_from_both_teema_forms(signed_in, specialist):
    """The withdrawn Valdkond is offered by neither page, and `Track` still holds it.

    This test used to be called «Menetlusliik still offers the words the area
    gave up», and it asserted «ELi õiguse ülevõtmine» *on* `Muuda teemat`: the
    four words were not gone from the product, they were `Menetlusliik`'s
    answer and always had been, and docs/adr/0088 §3 removed only the second
    place the same question was asked (docs/adr/0090 §4 then took
    `Menetlusliik` off the create form, leaving the edit page the last surface
    that offered it).

    docs/adr/0096 §5 takes it off that page too, and off the Teema rail with
    it — a question asked on one of two pages that are supposed to be one job
    seen twice is drift rather than a decision. So the old assertion is not
    merely stale, it asserts the defect.

    What survives unchanged is what this test was always *for*: the retired
    area is offered by neither Teema form. And what replaces the other half is
    the claim docs/adr/0096 §5 actually makes — `Track` is withdrawn from the
    ordinary interface, not from the product. The enum still carries the words,
    the column still stores them, and the importers still write them; see
    `test_teema_ux_consolidation.py` for the Matters that keep a stored track
    through a save of the simplified form.
    """
    matter = factories.MatterFactory(owner=specialist)
    create = signed_in.get(CREATE).content.decode()
    edit = signed_in.get(reverse("matters:matter_edit", kwargs={"pk": matter.pk})).content.decode()

    assert "ELi õiguse ülevõtmine" not in valdkond_block(create)
    assert "ELi õiguse ülevõtmine" not in create
    assert "ELi õiguse ülevõtmine" not in edit
    # Not gone from the product — gone from the two forms. The vocabulary that
    # holds the words is untouched and is what the register still reads.
    assert "ELi õiguse ülevõtmine" in dict(Track.choices).values()


@pytest.mark.parametrize("key", WITHDRAWN)
def test_a_matter_filed_under_a_withdrawn_area_keeps_it_through_an_edit(signed_in, specialist, key):
    """The failure the whole retirement arrangement exists to prevent.

    A form whose queryset held only today's nineteen would refuse — or worse,
    silently discard — a save that merely left a retired area ticked.
    """
    area = PolicyArea.objects.get(key=key)
    matter = factories.MatterFactory(owner=specialist, title="Vana teema")
    matter.policy_areas.add(area)

    # The control offers it back rather than pretending it is not there…
    offered = [
        str(label) for _value, label in MatterEditForm(matter=matter).fields["policy_areas"].choices
    ]
    assert area.name_et in offered

    # …and a correction to another field validates with it still ticked.
    form = MatterEditForm(
        {"title": "Vana teema, parandatud", "policy_areas": [str(area.pk)]},
        matter=matter,
    )
    assert form.is_valid(), form.errors
    assert list(form.cleaned_data["policy_areas"]) == [area]


@pytest.mark.parametrize("key", WITHDRAWN)
def test_a_withdrawn_area_keeps_its_matters_in_search(signed_in, specialist, key):
    """Retirement is not an index change, because no relation moved.

    `is_active` decides what is *offered*; the projection is built from the
    relations, which this round did not touch — so no reindex, no search
    version bump, and a Matter filed under a withdrawn label is still findable
    by that label (app/search/indexing.py).
    """
    from app.search.indexing import rebuild_all
    from app.search.models import SearchDocument, SearchSourceKind

    area = PolicyArea.objects.get(key=key)
    matter = factories.MatterFactory(owner=specialist)
    matter.policy_areas.add(area)
    rebuild_all()

    # The names column, which is where a Valdkond lands (`alias_text`, weight C).
    row = SearchDocument.objects.get(matter=matter, source_kind=SearchSourceKind.MATTER)
    assert area.name_et in row.alias_text


# ---------------------------------------------------------------------------
# Regression — the page still files a Teema
# ---------------------------------------------------------------------------


def test_a_title_alone_still_creates_a_matter(signed_in):
    """Only the title is required, and the three changes did not change that."""
    response = signed_in.post(CREATE, {"title": "Ainult pealkiri"})

    assert response.status_code == 302
    matter = Matter.objects.get(title="Ainult pealkiri")
    assert list(matter.policy_areas.all()) == []
    assert list(matter.source_organisations.all()) == []
