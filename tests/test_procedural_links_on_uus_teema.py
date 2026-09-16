"""A `Menetluse link` recorded while the Teema is being created (docs/adr/0089 §13).

The lawyer feedback asked for this by name: a Matter frequently arrives *from* a
proceeding, the address is on screen while the file is being opened, and making
somebody create the Teema and then go and find it again is how it ends up in a
browser history instead of on the file.

The rules that matter here are transactional rather than visual, because they
are the ones a page cannot show:

* **atomic** — the Matter and its link land together or neither does;
* **a refused save keeps what was typed**, including the address, which is the
  one fact somebody opened this block to record;
* **a refusal leaves no orphan** — not a link with no Matter, and not a Matter
  with a half-written link;
* **untouched is silent** — three empty boxes write no row, no event and no
  refusal, because nothing on `Uus teema` but the title is required;
* **a retry is not a duplicate.**

The block's own validation — the address rule, the vocabulary, the label bound —
is `tests/test_procedural_links.py`, which owns the record. This file owns the
create path.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.enums import ProceduralLinkKind
from app.matters.models import Matter, MatterProceduralLink

pytestmark = pytest.mark.django_db

EIS_URL = "https://eelnoud.valitsus.ee/main/mount/docList/8f2c1a30-0000-0000-0000-000000000001"


def _create(client, **fields):
    payload = {"title": "Pakendiseaduse muutmise eelnõu"}
    payload.update(fields)
    return client.post(reverse("matters:matter_create"), payload)


def _form(client) -> str:
    response = client.get(reverse("matters:matter_create"))
    assert response.status_code == 200
    return response.content.decode()


def test_the_block_is_on_the_form_and_asks_three_optional_questions(signed_in):
    """One row, prefixed, and nothing on it required.

    A repeating control would put an empty table on a form whose whole design is
    that nothing on it is required (docs/adr/0089 §7).
    """
    body = _form(signed_in)

    assert 'id="menetluse-link"' in body
    assert "Menetluse link" in body
    assert 'name="menetlus-kind"' in body
    assert 'name="menetlus-url"' in body
    assert 'name="menetlus-label"' in body
    # One address box, not five.
    assert body.count('name="menetlus-url"') == 1
    # `EIS` arrives selected, because that is where most files come from — and
    # it records nothing on its own.
    kind_boxes = body[body.index('id="menetluse-link"') :]
    kind_boxes = kind_boxes[: kind_boxes.index("</div>\n</div>")]
    assert 'value="EIS"' in kind_boxes


def test_creating_a_matter_with_a_link_saves_both(signed_in, specialist):
    """Scenario D. The ordinary case: one Teema, one reference, one act."""
    response = _create(
        signed_in,
        **{
            "menetlus-kind": ProceduralLinkKind.EIS.value,
            "menetlus-url": EIS_URL,
            "menetlus-label": "Eelnõu 123 SE",
        },
    )

    assert response.status_code == 302
    matter = Matter.objects.get(title="Pakendiseaduse muutmise eelnõu")
    link = MatterProceduralLink.objects.get(matter=matter)
    assert link.kind == ProceduralLinkKind.EIS
    assert link.url == EIS_URL
    assert link.label == "Eelnõu 123 SE"
    assert link.created_by == specialist
    assert ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.PROCEDURAL_LINK_RECORDED
    ).exists()


def test_an_untouched_block_writes_nothing_and_refuses_nothing(signed_in):
    """Scenario C, on the create path. Silent when nobody used it.

    The chip row arrives with `EIS` selected, so a form binding unconditionally
    would refuse every save that had not touched this block — with «Menetluse
    link vajab veebiaadressi.» under a box nobody had typed in.
    """
    response = _create(signed_in)

    assert response.status_code == 302
    matter = Matter.objects.get(title="Pakendiseaduse muutmise eelnõu")
    assert not MatterProceduralLink.objects.filter(matter=matter).exists()


def test_a_label_without_an_address_writes_nothing(signed_in):
    """A name for a link that does not exist is not a request to record one."""
    response = _create(signed_in, **{"menetlus-label": "Eelnõu 123 SE"})

    assert response.status_code == 302
    assert not MatterProceduralLink.objects.exists()


def test_a_refused_address_refuses_the_whole_save_and_creates_no_matter(signed_in):
    """Scenario D's second half: **atomic**, in the direction that costs most.

    A Teema created while its link was refused would be a file somebody believes
    carries a reference it does not — and they would find out months later.
    """
    response = _create(
        signed_in,
        **{
            "menetlus-kind": ProceduralLinkKind.EIS.value,
            "menetlus-url": "javascript:alert(1)",
        },
    )

    assert response.status_code == 400
    assert not Matter.objects.filter(title="Pakendiseaduse muutmise eelnõu").exists()
    assert not MatterProceduralLink.objects.exists()


def test_a_refused_save_keeps_the_typed_address_and_kind(signed_in):
    """Scenario D's third half. Losing it is the defect this block is prone to.

    A browser cannot put a value back into a box the server did not re-render,
    and the address is the one fact somebody opened this block to record.
    """
    response = _create(
        signed_in,
        title="",
        **{
            "menetlus-kind": ProceduralLinkKind.RIIGIKOGU.value,
            "menetlus-url": EIS_URL,
            "menetlus-label": "Eelnõu 123 SE",
        },
    )
    body = response.content.decode()

    assert response.status_code == 400
    assert f'value="{EIS_URL}"' in body
    assert 'value="Eelnõu 123 SE"' in body
    block = body[body.index('id="menetluse-link"') :]
    chosen = block[block.index('value="RIIGIKOGU"') :]
    assert "checked" in chosen[: chosen.index(">")]


def test_a_refused_matter_leaves_no_orphan_link(signed_in):
    """The other direction: a Teema refused after its link was read.

    An empty title is refused by the form before the transaction opens, so this
    is belt and braces — but an orphan link row is exactly the residue a
    non-atomic create would leave, and it would be invisible until somebody
    counted rows.
    """
    response = _create(
        signed_in,
        title="",
        **{"menetlus-kind": ProceduralLinkKind.EIS.value, "menetlus-url": EIS_URL},
    )

    assert response.status_code == 400
    assert MatterProceduralLink.objects.count() == 0


def test_retrying_after_a_refusal_files_one_link(signed_in):
    """Scenario D's last half. A corrected retry is one Teema and one reference."""
    refused = _create(
        signed_in,
        title="",
        **{"menetlus-kind": ProceduralLinkKind.EIS.value, "menetlus-url": EIS_URL},
    )
    assert refused.status_code == 400

    accepted = _create(
        signed_in, **{"menetlus-kind": ProceduralLinkKind.EIS.value, "menetlus-url": EIS_URL}
    )

    assert accepted.status_code == 302
    matter = Matter.objects.get(title="Pakendiseaduse muutmise eelnõu")
    assert MatterProceduralLink.objects.filter(matter=matter).count() == 1


def test_the_link_reads_on_the_matter_immediately_after_creation(signed_in):
    """The point of recording it here: it is on the file when the file opens."""
    _create(signed_in, **{"menetlus-kind": ProceduralLinkKind.EIS.value, "menetlus-url": EIS_URL})
    matter = Matter.objects.get(title="Pakendiseaduse muutmise eelnõu")

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()

    assert 'id="menetluse-lingid"' in body
    assert f'href="{EIS_URL}"' in body


def test_the_block_posts_under_its_own_prefix(signed_in):
    """docs/adr/0089 §13. The two forms on this page cannot collide by accident.

    `MatterCreateForm` is being worked on concurrently by another package, so a
    field name added there must not be able to reach this form's `clean` — which
    a shared namespace would allow and nothing would catch.
    """
    body = _form(signed_in)

    # The unprefixed spellings belong to nothing on this page.
    assert 'name="url"' not in body
    assert 'name="kind"' not in body
    assert 'name="label"' not in body
