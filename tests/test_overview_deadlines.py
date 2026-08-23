"""Ülevaade's deadline period: what each window means, and what it may not do.

The window is arithmetic on dates, which is exactly the kind of code that looks
obviously right and is off by one. So every boundary is asserted from both
sides, and from both date sources — a Matter's own `response_deadline` and an
open NextAction's target date are different facts that share this table, and a
period that quietly applied to one of them would hide half the department's
work while looking like it worked.

Two rules that are not about arithmetic:

*Nothing here reaches backwards.* A date that has passed is not upcoming. What
is genuinely late is already said, in its own words, by the attention list.

*Authorization runs before the count.* The number beside the heading is read
off the same scoped queryset as the rows, so a restricted Matter cannot raise a
total on a page that does not list it.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.matters import dashboard
from app.matters.services import create_matter
from app.workflow.enums import ActionKind, DateSemantics
from app.workflow.services import set_next_action

pytestmark = pytest.mark.django_db

OVERVIEW = "matters:overview"


@pytest.fixture
def today():
    return timezone.localdate()


def _matter_due(owner, *, days: int, title: str):
    """A Matter whose own `Arvamuse tähtaeg` falls `days` from today."""
    return create_matter(
        title=title,
        owner=owner,
        reference_year=2026,
        response_deadline=timezone.localdate() + timedelta(days=days),
    )


def _action_due(owner, *, days: int, title: str, kind=ActionKind.DO, semantics=None):
    """A Matter carrying an open NextAction dated `days` from today."""
    matter = create_matter(title=title, owner=owner, reference_year=2026)
    set_next_action(
        matter=matter,
        text="Järgmine samm",
        kind=kind,
        date_semantics=semantics or DateSemantics.DEADLINE,
        target_date=timezone.localdate() + timedelta(days=days),
        actor=owner,
    )
    return matter


def _titles(user, key: str) -> set[str]:
    window = dashboard.deadline_window(key)
    return {row.matter.title for row in dashboard.upcoming_rows(user, window=window).rows}


# -- what the page leads with -----------------------------------------------


def _heading_order(body: str) -> list[str]:
    """The main column's section headings, in the order the document has them.

    Positions of the heading ids in the rendered document rather than the order
    they happen to sit in the template file: an include, a block override or a
    grid could put them on screen in a different order than the source suggests,
    and it is the rendered order the department reads.
    """
    anchors = ("tahtajad-heading", "saabunud-heading", "tahelepanu-heading")
    found = [(body.index(f'id="{anchor}"'), anchor) for anchor in anchors]
    return [anchor for _, anchor in sorted(found)]


def test_the_page_leads_with_dates_then_arrivals_then_the_quality_queue(client, specialist) -> None:
    """Priority order. The triage list is useful; it is not the morning read."""
    client.force_login(specialist)
    body = client.get(reverse(OVERVIEW)).content.decode()

    assert _heading_order(body) == [
        "tahtajad-heading",
        "saabunud-heading",
        "tahelepanu-heading",
    ]


def test_the_attention_section_says_what_it_wants(client, specialist) -> None:
    """ "Tähelepanu" named a topic. "Vajab tähelepanu" names a queue."""
    client.force_login(specialist)
    body = client.get(reverse(OVERVIEW)).content.decode()

    assert "Vajab tähelepanu" in body


def test_the_deadline_section_is_no_longer_named_for_one_horizon(client, specialist) -> None:
    """It was "Lähenevad tähtajad" while it could only look a fortnight ahead."""
    client.force_login(specialist)
    body = client.get(reverse(OVERVIEW)).content.decode()

    assert "Lähenevad tähtajad" not in body
    assert "Tähtajad" in body


# -- the windows, from both sides of every boundary -------------------------


@pytest.mark.parametrize(
    ("key", "included", "excluded"),
    [
        ("7", (0, 1, 7), (-1, 8, 31, 400)),
        ("14", (0, 7, 14), (-1, 15, 31, 400)),
        ("30", (0, 14, 30), (-1, 31, 400)),
        ("30plus", (31, 400), (-1, 0, 7, 30)),
        ("koik", (0, 7, 30, 31, 400), (-1,)),
    ],
)
def test_a_response_deadline_falls_in_exactly_the_right_windows(
    specialist, key, included, excluded
) -> None:
    for offset in {*included, *excluded}:
        _matter_due(specialist, days=offset, title=f"Arvamus {offset}")

    titles = _titles(specialist, key)
    for offset in included:
        assert f"Arvamus {offset}" in titles, f"{key}: {offset} should be inside"
    for offset in excluded:
        assert f"Arvamus {offset}" not in titles, f"{key}: {offset} should be outside"


@pytest.mark.parametrize(
    ("key", "included", "excluded"),
    [
        ("7", (0, 1, 7), (-1, 8, 31, 400)),
        ("14", (0, 7, 14), (-1, 15, 31, 400)),
        ("30", (0, 14, 30), (-1, 31, 400)),
        ("30plus", (31, 400), (-1, 0, 7, 30)),
        ("koik", (0, 7, 30, 31, 400), (-1,)),
    ],
)
def test_an_action_target_date_falls_in_exactly_the_same_windows(
    specialist, key, included, excluded
) -> None:
    """The second source. A period that filtered only the first would lie."""
    for offset in {*included, *excluded}:
        _action_due(specialist, days=offset, title=f"Tegevus {offset}")

    titles = _titles(specialist, key)
    for offset in included:
        assert f"Tegevus {offset}" in titles, f"{key}: {offset} should be inside"
    for offset in excluded:
        assert f"Tegevus {offset}" not in titles, f"{key}: {offset} should be outside"


def test_the_thirty_day_windows_meet_without_a_gap_or_an_overlap(specialist) -> None:
    """Day 30 belongs to one of them and day 31 to the other. Never both."""
    _matter_due(specialist, days=30, title="Kolmekümnes päev")
    _matter_due(specialist, days=31, title="Kolmekümne esimene päev")

    within = _titles(specialist, "30")
    beyond = _titles(specialist, "30plus")

    assert within == {"Kolmekümnes päev"}
    assert beyond == {"Kolmekümne esimene päev"}


def test_no_window_reaches_into_the_past(specialist) -> None:
    """Yesterday is not upcoming — in any period, including Kõik."""
    _matter_due(specialist, days=-3, title="Eilne arvamus")
    _action_due(specialist, days=-3, title="Eilne tegevus")

    for window in dashboard.DEADLINE_WINDOWS:
        titles = _titles(specialist, window.key)
        assert "Eilne arvamus" not in titles, window.key
        assert "Eilne tegevus" not in titles, window.key


# -- meaning ----------------------------------------------------------------


def test_each_date_keeps_the_meaning_it_actually_has(specialist) -> None:
    """A review date inside the window is still a review date.

    Four kinds of date share this column, and a period control that collapsed
    them into one word called "tähtaeg" would undo the distinction the whole
    register cutover exists to preserve.
    """
    _matter_due(specialist, days=5, title="Arvamuse tähtajaga teema")
    _action_due(specialist, days=5, title="Tegevuse tähtajaga teema")
    _action_due(
        specialist,
        days=5,
        title="Ülevaadatav teema",
        kind=ActionKind.WAIT,
        semantics=DateSemantics.REVIEW_ON,
    )
    _action_due(
        specialist,
        days=5,
        title="Oodatava ajaga teema",
        kind=ActionKind.MONITOR,
        semantics=DateSemantics.EXPECTED_AROUND,
    )

    window = dashboard.deadline_window("7")
    meanings = {
        row.matter.title: row.meaning
        for row in dashboard.upcoming_rows(specialist, window=window).rows
    }

    assert meanings["Arvamuse tähtajaga teema"] == dashboard.MEANING_RESPONSE
    assert meanings["Tegevuse tähtajaga teema"] == dashboard.MEANING_ACTION
    assert meanings["Ülevaadatav teema"] == dashboard.MEANING_REVIEW
    assert meanings["Oodatava ajaga teema"] == dashboard.MEANING_EXPECTED


# -- the count beside the heading -------------------------------------------


def test_the_total_counts_every_match_even_when_the_table_stops(specialist) -> None:
    """The cap is a rendering decision; the heading states the truth.

    Deriving the number from the rendered list would make it stop at the limit
    and read as "that is all there is".
    """
    rows_wanted = dashboard.UPCOMING_LIMIT + 5
    for index in range(rows_wanted):
        _matter_due(specialist, days=3, title=f"Teema {index:03d}")

    result = dashboard.upcoming_rows(specialist, window=dashboard.deadline_window("7"))
    assert result.total == rows_wanted
    assert len(result.rows) == dashboard.UPCOMING_LIMIT


def test_the_total_counts_both_sources(specialist) -> None:
    _matter_due(specialist, days=2, title="Arvamus")
    _action_due(specialist, days=2, title="Tegevus")

    assert dashboard.upcoming_rows(specialist, window=dashboard.deadline_window("7")).total == 2


def test_a_restricted_matter_raises_no_total_for_a_reader_who_cannot_see_it(
    specialist, other_specialist
) -> None:
    """Authorization before arithmetic, in the one place a period could skip it."""
    create_matter(
        title="Piiratud tähtaeg",
        owner=specialist,
        reference_year=2026,
        visibility=Visibility.RESTRICTED,
        response_deadline=timezone.localdate() + timedelta(days=2),
    )

    for window in dashboard.DEADLINE_WINDOWS:
        result = dashboard.upcoming_rows(other_specialist, window=window)
        assert result.total == 0, window.key
        assert result.rows == [], window.key

    assert dashboard.upcoming_rows(specialist, window=dashboard.deadline_window("7")).total == 1


# -- the URL ----------------------------------------------------------------


def test_the_default_window_is_the_fortnight_the_table_always_used(specialist) -> None:
    assert dashboard.DEFAULT_DEADLINE_WINDOW.key == "14"
    assert dashboard.DEFAULT_DEADLINE_WINDOW.days == dashboard.UPCOMING_HORIZON_DAYS
    assert dashboard.deadline_window(None).key == "14"


@pytest.mark.parametrize("raw", ["nonsense", "", "7 päeva", "-1", "30PLUS", "0"])
def test_an_unrecognised_period_falls_back_instead_of_emptying_the_page(raw) -> None:
    """No 500, and no convincing empty list somebody reads as "no deadlines"."""
    assert dashboard.deadline_window(raw).key == dashboard.DEFAULT_DEADLINE_WINDOW.key


def test_the_page_reads_the_period_from_the_url(client, specialist) -> None:
    _matter_due(specialist, days=20, title="Kahekümne päeva pärast")
    client.force_login(specialist)

    fortnight = client.get(reverse(OVERVIEW)).content.decode()
    assert "Kahekümne päeva pärast" not in fortnight

    month = client.get(reverse(OVERVIEW), {"tahtajad": "30"}).content.decode()
    assert "Kahekümne päeva pärast" in month


def test_a_nonsense_period_still_renders_the_default_view(client, specialist) -> None:
    _matter_due(specialist, days=3, title="Kolme päeva pärast")
    client.force_login(specialist)

    response = client.get(reverse(OVERVIEW), {"tahtajad": "jama"})
    assert response.status_code == 200

    body = response.content.decode()
    assert "Kolme päeva pärast" in body
    selected = [option for option in response.context["dashboard"].windows if option.active]
    assert [option.key for option in selected] == [dashboard.DEFAULT_DEADLINE_WINDOW.key]


def test_every_period_link_carries_the_whole_state(client, specialist) -> None:
    """A link, not a script: the page has to survive being pasted and reloaded."""
    client.force_login(specialist)
    options = client.get(reverse(OVERVIEW), {"tahtajad": "30plus"}).context["dashboard"].windows

    assert [option.label for option in options] == [
        "7 päeva",
        "14 päeva",
        "30 päeva",
        "30+ päeva",
        "Kõik",
    ]
    assert [option.query for option in options] == [
        "tahtajad=7",
        "tahtajad=14",
        "tahtajad=30",
        "tahtajad=30plus",
        "tahtajad=koik",
    ]
    assert [option.key for option in options if option.active] == ["30plus"]


def test_the_summary_card_keeps_its_own_fixed_horizon(client, specialist) -> None:
    """The card is a KPI, not a view of the table. The period does not move it."""
    _matter_due(specialist, days=3, title="Nädala sees")
    _matter_due(specialist, days=25, title="Kuu sees")
    client.force_login(specialist)

    for period in ("7", "koik"):
        cards = {
            card.key: card
            for card in client.get(reverse(OVERVIEW), {"tahtajad": period})
            .context["dashboard"]
            .cards
        }
        assert cards["deadlines"].count == 1, period
        assert cards["deadlines"].label == "Tähtajad 7 päeva jooksul"
