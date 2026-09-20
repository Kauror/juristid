"""The consolidated Teema model: andmed, lisamine, toimingud (docs/adr/0096).

This file owns the claims the round makes that no existing file already owns,
and deliberately stops where another file starts:

* `tests/test_uus_teema_ux_corrections.py` owns how `Valdkonnad` and
  `Hetkeseis` are drawn on `Uus teema` — the `chipfold`, its `open` attribute
  on a first and a refused render, and what each summary says. What is asserted
  here is only that `Muuda teemat` draws the *same* controls, which is the part
  that is new;
* `tests/test_uus_teema_manual_first.py` owns the Valdkond vocabulary and its
  withdrawals;
* `tests/test_procedural_links_on_uus_teema.py` owns the link's create path;
* `e2e/test_uus_teema_valikud.py` owns what only a browser can say — that the
  options are visible without a click and that collapsing is the reader's act.

Four groups, in the order docs/adr/0096 decides them:

1. the two forms ask the same questions with the same controls (§1–§3);
2. four questions left the ordinary interface and took their write paths with
   them, and no stored value moved (§4–§5);
3. `LISA TEEMALE` is four families and `TEEMA TOIMINGUD` is not one of them
   (§6–§9);
4. `Kustuta teema` removes the content, leaves a tombstone, and refuses by name
   (§10).

**The crafted-POST tests are the load-bearing half of group 2.** A control
removed from a template is a control removed from one render; a question is
only withdrawn when the form no longer binds it and the route no longer exists.
Each of those is asserted against the stored value, never against the markup.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.matters.deletion import (
    BLOCKED_BY_APPEND_ONLY,
    build_deletion_plan,
    delete_matter,
)
from app.matters.enums import ProceduralLinkKind
from app.matters.forms import MatterEditForm
from app.matters.models import (
    EntryRevision,
    Matter,
    MatterProceduralDevelopment,
    MatterProceduralLink,
)
from app.organisations.models import Organisation
from app.taxonomy.models import PolicyArea, Tag
from app.workflow.enums import ActionStatus, Track
from app.workflow.models import NextAction
from tests import factories

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")


def edit_url(matter: Matter) -> str:
    return reverse("matters:matter_edit", kwargs={"pk": matter.pk})


def teema_url(matter: Matter) -> str:
    return reverse("matters:matter_detail", kwargs={"pk": matter.pk})


def page_of(client, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def fold(page: str, field: str) -> str:
    """One `chipfold`, from its opening tag to its close.

    `rindex` back from the summary that names the field, because both folds on
    a page open with an identical tag and order-based slicing would silently
    read the wrong one.
    """
    anchor = page.index(f'data-chipsummary-for="{field}"')
    start = page.rindex('<details class="chipfold"', 0, anchor)
    return page[start : page.index("</details>", start)]


# ---------------------------------------------------------------------------
# 1 — one question, one control, on both pages (§1–§3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["policy_areas", "stage"])
def test_muuda_teemat_draws_the_same_open_fold_as_uus_teema(signed_in, specialist, stage, field):
    """The classification controls are one partial included twice.

    Asserted as sameness rather than as shape: the shape is
    `test_uus_teema_ux_corrections.py`'s claim, and restating it here would be
    two places for it to drift — which is the defect this round exists to
    close.
    """
    matter = factories.MatterFactory(owner=specialist)
    create = fold(page_of(signed_in, CREATE), field)
    edit = fold(page_of(signed_in, edit_url(matter)), field)

    for markup in (create, edit):
        assert markup.startswith('<details class="chipfold" open>')
        assert '<summary class="chipfold__trigger">' in markup
        assert "chipfold__body" in markup


def test_no_teema_form_renders_a_floating_menu(signed_in, specialist):
    """The overlay is gone from both pages, not merely from the one that had it.

    `chipmenu` was `Uus teema`'s alone; this is the assertion that nothing
    reintroduced it on the page that followed it (docs/adr/0096 §3).
    """
    matter = factories.MatterFactory(owner=specialist)

    for url in (CREATE, edit_url(matter)):
        page = page_of(signed_in, url)
        assert "chipmenu" not in page
        assert "data-chipmenu" not in page


def test_muu_valdkond_is_a_chip_on_the_edit_page_too(signed_in, specialist):
    """`Muu` reveals its box on both pages, instead of a chip on one and a box on the other."""
    matter = factories.MatterFactory(owner=specialist, policy_area_other="Kosmoseõigus")

    page = page_of(signed_in, edit_url(matter))

    assert 'id="valdkond-muu"' in fold(page, "policy_areas")
    # Ticked from the stored text, so the box renders open with the value in it
    # and nothing depends on scripting having run (docs/adr/0096 §2).
    assert 'id="valdkond-muu-tekst"' in page
    box = page[page.index('id="valdkond-muu-tekst"') :][:200]
    assert "hidden" not in box


def test_the_edit_page_offers_menetluse_link(signed_in, specialist):
    """`Menetluse link` is a Matter fact and is therefore correctable (§6)."""
    matter = factories.MatterFactory(owner=specialist)

    page = page_of(signed_in, edit_url(matter))

    assert 'id="menetluse-link"' in page
    assert 'name="menetlus-url"' in page


def test_an_existing_menetluse_link_is_editable_from_muuda_teemat(signed_in, specialist):
    """The address on the record comes back in the box, and a save corrects it."""
    matter = factories.MatterFactory(owner=specialist)
    link = MatterProceduralLink.objects.create(
        matter=matter,
        kind=ProceduralLinkKind.EIS,
        url="https://eelnoud.ee/vana",
        created_by=specialist,
    )

    page = page_of(signed_in, edit_url(matter))
    assert "https://eelnoud.ee/vana" in page
    # The token the page rendered, submitted as a browser submits it. Posting
    # without it is a *stale* copy by definition and is answered 409 — which is
    # the contract, and which is how the missing hidden field was found.
    revision = re.search(r'name="menetlus-revision" value="([^"]+)"', page)
    assert revision is not None

    response = signed_in.post(
        edit_url(matter),
        {
            "title": matter.title,
            "menetlus-url": "https://eelnoud.ee/uus",
            "menetlus-revision": revision.group(1),
        },
    )

    assert response.status_code == 302
    link.refresh_from_db()
    assert link.url == "https://eelnoud.ee/uus"
    # And the kind it was filed under is not rewritten by an address
    # correction: only the Teema page's own `Paranda` offers that vocabulary
    # (docs/adr/0096 §6).
    assert link.kind == ProceduralLinkKind.EIS


def test_menetluse_link_is_not_in_the_launcher(signed_in, specialist, stage):
    """It is a fact about the Matter, not something that happened to it (§6)."""
    matter = factories.MatterFactory(owner=specialist)

    page = page_of(signed_in, teema_url(matter))

    assert "+ Menetluse link" not in page


# ---------------------------------------------------------------------------
# 2 — four questions left, and nothing stored moved (§4–§5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "absent",
    ["Sildid", "Nähtavus", "Menetlusliik", "Kellele"],
    ids=["sildid", "nahtavus", "menetlusliik", "adressaat"],
)
def test_the_ordinary_edit_form_no_longer_asks(signed_in, specialist, absent):
    """None of the four is a control on `Muuda teemat` any more."""
    matter = factories.MatterFactory(owner=specialist)

    page = page_of(signed_in, edit_url(matter))

    assert absent not in page


@pytest.mark.parametrize(
    "field", ["tags", "visibility", "track", "addressee_organisation", "addressee_name"]
)
def test_the_edit_form_does_not_bind_the_withdrawn_fields(field):
    """Deleted, not hidden — which is the claim the crafted POSTs below rest on.

    A form that still declared these would still clean them, and the view would
    still have a value to hand to a service. `MatterEditForm` has no such field,
    so the parameter is not part of the request as far as this form is
    concerned (docs/adr/0095's rule, applied in docs/adr/0096 §4).
    """
    assert field not in MatterEditForm().fields


def test_a_crafted_edit_post_cannot_change_visibility(signed_in, specialist):
    """The one that matters. A restricted Matter stays restricted."""
    matter = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)

    response = signed_in.post(
        edit_url(matter),
        {"title": "Parandatud pealkiri", "visibility": Visibility.NORMAL},
    )

    assert response.status_code == 302
    matter.refresh_from_db()
    assert matter.title == "Parandatud pealkiri"
    assert matter.visibility == Visibility.RESTRICTED


def test_a_crafted_edit_post_cannot_change_tags(signed_in, specialist):
    """Historical tag assignments survive a save of the simplified form."""
    tag = Tag.objects.create(key="maksud", name_et="Maksud", is_active=True)
    other = Tag.objects.create(key="energeetika", name_et="Energeetika", is_active=True)
    matter = factories.MatterFactory(owner=specialist)
    matter.tags.set([tag])

    response = signed_in.post(edit_url(matter), {"title": matter.title, "tags": [str(other.pk)]})

    assert response.status_code == 302
    assert list(matter.tags.all()) == [tag]


def test_a_crafted_edit_post_cannot_change_track_or_addressee(signed_in, specialist):
    """Both keep their stored value through an unrelated correction."""
    body = Organisation.objects.create(name="Rahandusministeerium")
    matter = factories.MatterFactory(
        owner=specialist,
        track=Track.NATIONAL_TRANSPOSITION,
        addressee_organisation=body,
    )
    other = Organisation.objects.create(name="Kliimaministeerium")

    response = signed_in.post(
        edit_url(matter),
        {
            "title": matter.title,
            "track": Track.KODA_INITIATIVE,
            "addressee_organisation": str(other.pk),
        },
    )

    assert response.status_code == 302
    matter.refresh_from_db()
    assert matter.track == Track.NATIONAL_TRANSPOSITION
    assert matter.addressee_organisation_id == body.pk


def test_an_ordinary_save_clears_no_unrelated_field(signed_in, specialist):
    """The whole of the removal, in one assertion.

    A field removed from a form is a value the view stops sending. What it must
    never become is a value the view sends as *empty* — `set_organisations`
    takes `_UNSET` for «leave this alone», and passing `None` instead would
    clear a fact nobody was offered the chance to state (docs/adr/0096 §5).
    """
    body = Organisation.objects.create(name="Justiitsministeerium")
    tag = Tag.objects.create(key="maksud", name_et="Maksud", is_active=True)
    matter = factories.MatterFactory(
        owner=specialist,
        track=Track.EU_INITIATIVE,
        addressee_organisation=body,
        visibility=Visibility.RESTRICTED,
    )
    matter.tags.set([tag])

    response = signed_in.post(edit_url(matter), {"title": "Uus pealkiri"})

    assert response.status_code == 302
    matter.refresh_from_db()
    assert matter.title == "Uus pealkiri"
    assert matter.track == Track.EU_INITIATIVE
    assert matter.addressee_organisation_id == body.pk
    assert matter.visibility == Visibility.RESTRICTED
    assert list(matter.tags.all()) == [tag]


@pytest.mark.parametrize("field", ["visibility", "track", "addressee_organisation"])
def test_the_inline_field_route_no_longer_exists(signed_in, specialist, field):
    """A branch left behind a withdrawn control is still a reachable write path.

    `update_field` 404s on a field `FIELD_SERVICES` does not name, so these
    three are not addresses at all rather than addresses nothing links to
    (docs/adr/0096 §4).
    """
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        reverse("matters:update_field", kwargs={"pk": matter.pk, "field": field}),
        {field: ""},
    )

    assert response.status_code == 404


def test_the_teema_header_offers_no_visibility_control(signed_in, specialist, stage):
    """The `⋯` menu that held it is gone with it (§4.2)."""
    matter = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)

    page = page_of(signed_in, teema_url(matter))

    assert 'name="visibility"' not in page
    assert "headmenu" not in page
    # The banner that *states* the restriction is not a control and stays: a
    # reader has to be told the file is restricted.
    assert "Piiratud" in page


def test_a_restricted_matter_keeps_its_visibility_and_its_filter(signed_in, reader, specialist):
    """Withdrawing the control withdrew no part of the mechanism (§4).

    **Read against `reader`, not against another specialist.** `RESTRICTED` on
    this product does not mean «the owner and the participants» — ADR 0042
    widened it to the department, which is why
    `app/core/visibility_help.py` exists and why the banner says what it says.
    A test asserting the narrower promise would be asserting a rule the product
    deliberately does not have (`tests/test_authorization_matrix.py` owns the
    matrix itself).
    """
    matter = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)

    assert matter in Matter.objects.visible_to(specialist)
    assert matter not in Matter.objects.visible_to(reader)
    matter.refresh_from_db()
    assert matter.visibility == Visibility.RESTRICTED


# ---------------------------------------------------------------------------
# 3 — the launcher is four families (§8), and operations are not among them
# ---------------------------------------------------------------------------

#: The four, and the whole of the four.
FAMILIES = ["+ Märge", "+ Kaasamine", "+ Arvamus / tagasiside", "+ Ülevaade / uudis"]

#: Every chip that used to be a peer of those and is not one now.
RETIRED_CHIPS = [
    "+ Järgmine tegevus",
    "+ Oluline tähtaeg",
    "+ Jõustumine",
    "+ Töövõit",
    "+ Meile saadetud tagasiside",
    "+ Teiste arvamus",
    "+ Koja arvamus",
    "+ Menetluse areng",
    "+ Menetluse link",
    "+ Lõpeta teema",
]


def launcher(page: str) -> str:
    start = page.index('id="lisa-teemale"')
    return page[start : page.index("</section>", start)]


def test_the_launcher_offers_exactly_four_families(signed_in, specialist, stage):
    matter = factories.MatterFactory(owner=specialist)

    zone = launcher(page_of(signed_in, teema_url(matter)))
    chips = re.findall(
        r'<label class="disclosure-chip" for="lisa-[a-z-]+-valik">([^<]+)</label>', zone
    )

    assert chips == FAMILIES


@pytest.mark.parametrize("chip", RETIRED_CHIPS, ids=[c[2:] for c in RETIRED_CHIPS])
def test_no_retired_chip_is_a_top_level_choice(signed_in, specialist, stage, chip):
    """None of the ten is a peer of the four any more.

    Asserted over the launcher rather than the page: `Lõpeta teema` is still on
    the Teema page and must be — under `TEEMA TOIMINGUD`, which is a different
    section and is what the next test is about.
    """
    matter = factories.MatterFactory(owner=specialist)

    assert chip not in launcher(page_of(signed_in, teema_url(matter)))


def test_marge_offers_its_four_kinds_and_defaults_to_tavaline(signed_in, specialist, stage):
    matter = factories.MatterFactory(owner=specialist)

    zone = launcher(page_of(signed_in, teema_url(matter)))

    for label in ("Tavaline", "Oluline tähtaeg", "Jõustumine", "Töövõit"):
        assert f">{label}</label>" in zone
    opening = zone[zone.index('id="marge-tavaline-valik"') - 200 :]
    assert "checked" in opening[: opening.index(">") + 400]


def test_arvamus_offers_its_three_children(signed_in, specialist, stage):
    matter = factories.MatterFactory(owner=specialist)

    zone = launcher(page_of(signed_in, teema_url(matter)))

    for label in ("Meile saadetud tagasiside", "Teiste arvamus", "Koja arvamus"):
        assert f">{label}</label>" in zone


def test_the_sub_choices_are_their_own_radio_groups(signed_in, specialist, stage):
    """Two groups, so choosing a `Märge` kind cannot deselect an `Arvamus` one."""
    matter = factories.MatterFactory(owner=specialist)

    zone = launcher(page_of(signed_in, teema_url(matter)))

    assert zone.count('name="marke-liik"') == 4
    assert zone.count('name="arvamuse-liik"') == 3


def test_teema_toimingud_is_a_separate_section(signed_in, specialist, stage):
    """Close and delete are operations on the Matter, not content added to it (§9)."""
    matter = factories.MatterFactory(owner=specialist)

    page = page_of(signed_in, teema_url(matter))

    assert 'id="teema-toimingud"' in page
    assert "Teema toimingud" in page
    ops = page[page.index('id="teema-toimingud"') :]
    ops = ops[: ops.index("</section>")]
    assert "Lõpeta teema" in ops
    assert "Kustuta teema" in ops
    # And neither is inside the launcher.
    zone = launcher(page)
    assert "Kustuta teema" not in zone


# ---------------------------------------------------------------------------
# 4 — `+ Märge` writes the structured record (§7)
# ---------------------------------------------------------------------------


def add_note_url(matter: Matter) -> str:
    return reverse("matters:add_note", kwargs={"pk": matter.pk})


def test_the_marge_panel_offers_no_precision_control(signed_in, specialist, stage):
    """One date box, and `Täpsus` is not asked (§7.1)."""
    matter = factories.MatterFactory(owner=specialist)

    zone = launcher(page_of(signed_in, teema_url(matter)))
    panel = zone[zone.index('id="marge-tavaline"') :]
    panel = panel[: panel.index('id="marge-tahtaeg"')]

    assert "Täpsus" not in panel
    assert "Juristi märkus" not in panel
    assert 'name="occurred_on"' in panel


def test_the_marge_date_defaults_to_today(signed_in, specialist, stage):
    """Visible in the box where it can be read, changed and emptied."""
    matter = factories.MatterFactory(owner=specialist)

    zone = launcher(page_of(signed_in, teema_url(matter)))

    # `format_estonian_date`'s own shape — `20.9.2026`, no leading zeroes.
    # `strftime` is what that function exists to avoid: the directive that
    # drops a leading zero is `%-d` on Linux and `%#d` on Windows.
    today = timezone.localdate()
    assert f"{today.day}.{today.month}.{today.year}" in zone


def test_a_marge_writes_a_procedural_development(signed_in, specialist, stage):
    """One visible control, and the record underneath is the structured one (§7)."""
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        add_note_url(matter),
        {"title": "Ministeerium saatis uue eelnõu versiooni", "occurred_on": "19.09.2026"},
    )

    assert response.status_code == 200
    record = MatterProceduralDevelopment.objects.get(matter=matter)
    assert record.title == "Ministeerium saatis uue eelnõu versiooni"
    assert record.occurred_on.isoformat() == "2026-09-19"
    assert record.note == ""


def test_a_marge_can_move_the_stage_in_the_same_save(signed_in, specialist, stage):
    """«Eelnõu saadeti Riigikokku» and the stage are one act (§7.1)."""
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        add_note_url(matter),
        {
            "title": "Eelnõu saadeti Riigikokku",
            "occurred_on": "19.09.2026",
            "stage": str(stage.pk),
        },
    )

    assert response.status_code == 200
    matter.refresh_from_db()
    assert matter.stage_id == stage.pk


def test_a_marge_with_no_stage_infers_none(signed_in, specialist, stage):
    """No text is read and no keyword matched. Ever."""
    matter = factories.MatterFactory(owner=specialist, stage=None)

    signed_in.post(
        add_note_url(matter),
        {"title": "Riigikogu võttis seaduse vastu", "occurred_on": "19.09.2026"},
    )

    matter.refresh_from_db()
    assert matter.stage_id is None


def test_a_marge_can_set_the_next_action_and_otherwise_creates_none(signed_in, specialist, stage):
    matter = factories.MatterFactory(owner=specialist)

    signed_in.post(
        add_note_url(matter),
        {"title": "Uus versioon saabus", "occurred_on": "19.09.2026"},
    )
    assert not NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).exists()

    signed_in.post(
        add_note_url(matter),
        {
            "title": "Teine versioon saabus",
            "occurred_on": "19.09.2026",
            "next_text": "Vaatan uue versiooni üle",
            "next_date": "25.09.2026",
        },
    )
    action = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert action.text == "Vaatan uue versiooni üle"


def test_a_crafted_precision_reaches_nothing(signed_in, specialist, stage):
    """The group is deleted from the form, so the POST key binds to no field (§7.2)."""
    matter = factories.MatterFactory(owner=specialist)

    signed_in.post(
        add_note_url(matter),
        {
            "title": "Midagi juhtus",
            "occurred_on": "19.09.2026",
            "areng_precision": "QUARTER",
            "areng_quarter": "2026-Q3",
        },
    )

    record = MatterProceduralDevelopment.objects.get(matter=matter)
    assert record.occurred_on_precision == "EXACT"
    assert record.occurred_on.isoformat() == "2026-09-19"


def test_the_retired_routes_are_gone(signed_in, specialist):
    """A withdrawn control whose endpoint still answers is withdrawn in the templates only."""
    matter = factories.MatterFactory(owner=specialist)

    from django.urls import NoReverseMatch

    for name in ("add_development", "add_procedural_link"):
        with pytest.raises(NoReverseMatch):
            reverse(f"matters:{name}", kwargs={"pk": matter.pk})


def test_historical_developments_still_read_and_correct(signed_in, specialist, stage):
    """The record type survives; only the word left the screen (§7)."""
    matter = factories.MatterFactory(owner=specialist)
    record = MatterProceduralDevelopment.objects.create(
        matter=matter,
        title="Vana samm",
        occurred_on=None,
        occurred_on_precision="EXACT",
        note="Juristi tähelepanek",
        created_by=specialist,
    )

    page = page_of(signed_in, teema_url(matter))

    assert "Vana samm" in page
    assert str(record.pk) in page


# ---------------------------------------------------------------------------
# 5 — `Kustuta teema` (§10)
# ---------------------------------------------------------------------------


def delete_url(matter: Matter) -> str:
    return reverse("matters:matter_delete", kwargs={"pk": matter.pk})


def test_get_shows_the_confirmation_and_deletes_nothing(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist, title="Kustutatav teema")

    page = page_of(signed_in, delete_url(matter))

    assert "Kustutatav teema" in page
    assert "Loobu" in page
    matter.refresh_from_db()
    assert matter.deleted_at is None


def test_the_confirmation_requires_csrf(client, specialist):
    """No CSRF token, no deletion — the form's token is the confirmation."""
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(specialist)
    client.handler.enforce_csrf_checks = True

    response = client.post(delete_url(matter))

    assert response.status_code == 403
    matter.refresh_from_db()
    assert matter.deleted_at is None


def test_a_successful_deletion_removes_the_matter_from_every_surface(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    pk = matter.pk

    response = signed_in.post(delete_url(matter))

    assert response.status_code == 302
    assert not Matter.objects.filter(pk=pk).exists()
    assert Matter.all_objects.filter(pk=pk).exists()
    assert signed_in.get(teema_url(matter)).status_code == 404
    assert pk not in {row.pk for row in Matter.objects.visible_to(specialist)}


def test_deletion_leaves_audit_proof(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    signed_in.post(delete_url(matter))

    event = ChangeEvent.objects.get(matter_id=matter.pk, event_type=ChangeEventType.MATTER_DELETED)
    assert event.actor_id == specialist.pk
    # Counts, never content.
    assert "rows" in event.payload
    assert matter.title not in str(event.payload)


def test_deletion_keeps_shared_vocabulary(signed_in, specialist):
    """An Organisation the Matter pointed at is not owned by it."""
    body = Organisation.objects.create(name="Kliimaministeerium")
    area = PolicyArea.objects.filter(is_active=True).first()
    matter = factories.MatterFactory(owner=specialist)
    matter.source_organisations.set([body])
    if area is not None:
        matter.policy_areas.set([area])

    signed_in.post(delete_url(matter))

    assert Organisation.objects.filter(pk=body.pk).exists()
    if area is not None:
        assert PolicyArea.objects.filter(pk=area.pk).exists()


def test_deletion_removes_owned_business_data(signed_in, specialist, stage):
    matter = factories.MatterFactory(owner=specialist)
    signed_in.post(
        add_note_url(matter),
        {"title": "Midagi juhtus", "occurred_on": "19.09.2026"},
    )
    assert MatterProceduralDevelopment.objects.filter(matter=matter).exists()

    signed_in.post(delete_url(matter))

    assert not MatterProceduralDevelopment.objects.filter(matter_id=matter.pk).exists()


def test_a_second_deletion_is_a_no_op(signed_in, specialist):
    """Two tabs, two clicks, one deletion."""
    matter = factories.MatterFactory(owner=specialist)
    signed_in.post(delete_url(matter))
    first = Matter.all_objects.get(pk=matter.pk).deleted_at

    delete_matter(matter=Matter.all_objects.get(pk=matter.pk), actor=specialist)

    assert Matter.all_objects.get(pk=matter.pk).deleted_at == first


def test_a_legal_hold_refuses_the_whole_deletion(signed_in, specialist):
    """A legal hold outlives anybody's wish to tidy up."""
    matter = factories.MatterFactory(owner=specialist)
    document = factories.DocumentFactory(matter=matter)
    document.legal_hold = True
    document.legal_hold_reason = "Kohtuvaidlus"
    document.save(update_fields=["legal_hold", "legal_hold_reason"])

    plan = build_deletion_plan(matter)

    assert plan.is_blocked
    assert "säilituskohustus" in plan.refusal
    response = signed_in.post(delete_url(matter))
    assert response.status_code == 409
    matter.refresh_from_db()
    assert matter.deleted_at is None


def test_an_append_only_child_refuses_by_name(signed_in, specialist, stage):
    """The `EntryRevision` case: a corrected entry's previous wording is evidence."""
    matter = factories.MatterFactory(owner=specialist)
    entry = factories.EntryFactory(matter=matter, body="Teine sõnastus")
    EntryRevision.objects.create(
        entry=entry, revision_number=1, body="Esimene sõnastus", edited_by=specialist
    )

    plan = build_deletion_plan(matter)

    assert plan.is_blocked
    assert any(blocker.category == BLOCKED_BY_APPEND_ONLY for blocker in plan.blockers)
    assert "muutumatuid" in plan.refusal
    matter.refresh_from_db()
    assert matter.deleted_at is None


def test_a_reader_cannot_reach_the_deletion(client, reader, specialist):
    """`business_write_required`, like every other write on this product."""
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(reader)

    assert client.get(delete_url(matter)).status_code in (403, 404)
    assert client.post(delete_url(matter)).status_code in (403, 404)
    matter.refresh_from_db()
    assert matter.deleted_at is None
