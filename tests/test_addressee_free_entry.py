"""Adressaat, typed on the Teema form itself.

The workflow this replaces was: notice the body is not in the list, abandon the
half-filled Teema, add the institution under Asutused, come back, find it again,
save. Nobody used it. What is here instead is one field beside the chips — type
the name, save the Teema — and the whole risk of that convenience is identity.

So these tests are almost entirely about identity, and they pin the rule rather
than the implementation: **normalised exact, or nothing**. Casefolded, diacritics
stripped, whitespace collapsed, canonical names and recorded aliases — exactly
what `app.organisations.services` has always meant by "is this institution
already here?" (module docstring there). Similarity is not identity:
`Keskkonnaministeerium` and `Kliimaministeerium` score highly against each other
and are two ministries with two remits.

Three outcomes, and the third is the one worth having tests for:

* one match — reuse it;
* no match — create it;
* two matches — refuse the save, because `find_exact` answers ``None`` to both
  "nothing" and "two things", and a caller that reads the second as the first
  writes a third row and makes the ambiguity permanent (§7D).

`Saatja` is deliberately untouched and has a test saying so.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.core.errors import DomainError
from app.matters.models import Matter
from app.matters.services import resolve_addressee
from app.organisations.models import Organisation, OrganisationAlias, OrganisationType
from app.organisations.services import resolve_organisation_name
from tests import factories

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")


def _edit(matter) -> str:
    return reverse("matters:matter_edit", kwargs={"pk": matter.pk})


def _edit_payload(matter, **overrides) -> dict:
    """What `Muuda teemat` posts when nothing but the overrides changed.

    Written out rather than derived from `edit_initial`, because a test that
    built its own POST from the same function the view reads would pass while
    the form and the page disagreed about the field names.
    """
    payload = {
        "title": matter.title,
        "brief_summary": matter.brief_summary,
        "visibility": matter.visibility,
    }
    if matter.addressee_organisation_id:
        payload["addressee_organisation"] = str(matter.addressee_organisation_id)
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# The resolver itself
# ---------------------------------------------------------------------------


def test_a_canonical_name_resolves_to_the_row_that_already_has_it():
    existing = factories.OrganisationFactory(name="Kliimaministeerium")

    assert resolve_organisation_name(name="Kliimaministeerium") == existing
    assert Organisation.objects.count() == 1


def test_a_similar_name_is_not_the_same_institution():
    """The rule the module exists to state, as a test.

    Two ministries whose names share a suffix are two ministries. A matcher that
    merged them would take a decade of filing with it, and no amount of
    convenience buys that back.
    """
    factories.OrganisationFactory(name="Keskkonnaministeerium")

    resolved = resolve_organisation_name(name="Kliimaministeerium")

    assert resolved.name == "Kliimaministeerium"
    assert Organisation.objects.count() == 2


def test_two_rows_under_one_spelling_are_refused_rather_than_guessed():
    factories.OrganisationFactory(name="Ministeerium")
    factories.OrganisationFactory(name="ministeerium")

    with pytest.raises(DomainError):
        resolve_organisation_name(name="Ministeerium")

    assert Organisation.objects.count() == 2


def test_a_blank_name_is_the_absence_of_an_answer_not_an_error():
    assert resolve_organisation_name(name="   ") is None
    assert Organisation.objects.count() == 0


# ---------------------------------------------------------------------------
# Precedence: which of the two controls answered the question
# ---------------------------------------------------------------------------


def test_a_typed_name_wins_over_the_chip_that_was_already_selected():
    """Not a preference — the only rule that makes `Muuda teemat` work.

    That form's radio group always carries the addressee the Matter already has,
    so a rule that let the chip win would make replacing an addressee by typing
    impossible, which is the case this feature exists for.
    """
    chosen = factories.OrganisationFactory(name="Rahandusministeerium")

    resolved = resolve_addressee(chosen=chosen, typed_name="Kliimaministeerium")

    assert resolved.name == "Kliimaministeerium"


def test_the_chip_stands_when_nothing_was_typed():
    chosen = factories.OrganisationFactory(name="Rahandusministeerium")

    assert resolve_addressee(chosen=chosen, typed_name="") == chosen
    assert resolve_addressee(chosen=chosen, typed_name="   ") == chosen
    assert Organisation.objects.count() == 1


def test_nothing_chosen_and_nothing_typed_is_no_addressee():
    assert resolve_addressee(chosen=None, typed_name="") is None
    assert Organisation.objects.count() == 0


# ---------------------------------------------------------------------------
# Uus teema
# ---------------------------------------------------------------------------


def test_an_existing_canonical_name_typed_on_uus_teema_reuses_that_row(signed_in):
    existing = factories.OrganisationFactory(name="Majandus- ja Kommunikatsiooniministeerium")

    signed_in.post(
        CREATE,
        {"title": "Olemasolev adressaat", "addressee_name": existing.name},
    )

    matter = Matter.objects.get(title="Olemasolev adressaat")
    assert matter.addressee_organisation == existing
    assert Organisation.objects.count() == 1


@pytest.mark.parametrize(
    ("label", "typed"),
    [
        ("tyhikud", "  Majandus- ja Kommunikatsiooniministeerium  "),
        ("vaiketahed", "majandus- ja kommunikatsiooniministeerium"),
        ("topelttyhik", "Majandus-  ja   Kommunikatsiooniministeerium"),
        ("suurtahed", "MAJANDUS- JA KOMMUNIKATSIOONIMINISTEERIUM"),
    ],
)
def test_a_normalised_equivalent_spelling_reuses_the_existing_row(signed_in, label, typed):
    """Whitespace, case and diacritics change spelling, not identity.

    The behaviour is `app.core.text.normalize_for_matching`'s and is not
    re-specified here — what is pinned is that the Teema form goes through it
    rather than comparing raw strings.
    """
    existing = factories.OrganisationFactory(name="Majandus- ja Kommunikatsiooniministeerium")

    signed_in.post(CREATE, {"title": f"Kirjapilt {label}", "addressee_name": typed})

    matter = Matter.objects.get(title=f"Kirjapilt {label}")
    assert matter.addressee_organisation == existing
    assert Organisation.objects.count() == 1


def test_a_diacritic_variant_reuses_the_existing_row(signed_in):
    existing = factories.OrganisationFactory(name="Sotsiaalministeeriumi õigusosakond")

    signed_in.post(
        CREATE,
        {"title": "Täpitähed", "addressee_name": "sotsiaalministeeriumi oigusosakond"},
    )

    matter = Matter.objects.get(title="Täpitähed")
    assert matter.addressee_organisation == existing
    assert Organisation.objects.count() == 1


def test_a_recorded_alias_reuses_the_canonical_institution(signed_in):
    """An alias match is not fuzzy matching.

    Somebody decided that `MKM` names that ministry; this reads their decision.
    """
    canonical = factories.OrganisationFactory(name="Majandus- ja Kommunikatsiooniministeerium")
    OrganisationAlias.objects.create(organisation=canonical, alias="MKM")

    signed_in.post(CREATE, {"title": "Lühend", "addressee_name": "mkm"})

    matter = Matter.objects.get(title="Lühend")
    assert matter.addressee_organisation == canonical
    assert Organisation.objects.count() == 1


def test_a_brand_new_name_creates_exactly_one_institution(signed_in):
    signed_in.post(
        CREATE,
        {"title": "Uus asutus", "addressee_name": "  Riigikogu keskkonnakomisjon  "},
    )

    matter = Matter.objects.get(title="Uus asutus")
    created = Organisation.objects.get()
    assert created.name == "Riigikogu keskkonnakomisjon"
    assert created.organisation_type == OrganisationType.OTHER
    assert matter.addressee_organisation == created


def test_the_same_new_name_on_a_second_teema_does_not_duplicate_it(signed_in):
    signed_in.post(CREATE, {"title": "Esimene", "addressee_name": "Riigikogu keskkonnakomisjon"})
    signed_in.post(CREATE, {"title": "Teine", "addressee_name": "riigikogu  keskkonnakomisjon"})

    assert Organisation.objects.count() == 1
    organisation = Organisation.objects.get()
    assert Matter.objects.get(title="Esimene").addressee_organisation == organisation
    assert Matter.objects.get(title="Teine").addressee_organisation == organisation


def test_an_ambiguous_spelling_refuses_the_whole_save(signed_in):
    """Neither half of the wrong answer: no third row, and no Teema either.

    Creating a duplicate would make the ambiguity permanent; picking one would
    file the Teema against a body nobody named. The person has to choose.
    """
    factories.OrganisationFactory(name="Ministeerium")
    factories.OrganisationFactory(name="ministeerium")

    response = signed_in.post(
        CREATE, {"title": "Mitmetähenduslik", "addressee_name": "Ministeerium"}
    )

    assert response.status_code == 400
    assert not Matter.objects.filter(title="Mitmetähenduslik").exists()
    assert Organisation.objects.count() == 2
    assert "vali nimekirjast" in response.content.decode().lower()


def test_leaving_the_addressee_blank_still_means_maaramata(signed_in):
    signed_in.post(
        CREATE, {"title": "Ilma adressaadita", "addressee_organisation": "", "addressee_name": ""}
    )

    matter = Matter.objects.get(title="Ilma adressaadita")
    assert matter.addressee_organisation is None
    assert Organisation.objects.count() == 0


def test_choosing_an_existing_chip_still_works_exactly_as_before(signed_in):
    existing = factories.OrganisationFactory(name="Kliimaministeerium")

    signed_in.post(
        CREATE,
        {"title": "Valitud kiibilt", "addressee_organisation": str(existing.pk)},
    )

    matter = Matter.objects.get(title="Valitud kiibilt")
    assert matter.addressee_organisation == existing
    assert Organisation.objects.count() == 1


def test_a_partial_search_string_can_never_become_an_institution(signed_in):
    """The defect this design is shaped around.

    Somebody types «Kliima» into the disclosure's search box, the list narrows
    to `Kliimaministeerium`, they click it and save. If that box posted its
    contents, the register would gain an institution called «Kliima».

    It cannot, because the box has no `name` — it is a client-side filter and
    nothing else. The POST below is what the browser actually sends in that
    scenario, and the only string in it is the chosen organisation's key.
    """
    existing = factories.OrganisationFactory(name="Kliimaministeerium")

    signed_in.post(
        CREATE,
        {
            "title": "Otsingust valitud",
            "addressee_organisation": str(existing.pk),
            # What a filter box would have posted, had anybody given it a name.
            # It is here to prove the view reads no such field.
            "adressaat-otsing": "Kliima",
        },
    )

    matter = Matter.objects.get(title="Otsingust valitud")
    assert matter.addressee_organisation == existing
    assert Organisation.objects.count() == 1
    assert not Organisation.objects.filter(name="Kliima").exists()


def test_the_search_input_posts_nothing(signed_in):
    """Stated against the rendered page, not only against the POST above.

    A later round that "helpfully" added `name=` to the filter box would make
    the previous test pass and this one fail, which is the right way round.
    """
    import re

    # More than the ten offered as chips, so there is a long tail and the
    # disclosure that holds the filter box is actually rendered.
    factories.OrganisationFactory.create_batch(12)
    body = signed_in.get(CREATE).content.decode()

    assert 'data-choicefilter="adressaat-nimekiri"' in body

    # Every filter box on the page: the label carries `data-choicefilter`, and
    # the input inside it is the one a person types into to narrow the list.
    # The header's own site search is a different control and does post `q`.
    boxes = re.findall(r"<label[^>]*data-choicefilter=[^>]*>.*?</label>", body, flags=re.S)
    assert boxes, "the long-tail filter box is not on the page"
    for box in boxes:
        assert 'type="search"' in box, box
        assert "name=" not in box, f"a filter box would post its contents: {box}"


# ---------------------------------------------------------------------------
# Muuda teemat
# ---------------------------------------------------------------------------


def test_editing_replaces_an_existing_addressee_with_a_typed_new_one(signed_in, specialist):
    first = factories.OrganisationFactory(name="Rahandusministeerium")
    matter = factories.MatterFactory(owner=specialist, addressee_organisation=first)

    response = signed_in.post(
        _edit(matter), _edit_payload(matter, addressee_name="Riigikogu rahanduskomisjon")
    )

    assert response.status_code == 302
    matter.refresh_from_db()
    created = Organisation.objects.get(name="Riigikogu rahanduskomisjon")
    assert matter.addressee_organisation == created
    # The body that was there is a record other Matters may point at. Replacing
    # this Matter's addressee is not a reason to touch it.
    first.refresh_from_db()
    assert first.name == "Rahandusministeerium"
    assert Organisation.objects.count() == 2


def test_editing_with_an_existing_name_reuses_that_row(signed_in, specialist):
    first = factories.OrganisationFactory(name="Rahandusministeerium")
    second = factories.OrganisationFactory(name="Kliimaministeerium")
    matter = factories.MatterFactory(owner=specialist, addressee_organisation=first)

    signed_in.post(_edit(matter), _edit_payload(matter, addressee_name="kliimaministeerium"))

    matter.refresh_from_db()
    assert matter.addressee_organisation == second
    assert Organisation.objects.count() == 2


def test_editing_without_typing_keeps_the_selected_chip(signed_in, specialist):
    first = factories.OrganisationFactory(name="Rahandusministeerium")
    matter = factories.MatterFactory(owner=specialist, addressee_organisation=first)

    signed_in.post(_edit(matter), _edit_payload(matter, addressee_name=""))

    matter.refresh_from_db()
    assert matter.addressee_organisation == first
    assert Organisation.objects.count() == 1


def test_an_ambiguous_spelling_on_edit_changes_nothing(signed_in, specialist):
    first = factories.OrganisationFactory(name="Rahandusministeerium")
    factories.OrganisationFactory(name="Ministeerium")
    factories.OrganisationFactory(name="ministeerium")
    matter = factories.MatterFactory(owner=specialist, addressee_organisation=first)

    response = signed_in.post(
        _edit(matter), _edit_payload(matter, title="Uus pealkiri", addressee_name="Ministeerium")
    )

    assert response.status_code == 400
    matter.refresh_from_db()
    assert matter.addressee_organisation == first
    # The whole edit is one transaction, so the title did not move either.
    assert matter.title != "Uus pealkiri"
    assert Organisation.objects.count() == 3


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------


def test_a_late_failure_on_uus_teema_leaves_no_institution_behind(signed_in, monkeypatch):
    """Resolution and the Teema are one write or neither.

    The failure is forced after the addressee would have been created, in the
    next-action service the view calls last. What must not survive is an
    institution nobody asked for, sitting in the catalogue with nothing pointing
    at it.
    """

    def refuse(**kwargs):
        raise DomainError("Järgmine samm ei kõlba.")

    monkeypatch.setattr("app.matters.views.set_next_action_for_new_work", refuse)

    response = signed_in.post(
        CREATE,
        {
            "title": "Katkenud loomine",
            "addressee_name": "Riigikogu keskkonnakomisjon",
            "next-text": "Jälgida menetlust",
            "next-target_date": "1.9.2026",
        },
    )

    assert response.status_code == 400
    assert not Matter.objects.filter(title="Katkenud loomine").exists()
    assert not Organisation.objects.filter(name="Riigikogu keskkonnakomisjon").exists()
    assert Organisation.objects.count() == 0


def test_a_late_failure_on_muuda_teemat_leaves_no_institution_behind(
    signed_in, specialist, monkeypatch
):
    """Same guarantee on the edit path, where there is also a record to protect.

    `set_matter_visibility` is the last service the view calls, so a refusal
    there is a refusal after the addressee has been resolved and written.
    """
    first = factories.OrganisationFactory(name="Rahandusministeerium")
    matter = factories.MatterFactory(
        owner=specialist, title="Puutumata pealkiri", addressee_organisation=first
    )

    def refuse(**kwargs):
        raise DomainError("Nähtavust ei saa muuta.")

    monkeypatch.setattr("app.matters.views.set_matter_visibility", refuse)

    response = signed_in.post(
        _edit(matter),
        _edit_payload(matter, title="Muudetud pealkiri", addressee_name="Uus tundmatu asutus"),
    )

    assert response.status_code == 400
    matter.refresh_from_db()
    assert matter.title == "Puutumata pealkiri"
    assert matter.addressee_organisation == first
    assert not Organisation.objects.filter(name="Uus tundmatu asutus").exists()
    assert Organisation.objects.count() == 1


# ---------------------------------------------------------------------------
# Query discipline
# ---------------------------------------------------------------------------


def test_matching_happens_once_on_save_and_not_per_rendered_chip():
    """The catalogue is local and small; matching is still not free.

    Two lookups at most — canonical names, then aliases if the first found
    nothing — and they happen when somebody saves, never while they type. The
    client-side filter over the rendered chips stays client-side, so nothing
    here scales with the number of chips on the page.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    factories.OrganisationFactory.create_batch(25)
    existing = factories.OrganisationFactory(name="Kliimaministeerium")

    with CaptureQueriesContext(connection) as captured:
        assert resolve_organisation_name(name="Kliimaministeerium") == existing
    organisation_reads = [
        query
        for query in captured.captured_queries
        if "organisations_organisation" in query["sql"] and query["sql"].startswith("SELECT")
    ]
    assert len(organisation_reads) == 1, organisation_reads


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_a_reader_gains_no_institution_creation_endpoint_through_uus_teema(client, reader):
    """The feature must not become a way around the business-write boundary.

    A crafted POST from somebody who may read the register and change nothing in
    it is refused where every other write on this module is refused — before any
    service runs — so no Teema and no institution appear.
    """
    client.force_login(reader)

    response = client.post(
        CREATE, {"title": "Lugeja teema", "addressee_name": "Lugeja loodud asutus"}
    )

    assert response.status_code == 404
    assert not Matter.objects.filter(title="Lugeja teema").exists()
    assert not Organisation.objects.filter(name="Lugeja loodud asutus").exists()
    assert Organisation.objects.count() == 0


def test_a_reader_gains_no_institution_creation_endpoint_through_muuda_teemat(
    client, reader, specialist
):
    first = factories.OrganisationFactory(name="Rahandusministeerium")
    matter = factories.MatterFactory(owner=specialist, addressee_organisation=first)
    client.force_login(reader)

    response = client.post(
        _edit(matter), _edit_payload(matter, addressee_name="Lugeja loodud asutus")
    )

    assert response.status_code == 404
    matter.refresh_from_db()
    assert matter.addressee_organisation == first
    assert not Organisation.objects.filter(name="Lugeja loodud asutus").exists()
    assert Organisation.objects.count() == 1


# ---------------------------------------------------------------------------
# What did not change
# ---------------------------------------------------------------------------


def test_saatja_is_still_existing_organisations_only(signed_in):
    """Out of scope, and stated as a test so it stays that way.

    The approved decision covers `Adressaat`. Nothing on either Teema form
    creates a sender, and no field on either form offers to.
    """
    from app.matters.forms import MatterCreateForm, MatterEditForm

    for form_class, kwargs in (
        (MatterCreateForm, {}),
        (MatterEditForm, {"matter": factories.MatterFactory()}),
    ):
        fields = form_class(**kwargs).fields
        assert "addressee_name" in fields
        assert not [
            name
            for name in fields
            if name.startswith("source_organisation") and name.endswith("_name")
        ]


def test_the_obsolete_helper_sentence_is_gone_from_both_forms(signed_in, specialist):
    """The sentence said the capability did not exist. It does now.

    Removed rather than reworded: the control beside the chips says what can be
    done, and a paragraph explaining a field is a field that needed explaining.
    """
    obsolete = "Kui adressaati siin ei ole"
    # The Saatja sentence is word-for-word analogous and stays: sender
    # auto-creation is a separate decision nobody has taken (§11).
    kept = "Kui saatjat siin ei ole"
    matter = factories.MatterFactory(owner=specialist)

    for body in (
        signed_in.get(CREATE).content.decode(),
        signed_in.get(_edit(matter)).content.decode(),
    ):
        assert obsolete not in body
        assert "teema vormilt uut asutust ei teki" not in body.split(kept)[0]
        assert kept in body


def test_both_teema_forms_offer_the_same_addressee_control(signed_in, specialist):
    """One workflow, not two. A person must not have to learn this twice."""
    matter = factories.MatterFactory(owner=specialist)

    for body in (
        signed_in.get(CREATE).content.decode(),
        signed_in.get(_edit(matter)).content.decode(),
    ):
        assert 'name="addressee_name"' in body
        assert 'name="addressee_organisation"' in body


def test_the_matter_still_holds_exactly_one_addressee():
    """No `addressees[]`, no join table, no migration. One field, as before."""
    field = Matter._meta.get_field("addressee_organisation")

    assert field.many_to_one
    assert not field.many_to_many
