"""`Uus teema`: one control for «which institution?», proved on the server.

The interaction this replaces asked the same question three ways. A row of quick
chips, a `<details>` reading «Vali nimekirjast (N)» with a search box inside it,
and — somewhere else again — a text field called «Uus saatja» for a body the
catalogue did not hold. Three paths, and the person had to decide which of them
they were on before typing a letter. Adressaat had the same three, nested one
disclosure deeper (docs/adr/0073).

There is one path now, and the sentence is the whole product decision:

    Otsi kõigepealt olemasolevat. Kui seda ei ole, lisa sama välja kaudu uus.

**What this file is for, and what it deliberately is not.** Everything here is a
GET or a POST. Not one assertion depends on a script having run, because the
things that are easy to get wrong in a rewrite like this one are not the
animations — they are whether the control still *posts* what it used to, whether
a refused save comes back holding the answer, whether the catalogue is one
catalogue, and whether anything at all can create an `Organisation` before
`Loo teema` says so. `e2e/test_unified_organisation_picker.py` owns the half a
browser has to answer.

**The boundary that must survive this redesign.** Typing is not creating. The
search box has no `name` and posts nothing; `+` writes the typed spelling into
`sender_name` / `addressee_name`, which are the fields that have always carried
a body the catalogue does not hold; and what that spelling *means* is still
`app.organisations.services.resolve_organisation_name` inside the save's own
transaction — reuse an exact or alias match, create only a genuinely new body,
refuse a spelling that already names two. Those rules are pinned in
`tests/test_sender_free_entry.py` and are not restated here; what is pinned here
is that the new control still reaches them (task §9, §10, §21, §25).
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from app.organisations.models import Organisation, OrganisationType
from tests import factories

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")

#: The exact words on the box. Exact, because the placeholder *is* the
#: instruction — «otsi **või lisa**» is what tells somebody the same field
#: answers both halves of the question (task §4).
PLACEHOLDER = "Otsi või lisa asutus…"


def scripted(page: str) -> str:
    """The document an ordinary browser builds, without the `<noscript>` half.

    A browser that runs scripts does not parse `<noscript>` content into
    anything — it is text the parser skips — so a claim about what somebody sees
    has to be made against the response with those blocks removed. Without this,
    the fallback's «Vali nimekirjast» satisfies a test written to prove it gone,
    which is the one way this file could be comprehensively wrong while green.
    """
    return re.sub(r"<noscript>.*?</noscript>", "", page, flags=re.DOTALL)


def chip_tags(page: str, name: str) -> dict[str, str]:
    """Every control in one group, by value, as its own opening tag."""
    found = {}
    for tag in re.findall(r"<input[^>]*>", page):
        if f'name="{name}"' not in tag:
            continue
        match = re.search(r'value="([^"]*)"', tag)
        if match:
            found[match.group(1)] = tag
    return found


def chip_label(page: str, name: str, value: str) -> str:
    """The `<label>` wrapping one control, so its `hidden` can be read.

    `hidden` sits on the label rather than on the input: the chip is what is
    shown or not shown, and the input inside it covers the chip at zero opacity
    (static/css/app.css `.chip__input`).
    """
    pattern = re.compile(
        r"<label[^>]*>\s*<input[^>]*name=\""
        + re.escape(name)
        + r"\"[^>]*value=\""
        + re.escape(value)
        + r"\"[^>]*>",
        re.DOTALL,
    )
    match = pattern.search(page)
    assert match, f"{name}={value} is not rendered as a chip at all"
    return match.group(0)[: match.group(0).index("<input")]


@pytest.fixture
def ministry():
    return Organisation.objects.create(
        name="Kliimaministeerium", organisation_type=OrganisationType.MINISTRY
    )


@pytest.fixture
def crowded(specialist):
    """A catalogue the shortlist cannot hold, so there is a genuine tail.

    Twelve bodies against a shortlist of eight. Named so that the alphabet
    decides which four fall out of it — `organisations_by_usage` fills the eight
    from the catalogue in name order when there is no sender history — which
    makes «outside the quick row» a fact this fixture establishes rather than one
    the test has to hope for.
    """
    return [Organisation.objects.create(name=f"Asutus {index:02d}") for index in range(12)]


# ---------------------------------------------------------------------------
# One control, and it is a search box
# ---------------------------------------------------------------------------


def test_both_fields_offer_the_same_search_and_add_control(signed_in, crowded):
    """Saatja and Adressaat are two questions about one catalogue (task §13)."""
    page = scripted(signed_in.get(CREATE).content.decode())

    assert page.count(PLACEHOLDER) == 2
    assert "Lisa uus saatja" in page
    assert "Lisa uus adressaat" in page


def test_the_add_button_arrives_disabled(signed_in, crowded):
    """Nothing to add from an empty box, and the button says so honestly.

    A real `disabled`, never `aria-disabled` on a working control: a screen
    reader repeats that claim and a browser driver refuses to click it, which is
    a defect this application has already paid for once (Uus teema redesign §8,
    task §4).
    """
    page = scripted(signed_in.get(CREATE).content.decode())

    buttons = re.findall(r"<button[^>]*data-orgfind-add[^>]*>", page)

    assert len(buttons) == 2
    assert all("disabled" in button for button in buttons)
    assert not any("aria-disabled" in button for button in buttons)


def test_the_search_box_posts_nothing(signed_in, crowded):
    """The whole of «typing is not creating», in one assertion.

    A box that posted would turn a half-typed «Kliima» — left behind after
    somebody chose `Kliimaministeerium` from the list it filtered — into an
    institution called «Kliima» (task §9, §25).
    """
    page = scripted(signed_in.get(CREATE).content.decode())

    boxes = [tag for tag in re.findall(r"<input[^>]*>", page) if "data-orgfind-input" in tag]

    assert len(boxes) == 2
    for tag in boxes:
        assert 'type="search"' in tag
        assert "name=" not in tag, tag


def test_the_typed_field_still_posts_behind_the_button(signed_in, crowded):
    """`+` needs somewhere to write, and it is the field that always carried this.

    Hidden rather than removed: the distinction between *finding* a body and
    *naming* one survives in the posted data, which is what lets the server keep
    deciding what a name means (task §9, §21).
    """
    page = scripted(signed_in.get(CREATE).content.decode())

    for name in ("sender_name", "addressee_name"):
        tags = [tag for tag in re.findall(r"<input[^>]*>", page) if f'name="{name}"' in tag]
        assert len(tags) == 1, f"{name} should post exactly once with scripting on"
        assert 'type="hidden"' in tags[0]


def test_the_fallback_box_is_written_after_the_carrier(signed_in, crowded):
    """Document order, because with scripting off both of them post.

    Django reads the *last* value a request carries for a text field, so the box
    somebody actually typed into has to be the second one. Written down as a
    test because it is invisible in either rendering: with scripting on the
    fallback is not markup, and with scripting off the carrier is empty — so the
    only way this can go wrong is silently, by somebody moving a block
    (templates/matters/partials/organisation_picker.html, task §22).
    """
    page = signed_in.get(CREATE).content.decode()

    for name in ("sender_name", "addressee_name"):
        tags = [tag for tag in re.findall(r"<input[^>]*>", page) if f'name="{name}"' in tag]
        assert len(tags) == 2, f"{name} should have a carrier and a fallback"
        assert 'type="hidden"' in tags[0], "the carrier is not first"
        assert 'type="hidden"' not in tags[1], "the fallback box is not second"


# ---------------------------------------------------------------------------
# The catalogue is on the page, and only the shortlist is on screen
# ---------------------------------------------------------------------------


def test_every_institution_is_a_real_control_in_both_fields(signed_in, crowded, ministry):
    """What makes the search select a row rather than describe one.

    The search finds a chip that is already in the document and ticks it, so
    choosing an existing body reaches the server as an identifier and can never
    become a second institution spelled the same way (task §7, §24).
    """
    page = scripted(signed_in.get(CREATE).content.decode())
    everything = {str(pk) for pk in Organisation.objects.values_list("pk", flat=True)}

    senders = set(chip_tags(page, "source_organisations")) | set(
        chip_tags(page, "source_organisations_other")
    )
    addressees = set(chip_tags(page, "addressee_organisation")) - {""}

    assert senders == everything
    assert addressees == everything


def test_the_bodies_outside_the_shortlist_arrive_out_of_sight(signed_in, crowded, ministry):
    """One or two rows of quick choices, not the whole catalogue (task §5, §24)."""
    page = scripted(signed_in.get(CREATE).content.decode())
    visible = [
        value
        for value in chip_tags(page, "source_organisations")
        if "hidden" not in chip_label(page, "source_organisations", value)
    ]
    hidden = [
        value
        for value in chip_tags(page, "source_organisations_other")
        if "hidden" in chip_label(page, "source_organisations_other", value)
    ]

    assert len(visible) == 8, "the quick row should hold the shortlist and nothing else"
    assert len(hidden) == Organisation.objects.count() - 8


def test_a_recorded_alias_reaches_the_control_it_belongs_to(signed_in, ministry):
    """«MKM» has to find the ministry, and the alias is not in the label.

    Normalised by `OrganisationAlias.save`, handed to the browser as it is
    stored, so nothing on the page re-implements `normalize_for_matching`
    (task §6, §21).
    """
    ministry.aliases.create(alias="KLIM")
    page = scripted(signed_in.get(CREATE).content.decode())

    tag = chip_tags(page, "addressee_organisation")[str(ministry.pk)]

    assert 'data-aliases="klim"' in tag


def test_reading_the_page_creates_no_institution(signed_in, crowded):
    """Search is not creation, at the one moment it would be cheapest to be wrong.

    Opening the form renders the whole catalogue and every alias in it; a
    rendering that touched the catalogue would be a page that grew it
    (task §25).
    """
    before = Organisation.objects.count()

    signed_in.get(CREATE)

    assert Organisation.objects.count() == before


# ---------------------------------------------------------------------------
# An answer is visible, whatever put it there
# ---------------------------------------------------------------------------


def test_a_refused_save_brings_a_non_shortlist_sender_back_in_sight(signed_in, crowded):
    """Task §8, on the path that matters most: the form somebody just lost.

    A body chosen through the search is normally one the quick row does not
    hold, so the naive rendering — shortlist visible, everything else hidden —
    comes back with the answer in the document and off the screen. That reads
    exactly like a form that discarded it.
    """
    outside = Organisation.objects.order_by("name").last()
    assert outside is not None

    # Under the name the control in the document actually carries. Saatja is one
    # logical set split across two fields, because a checkbox group cannot be
    # rendered in two places without becoming two — and the browser ticks the
    # control it finds rather than choosing a field name (app/matters/forms.py).
    response = signed_in.post(
        CREATE, {"title": "", "source_organisations_other": [str(outside.pk)]}
    )
    page = scripted(response.content.decode())

    label = chip_label(page, "source_organisations_other", str(outside.pk))

    assert "checked" in chip_tags(page, "source_organisations_other")[str(outside.pk)]
    assert "hidden" not in label, "the chosen sender came back out of sight"


def test_a_refused_save_brings_a_non_shortlist_addressee_back_in_sight(signed_in, crowded):
    """The same claim for the field that holds one value.

    Adressaat's own shortlist is `addressees_by_usage`, and a body chosen
    through the search is no more likely to be in it.
    """
    outside = Organisation.objects.order_by("name").last()
    assert outside is not None

    response = signed_in.post(
        CREATE,
        {
            "title": "",
            "addressee_organisation": str(outside.pk),
            "addressee_is_manual": "1",
        },
    )
    page = scripted(response.content.decode())
    tag = chip_tags(page, "addressee_organisation")[str(outside.pk)]

    assert "checked" in tag
    assert "hidden" not in chip_label(page, "addressee_organisation", str(outside.pk))


def test_a_refused_save_keeps_the_typed_name_and_shows_it_as_a_chip(signed_in):
    """Task §11: a refusal returns the control holding what was typed.

    Both halves matter. The value has to come back, or the person retypes an
    institution name they already gave; and it has to come back *visible*, or
    they retype it because nothing on the page says it is still there.
    """
    response = signed_in.post(CREATE, {"title": "", "sender_name": "Euroopa Näidiskomisjon"})
    page = scripted(response.content.decode())

    carrier = [tag for tag in re.findall(r"<input[^>]*>", page) if 'name="sender_name"' in tag]

    assert 'value="Euroopa Näidiskomisjon"' in carrier[0]
    assert "data-orgfind-provisional" in page
    assert "chip--provisional" in page


def test_an_ambiguous_typed_name_comes_back_with_the_refusal_and_the_value(signed_in):
    """Fail closed, and say so where the person can act on it (task §11).

    Two rows under one spelling is a question for a person, so the save is
    refused rather than guessed and rather than made permanent by a third row.
    What this pins is the *return journey*: the message is on the page and the
    spelling is still in the control, so «vali nimekirjast» is an instruction
    somebody can follow rather than an invitation to type it all again.
    """
    for _ in range(2):
        Organisation.objects.create(name="Näidisamet", organisation_type=OrganisationType.OTHER)
    before = Organisation.objects.count()

    response = signed_in.post(
        CREATE,
        {
            "title": "Kahtlane",
            "sender_name": "Näidisamet",
            "owner": "",
            "stage": "",
        },
    )
    page = scripted(response.content.decode())

    assert response.status_code == 400, "an ambiguous name should refuse the save"
    assert Organisation.objects.count() == before, "an ambiguous name created a third row"
    assert "sobib mitme organisatsiooniga" in page
    carrier = [tag for tag in re.findall(r"<input[^>]*>", page) if 'name="sender_name"' in tag]
    assert 'value="Näidisamet"' in carrier[0]


def test_a_reader_chosen_sender_outside_the_shortlist_is_offered_as_a_control(
    signed_in, crowded, specialist
):
    """Task §16, in the half a server can answer.

    The intake reader hands the browser an `Organisation` primary key and the
    browser ticks the control that carries it. That only works if the control
    exists, so the reader must not depend on the ministry it inferred happening
    to be one of the eight in the quick row — which is precisely the case the
    old rendering could not serve, because everything outside the shortlist was
    behind a closed disclosure.
    """
    factories.MatterFactory(owner=specialist)
    outside = Organisation.objects.order_by("name").last()
    assert outside is not None
    page = scripted(signed_in.get(CREATE).content.decode())

    assert str(outside.pk) not in chip_tags(page, "source_organisations")
    assert str(outside.pk) in chip_tags(page, "source_organisations_other")


# ---------------------------------------------------------------------------
# Ranking stays a presentation concern
# ---------------------------------------------------------------------------


def test_no_usage_count_reaches_the_picker(signed_in, crowded, specialist):
    """Popularity orders the chips and says nothing else (task §28).

    Ordering derived from records the reader may see is presentation; a *number*
    beside a body is a disclosure about how much work there is with it. The old
    control never printed one and neither does this one, and the assertion is
    worth keeping because a picker is exactly where somebody would add
    «(14 teemat)» as a helpful touch.
    """
    busy = Organisation.objects.first()
    assert busy is not None
    for _ in range(3):
        factories.MatterFactory(owner=specialist, source_organisations=[busy])

    page = scripted(signed_in.get(CREATE).content.decode())
    field = page[page.index("senderpick") : page.index("Valdkonnad")]

    assert "data-usage" not in field
    assert not re.search(r"\(\d+ teemat\)", field)
