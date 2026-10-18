"""Independent QA 2026-10-14 — workflow area: `Uus teema` writes through the real view.

ADR 0130, 0150, 0032 and PR #427. Each test posts the form a person posts and
reads the stored rows back (Matter, MatterRelation, ChangeEvent), not the markup.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.models import Matter, MatterReferenceSequence
from app.related_materials.models import MatterRelation
from app.related_materials.views import LINK_ON_CREATE_CHOSEN_FIELD
from app.taxonomy.models import LegalInstrumentType
from app.workflow.models import StageVocabulary
from tests import factories

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")


def et(day: dt.date) -> str:
    return f"{day.day}.{day.month}.{day.year}"


def token(client: Client) -> str:
    body = client.get(CREATE).content.decode()
    match = re.search(r'name="intake" value="([^"]+)"', body)
    assert match
    return match.group(1)


def relations_of(matter: Matter) -> set:
    rows = MatterRelation.objects.filter(matter_a=matter) | MatterRelation.objects.filter(
        matter_b=matter
    )
    return {row.matter_b_id if row.matter_a_id == matter.pk else row.matter_a_id for row in rows}


def numbers_used() -> int:
    return sum(MatterReferenceSequence.objects.values_list("last_number", flat=True))


def instrument_pk(key: str) -> str:
    return str(LegalInstrumentType.objects.get(key=key).pk)


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("typed", ["31.2.2026", "32.10.2026", "1.13.2026", "eile", "2026-10-14x"])
def test_an_impossible_date_is_refused_and_nothing_is_written(signed_in, typed):
    before = numbers_used()

    response = signed_in.post(CREATE, {"title": f"Vigane kuupäev {typed}", "received_date": typed})

    assert response.status_code == 400
    assert not Matter.objects.filter(title=f"Vigane kuupäev {typed}").exists()
    assert numbers_used() == before
    assert "received_date" in response.context["form"].errors


def test_a_deadline_before_saabus_warns_and_is_saved_only_when_confirmed(signed_in, specialist):
    """Was `test_a_deadline_before_saabus_is_accepted_evidence` (QA-WORKFLOW-D1:
    stored silently, overdue the moment it was filed). Inverted with the owner's
    approved warning (docs/adr/0166 §4): the save stops once and goes through
    when the person confirms the dates."""
    today = timezone.localdate()
    data = {
        "title": "Tähtaeg enne saabumist",
        "received_date": et(today),
        "response_deadline": et(today - dt.timedelta(days=30)),
    }

    warned = signed_in.post(CREATE, data)

    assert warned.status_code == 400
    assert not Matter.objects.filter(title="Tähtaeg enne saabumist").exists()

    confirmed = signed_in.post(CREATE, {**data, "kinnita_kuupaevad": "on"})

    assert confirmed.status_code == 302
    matter = Matter.objects.get(title="Tähtaeg enne saabumist")
    assert matter.response_deadline < matter.received_date


def test_empty_optional_fields_store_nothing_invented(signed_in, specialist):
    response = signed_in.post(
        CREATE,
        {
            "title": "Ainult pealkiri",
            "received_date": "",
            "response_deadline": "",
            "legal_instruments": [""],
            "stage": "",
            "notes": "   ",
        },
    )

    assert response.status_code == 302
    matter = Matter.objects.get(title="Ainult pealkiri")
    assert (matter.received_date, matter.response_deadline, matter.stage_id) == (None, None, None)
    assert matter.response_requested_at is None
    # ADR 0150 §1 as amended by 0166 §3 (QA-WORKFLOW-D2): the page offers no
    # «Määramata» owner, so a POST naming nobody makes the creator Vastutaja.
    assert matter.owner == specialist
    assert not matter.legal_instruments.exists()
    assert matter.track == ""


# ---------------------------------------------------------------------------
# Title and Õigusakt
# ---------------------------------------------------------------------------


def test_a_long_title_is_kept_whole_and_one_over_the_limit_refused(signed_in):
    long = ("Õigusakti eelnõu " * 80)[:1000].strip()
    ok = signed_in.post(CREATE, {"title": long})
    too_long = signed_in.post(CREATE, {"title": "Ä" * 1001})

    assert ok.status_code == 302
    assert Matter.objects.filter(title=long).count() == 1
    assert too_long.status_code == 400
    assert not Matter.objects.filter(title="Ä" * 1001).exists()
    page = signed_in.get(ok["Location"])
    assert page.status_code == 200


def test_a_whitespace_title_is_refused(signed_in):
    response = signed_in.post(CREATE, {"title": "   \t  "})
    assert response.status_code == 400
    assert not Matter.objects.filter(title="").exists()


def test_two_instruments_are_refused_one_is_stored(signed_in):
    two = signed_in.post(
        CREATE,
        {
            "title": "Kaks õigusakti",
            "legal_instruments": [instrument_pk("vtk"), instrument_pk("seadus")],
        },
    )
    one = signed_in.post(
        CREATE, {"title": "Üks õigusakt", "legal_instruments": [instrument_pk("seadus")]}
    )

    assert two.status_code == 400
    assert not Matter.objects.filter(title="Kaks õigusakti").exists()
    assert one.status_code == 302
    assert [i.key for i in Matter.objects.get(title="Üks õigusakt").legal_instruments.all()] == [
        "seadus"
    ]


def test_switching_the_instrument_to_and_from_the_proposal_on_refusals(signed_in, specialist):
    """Õigusakt switched across refused saves; the save that lands decides the track."""
    form = token(signed_in)
    base = {"intake": form, "received_date": et(timezone.localdate())}
    first = signed_in.post(
        CREATE, {**base, "title": "", "legal_instruments": [instrument_pk("koja-ettepanek")]}
    )
    second = signed_in.post(
        CREATE, {**base, "title": "", "legal_instruments": [instrument_pk("seadus")]}
    )
    final = signed_in.post(
        CREATE,
        {**base, "title": "Vahetatud õigusakt", "legal_instruments": [instrument_pk("seadus")]},
    )

    assert (first.status_code, second.status_code, final.status_code) == (400, 400, 302)
    matter = Matter.objects.get(title="Vahetatud õigusakt")
    assert matter.track == ""
    assert matter.received_date == timezone.localdate()


# ---------------------------------------------------------------------------
# Related Teemad: persisted relations, duplicates, removal, replay, refusal + retry
# ---------------------------------------------------------------------------


@pytest.fixture
def others(specialist):
    return [factories.MatterFactory(owner=specialist, title=f"Varasem {n}") for n in range(3)]


def test_replaying_the_form_with_related_teemad_links_once(signed_in, others):
    data = {
        "title": "Seostega korduv",
        "intake": token(signed_in),
        LINK_ON_CREATE_CHOSEN_FIELD: [str(others[0].pk), str(others[1].pk), str(others[0].pk)],
    }

    first = signed_in.post(CREATE, data)
    second = signed_in.post(CREATE, data)

    assert first.status_code == second.status_code == 302
    (created,) = Matter.objects.filter(title="Seostega korduv")
    assert relations_of(created) == {others[0].pk, others[1].pk}
    assert (
        ChangeEvent.objects.filter(
            matter=created, event_type=ChangeEventType.MATTER_RELATION_ADDED
        ).count()
        == 2
    )


def test_a_refused_save_links_nothing_and_the_retry_links_what_is_left(signed_in, others):
    form = token(signed_in)
    refused = signed_in.post(
        CREATE,
        {
            "title": "Seosed pärast viga",
            "intake": form,
            "received_date": "31.2.2026",
            LINK_ON_CREATE_CHOSEN_FIELD: [str(o.pk) for o in others],
        },
    )
    assert refused.status_code == 400
    assert not MatterRelation.objects.exists()

    # The person removes one (×) and corrects the date; the same form is saved.
    saved = signed_in.post(
        CREATE,
        {
            "title": "Seosed pärast viga",
            "intake": form,
            "received_date": et(timezone.localdate()),
            LINK_ON_CREATE_CHOSEN_FIELD: [str(others[0].pk), str(others[2].pk)],
        },
    )

    assert saved.status_code == 302
    (created,) = Matter.objects.filter(title="Seosed pärast viga")
    assert relations_of(created) == {others[0].pk, others[2].pk}
    assert relations_of(others[1]) == set()


def test_a_related_teema_that_is_closed_can_be_chosen(signed_in, specialist):
    stage = StageVocabulary.objects.get(key="in_force")
    closed = factories.MatterFactory(owner=specialist, title="Suletud varasem")
    from app.matters.services import change_stage

    change_stage(matter=closed, stage=stage, actor=specialist, follow_ups_confirmed=True)
    closed.refresh_from_db()
    assert not closed.is_open

    response = signed_in.post(
        CREATE, {"title": "Seos suletuga", LINK_ON_CREATE_CHOSEN_FIELD: [str(closed.pk)]}
    )

    assert response.status_code == 302
    assert relations_of(Matter.objects.get(title="Seos suletuga")) == {closed.pk}


def test_a_crafted_unknown_related_id_refuses_the_whole_save(signed_in, others):
    import uuid

    before = numbers_used()
    response = signed_in.post(
        CREATE,
        {
            "title": "Võltsitud seos",
            LINK_ON_CREATE_CHOSEN_FIELD: [str(others[0].pk), str(uuid.uuid4())],
        },
    )

    assert response.status_code == 400
    assert not Matter.objects.filter(title="Võltsitud seos").exists()
    assert not MatterRelation.objects.exists()
    assert numbers_used() == before


def test_a_test_data_related_teema_is_refused_for_real_work(signed_in, specialist):
    from app.matters.enums import MatterDataClass

    twin = factories.MatterFactory(owner=specialist, title="TEST kaksik")
    Matter.objects.filter(pk=twin.pk).update(data_class=MatterDataClass.TEST)

    response = signed_in.post(
        CREATE, {"title": "Päris teema TEST seosega", LINK_ON_CREATE_CHOSEN_FIELD: [str(twin.pk)]}
    )

    assert response.status_code == 400
    assert not Matter.objects.filter(title="Päris teema TEST seosega").exists()
    assert relations_of(twin) == set()


def test_a_reader_cannot_create_with_links(client, reader, others):
    client.force_login(reader)
    response = client.post(
        CREATE, {"title": "Lugeja loob", LINK_ON_CREATE_CHOSEN_FIELD: [str(others[0].pk)]}
    )
    assert response.status_code == 404
    assert not Matter.objects.filter(title="Lugeja loob").exists()
    assert not MatterRelation.objects.exists()


def test_csrf_is_enforced_on_create(specialist):
    client = Client(enforce_csrf_checks=True)
    client.force_login(specialist)
    response = client.post(CREATE, {"title": "Ilma CSRF-ita"})
    assert response.status_code == 403
    assert not Matter.objects.filter(title="Ilma CSRF-ita").exists()
