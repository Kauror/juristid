"""Õigusakt as a Matter fact: created, refused, corrected, audited.

The vocabulary and the historical mapping are pinned in
`tests/test_reference_legal_instruments.py`. What is here is the other half —
what a POST from `Uus teema` and from `Muuda teemat` actually leaves on a
Matter, and what it must leave *absent*.

Two invariants run through nearly every test below:

* **`Menetlusliik` and `Õigusakt` are independent.** They are two facts about
  one file and neither is a filter over the other, so a Matter can hold
  `ELi õiguse ülevõtmine` and `Seadus` at once, and changing either leaves the
  other exactly where it was (docs/adr/0070 §1).
* **`Muu` is an answer, not a hiding place.** Ticked, it demands the free text;
  unticked, it takes the free text with it, whatever the box still holds.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.forms import MatterCreateForm, MatterEditForm, edit_initial
from app.matters.models import Matter
from app.matters.services import (
    create_matter,
    set_legal_instrument_other,
    set_legal_instruments,
)
from app.taxonomy.models import LegalInstrumentType, PolicyArea, Tag
from app.workflow.enums import Track
from tests import factories

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")


def instrument(key: str) -> LegalInstrumentType:
    return LegalInstrumentType.objects.get(key=key)


def edit_url(matter: Matter) -> str:
    return reverse("matters:matter_edit", kwargs={"pk": matter.pk})


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
# Creating
# ---------------------------------------------------------------------------


def test_a_title_alone_leaves_oigusakt_empty(signed_in):
    """Blank stays valid. Only Pealkiri is ever required (task §6)."""
    signed_in.post(CREATE, {"title": "Ainult pealkiri"})

    matter = Matter.objects.get(title="Ainult pealkiri")
    assert list(matter.legal_instruments.all()) == []
    assert matter.legal_instrument_other == ""


def test_one_instrument_persists(signed_in):
    seadus = instrument("seadus")
    signed_in.post(CREATE, {"title": "Üks liik", "legal_instruments": [str(seadus.pk)]})

    matter = Matter.objects.get(title="Üks liik")
    assert [item.key for item in matter.legal_instruments.all()] == ["seadus"]


def test_several_instruments_persist(signed_in):
    """The whole reason the field is many-to-many (task §6).

    A package that amends an Act and a Regulation together is ordinary work,
    and the historical register writes exactly that as `S, M`.
    """
    chosen = [instrument("seadus"), instrument("maarus")]
    signed_in.post(
        CREATE,
        {"title": "Kaks liiki", "legal_instruments": [str(item.pk) for item in chosen]},
    )

    matter = Matter.objects.get(title="Kaks liiki")
    assert {item.key for item in matter.legal_instruments.all()} == {"seadus", "maarus"}


def test_the_same_instrument_twice_is_one_relation(signed_in):
    seadus = instrument("seadus")
    signed_in.post(
        CREATE,
        {"title": "Kaks korda üks", "legal_instruments": [str(seadus.pk), str(seadus.pk)]},
    )

    matter = Matter.objects.get(title="Kaks korda üks")
    assert matter.legal_instruments.count() == 1


def test_muu_with_its_text_persists(signed_in):
    muu = instrument("muu")
    signed_in.post(
        CREATE,
        {
            "title": "Muu liik",
            "legal_instruments": [str(muu.pk)],
            "legal_instrument_other": "  Komisjoni soovitus  ",
        },
    )

    matter = Matter.objects.get(title="Muu liik")
    assert [item.key for item in matter.legal_instruments.all()] == ["muu"]
    assert matter.legal_instrument_other == "Komisjoni soovitus"


def test_muu_without_its_text_is_refused_on_the_empty_box(signed_in):
    muu = instrument("muu")
    response = signed_in.post(
        CREATE,
        {
            "title": "Muu ilma tekstita",
            "legal_instruments": [str(muu.pk)],
            "legal_instrument_other": "",
        },
    )

    assert response.status_code == 400
    assert not Matter.objects.filter(title="Muu ilma tekstita").exists()
    form = response.context["form"]
    assert "legal_instrument_other" in form.errors
    assert "legal_instruments" not in form.errors, "the refusal names the box that is empty"


def test_text_without_muu_is_not_persisted(signed_in):
    """Free text belongs to the chip that reveals it (task §8, §17).

    Unticking `Muu` and leaving the box full must not quietly save what is in
    it — the same rule `policy_area_other` follows.
    """
    seadus = instrument("seadus")
    signed_in.post(
        CREATE,
        {
            "title": "Tekst ilma Muuta",
            "legal_instruments": [str(seadus.pk)],
            "legal_instrument_other": "midagi muud",
        },
    )

    matter = Matter.objects.get(title="Tekst ilma Muuta")
    assert matter.legal_instrument_other == ""


def test_text_alone_with_nothing_ticked_is_not_persisted(signed_in):
    signed_in.post(CREATE, {"title": "Ainult tekst", "legal_instrument_other": "midagi muud"})

    matter = Matter.objects.get(title="Ainult tekst")
    assert matter.legal_instrument_other == ""
    assert list(matter.legal_instruments.all()) == []


def test_a_refused_save_keeps_every_choice_and_leaves_the_box_open(signed_in):
    """§8: on a refused save Muu stays ticked, the text stays, the box shows.

    The last of those is what makes the refusal legible without scripting: a
    reveal only JavaScript can open would hide the error behind a click.
    """
    muu = instrument("muu")
    seadus = instrument("seadus")
    response = signed_in.post(
        CREATE,
        {
            "title": "",  # the refusal
            "legal_instruments": [str(seadus.pk), str(muu.pk)],
            "legal_instrument_other": "Komisjoni soovitus",
        },
    )

    assert response.status_code == 400
    form = response.context["form"]
    chosen = {str(value) for value in form["legal_instruments"].value()}
    assert {str(seadus.pk), str(muu.pk)} <= chosen
    assert form["legal_instrument_other"].value() == "Komisjoni soovitus"
    assert form.other_instrument_open is True
    assert form.other_instrument_value == str(muu.pk)

    body = response.content.decode()
    assert 'id="oigusakt-muu-tekst"' in body
    assert 'id="oigusakt-muu-tekst"\n             hidden' not in body


def test_a_refused_muu_save_shows_the_error_inside_the_open_box(signed_in):
    muu = instrument("muu")
    response = signed_in.post(
        CREATE, {"title": "Pealkiri on olemas", "legal_instruments": [str(muu.pk)]}
    )

    form = response.context["form"]
    assert form.other_instrument_open is True
    assert "Kirjuta, millise õigusaktiga on tegemist." in response.content.decode()


def test_a_javascript_free_post_works(signed_in):
    """Nothing about correctness depends on the browser (design §16).

    A POST carrying no `addressee_is_manual`, no staged-intake key and no
    client-written field at all — which is exactly what a browser with
    scripting off sends — still files the instruments.
    """
    signed_in.post(
        CREATE,
        {
            "title": "Ilma skriptita",
            "legal_instruments": [str(instrument("direktiiv").pk)],
        },
    )

    matter = Matter.objects.get(title="Ilma skriptita")
    assert [item.key for item in matter.legal_instruments.all()] == ["direktiiv"]


# ---------------------------------------------------------------------------
# Independence from Menetlusliik
# ---------------------------------------------------------------------------


def test_a_matter_holds_a_track_and_an_instrument_at_once(signed_in):
    """The example the brief names, asserted as a record (docs/adr/0070 §1)."""
    signed_in.post(
        CREATE,
        {
            "title": "Ülevõtmine, seadus",
            "track": Track.NATIONAL_TRANSPOSITION,
            "legal_instruments": [str(instrument("seadus").pk)],
        },
    )

    matter = Matter.objects.get(title="Ülevõtmine, seadus")
    assert matter.track == Track.NATIONAL_TRANSPOSITION
    assert [item.key for item in matter.legal_instruments.all()] == ["seadus"]


def test_the_track_vocabulary_is_untouched():
    """Nothing in this change adds an instrument to Menetlusliik or the reverse."""
    assert set(Track.values) == {
        "DOMESTIC",
        "EU_INITIATIVE",
        "NATIONAL_TRANSPOSITION",
        "STRATEGY",
        "KODA_INITIATIVE",
        "IMPLEMENTATION",
        "OTHER",
    }
    labels = {label for _value, label in Track.choices}
    instruments = {item.label_et for item in LegalInstrumentType.objects.all()}
    # The one word both vocabularies use, and it is the catch-all in each. That
    # `Strateegia või arengukava` is a *procedure* and `Strateegia` an
    # *instrument* is exactly the distinction this field exists to keep, so the
    # two lists are checked to overlap in nothing else.
    assert labels & instruments == {"Muu"}


def test_changing_one_leaves_the_other_alone(signed_in, specialist):
    matter = create_matter(
        title="Mõlemad",
        actor=specialist,
        track=Track.EU_INITIATIVE,
        legal_instruments=[instrument("el-maarus")],
    )

    signed_in.post(edit_url(matter), edit_payload(matter, track=Track.DOMESTIC))
    matter.refresh_from_db()
    assert matter.track == Track.DOMESTIC
    assert [item.key for item in matter.legal_instruments.all()] == ["el-maarus"]

    signed_in.post(
        edit_url(matter),
        edit_payload(matter, legal_instruments=[str(instrument("maarus").pk)]),
    )
    matter.refresh_from_db()
    assert matter.track == Track.DOMESTIC
    assert [item.key for item in matter.legal_instruments.all()] == ["maarus"]


# ---------------------------------------------------------------------------
# Nothing becomes taxonomy
# ---------------------------------------------------------------------------


def test_the_free_text_creates_no_vocabulary_row(signed_in):
    types_before = LegalInstrumentType.objects.count()
    areas_before = PolicyArea.objects.count()
    tags_before = Tag.objects.count()

    muu = instrument("muu")
    signed_in.post(
        CREATE,
        {
            "title": "Vabatekst",
            "legal_instruments": [str(muu.pk)],
            "legal_instrument_other": "Kosmoseharta",
        },
    )

    assert LegalInstrumentType.objects.count() == types_before
    assert PolicyArea.objects.count() == areas_before
    assert Tag.objects.count() == tags_before


def test_the_service_trims_and_caps(specialist):
    matter = create_matter(
        title="Trim",
        actor=specialist,
        legal_instruments=[instrument("muu")],
        legal_instrument_other="  Komisjoni soovitus  ",
    )
    assert matter.legal_instrument_other == "Komisjoni soovitus"

    long = create_matter(title="Pikk", actor=specialist, legal_instrument_other="x" * 900)
    assert len(long.legal_instrument_other) == 400


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------


def test_the_edit_form_prefills_what_the_matter_holds(specialist):
    matter = create_matter(
        title="Olemas",
        actor=specialist,
        legal_instruments=[instrument("seadus"), instrument("muu")],
        legal_instrument_other="Komisjoni soovitus",
    )

    form = MatterEditForm(initial=edit_initial(matter), matter=matter, viewer=specialist)
    assert set(form["legal_instruments"].value()) == {
        instrument("seadus").pk,
        instrument("muu").pk,
    }
    assert form["legal_instrument_other"].value() == "Komisjoni soovitus"
    assert form.other_instrument_open is True


def test_editing_adds_removes_and_keeps(signed_in, specialist):
    matter = create_matter(
        title="Muudetav", actor=specialist, legal_instruments=[instrument("seadus")]
    )

    signed_in.post(
        edit_url(matter),
        edit_payload(
            matter,
            legal_instruments=[str(instrument("seadus").pk), str(instrument("direktiiv").pk)],
        ),
    )
    matter.refresh_from_db()
    assert {item.key for item in matter.legal_instruments.all()} == {"seadus", "direktiiv"}

    signed_in.post(
        edit_url(matter),
        edit_payload(matter, legal_instruments=[str(instrument("direktiiv").pk)]),
    )
    matter.refresh_from_db()
    assert [item.key for item in matter.legal_instruments.all()] == ["direktiiv"]

    signed_in.post(edit_url(matter), edit_payload(matter, legal_instruments=[]))
    matter.refresh_from_db()
    assert list(matter.legal_instruments.all()) == []


def test_editing_carries_muu_and_its_text(signed_in, specialist):
    matter = create_matter(title="Muu edit", actor=specialist)

    signed_in.post(
        edit_url(matter),
        edit_payload(
            matter,
            legal_instruments=[str(instrument("muu").pk)],
            legal_instrument_other="Komisjoni soovitus",
        ),
    )
    matter.refresh_from_db()
    assert [item.key for item in matter.legal_instruments.all()] == ["muu"]
    assert matter.legal_instrument_other == "Komisjoni soovitus"

    # Taking Muu off takes its text with it.
    signed_in.post(
        edit_url(matter),
        edit_payload(
            matter,
            legal_instruments=[str(instrument("seadus").pk)],
            legal_instrument_other="Komisjoni soovitus",
        ),
    )
    matter.refresh_from_db()
    assert matter.legal_instrument_other == ""


def test_a_refused_edit_keeps_the_choices(signed_in, specialist):
    matter = create_matter(title="Refused", actor=specialist)

    response = signed_in.post(
        edit_url(matter),
        edit_payload(
            matter,
            title="",
            legal_instruments=[str(instrument("vtk").pk), str(instrument("eelnou").pk)],
        ),
    )

    assert response.status_code == 400
    form = response.context["form"]
    chosen = {str(value) for value in form["legal_instruments"].value()}
    assert chosen == {str(instrument("vtk").pk), str(instrument("eelnou").pk)}
    matter.refresh_from_db()
    assert list(matter.legal_instruments.all()) == []


def test_the_edit_form_accepts_a_retired_type_the_matter_already_holds(specialist):
    """A Matter classified under a since-retired type keeps it.

    Exactly what `policy_areas` does on this form, and for the same reason:
    correcting a title must not silently drop a classification.
    """
    visioon = instrument("visioon")
    matter = create_matter(title="Vana", actor=specialist, legal_instruments=[visioon])
    visioon.is_active = False
    visioon.save(update_fields=["is_active"])

    form = MatterEditForm(data=edit_payload(matter), matter=matter, viewer=specialist)
    assert form.is_valid(), form.errors
    assert [item.key for item in form.cleaned_data["legal_instruments"]] == ["visioon"]
    # Offered as a chip too, not hidden — a control that dropped the current
    # value would look like it had cleared it.
    offered = {str(value) for value, _label in form.fields["legal_instruments"].choices}
    assert str(visioon.pk) in offered


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def test_creation_records_one_event_and_no_classification_event(signed_in, specialist):
    signed_in.post(
        CREATE,
        {"title": "Auditi teema", "legal_instruments": [str(instrument("seadus").pk)]},
    )
    matter = Matter.objects.get(title="Auditi teema")

    assert (
        ChangeEvent.objects.filter(matter=matter, event_type=ChangeEventType.MATTER_CREATED).count()
        == 1
    )
    # The creation event is the whole story. A second event describing a change
    # from nothing to something would be describing a change that never happened
    # — the same rule `data_class` follows on this path.
    assert not ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.MATTER_LEGAL_INSTRUMENTS_CHANGED
    ).exists()


def test_an_edit_is_audited_through_the_service(signed_in, specialist):
    matter = create_matter(title="Auditeeritav", actor=specialist)

    signed_in.post(
        edit_url(matter),
        edit_payload(matter, legal_instruments=[str(instrument("maarus").pk)]),
    )

    event = ChangeEvent.objects.get(
        matter=matter, event_type=ChangeEventType.MATTER_LEGAL_INSTRUMENTS_CHANGED
    )
    assert event.summary == "Määrus"
    assert event.payload["removed"] == []
    assert event.payload["added"] == [str(instrument("maarus").pk)]


def test_the_free_text_has_its_own_event_and_never_quotes_itself(specialist):
    matter = create_matter(title="Vabatekst", actor=specialist)

    set_legal_instrument_other(matter=matter, value="Komisjoni soovitus", actor=specialist)

    event = ChangeEvent.objects.get(
        matter=matter, event_type=ChangeEventType.MATTER_LEGAL_INSTRUMENT_OTHER_SET
    )
    assert event.payload == {"cleared": False}
    assert "Komisjoni soovitus" not in str(event.payload)


def test_a_service_call_that_changes_nothing_records_nothing(specialist):
    seadus = instrument("seadus")
    matter = create_matter(title="Sama", actor=specialist, legal_instruments=[seadus])

    set_legal_instruments(matter=matter, legal_instruments=[seadus], actor=specialist)
    set_legal_instrument_other(matter=matter, value="", actor=specialist)

    assert not ChangeEvent.objects.filter(
        matter=matter,
        event_type__in=(
            ChangeEventType.MATTER_LEGAL_INSTRUMENTS_CHANGED,
            ChangeEventType.MATTER_LEGAL_INSTRUMENT_OTHER_SET,
        ),
    ).exists()


def test_the_events_stay_out_of_the_timeline():
    """Correcting a filing is data management, not authored chronology.

    Exactly where the two policy-area events sit, and for the same reason.
    """
    from app.matters.timeline import TIMELINE_EVENT_TYPES

    assert ChangeEventType.MATTER_LEGAL_INSTRUMENTS_CHANGED not in TIMELINE_EVENT_TYPES
    assert ChangeEventType.MATTER_LEGAL_INSTRUMENT_OTHER_SET not in TIMELINE_EVENT_TYPES
    assert ChangeEventType.MATTER_POLICY_AREAS_CHANGED not in TIMELINE_EVENT_TYPES


# ---------------------------------------------------------------------------
# The control is a promise about the data (ADR 0025)
# ---------------------------------------------------------------------------


def test_the_control_is_checkboxes_over_the_whole_active_vocabulary(specialist):
    form = MatterCreateForm(viewer=specialist)
    field = form.fields["legal_instruments"]

    assert field.widget.__class__.__name__ == "CheckboxSelectMultiple"
    assert not field.required
    offered = [label for _value, label in field.choices]
    assert offered == [
        item.label_et
        for item in LegalInstrumentType.objects.filter(is_active=True).order_by("sort_order")
    ]
    assert offered[-1] == "Muu"


def test_the_rendered_page_puts_oigusakt_between_menetlusliik_and_adressaat(signed_in):
    """The approved placement, asserted on the markup the server sends.

    `e2e/test_oigusakt_row.py` owns the geometry; this owns the order, which is
    the half a screenshot cannot state.
    """
    body = signed_in.get(CREATE).content.decode()

    track = body.index('name="track"')
    oigusakt = body.index('name="legal_instruments"')
    addressee = body.index("data-addressee-disclosure")
    assert track < oigusakt < addressee


def test_the_page_offers_no_new_component(signed_in):
    """No select, no disclosure, no search over this vocabulary (design §14)."""
    body = signed_in.get(CREATE).content.decode()
    row_start = body.index('data-chipcount-for="legal_instruments"')
    # The field's own fieldset, and no further. Slicing to the Adressaat row
    # below it would drag that row's `<details>` into the assertion and the
    # test would fail for a disclosure it is not about.
    row = body[row_start : body.index("</fieldset>", row_start)]

    assert "<details" not in row
    assert "<select" not in row
    assert 'type="radio"' not in row
    assert (
        row.count('type="checkbox"') == LegalInstrumentType.objects.filter(is_active=True).count()
    )


def test_menetlusliik_gains_no_count_and_no_clear_marks(signed_in):
    """The asymmetry that keeps the two rows from reading as one question.

    Menetlusliik holds one value, so it has neither the `field__count` nor the
    `chip__clear` marks that mark a multi-select (design §4, §6).
    """
    body = signed_in.get(CREATE).content.decode()
    start = body.index('name="track"')
    track_field = body[start : body.index("</fieldset>", start)]
    assert "field__count" not in track_field
    assert "chip__clear" not in track_field
    assert 'type="radio"' in track_field


def test_the_create_page_still_holds_the_sender_default_and_the_folded_addressee(
    signed_in, specialist
):
    """PR #169's behaviour, unchanged by the row inserted above it (task §15)."""
    organisation = factories.OrganisationFactory(name="Kliimaministeerium")
    response = signed_in.post(
        CREATE,
        {
            "title": "Saatja täidab adressaadi",
            "source_organisations": [str(organisation.pk)],
            "legal_instruments": [str(instrument("seadus").pk)],
        },
    )

    assert response.status_code == 302
    matter = Matter.objects.get(title="Saatja täidab adressaadi")
    assert matter.addressee_organisation == organisation

    body = signed_in.get(CREATE).content.decode()
    disclosure = body[body.index("data-addressee-disclosure") :][:400]
    assert " open" not in disclosure.split(">")[0]


# ---------------------------------------------------------------------------
# Raw source and canonical reading are two facts, and both survive
# ---------------------------------------------------------------------------


def test_the_raw_register_value_survives_beside_an_empty_canonical_reading(specialist):
    """The invariant the whole change is arranged around (task §5, §11, §21).

    `CurrentRegisterState.legal_instrument_raw` is what the spreadsheet said.
    `Matter.legal_instruments` is the reviewed interpretation. They are
    different concepts, both exist for one record, and nothing normalises the
    first in order to produce the second.

    Asserted on the two spellings that produce *no* canonical reading, because
    that is where a codebase would be tempted to tidy: an unmappable value is
    exactly the one somebody would round to `Muu` to make a coverage number
    look better, and rounding it would destroy the only record of what the
    department actually wrote.
    """
    from django.utils import timezone

    from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
    from app.taxonomy.legal_instruments import (
        UNMAPPABLE_RAW_VALUES,
        canonical_legal_instrument_keys,
    )

    snapshot = "e" * 64
    for index, raw in enumerate(sorted(UNMAPPABLE_RAW_VALUES), start=1):
        matter = factories.MatterFactory(title=f"Ajalooline {index}")
        reference = factories.MatterSourceReferenceFactory(
            matter=matter,
            source_sheet="2026",
            source_row_number=index,
            source_snapshot_sha256=snapshot,
        )
        state = CurrentRegisterState.objects.create(
            matter=matter,
            source_reference=reference,
            source_snapshot_sha256=snapshot,
            source_sheet="2026",
            source_row_number=index,
            currency=RegisterCurrency.CURRENT,
            legal_instrument_raw=raw,
            observed_at=timezone.now(),
        )

        state.refresh_from_db()
        assert state.legal_instrument_raw == raw, "the source string was altered"
        assert canonical_legal_instrument_keys(raw) == ()
        # And the canonical field is untouched — no importer wrote it, and the
        # unmappable value certainly did not become `Muu`.
        assert list(matter.legal_instruments.all()) == []
        assert matter.legal_instrument_other == ""


def test_a_mappable_raw_value_still_does_not_write_the_canonical_field(specialist):
    """A reviewed *reading* is not an importer that applies it (task §12).

    `seadus` reads as `["seadus"]` and the Matter beside it stays unclassified,
    because whether the source may answer this question for a person is a
    decision nobody has made. The era contracts say `mapped` rather than
    `authoritative` for exactly this reason.
    """
    from django.utils import timezone

    from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
    from app.taxonomy.legal_instruments import canonical_legal_instrument_keys

    snapshot = "d" * 64
    matter = factories.MatterFactory(title="Registri rida")
    reference = factories.MatterSourceReferenceFactory(
        matter=matter,
        source_sheet="2026",
        source_row_number=9,
        source_snapshot_sha256=snapshot,
    )
    CurrentRegisterState.objects.create(
        matter=matter,
        source_reference=reference,
        source_snapshot_sha256=snapshot,
        source_sheet="2026",
        source_row_number=9,
        currency=RegisterCurrency.CURRENT,
        legal_instrument_raw="seadus",
        observed_at=timezone.now(),
    )

    assert canonical_legal_instrument_keys("seadus") == ("seadus",)
    assert list(matter.legal_instruments.all()) == []


def test_the_register_refresh_does_not_carry_legal_instruments():
    """Nothing picks this field up by accident on the recurring refresh.

    `refresh_matter_from_register` takes one explicit keyword per field it is
    allowed to move, so a field it does not name is a field it cannot touch.
    That is the guarantee standing in for the precedence decision nobody has
    made yet (docs/open-decisions.md, task §12).
    """
    import inspect

    from app.matters.services import refresh_matter_from_register

    parameters = set(inspect.signature(refresh_matter_from_register).parameters)
    assert "legal_instruments" not in parameters
    assert "legal_instrument_other" not in parameters
    # The keywords it *does* carry, so widening it is a deliberate edit here.
    assert parameters == {
        "matter",
        "owner",
        "stage",
        "received_date",
        "response_deadline",
        "source_organisations",
        "addressee_organisation",
        "actor",
        "provenance",
    }
