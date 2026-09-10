"""One Organisation catalogue, from Uus teema to the register's Asutus filter.

The reported defect: an institution added through Uus teema's typed Saatja or
Adressaat was "not reliably available" in Teemad → Täpsem otsing → Asutus.

It was never a second catalogue. Every one of these controls has always read
``Organisation.objects``, and the lifecycle tests below assert that as an
identity — same primary key from creation to filter — rather than as a
behaviour that could be satisfied by a copy.

What was actually wrong was the **first page**. ``_organisation_options("")``
returns the first twenty bodies alphabetically, and the Täpsem otsing panel
populated all three institution controls from that one truncated list. Only
`Asutus` had a search box, so:

* `Saatja` and `Adressaat` could not reach a body outside the alphabetical
  first twenty at all — and an applied ``?saatja=<pk>`` outside it was not
  redisplayed in its own control either, so the next submit silently dropped a
  filter the chip above still claimed was applied;
* with scripting off, or before HTMX has loaded, `Asutus` was limited to the
  same twenty: typing a name and pressing Enter reloaded the page and rendered
  the unsearched first page in answer.

"Reliably available" was therefore literally true — reliable exactly when the
body happened to sort in the first twenty — and a newly typed institution is
the case most likely not to (docs/adr/0071).

A fourth control was found while writing these: `Asutus` offered *Määramata* and
the register answered it with an empty list, because `puudub` reached
``uuid.UUID()`` and was refused as unreadable.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.matters.models import Matter
from app.organisations.models import Organisation, OrganisationAlias
from tests import factories

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")
REGISTER = reverse("matters:matter_list")
CHOOSER = reverse("matters:organisation_choices")

#: More than `ORGANISATION_CHOICES`, so nothing below can pass by accident on a
#: catalogue small enough to fit in the unsearched first page.
CROWD = 25


def crowd_the_catalogue() -> None:
    """Twenty-five bodies that all sort before anything named later.

    The register's controls offer the first twenty alphabetically without being
    asked, so a test whose institution sorts inside that window proves nothing.
    """
    for index in range(CROWD):
        factories.OrganisationFactory(name=f"Aaa asutus {index:02d}")


def offered_by(response) -> list[str]:
    return [row.name for row in response.context["organisation_options"]]


def titles_on(response) -> list[str]:
    return [matter.title for matter in response.context["page"].object_list]


def select_block(body: str, field: str) -> str:
    """The one ``<select>`` this dimension submits, and nothing else.

    Sliced on the exact opening tag: the search box's own hidden inputs carry
    the same ``name``, and a looser match reads a filter value out of one of
    those and calls the control correct.
    """
    start = body.index(f'<select class="field__input" name="{field}">')
    return body[start : body.index("</select>", start)]


# ---------------------------------------------------------------------------
# A. the lifecycle — created on Uus teema, found in the register
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "typed_field,payload_key",
    [("saatja", "sender_name"), ("adressaat", "addressee_name")],
)
def test_a_body_typed_on_uus_teema_is_immediately_findable(signed_in, typed_field, payload_key):
    """The whole reported round trip, in one test and with no reindex between.

    Create the institution the way Uus teema creates one — by typing it into
    the sender or addressee field — then search for it in the register's
    chooser and apply it. The chooser's contract is a direct lookup against the
    canonical catalogue, so nothing here waits on a search-index rebuild and
    nothing here may start to.
    """
    crowd_the_catalogue()
    signed_in.post(CREATE, {"title": "Uue asutusega teema", payload_key: "Zeta Näidisliit"})

    created = Organisation.objects.get(name="Zeta Näidisliit")

    for field in ("asutus", typed_field):
        found = signed_in.get(CHOOSER, {"vali": field, f"{field}_otsing": "Zeta"})
        assert offered_by(found) == ["Zeta Näidisliit"], field
        assert str(created.pk) in found.content.decode(), field

        applied = signed_in.get(REGISTER, {field: str(created.pk), "olek": "koik"})
        assert titles_on(applied) == ["Uue asutusega teema"], field


@pytest.mark.parametrize("payload_key", ["sender_name", "addressee_name"])
def test_the_register_filter_uses_the_very_row_uus_teema_created(signed_in, payload_key):
    """Identity, not resemblance.

    A fix that added a second source would satisfy "the name appears"; this
    asserts the primary key on the Matter is the primary key the chooser
    offered, which no copy can satisfy.
    """
    signed_in.post(CREATE, {"title": "Sama rida", payload_key: "Zeta Näidisliit"})

    created = Organisation.objects.get(name="Zeta Näidisliit")
    matter = Matter.objects.get(title="Sama rida")
    stored = (
        matter.addressee_organisation
        if payload_key == "addressee_name"
        else matter.source_organisations.get()
    )

    assert stored.pk == created.pk
    offered = signed_in.get(CHOOSER, {"vali": "asutus", "asutus_otsing": "Zeta"})
    assert [row.pk for row in offered.context["organisation_options"]] == [created.pk]


#: A Teema that names Zeta as its Saatja and **nobody** as its Adressaat.
#:
#: Since docs/adr/0069 a single unambiguous Saatja answers Adressaat by
#: default, so `{"sender_name": …}` alone no longer builds a one-directional
#: Matter — it builds one where Zeta is both, which is the intended product
#: behaviour and not something to work around. `addressee_is_manual` is the
#: field that exists for exactly this: it is how the form says «this person
#: answered Adressaat themselves», and answering it with nothing is choosing
#: «Määramata» beside a sender (app/matters/forms.py `_default_addressee`).
#:
#: Both tests below need the one-directional case to mean anything at all —
#: one of them would otherwise assert that a body is found in «either»
#: direction while only ever having been stored in one.
SENDER_ONLY = {"sender_name": "Zeta Näidisliit", "addressee_is_manual": "1"}
ADDRESSEE_ONLY = {"addressee_name": "Zeta Näidisliit"}


def test_asutus_finds_the_body_in_either_direction(signed_in):
    """The convenience filter, for when somebody only remembers who was involved."""
    signed_in.post(CREATE, {"title": "Saatjana", **SENDER_ONLY})
    signed_in.post(CREATE, {"title": "Adressaadina", **ADDRESSEE_ONLY})

    created = Organisation.objects.get(name="Zeta Näidisliit")
    response = signed_in.get(REGISTER, {"asutus": str(created.pk), "olek": "koik"})

    assert set(titles_on(response)) == {"Saatjana", "Adressaadina"}


def test_the_precise_directions_still_tell_the_two_apart(signed_in):
    """`Asutus` does not replace them.

    The register's own counterparty column meant the sender until 2019 and the
    addressee from 2020, so a combined filter would answer a question nobody
    asked (Stage-2E brief 27).
    """
    signed_in.post(CREATE, {"title": "Saatjana", **SENDER_ONLY})
    signed_in.post(CREATE, {"title": "Adressaadina", **ADDRESSEE_ONLY})
    created = Organisation.objects.get(name="Zeta Näidisliit")

    sent = signed_in.get(REGISTER, {"saatja": str(created.pk), "olek": "koik"})
    assert titles_on(sent) == ["Saatjana"]

    addressed = signed_in.get(REGISTER, {"adressaat": str(created.pk), "olek": "koik"})
    assert titles_on(addressed) == ["Adressaadina"]


def test_a_defaulted_addressee_is_found_by_both_precise_filters(signed_in):
    """And the composed case is not a gap in the two above — it is the ordinary one.

    A Teema created with one Saatja and nothing said about Adressaat has *both*
    relations pointing at that body since docs/adr/0069. It must therefore
    answer `?saatja=` **and** `?adressaat=`, because both are true of it — and
    the two tests above deliberately construct the one-directional case
    instead, so without this one nothing would assert what the default actually
    does to the register.
    """
    signed_in.post(CREATE, {"title": "Mõlemana", "sender_name": "Zeta Näidisliit"})
    created = Organisation.objects.get(name="Zeta Näidisliit")

    for parameter in ("saatja", "adressaat", "asutus"):
        response = signed_in.get(REGISTER, {parameter: str(created.pk), "olek": "koik"})
        assert titles_on(response) == ["Mõlemana"], (
            f"a Teema whose Adressaat was defaulted from its Saatja is not found by ?{parameter}="
        )


def test_a_typed_name_that_already_names_a_body_reuses_it(signed_in):
    """The catalogue stays one catalogue in the other direction too.

    Nothing about this change may start creating a near-duplicate: an exact
    normalised match — canonical or aliased — *is* that institution
    (app/organisations/services.py).
    """
    existing = factories.OrganisationFactory(name="Kliimaministeerium")
    OrganisationAlias.objects.create(organisation=existing, alias="KLIM")
    before = Organisation.objects.count()

    signed_in.post(CREATE, {"title": "Lühendiga", "sender_name": "klim"})

    assert Organisation.objects.count() == before
    matter = Matter.objects.get(title="Lühendiga")
    assert matter.source_organisations.get().pk == existing.pk


def test_the_chooser_finds_a_newly_created_body_by_a_recorded_alias(signed_in):
    """Aliases are reviewed data, and the chooser reads them for every dimension."""
    signed_in.post(CREATE, {"title": "Uue asutusega", "sender_name": "Zeta Näidisliit"})
    created = Organisation.objects.get(name="Zeta Näidisliit")
    OrganisationAlias.objects.create(organisation=created, alias="ZNL")

    for field in ("asutus", "saatja", "adressaat"):
        found = signed_in.get(CHOOSER, {"vali": field, f"{field}_otsing": "ZNL"})
        assert offered_by(found) == ["Zeta Näidisliit"], field


# ---------------------------------------------------------------------------
# B. all three controls, one pool
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["asutus", "saatja", "adressaat"])
def test_every_institution_control_is_searchable(signed_in, field):
    """The defect, stated as the missing capability.

    `Saatja` and `Adressaat` were plain selects with no search box, so a body
    outside the alphabetical first twenty could not be chosen from them at all.
    """
    crowd_the_catalogue()
    factories.OrganisationFactory(name="Zeta Näidisliit")

    body = signed_in.get(REGISTER).content.decode()
    assert f'name="{field}_otsing"' in body


@pytest.mark.parametrize("field", ["asutus", "saatja", "adressaat"])
def test_an_applied_body_comes_back_selected_in_its_own_control(signed_in, field):
    """Otherwise the next submit silently drops a filter the chip still claims.

    The body sorts past the unsearched first page on purpose: this is exactly
    the case that failed.
    """
    crowd_the_catalogue()
    chosen = factories.OrganisationFactory(name="Zeta Näidisliit")

    body = signed_in.get(REGISTER, {field: str(chosen.pk), "olek": "koik"}).content.decode()
    block = select_block(body, field)

    assert str(chosen.pk) in block
    assert "selected" in block


@pytest.mark.parametrize("field", ["asutus", "saatja", "adressaat"])
def test_choosing_one_body_does_not_pin_it_into_the_other_controls(signed_in, field):
    """Three controls, three selections. One shared `chosen_organisation` would
    have put the Asutus choice into the Saatja select as well."""
    chosen = factories.OrganisationFactory(name="Zeta Näidisliit")

    body = signed_in.get(REGISTER, {field: str(chosen.pk), "olek": "koik"}).content.decode()
    for other in {"asutus", "saatja", "adressaat"} - {field}:
        assert "selected" not in select_block(body, other), other


@pytest.mark.parametrize("field", ["asutus", "saatja", "adressaat"])
def test_a_typed_term_survives_a_submit_without_scripting(signed_in, field):
    """The no-JavaScript path, which is the one that answered with the wrong list.

    Pressing Enter in the search box submits the enclosing filter form, so the
    term arrives as an ordinary parameter. Rendering the unsearched first page
    in answer would be the control ignoring what somebody just typed into it —
    and the unsearched page is precisely where their institution is not.
    """
    crowd_the_catalogue()
    factories.OrganisationFactory(name="Zeta Näidisliit")

    response = signed_in.get(REGISTER, {f"{field}_otsing": "Zeta"})
    block = select_block(response.content.decode(), field)

    assert "Zeta Näidisliit" in block


def test_the_choosers_own_search_text_is_not_a_filter(signed_in):
    """It narrows a list of options and nothing else.

    So it must not become a chip, must not survive `Tühjenda kõik`, and must not
    be carried into a link somebody shares.
    """
    response = signed_in.get(REGISTER, {"asutus_otsing": "kliima"})

    assert [chip["name"] for chip in response.context["active_filters"]] == []
    assert response.context["cleared_query"] == ""
    assert "asutus_otsing" not in dict(response.context["carried_params"])


@pytest.mark.parametrize("field", ["asutus", "saatja", "adressaat"])
def test_the_control_does_not_rename_itself_when_somebody_types_into_it(signed_in, field):
    """The panel and the HTMX fragment are the same partial over one label.

    They were two spellings, so `Asutus (saatja või adressaat)` came back from
    a search calling itself `Asutus` — a control that renames itself the first
    time it is used.
    """
    page = signed_in.get(REGISTER).content.decode()
    fragment = signed_in.get(CHOOSER, {"vali": field, f"{field}_otsing": "kliima"})
    legend = fragment.context["field_label"]

    assert f'<legend class="field__label">{legend}</legend>' in page


# ---------------------------------------------------------------------------
# C. Määramata, on the convenience filter
# ---------------------------------------------------------------------------


def test_maaramata_on_asutus_finds_the_files_with_neither_side_recorded(signed_in):
    """The option has always been offered and has always emptied the register.

    `puudub` reached `uuid.UUID()`, was refused as unreadable, and emptied the
    list — a dead option above a list of nothing, which is the exact lie the
    "an unreadable value empties the list" rule exists to prevent
    (app/matters/register_filters.py).
    """
    lonely = factories.MatterFactory(title="Ilma asutuseta")
    factories.MatterFactory(
        title="Saatjaga", source_organisations=[factories.OrganisationFactory()]
    )
    factories.MatterFactory(
        title="Adressaadiga", addressee_organisation=factories.OrganisationFactory()
    )

    response = signed_in.get(REGISTER, {"asutus": "puudub", "olek": "koik"})
    assert titles_on(response) == [lonely.title]


def test_maaramata_comes_back_selected(signed_in):
    """It names no body, so a control that only redisplays bodies loses it."""
    body = signed_in.get(REGISTER, {"asutus": "puudub", "olek": "koik"}).content.decode()
    block = select_block(body, "asutus")

    assert '<option value="puudub" selected>' in block.replace(" >", ">")


def test_maaramata_still_reads_as_maaramata_in_the_chip(signed_in):
    response = signed_in.get(REGISTER, {"asutus": "puudub", "olek": "koik"})
    values = {chip["name"]: chip["value"] for chip in response.context["active_filters"]}

    assert values["asutus"] == "Määramata"
