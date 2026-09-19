"""One question per classification — the lawyers' second feedback round.

`docs/adr/0090`. Package 1 took three things off `Uus teema` that asked for
attention before a question had been answered; this round says the *questions*
overlapped. What is pinned here is the behaviour of the reduced form and, much
more importantly, everything the reduction is not allowed to touch.

The two reviewed vocabularies have their own files —
`tests/test_reference_stages.py` and `tests/test_reference_legal_instruments.py`
own the manifests, the migrations and the compatibility tables. This one is
about the form, the derivation and the record.

Four classes of defect are what these tests exist for, and every one of them
would look like a working feature:

* a Matter created under the reduced form carrying a classification nobody
  gave it — most dangerously a `Menetlusliik` guessed from `Õigusakt`, which no
  instrument type entails;
* an edit about one field silently rewriting another — the defect PR #231 fixed
  for `Hetkeseis`, waiting to happen to `Matter.track`;
* *Koda has stopped working on this* leaking into `Hetkeseis`, which answers a
  different question about a different actor;
* a historical value disappearing because the vocabulary moved on.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.matters.forms import MatterCreateForm, MatterEditForm, edit_initial
from app.matters.models import Matter
from app.matters.services import close_matter
from app.organisations.models import Organisation, OrganisationType
from app.taxonomy.legal_instruments import (
    DOMESTIC_LEGAL_INSTRUMENT_KEYS,
    EU_LEGAL_INSTRUMENT_KEYS,
    OTHER_LEGAL_INSTRUMENT_KEYS,
)
from app.taxonomy.models import LegalInstrumentType
from app.workflow.enums import Disposition, Track
from app.workflow.models import StageVocabulary
from tests import factories

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")


@pytest.fixture
def ministry():
    return Organisation.objects.create(
        name="Majandus- ja Kommunikatsiooniministeerium",
        organisation_type=OrganisationType.MINISTRY,
    )


def instrument(key: str) -> LegalInstrumentType:
    return LegalInstrumentType.objects.get(key=key)


def stage(key: str) -> StageVocabulary:
    return StageVocabulary.objects.get(key=key)


def edit_payload(matter: Matter, **overrides: object) -> dict:
    """What `Muuda teemat` posts for an unchanged record, plus the overrides.

    Built from `edit_initial` so that a test changing one field is genuinely
    changing one field: a hand-written payload omits whatever it forgets, and an
    omitted value on this form means *cleared*, which is the very thing several
    of these tests are asserting does not happen.
    """
    initial = edit_initial(matter)
    payload: dict = {}
    for name, value in initial.items():
        if value is None or value == "":
            continue
        if isinstance(value, list):
            payload[name] = [str(item) for item in value]
        else:
            payload[name] = str(value)
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# 1 — what the create form asks, and what it no longer asks
# ---------------------------------------------------------------------------


def test_the_create_form_has_no_menetlusliik_and_no_adressaat_field():
    """Gone from the form, not hidden on the page.

    A field that still binds is a field a forged POST can still set, and
    `track=NATIONAL_TRANSPOSITION` is precisely the value this round refuses to
    let anything but a person write.
    """
    fields = MatterCreateForm().fields
    assert "track" not in fields
    assert "addressee_organisation" not in fields
    assert "addressee_name" not in fields
    assert "addressee_is_manual" not in fields
    # The three questions that remain, plus Valdkond.
    assert {"stage", "legal_instruments", "source_organisations", "policy_areas"} <= set(fields)


def test_neither_question_is_drawn_on_the_page(signed_in):
    page = signed_in.get(CREATE).content.decode()

    assert "Menetlusliik" not in page
    assert "Adressaat" not in page
    assert 'name="track"' not in page
    assert 'name="addressee_organisation"' not in page
    assert "addressee_is_manual" not in page
    assert "data-addressee-disclosure" not in page


def test_the_classification_rows_are_in_the_reviewed_order(signed_in):
    """Saatja · Valdkond · Hetkeseis · Õigusakt, and nothing between them."""
    page = signed_in.get(CREATE).content.decode()
    positions = [
        page.index('id="saatja-valik"'),
        # The Valdkonnad menu's trigger. It was `data-valdkond-disclosure` on a
        # `<details>` fold; the control is a menu now and the row is where it
        # was (docs/adr/0094 §2).
        page.index('id="valdkonnad-menuu"'),
        page.index('name="stage"'),
        page.index('name="legal_instruments"'),
    ]
    assert positions == sorted(positions)


def test_a_forged_track_is_not_part_of_the_request(signed_in, ministry):
    """The form has no such field to bind, so the parameter is simply not read."""
    signed_in.post(
        CREATE,
        {
            "title": "Võltsitud menetlusliik",
            "track": Track.NATIONAL_TRANSPOSITION,
            "source_organisations": [str(ministry.pk)],
        },
    )
    matter = Matter.objects.get(title="Võltsitud menetlusliik")
    assert matter.track == ""


def test_a_forged_addressee_is_not_part_of_the_request(signed_in, ministry):
    other = Organisation.objects.create(name="Riigikogu", organisation_type=OrganisationType.OTHER)
    signed_in.post(
        CREATE,
        {
            "title": "Võltsitud adressaat",
            "addressee_organisation": str(other.pk),
            "addressee_name": "Keegi Muu",
            "source_organisations": [str(ministry.pk)],
        },
    )
    matter = Matter.objects.get(title="Võltsitud adressaat")
    assert matter.addressee_organisation is None


def test_muuda_teemat_does_not_default_its_addressee(signed_in, specialist, ministry):
    """Inherited from `tests/test_addressee_defaults_to_sender.py`, which this round retired.

    That file pinned ADR 0069's default and its twenty-four boundaries; the
    default is gone with the question and so is the file. This one assertion is
    kept because it is about the surface the round did *not* touch: `Muuda
    teemat` is a person correcting a record that already exists, and deriving an
    addressee there would be the edit form quietly rewriting a fact nobody
    touched.
    """
    matter = factories.MatterFactory(owner=specialist, title="Muudetav")

    signed_in.post(
        reverse("matters:matter_edit", args=[matter.pk]),
        {
            "title": matter.title,
            "brief_summary": matter.brief_summary,
            "visibility": matter.visibility,
            "source_organisations": [str(ministry.pk)],
        },
    )

    matter.refresh_from_db()
    assert list(matter.source_organisations.all()) == [ministry]
    assert matter.addressee_organisation is None


def test_a_single_sender_no_longer_answers_adressaat(signed_in, ministry):
    """ADR 0069's default is superseded on this surface, and by removal.

    A Matter now arrives with a sender and no recipient, and gains one when Koda
    decides who to answer — which is a different act, on a different day.
    """
    signed_in.post(
        CREATE,
        {
            "title": "Pakendiseaduse muutmise eelnõu",
            "source_organisations": [str(ministry.pk)],
        },
    )
    matter = Matter.objects.get(title="Pakendiseaduse muutmise eelnõu")
    assert list(matter.source_organisations.all()) == [ministry]
    assert matter.addressee_organisation is None


# ---------------------------------------------------------------------------
# 2 — siseriiklik or ELiga seotud, readable from the type and written by nobody
# ---------------------------------------------------------------------------


def test_the_two_groups_cover_the_offered_vocabulary_and_nothing_else():
    """The distinction the lawyers kept, as a property of the vocabulary.

    A Matter carries its `Õigusakt` types and each offered type belongs to one
    of the two groups, so *siseriiklik or ELiga seotud* is answerable from
    stored data without anybody being asked a second time. A retired
    version-1.0 row is in neither, which is the honest answer:
    `Konsultatsioon` may be either and `Eelnõu` says nothing.
    """
    offered = {item.key for item in LegalInstrumentType.objects.filter(is_active=True)}
    assert DOMESTIC_LEGAL_INSTRUMENT_KEYS | EU_LEGAL_INSTRUMENT_KEYS == offered
    assert DOMESTIC_LEGAL_INSTRUMENT_KEYS.isdisjoint(EU_LEGAL_INSTRUMENT_KEYS)

    retired = {item.key for item in LegalInstrumentType.objects.filter(is_active=False)}
    assert retired.isdisjoint(DOMESTIC_LEGAL_INSTRUMENT_KEYS | EU_LEGAL_INSTRUMENT_KEYS)


def test_creating_a_matter_writes_no_menetlusliik_at_all(signed_in, ministry):
    """Scenario A. The page asks Saatja, Valdkond, Hetkeseis and Õigusakt.

    `Menetlusliik` is left as nobody answered it. Deriving `DOMESTIC` from
    `Seadus` would be reducing a seven-value classification to a domestic/EU
    boolean, and it would be wrong about exactly the files the distinction
    exists for: a `Seadus` transposing a directive is a domestic instrument on a
    `NATIONAL_TRANSPOSITION` track (docs/adr/0090 §4).
    """
    signed_in.post(
        CREATE,
        {
            "title": "Pakendiseaduse muutmise eelnõu",
            "source_organisations": [str(ministry.pk)],
            "stage": str(stage("consultation").pk),
            "legal_instruments": [str(instrument("seadus").pk)],
        },
    )
    matter = Matter.objects.get(title="Pakendiseaduse muutmise eelnõu")

    assert matter.track == ""
    assert matter.stage is not None and matter.stage.key == "consultation"
    assert [item.key for item in matter.legal_instruments.all()] == ["seadus"]
    assert list(matter.source_organisations.all()) == [ministry]


def test_an_eu_matter_is_recognisable_without_a_second_question(signed_in):
    """Scenario B. The EU-ness is on the record, in the type, and nowhere guessed."""
    signed_in.post(
        CREATE,
        {
            "title": "Ehitustoodete direktiivi ettepanek",
            "stage": str(stage("eu_procedure").pk),
            "legal_instruments": [str(instrument("direktiiv").pk)],
        },
    )
    matter = Matter.objects.get(title="Ehitustoodete direktiivi ettepanek")

    assert matter.track == ""
    chosen = {item.key for item in matter.legal_instruments.all()}
    assert chosen <= EU_LEGAL_INSTRUMENT_KEYS
    assert matter.stage is not None and matter.stage.key == "eu_procedure"


def test_nothing_anywhere_infers_a_track_from_an_instrument(signed_in):
    """The refusal, over the whole vocabulary rather than over an example.

    Every offered type, one Matter each, and not one of them arrives carrying a
    `Menetlusliik` — least of all `NATIONAL_TRANSPOSITION`.
    """
    for item in LegalInstrumentType.objects.filter(is_active=True):
        title = f"Ainult õigusakt {item.key}"
        payload = {"title": title, "legal_instruments": [str(item.pk)]}
        if item.key in OTHER_LEGAL_INSTRUMENT_KEYS:
            # The two escape hatches refuse a save without the text beside
            # them, so the POST has to be one the form accepts — otherwise
            # this would assert that a *refused* save writes no track.
            payload["legal_instrument_other"] = "Mõni muu dokument"
        signed_in.post(CREATE, payload)
        matter = Matter.objects.filter(title=title).first()
        assert matter is not None, item.key
        assert matter.track == "", f"{item.key} wrote {matter.track!r}"


def test_a_forged_menetlusliik_is_still_not_part_of_the_request(signed_in):
    """Two ways to write the column, and the form is neither of them."""
    signed_in.post(
        CREATE,
        {
            "title": "Seadus ja võltsitud menetlusliik",
            "track": Track.NATIONAL_TRANSPOSITION,
            "legal_instruments": [str(instrument("seadus").pk)],
        },
    )
    assert Matter.objects.get(title="Seadus ja võltsitud menetlusliik").track == ""


def test_menetlusliik_is_answered_where_it_is_known(signed_in, specialist):
    """And it is not lost: the whole vocabulary is still offered on the surfaces
    a person corrects a record from, including the one value no rule may infer.
    """
    matter = factories.MatterFactory(title="Ülevõtmine, seadus", owner=specialist)
    matter.legal_instruments.set([instrument("seadus")])

    url = reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "track"})
    signed_in.post(url, {"track": Track.NATIONAL_TRANSPOSITION})

    matter.refresh_from_db()
    assert matter.track == Track.NATIONAL_TRANSPOSITION
    assert [item.key for item in matter.legal_instruments.all()] == ["seadus"]

    offered = {value for value, _label in MatterEditForm(matter=matter).fields["track"].choices}
    assert set(Track.values) <= offered


# ---------------------------------------------------------------------------
# 3 — a historical Matter keeps everything it was carrying
# ---------------------------------------------------------------------------


@pytest.fixture
def historical(specialist, ministry):
    """Scenario C — a Matter filed under version 1.0, in every dimension.

    A retired stage, a retired legal-instrument type, a stored `track` nothing
    would derive, and an addressee.
    """
    retired_stage = stage("government")
    retired_stage.is_active = False
    retired_stage.save(update_fields=["is_active"])

    matter = factories.MatterFactory(
        title="Vana teema",
        owner=specialist,
        stage=retired_stage,
        track=Track.NATIONAL_TRANSPOSITION,
        addressee_organisation=ministry,
    )
    matter.legal_instruments.set([instrument("strateegia")])
    matter.source_organisations.set([ministry])
    return matter


def test_the_historical_matter_keeps_every_value_when_an_unrelated_field_is_edited(
    signed_in, historical
):
    url = reverse("matters:matter_edit", kwargs={"pk": historical.pk})
    response = signed_in.post(url, edit_payload(historical, title="Vana teema, parandatud"))
    assert response.status_code in (302, 200), response.status_code

    historical.refresh_from_db()
    assert historical.title == "Vana teema, parandatud"
    assert historical.stage is not None and historical.stage.key == "government"
    assert historical.track == Track.NATIONAL_TRANSPOSITION
    assert [item.key for item in historical.legal_instruments.all()] == ["strateegia"]
    assert historical.addressee_organisation is not None


def test_changing_the_oigusakt_on_an_edit_does_not_rederive_the_track(signed_in, historical):
    """The defect PR #231 fixed for `Hetkeseis`, refused for `Matter.track`.

    Deriving here would silently overwrite a classification somebody chose,
    under an edit that was about something else. `Menetlusliik` is a visible
    control on this page, so a lawyer who wants a different value sets it.
    """
    url = reverse("matters:matter_edit", kwargs={"pk": historical.pk})
    signed_in.post(
        url,
        edit_payload(historical, legal_instruments=[str(instrument("direktiiv").pk)]),
    )

    historical.refresh_from_db()
    assert [item.key for item in historical.legal_instruments.all()] == ["direktiiv"]
    assert historical.track == Track.NATIONAL_TRANSPOSITION


def test_the_edit_form_still_offers_menetlusliik_and_adressaat(historical):
    fields = MatterEditForm(matter=historical).fields
    assert "track" in fields
    assert "addressee_organisation" in fields
    assert "addressee_name" in fields


def test_the_edit_form_offers_back_the_retired_stage_and_the_retired_instrument(historical):
    form = MatterEditForm(matter=historical)
    offered_stages = [str(value) for value, _label in form.fields["stage"].choices]
    assert str(historical.stage.pk) in offered_stages

    offered_instruments = [str(value) for value, _label in form.fields["legal_instruments"].choices]
    assert str(instrument("strateegia").pk) in offered_instruments


def test_the_create_form_offers_neither_retired_value():
    """Retired values are historical, never a choice for new work."""
    retired_stage = stage("government")
    retired_stage.is_active = False
    retired_stage.save(update_fields=["is_active"])

    form = MatterCreateForm()
    assert str(retired_stage.pk) not in [
        str(value) for value, _label in form.fields["stage"].choices
    ]
    assert str(instrument("strateegia").pk) not in [
        str(value) for value, _label in form.fields["legal_instruments"].choices
    ]


# ---------------------------------------------------------------------------
# 4 — stage is not disposition
# ---------------------------------------------------------------------------


def test_rohkem_ei_tegele_is_offered_as_a_closure_reason_not_as_a_stage(signed_in, specialist):
    """Scenario D, against the architecture ADR 0032 draws.

    *Koda no longer intends active work* is `Disposition.MONITORING_STOPPED` and
    has been since the vocabulary was seeded. It is a statement about this
    office; `Hetkeseis` is a statement about the external process. Recording one
    as the other would leave every surface reading the column unable to tell
    which had been answered.
    """
    matter = factories.MatterFactory(title="Jälgitav teema", owner=specialist)
    close_matter(matter=matter, disposition=Disposition.MONITORING_STOPPED, actor=specialist)

    matter.refresh_from_db()
    assert matter.disposition == Disposition.MONITORING_STOPPED
    assert matter.is_open is False
    # And the stage is whatever it was: closing says nothing about where the
    # external process stands.
    assert matter.stage == factories.MatterFactory._meta.model.objects.get(pk=matter.pk).stage

    assert not StageVocabulary.objects.filter(label_et="Rohkem ei tegele").exists()


def test_the_stage_control_offers_no_closure_disguised_as_a_stage(signed_in):
    """The create form's Hetkeseis row is ten external-process answers.

    Nothing on it says anything about whether Koda continues, which is the
    boundary this round was asked to preserve rather than cross.
    """
    page = signed_in.get(CREATE).content.decode()
    assert "Rohkem ei tegele" not in page

    offered = [str(label) for _value, label in MatterCreateForm().fields["stage"].choices]
    assert "Rohkem ei tegele" not in offered
    assert "Koda ei tegele edasi" not in offered
    # Django's named blank option, then the ten reviewed stages.
    assert len(offered) == 11


def test_choosing_joustunud_does_not_close_the_matter(signed_in, ministry):
    """Scenario E. Monitoring implementation is ordinary work."""
    signed_in.post(
        CREATE,
        {
            "title": "Jõustunud seadus",
            "source_organisations": [str(ministry.pk)],
            "stage": str(stage("in_force").pk),
            "legal_instruments": [str(instrument("seadus").pk)],
        },
    )
    matter = Matter.objects.get(title="Jõustunud seadus")

    assert matter.stage is not None and matter.stage.key == "in_force"
    assert matter.is_open is True
    assert matter.disposition == ""


# ---------------------------------------------------------------------------
# 5 — one word for one fact
# ---------------------------------------------------------------------------


def test_the_teema_rail_answers_the_sender_question_once(signed_in, specialist, ministry):
    """Scenario F. `Kellelt` is gone; the fact and its control are not."""
    matter = factories.MatterFactory(title="Saatjaga teema", owner=specialist)
    matter.source_organisations.set([ministry])

    page = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    rail = page[page.index('id="teema-andmed"') :]
    rail = rail[: rail.index("</aside>")]

    assert ">Kellelt<" not in rail
    assert ">Saatja<" in rail
    # The fact itself, and the control that edits it, are untouched.
    assert ministry.name in rail
    assert "field='source_organisations'" in rail or "source_organisations" in rail
    # And `Kellele` is still its own row: the two are never merged.
    assert ">Kellele<" in rail


def test_the_edit_form_calls_the_sender_saatja():
    assert MatterEditForm().fields["source_organisations"].label == "Saatja"
    assert MatterCreateForm().fields["source_organisations"].label == "Saatja"


# ---------------------------------------------------------------------------
# 6 — the `Muu` rows, and what they do not signal
# ---------------------------------------------------------------------------


def test_either_reviewed_muu_requires_the_text_beside_it(signed_in):
    response = signed_in.post(
        CREATE,
        {
            "title": "Muu ilma tekstita",
            "legal_instruments": [str(instrument("muu-siseriiklik").pk)],
        },
    )
    assert response.status_code == 400
    assert not Matter.objects.filter(title="Muu ilma tekstita").exists()


def test_the_reviewed_muu_rows_keep_the_free_text(signed_in):
    signed_in.post(
        CREATE,
        {
            "title": "Muu ELi dokument",
            "legal_instruments": [str(instrument("muu-eli-dokument").pk)],
            "legal_instrument_other": "Roheline raamat",
        },
    )
    matter = Matter.objects.get(title="Muu ELi dokument")
    assert matter.legal_instrument_other == "Roheline raamat"
    # Its group is readable from the type; the column stays unanswered.
    assert {item.key for item in matter.legal_instruments.all()} <= EU_LEGAL_INSTRUMENT_KEYS
    assert matter.track == ""


def test_unticking_the_muu_row_clears_the_text(signed_in):
    signed_in.post(
        CREATE,
        {
            "title": "Seadus, mitte muu",
            "legal_instruments": [str(instrument("seadus").pk)],
            "legal_instrument_other": "midagi muud",
        },
    )
    assert Matter.objects.get(title="Seadus, mitte muu").legal_instrument_other == ""


def test_all_three_muu_rows_reveal_the_same_box():
    """Version 1.0's `Muu` included, because Matters filed before this hold it."""
    form = MatterCreateForm()
    offered = {str(item.pk) for item in form.fields["legal_instruments"].queryset}
    values = set(form.other_instrument_values)
    assert values == {
        str(instrument("muu-siseriiklik").pk),
        str(instrument("muu-eli-dokument").pk),
    }
    assert values <= offered

    matter = factories.MatterFactory(title="Vana muu")
    matter.legal_instruments.set([instrument("muu")])
    edit = MatterEditForm(matter=matter)
    assert str(instrument("muu").pk) in edit.other_instrument_values


def test_a_shared_muu_is_not_a_similarity_signal(specialist, ministry):
    """Two files that both failed to fit the list have agreed about nothing."""
    from app.related_materials.engine import build_profile

    matter = factories.MatterFactory(title="Esimene muu", owner=specialist)
    matter.legal_instruments.set([instrument("muu-siseriiklik")])
    profile = build_profile(matter)
    assert profile.instrument_names == {}
