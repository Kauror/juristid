"""A counterparty, typed where it is asked rather than found in a list.

The workflow this replaces was: notice the body is not in the list, abandon the
half-filled record, add the institution under Asutused, come back, find it
again, save. Nobody used it. What is here instead is one field beside the chips
— type the name, save — and the whole risk of that convenience is identity.

**The form that asks is `Meile saadetud tagasiside`.** It asked `Muuda teemat`
until 2026-09-20: the Matter-level `Kellele` was the last Teema-form control
that typed an institution, and it left the ordinary Teema UI with `Sildid` and
`Menetlusliik` (docs/adr/0097 §4). `resolve_addressee` did not move with it —
it is the shared rule behind `Saatja`, both feedback panels and `Koja arvamus`
recipients — so the tests below exercise it through a panel that still asks,
and the identity rules they pin are unchanged.

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

`Saatja` answers the same way through the same resolver and has a test saying
which form offers which control.
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


def _feedback(matter) -> str:
    return reverse("matters:add_received_feedback", kwargs={"pk": matter.pk})


def _feedback_payload(**overrides) -> dict:
    """The smallest `Meile saadetud tagasiside` save that would otherwise succeed.

    A summary, a date, and whichever half of the picker the test is answering.
    The panel's own rules are `tests/test_teema_composer_simplification.py`'s;
    what is under test here is only what a typed name *means*.
    """
    payload = {"summary": "Toetab eelnõu.", "stated_on": "14.03.2026"}
    payload.update(overrides)
    return payload


def _edit_payload(matter, **overrides) -> dict:
    """What `Muuda teemat` posts when nothing but the overrides changed.

    Written out rather than derived from `edit_initial`, because a test that
    built its own POST from the same function the view reads would pass while
    the form and the page disagreed about the field names.

    `visibility` and `addressee_organisation` were in this base payload and are
    not fields any more, so sending them would make the helper a worse model of
    the browser rather than a more thorough one. The test that proves a crafted
    POST cannot write them sends them deliberately, through `overrides`
    (docs/adr/0096 §3, docs/adr/0097 §4).
    """
    payload = {
        "title": matter.title,
        "brief_summary": matter.brief_summary,
    }
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
# Answering Adressaat, on the form that asks it
# ---------------------------------------------------------------------------
#
# **This was `Uus teema` and is now `Muuda teemat`.** The create form stopped
# asking who Koda answers — a file arriving has a sender, and who it is answered
# to is decided later and elsewhere (docs/adr/0090 §5). Every identity rule
# below is unchanged and is asserted on the surface that now carries the
# control; what moved is the page, not the contract.


def answer_addressee(signed_in, title: str, **fields):
    """File a Teema, then name an institution on `Meile saadetud tagasiside`.

    Returns the `Organisation` the save resolved to, or `None` where it named
    none — which is what every test below is actually about. `addressee_name`
    is spelled `organisation_name` on this panel and `addressee_organisation`
    is `organisation`; the mapping is here so the cases below read as the
    identity rules they are rather than as one panel's field names.
    """
    signed_in.post(CREATE, {"title": title})
    matter = Matter.objects.get(title=title)
    fields = {
        {"addressee_name": "organisation_name", "addressee_organisation": "organisation"}.get(
            name, name
        ): value
        for name, value in fields.items()
    }
    signed_in.post(_feedback(matter), _feedback_payload(**fields), headers={"HX-Request": "true"})
    position = matter.external_positions.first()
    return None if position is None else position.organisation


def test_an_existing_canonical_name_typed_on_muuda_teemat_reuses_that_row(signed_in):
    existing = factories.OrganisationFactory(name="Majandus- ja Kommunikatsiooniministeerium")

    named = answer_addressee(signed_in, "Olemasolev adressaat", addressee_name=existing.name)

    assert named == existing
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

    named = answer_addressee(signed_in, f"Kirjapilt {label}", addressee_name=typed)

    assert named == existing
    assert Organisation.objects.count() == 1


def test_a_diacritic_variant_reuses_the_existing_row(signed_in):
    existing = factories.OrganisationFactory(name="Sotsiaalministeeriumi õigusosakond")

    named = answer_addressee(
        signed_in, "Täpitähed", addressee_name="sotsiaalministeeriumi oigusosakond"
    )

    assert named == existing
    assert Organisation.objects.count() == 1


def test_a_recorded_alias_reuses_the_canonical_institution(signed_in):
    """An alias match is not fuzzy matching.

    Somebody decided that `MKM` names that ministry; this reads their decision.
    """
    canonical = factories.OrganisationFactory(name="Majandus- ja Kommunikatsiooniministeerium")
    OrganisationAlias.objects.create(organisation=canonical, alias="MKM")

    named = answer_addressee(signed_in, "Lühend", addressee_name="mkm")

    assert named == canonical
    assert Organisation.objects.count() == 1


def test_a_brand_new_name_creates_exactly_one_institution(signed_in):
    named = answer_addressee(
        signed_in, "Uus asutus", addressee_name="  Riigikogu keskkonnakomisjon  "
    )

    created = Organisation.objects.get()
    assert created.name == "Riigikogu keskkonnakomisjon"
    assert created.organisation_type == OrganisationType.OTHER
    assert named == created


def test_the_same_new_name_on_a_second_teema_does_not_duplicate_it(signed_in):
    first = answer_addressee(signed_in, "Esimene", addressee_name="Riigikogu keskkonnakomisjon")
    second = answer_addressee(signed_in, "Teine", addressee_name="riigikogu  keskkonnakomisjon")

    assert Organisation.objects.count() == 1
    organisation = Organisation.objects.get()
    assert first == organisation
    assert second == organisation


def test_an_ambiguous_spelling_refuses_the_whole_save(signed_in):
    """Neither half of the wrong answer: no third row, and no answer either.

    Creating a duplicate would make the ambiguity permanent; picking one would
    file the Teema against a body nobody named. The person has to choose.
    """
    factories.OrganisationFactory(name="Ministeerium")
    factories.OrganisationFactory(name="ministeerium")

    signed_in.post(CREATE, {"title": "Mitmetähenduslik"})
    matter = Matter.objects.get(title="Mitmetähenduslik")
    response = signed_in.post(
        _feedback(matter),
        _feedback_payload(organisation_name="Ministeerium"),
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 400
    assert not matter.external_positions.exists()
    assert Organisation.objects.count() == 2
    assert "vali nimekirjast" in response.content.decode().lower()


def test_leaving_the_addressee_blank_still_means_maaramata(signed_in):
    named = answer_addressee(
        signed_in, "Ilma adressaadita", addressee_organisation="", addressee_name=""
    )

    assert named is None
    assert Organisation.objects.count() == 0


def test_choosing_an_existing_chip_still_works_exactly_as_before(signed_in):
    existing = factories.OrganisationFactory(name="Kliimaministeerium")

    named = answer_addressee(signed_in, "Valitud kiibilt", addressee_organisation=str(existing.pk))

    assert named == existing
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

    named = answer_addressee(
        signed_in,
        "Otsingust valitud",
        addressee_organisation=str(existing.pk),
        # What a filter box would have posted, had anybody given it a name.
        # It is here to prove the view reads no such field.
        **{"tagasiside-otsing": "Kliima"},
    )

    assert named == existing
    assert Organisation.objects.count() == 1
    assert not Organisation.objects.filter(name="Kliima").exists()


def test_the_search_input_posts_nothing(signed_in):
    """Stated against the rendered page, not only against the POST above.

    A later round that "helpfully" added `name=` to the search box would make
    the previous test pass and this one fail, which is the right way round —
    and that round is now *more* tempting rather than less, because since
    docs/adr/0073 the same box also carries the name a person is proposing. It
    does not post it: the `+` beside it moves the text into `addressee_name`,
    which is a different control with a different meaning.

    The box is `[data-orgfind-input]` now rather than a label carrying
    `data-choicefilter`. Asserted on both Teema forms, because both are asked
    the same question — and both now offer exactly **one** picker, which is
    `Saatja`. `Muuda teemat` offered a second for the Matter-level `Kellele`
    until docs/adr/0097 §4 withdrew that question; `Uus teema` never had it.
    One picker each is the sameness this round is for.
    """
    import re

    # More than the ten offered as chips, so there are bodies the search has to
    # reach and the control is doing something.
    factories.OrganisationFactory.create_batch(12)
    matter = factories.MatterFactory()

    for page, expected in (
        (signed_in.get(CREATE).content.decode(), 1),
        (signed_in.get(_edit(matter)).content.decode(), 1),
    ):
        boxes = [tag for tag in re.findall(r"<input[^>]*>", page) if "data-orgfind-input" in tag]
        assert len(boxes) == expected, boxes
        for box in boxes:
            assert 'type="search"' in box, box
            assert "name=" not in box, f"a search box would post its contents: {box}"


# ---------------------------------------------------------------------------
# Muuda teemat, which no longer asks
# ---------------------------------------------------------------------------


def test_muuda_teemat_offers_no_addressee_control_at_all(signed_in, specialist):
    """The question left the ordinary Teema UI, and the control with it.

    These four tests drove that control: typing a new addressee over an
    existing one, reusing a row by name, keeping the chip when nothing was
    typed, and a whole-save refusal on an ambiguous spelling. Each of those
    rules is `resolve_addressee`'s and every one of them is still exercised
    above, through the panel that still asks (docs/adr/0097 §4).

    What is left to assert here is the withdrawal itself, and the shape of it:
    **the fields are gone, not hidden**, so the page draws nothing and a
    crafted POST binds to nothing.
    """
    page = signed_in.get(_edit(factories.MatterFactory(owner=specialist))).content.decode()

    assert "Kellele" not in page
    assert 'name="addressee_organisation"' not in page
    assert 'name="addressee_name"' not in page


def test_a_crafted_edit_post_cannot_set_an_addressee(signed_in, specialist):
    """Neither half of the retired control writes anything.

    Both spellings in one POST, because the defect this guards against is a
    *branch* surviving its control rather than a field surviving its label: a
    view that still read either name would file this Matter against a body
    nobody was offered the chance to name.
    """
    first = factories.OrganisationFactory(name="Rahandusministeerium")
    second = factories.OrganisationFactory(name="Kliimaministeerium")
    matter = factories.MatterFactory(owner=specialist, addressee_organisation=first)

    response = signed_in.post(
        _edit(matter),
        _edit_payload(
            matter,
            addressee_organisation=str(second.pk),
            addressee_name="Riigikogu rahanduskomisjon",
        ),
    )

    assert response.status_code == 302
    matter.refresh_from_db()
    # The stored addressee is untouched — `set_organisations` is called without
    # the parameter, and its `_UNSET` default means «leave this alone» where
    # `None` would have cleared a fact nobody stated.
    assert matter.addressee_organisation == first
    # And no institution was created from the typed half.
    assert not Organisation.objects.filter(name="Riigikogu rahanduskomisjon").exists()
    assert Organisation.objects.count() == 2


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------


def test_a_late_failure_on_uus_teema_leaves_no_institution_behind(signed_in, monkeypatch):
    """Resolution and the Teema are one write or neither.

    The failure is forced after the institution would have been created, in the
    next-action service the view calls last. What must not survive is an
    institution nobody asked for, sitting in the catalogue with nothing pointing
    at it.

    Through `sender_name` rather than `addressee_name`: `Uus teema` no longer
    asks Adressaat, and Saatja's typed half resolves through the same catalogue
    in the same transaction (docs/adr/0063, docs/adr/0090 §5). The guarantee is
    the transaction's, not the field's.
    """

    def refuse(**kwargs):
        raise DomainError("Järgmine samm ei kõlba.")

    # The last service `matter_create` calls, which is what makes this a *late*
    # failure. It used to be `set_next_action_for_new_work` behind `Järgmiseks`;
    # that block is off the page and the step is established from `Arvamuse
    # tähtaeg` instead (docs/adr/0094 §5). The guarantee under test — one
    # transaction, so a refusal after the institution was resolved takes it with
    # it — is unchanged, and it is still asserted against the last thing to run.
    monkeypatch.setattr("app.matters.views.establish_opinion_preparation_action", refuse)

    response = signed_in.post(
        CREATE,
        {
            "title": "Katkenud loomine",
            "sender_name": "Riigikogu keskkonnakomisjon",
            "response_deadline": "1.9.2026",
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

    `_save_procedural_link` is the last thing the view does, so a refusal there
    is a refusal after `Saatja` has been resolved and written. It was
    `set_matter_visibility`, then `set_tags`, and each of those calls went with
    the control that fed it (docs/adr/0096 §3, docs/adr/0097 §2); what is being
    tested is the transaction boundary, not which service happens to stand at
    the end of it.

    Through `sender_name`, because that is the typed control this page still
    offers. The guarantee is the transaction's, not the field's.
    """
    first = factories.OrganisationFactory(name="Rahandusministeerium")
    matter = factories.MatterFactory(
        owner=specialist, title="Puutumata pealkiri", addressee_organisation=first
    )

    def refuse(**kwargs):
        raise DomainError("Menetluse linki ei saa salvestada.")

    monkeypatch.setattr("app.matters.views._save_procedural_link", refuse)

    response = signed_in.post(
        _edit(matter),
        _edit_payload(matter, title="Muudetud pealkiri", sender_name="Uus tundmatu asutus"),
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

    response = client.post(CREATE, {"title": "Lugeja teema", "sender_name": "Lugeja loodud asutus"})

    assert response.status_code == 404
    assert not Matter.objects.filter(title="Lugeja teema").exists()
    assert not Organisation.objects.filter(name="Lugeja loodud asutus").exists()
    assert Organisation.objects.count() == 0


def test_a_reader_gains_no_institution_creation_endpoint_through_muuda_teemat(
    client, reader, specialist
):
    """Through `sender_name`, which is the typed control this page still offers."""
    first = factories.OrganisationFactory(name="Rahandusministeerium")
    matter = factories.MatterFactory(owner=specialist, addressee_organisation=first)
    client.force_login(reader)

    response = client.post(_edit(matter), _edit_payload(matter, sender_name="Lugeja loodud asutus"))

    assert response.status_code == 404
    matter.refresh_from_db()
    assert matter.addressee_organisation == first
    assert not Organisation.objects.filter(name="Lugeja loodud asutus").exists()
    assert Organisation.objects.count() == 1


def test_a_reader_gains_no_institution_creation_endpoint_through_the_feedback_panel(
    client, reader, specialist
):
    """The surface that *does* type an institution refuses the same actor."""
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(reader)

    response = client.post(
        _feedback(matter),
        _feedback_payload(organisation_name="Lugeja loodud asutus"),
        headers={"HX-Request": "true"},
    )

    assert response.status_code in (403, 404)
    assert not Organisation.objects.filter(name="Lugeja loodud asutus").exists()
    assert Organisation.objects.count() == 0


# ---------------------------------------------------------------------------
# What did not change
# ---------------------------------------------------------------------------


def test_each_form_offers_the_typed_control_for_the_question_it_asks(signed_in):
    """Which form asks which question, stated as a test so it stays that way.

    Both Teema forms ask who sent the file and both offer `sender_name`, and
    that is now the whole list: the Matter-level `Kellele` left the ordinary
    Teema UI with its typed half (docs/adr/0097 §4). Neither form ever grew a
    `source_organisation*_name` field beside the one it has: one typed control
    per question (docs/adr/0063, docs/adr/0073, docs/adr/0090 §5).
    """
    from app.matters.forms import MatterCreateForm, MatterEditForm

    create_fields = MatterCreateForm().fields
    assert "sender_name" in create_fields
    assert "addressee_name" not in create_fields

    edit_fields = MatterEditForm(matter=factories.MatterFactory()).fields
    assert "sender_name" in edit_fields
    assert "addressee_name" not in edit_fields

    for fields in (create_fields, edit_fields):
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
    # The Saatja sentence was word-for-word analogous and stood while sender
    # creation was still forbidden. docs/adr/0063 withdrew that rule, so it goes
    # for the same reason its twin did — the control says what can be done.
    obsolete_sender = "Kui saatjat siin ei ole"
    matter = factories.MatterFactory(owner=specialist)

    for body in (
        signed_in.get(CREATE).content.decode(),
        signed_in.get(_edit(matter)).content.decode(),
    ):
        assert obsolete not in body
        assert obsolete_sender not in body
        assert "teema vormilt uut asutust ei teki" not in body


def test_neither_teema_form_draws_an_addressee_control(signed_in, specialist):
    """One workflow, not two — and now neither page asks the question.

    `Uus teema` drew neither half of Adressaat from docs/adr/0090 §5: not the
    catalogue, not the typed box, and not the hidden marker that made the old
    default overridable. `Muuda teemat` drew both until docs/adr/0097 §4, on
    the reading that a canonical fact the master does not ask has to be
    answerable somewhere. The owner withdrew the reading, and the control went
    with it.
    """
    matter = factories.MatterFactory(owner=specialist)

    for body in (
        signed_in.get(CREATE).content.decode(),
        signed_in.get(_edit(matter)).content.decode(),
    ):
        assert 'name="addressee_name"' not in body
        assert 'name="addressee_organisation"' not in body
        assert "addressee_is_manual" not in body


def test_the_matter_still_holds_exactly_one_addressee():
    """No `addressees[]`, no join table, no migration. One field, as before."""
    field = Matter._meta.get_field("addressee_organisation")

    assert field.many_to_one
    assert not field.many_to_many
