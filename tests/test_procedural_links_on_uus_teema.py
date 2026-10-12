"""`Menetluse link` is no longer asked on `Uus teema` (owner's decision of 2026-10-12).

**Superseded file.** This module pinned the link recorded while the Teema was
being created (docs/adr/0089 §13, 0094 §3, 0130 §3): atomic with the Teema,
kept across a refusal, silent when untouched. The owner removed the question
from creation on 2026-10-12 (docs/adr/0130 amendment): a lawyer does not need
to answer it while filing. Those creation tests are retired with the control,
by that decision (docs/adr/0155 §3).

What is pinned now:

* the page asks nothing about a link, and a crafted POST naming one — address,
  kind or label — writes no link and no link event;
* the capability is intact: `MatterLinkForm` (`Muuda teemat`) still builds on
  `ProceduralLinkCreateForm` and stores the neutral kind, and the correction
  form still offers the whole vocabulary.

The record's own rules are `tests/test_procedural_links.py`.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.enums import ProceduralLinkKind
from app.matters.forms import MatterLinkForm, ProceduralLinkCreateForm
from app.matters.models import Matter, MatterProceduralLink

pytestmark = pytest.mark.django_db

EIS_URL = "https://eelnoud.valitsus.ee/main/mount/docList/8f2c1a30-0000-0000-0000-000000000001"


def test_uus_teema_asks_nothing_about_a_link(signed_in):
    body = signed_in.get(reverse("matters:matter_create")).content.decode()

    assert 'name="menetlus-url"' not in body
    assert 'name="menetlus-label"' not in body
    assert 'name="menetlus-kind"' not in body
    assert 'id="menetluse-link"' not in body


def test_a_crafted_link_on_create_writes_nothing(signed_in):
    response = signed_in.post(
        reverse("matters:matter_create"),
        {
            "title": "Võltsitud menetluse link",
            "menetlus-url": EIS_URL,
            "menetlus-kind": ProceduralLinkKind.EIS,
            "menetlus-label": "EIS",
        },
    )

    assert response.status_code == 302
    matter = Matter.objects.get(title="Võltsitud menetluse link")
    assert not MatterProceduralLink.objects.filter(matter=matter).exists()
    assert not ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.PROCEDURAL_LINK_RECORDED
    ).exists()


def test_an_invalid_crafted_address_refuses_nothing_either(signed_in):
    """There is no block to refuse: the Teema is filed as if nothing was sent."""
    response = signed_in.post(
        reverse("matters:matter_create"),
        {"title": "Vigane võltsitud link", "menetlus-url": "javascript:alert(1)"},
    )

    assert response.status_code == 302
    matter = Matter.objects.get(title="Vigane võltsitud link")
    assert not MatterProceduralLink.objects.filter(matter=matter).exists()


def test_the_capability_stays_on_muuda_teemat():
    """`Muuda teemat`'s link form still builds on the shared base and asks the
    address only, filed under the neutral kind (docs/adr/0094 §3, 0130 §3)."""
    assert issubclass(MatterLinkForm, ProceduralLinkCreateForm)
    assert "label" not in ProceduralLinkCreateForm().fields
    assert ProceduralLinkCreateForm.STORED_KIND == ProceduralLinkKind.OTHER.value


def test_the_withdrawn_kinds_are_still_supported_elsewhere():
    """A vocabulary is not retired because one form stopped asking for it."""
    from app.matters.forms import ProceduralLinkEditForm

    offered = {value for value, _label in ProceduralLinkEditForm().fields["kind"].choices if value}
    assert offered == set(ProceduralLinkKind.values)
