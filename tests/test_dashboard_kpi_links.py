"""A number on Ülevaade and the list behind it are the same query.

The failure this file exists to prevent is quiet and was live: a card reading
*15 Arvamusi koostamisel* linked to an unfiltered register, so a lawyer clicking
it landed on two hundred rows and had to rebuild the filter by hand. Four of the
six cards shared one link.

Every test here asserts the same shape twice: the card's *count* and the
register's *rows* for the card's own URL, compared as sets of primary keys
rather than as two integers. Two counts can agree by accident on a small
fixture; two identical row sets cannot.
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qsl, urlparse

import pytest
from django.utils import timezone

from app.core.enums import Visibility
from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
from app.legacy_import.register_semantics import OpinionSentState
from app.matters import dashboard
from app.matters.enums import RecordMode
from app.matters.register_filters import register_population
from app.matters.services import close_matter, create_matter
from app.workflow.enums import ActionKind, DateSemantics, Disposition
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

#: The cards a lawyer clicks. Kept as data so a card added to the dashboard
#: without a test here shows up as a missing key rather than as silence.
CLICKABLE = ("active", "deadlines", "overdue", "drafting", "unassigned")


def card(user, key):
    return next(entry for entry in dashboard.summary_cards(user) if entry.key == key)


def params_of(card_url: str) -> dict[str, str]:
    """The query string a card links to, as the register would read it."""
    return dict(parse_qsl(urlparse(card_url).query))


def register_ids(user, card_url: str) -> set:
    """Exactly the primary keys `matters:matter_list?<card query>` would page."""
    return set(register_population(user, params_of(card_url)).values_list("pk", flat=True))


def _register_state(matter, *, sent_recorded: bool) -> None:
    """A current register row, enough to answer `?arvamus=`.

    Both derivations of ``VÄLJA`` are set, and a check constraint requires it:
    presence and the four-way reading describe one cell, so a row saying
    "something is recorded" and "nothing is written" at once is a state the
    database refuses. Which of the three non-blank readings this is does not
    matter to the cards — they ask about presence — so the fixture writes the
    one that claims least (ADR 0044).
    """
    reference = factories.MatterSourceReferenceFactory(matter=matter)
    CurrentRegisterState.objects.create(
        matter=matter,
        source_reference=reference,
        source_snapshot_sha256="0" * 64,
        source_sheet="2026",
        source_row_number=reference.source_row_number,
        currency=RegisterCurrency.CURRENT,
        opinion_sent_recorded=sent_recorded,
        opinion_sent_state=(
            OpinionSentState.RECORDED_OTHER if sent_recorded else OpinionSentState.BLANK
        ),
        observed_at=timezone.now(),
    )


@pytest.fixture
def world(db, specialist, other_specialist):
    """One of each shape the cards count, plus one nobody else may see.

    Deliberately more than the minimum: every card's population has at least one
    row that belongs in it and at least one that does not, so a filter that
    matched everything would fail rather than pass.
    """
    today = timezone.localdate()
    stage = factories.StageFactory()

    soon = create_matter(
        title="Tähtaeg nädala sees",
        owner=other_specialist,
        stage=stage,
        response_deadline=today + timedelta(days=3),
    )
    # Outside the seven-day horizon: in *Aktiivsed teemad*, not in *Tähtajad*.
    create_matter(
        title="Tähtaeg kaugel",
        owner=other_specialist,
        stage=stage,
        response_deadline=today + timedelta(days=90),
    )
    unassigned = create_matter(title="Vastutajata teema", owner=None, stage=stage)

    overdue = create_matter(title="Hilinenud tegevusega teema", owner=other_specialist)
    set_next_action(
        matter=overdue,
        text="Saada arvamus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today - timedelta(days=2),
    )
    # A WAIT whose review date has passed is *not* overdue, and the card must
    # not collect it (master specification 18.8).
    waiting = create_matter(title="Ootab ministeeriumi", owner=other_specialist)
    set_next_action(
        matter=waiting,
        text="Ootame vastust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=today - timedelta(days=2),
    )

    drafting = create_matter(title="Arvamus koostamisel", owner=other_specialist)
    _register_state(drafting, sent_recorded=False)
    sent = create_matter(title="Arvamus saadetud", owner=other_specialist)
    _register_state(sent, sent_recorded=True)

    # Closed and archived rows exist so "aktiivsed" has something to exclude.
    # Closed through the service: `is_open=False` on its own violates
    # `matters_closure_fields_consistent`, because a closed Matter without a
    # disposition and a closing timestamp is not a state the database allows.
    close_matter(
        matter=create_matter(title="Suletud teema", owner=other_specialist),
        disposition=Disposition.COMPLETED,
        actor=other_specialist,
    )
    factories.ArchiveMatterFactory(title="Arhiivirida")

    hidden = create_matter(
        title="Piiratud teema",
        owner=specialist,
        visibility=Visibility.RESTRICTED,
        response_deadline=today + timedelta(days=2),
    )
    return {
        "soon": soon,
        "unassigned": unassigned,
        "overdue": overdue,
        "waiting": waiting,
        "drafting": drafting,
        "sent": sent,
        "hidden": hidden,
    }


# ---------------------------------------------------------------------------
# Parity: the count is the list
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", CLICKABLE)
def test_every_card_counts_exactly_the_rows_its_link_opens(world, other_specialist, key):
    """The whole point of the branch, asserted five times.

    Compared as row identities, not as two integers: two counts can coincide on
    a small fixture, two row sets cannot.
    """
    entry = card(other_specialist, key)
    assert entry.count == len(register_ids(other_specialist, entry.url))


@pytest.mark.parametrize("key", CLICKABLE)
def test_every_card_carries_a_filter(world, other_specialist, key):
    """A card that links to the bare register is the defect this replaced."""
    entry = card(other_specialist, key)
    assert params_of(entry.url), f"card {key!r} opens an unfiltered register"


# ---------------------------------------------------------------------------
# Identity: the right rows, not merely the right number
# ---------------------------------------------------------------------------


def test_deadlines_opens_the_matters_inside_the_horizon(world, other_specialist):
    rows = register_ids(other_specialist, card(other_specialist, "deadlines").url)
    assert world["soon"].pk in rows
    assert world["unassigned"].pk not in rows, "no deadline recorded"


def test_drafting_opens_the_unsent_opinion_and_not_the_sent_one(world, other_specialist):
    rows = register_ids(other_specialist, card(other_specialist, "drafting").url)
    assert rows == {world["drafting"].pk}


def test_unassigned_opens_only_matters_with_no_owner(world, other_specialist):
    rows = register_ids(other_specialist, card(other_specialist, "unassigned").url)
    assert rows == {world["unassigned"].pk}


def test_overdue_opens_late_work_and_not_a_passed_review(world, other_specialist):
    """A WAIT past its review date is due for a look, never late."""
    rows = register_ids(other_specialist, card(other_specialist, "overdue").url)
    assert world["overdue"].pk in rows
    assert world["waiting"].pk not in rows


def test_active_excludes_closed_and_archive_rows(world, other_specialist):
    rows = register_ids(other_specialist, card(other_specialist, "active").url)
    titles = {"Suletud teema", "Arhiivirida"}
    from app.matters.models import Matter

    excluded = set(Matter.objects.filter(title__in=titles).values_list("pk", flat=True))
    assert rows.isdisjoint(excluded)
    assert all(
        matter.record_mode == RecordMode.FULL and matter.is_open
        for matter in Matter.objects.filter(pk__in=rows)
    )


# ---------------------------------------------------------------------------
# Authorization: a restricted Matter reaches neither the count nor the list
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", CLICKABLE)
def test_a_restricted_matter_is_absent_from_both_halves(world, reader, key):
    """The disclosure that is easy to miss, because nothing on screen looks wrong.

    Hiding a row at render time while leaving it inside the total tells the
    reader it exists. Both halves come from `visible_to`, so this asserts the
    property rather than the implementation.
    """
    entry = card(reader, key)
    assert world["hidden"].pk not in register_ids(reader, entry.url)


def test_the_owner_of_a_restricted_matter_sees_it_in_both_halves(world, specialist):
    entry = card(specialist, "deadlines")
    assert world["hidden"].pk in register_ids(specialist, entry.url)
    assert entry.count == len(register_ids(specialist, entry.url))


# ---------------------------------------------------------------------------
# The card that was removed
# ---------------------------------------------------------------------------


def test_the_missing_next_action_card_is_gone(world, other_specialist):
    """It measured how far the migration had got, not a problem to act on.

    The condition is not gone with it — `?tegevus=puudub` still opens exactly
    those Matters, which is what makes removing the card safe.
    """
    assert "no_action" not in {entry.key for entry in dashboard.summary_cards(other_specialist)}
    assert register_ids(other_specialist, "/teemad/?olek=avatud&tegevus=puudub")


def test_no_card_note_names_a_register_column(world, other_specialist):
    """Subtitles explain the metric, never where the data came from.

    *Registris puudub VÄLJA märge* described an import column to somebody who
    wanted to know which opinions still need writing.
    """
    notes = " ".join(entry.note for entry in dashboard.summary_cards(other_specialist))
    for jargon in ("VÄLJA", "VASTUTAJA", "Registripõhine"):
        assert jargon not in notes


# ---------------------------------------------------------------------------
# The list surface behind the link
# ---------------------------------------------------------------------------


def test_the_register_shows_a_chip_for_every_filter_a_card_applies(world, client, other_specialist):
    """Arriving from a KPI, the reader can see *why* this set is on screen.

    A filtered list with no visible filter is indistinguishable from a broken
    register, and the reader has no way to get back to the whole list.
    """
    client.force_login(other_specialist)
    response = client.get(card(other_specialist, "drafting").url)

    assert response.status_code == 200
    chips = {chip["name"] for chip in response.context["active_filters"]}
    assert "arvamus" in chips
    assert response.context["has_any_filter"] is True
    # And clearing returns to the ordinary register rather than to "everything
    # except the dimension somebody forgot to list".
    assert "arvamus" not in response.context["cleared_query"]


def test_clearing_the_filter_returns_the_whole_register(world, client, other_specialist):
    client.force_login(other_specialist)
    filtered = client.get(card(other_specialist, "drafting").url)
    cleared = client.get(f"/teemad/?{filtered.context['cleared_query']}")

    assert cleared.context["total"] > filtered.context["total"]
    assert cleared.context["has_any_filter"] is False


def test_the_register_panel_can_set_the_dimension_the_card_opens(world, client, other_specialist):
    """A filter a KPI can apply and the panel cannot is one somebody can arrive
    at and never reproduce — or narrow further without editing the URL by hand.
    """
    client.force_login(other_specialist)
    body = client.get("/teemad/").content.decode()

    assert 'name="arvamus"' in body
    for value in ("koostamisel", "saadetud"):
        assert f'value="{value}"' in body


def test_the_opinion_dimension_answers_both_of_its_values(world, other_specialist):
    """`saadetud` is the complement of `koostamisel` over current register rows.

    Asserted because the filter is the card's definition: a value that quietly
    matched nothing would make the card's own count wrong rather than merely
    make one control useless.
    """
    drafting = register_ids(other_specialist, "/teemad/?olek=avatud&arvamus=koostamisel")
    sent = register_ids(other_specialist, "/teemad/?olek=avatud&arvamus=saadetud")

    assert drafting == {world["drafting"].pk}
    assert sent == {world["sent"].pk}
    assert drafting.isdisjoint(sent)
