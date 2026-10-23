"""`Uus teema` is filed once by accident; dates out of order are warned about.

QA-COORD-01 / D3, D-UUS (docs/adr/0166 §2–§4). The browser half of the Back
defect is `e2e/test_qa_audit_back_resubmit.py`; these are the server rules it
stands on: a replayed token, a fresh token within the window, the earlier
tokens a restored form posts, the deliberate «Loo siiski uus», the creator
default on a crafted POST, and the `Arvamuse tähtaeg` < `Saabus` warning on
`Uus teema` and on both header editors.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.matters import duplicate_intake
from app.matters.date_order import CONFIRM_LABEL, DEADLINE_BEFORE_RECEIVED
from app.matters.models import Matter
from app.matters.services import create_matter, matter_field_revision
from app.matters.staging import MatterIntakeSession
from app.matters.views import CREATE_FORM_ALREADY_SUBMITTED
from tests import factories

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")


def _client(user: Any) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _token(client: Client) -> str:
    page = client.get(CREATE).content.decode()
    found = re.search(r'name="intake" value="([^"]+)"', page)
    assert found
    return found.group(1)


def _form(title: str, owner: Any, token: str, **extra: Any) -> dict[str, Any]:
    return {"title": title, "owner": str(owner.pk), "intake": token, **extra}


def _titled(title: str) -> list[Matter]:
    return list(Matter.objects.filter(title=title))


# ---------------------------------------------------------------------------
# Accidental duplicates
# ---------------------------------------------------------------------------


def test_the_same_token_replayed_lands_on_the_first_teema(specialist):
    client = _client(specialist)
    data = _form("Kordus sama vormiga", specialist, _token(client))
    first = client.post(CREATE, data)
    replay = client.post(CREATE, data, follow=True)

    assert len(_titled("Kordus sama vormiga")) == 1
    assert replay.redirect_chain[-1][0] == first["Location"]
    assert CREATE_FORM_ALREADY_SUBMITTED in replay.content.decode()


def test_a_fresh_form_with_the_title_just_filed_asks_first(specialist):
    """Browser Back: the typed form restored under a new token, no script."""
    client = _client(specialist)
    client.post(CREATE, _form("Tagasi ja uuesti", specialist, _token(client)))
    (existing,) = _titled("Tagasi ja uuesti")

    again = client.post(CREATE, _form("  tagasi JA  uuesti ", specialist, _token(client)))

    assert again.status_code == 400
    body = again.content.decode()
    assert "Sama pealkirjaga teema loodi just" in body
    assert reverse("matters:matter_detail", kwargs={"pk": existing.pk}) in body
    assert 'name="loo_siiski_uus"' in body
    assert Matter.objects.filter(title__iexact="tagasi ja uuesti").count() == 1
    assert Matter.objects.filter(title__icontains="uuesti").count() == 1


def test_a_title_just_filed_through_saabunud_asks_first_on_uus_teema(specialist):
    """Both doors leave the same record (`record_created_matter`), so both count.

    «X» filed from `Saabunud → Lisa`, then «X» typed into a fresh `Uus teema`
    form within `RECENT_WINDOW`, is the same accidental resubmission.
    """
    from django.core.files.uploadedfile import SimpleUploadedFile

    client = _client(specialist)
    page = client.get(reverse("matters:intake")).content.decode()
    drawn = re.search(r'name="intake" value="([0-9a-f-]{36})"', page)
    assert drawn
    filed = client.post(
        reverse("matters:intake"),
        {
            "title": "Kahest uksest",
            "intake": drawn.group(1),
            "uploads": [SimpleUploadedFile("kiri.pdf", b"%PDF-1.4 synthetic incoming")],
        },
    )
    assert filed.status_code == 302
    (existing,) = _titled("Kahest uksest")

    again = client.post(CREATE, _form("Kahest uksest", specialist, _token(client)))

    assert again.status_code == 400
    body = again.content.decode()
    # The block's sentence (`RECENT_DUPLICATE`, drawn with the title it found).
    assert again.context["recent_duplicate"] == existing
    assert "Sama pealkirjaga teema loodi just: «Kahest uksest»" in body
    assert reverse("matters:matter_detail", kwargs={"pk": existing.pk}) in body
    assert len(_titled("Kahest uksest")) == 1


def test_loo_siiski_uus_files_the_deliberate_second_teema(specialist):
    client = _client(specialist)
    client.post(CREATE, _form("Teadlikult kaks", specialist, _token(client)))
    token = _token(client)
    refused = client.post(CREATE, _form("Teadlikult kaks", specialist, token))
    assert refused.status_code == 400
    # The refused save rolled its claim back: the same token files exactly once.
    carried = re.search(r'name="intake" value="([^"]+)"', refused.content.decode())
    assert carried and carried.group(1) == token

    confirmed = client.post(CREATE, _form("Teadlikult kaks", specialist, token, loo_siiski_uus="1"))
    replayed = client.post(CREATE, _form("Teadlikult kaks", specialist, token, loo_siiski_uus="1"))

    assert confirmed.status_code == 302
    assert replayed.status_code == 302
    created = _titled("Teadlikult kaks")
    assert len(created) == 2
    assert len({matter.reference_number for matter in created}) == 2


def test_outside_the_window_a_fresh_form_files_without_asking(specialist):
    client = _client(specialist)
    client.post(CREATE, _form("Eile loodud", specialist, _token(client)))
    MatterIntakeSession.objects.filter(owner=specialist).update(
        consumed_at=timezone.now() - duplicate_intake.RECENT_WINDOW - dt.timedelta(minutes=1)
    )

    again = client.post(CREATE, _form("Eile loodud", specialist, _token(client)))

    assert again.status_code == 302
    assert len(_titled("Eile loodud")) == 2


def test_a_restored_form_naming_its_earlier_token_asks_however_long_ago(specialist):
    """The history-state layer: the earlier token identifies the form's own Teema."""
    client = _client(specialist)
    first_token = _token(client)
    client.post(CREATE, _form("Vana vorm taastatud", specialist, first_token))
    MatterIntakeSession.objects.filter(pk=first_token).update(
        consumed_at=timezone.now() - dt.timedelta(days=2)
    )

    again = client.post(
        CREATE,
        _form("Vana vorm taastatud", specialist, _token(client), intake_earlier=first_token),
    )

    assert again.status_code == 400
    assert "Sama pealkirjaga teema loodi just" in again.content.decode()
    assert len(_titled("Vana vorm taastatud")) == 1


def test_a_different_title_on_a_restored_form_is_a_new_teema(specialist):
    client = _client(specialist)
    first_token = _token(client)
    client.post(CREATE, _form("Esimene pealkiri", specialist, first_token))

    other = client.post(
        CREATE,
        _form("Teine pealkiri", specialist, _token(client), intake_earlier=first_token),
    )

    assert other.status_code == 302
    assert len(_titled("Teine pealkiri")) == 1


def test_a_colleagues_same_title_is_not_a_duplicate(specialist, other_specialist):
    """Only the person's own recent work counts: no global title uniqueness."""
    mine = _client(specialist)
    mine.post(CREATE, _form("Ühine pealkiri", specialist, _token(mine)))
    theirs = _client(other_specialist)

    answer = theirs.post(CREATE, _form("Ühine pealkiri", other_specialist, _token(theirs)))

    assert answer.status_code == 302
    assert len(_titled("Ühine pealkiri")) == 2


def test_another_persons_earlier_token_names_nothing(specialist, other_specialist):
    mine = _client(specialist)
    token = _token(mine)
    mine.post(CREATE, _form("Võõras märk", specialist, token))
    MatterIntakeSession.objects.filter(pk=token).update(
        consumed_at=timezone.now() - dt.timedelta(days=1)
    )
    theirs = _client(other_specialist)

    answer = theirs.post(
        CREATE, _form("Võõras märk", other_specialist, _token(theirs), intake_earlier=token)
    )

    assert answer.status_code == 302


def test_a_teema_created_elsewhere_is_never_mistaken_for_a_resubmission(specialist):
    """Imports, seeds and service calls leave no intake session and never count."""
    create_matter(title="Teenusega loodud", actor=specialist, owner=specialist)
    client = _client(specialist)

    answer = client.post(CREATE, _form("Teenusega loodud", specialist, _token(client)))

    assert answer.status_code == 302
    assert len(_titled("Teenusega loodud")) == 2


# ---------------------------------------------------------------------------
# The creator is the Vastutaja on a POST that names nobody
# ---------------------------------------------------------------------------


def test_a_crafted_post_without_owner_makes_the_creator_owner(specialist):
    client = _client(specialist)
    response = client.post(CREATE, {"title": "Vastutajata katse", "intake": _token(client)})
    assert response.status_code == 302
    (matter,) = _titled("Vastutajata katse")
    assert matter.owner == specialist


def test_a_chosen_colleague_is_never_replaced(specialist):
    head = factories.DepartmentHeadFactory()
    colleague = factories.UserFactory()
    client = _client(head)
    client.post(CREATE, _form("Kolleegile antud", colleague, _token(client)))
    (matter,) = _titled("Kolleegile antud")
    assert matter.owner == colleague


# ---------------------------------------------------------------------------
# `Arvamuse tähtaeg` before `Saabus`
# ---------------------------------------------------------------------------


def test_uus_teema_warns_when_the_deadline_precedes_saabus(specialist):
    client = _client(specialist)
    token = _token(client)
    data = _form(
        "Tähtaeg enne saabumist",
        specialist,
        token,
        received_date="10.9.2026",
        response_deadline="1.9.2026",
    )

    warned = client.post(CREATE, data)

    assert warned.status_code == 400
    body = warned.content.decode()
    assert DEADLINE_BEFORE_RECEIVED in body
    assert CONFIRM_LABEL in body
    assert 'name="kinnita_kuupaevad"' in body
    assert not _titled("Tähtaeg enne saabumist")

    saved = client.post(CREATE, {**data, "kinnita_kuupaevad": "on"})

    assert saved.status_code == 302
    (matter,) = _titled("Tähtaeg enne saabumist")
    assert matter.received_date == dt.date(2026, 9, 10)
    assert matter.response_deadline == dt.date(2026, 9, 1)


def test_uus_teema_dates_in_order_need_no_confirmation(specialist):
    client = _client(specialist)
    response = client.post(
        CREATE,
        _form(
            "Tähtaeg pärast saabumist",
            specialist,
            _token(client),
            received_date="1.9.2026",
            response_deadline="1.9.2026",
        ),
    )
    assert response.status_code == 302


def _header_matter(specialist, *, received=None, deadline=None) -> Matter:
    return create_matter(
        title="Päise kuupäevad",
        actor=specialist,
        owner=specialist,
        received_date=received,
        response_deadline=deadline,
        response_requested_at=timezone.now() if deadline else None,
    )


def test_a_header_saabus_after_the_deadline_warns_and_saves_on_confirmation(specialist):
    matter = _header_matter(specialist, deadline=dt.date(2026, 9, 1))
    client = _client(specialist)
    url = reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "received_date"})
    data = {
        "received_date": "10.9.2026",
        "revision": matter_field_revision(matter, "received_date"),
    }

    warned = client.post(url, data, HTTP_HX_REQUEST="true")

    assert warned.status_code == 400
    body = warned.content.decode()
    assert DEADLINE_BEFORE_RECEIVED in body
    assert CONFIRM_LABEL in body
    assert 'name="kinnita_kuupaevad" value="on"' in body
    assert f'name="revision" value="{data["revision"]}"' in body
    matter.refresh_from_db()
    assert matter.received_date is None

    saved = client.post(url, {**data, "kinnita_kuupaevad": "on"}, HTTP_HX_REQUEST="true")

    assert saved.status_code == 200
    matter.refresh_from_db()
    assert matter.received_date == dt.date(2026, 9, 10)


def test_a_header_deadline_before_saabus_warns_and_saves_on_confirmation(specialist):
    matter = _header_matter(specialist, received=dt.date(2026, 9, 10))
    client = _client(specialist)
    from app.matters.response_deadlines import deadline_revision

    url = reverse("matters:response_deadline", kwargs={"pk": matter.pk})
    data = {
        "tegevus": "muuda",
        "response_deadline": "1.9.2026",
        "revision": deadline_revision(matter),
    }

    warned = client.post(url, data, HTTP_HX_REQUEST="true")

    assert warned.status_code == 400
    assert DEADLINE_BEFORE_RECEIVED in warned.content.decode()
    matter.refresh_from_db()
    assert matter.response_deadline is None

    saved = client.post(url, {**data, "kinnita_kuupaevad": "on"}, HTTP_HX_REQUEST="true")

    assert saved.status_code == 200
    matter.refresh_from_db()
    assert matter.response_deadline == dt.date(2026, 9, 1)
