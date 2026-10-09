"""One `Õigusakt` per Teema — and the Teemad that already hold several keep them.

The owner's decision of 2026-10-09 (R2, docs/adr/0070's amendment of that date).
What is pinned here, beside the field tests in `tests/test_oigusakt_field.py`:

* **The rule is the server's.** The form refuses a request naming two, and so do
  the two services a person reaches — native `create_matter` and
  `set_legal_instruments` — so a route that skipped the form would be refused
  too. Nothing is trimmed to one: which was meant is not the server's guess.
* **History is not rewritten.** The relation stays many-to-many. A Matter that
  holds two — filed before the rule, or imported from a register cell naming
  `S, M` — keeps both through every save that is about something else, and
  loses one only when somebody deliberately chooses one (or «Määramata»), which
  is audited like any change.
* **Importers record what the register says.** A `LEGACY_IMPORT` creation with
  two instruments is still accepted.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.forms import HELD_LEGAL_INSTRUMENTS_VALUE, MatterEditForm, edit_initial
from app.matters.models import Matter
from app.matters.services import (
    ONE_LEGAL_INSTRUMENT_REFUSAL,
    create_imported_matter,
    create_matter,
    set_legal_instruments,
)
from app.taxonomy.models import LegalInstrumentType
from tests import factories
from tests.refusals import refused

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")


def instrument(key: str) -> LegalInstrumentType:
    return LegalInstrumentType.objects.get(key=key)


def edit_url(matter: Matter) -> str:
    return reverse("matters:matter_edit", kwargs={"pk": matter.pk})


def keys(matter: Matter) -> set[str]:
    matter.refresh_from_db()
    return {item.key for item in matter.legal_instruments.all()}


def instrument_events(matter: Matter) -> int:
    return ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.MATTER_LEGAL_INSTRUMENTS_CHANGED
    ).count()


def historical_pair(owner, *pair: str, title: str = "Varasem kahe õigusaktiga teema") -> Matter:
    """A Matter holding several, the way one exists: written to the relation."""
    matter = create_matter(title=title, actor=owner, owner=owner)
    matter.legal_instruments.set([instrument(key) for key in pair])
    return matter


def edit_payload(matter: Matter, **overrides: object) -> dict[str, object]:
    """Every control `Muuda teemat` posts, as the page arrives with it."""
    initial = edit_initial(matter)
    payload: dict[str, object] = {
        "revision": initial["revision"],
        "title": initial["title"],
        "brief_summary": initial["brief_summary"] or "",
        "owner": initial["owner"] or "",
        "stage": initial["stage"] or "",
        "policy_areas": [str(pk) for pk in initial["policy_areas"]],
        "policy_area_other_selected": "on" if initial["policy_area_other_selected"] else "",
        "policy_area_other": initial["policy_area_other"] or "",
        "legal_instruments": [str(value) for value in initial["legal_instruments"]],
        "legal_instrument_other": initial["legal_instrument_other"] or "",
        "source_organisations": [str(pk) for pk in initial["source_organisations"]],
        "sender_name": "",
        "received_date": "",
        "response_deadline": "",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# The services refuse a second one, whoever calls them
# ---------------------------------------------------------------------------


def test_native_creation_refuses_two_instruments(specialist):
    with refused(ONE_LEGAL_INSTRUMENT_REFUSAL):
        create_matter(
            title="Kaks",
            actor=specialist,
            legal_instruments=[instrument("seadus"), instrument("maarus")],
        )
    assert not Matter.objects.filter(title="Kaks").exists()


def test_the_same_instrument_twice_is_still_one(specialist):
    matter = create_matter(
        title="Üks kaks korda",
        actor=specialist,
        legal_instruments=[instrument("seadus"), instrument("seadus")],
    )
    assert keys(matter) == {"seadus"}


def test_an_import_still_records_what_the_register_says(specialist):
    """`S, M` in a register cell is two answers, and the importer keeps both."""
    matter = create_imported_matter(
        title="Registri kahe liigiga rida",
        reference_year=2099,
        reference_number=4711,
        legal_instruments=[instrument("seadus"), instrument("maarus")],
    )
    assert keys(matter) == {"seadus", "maarus"}


def test_a_change_to_two_is_refused_and_moves_nothing(specialist):
    matter = create_matter(title="Üks", actor=specialist, legal_instruments=[instrument("seadus")])
    with refused(ONE_LEGAL_INSTRUMENT_REFUSAL):
        set_legal_instruments(
            matter=matter,
            legal_instruments=[instrument("seadus"), instrument("direktiiv")],
            actor=specialist,
        )
    assert keys(matter) == {"seadus"}
    assert instrument_events(matter) == 0


def test_an_unchanged_historical_pair_is_not_a_change(specialist):
    matter = historical_pair(specialist, "seadus", "maarus")

    set_legal_instruments(
        matter=matter,
        legal_instruments=[instrument("maarus"), instrument("seadus")],
        actor=specialist,
    )

    assert keys(matter) == {"seadus", "maarus"}
    assert instrument_events(matter) == 0


def test_a_historical_pair_may_be_narrowed_to_one_or_cleared(specialist):
    matter = historical_pair(specialist, "seadus", "maarus")

    set_legal_instruments(matter=matter, legal_instruments=[instrument("maarus")], actor=specialist)
    assert keys(matter) == {"maarus"}
    event = ChangeEvent.objects.get(
        matter=matter, event_type=ChangeEventType.MATTER_LEGAL_INSTRUMENTS_CHANGED
    )
    assert event.payload["removed"] == [str(instrument("seadus").pk)]
    assert event.payload["added"] == []

    other = historical_pair(specialist, "vtk", "seadus", title="Teine paar")
    set_legal_instruments(matter=other, legal_instruments=[], actor=specialist)
    assert keys(other) == set()


def test_a_historical_pair_cannot_be_swapped_for_another_pair(specialist):
    matter = historical_pair(specialist, "seadus", "maarus")
    with refused(ONE_LEGAL_INSTRUMENT_REFUSAL):
        set_legal_instruments(
            matter=matter,
            legal_instruments=[instrument("seadus"), instrument("direktiiv")],
            actor=specialist,
        )
    assert keys(matter) == {"seadus", "maarus"}


# ---------------------------------------------------------------------------
# Muuda teemat on a Matter that holds several
# ---------------------------------------------------------------------------


def test_the_edit_page_states_the_pair_and_arrives_keeping_it(signed_in, specialist):
    matter = historical_pair(specialist, "seadus", "maarus")

    body = signed_in.get(edit_url(matter)).content.decode()

    assert "Sellel teemal on varasemast mitu õigusakti" in body
    held = body.split(f'value="{HELD_LEGAL_INSTRUMENTS_VALUE}"', 1)[1].split(">", 1)[0]
    assert "checked" in held
    assert 'data-instrument-keys="seadus maarus"' in body or (
        'data-instrument-keys="maarus seadus"' in body
    )
    assert "Jäta alles: " in body


def test_an_unrelated_edit_keeps_the_historical_pair_untouched(signed_in, specialist):
    matter = historical_pair(specialist, "seadus", "maarus")

    response = signed_in.post(edit_url(matter), edit_payload(matter, title="Uus pealkiri"))

    assert response.status_code == 302
    matter.refresh_from_db()
    assert matter.title == "Uus pealkiri"
    assert keys(matter) == {"seadus", "maarus"}
    assert instrument_events(matter) == 0


def test_choosing_one_is_the_deliberate_correction(signed_in, specialist):
    matter = historical_pair(specialist, "seadus", "maarus")

    response = signed_in.post(
        edit_url(matter), edit_payload(matter, legal_instruments=[str(instrument("seadus").pk)])
    )

    assert response.status_code == 302
    assert keys(matter) == {"seadus"}
    assert instrument_events(matter) == 1


def test_keeping_the_pair_cannot_be_combined_with_a_choice(signed_in, specialist):
    matter = historical_pair(specialist, "seadus", "maarus")

    response = signed_in.post(
        edit_url(matter),
        edit_payload(
            matter,
            legal_instruments=[HELD_LEGAL_INSTRUMENTS_VALUE, str(instrument("vtk").pk)],
        ),
    )

    assert response.status_code == 400
    assert keys(matter) == {"seadus", "maarus"}


def test_keeping_a_pair_is_no_answer_on_a_matter_without_one(signed_in, specialist):
    """A forged «Jäta alles» on a Matter holding one is not a way to keep anything."""
    matter = create_matter(title="Üks", actor=specialist, legal_instruments=[instrument("seadus")])

    response = signed_in.post(
        edit_url(matter), edit_payload(matter, legal_instruments=[HELD_LEGAL_INSTRUMENTS_VALUE])
    )

    assert response.status_code == 400
    assert keys(matter) == {"seadus"}


def test_a_pair_with_muu_keeps_its_text_through_an_unrelated_edit(signed_in, specialist):
    matter = historical_pair(specialist, "seadus", "muu")
    Matter.objects.filter(pk=matter.pk).update(legal_instrument_other="Komisjoni soovitus")
    matter.refresh_from_db()

    form = MatterEditForm(initial=edit_initial(matter), matter=matter, viewer=specialist)
    assert form.other_instrument_open is True

    signed_in.post(edit_url(matter), edit_payload(matter, title="Pealkiri muutus"))

    matter.refresh_from_db()
    assert keys(matter) == {"seadus", "muu"}
    assert matter.legal_instrument_other == "Komisjoni soovitus"


# ---------------------------------------------------------------------------
# Both Muu answers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("muu", ["muu-siseriiklik", "muu-eli-dokument"])
def test_each_muu_demands_its_text_and_keeps_it(signed_in, muu):
    refused_post = signed_in.post(
        CREATE, {"title": f"Muu {muu}", "legal_instruments": [str(instrument(muu).pk)]}
    )
    assert refused_post.status_code == 400
    assert "legal_instrument_other" in refused_post.context["form"].errors
    # Refused with the box open and the choice still chosen.
    assert refused_post.context["form"].other_instrument_open is True

    signed_in.post(
        CREATE,
        {
            "title": f"Muu {muu}",
            "legal_instruments": [str(instrument(muu).pk)],
            "legal_instrument_other": "Ministri käskkiri",
        },
    )
    matter = Matter.objects.get(title=f"Muu {muu}")
    assert keys(matter) == {muu}
    assert matter.legal_instrument_other == "Ministri käskkiri"


def test_switching_from_muu_to_another_takes_the_text_with_it(signed_in, specialist):
    matter = create_matter(
        title="Muu ära",
        actor=specialist,
        legal_instruments=[instrument("muu-eli-dokument")],
        legal_instrument_other="Nõukogu järeldused",
    )

    signed_in.post(
        edit_url(matter),
        edit_payload(matter, legal_instruments=[str(instrument("el-maarus").pk)]),
    )

    matter.refresh_from_db()
    assert keys(matter) == {"el-maarus"}
    assert matter.legal_instrument_other == ""


# ---------------------------------------------------------------------------
# What reads the answer
# ---------------------------------------------------------------------------


def test_the_draft_suggestions_read_one_instrument_and_ignore_maaramata(signed_in, specialist):
    """`Sarnased teemad` posts the radio group's one value — or «Määramata»'s ``""``."""
    earlier = factories.MatterFactory(
        owner=specialist,
        title="Pakendiseaduse muutmise eelnõu",
        reference_year=2099,
        reference_number=31,
    )
    earlier.legal_instruments.set([instrument("seadus")])
    url = reverse("related_materials:draft_suggestions")

    for chosen in ([str(instrument("seadus").pk)], [""]):
        response = signed_in.post(
            url, {"title": "Pakendiseaduse muutmise eelnõu", "legal_instruments": chosen}
        )
        assert response.status_code == 200
        assert "Pakendiseaduse muutmise eelnõu" in response.content.decode()
