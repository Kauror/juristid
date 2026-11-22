"""`Menetluse link` can be taken off the file (owner, 2026-11-22; `removal`, key «menetluslink»)."""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.enums import ProceduralLinkKind
from app.matters.models import MatterProceduralLink
from app.matters.removal import remove_matter_record
from app.matters.services import record_procedural_link

pytestmark = pytest.mark.django_db

URL = "https://adr.envir.ee/et/document.html?id=b89d"


def _link(matter):
    return record_procedural_link(
        matter=matter,
        kind=ProceduralLinkKind.MINISTRY_REGISTER,
        url=URL,
        label="1-4/26/2491-14",
        actor=matter.owner,
    )


def test_removing_a_link_takes_it_off_the_rail_and_logs_it(signed_in, normal_matter):
    link = _link(normal_matter)
    page = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}))
    assert "1-4/26/2491-14" in page.content.decode()
    assert (
        reverse(
            "matters:remove_record",
            kwargs={"pk": normal_matter.pk, "kind": "menetluslink", "record_id": link.pk},
        )
        in page.content.decode()
    )

    response = signed_in.post(
        reverse(
            "matters:remove_record",
            kwargs={"pk": normal_matter.pk, "kind": "menetluslink", "record_id": link.pk},
        ),
        {"revision": link.updated_at.isoformat()},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    link.refresh_from_db()
    assert link.removed_at is not None
    assert (
        not MatterProceduralLink.objects.visible_to(normal_matter.owner)
        .filter(matter=normal_matter)
        .exists()
    )
    assert ChangeEvent.objects.filter(
        event_type=ChangeEventType.PROCEDURAL_LINK_REMOVED, matter=normal_matter
    ).exists()
    after = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}))
    assert "1-4/26/2491-14" not in after.content.decode()


def test_adding_a_removed_address_again_brings_the_row_back(normal_matter):
    link = _link(normal_matter)
    remove_matter_record(
        matter_id=normal_matter.pk,
        kind_key="menetluslink",
        record_id=link.pk,
        actor=normal_matter.owner,
    )

    again = _link(normal_matter)

    assert again.pk == link.pk and again.removed_at is None
    assert MatterProceduralLink.objects.visible_to(normal_matter.owner).count() == 1
