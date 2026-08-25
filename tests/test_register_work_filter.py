"""`?too=` — the dated-work populations, as a register dimension.

Why this dimension exists
-------------------------
Ülevaade counts *work* in four places, and work is not the same thing as an open
instruction. An ``Oluline tähtaeg`` whose day has passed is genuinely late and
carries no ``NextAction`` at all, so ``?tegevus=hilinenud`` — which can only ask
about the open action — returns a list shorter than the number that linked to
it. A list shorter than its own count reads as a bug in the count, and that is
the trust this page cannot spend.

So the register gained one parameter whose values are the read model's own
populations, resolved by the read model's own function. Nothing new is measured
here: ``work_population_ids`` is what the figure counted, and what the filter
narrows to. The tests below hold the three properties that make it worth having.

**One definition.** The filter's rows are the read model's rows.
**Two different questions.** ``?vastutaja=`` asks who owns the file;
``?too_vastutaja=`` asks who must do the late work. Merging them would answer
neither (master specification 18.1).
**An unreadable value empties the list.** Every other filter in
``register_filters`` behaves this way, for the same reason: a chip above the
whole register is a lie the reader has no way to catch.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from app.intelligence.services import add_important_date
from app.matters import work_items as wi
from app.matters.register_filters import register_population
from app.matters.services import close_matter, create_matter
from app.workflow.enums import ActionKind, DateSemantics, Disposition
from app.workflow.services import set_next_action

pytestmark = pytest.mark.django_db


@pytest.fixture
def today():
    return timezone.localdate()


def rows(user, **params) -> set[str]:
    return {
        matter.title
        for matter in register_population(user, {"olek": "avatud", "liik": "FULL", **params})
    }


@pytest.fixture
def late_world(db, specialist, other_specialist, today):
    """One Matter late through an action, one late through a milestone.

    The milestone is the case ``?tegevus=`` cannot reach, and it is the reason
    this dimension exists rather than one more value on the existing one.
    """
    by_action = create_matter(
        title="Hilinenud tegevusega", owner=specialist, reference_year=2026, actor=specialist
    )
    set_next_action(
        matter=by_action,
        text="Saada arvamus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today - timedelta(days=2),
        responsible=other_specialist,
        actor=specialist,
    )
    by_milestone = create_matter(
        title="Hilinenud tähtajaga", owner=specialist, reference_year=2026, actor=specialist
    )
    add_important_date(
        matter=by_milestone,
        title="Möödunud oluline tähtaeg",
        date_value=today - timedelta(days=5),
        period_end=today - timedelta(days=5),
        actor=specialist,
    )
    # A WAIT past its review date is ripe for a look, never late.
    waiting = create_matter(
        title="Ootab ministeeriumi", owner=specialist, reference_year=2026, actor=specialist
    )
    set_next_action(
        matter=waiting,
        text="Ootame vastust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=today - timedelta(days=9),
        actor=specialist,
    )
    return {"by_action": by_action, "by_milestone": by_milestone, "waiting": waiting}


def test_late_work_includes_the_milestone_the_action_filter_cannot_see(late_world, department_head):
    """The whole reason for the dimension, in one assertion.

    ``?tegevus=hilinenud`` finds the Matter with the late DO and cannot find the
    one whose ``Oluline tähtaeg`` passed, because there is no action on it to
    filter by.
    """
    assert rows(department_head, too=wi.WORK_OVERDUE) == {
        "Hilinenud tegevusega",
        "Hilinenud tähtajaga",
    }
    assert rows(department_head, tegevus="hilinenud") == {"Hilinenud tegevusega"}


def test_a_passed_review_date_is_ripe_and_never_late(late_world, department_head):
    """One word is the difference between "you failed" and "have a look at this"."""
    assert "Ootab ministeeriumi" not in rows(department_head, too=wi.WORK_OVERDUE)
    assert rows(department_head, too=wi.WORK_RIPE) == {"Ootab ministeeriumi"}


def test_the_filter_and_the_read_model_hold_the_same_matters(late_world, department_head, today):
    """Asserted as row identities rather than as two integers.

    Two counts can agree by accident on a small fixture; two identical sets
    cannot.
    """
    for key in wi.WORK_POPULATIONS:
        expected = wi.work_population_ids(department_head, key, today=today)
        listed = set(
            register_population(
                department_head, {"olek": "avatud", "liik": "FULL", "too": key}
            ).values_list("pk", flat=True)
        )
        assert listed == expected, key


def test_responsibility_is_not_ownership(late_world, department_head, specialist, other_specialist):
    """The late step belongs to whoever must do it; the file belongs to its owner.

    Both Matters are owned by the same person, and only one of them carries a
    step delegated to somebody else. A single "who is this about" filter would
    have to pick one of those two answers and would be wrong about the other.
    """
    assert rows(department_head, too=wi.WORK_OVERDUE, too_vastutaja=other_specialist.pk) == {
        "Hilinenud tegevusega"
    }
    assert rows(department_head, too=wi.WORK_OVERDUE, too_vastutaja=specialist.pk) == {
        "Hilinenud tähtajaga"
    }
    assert rows(department_head, too=wi.WORK_OVERDUE, vastutaja=specialist.pk) == {
        "Hilinenud tegevusega",
        "Hilinenud tähtajaga",
    }


def test_work_nobody_carries_is_its_own_answer(late_world, department_head, today):
    """``puudub`` is the same word every other dimension uses for "empty"."""
    orphan = create_matter(title="Vastutajata hilinenud", owner=None, reference_year=2026)
    set_next_action(
        matter=orphan,
        text="Keegi peab midagi tegema",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today - timedelta(days=1),
    )

    assert rows(department_head, too=wi.WORK_OVERDUE, too_vastutaja="puudub") == {
        "Vastutajata hilinenud"
    }


def test_an_unowned_matter_belongs_to_nobodys_intervention_list(
    late_world, department_head, specialist, today
):
    """The one place the two undated halves of *Vajab sekkumist* could go wrong.

    An uninstructed Matter belongs to its owner and an unowned one belongs to
    nobody. Getting the second wrong would put every unassigned file into every
    colleague's count.
    """
    create_matter(title="Vastutajata ja tegevuseta", owner=None, reference_year=2026)
    create_matter(title="Tegevuseta", owner=specialist, reference_year=2026, actor=specialist)

    everybody = rows(department_head, too=wi.WORK_NEEDS_ATTENTION)
    mine = rows(department_head, too=wi.WORK_NEEDS_ATTENTION, too_vastutaja=specialist.pk)

    assert {"Vastutajata ja tegevuseta", "Tegevuseta"} <= everybody
    assert "Tegevuseta" in mine
    assert "Vastutajata ja tegevuseta" not in mine


@pytest.mark.parametrize("value", ["", "midagi-muud", "HILINENUD", "0"])
def test_an_unreadable_value_empties_the_list(late_world, department_head, value):
    """A chip nobody can trust above rows nobody asked for is worse than nothing.

    The empty string is the exception the pipeline already makes everywhere: an
    absent parameter is not a filter.
    """
    listed = rows(department_head, too=value)
    assert listed == (rows(department_head) if value == "" else set())


def test_an_unparseable_person_empties_the_list(late_world, department_head):
    """A hand-edited URL must not reach the database as a malformed UUID."""
    assert rows(department_head, too=wi.WORK_OVERDUE, too_vastutaja="mitte-uuid") == set()


def test_a_restricted_matter_reaches_neither_half(late_world, specialist, other_specialist, today):
    """Both the count and the list come from ``visible_to``, so neither may leak."""
    from app.core.enums import Visibility

    hidden = create_matter(
        title="Piiratud hilinenud",
        owner=specialist,
        visibility=Visibility.RESTRICTED,
        reference_year=2026,
        actor=specialist,
    )
    set_next_action(
        matter=hidden,
        text="Konfidentsiaalne hilinenud samm",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today - timedelta(days=3),
        actor=specialist,
    )

    assert "Piiratud hilinenud" in rows(specialist, too=wi.WORK_OVERDUE)
    assert "Piiratud hilinenud" not in rows(other_specialist, too=wi.WORK_OVERDUE)


# ---------------------------------------------------------------------------
# `?suletud=` — the year a Matter was finished, not the year it belongs to
# ---------------------------------------------------------------------------


def test_the_closing_year_is_not_the_reporting_year(db, department_head, specialist, today):
    """A 2024 consultation closed in 2026 is one of 2026's completions."""
    from app.matters.models import Matter

    old_file = create_matter(title="Vana teema", owner=specialist, reference_year=2024)
    close_matter(matter=old_file, disposition=Disposition.COMPLETED, actor=specialist)
    long_ago = create_matter(title="Ammu suletud", owner=specialist, reference_year=2024)
    close_matter(matter=long_ago, disposition=Disposition.COMPLETED, actor=specialist)
    Matter.objects.filter(pk=long_ago.pk).update(closed_at=timezone.now() - timedelta(days=800))

    listed = {
        matter.title
        for matter in register_population(
            department_head, {"olek": "suletud", "suletud": str(today.year)}
        )
    }
    assert listed == {"Vana teema"}


def test_a_closing_year_that_is_not_a_year_empties_the_list(db, department_head):
    assert not register_population(
        department_head, {"olek": "suletud", "suletud": "kahetuhat"}
    ).exists()


# ---------------------------------------------------------------------------
# The control beside the link
# ---------------------------------------------------------------------------


def test_the_panel_can_set_the_dimension_a_figure_opens(late_world, client, department_head):
    """A filter a figure can apply and the panel cannot is one somebody can
    arrive at and never reproduce — or narrow further without editing the URL.
    """
    client.force_login(department_head)
    body = client.get("/teemad/").content.decode()

    assert 'name="too"' in body
    for value in wi.WORK_POPULATIONS:
        assert f'value="{value}"' in body


def test_the_chip_names_the_population_in_words(late_world, client, department_head):
    client.force_login(department_head)
    response = client.get(f"/teemad/?olek=avatud&liik=FULL&too={wi.WORK_OVERDUE}")
    chips = {chip["name"]: chip["value"] for chip in response.context["active_filters"]}

    assert chips["too"] == wi.WORK_POPULATION_LABELS[wi.WORK_OVERDUE]
    assert response.context["has_any_filter"] is True


def test_a_person_filter_with_no_population_is_not_advertised_as_one(
    late_world, client, department_head, specialist
):
    """``?too_vastutaja=`` narrows ``?too=`` and does nothing on its own.

    A chip for a parameter that changed no rows tells the reader the list is
    narrower than it is.
    """
    client.force_login(department_head)
    response = client.get(f"/teemad/?too_vastutaja={specialist.pk}")
    names = {chip["name"] for chip in response.context["active_filters"]}

    assert "too_vastutaja" not in names
    assert response.context["has_any_filter"] is False


def test_clearing_returns_the_whole_register(late_world, client, department_head):
    client.force_login(department_head)
    filtered = client.get(f"/teemad/?olek=avatud&liik=FULL&too={wi.WORK_OVERDUE}")
    cleared = client.get(f"/teemad/?{filtered.context['cleared_query']}")

    assert cleared.context["total"] > filtered.context["total"]
    assert cleared.context["has_any_filter"] is False
