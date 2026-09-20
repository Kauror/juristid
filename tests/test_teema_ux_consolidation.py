"""The consolidated Teema model: andmed, lisamine, toimingud (docs/adr/0097).

This file owns the claims the round makes that no existing file already owns,
and deliberately stops where another file starts:

* `tests/test_teema_live_audit_round_2.py` owns docs/adr/0096 in full — that
  the two forms ask the same questions with the same controls and from the same
  partials, that `Nähtavus` is absent from the ordinary interface, and every
  claim about `Kustuta teema`: the confirmation page, the tombstone, the audit
  proof and each refusal. This round changes none of it. What is asserted here
  is only that the deletion control lives under `TEEMA TOIMINGUD` rather than in
  the content launcher, which is the part that is new;
* `tests/test_uus_teema_ux_corrections.py` owns how `Valdkonnad` and
  `Hetkeseis` are drawn;
* `tests/test_uus_teema_manual_first.py` owns the Valdkond vocabulary and its
  withdrawals;
* `tests/test_procedural_links_on_uus_teema.py` owns the link's create path;
* `tests/test_lawyer_workflow_package.py` owns what a
  `MatterProceduralDevelopment` means once it is stored, and the correction
  surface that still offers the whole precision control on one.

Three groups, in the order docs/adr/0097 decides them:

1. `Menetluse link` is a Matter fact and is answered on both Teema forms (§5);
2. four questions left the ordinary interface and took their write paths with
   them, and no stored value moved (§2–§5);
3. `LISA TEEMALE` is four families, `+ Märge` is one of them and
   `TEEMA TOIMINGUD` is not (§6–§9).

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

from app.core.enums import Visibility
from app.matters.enums import ProceduralLinkKind
from app.matters.forms import MatterEditForm
from app.matters.models import (
    Matter,
    MatterProceduralDevelopment,
    MatterProceduralLink,
)
from app.organisations.models import Organisation
from app.taxonomy.models import Tag
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


# ---------------------------------------------------------------------------
# 1 — `Menetluse link` is a Matter fact (§5)
# ---------------------------------------------------------------------------
#
# How `Valdkonnad` and `Hetkeseis` are drawn, and that both pages draw one
# partial, is `tests/test_teema_live_audit_round_2.py` §1. This round changes
# neither, so nothing about either is restated here — two places for one claim
# is the drift this whole round exists to close.


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
    # (docs/adr/0097 §5).
    assert link.kind == ProceduralLinkKind.EIS


def test_menetluse_link_is_not_in_the_launcher(signed_in, specialist, stage):
    """It is a fact about the Matter, not something that happened to it (§6)."""
    matter = factories.MatterFactory(owner=specialist)

    page = page_of(signed_in, teema_url(matter))

    assert "+ Menetluse link" not in page


# ---------------------------------------------------------------------------
# 2 — four questions left, and nothing stored moved (§2–§4)
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
    concerned (docs/adr/0095's rule, applied in docs/adr/0097 §2–§4).
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
    clear a fact nobody was offered the chance to state (docs/adr/0097 §4).
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
    (docs/adr/0096 §3, docs/adr/0097 §3, §4).
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
# 3b — `+ Märge` writes the structured record (§6)
# ---------------------------------------------------------------------------


def add_note_url(matter: Matter) -> str:
    return reverse("matters:add_note", kwargs={"pk": matter.pk})


def test_the_marge_panel_offers_no_precision_control(signed_in, specialist, stage):
    """One date box, and `Täpsus` is not asked (§6.1)."""
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
    """One visible control, and the record underneath is the structured one (§6)."""
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
    """«Eelnõu saadeti Riigikokku» and the stage are one act (§6.1)."""
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
    """The record type survives; only the word left the screen (§6)."""
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
# 4 — `Töövõit` is an exact date (§7)
# ---------------------------------------------------------------------------


def victory_url(matter: Matter) -> str:
    return reverse("matters:add_work_victory", kwargs={"pk": matter.pk})


def test_the_toovoit_panel_asks_for_a_day_and_offers_no_precision(signed_in, specialist, stage):
    """A win is something Koda achieved, and the organisation should know when.

    One date box holding today, and the four-way `Täpsus` group is not on this
    panel — `Kuu`, `Kvartal` and `Aasta` are what it offered and what it no
    longer does (docs/adr/0097 §7).
    """
    matter = factories.MatterFactory(owner=specialist)

    zone = launcher(page_of(signed_in, teema_url(matter)))
    panel = zone[zone.index('id="marge-toovoit"') :]
    # To the end of the `+ Märge` family: `Töövõit` is its last sub-choice,
    # and the next chip in the document is the family after it.
    panel = panel[: panel.index('id="lisa-kaasamine"')]

    assert 'name="victory_date"' in panel
    assert "Täpsus" not in panel
    for label in ("Kvartal", "Poolaasta"):
        assert label not in panel
    today = timezone.localdate()
    assert f"{today.day}.{today.month}.{today.year}" in panel


def test_a_toovoit_is_stored_as_the_single_day_it_was_won_on(signed_in, specialist, stage):
    """`period_date` and `period_end` are the same day, and the precision is EXACT."""
    from app.intelligence.models import MatterWorkVictory

    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        victory_url(matter),
        {"victory_change": "Üleminekuaega pikendati", "victory_date": "19.09.2026"},
    )

    assert response.status_code == 200
    victory = MatterWorkVictory.objects.get(matter=matter)
    assert victory.period_date.isoformat() == "2026-09-19"
    assert victory.period_end.isoformat() == "2026-09-19"
    assert victory.date_precision == "EXACT"


def test_a_crafted_precision_on_a_toovoit_reaches_nothing(signed_in, specialist, stage):
    """No `victory_precision` field, so a forged one binds to nothing (§7)."""
    from app.intelligence.models import MatterWorkVictory

    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        victory_url(matter),
        {
            "victory_change": "Üleminekuaega pikendati",
            "victory_date": "19.09.2026",
            "victory_precision": "YEAR",
            "victory_year": "2024",
        },
    )

    assert response.status_code == 200
    victory = MatterWorkVictory.objects.get(matter=matter)
    assert victory.date_precision == "EXACT"
    assert victory.period_date.isoformat() == "2026-09-19"


def test_an_undated_toovoit_is_refused_rather_than_stored_with_no_period(
    signed_in, specialist, stage
):
    """The gap Stage-2G closed stays closed: an empty box is not `NULL`."""
    from app.intelligence.models import MatterWorkVictory

    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        victory_url(matter), {"victory_change": "Üleminekuaega pikendati", "victory_date": ""}
    )

    assert response.status_code == 400
    assert "Märgi, millal see töövõit saavutati." in response.content.decode()
    assert not MatterWorkVictory.objects.filter(matter=matter).exists()


def test_a_historical_approximate_toovoit_keeps_its_period(specialist):
    """Nothing is backfilled, clamped or rewritten (docs/adr/0097 §7)."""
    import datetime as dt

    from app.intelligence.models import MatterWorkVictory

    matter = factories.MatterFactory(owner=specialist)
    victory = MatterWorkVictory.objects.create(
        matter=matter,
        title="Vana võit",
        period_date=dt.date(2024, 1, 1),
        period_end=dt.date(2024, 12, 31),
        date_precision="YEAR",
        created_by=specialist,
    )

    victory.refresh_from_db()
    assert victory.date_precision == "YEAR"
    assert victory.period_date.isoformat() == "2024-01-01"
    assert victory.period_end.isoformat() == "2024-12-31"
