"""`LISA TEEMALE` is a stable choice bar, and the server's half of that contract.

Eight chips in one order. Choosing one opens its form; it does not move the
chip, reorder the row, or change which line anything sits on. The browser half —
that no control's bounding box moves by a pixel when a panel opens — is
`e2e/test_add_launcher_stability.py`; what *this* file pins is the markup that
makes the CSS able to keep that promise:

* the order the launcher renders in, with and without an open `NextAction`;
* that each chip is a control and each form is a separate element after it, so
  the element that grows is never the element you press;
* that the open state lives on a radio, which is what gives the browser
  one-open-at-a-time and a keyboard contract without a line of script.

The panels were `<details>` until 2026-09-14. An open one took
`flex: 1 1 100%; order: 1`, which sent the chip somebody had just clicked to the
head of the next line and took the rest of the row with it (design feedback
2026-09-14).
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

#: The canonical order, top to bottom of the product's own reasoning: what
#: happened, what happens next, who was asked, what is due, what commences, what
#: was won — and only then, finishing the file.
CANONICAL = [
    "+ Märge",
    "+ Järgmine tegevus",
    "+ Kaasamine",
    "+ Oluline tähtaeg",
    "+ Jõustumine",
    "+ Töövõit",
    # `+ Ülevaade / uudis` goes after the win and before the closure, which is
    # where the reasoning puts it: telling the membership what happened is the
    # last thing done *about* a file, and finishing the file is not routine
    # capture at all (docs/adr/0081 §2).
    "+ Ülevaade / uudis",
    # `+ Väline seisukoht` is the last of the capture operations and sits
    # directly before the closure: what somebody else said about the file is
    # reference material recorded alongside the work, and finishing the file is
    # not routine capture at all (docs/adr/0084 §6).
    "+ Väline seisukoht",
    "+ Lõpeta teema",
]

#: With a task already open there is exactly one control for it and it is
#: `Muuda` in PRAEGUNE TEGEVUS. Two controls both offering to set "the next
#: action" is how a lawyer ends up believing they have two (brief §15).
WITHOUT_NEXT_ACTION = [label for label in CANONICAL if label != "+ Järgmine tegevus"]

PANEL_IDS = [
    "lisa-marge",
    "lisa-jargmine",
    "lisa-kaasamine",
    "lisa-tahtaeg",
    "lisa-joustumine",
    "lisa-toovoit",
    "lisa-koduleht",
    "lisa-valine-seisukoht",
    "lisa-lopeta",
]


def _zone(client, matter) -> str:
    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()
    start = body.index('id="lisa-teemale"')
    return body[start : body.index('id="ajajoon"', start)]


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


def test_the_launcher_offers_nine_choices_in_the_canonical_order(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    assert _chips(_zone(signed_in, matter)) == CANONICAL


def test_an_open_step_removes_only_its_own_chip_and_reorders_nothing(signed_in, specialist):
    """`+ Järgmine tegevus` goes; the remaining eight keep their relative order."""
    matter = factories.MatterFactory(owner=specialist)
    set_next_action(
        matter=matter,
        text="Saada arvamus ministeeriumile",
        target_date=timezone.localdate(),
        actor=specialist,
    )

    assert _chips(_zone(signed_in, matter)) == WITHOUT_NEXT_ACTION


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
    assert _chips(zone) == CANONICAL
    assert _chosen(zone) == ["lisa-kaasamine"]


# ---------------------------------------------------------------------------
# The shape that makes the order stable
# ---------------------------------------------------------------------------


def _chosen(zone: str) -> list[str]:
    """Which launcher radios render `checked`."""
    return [
        pick.group(1)
        for pick in re.finditer(r'id="(lisa-[a-z]+)-valik"([^>]*)>', zone)
        if re.search(r"\bchecked\b", pick.group(2))
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


def test_the_choices_are_one_radio_group_so_only_one_form_can_be_open(signed_in, specialist):
    """One `name`, nine values: the browser enforces the product rule, and it
    keeps enforcing it with scripting off."""
    matter = factories.MatterFactory(owner=specialist)
    zone = _zone(signed_in, matter)

    names = re.findall(r'<input class="addpick" type="radio" name="([^"]+)"', zone)

    assert len(names) == len(PANEL_IDS)
    assert set(names) == {"lisa-valik"}


def test_nothing_is_chosen_until_somebody_chooses(signed_in, specialist):
    """The zone is a choice of nine, not nine forms."""
    matter = factories.MatterFactory(owner=specialist)

    assert _chosen(_zone(signed_in, matter)) == []


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
