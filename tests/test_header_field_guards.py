"""The header's `Vastutaja` and `Saabus` are guarded; `+ Saabus` invents no history.

QA-WORKFLOW-03 and D5 (docs/adr/0166 §1, §5). `tests/qa_audit/
test_qa_workflow_concurrency.py` holds the two-person stale replays; this file
holds the partition that keeps a new header field from arriving without a
token, the register row's `Määra ▾`, field scoping, and the `+ Saabus` prefill.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.matters.enums import RecordMode
from app.matters.services import (
    GUARDED_MATTER_FIELDS,
    MATTER_FIELD_CONFLICT,
    UNGUARDED_MATTER_FIELDS,
    create_matter,
    matter_field_revision,
    owner_revision_for,
)
from app.matters.views import FIELD_SERVICES
from app.workflow.enums import Disposition
from app.workflow.models import StageVocabulary
from tests import factories

pytestmark = pytest.mark.django_db


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _field_url(matter, field: str) -> str:
    return reverse("matters:update_field", kwargs={"pk": matter.pk, "field": field})


def _detail(client: Client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _editor(body: str, matter, field: str) -> str:
    form = re.search(
        r'hx-post="' + re.escape(_field_url(matter, field)) + r'".*?</form>', body, re.S
    )
    assert form, f"no {field} editor on the page"
    return form.group(0)


# ---------------------------------------------------------------------------
# The partition: every inline field decides whether it is guarded
# ---------------------------------------------------------------------------


def test_every_inline_header_field_is_either_guarded_or_named_unguarded():
    """A new `update_field` field must choose: carry a revision, or be named here.

    The QA found `owner` and `received_date` silently unguarded because the
    revision tests only ever asked about the guarded fields (QA test-suite gap
    6). This asks the other question.
    """
    assert GUARDED_MATTER_FIELDS.isdisjoint(UNGUARDED_MATTER_FIELDS)
    # `brief_summary` is guarded by its own route (`update_summary`), not
    # through `update_field`; every other guarded field is an inline one.
    assert set(FIELD_SERVICES) - UNGUARDED_MATTER_FIELDS == GUARDED_MATTER_FIELDS - {
        "brief_summary"
    }
    assert UNGUARDED_MATTER_FIELDS <= set(FIELD_SERVICES)
    assert set(UNGUARDED_MATTER_FIELDS) == {"response_deadline", "policy_area_other"}


def test_every_guarded_field_has_a_revision(normal_matter):
    for field in GUARDED_MATTER_FIELDS:
        assert len(matter_field_revision(normal_matter, field)) == 32


def test_the_header_owner_and_saabus_editors_post_their_revision(normal_matter):
    head = factories.DepartmentHeadFactory()
    body = _detail(_client(head), normal_matter)

    owner_form = _editor(body, normal_matter, "owner")
    assert f'name="revision" value="{matter_field_revision(normal_matter, "owner")}"' in owner_form
    saabus_form = _editor(body, normal_matter, "received_date")
    assert (
        f'name="revision" value="{matter_field_revision(normal_matter, "received_date")}"'
        in saabus_form
    )


def test_a_tokenless_saabus_is_refused_with_the_conflict_answer(signed_in, normal_matter):
    before = normal_matter.received_date
    response = signed_in.post(
        _field_url(normal_matter, "received_date"),
        {"received_date": "1.2.2026"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 409
    assert MATTER_FIELD_CONFLICT in response.content.decode()
    normal_matter.refresh_from_db()
    assert normal_matter.received_date == before


def test_an_unrelated_change_does_not_make_the_owner_editor_stale(normal_matter):
    """Field-scoped: a colleague's stage move leaves my `Vastutaja` token valid."""
    head, colleague = factories.DepartmentHeadFactory(), factories.UserFactory()
    other = _client(factories.DepartmentHeadFactory())
    owner_token = matter_field_revision(normal_matter, "owner")
    moved = other.post(
        _field_url(normal_matter, "stage"),
        {
            "stage": str(StageVocabulary.objects.get(key="parliament").pk),
            "revision": matter_field_revision(normal_matter, "stage"),
        },
        HTTP_HX_REQUEST="true",
    )
    assert moved.status_code == 200

    response = _client(head).post(
        _field_url(normal_matter, "owner"),
        {"owner": str(colleague.pk), "revision": owner_token},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    normal_matter.refresh_from_db()
    assert normal_matter.owner == colleague
    assert normal_matter.stage.key == "parliament"


def test_the_header_redraws_with_fresh_tokens_after_a_save(normal_matter):
    """The same tab keeps working: the swapped header carries the new revision."""
    head, colleague = factories.DepartmentHeadFactory(), factories.UserFactory()
    response = _client(head).post(
        _field_url(normal_matter, "owner"),
        {"owner": str(colleague.pk), "revision": matter_field_revision(normal_matter, "owner")},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert (
        f'name="revision" value="{owner_revision_for(colleague.pk)}"' in response.content.decode()
    )


# ---------------------------------------------------------------------------
# The register row's `Määra ▾` (views.assign_owner)
# ---------------------------------------------------------------------------


def _unassigned():
    return factories.MatterFactory(owner=None)


def test_the_register_row_posts_the_owner_revision_it_was_drawn_with():
    matter = _unassigned()
    head = factories.DepartmentHeadFactory()
    page = _client(head).get(reverse("matters:matter_list")).content.decode()
    form = re.search(
        r'action="'
        + re.escape(reverse("matters:assign_owner", kwargs={"pk": matter.pk}))
        + r'".*?</form>',
        page,
        re.S,
    )
    assert form, "the unassigned row drew no Määra control"
    assert f'name="revision" value="{owner_revision_for(None)}"' in form.group(0)
    assert owner_revision_for(None) == matter_field_revision(matter, "owner")


def test_a_stale_maara_does_not_hand_on_a_file_a_colleague_just_took():
    matter = _unassigned()
    token = matter_field_revision(matter, "owner")
    first, second = factories.DepartmentHeadFactory(), factories.DepartmentHeadFactory()
    taker, other = factories.UserFactory(), factories.UserFactory()
    url = reverse("matters:assign_owner", kwargs={"pk": matter.pk})

    assert _client(first).post(url, {"owner": str(taker.pk), "revision": token}).status_code == 302
    stale_client = _client(second)
    stale = stale_client.post(url, {"owner": str(other.pk), "revision": token})

    matter.refresh_from_db()
    assert stale.status_code == 302
    assert matter.owner == taker
    said = [str(message) for message in get_messages(stale.wsgi_request)]
    assert any("vahepeal mujal muudetud" in text for text in said)


def test_a_maara_without_a_revision_is_refused():
    matter = _unassigned()
    head, person = factories.DepartmentHeadFactory(), factories.UserFactory()
    _client(head).post(
        reverse("matters:assign_owner", kwargs={"pk": matter.pk}), {"owner": str(person.pk)}
    )
    matter.refresh_from_db()
    assert matter.owner is None


# ---------------------------------------------------------------------------
# D5 — `+ Saabus` on closed and archive records starts empty
# ---------------------------------------------------------------------------


def _saabus_input(body: str, matter) -> str:
    form = _editor(body, matter, "received_date")
    found = re.search(r'<input[^>]*name="received_date"[^>]*>', form, re.S)
    assert found
    return found.group(0)


def _today_et() -> str:
    today = timezone.localdate()
    return f"{today.day}.{today.month}.{today.year}"


def test_open_work_still_starts_saabus_from_today(signed_in, specialist):
    matter = create_matter(title="Avatud, saabumata", actor=specialist, owner=specialist)
    field = _saabus_input(_detail(signed_in, matter), matter)
    assert f'value="{_today_et()}"' in field
    assert "required" not in field


def test_a_closed_matter_starts_saabus_empty(signed_in, specialist):
    matter = factories.MatterFactory(
        title="Suletud, saabumata",
        owner=specialist,
        received_date=None,
        is_open=False,
        closed_at=timezone.now(),
        disposition=Disposition.COMPLETED,
    )

    field = _saabus_input(_detail(signed_in, matter), matter)

    assert 'value=""' in field
    assert "required" in field


def test_an_archive_record_starts_saabus_empty(signed_in):
    matter = factories.ArchiveMatterFactory(title="Arhiivikirje 2019", received_date=None)
    assert matter.record_mode == RecordMode.ARCHIVE

    body = _detail(signed_in, matter)

    field = _saabus_input(body, matter)
    assert 'value=""' in field
    assert _today_et() not in field


def test_an_explicit_historical_saabus_is_saved(signed_in):
    matter = factories.ArchiveMatterFactory(title="Arhiivikirje 2018", received_date=None)
    response = signed_in.post(
        _field_url(matter, "received_date"),
        {"received_date": "3.4.2018", "revision": matter_field_revision(matter, "received_date")},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    matter.refresh_from_db()
    assert matter.received_date == dt.date(2018, 4, 3)
