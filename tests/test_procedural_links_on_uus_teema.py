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
    block = body[body.index('id="menetluse-link"') :]
    block = block[: block.index("</details>")]
    assert 'value="EIS"' in block


def test_the_block_arrives_folded_and_says_nothing_it_does_not_hold(signed_in):
    """docs/adr/0088's complaint, answered rather than re-created.

    The same round of lawyer feedback that asked for this block also said the
    capture page reads as a survey when too much on it is expanded at once. So
    it takes `Valdkond`'s own shape: a closed disclosure, `data-stay-closed` so
    the pre-selected `EIS` chip does not unfold it, and no summary suffix until
    there is an address to summarise.
    """
    body = _form(signed_in)
    block = body[body.index('id="menetluse-link"') :]
    head = block[: block.index("</summary>")]

    assert "chipdetails" in head
    assert "data-stay-closed" in head
    assert " open" not in head.split(">", 1)[0]
    # The word, and nothing claiming an answer nobody gave.
    assert "Menetluse link" in head
    assert "EIS" not in head


def test_a_filled_block_says_so_on_its_summary_without_being_opened(signed_in):
    """A shut field that hid the answer would be quieter and worse.

    After a refusal elsewhere on the page the fold stays shut — the summary is
    what says it is holding something, so nobody has to open it to find out.
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
    block = body[body.index('id="menetluse-link"') :]
    head = block[: block.index("</summary>")]

    assert response.status_code == 400
    assert "Riigikogu: Eelnõu 123 SE" in head
    # Nothing is wrong *inside* this block, so it does not unfold itself.
    assert " open" not in head.split(">", 1)[0]


def test_a_refusal_this_block_owns_opens_it(signed_in):
    """The box somebody has to correct must be reachable, with scripting off too."""
    response = _create(
        signed_in,
        **{
            "menetlus-kind": ProceduralLinkKind.EIS.value,
            "menetlus-url": "javascript:alert(1)",
        },
    )
    body = response.content.decode()
    block = body[body.index('id="menetluse-link"') :]

    assert response.status_code == 400
    assert " open" in block.split(">", 1)[0]
    assert "http:// või https://" in block


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


# ===========================================================================
# QA-01 — an untouched optional block refuses nothing, whatever else fails
# ===========================================================================
#
# The block arrives with `EIS` pre-selected, so a real browser posts
# `menetlus-kind=EIS` on **every** save from this page, touched or not. That is
# the state these tests describe, and the one the bug lived in: the form was
# bound unconditionally, so a save refused for a reason somewhere else printed
# «Menetluse link vajab veebiaadressi.» under an address box nobody had typed
# in. `wants_link` is the single definition of whether this block was answered
# — the address, and nothing else — and the form now validates only when it
# says so.


UNTOUCHED = {
    "menetlus-kind": ProceduralLinkKind.EIS.value,
    "menetlus-url": "",
    "menetlus-label": "",
}


def test_a_refusal_elsewhere_prints_no_address_refusal_under_an_untouched_block(signed_in):
    """QA-01, case A. A blank title is one problem, and it is the only one shown.

    The pre-selected chip is what makes this the ordinary path rather than an
    edge case: every browser submit carries `menetlus-kind`, so an
    unconditionally validated block refused *every* save that had not used it.
    """
    response = _create(signed_in, title="", **UNTOUCHED)
    body = response.content.decode()

    assert response.status_code == 400
    assert "Menetluse link vajab veebiaadressi." not in body
    assert "Vali, millise menetluse allikaga" not in body
    assert not Matter.objects.exists()
    assert not MatterProceduralLink.objects.exists()


def test_the_untouched_block_stays_folded_when_something_else_is_refused(signed_in):
    """A fold opens for a box somebody has to correct, not for one they never used."""
    response = _create(signed_in, title="", **UNTOUCHED)
    body = response.content.decode()
    block = body[body.index('id="menetluse-link"') :]

    assert response.status_code == 400
    assert " open" not in block.split(">", 1)[0]
    # And it says nothing it does not hold.
    assert "Menetluse link ·" not in block


def test_the_two_first_steps_refusal_is_the_only_one_shown(signed_in):
    """QA-01, case B. The real refusal, and nothing invented beside it.

    `Järgmiseks` and `Koostan arvamuse` both want the Matter's single open
    action, so answering both is refused — and everything typed comes back.
    """
    response = _create(
        signed_in,
        **{
            **UNTOUCHED,
            "next-text": "Helista ministeeriumi",
            "arvamus-prepare_by": "2026-12-01",
        },
    )
    body = response.content.decode()

    assert response.status_code == 400
    assert "üks pooleli tegevus" in body
    assert "Menetluse link vajab veebiaadressi." not in body
    # What was typed survives the refusal.
    assert "Helista ministeeriumi" in body
    assert "2026-12-01" in body
    assert not Matter.objects.exists()


def test_an_untouched_block_saves_the_teema_and_writes_no_link(signed_in):
    """QA-01, case C. The ordinary submit from a real browser: silent.

    `test_an_untouched_block_writes_nothing_and_refuses_nothing` above posts no
    `menetlus-*` key at all; this one posts the chip the page actually sends.
    """
    response = _create(signed_in, **UNTOUCHED)

    assert response.status_code == 302
    matter = Matter.objects.get(title="Pakendiseaduse muutmise eelnõu")
    assert not MatterProceduralLink.objects.filter(matter=matter).exists()


def test_an_address_with_no_kind_is_still_refused_on_the_chip_row(signed_in):
    """QA-01, case D. An answered block is validated exactly as before.

    The address is what says this block was used, so clearing the chip beside a
    typed address is a real omission rather than an untouched form — and it is
    refused before anything is written, with what was typed back in the box.
    """
    response = _create(signed_in, **{"menetlus-kind": "", "menetlus-url": EIS_URL})
    body = response.content.decode()

    assert response.status_code == 400
    assert "Vali, millise menetluse allikaga" in body
    assert f'value="{EIS_URL}"' in body
    assert not Matter.objects.exists()
    assert not MatterProceduralLink.objects.exists()


def test_a_label_beside_an_untouched_address_still_refuses_nothing(signed_in):
    """The address alone decides it — `wants_link`, unchanged by this fix.

    A name for a link that does not exist is not a request to record one, so it
    writes nothing and refuses nothing. `test_a_label_without_an_address_writes_nothing`
    owns the saved outcome; this one owns the absence of a refusal on the page.
    """
    response = _create(signed_in, title="", **{**UNTOUCHED, "menetlus-label": "Eelnõu 123 SE"})
    body = response.content.decode()

    assert response.status_code == 400
    assert "Menetluse link vajab veebiaadressi." not in body
    # What was typed into the optional box still comes back.
    assert 'value="Eelnõu 123 SE"' in body
