"""One question per classification — the lawyers' second feedback round.

`docs/adr/0089`. Package 1 took three things off `Uus teema` that asked for
attention before a question had been answered; this round says the *questions*
overlapped. What is pinned here is the behaviour of the reduced form and, much
more importantly, everything the reduction is not allowed to touch.

The two reviewed vocabularies have their own files —
`tests/test_reference_stages.py` and `tests/test_reference_legal_instruments.py`
own the manifests, the migrations and the compatibility tables. This one is
about the form, the derivation and the record.

Four classes of defect are what these tests exist for, and every one of them
would look like a working feature:

* a Matter created under the new form carrying a *derived* classification that
  is not true of it — most dangerously `ELi õiguse ülevõtmine`, which no
  instrument type entails;
* an edit about one field silently rewriting another — the defect PR #231 fixed
  for `Hetkeseis`, waiting to happen to `Matter.track`;
* a stage that reads like a closure quietly becoming one;
* a historical value disappearing because the vocabulary moved on.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.matters.forms import MatterCreateForm, MatterEditForm, edit_initial
from app.matters.models import Matter
from app.matters.services import close_matter, derived_track
from app.organisations.models import Organisation, OrganisationType
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
        page.index("data-valdkond-disclosure"),
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
# 2 — siseriiklik or ELiga seotud, derived and refused
# ---------------------------------------------------------------------------


def test_the_domestic_types_derive_the_domestic_track():
    for key in ("vtk", "seadus", "maarus", "koja-ettepanek", "muu-siseriiklik"):
        assert derived_track([instrument(key)]) == Track.DOMESTIC


def test_the_eu_types_derive_the_eu_track():
    for key in ("eli-konsultatsioon", "direktiiv", "el-maarus", "muu-eli-dokument"):
        assert derived_track([instrument(key)]) == Track.EU_INITIATIVE


def test_two_types_from_one_group_still_derive_that_group():
    assert derived_track([instrument("seadus"), instrument("maarus")]) == Track.DOMESTIC
    assert derived_track([instrument("direktiiv"), instrument("el-maarus")]) == Track.EU_INITIATIVE


def test_a_mixed_answer_derives_nothing():
    """A file can concern a directive and the act transposing it.

    That is two answers about two instruments, not one about the procedure, so
    the deterministic reading of a mixed set is *no answer*.
    """
    assert derived_track([instrument("seadus"), instrument("direktiiv")]) == ""


def test_a_retired_type_derives_nothing():
    """`Konsultatsioon` may be European or domestic and `Eelnõu` says nothing."""
    for key in ("konsultatsioon", "eelnou", "strateegia", "muu"):
        assert derived_track([instrument(key)]) == ""
    assert derived_track([instrument("seadus"), instrument("eelnou")]) == ""


def test_no_answer_derives_nothing():
    assert derived_track([]) == ""


def test_transposition_is_never_derived():
    """A `Seadus` implementing a directive is a domestic legal instrument.

    Whether the *procedure* is a transposition is a separate fact that no
    instrument type entails, which is why ADR 0070 keeps the two apart.
    """
    every = list(LegalInstrumentType.objects.all())
    for item in every:
        assert derived_track([item]) != Track.NATIONAL_TRANSPOSITION
    for left in every:
        for right in every:
            assert derived_track([left, right]) != Track.NATIONAL_TRANSPOSITION


def test_only_two_of_the_seven_tracks_are_ever_derived():
    """The mismatch ADR 0089 §4 documents rather than resolves.

    `Track`'s seven values are not a clean domestic/EU axis. Deriving the more
    specific `KODA_INITIATIVE` or `STRATEGY` would stop a Koda proposal reading
    as domestic, which is the opposite of what the lawyers asked to keep.
    """
    every = list(LegalInstrumentType.objects.all())
    derived = {derived_track([item]) for item in every}
    assert derived <= {"", Track.DOMESTIC, Track.EU_INITIATIVE}


def test_creating_a_domestic_matter_records_the_domestic_track(signed_in, ministry):
    """Scenario A — an ordinary incoming draft."""
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
    assert matter.track == Track.DOMESTIC
    assert matter.stage is not None and matter.stage.key == "consultation"
    assert [item.key for item in matter.legal_instruments.all()] == ["seadus"]
    assert list(matter.source_organisations.all()) == [ministry]


def test_creating_an_eu_matter_records_the_eu_track(signed_in):
    """Scenario B — no duplicate EU question, and no invented transposition."""
    signed_in.post(
        CREATE,
        {
            "title": "Ehitustoodete määruse ettepanek",
            "stage": str(stage("eu_procedure").pk),
            "legal_instruments": [str(instrument("direktiiv").pk)],
        },
    )
    matter = Matter.objects.get(title="Ehitustoodete määruse ettepanek")
    assert matter.track == Track.EU_INITIATIVE
    assert matter.track != Track.NATIONAL_TRANSPOSITION
    assert matter.stage is not None and matter.stage.key == "eu_procedure"


def test_creating_with_a_mixed_answer_leaves_the_track_unanswered(signed_in):
    signed_in.post(
        CREATE,
        {
            "title": "Direktiiv ja seda üle võttev seadus",
            "legal_instruments": [
                str(instrument("seadus").pk),
                str(instrument("direktiiv").pk),
            ],
        },
    )
    matter = Matter.objects.get(title="Direktiiv ja seda üle võttev seadus")
    assert matter.track == ""


def test_creating_without_an_oigusakt_leaves_the_track_unanswered(signed_in):
    signed_in.post(CREATE, {"title": "Ainult pealkiri"})
    assert Matter.objects.get(title="Ainult pealkiri").track == ""


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


def test_choosing_rohkem_ei_tegele_does_not_close_the_matter(signed_in, ministry):
    """Scenario D. The stage records where Koda's attention is, and nothing else."""
    signed_in.post(
        CREATE,
        {
            "title": "Teema, millega enam ei tegele",
            "source_organisations": [str(ministry.pk)],
            "stage": str(stage("no_further_work").pk),
        },
    )
    matter = Matter.objects.get(title="Teema, millega enam ei tegele")

    assert matter.stage is not None and matter.stage.key == "no_further_work"
    assert matter.is_open is True
    assert matter.closed_at is None
    assert matter.disposition == ""
    assert matter.record_mode == "FULL"


def test_moving_an_existing_matter_to_rohkem_ei_tegele_does_not_close_it(signed_in, specialist):
    matter = factories.MatterFactory(title="Jälgitav teema", owner=specialist)
    url = reverse(
        "matters:update_field",
        kwargs={"pk": matter.pk, "field": "stage"},
    )
    signed_in.post(url, {"stage": str(stage("no_further_work").pk)})

    matter.refresh_from_db()
    assert matter.stage is not None and matter.stage.key == "no_further_work"
    assert matter.is_open is True
    assert matter.disposition == ""


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


def test_a_closed_matter_and_the_new_stage_stay_distinguishable(specialist):
    """The two concepts are stored separately and neither implies the other."""
    matter = factories.MatterFactory(
        title="Suletud teema", owner=specialist, stage=stage("no_further_work")
    )
    close_matter(matter=matter, disposition=Disposition.MONITORING_STOPPED, actor=specialist)

    matter.refresh_from_db()
    assert matter.stage is not None and matter.stage.key == "no_further_work"
    assert matter.disposition == Disposition.MONITORING_STOPPED
    assert matter.is_open is False


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
    assert matter.track == Track.EU_INITIATIVE


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
