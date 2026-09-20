"""`LISA TEEMALE` is a stable choice bar, and the server's half of that contract.

**Four chips in one order**, where there were thirteen. Choosing one opens its
form; it does not move the chip, reorder the row, or change which line anything
sits on. The browser half — that no control's bounding box moves by a pixel when
a panel opens — is `e2e/test_add_launcher_stability.py`; what *this* file pins is
the markup that makes the CSS able to keep that promise:

* the order the launcher renders in, and the sub-choices inside the two families
  that have them;
* that each chip is a control and each form is a separate element after it, so
  the element that grows is never the element you press;
* that the open state lives on a radio, which is what gives the browser
  one-open-at-a-time and a keyboard contract without a line of script — and that
  each family's sub-choices are a radio group of *their own*, so opening one
  cannot close the family it lives in.

The panels were `<details>` until 2026-09-14. An open one took
`flex: 1 1 100%; order: 1`, which sent the chip somebody had just clicked to the
head of the next line and took the rest of the row with it (design feedback
2026-09-14).

The row was thirteen chips until 2026-09-20. Every one of them was a truthful
distinction and the row was still wrong: the lawyer in front of it does not have
a record type in mind, they have something that happened. So the launcher asks
what *kind of thing* is being recorded, and the distinctions that remain are
asked second, inside the family that was chosen — grouped on the screen, and
still three models and three endpoints underneath (docs/adr/0097 §8).
"""

from __future__ import annotations

import re

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.urls import reverse
from django.utils import timezone

from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

#: The four families, in the order the product's own reasoning puts them:
#: something happened, somebody was asked, somebody had a view, something was
#: published.
CANONICAL = [
    "+ Märge",
    "+ Kaasamine",
    "+ Arvamus / tagasiside",
    "+ Ülevaade / uudis",
]

#: What each family asks second, where it asks anything. `+ Kaasamine` and
#: `+ Ülevaade / uudis` ask nothing further and their own panel is the form.
SUBCHOICES = {
    "lisa-marge": ["Tavaline", "Oluline tähtaeg", "Jõustumine", "Töövõit"],
    "lisa-arvamus": ["Meile saadetud tagasiside", "Teiste arvamus", "Koja arvamus"],
}

#: Every panel in the zone, family and sub-choice alike.
#:
#: `lisa-jargmine` is **not** among them: `+ Järgmine tegevus` left the row, and
#: the one ordinary way to set a next step while none is open is the optional
#: box inside `+ Märge`. Nor is `teema-lopeta`, which is real but renders in
#: `TEEMA TOIMINGUD` — a section of its own, outside `#lisa-teemale`, asserted
#: below. Nor `lisa-menetluse-link`, which is not a thing that happened to a
#: Matter at all and is asked on the two Teema forms (docs/adr/0097 §5, §8.2,
#: §9).
PANEL_IDS = [
    "lisa-marge",
    "marge-tavaline",
    "marge-tahtaeg",
    "marge-joustumine",
    "marge-toovoit",
    "lisa-kaasamine",
    "lisa-arvamus",
    "arvamus-tagasiside",
    "arvamus-teiste",
    "arvamus-koja",
    "lisa-koduleht",
]

#: The families, which are the only chips in `name="lisa-valik"`.
FAMILY_IDS = ["lisa-marge", "lisa-kaasamine", "lisa-arvamus", "lisa-koduleht"]


def _zone(client, matter) -> str:
    """`LISA TEEMALE` alone — up to `TEEMA TOIMINGUD`, not up to the chronology.

    The two sections are siblings and `TEEMA TOIMINGUD` renders between the
    launcher and `#ajajoon`, so slicing to the chronology would pull
    `Lõpeta teema` and `Kustuta teema` back into every claim this file makes
    about the launcher — which is the exact distinction the section exists to
    draw (docs/adr/0097 §9).
    """
    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()
    start = body.index('id="lisa-teemale"')
    end = body.index('id="teema-toimingud"', start)
    return body[start:end]


def _chip_ids(zone: str) -> list[str]:
    """The panel each chip opens, in document order — the key `_chips` lacks."""
    return re.findall(r'<label class="disclosure-chip[^"]*" for="([a-z-]+)-valik"', zone)


def _chips(zone: str) -> list[str]:
    """Every launcher control, in the order the document renders them."""
    return [
        text.strip()
        for text in re.findall(
            r'<label class="disclosure-chip[^"]*"[^>]*>(.*?)</label>', zone, re.S
        )
    ]


# ---------------------------------------------------------------------------
# The order
# ---------------------------------------------------------------------------


def _family_chips(zone: str) -> list[str]:
    """The top-level row: the chips whose radio is in the launcher's own group."""
    return [
        label
        for panel_id, label in zip(_chip_ids(zone), _chips(zone), strict=True)
        if panel_id in FAMILY_IDS
    ]


def test_the_launcher_offers_four_families_in_the_canonical_order(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    assert _family_chips(_zone(signed_in, matter)) == CANONICAL


@pytest.mark.parametrize("family", sorted(SUBCHOICES), ids=sorted(SUBCHOICES))
def test_a_family_asks_its_own_question_second(signed_in, specialist, family):
    """The distinctions are inside the family, in their own order."""
    matter = factories.MatterFactory(owner=specialist)
    zone = _zone(signed_in, matter)
    ids = _chip_ids(zone)
    labels = _chips(zone)

    inside = [
        label
        for panel_id, label in zip(ids, labels, strict=True)
        if panel_id not in FAMILY_IDS and panel_id.startswith(family.split("-", 1)[1][:3])
    ]
    assert inside == SUBCHOICES[family]


def test_the_open_step_control_is_not_in_the_launcher(signed_in, specialist):
    """`+ Järgmine tegevus` left the row, with a task open and without one.

    There is at most one open `NextAction` and there is now exactly one
    *ordinary* way to set one: while a task is current, `Muuda` in
    `PRAEGUNE TEGEVUS`; otherwise the optional `Järgmine tegevus` inside
    `+ Märge`, beside the thing that prompted it. Two controls both offering to
    set «the next action» is how a lawyer ends up believing they have two
    (docs/adr/0097 §8.2).
    """
    matter = factories.MatterFactory(owner=specialist)
    assert _family_chips(_zone(signed_in, matter)) == CANONICAL

    set_next_action(
        matter=matter,
        text="Saada arvamus ministeeriumile",
        target_date=timezone.localdate(),
        actor=specialist,
    )

    zone = _zone(signed_in, matter)
    assert _family_chips(zone) == CANONICAL
    assert "lisa-jargmine" not in zone


def test_closure_and_deletion_are_outside_the_launcher(signed_in, specialist):
    """`TEEMA TOIMINGUD` is a region of its own, under the capture controls.

    `+ Lõpeta teema` was the last chip in this row and `Kustuta teema` was on
    the edit page. Neither adds content to the Matter, and a row that mixes
    «write this down» with «this file is finished» is a row where the most
    consequential control looks exactly like the most routine one
    (docs/adr/0097 §9).
    """
    matter = factories.MatterFactory(owner=specialist)
    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    zone = _zone(signed_in, matter)

    assert "teema-lopeta" not in zone
    assert "Kustuta teema" not in zone

    operations = body[body.index('id="teema-toimingud"') :]
    operations = operations[: operations.index("</section>")]
    assert 'for="teema-lopeta-valik"' in operations
    assert "Kustuta teema" in operations


def test_a_refusal_reopens_its_own_panel_and_leaves_the_order_alone(signed_in, specialist):
    """The state a refusal comes back in is the one case where the bar is not
    freshly rendered, and it is the case a jumping launcher was worst in."""
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}),
        {"kind": "SURVEY", "audience": ""},
        headers={"HX-Request": "true"},
    )
    body = response.content.decode()
    zone = body[body.index('id="lisa-teemale"') : body.index('id="ajajoon"')]

    assert response.status_code == 400
    assert _family_chips(zone) == CANONICAL
    assert _chosen(zone) == ["lisa-kaasamine"]


def test_a_refusal_inside_a_family_reopens_both_levels(signed_in, specialist):
    """A refused `Töövõit` comes back on `+ Märge` **and** on `Töövõit`.

    One level would be an error message inside a panel nobody can see, or a
    person put back on the ordinary note holding a sentence about a win
    (docs/adr/0097 §8).
    """
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        reverse("matters:add_work_victory", kwargs={"pk": matter.pk}),
        {"victory_change": "", "victory_date": ""},
        headers={"HX-Request": "true"},
    )
    body = response.content.decode()
    zone = body[body.index('id="lisa-teemale"') : body.index('id="ajajoon"')]

    assert response.status_code == 400
    assert _chosen(zone) == ["lisa-marge"]
    checked = {
        pick.group(1)
        for pick in re.finditer(r'id="([a-z-]+)-valik"([^>]*)>', zone)
        if re.search(r"\bchecked\b", pick.group(2))
    }
    assert "marge-toovoit" in checked
    assert "marge-tavaline" not in checked


# ---------------------------------------------------------------------------
# The shape that makes the order stable
# ---------------------------------------------------------------------------


def _chosen(zone: str) -> list[str]:
    """Which launcher radios render `checked`.

    Families only. The two sub-choice groups always have one of their own
    checked — `Tavaline` and `Meile saadetud tagasiside` arrive chosen, so a
    family panel never opens on more chips and no form — and counting those
    here would make «nothing is chosen until somebody chooses» false about a
    page where nothing is open (docs/adr/0097 §8.3).
    """
    return [
        pick.group(1)
        for pick in re.finditer(r'id="([a-z-]+)-valik"([^>]*)>', zone)
        if pick.group(1) in FAMILY_IDS and re.search(r"\bchecked\b", pick.group(2))
    ]


def test_every_chip_is_a_control_and_its_form_is_a_separate_element(signed_in, specialist):
    """The structural claim. A chip is a `<label>` for a radio; the form is the
    `.cx-panel` that follows it. Nothing that a person clicks is also the thing
    that grows when the form opens, which is why no `order` value has to be
    tuned to keep the row still."""
    matter = factories.MatterFactory(owner=specialist)
    zone = _zone(signed_in, matter)

    for panel_id in PANEL_IDS:
        pick = f'id="{panel_id}-valik"'
        assert pick in zone, panel_id
        assert f'for="{panel_id}-valik"' in zone, panel_id
        assert f'id="{panel_id}"' in zone, panel_id
        # radio, then its chip, then its form — in that order and adjacent, which
        # is exactly what `:checked + .disclosure-chip + .cx-panel` needs.
        assert (
            zone.index(pick)
            < zone.index(f'for="{panel_id}-valik"')
            < zone.index(f'id="{panel_id}"')
        ), panel_id

    assert '<details class="cx-panel' not in zone, (
        "a launcher panel that is a disclosure is one that can jump"
    )


def test_each_group_is_its_own_radio_group_so_only_one_form_can_be_open(signed_in, specialist):
    """Three groups, and which one a chip is in is what makes the nesting work.

    The four families share `lisa-valik`, so the browser enforces one-open-at-a-
    time across the row with scripting off. Each family's sub-choices are a
    group of their **own** — `marke-liik`, `arvamuse-liik` — because putting
    them in `lisa-valik` would make choosing `Oluline tähtaeg` un-choose
    `+ Märge`, which is the panel it lives inside (docs/adr/0097 §8).
    """
    matter = factories.MatterFactory(owner=specialist)
    zone = _zone(signed_in, matter)

    names = re.findall(r'<input class="addpick" type="radio" name="([^"]+)"', zone)

    assert len(names) == len(PANEL_IDS)
    assert names.count("lisa-valik") == len(FAMILY_IDS)
    assert set(names) == {"lisa-valik", "marke-liik", "arvamuse-liik"}


def test_no_family_is_chosen_until_somebody_chooses(signed_in, specialist):
    """The row is a choice of four, not four forms."""
    matter = factories.MatterFactory(owner=specialist)

    assert _chosen(_zone(signed_in, matter)) == []


def test_each_family_arrives_with_its_ordinary_sub_choice_chosen(signed_in, specialist):
    """`Tavaline` and `Meile saadetud tagasiside`, so a family opens on a form.

    Not the first alphabetically: a `Märge` is usually just a note, and most of
    what reaches a department is somebody answering it. A family panel that
    opened on three more chips and no form would be an extra click on every
    visit (docs/adr/0097 §8).
    """
    matter = factories.MatterFactory(owner=specialist)
    zone = _zone(signed_in, matter)

    checked = {
        pick.group(1)
        for pick in re.finditer(r'id="([a-z-]+)-valik"([^>]*)>', zone)
        if re.search(r"\bchecked\b", pick.group(2))
    }
    assert checked == {"marge-tavaline", "arvamus-tagasiside"}


def test_each_chip_points_at_the_form_it_opens(signed_in, specialist):
    """`aria-controls`, so the relationship is in the accessibility tree and not
    only in the stylesheet."""
    matter = factories.MatterFactory(owner=specialist)
    zone = _zone(signed_in, matter)

    for panel_id in PANEL_IDS:
        opening = zone[zone.rindex("<input", 0, zone.index(f'id="{panel_id}-valik"')) :]
        opening = opening[: opening.index(">")]
        assert 'type="radio"' in opening, panel_id
        assert f'aria-controls="{panel_id}"' in opening, panel_id


# ---------------------------------------------------------------------------
# The stylesheet's half, asserted where a rename would break it silently
# ---------------------------------------------------------------------------


def test_the_stylesheet_moves_the_form_and_never_a_control():
    """`order` appears in this zone exactly once, on the panel.

    A rule that gave a chip an `order` would be the defect this release removes,
    written back in a different place.
    """
    from pathlib import Path

    from django.conf import settings

    css = (Path(settings.BASE_DIR) / "static" / "css" / "app.css").read_text(encoding="utf-8")
    block = css[css.index("LISA TEEMALE: a stable choice bar") :]
    block = block[: block.index("-- the same language for a lone disclosure")]

    assert ".addpick:checked + .disclosure-chip + .cx-panel" in block
    assert "order: 1" in block
    # The only `order` declaration in the block is the one on the form. Matched
    # at the head of a declaration, because `border:` ends in the same six
    # letters and there are several of those.
    assert len(re.findall(r"(?m)^\s*order:", block)) == 1
    chip_rule = block[block.index("#teema-vaade-wrap .cx-panels > .disclosure-chip") :]
    assert "flex: none" in chip_rule[: chip_rule.index("}")]


# ---------------------------------------------------------------------------
# §7.I — the migration this release carries
# ---------------------------------------------------------------------------


def test_the_feedback_deadline_column_arrives_with_no_data_migration():
    """**§7.I.** One nullable `DateField`, and nothing else at all.

    Every `MatterEngagement` in production predates the question, so `NULL` is
    the truthful answer for all of them — and a nullable `AddField` gives them
    that through schema semantics, with no `RunPython`, no `RunSQL` and no table
    rewrite. A backfill here would be a deadline nobody set.
    """
    migration = MigrationExecutor(connection).loader.get_migration(
        "matters", "0020_engagement_feedback_deadline"
    )

    assert [type(operation).__name__ for operation in migration.operations] == ["AddField"]
    field = migration.operations[0].field
    assert migration.operations[0].name == "feedback_deadline"
    assert field.null is True
    assert field.blank is True
    assert field.has_default() is False
    assert field.db_index is False
