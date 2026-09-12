"""The post-QA read-surface round: what a Matter says back about itself.

Eight findings, one property. The application had grown a set of facts it asked
for and then declined to state: a mailing recorded as `Kirjade voor` read back
as `E-kiri või kampaania`, a Matter filed under `Muu` alone read back as
`Määramata`, and `Õigusakt` — asked on creation, corrected on the edit page,
indexed for search — read back nowhere at all. Beside them, a deadline editor
that proposed today's date for a fact only somebody else can assert, a file
count that looked like a total while showing a filtered subset, and two adjacent
upload controls sharing one visible name.

None of it is a storage defect and none of it has a migration behind it. Every
value in here was already being written correctly; what these tests pin is that
it is now *read* correctly, which is the half a person actually experiences.

The browser-side halves — the picker's popup lifecycle (R2-10) and the selected
count over a provisional body (R2-11) — are in `e2e/test_post_qa_surfaces.py`,
because a blur is not a thing a Django test client has.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.documents.enums import DocumentRole
from app.matters.enums import COMPOSER_ENGAGEMENT_KINDS, EngagementKind
from app.matters.forms import ENGAGEMENT_CHOICES, MatterEditForm, edit_initial
from app.matters.models import Matter
from app.matters.services import (
    add_engagement,
    set_legal_instrument_other,
    set_legal_instruments,
    set_policy_area_other,
    set_policy_areas,
)
from app.organisations.models import Organisation
from app.taxonomy.models import LegalInstrumentType, PolicyArea
from tests import factories

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")


def instrument(key: str) -> LegalInstrumentType:
    return LegalInstrumentType.objects.get(key=key)


def detail(client, matter: Matter) -> str:
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


def edit_page(client, matter: Matter) -> str:
    response = client.get(reverse("matters:matter_edit", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


def documents(client, matter: Matter, query: str = "") -> str:
    url = reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    response = client.get(f"{url}{query}")
    assert response.status_code == 200
    return response.content.decode()


def edit_payload(matter: Matter, **overrides: object) -> dict[str, object]:
    """What `Muuda teemat` posts for this Matter, with the given fields changed.

    Built from `edit_initial`, because a partial POST to that page is a POST no
    browser makes: every control on it posts, and a test that omitted the ones
    it was not interested in would be asserting against a clearing save.
    """
    initial = edit_initial(matter)
    payload: dict[str, object] = {
        "title": initial["title"],
        "brief_summary": initial["brief_summary"] or "",
        "owner": initial["owner"] or "",
        "stage": initial["stage"] or "",
        "track": initial["track"] or "",
        "policy_areas": [str(pk) for pk in initial["policy_areas"]],
        "policy_area_other": initial["policy_area_other"] or "",
        "legal_instruments": [str(pk) for pk in initial["legal_instruments"]],
        "legal_instrument_other": initial["legal_instrument_other"] or "",
        "source_organisations": [str(pk) for pk in initial["source_organisations"]],
        "sender_name": "",
        "addressee_organisation": initial["addressee_organisation"] or "",
        "addressee_name": "",
        "received_date": "",
        "response_deadline": "",
        "tags": [str(pk) for pk in initial["tags"]],
        "visibility": initial["visibility"],
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# R2-06 — one label for EMAIL_CAMPAIGN
# ---------------------------------------------------------------------------


def test_the_enum_is_the_one_dictionary_for_email_campaign():
    """`Kirjade voor`, and no second spelling anywhere that renders it.

    The value had three labels: the enum's «E-kiri või kampaania», the composer
    chips' «Kirjade voor» and `EngagementForm`'s «Otsepostitus». Everything that
    renders this kind goes through `get_kind_display`, so fixing the enum is
    what fixes the chronology and the process strip; the two write surfaces now
    read their labels off it rather than carrying their own.
    """
    assert EngagementKind.EMAIL_CAMPAIGN.label == "Kirjade voor"

    offered = dict(COMPOSER_ENGAGEMENT_KINDS)
    assert offered[EngagementKind.EMAIL_CAMPAIGN.value] == "Kirjade voor"
    assert dict(ENGAGEMENT_CHOICES)[EngagementKind.EMAIL_CAMPAIGN.value] == "Kirjade voor"


def test_the_stored_value_did_not_move_with_the_label():
    """A relabelling that renamed the value would be a data migration."""
    assert EngagementKind.EMAIL_CAMPAIGN.value == "EMAIL_CAMPAIGN"


def test_a_mailing_reads_back_in_the_words_it_was_recorded_in(signed_in, specialist):
    """The whole of R2-06, end to end on the surface that reported it.

    Somebody chose `Kirjade voor` and the chronology told them they had recorded
    an «E-kiri või kampaania» — a phrase the page that wrote it had never shown
    them.
    """
    matter = factories.MatterFactory(owner=specialist)
    add_engagement(
        matter=matter,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title="liikmed",
        actor=specialist,
    )

    body = detail(signed_in, matter)

    assert "Kirjade voor" in body
    assert "E-kiri või kampaania" not in body


def test_a_historical_web_call_still_reads(signed_in, specialist):
    """Nothing was retired. A value no write surface offers still renders."""
    matter = factories.MatterFactory(owner=specialist)
    add_engagement(
        matter=matter, kind=EngagementKind.WEB_CALL, title="avalik konsultatsioon", actor=specialist
    )

    assert "Kaasamiskutse veebis" in detail(signed_in, matter)


# ---------------------------------------------------------------------------
# R2-07 — Muu valdkond must be truthfully readable
# ---------------------------------------------------------------------------


def test_muu_alone_is_never_read_as_maaramata(signed_in, specialist):
    """The defect exactly: the question was answered and the page denied it."""
    matter = factories.MatterFactory(owner=specialist)
    set_policy_area_other(matter=matter, value="Ringmajandus", actor=specialist)

    body = detail(signed_in, matter)

    assert "Muu: Ringmajandus" in body
    assert "Valdkond" in body
    # The only `Määramata` a Matter with an owner could still carry is the
    # owner slot's, and this Matter has an owner — so none may remain.
    assert 'metafield__missing">Määramata' not in body


def test_canonical_areas_and_muu_read_together(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    area = PolicyArea.objects.filter(is_active=True).first()
    assert area is not None
    set_policy_areas(matter=matter, policy_areas=[area], actor=specialist)
    set_policy_area_other(matter=matter, value="Ringmajandus", actor=specialist)

    body = detail(signed_in, matter)

    assert area.name_et in body
    assert "Muu: Ringmajandus" in body


def test_no_valdkond_at_all_still_says_maaramata(signed_in, specialist):
    """`Määramata` keeps the one state it truthfully names."""
    matter = factories.MatterFactory(owner=specialist)

    assert "Määramata" in detail(signed_in, matter)


def test_creating_with_muu_and_reading_it_back_is_one_round_trip(signed_in):
    """create → read. The first half of the round trip §4 asks for."""
    signed_in.post(
        CREATE,
        {
            "title": "Ainult muu",
            "policy_area_other_selected": "on",
            "policy_area_other": "Ringmajandus",
        },
    )

    matter = Matter.objects.get(title="Ainult muu")
    assert matter.policy_area_other == "Ringmajandus"
    assert "Muu: Ringmajandus" in detail(signed_in, matter)


def test_the_edit_page_shows_the_existing_muu_text(signed_in, specialist):
    """edit. A value the correction page cannot show is a value nobody can fix."""
    matter = factories.MatterFactory(owner=specialist)
    set_policy_area_other(matter=matter, value="Ringmajandus", actor=specialist)

    body = edit_page(signed_in, matter)

    assert 'name="policy_area_other"' in body
    assert "Ringmajandus" in body


def test_saving_the_edit_form_unchanged_retains_muu(signed_in, specialist):
    """Retaining it is the commonest thing somebody does to it."""
    matter = factories.MatterFactory(owner=specialist)
    set_policy_area_other(matter=matter, value="Ringmajandus", actor=specialist)

    signed_in.post(reverse("matters:matter_edit", kwargs={"pk": matter.pk}), edit_payload(matter))

    matter.refresh_from_db()
    assert matter.policy_area_other == "Ringmajandus"
    assert "Muu: Ringmajandus" in detail(signed_in, matter)


def test_changing_muu_on_the_edit_form_changes_what_the_teema_reads(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    set_policy_area_other(matter=matter, value="Ringmajandus", actor=specialist)

    signed_in.post(
        reverse("matters:matter_edit", kwargs={"pk": matter.pk}),
        edit_payload(matter, policy_area_other="Kliimaneutraalsus"),
    )

    matter.refresh_from_db()
    assert matter.policy_area_other == "Kliimaneutraalsus"
    body = detail(signed_in, matter)
    assert "Muu: Kliimaneutraalsus" in body
    assert "Ringmajandus" not in body


def test_clearing_muu_leaves_no_orphaned_text(signed_in, specialist):
    """And the page goes back to saying `Määramata`, which is then true."""
    matter = factories.MatterFactory(owner=specialist)
    set_policy_area_other(matter=matter, value="Ringmajandus", actor=specialist)

    signed_in.post(
        reverse("matters:matter_edit", kwargs={"pk": matter.pk}),
        edit_payload(matter, policy_area_other=""),
    )

    matter.refresh_from_db()
    assert matter.policy_area_other == ""
    body = detail(signed_in, matter)
    assert "Ringmajandus" not in body
    assert "Määramata" in body


def test_the_edit_form_needs_no_invisible_boolean_to_keep_muu():
    """`policy_area_other_selected` is `Uus teema`'s reveal and is not here.

    On the edit page the box is the answer: it is always visible, it holds what
    the record holds, and emptying it is how `Muu` is cleared. Nobody has to
    know about a checkbox that is not on the page (§4).
    """
    assert "policy_area_other_selected" not in MatterEditForm().fields
    assert "policy_area_other" in MatterEditForm().fields


def test_free_text_is_not_a_policy_area_row(signed_in):
    """Muu creates no taxonomy row, so it enters no canonical statistic."""
    before = PolicyArea.objects.count()
    signed_in.post(
        CREATE,
        {
            "title": "Muu ei ole valdkond",
            "policy_area_other_selected": "on",
            "policy_area_other": "Ringmajandus",
        },
    )

    assert PolicyArea.objects.count() == before
    assert not PolicyArea.objects.filter(name_et="Ringmajandus").exists()


# ---------------------------------------------------------------------------
# R2-08 — Õigusakt must have a read surface
# ---------------------------------------------------------------------------


def test_one_oigusakt_reads_on_teema_andmed(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    set_legal_instruments(matter=matter, legal_instruments=[instrument("seadus")], actor=specialist)

    body = detail(signed_in, matter)

    assert "Õigusakt" in body
    assert "Seadus" in body


def test_several_oigusakt_values_all_read(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    set_legal_instruments(
        matter=matter,
        legal_instruments=[instrument("seadus"), instrument("maarus")],
        actor=specialist,
    )

    body = detail(signed_in, matter)

    assert "Seadus" in body
    assert instrument("maarus").label_et in body


def test_muu_oigusakt_reads_with_its_own_text(signed_in, specialist):
    """`Muu` is a vocabulary row here, so the text rides on that row."""
    matter = factories.MatterFactory(owner=specialist)
    set_legal_instruments(matter=matter, legal_instruments=[instrument("muu")], actor=specialist)
    set_legal_instrument_other(matter=matter, value="Rohepöörde kava", actor=specialist)

    assert "Muu: Rohepöörde kava" in detail(signed_in, matter)


def test_oigusakt_survives_a_refresh(signed_in, specialist):
    """It is read from the record, not from anything the save left behind."""
    matter = factories.MatterFactory(owner=specialist)
    set_legal_instruments(matter=matter, legal_instruments=[instrument("seadus")], actor=specialist)

    assert "Seadus" in detail(signed_in, matter)
    assert "Seadus" in detail(signed_in, matter)


def test_a_matter_with_no_oigusakt_prints_no_row(signed_in, specialist):
    """The quiet convention: `Teemaviide`'s, not `+ Lisa`'s.

    The row is read-only — `update_field` does not name `legal_instruments` and
    this change does not add it — so an empty one has no invitation to make and
    is simply absent.
    """
    matter = factories.MatterFactory(owner=specialist)

    assert "Õigusakt" not in detail(signed_in, matter)


def test_oigusakt_reads_in_teema_andmed_and_not_in_ajajoon(signed_in, specialist):
    """It is Matter metadata, not an event: no date, no author, no order."""
    matter = factories.MatterFactory(owner=specialist)
    set_legal_instruments(matter=matter, legal_instruments=[instrument("seadus")], actor=specialist)

    body = detail(signed_in, matter)
    rail = body[body.index('id="teema-andmed"') :].split("</aside>")[0]
    rest = body[: body.index('id="teema-andmed"')]

    assert "Õigusakt" in rail
    assert "Õigusakt" not in rest


# ---------------------------------------------------------------------------
# R2-09 — an empty Tähtaeg editor starts empty
# ---------------------------------------------------------------------------


def test_the_empty_deadline_editor_opens_blank(signed_in, specialist):
    """A response deadline is somebody else's assertion, never a suggestion.

    The control that offers to add one used to arrive holding today's date, so
    the Matter whose defect was that nobody knew the deadline was one Enter away
    from being told it was due today.
    """
    matter = factories.MatterFactory(owner=specialist, response_deadline=None)

    body = detail(signed_in, matter)
    editor = body[body.index("+ Tähtaeg") :].split("</details>")[0]

    assert 'name="response_deadline" value=""' in editor


def test_an_existing_deadline_still_prefills(signed_in, specialist):
    """Only the empty case changed. Editing a date starts from the date."""
    from datetime import date

    matter = factories.MatterFactory(owner=specialist, response_deadline=date(2026, 3, 17))

    body = detail(signed_in, matter)

    assert 'name="response_deadline"' in body
    assert 'value="17.3.2026"' in body


def test_opening_the_editor_writes_nothing(signed_in, specialist):
    """A GET is a GET. Rendering the control cannot have set a deadline."""
    matter = factories.MatterFactory(owner=specialist, response_deadline=None)

    detail(signed_in, matter)

    matter.refresh_from_db()
    assert matter.response_deadline is None


def test_saving_the_editor_blank_leaves_the_matter_without_a_deadline(signed_in, specialist):
    """The existing clear semantics, unchanged — and reachable from blank."""
    matter = factories.MatterFactory(owner=specialist, response_deadline=None)

    signed_in.post(
        reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "response_deadline"}),
        {"response_deadline": ""},
    )

    matter.refresh_from_db()
    assert matter.response_deadline is None


# ---------------------------------------------------------------------------
# R2-12 — Muuda teemat asks the Organisation question the same way
# ---------------------------------------------------------------------------


def test_the_edit_page_renders_the_unified_picker_for_both_fields(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    body = edit_page(signed_in, matter)

    assert 'id="muuda-saatja-valik"' in body
    assert 'id="muuda-adressaat-valik"' in body
    assert "Otsi või lisa asutus…" in body


def test_the_edit_page_no_longer_carries_the_old_three_controls(signed_in, specialist):
    """One concept, one interaction. The disclosure and its twin box are gone."""
    matter = factories.MatterFactory(owner=specialist)

    body = edit_page(signed_in, matter)

    assert "Vali nimekirjast" not in body
    assert 'data-choicefilter="muuda-saatja-nimekiri"' not in body
    assert 'data-choicefilter="muuda-adressaat-nimekiri"' not in body


def test_the_edit_picker_carries_the_recorded_spellings(signed_in, specialist):
    """«MKM» has to find the ministry here as well as on `Uus teema`."""
    organisation = factories.OrganisationFactory(name="Kliimaministeerium")
    organisation.aliases.create(alias="KLIM")
    matter = factories.MatterFactory(owner=specialist)

    body = edit_page(signed_in, matter)

    assert "data-aliases" in body
    assert "klim" in body


def test_the_edit_picker_prepopulates_the_current_selections(signed_in, specialist):
    """An edit page that hides the current answer looks like it cleared it."""
    sender = factories.OrganisationFactory(name="Rahandusministeerium")
    addressee = factories.OrganisationFactory(name="Kliimaministeerium")
    matter = factories.MatterFactory(owner=specialist, addressee_organisation=addressee)
    matter.source_organisations.set([sender])

    form = MatterEditForm(matter=matter, viewer=specialist, initial=edit_initial(matter))
    chosen = [
        choice.data["value"] for choice in form.sender_chip_choices if choice.data["selected"]
    ]
    assert [str(value) for value in chosen] == [str(sender.pk)]

    body = edit_page(signed_in, matter)
    assert "Rahandusministeerium" in body
    assert "Kliimaministeerium" in body


def test_sender_and_addressee_stay_independent_on_the_edit_page(signed_in, specialist):
    """§11. On `Uus teema` a sender may default the addressee; here it may not.

    Both are established business facts by the time this page opens, so adding a
    second sender must leave the recorded addressee exactly where it was.
    """
    first = factories.OrganisationFactory(name="Rahandusministeerium")
    second = factories.OrganisationFactory(name="Justiitsministeerium")
    addressee = factories.OrganisationFactory(name="Kliimaministeerium")
    matter = factories.MatterFactory(owner=specialist, addressee_organisation=addressee)
    matter.source_organisations.set([first])

    signed_in.post(
        reverse("matters:matter_edit", kwargs={"pk": matter.pk}),
        edit_payload(matter, source_organisations=[str(first.pk), str(second.pk)]),
    )

    matter.refresh_from_db()
    assert matter.addressee_organisation_id == addressee.pk
    assert {item.pk for item in matter.source_organisations.all()} == {first.pk, second.pk}


def test_the_edit_form_has_no_addressee_defaulting_machinery():
    """Not suppressed at render time — simply not part of this form."""
    assert "addressee_is_manual" not in MatterEditForm().fields
    assert not hasattr(MatterEditForm(), "addressee_default")


def test_the_edit_page_does_not_mark_the_sender_row_for_the_default(signed_in, specialist):
    """`data-sender-chips` is what `bindAddresseeDefault` binds to."""
    matter = factories.MatterFactory(owner=specialist)

    assert "data-sender-chips" not in edit_page(signed_in, matter)


def test_editing_the_sender_alone_leaves_the_addressee(signed_in, specialist):
    first = factories.OrganisationFactory(name="Rahandusministeerium")
    second = factories.OrganisationFactory(name="Justiitsministeerium")
    addressee = factories.OrganisationFactory(name="Kliimaministeerium")
    matter = factories.MatterFactory(owner=specialist, addressee_organisation=addressee)
    matter.source_organisations.set([first])

    signed_in.post(
        reverse("matters:matter_edit", kwargs={"pk": matter.pk}),
        edit_payload(matter, source_organisations=[str(second.pk)]),
    )

    matter.refresh_from_db()
    assert matter.addressee_organisation_id == addressee.pk
    assert [item.pk for item in matter.source_organisations.all()] == [second.pk]


def test_editing_the_addressee_alone_leaves_the_senders(signed_in, specialist):
    sender = factories.OrganisationFactory(name="Rahandusministeerium")
    was = factories.OrganisationFactory(name="Kliimaministeerium")
    now = factories.OrganisationFactory(name="Sotsiaalministeerium")
    matter = factories.MatterFactory(owner=specialist, addressee_organisation=was)
    matter.source_organisations.set([sender])

    signed_in.post(
        reverse("matters:matter_edit", kwargs={"pk": matter.pk}),
        edit_payload(matter, addressee_organisation=str(now.pk)),
    )

    matter.refresh_from_db()
    assert matter.addressee_organisation_id == now.pk
    assert [item.pk for item in matter.source_organisations.all()] == [sender.pk]


def test_removing_a_sender_on_the_edit_page_removes_only_that_sender(signed_in, specialist):
    first = factories.OrganisationFactory(name="Rahandusministeerium")
    second = factories.OrganisationFactory(name="Justiitsministeerium")
    matter = factories.MatterFactory(owner=specialist)
    matter.source_organisations.set([first, second])

    signed_in.post(
        reverse("matters:matter_edit", kwargs={"pk": matter.pk}),
        edit_payload(matter, source_organisations=[str(first.pk)]),
    )

    matter.refresh_from_db()
    assert [item.pk for item in matter.source_organisations.all()] == [first.pk]


def test_a_provisional_new_sender_is_created_once(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    signed_in.post(
        reverse("matters:matter_edit", kwargs={"pk": matter.pk}),
        edit_payload(matter, sender_name="Eesti Kaubandus-Tööstuskoda"),
    )

    matter.refresh_from_db()
    names = [item.name for item in matter.source_organisations.all()]
    assert names == ["Eesti Kaubandus-Tööstuskoda"]


# ---------------------------------------------------------------------------
# §10 — the edit page must not manufacture a duplicate institution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "typed",
    [
        "Kliimaministeerium",
        "kliimaministeerium",
        "KLIIMAMINISTEERIUM",
        "  Kliimaministeerium  ",
    ],
)
def test_typing_an_existing_name_reuses_the_canonical_row(signed_in, specialist, typed):
    """Exact, case-folded and space-normalised all name the one body."""
    existing = factories.OrganisationFactory(name="Kliimaministeerium")
    matter = factories.MatterFactory(owner=specialist)
    before = Organisation.objects.count()

    signed_in.post(
        reverse("matters:matter_edit", kwargs={"pk": matter.pk}),
        edit_payload(matter, sender_name=typed),
    )

    matter.refresh_from_db()
    assert [item.pk for item in matter.source_organisations.all()] == [existing.pk]
    assert Organisation.objects.count() == before


def test_typing_a_recorded_alias_reuses_the_canonical_row(signed_in, specialist):
    existing = factories.OrganisationFactory(name="Kliimaministeerium")
    existing.aliases.create(alias="KLIM")
    matter = factories.MatterFactory(owner=specialist)
    before = Organisation.objects.count()

    signed_in.post(
        reverse("matters:matter_edit", kwargs={"pk": matter.pk}),
        edit_payload(matter, sender_name="KLIM"),
    )

    matter.refresh_from_db()
    assert [item.pk for item in matter.source_organisations.all()] == [existing.pk]
    assert Organisation.objects.count() == before


def test_a_diacritic_folded_spelling_reuses_the_canonical_row(signed_in, specialist):
    existing = factories.OrganisationFactory(name="Sotsiaalministeerium")
    existing.aliases.create(alias="Sotsiaalministeerium")
    matter = factories.MatterFactory(owner=specialist)
    before = Organisation.objects.count()

    signed_in.post(
        reverse("matters:matter_edit", kwargs={"pk": matter.pk}),
        edit_payload(matter, sender_name="sotsiaalministeerium"),
    )

    matter.refresh_from_db()
    assert [item.pk for item in matter.source_organisations.all()] == [existing.pk]
    assert Organisation.objects.count() == before


def test_an_ambiguous_alias_is_refused_rather_than_guessed(signed_in, specialist):
    """Fail closed. A name that could be two bodies chooses neither."""
    one = factories.OrganisationFactory(name="Esimene amet")
    two = factories.OrganisationFactory(name="Teine amet")
    one.aliases.create(alias="Amet")
    two.aliases.create(alias="Amet")
    matter = factories.MatterFactory(owner=specialist)
    before = Organisation.objects.count()

    response = signed_in.post(
        reverse("matters:matter_edit", kwargs={"pk": matter.pk}),
        edit_payload(matter, sender_name="Amet"),
    )

    matter.refresh_from_db()
    assert list(matter.source_organisations.all()) == []
    assert Organisation.objects.count() == before
    assert response.status_code in (200, 400)


# ---------------------------------------------------------------------------
# R2-13 — a filtered file count has to say it is filtered
# ---------------------------------------------------------------------------


def test_an_unfiltered_file_list_carries_no_filter_chips(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    factories.DocumentFactory(matter=matter, created_by=specialist)

    body = documents(signed_in, matter)

    assert "faili" in body
    assert "filtreeritud" not in body
    assert "filterchip" not in body


def test_the_opinion_redirect_lands_on_a_visibly_filtered_list(signed_in, specialist):
    """The exact state the QA reported: `?roll=arvamus` applied on the reader's
    behalf, with `1 faili` the only thing the heading said about it."""
    matter = factories.MatterFactory(owner=specialist)
    factories.DocumentFactory(
        matter=matter, created_by=specialist, role=DocumentRole.KODA_SUBMISSION_FINAL
    )
    # And two more the filter hides, which is what made «1 faili» misleading.
    factories.DocumentFactory(matter=matter, created_by=specialist)
    factories.DocumentFactory(matter=matter, created_by=specialist)

    body = documents(signed_in, matter, "?roll=arvamus")

    assert "filtreeritud" in body
    assert "filterchip" in body
    assert "Roll:" in body
    assert "Arvamus" in body


def test_the_filter_chip_links_back_to_the_whole_list(signed_in, specialist):
    """«How do I get back to all files» must be answerable from the chip."""
    matter = factories.MatterFactory(owner=specialist)
    factories.DocumentFactory(
        matter=matter, created_by=specialist, role=DocumentRole.KODA_SUBMISSION_FINAL
    )

    body = documents(signed_in, matter, "?roll=arvamus")
    chip = body[body.index("filterchip") :].split("</a>")[0]

    assert 'href="?"' in chip


def test_a_saved_stored_role_link_names_the_filter_that_ran(signed_in, specialist):
    """`?roll=KODA_SUBMISSION_FINAL` is read as the opinion union, so the chip
    says `Arvamus` rather than echoing a parameter the page is not running."""
    matter = factories.MatterFactory(owner=specialist)
    factories.DocumentFactory(
        matter=matter, created_by=specialist, role=DocumentRole.KODA_SUBMISSION_FINAL
    )

    body = documents(signed_in, matter, "?roll=KODA_SUBMISSION_FINAL")

    assert "KODA_SUBMISSION_FINAL" not in body.split("filterchip")[1].split("</a>")[0]
    assert "Roll:" in body


def test_a_search_filter_is_named_too(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    factories.DocumentFactory(matter=matter, created_by=specialist, title="Koostööleping")

    body = documents(signed_in, matter, "?otsi=leping")

    assert "Otsing:" in body
    assert "leping" in body


# ---------------------------------------------------------------------------
# R2-14 — the chooser and the submit are two controls with two names
# ---------------------------------------------------------------------------


def test_the_opinion_upload_controls_have_distinct_visible_labels(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    factories.SubmissionFactory(matter=matter, created_by=specialist)

    body = documents(signed_in, matter)

    assert ">Vali fail<" in body
    assert ">Lisa fail<" in body
    # One submit, and its name is not the chooser's.
    assert body.count(">Lisa fail<") == 1


def test_the_opinion_upload_controls_have_distinct_accessible_names(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    submission = factories.SubmissionFactory(matter=matter, created_by=specialist)

    body = documents(signed_in, matter)

    assert f'aria-label="Vali lõplik saadetud fail – {submission.title}"' in body
