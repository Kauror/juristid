"""The register filters the retired Ülevaade cards linked to, and their rows.

The cards are gone; the filters are the register's own and other surfaces link
to them. Each test asserts the rows a filter opens, as sets of primary keys
rather than as a count — two counts can agree by accident on a small fixture,
two identical row sets cannot.
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlparse

import pytest
from django.utils import timezone

from app.core.dates import format_estonian_date
from app.core.enums import Visibility
from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
from app.legacy_import.register_semantics import OpinionSentState
from app.matters.enums import RecordMode
from app.matters.register_filters import OPINION_DRAFTING, register_population
from app.matters.selectors import MISSING
from app.matters.services import close_matter, create_matter
from app.workflow.enums import ActionKind, DateSemantics, Disposition
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

#: The filters the five cards applied, by the key each card had.
CLICKABLE = ("active", "deadlines", "overdue", "drafting", "unassigned")


def _url(key: str) -> str:
    today = timezone.localdate()
    current = {"olek": "avatud", "liik": RecordMode.FULL}
    params = {
        "active": current,
        "deadlines": {
            **current,
            "tahtaeg_alates": format_estonian_date(today),
            "tahtaeg_kuni": format_estonian_date(today + timedelta(days=7)),
        },
        "overdue": {"olek": "avatud", "tegevus": "hilinenud"},
        "drafting": {**current, "arvamus": OPINION_DRAFTING},
        "unassigned": {**current, "vastutaja": MISSING},
    }[key]
    return f"/teemad/?{urlencode(params)}"


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
    database refuses. A marked row reads ``DATE``: «Arvamus saadetud» asks
    whether the register says the opinion went out, which only a date says —
    «ei saatnud» and an unreadable mark are marked too, and are not sent
    (RULE-04, ADR 0045).
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
        opinion_sent_state=OpinionSentState.DATE if sent_recorded else OpinionSentState.BLANK,
        opinion_sent_date=timezone.localdate() if sent_recorded else None,
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
# Identity: the right rows, not merely the right number
# ---------------------------------------------------------------------------


def test_deadlines_opens_the_matters_inside_the_horizon(world, other_specialist):
    rows = register_ids(other_specialist, _url("deadlines"))
    assert world["soon"].pk in rows
    assert world["unassigned"].pk not in rows, "no deadline recorded"


def test_drafting_opens_the_unsent_opinion_and_not_the_sent_one(world, other_specialist):
    rows = register_ids(other_specialist, _url("drafting"))
    assert rows == {world["drafting"].pk}


def test_unassigned_opens_only_matters_with_no_owner(world, other_specialist):
    rows = register_ids(other_specialist, _url("unassigned"))
    assert rows == {world["unassigned"].pk}


def test_overdue_opens_late_work_and_not_a_passed_review(world, other_specialist):
    """A WAIT past its review date is due for a look, never late."""
    rows = register_ids(other_specialist, _url("overdue"))
    assert world["overdue"].pk in rows
    assert world["waiting"].pk not in rows


def test_active_excludes_closed_and_archive_rows(world, other_specialist):
    rows = register_ids(other_specialist, _url("active"))
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
    assert world["hidden"].pk not in register_ids(reader, _url(key))


def test_the_owner_of_a_restricted_matter_sees_it_in_both_halves(world, specialist):
    assert world["hidden"].pk in register_ids(specialist, _url("deadlines"))


# ---------------------------------------------------------------------------
# The card that was removed
# ---------------------------------------------------------------------------


def test_the_quiet_population_is_still_one_filter_away(world, other_specialist):
    """`?tegevus=puudub` opens the Matters with no next step."""
    assert register_ids(other_specialist, "/teemad/?olek=avatud&tegevus=puudub")


# ---------------------------------------------------------------------------
# The list surface behind the link
# ---------------------------------------------------------------------------


def test_the_register_shows_a_chip_for_every_filter_a_card_applies(world, client, other_specialist):
    """Arriving from a KPI, the reader can see *why* this set is on screen.

    A filtered list with no visible filter is indistinguishable from a broken
    register, and the reader has no way to get back to the whole list.
    """
    client.force_login(other_specialist)
    response = client.get(_url("drafting"))

    assert response.status_code == 200
    chips = {chip["name"] for chip in response.context["active_filters"]}
    assert "arvamus" in chips
    assert response.context["has_any_filter"] is True
    # And clearing returns to the ordinary register rather than to "everything
    # except the dimension somebody forgot to list".
    assert "arvamus" not in response.context["cleared_query"]


def test_clearing_the_filter_returns_the_whole_register(world, client, other_specialist):
    client.force_login(other_specialist)
    filtered = client.get(_url("drafting"))
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
