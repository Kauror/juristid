"""`Koja ettepanek või pöördumine` — a Teema the Chamber starts itself (owner's R5).

docs/adr/0151. What is pinned here:

* **One stored fact, the one that already existed**: `Matter.track` «Koja
  algatus». Marking and unmarking go through the audited track writer; nothing
  is derived from `Õigusakt`.
* **No incoming dates are established.** `Uus teema` records neither `Saabus`
  nor `Arvamuse tähtaeg` for an initiative, whatever the two boxes held — so no
  response obligation begins and nothing reads «arvamust koostamisel». Every
  writer refuses to *establish* either on an initiative, with one sentence.
* **History is not erased.** A Teema marked later keeps the dates it holds;
  moving or clearing one is the ordinary audited correction.
* **An ordinary Teema is unchanged.**
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.dashboard import drafting_matters
from app.matters.forms import edit_initial
from app.matters.initiative import (
    INITIATIVE_HAS_NO_RECEIVED_DATE,
    INITIATIVE_HAS_NO_RESPONSE_DEADLINE,
    is_koda_initiative,
    set_koda_initiative,
)
from app.matters.models import Matter
from app.matters.response_deadlines import change_response_deadline, request_response_deadline
from app.matters.services import create_imported_matter, create_matter, set_matter_dates
from app.matters.work_items import response_obligation_of
from app.taxonomy.models import LegalInstrumentType
from app.workflow.enums import Track
from tests import factories
from tests.refusals import refused

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")


def today() -> date:
    return timezone.localdate()


def estonian(day: date) -> str:
    return f"{day.day}.{day.month}.{day.year}"


def edit_url(matter: Matter) -> str:
    return reverse("matters:matter_edit", kwargs={"pk": matter.pk})


def edit_payload(matter: Matter, **overrides: object) -> dict[str, object]:
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
        "received_date": estonian(initial["received_date"]) if initial["received_date"] else "",
        "response_deadline": (
            estonian(initial["response_deadline"]) if initial["response_deadline"] else ""
        ),
        "koda_initiative": "on" if initial["koda_initiative"] else "",
    }
    payload.update(overrides)
    return {key: value for key, value in payload.items() if value != "" or key != "koda_initiative"}


def track_events(matter: Matter) -> list[dict]:
    return [
        event.payload
        for event in ChangeEvent.objects.filter(
            matter=matter, event_type=ChangeEventType.MATTER_TRACK_CHANGED
        ).order_by("occurred_at")
    ]


# ---------------------------------------------------------------------------
# Uus teema
# ---------------------------------------------------------------------------


def test_an_initiative_is_filed_with_no_incoming_dates(signed_in, specialist):
    """`Saabus` arrives holding today and a deadline may have been typed —
    neither is recorded, and no response obligation begins."""
    response = signed_in.post(
        CREATE,
        {
            "title": "Koja ettepanek maksukorralduse muutmiseks",
            "koda_initiative": "on",
            "received_date": estonian(today()),
            "response_deadline": estonian(today() + timedelta(days=14)),
        },
    )

    assert response.status_code == 302
    matter = Matter.objects.get(title="Koja ettepanek maksukorralduse muutmiseks")
    assert matter.track == Track.KODA_INITIATIVE
    assert is_koda_initiative(matter)
    assert matter.received_date is None
    assert matter.response_deadline is None
    assert matter.response_requested_at is None
    assert not response_obligation_of(matter, specialist).is_outstanding
    assert matter not in drafting_matters(specialist)


def test_an_ordinary_teema_is_filed_exactly_as_before(signed_in, specialist):
    due = today() + timedelta(days=14)
    signed_in.post(
        CREATE,
        {
            "title": "Tavaline päring",
            "received_date": estonian(today()),
            "response_deadline": estonian(due),
        },
    )

    matter = Matter.objects.get(title="Tavaline päring")
    assert matter.track == ""
    assert matter.received_date == today()
    assert matter.response_deadline == due
    assert matter.response_requested_at is not None
    assert response_obligation_of(matter, specialist).is_outstanding


def test_the_instrument_koja_ettepanek_marks_nothing_by_itself(signed_in):
    """`Õigusakt` is the kind of instrument; who started the work is asked."""
    instrument = LegalInstrumentType.objects.get(key="koja-ettepanek")
    signed_in.post(
        CREATE,
        {
            "title": "Ainult õigusakti liik",
            "legal_instruments": [str(instrument.pk)],
            "received_date": estonian(today()),
        },
    )

    matter = Matter.objects.get(title="Ainult õigusakti liik")
    assert matter.track == ""
    assert matter.received_date == today()


def test_an_initiative_may_concern_any_instrument(signed_in):
    maarus = LegalInstrumentType.objects.get(key="maarus")
    signed_in.post(
        CREATE,
        {
            "title": "Koja ettepanek määruse muutmiseks",
            "koda_initiative": "on",
            "legal_instruments": [str(maarus.pk)],
        },
    )

    matter = Matter.objects.get(title="Koja ettepanek määruse muutmiseks")
    assert matter.track == Track.KODA_INITIATIVE
    assert [item.key for item in matter.legal_instruments.all()] == ["maarus"]


def test_a_refused_initiative_save_comes_back_ticked(signed_in):
    response = signed_in.post(CREATE, {"title": "", "koda_initiative": "on"})

    assert response.status_code == 400
    assert response.context["form"]["koda_initiative"].value() is True
    assert not Matter.objects.filter(track=Track.KODA_INITIATIVE).exists()


def test_native_creation_refuses_incoming_dates_on_an_initiative(specialist):
    with refused(INITIATIVE_HAS_NO_RESPONSE_DEADLINE):
        create_matter(
            title="Teenusest mööda",
            actor=specialist,
            track=Track.KODA_INITIATIVE,
            response_deadline=today() + timedelta(days=3),
        )
    with refused(INITIATIVE_HAS_NO_RECEIVED_DATE):
        create_matter(
            title="Teenusest mööda",
            actor=specialist,
            track=Track.KODA_INITIATIVE,
            received_date=today(),
        )
    assert not Matter.objects.filter(title="Teenusest mööda").exists()


def test_an_import_records_what_the_register_says(specialist):
    matter = create_imported_matter(
        title="Registri Koja algatus",
        reference_year=2099,
        reference_number=777,
        track=Track.KODA_INITIATIVE,
        received_date=today(),
    )
    assert matter.received_date == today()


# ---------------------------------------------------------------------------
# Muuda teemat
# ---------------------------------------------------------------------------


def test_marking_keeps_the_dates_a_teema_already_holds(signed_in, specialist):
    due = today() + timedelta(days=10)
    matter = create_matter(
        title="Hiljem algatuseks",
        actor=specialist,
        owner=specialist,
        received_date=today() - timedelta(days=3),
        response_deadline=due,
        response_requested_at=timezone.now(),
    )

    response = signed_in.post(edit_url(matter), edit_payload(matter, koda_initiative="on"))

    assert response.status_code == 302
    matter.refresh_from_db()
    assert matter.track == Track.KODA_INITIATIVE
    assert matter.received_date == today() - timedelta(days=3)
    assert matter.response_deadline == due
    assert track_events(matter) == [{"from": "", "to": Track.KODA_INITIATIVE}]


def test_an_initiative_is_not_given_a_new_saabus_or_deadline(signed_in, specialist):
    matter = create_matter(title="Algatus", actor=specialist, track=Track.KODA_INITIATIVE)

    for field, refusal in (
        ("received_date", INITIATIVE_HAS_NO_RECEIVED_DATE),
        ("response_deadline", INITIATIVE_HAS_NO_RESPONSE_DEADLINE),
    ):
        response = signed_in.post(
            edit_url(matter),
            edit_payload(matter, **{field: estonian(today() + timedelta(days=5))}),
        )
        assert response.status_code == 400, field
        assert response.context["form"].errors[field] == [refusal]
    matter.refresh_from_db()
    assert (matter.received_date, matter.response_deadline) == (None, None)


def test_a_held_date_may_still_be_cleared_on_an_initiative(signed_in, specialist):
    matter = create_matter(
        title="Ajaloolise kuupäevaga algatus",
        actor=specialist,
        received_date=today() - timedelta(days=30),
    )
    set_koda_initiative(matter=matter, value=True, actor=specialist)
    matter.refresh_from_db()

    response = signed_in.post(edit_url(matter), edit_payload(matter, received_date=""))

    assert response.status_code == 302
    matter.refresh_from_db()
    assert matter.received_date is None
    assert ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.MATTER_DATE_CHANGED
    ).exists()


def test_unmarking_and_adding_a_deadline_in_one_save_is_an_ordinary_teema(signed_in, specialist):
    matter = create_matter(title="Muutub tavaliseks", actor=specialist, track=Track.KODA_INITIATIVE)
    due = today() + timedelta(days=7)

    response = signed_in.post(
        edit_url(matter),
        {
            key: value
            for key, value in edit_payload(matter, response_deadline=estonian(due)).items()
            if key != "koda_initiative"
        },
    )

    assert response.status_code == 302
    matter.refresh_from_db()
    assert matter.track == ""
    assert matter.response_deadline == due
    assert track_events(matter)[-1] == {"from": Track.KODA_INITIATIVE, "to": ""}


def test_another_track_is_left_alone_by_an_unrelated_save(signed_in, specialist):
    """Unmarking clears only «Koja algatus»; a historical track is not touched."""
    matter = create_matter(title="ELi algatus", actor=specialist, track=Track.EU_INITIATIVE)

    signed_in.post(edit_url(matter), edit_payload(matter, title="ELi algatus, uus pealkiri"))

    matter.refresh_from_db()
    assert matter.track == Track.EU_INITIATIVE
    assert track_events(matter) == []


def test_marking_over_another_track_says_what_it_replaced(specialist):
    matter = create_matter(title="Vahetus", actor=specialist, track=Track.EU_INITIATIVE)

    set_koda_initiative(matter=matter, value=True, actor=specialist)

    assert track_events(matter) == [{"from": Track.EU_INITIATIVE, "to": Track.KODA_INITIATIVE}]


def test_somebody_who_may_not_write_cannot_mark_one(client, reader, specialist):
    matter = create_matter(title="Lugeja ei muuda", actor=specialist)
    client.force_login(reader)

    response = client.post(edit_url(matter), edit_payload(matter, koda_initiative="on"))

    assert response.status_code in (403, 404)
    matter.refresh_from_db()
    assert matter.track == ""


# ---------------------------------------------------------------------------
# The other writers
# ---------------------------------------------------------------------------


def test_every_writer_refuses_to_establish_a_date_on_an_initiative(specialist):
    matter = create_matter(title="Kõik kirjutajad", actor=specialist, track=Track.KODA_INITIATIVE)

    with refused(INITIATIVE_HAS_NO_RECEIVED_DATE):
        set_matter_dates(matter=matter, received_date=today(), actor=specialist)
    with refused(INITIATIVE_HAS_NO_RESPONSE_DEADLINE):
        change_response_deadline(
            matter=matter, deadline=today() + timedelta(days=4), actor=specialist
        )
    with refused(INITIATIVE_HAS_NO_RESPONSE_DEADLINE):
        request_response_deadline(
            matter=matter, deadline=today() + timedelta(days=4), actor=specialist
        )
    matter.refresh_from_db()
    assert (matter.received_date, matter.response_deadline) == (None, None)


def test_a_held_deadline_may_still_be_moved_on_an_initiative(specialist):
    from app.matters.enums import ResponseDeadlineChange

    due = today() + timedelta(days=4)
    matter = create_matter(
        title="Liigutatav",
        actor=specialist,
        response_deadline=due,
        response_requested_at=timezone.now(),
    )
    set_koda_initiative(matter=matter, value=True, actor=specialist)
    matter.refresh_from_db()

    change_response_deadline(
        matter=matter,
        deadline=due + timedelta(days=3),
        change=ResponseDeadlineChange.MOVED,
        actor=specialist,
    )

    matter.refresh_from_db()
    assert matter.response_deadline == due + timedelta(days=3)


def test_the_header_inline_editor_refuses_a_new_saabus(signed_in, specialist):
    matter = create_matter(title="Päise kaudu", actor=specialist, track=Track.KODA_INITIATIVE)

    response = signed_in.post(
        reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "received_date"}),
        {"received_date": estonian(today())},
        HTTP_HX_REQUEST="true",
    )

    assert INITIATIVE_HAS_NO_RECEIVED_DATE in response.content.decode()
    matter.refresh_from_db()
    assert matter.received_date is None


# ---------------------------------------------------------------------------
# What the Teema page says
# ---------------------------------------------------------------------------


def test_the_header_says_so_and_offers_no_incoming_date(signed_in, specialist):
    initiative = create_matter(
        title="Algatus päises", actor=specialist, track=Track.KODA_INITIATIVE
    )
    ordinary = create_matter(title="Tavaline päises", actor=specialist)

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": initiative.pk})
    ).content.decode()
    meta = body.split('class="metaline"', 1)[1].split("matters/partials/summary", 1)[0]
    assert "Koja ettepanek või pöördumine" in meta
    assert "+ Saabus" not in meta
    assert "+ Arvamuse tähtaeg" not in meta
    assert 'id="marge-arvamuse-tahtaeg-valik"' not in body

    plain = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": ordinary.pk})
    ).content.decode()
    plain_meta = plain.split('class="metaline"', 1)[1].split("matters/partials/summary", 1)[0]
    assert "Koja ettepanek või pöördumine" not in plain_meta
    assert "+ Saabus" in plain_meta
    assert "+ Arvamuse tähtaeg" in plain_meta


def test_a_held_saabus_is_still_shown_on_an_initiative(signed_in, specialist):
    matter = create_matter(title="Ajalugu nähtav", actor=specialist, received_date=date(2026, 9, 1))
    set_koda_initiative(matter=matter, value=True, actor=specialist)

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    assert (
        "1.9.2026" in body.split('class="metaline"', 1)[1].split("matters/partials/summary", 1)[0]
    )


def test_the_edit_page_arrives_ticked_for_an_initiative(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist, track=Track.KODA_INITIATIVE)

    body = signed_in.get(edit_url(matter)).content.decode()
    box = body.split('name="koda_initiative"', 1)[1].split(">", 1)[0]
    assert "checked" in box
    assert 'data-initiative-mode="keep"' in body


# ---------------------------------------------------------------------------
# Review fixes
# ---------------------------------------------------------------------------


def test_a_stale_edit_names_the_initiative_a_colleague_set(signed_in, specialist):
    """The conflict list says what moved, so a second Salvesta does not undo it."""
    matter = create_matter(title="Kaks toimetajat", actor=specialist)
    stale = edit_payload(matter)
    set_koda_initiative(matter=matter, value=True, actor=specialist)

    response = signed_in.post(edit_url(matter), stale)

    assert response.status_code == 409
    changes = {item["label"]: item for item in response.context["conflict_changes"]}
    assert changes["Koja ettepanek või pöördumine"]["current"] == "jah"
    assert changes["Koja ettepanek või pöördumine"]["submitted"] == "ei"
    matter.refresh_from_db()
    assert matter.track == Track.KODA_INITIATIVE


def test_the_assisted_review_proposes_no_deadline_for_an_initiative(monkeypatch):
    """`Muuda teemat → dokumendist` would otherwise fill a date its own save refuses."""
    from types import SimpleNamespace

    from app.matters.intake_suggestions import prefill as prefill_module
    from app.matters.intake_suggestions.analysis import CurrentValues
    from app.matters.intake_suggestions.types import SuggestedField

    # The analysis objects are frozen dataclasses; stand-ins need `replace` to
    # hand them back unchanged, which is all the tail of `prefill_initial` does.
    monkeypatch.setattr(prefill_module, "replace", lambda obj, **_: obj)
    due = (today() + timedelta(days=9)).isoformat()
    analysis = SimpleNamespace(
        fields={
            SuggestedField.RESPONSE_DEADLINE: SimpleNamespace(
                prefill_candidate=SimpleNamespace(value=due), candidates=()
            )
        }
    )

    ordinary, _ = prefill_module.prefill_initial(analysis, base={}, current=CurrentValues())
    initiative, _ = prefill_module.prefill_initial(
        analysis, base={}, current=CurrentValues(track=Track.KODA_INITIATIVE)
    )

    assert ordinary["response_deadline"] == date.fromisoformat(due)
    assert "response_deadline" not in initiative


def test_replacing_a_held_deadline_is_refused_on_an_initiative(specialist):
    from app.matters.enums import ResponseDeadlineChange

    due = today() + timedelta(days=4)
    matter = create_matter(
        title="Asendamine",
        actor=specialist,
        response_deadline=due,
        response_requested_at=timezone.now(),
    )
    set_koda_initiative(matter=matter, value=True, actor=specialist)
    matter.refresh_from_db()

    with refused(INITIATIVE_HAS_NO_RESPONSE_DEADLINE):
        change_response_deadline(
            matter=matter,
            deadline=due + timedelta(days=9),
            change=ResponseDeadlineChange.REPLACED,
            previous_outcome="CANCELLED",
            actor=specialist,
        )
    matter.refresh_from_db()
    assert matter.response_deadline == due


def test_a_saabus_is_judged_against_the_stored_track_not_a_stale_copy(specialist):
    """The header editor holds no lock of its own; the service reads the track under one."""
    matter = create_matter(title="Vana koopia", actor=specialist)
    stale = Matter.objects.get(pk=matter.pk)
    set_koda_initiative(matter=matter, value=True, actor=specialist)

    with refused(INITIATIVE_HAS_NO_RECEIVED_DATE):
        set_matter_dates(matter=stale, received_date=today(), actor=specialist)
    matter.refresh_from_db()
    assert matter.received_date is None
