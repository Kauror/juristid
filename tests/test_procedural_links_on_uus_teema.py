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
* **untouched is silent** — empty boxes write no row, no event and no refusal,
  because nothing on `Uus teema` but the title is required;
* **a retry is not a duplicate.**

What docs/adr/0094 §3 changed is the shape of the question, not any of those. The
block is open on arrival rather than folded, and it asks for an address and an
optional name — the five-chip source row is withdrawn from this surface, and every
row filed here is stored under `ProceduralLinkKind.OTHER`, which is what an
unclassified link truthfully is. Nothing infers a kind from the address.

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
from app.matters.forms import ProceduralLinkCreateForm
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


def _block(body: str) -> str:
    start = body.index('id="menetluse-link"')
    return body[start : body.index("</fieldset>", start)]


def test_the_block_is_on_the_form_and_asks_two_optional_questions(signed_in):
    """One row, prefixed, and nothing on it required.

    A repeating control would put an empty table on a form whose whole design is
    that nothing on it is required (docs/adr/0089 §7).
    """
    body = _form(signed_in)

    assert 'id="menetluse-link"' in body
    assert "Menetluse link" in body
    assert 'name="menetlus-url"' in body
    assert 'name="menetlus-label"' in body
    # One address box, not five.
    assert body.count('name="menetlus-url"') == 1


def test_the_source_chips_are_not_on_the_page_at_all(signed_in):
    """docs/adr/0094 §3. The lawyer types an address; nobody classifies it here.

    Not hidden and not defaulted-but-present: there is no `kind` control and no
    `kind` field on the form, so there is nothing for a browser to post and
    nothing for a forged POST to bind to.
    """
    body = _form(signed_in)
    block = _block(body)

    assert 'name="menetlus-kind"' not in body
    # Scoped to the block, because `Hetkeseis` legitimately offers a stage
    # called «ELi menetluses» and the source chip was called «ELi menetlus»:
    # asserting over the whole page would be asserting about a different control
    # that happens to share five words.
    for label in (
        "EIS",
        "Ministeeriumi dokumendiregister",
        "ELi menetlus",
        "Riigikogu",
        "Muu menetluslink",
        "Menetluse allikas",
    ):
        assert label not in block


def test_the_block_arrives_open(signed_in):
    """Visible on the first render, with no click before the box can be typed into.

    It was a shut `<details>`; docs/adr/0088's argument was about four competing
    blocks and there are two now, so what the fold bought was a click on the one
    question whose answer is already on the reader's screen (docs/adr/0094 §3).
    """
    body = _form(signed_in)
    block = _block(body)

    assert "<details" not in block
    assert "<summary" not in block
    assert "chipdetails" not in block
    # A fieldset and a visible legend, which is what two boxes answering one
    # question are.
    assert "<legend" in block


def test_the_explanatory_paragraph_is_gone_and_not_replaced(signed_in):
    """Three true sentences a lawyer does not need told on every visit."""
    body = _form(signed_in)

    assert "Avalik http:// või https:// aadress" not in body
    assert "lehte ei avata ega jälgita" not in body
    assert "Lisada saab ka hiljem teema lehel" not in body
    assert "cx-note" not in _block(body)


def test_nimetus_loses_the_word_valikuline_and_stays_optional(signed_in):
    """A label change only. The field refuses nothing it did not refuse before."""
    body = _form(signed_in)
    block = _block(body)

    assert "Nimetus" in block
    assert "valikuline" not in block
    assert ProceduralLinkCreateForm().fields["label"].required is False

    # And it really is still optional, on the write path.
    response = _create(signed_in, **{"menetlus-url": EIS_URL})
    assert response.status_code == 302
    assert MatterProceduralLink.objects.get().label == ""


def test_a_refusal_this_block_owns_is_readable_without_opening_anything(signed_in):
    """The box somebody has to correct is on the page, and so is the sentence."""
    response = _create(signed_in, **{"menetlus-url": "javascript:alert(1)"})
    body = response.content.decode()

    assert response.status_code == 400
    assert "http:// või https://" in body
    assert "<details" not in _block(body)


def test_creating_a_matter_with_a_link_saves_both(signed_in, specialist):
    """Scenario D. The ordinary case: one Teema, one reference, one act."""
    response = _create(
        signed_in,
        **{"menetlus-url": EIS_URL, "menetlus-label": "Eelnõu 123 SE"},
    )

    assert response.status_code == 302
    matter = Matter.objects.get(title="Pakendiseaduse muutmise eelnõu")
    link = MatterProceduralLink.objects.get(matter=matter)
    assert link.url == EIS_URL
    assert link.label == "Eelnõu 123 SE"
    assert link.created_by == specialist
    assert ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.PROCEDURAL_LINK_RECORDED
    ).exists()


def test_a_link_filed_here_is_stored_under_the_neutral_kind(signed_in):
    """docs/adr/0094 §3. `OTHER` is the enum's own answer for an unclassified link.

    The constant is asserted rather than the literal, because the storage default
    and the value the form states have to be one thing — a second spelling would
    be a second place for this decision to live.
    """
    _create(signed_in, **{"menetlus-url": EIS_URL})

    link = MatterProceduralLink.objects.get()
    assert link.kind == ProceduralLinkKind.OTHER
    assert ProceduralLinkCreateForm.STORED_KIND == ProceduralLinkKind.OTHER.value


def test_nothing_is_inferred_from_the_address(signed_in):
    """An EIS address is stored as unclassified, because nobody classified it.

    A hostname rule would be wrong about exactly the files the distinction exists
    for: a ministry runs several registers and a register that moved domain would
    silently reclassify every row under it (docs/adr/0089 §2).
    """
    # A Teema per address rather than a delete between them: a created Matter
    # carries audit rows and `ChangeEvent.matter` is PROTECTed, so tearing one
    # down mid-test refuses. Three files is also closer to what happens.
    for index, url in enumerate(
        (
            EIS_URL,
            "https://www.riigikogu.ee/tegevus/eelnoud/eelnou/1234abcd",
            "https://eur-lex.europa.eu/legal-content/ET/TXT/?uri=CELEX:32024R0001",
        )
    ):
        _create(signed_in, title=f"Eelnõu {index}", **{"menetlus-url": url})
        link = MatterProceduralLink.objects.get(url=url)
        assert link.kind == ProceduralLinkKind.OTHER


def test_a_forged_kind_reaches_nothing(signed_in):
    """The kind is not part of this request, so a POST claiming one is ignored.

    Not refused — ignored. There is no field to bind it to, which is the whole
    reason `STORED_KIND` is a constant rather than a hidden input.
    """
    response = _create(
        signed_in,
        **{"menetlus-kind": ProceduralLinkKind.EIS.value, "menetlus-url": EIS_URL},
    )

    assert response.status_code == 302
    assert MatterProceduralLink.objects.get().kind == ProceduralLinkKind.OTHER


def test_the_withdrawn_kinds_are_still_supported_elsewhere(signed_in):
    """A vocabulary is not retired because one form stopped asking for it.

    `Paranda` on a recorded row still offers all five, and every historical row
    keeps what it was filed under.

    It used to be two forms. `ProceduralLinkForm` — the `+ Menetluse link`
    panel's own class — went with the chip on 2026-09-20, when the question
    moved off the launcher and onto the two Teema forms, neither of which asks
    the source. `ProceduralLinkEditForm` is the one surviving surface that
    states it, which makes it the one this claim rests on
    (docs/adr/0097 §5).
    """
    from app.matters.forms import ProceduralLinkEditForm

    offered = {value for value, _label in ProceduralLinkEditForm().fields["kind"].choices if value}
    assert offered == set(ProceduralLinkKind.values)


def test_an_untouched_block_writes_nothing_and_refuses_nothing(signed_in):
    """Scenario C, on the create path. Silent when nobody used it."""
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
    response = _create(signed_in, **{"menetlus-url": "javascript:alert(1)"})

    assert response.status_code == 400
    assert not Matter.objects.filter(title="Pakendiseaduse muutmise eelnõu").exists()
    assert not MatterProceduralLink.objects.exists()


def test_a_refused_save_keeps_the_typed_address_and_name(signed_in):
    """Scenario D's third half. Losing it is the defect this block is prone to.

    A browser cannot put a value back into a box the server did not re-render,
    and the address is the one fact somebody opened this block to record. There
    is more of it to lose now that the block is on screen from the first render.
    """
    response = _create(
        signed_in,
        title="",
        **{"menetlus-url": EIS_URL, "menetlus-label": "Eelnõu 123 SE"},
    )
    body = response.content.decode()

    assert response.status_code == 400
    assert f'value="{EIS_URL}"' in body
    assert 'value="Eelnõu 123 SE"' in body


def test_a_refused_matter_leaves_no_orphan_link(signed_in):
    """The other direction: a Teema refused after its link was read.

    An empty title is refused by the form before the transaction opens, so this
    is belt and braces — but an orphan link row is exactly the residue a
    non-atomic create would leave, and it would be invisible until somebody
    counted rows.
    """
    response = _create(signed_in, title="", **{"menetlus-url": EIS_URL})

    assert response.status_code == 400
    assert MatterProceduralLink.objects.count() == 0


def test_retrying_after_a_refusal_files_one_link(signed_in):
    """Scenario D's last half. A corrected retry is one Teema and one reference."""
    refused = _create(signed_in, title="", **{"menetlus-url": EIS_URL})
    assert refused.status_code == 400

    accepted = _create(signed_in, **{"menetlus-url": EIS_URL})

    assert accepted.status_code == 302
    matter = Matter.objects.get(title="Pakendiseaduse muutmise eelnõu")
    assert MatterProceduralLink.objects.filter(matter=matter).count() == 1


def test_the_link_reads_on_the_matter_immediately_after_creation(signed_in):
    """The point of recording it here: it is on the file when the file opens."""
    _create(signed_in, **{"menetlus-url": EIS_URL})
    matter = Matter.objects.get(title="Pakendiseaduse muutmise eelnõu")

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()

    assert 'id="menetluse-lingid"' in body
    assert f'href="{EIS_URL}"' in body


def test_the_block_posts_under_its_own_prefix(signed_in):
    """docs/adr/0089 §13. The two forms on this page cannot collide by accident.

    `MatterCreateForm` is being worked on concurrently by another package, so a
    field name added there must not be able to reach this form's cleaning — which
    a shared namespace would allow and nothing would catch.
    """
    body = _form(signed_in)

    # The unprefixed spellings belong to nothing on this page.
    assert 'name="url"' not in body
    assert 'name="label"' not in body


# ===========================================================================
# QA-01 — an untouched optional block refuses nothing, whatever else fails
# ===========================================================================
#
# The block was bound unconditionally, so a save refused for a reason somewhere
# else printed «Menetluse link vajab veebiaadressi.» under an address box nobody
# had typed in. It arrived with `EIS` pre-selected at the time, so a real browser
# posted `menetlus-kind=EIS` on every save and the bug was on the ordinary path
# rather than an edge case.
#
# The chip is gone (docs/adr/0094 §3) and the rule it forced is right on its own
# merits: `wants_link` is the single definition of whether this block was
# answered — the address, and nothing else — and the form validates only when it
# says so. These tests hold that rule down without the chip.


UNTOUCHED: dict[str, str] = {"menetlus-url": "", "menetlus-label": ""}


def test_a_refusal_elsewhere_prints_no_address_refusal_under_an_untouched_block(signed_in):
    """QA-01, case A. A blank title is one problem, and it is the only one shown."""
    response = _create(signed_in, title="", **UNTOUCHED)
    body = response.content.decode()

    assert response.status_code == 400
    assert "Menetluse link vajab veebiaadressi." not in body
    assert "Vali, millise menetluse allikaga" not in body
    assert not Matter.objects.exists()
    assert not MatterProceduralLink.objects.exists()


def test_a_refusal_elsewhere_leaves_the_untouched_block_saying_nothing(signed_in):
    """It is open, so it has to be visibly *empty* rather than visibly wrong.

    A block that arrives on screen holding a refusal nobody caused is the same
    defect the fold used to produce, reached from the other side.
    """
    response = _create(signed_in, title="", **UNTOUCHED)
    block = _block(response.content.decode())

    assert response.status_code == 400
    assert "field__error" not in block
    assert 'value=""' in block or "value=" not in block


def test_an_untouched_block_saves_the_teema_and_writes_no_link(signed_in):
    """QA-01, case C. The ordinary submit from a real browser: silent."""
    response = _create(signed_in, **UNTOUCHED)

    assert response.status_code == 302
    matter = Matter.objects.get(title="Pakendiseaduse muutmise eelnõu")
    assert not MatterProceduralLink.objects.filter(matter=matter).exists()


def test_a_label_beside_an_untouched_address_still_refuses_nothing(signed_in):
    """The address alone decides it — `wants_link`, unchanged by this round.

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
