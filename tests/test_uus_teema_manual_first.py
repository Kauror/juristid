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
* Valdkond stops standing permanently open and keeps every value it had,
  including the two the same round withdrew from *new* selection.
  `tests/test_reference_data_foundation.py` owns the vocabulary manifest.

The fold itself did not survive. docs/adr/0088 put the vocabulary behind a
`<details>`, and docs/adr/0094 §2 replaced that with a menu whose panel is out of
flow — the lawyers' complaint about the fold was not that it hid the vocabulary
but that *opening* it re-laid out the form underneath them. The withdrawal this
file is about is unaffected either way, so what changed here is which markup the
assertions read.

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
from tests import factories

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")

#: The two labels the feedback round withdrew from new selection.
WITHDRAWN = ["koalitsioonilepped", "eli-oiguse-ulevotmine"]


def scripted(page: str) -> str:
    """The document a scripted browser builds — `<noscript>` is skipped text."""
    return re.sub(r"<noscript>.*?</noscript>", "", page, flags=re.DOTALL)


def valdkond_block(page: str) -> str:
    """The Valdkonnad fieldset: its legend, its chips and the `Muu` box.

    Bounded by the `<fieldset>` that carries all three. It was a `<details>`
    fold (docs/adr/0088 §3) and then a `chipmenu` whose panel overlaid the form
    (docs/adr/0094 §2); the owner's live audit put the chips back on the page,
    and the free-text box back inside the group it belongs to, because there is
    no longer a panel for it to vanish into (docs/adr/0096 §2).
    """
    at = page.index('data-chipcount-for="policy_areas"')
    start = page.rindex("<fieldset", 0, at)
    return page[start : page.index("</fieldset>", at)]


def valdkond_chips(page: str) -> str:
    """The chip row alone, without the `Muu` box beneath it."""
    block = valdkond_block(page)
    return block[: block.index("</div>", block.index('<div class="chiprow"'))]


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


def test_valdkond_is_drawn_at_rest_and_names_itself(signed_in):
    """A named group of chips, on the page, with nothing to open.

    The third shape this row has had, and the one the lawyers reviewing the
    live page asked for back: a classification you can read without opening
    anything is what `Õigusakt` beneath it always was (docs/adr/0096 §2).
    """
    page = signed_in.get(CREATE).content.decode()
    block = valdkond_block(page)

    assert "chipmenu" not in block
    assert "<details" not in block
    assert "<summary" not in block
    assert "Valdkonnad" in block
    # The legend is visible again: it was `visually-hidden` only because the
    # menu's trigger directly above already said the word.
    assert "visually-hidden" not in block[: block.index("</legend>")]


def test_the_whole_vocabulary_is_offered_on_the_page(signed_in):
    """Every active area, plus `Muu`. Nothing was dropped to make it compact."""
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


def test_a_refused_save_gives_every_ticked_area_back(signed_in):
    """Nothing to reopen, so the whole claim is that the ticks survive.

    The trigger's count was what made the menu affordable, and the count is
    still rendered — beside the legend, by `bindChipCounts`, the way `Õigusakt`
    and `Sildid` have always carried theirs. What the server has to get right is
    narrower and more important: every box that arrived ticked comes back
    ticked (docs/adr/0096 §2).
    """
    areas = list(PolicyArea.objects.filter(is_active=True)[:2])

    response = signed_in.post(
        CREATE, {"title": "", "policy_areas": [str(area.pk) for area in areas]}
    )
    page = response.content.decode()

    assert response.status_code == 400
    block = valdkond_block(page)
    assert 'data-chipcount-for="policy_areas"' in block
    for area in areas:
        chosen = block[block.index(f'value="{area.pk}"') :]
        assert "checked" in chosen[: chosen.index(">")]


def test_the_muu_refusal_is_readable_with_no_scripting_at_all(signed_in):
    """A message nobody can see is a form that refused for no stated reason.

    The fold rendered itself open for this and the menu kept the box outside
    the panel; drawn at rest the box simply sits in the group it belongs to,
    and the server renders it **open** because the chip arrived ticked. Nothing
    has to be opened and nothing has to run (docs/adr/0096 §2).
    """
    response = signed_in.post(
        CREATE, {"title": "Muu ilma tekstita", "policy_area_other_selected": "on"}
    )
    page = response.content.decode()

    assert response.status_code == 400
    assert "Kirjuta, millise valdkonnaga on tegemist." in page
    # Inside the group, and visible: the chip that reveals it arrived ticked.
    block = valdkond_block(page)
    assert 'id="valdkond-muu-tekst"' in block
    revealed = block[block.index('id="valdkond-muu-tekst"') :]
    assert "hidden" not in revealed[: revealed.index(">")]
    assert "Kirjuta, millise valdkonnaga on tegemist." in block


def test_the_two_withdrawn_areas_are_not_offered(signed_in, specialist):
    """Withdrawn from *new* selection, by `is_active` and nothing else."""
    offered = {
        str(label)
        for _value, label in MatterCreateForm(viewer=specialist).fields["policy_areas"].choices
    }

    assert "Koalitsioonilepped" not in offered
    assert "ELi õiguse ülevõtmine" not in offered
    assert PolicyArea.objects.filter(key__in=WITHDRAWN).count() == 2


def test_menetlusliik_still_offers_the_words_the_area_gave_up(signed_in, specialist):
    """The reason the area went, stated as an assertion.

    «ELi õiguse ülevõtmine» is not gone from the product: it is `Menetlusliik`'s
    answer and always was. The withdrawal removed the *second* place the same
    four words were asked for (docs/adr/0088 §3).

    Asserted on `Muuda teemat` rather than here, because the round after this
    one took `Menetlusliik` off the create form as well — derived from `Õigusakt`
    instead, and never as a transposition (docs/adr/0090 §4). The withdrawn
    Valdkond is still absent from both, which is what this test is for.
    """
    matter = factories.MatterFactory(owner=specialist)
    create = signed_in.get(CREATE).content.decode()
    edit = signed_in.get(reverse("matters:matter_edit", kwargs={"pk": matter.pk})).content.decode()

    assert "ELi õiguse ülevõtmine" in edit
    assert "ELi õiguse ülevõtmine" not in valdkond_block(create)
    assert "ELi õiguse ülevõtmine" not in create


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
