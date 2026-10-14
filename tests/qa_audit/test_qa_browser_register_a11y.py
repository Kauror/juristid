"""Independent QA 2026-10-14 — browser area: the register and the Teema rail without colour.

Found in a real browser against main 81a7d6e4 (worklog
C:/CC/_worklogs/independent-qa-2026-10-14/agents/browser/, scripts overdue_reg.py,
colour_only.py and dupnames.py) and pinned at template level.

Each defect pinned here as a strict xfail was fixed by the QA remediation of
2026-10-14 (agent I); the tests are now ordinary regression guards.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.matters.services import create_matter
from app.workflow.enums import ActionKind, DateSemantics
from tests.factories import NextActionFactory

pytestmark = pytest.mark.django_db


def date_cell_of(body: str, title: str) -> str:
    """The `Kuupäev` cell of the register row that carries ``title``."""
    row = body.split(title, 1)[1]
    cell = row.split('class="table__date"', 1)[1]
    return cell.split("</td>", 1)[0]


def visible_and_spoken_text(fragment: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip()


def test_the_register_marks_an_overdue_step_with_the_overdue_class(signed_in, specialist):
    """Control: the row is recognised as overdue (the red/bold style is applied)."""
    matter = create_matter(title="Hilinenud sammuga rida", actor=specialist, owner=specialist)
    NextActionFactory(
        matter=matter,
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() - timedelta(days=9),
        responsible=specialist,
    )

    body = signed_in.get(reverse("matters:matter_list") + "?olek=avatud").content.decode()

    assert "dateline--overdue" in date_cell_of(body, "Hilinenud sammuga rida")


def test_the_register_states_an_overdue_step_in_words(signed_in, specialist):
    matter = create_matter(title="Hilinenud sammuga sõnas", actor=specialist, owner=specialist)
    NextActionFactory(
        matter=matter,
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() - timedelta(days=9),
        responsible=specialist,
    )

    body = signed_in.get(reverse("matters:matter_list") + "?olek=avatud").content.decode()
    words = visible_and_spoken_text(date_cell_of(body, "Hilinenud sammuga sõnas")).lower()

    assert "üle" in words or "hilinenud" in words, words


def test_the_rail_add_controls_have_distinct_accessible_names(signed_in, specialist):
    matter = create_matter(title="Rööpa lisamisnupud", actor=specialist, owner=specialist)

    page = reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    body = signed_in.get(page).content.decode()
    summaries = re.findall(r"<summary([^>]*)>(.*?)</summary>", body, flags=re.S)
    bare = [
        attrs
        for attrs, inner in summaries
        if visible_and_spoken_text(inner) == "+ Lisa"
        and "aria-label" not in attrs
        and not re.search(r'title="[^"]+"', attrs)
    ]

    assert len(bare) <= 1, f"{len(bare)} disclosures are named only «+ Lisa»"
