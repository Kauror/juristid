"""Resolving who somebody is must not cost a query per read.

PERF-01. `scope_for_user` runs on every `visible_to` — 109 call sites — and each
run asked the database whether this person holds a break-glass grant. On the
fifteen pages measured that was **326 of 792 queries, 41%**, and on
`/statistika/andmekvaliteet/` it was 90 of 174. Every one of those asked the same
question about the same person inside one request and got the same answer.

Two changes, and the second only because the first does not reach far enough.

**The lookup is not made when the role already answers.** `sees_all_restricted`
short-circuits on `sees_restricted_by_role`, so for a SPECIALIST or a
DEPARTMENT_HEAD the grant could not change anything and the round trip bought
nothing. That removes the query entirely for the two roles that do the
department's work.

**The lookup is remembered for the rest of the request.** It has to be, because
a READER is a real role with a real job and the first change does nothing for
one: a reader still holds a grant sometimes, so the question is genuinely worth
asking — once, not ninety times.

What this file protects is not the numbers. It is the two properties that make
remembering an answer safe at the authorization chokepoint: the memo cannot
outlive its request, and the two callers with no primary key cannot enter it.

Why the key is the thing to watch
---------------------------------
`DepartmentViewer.pk` and `AnonymousUser.pk` are **both** `None`, and they map to
opposite scopes — the shared-gate sentinel sees every NORMAL Matter, an
anonymous visitor sees nothing at all. A memo keyed on `pk` that either could
enter would eventually answer an anonymous request with a shared-gate reader's
scope, which is a fail-open at the one place this codebase has spent the most
care keeping fail-closed.

They cannot enter it, because `scope_for_user` returns for both of them before
the lookup is reached. That is a property of the ordering rather than of the
key, so it is asserted here rather than assumed.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.accounts.services import grant_break_glass, revoke_break_glass
from app.core import authorization
from app.core.authorization import (
    DEPARTMENT_VIEWER,
    active_break_glass_grant_id,
    remember_grants_for_one_request,
    scope_for_user,
)
from app.matters.models import Matter

pytestmark = pytest.mark.django_db


def break_glass_queries(captured: CaptureQueriesContext) -> int:
    return sum(1 for query in captured.captured_queries if "breakglass" in query["sql"])


# ---------------------------------------------------------------------------
# The memo cannot outlive its request
# ---------------------------------------------------------------------------


def test_nothing_is_remembered_outside_a_request(reader):
    """The default is no memo at all, which is what commands and workers get.

    A cache that is absent by default cannot go stale in a process nobody
    wrapped — an extraction worker, a management command, a shell session.
    """
    assert authorization._looked_up_grants.get() is None

    with CaptureQueriesContext(connection) as captured:
        active_break_glass_grant_id(reader)
        active_break_glass_grant_id(reader)

    assert break_glass_queries(captured) == 2


def test_inside_one_request_the_same_person_is_asked_about_once(reader):
    with remember_grants_for_one_request(), CaptureQueriesContext(connection) as captured:
        for _ in range(5):
            active_break_glass_grant_id(reader)

    assert break_glass_queries(captured) == 1


def test_two_people_in_one_request_are_two_answers(reader, other_specialist):
    """Keyed per person, so one reader's answer is never another's."""
    with remember_grants_for_one_request(), CaptureQueriesContext(connection) as captured:
        active_break_glass_grant_id(reader)
        active_break_glass_grant_id(other_specialist)
        active_break_glass_grant_id(reader)

    assert break_glass_queries(captured) == 2


def test_the_memo_is_closed_when_the_request_ends(reader):
    with remember_grants_for_one_request():
        active_break_glass_grant_id(reader)
        assert authorization._looked_up_grants.get() is not None

    assert authorization._looked_up_grants.get() is None


def test_the_memo_is_closed_even_when_the_view_raises(reader):
    """The `finally`, which is the whole safety argument for the middleware."""
    with pytest.raises(RuntimeError), remember_grants_for_one_request():
        active_break_glass_grant_id(reader)
        raise RuntimeError("the view exploded")

    assert authorization._looked_up_grants.get() is None


def test_one_requests_answer_does_not_reach_the_next(reader, department_head, restricted_matter):
    """A grant created between two requests takes effect on the second.

    The memo is bounded to a request precisely so that this stays true. If it
    outlived one, a grant would appear not to work until the worker thread
    happened to be recycled.
    """
    with remember_grants_for_one_request():
        assert restricted_matter not in Matter.objects.visible_to(reader)

    grant_break_glass(
        user=reader,
        granted_by=department_head,
        reason="Tugijuhtum",
        duration=timedelta(hours=1),
    )

    with remember_grants_for_one_request():
        assert restricted_matter in Matter.objects.visible_to(reader)


def test_a_revoked_grant_stops_working_on_the_next_request(
    reader, department_head, restricted_matter
):
    grant = grant_break_glass(
        user=reader,
        granted_by=department_head,
        reason="Tugijuhtum",
        duration=timedelta(hours=1),
    )
    with remember_grants_for_one_request():
        assert restricted_matter in Matter.objects.visible_to(reader)

    revoke_break_glass(grant=grant, revoked_by=department_head)

    with remember_grants_for_one_request():
        assert restricted_matter not in Matter.objects.visible_to(reader)


# ---------------------------------------------------------------------------
# The two callers with no primary key never reach the memo
# ---------------------------------------------------------------------------


def test_the_shared_gate_sentinel_never_reaches_the_lookup(monkeypatch):
    """`DepartmentViewer.pk` is None, and so is `AnonymousUser.pk`.

    They map to opposite scopes, so a `pk`-keyed memo either could enter would
    be a fail-open. `scope_for_user` answers both before the lookup, and this
    asserts that ordering rather than trusting it.
    """
    asked: list[object] = []
    monkeypatch.setattr(
        authorization,
        "active_break_glass_grant_id",
        lambda user: asked.append(user),
    )

    with remember_grants_for_one_request():
        scope_for_user(DEPARTMENT_VIEWER)
        scope_for_user(None)
        # Checked here rather than after the block, where the memo is already
        # closed and would read `None` whatever happened inside it.
        assert authorization._looked_up_grants.get() == {}

    assert asked == []


def test_an_anonymous_reader_after_a_shared_gate_reader_still_sees_nothing(
    normal_matter, monkeypatch
):
    """The fail-open this design exists to make unreachable.

    Both identities key to `None`. If they shared a memo, the sentinel's NORMAL
    scope would be served to the anonymous request that followed it — inside one
    request here, which is stricter than the worker thread that would do it in
    production.
    """
    with remember_grants_for_one_request():
        assert normal_matter in Matter.objects.visible_to(DEPARTMENT_VIEWER)
        assert Matter.objects.visible_to(None).count() == 0
        assert normal_matter in Matter.objects.visible_to(DEPARTMENT_VIEWER)


def test_a_deactivated_account_is_not_remembered_as_anything(specialist):
    specialist.is_active = False
    specialist.save(update_fields=["is_active", "updated_at"])

    with remember_grants_for_one_request():
        scope = scope_for_user(specialist)

    assert scope.is_authenticated is False
    assert scope.break_glass_grant_id is None


# ---------------------------------------------------------------------------
# The role that already sees restricted content is not asked at all
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["specialist", "department_head"])
def test_a_lawyer_is_never_asked_about_a_grant(request, who):
    person = request.getfixturevalue(who)

    with CaptureQueriesContext(connection) as captured:
        scope = scope_for_user(person)

    assert scope.sees_all_restricted is True
    assert break_glass_queries(captured) == 0


def test_a_lawyers_scope_relies_on_no_grant_even_when_one_exists(
    specialist, department_head, restricted_matter
):
    """The field records the grant a scope *relies on*, and a lawyer relies on
    none — the role already answers. What must not change is what they see."""
    grant_break_glass(
        user=specialist,
        granted_by=department_head,
        reason="Tugijuhtum",
        duration=timedelta(hours=1),
    )
    scope = scope_for_user(specialist)

    assert scope.break_glass_grant_id is None
    assert scope.sees_restricted_by_role is True
    assert scope.sees_all_restricted is True
    assert restricted_matter in Matter.objects.visible_to(specialist)


def test_a_reader_is_still_asked_because_the_answer_can_change_what_they_see(
    reader, department_head, restricted_matter
):
    with CaptureQueriesContext(connection) as captured:
        assert scope_for_user(reader).sees_all_restricted is False
    assert break_glass_queries(captured) == 1

    grant = grant_break_glass(
        user=reader,
        granted_by=department_head,
        reason="Tugijuhtum",
        duration=timedelta(hours=1),
    )
    assert scope_for_user(reader).break_glass_grant_id == grant.id
    assert restricted_matter in Matter.objects.visible_to(reader)


def test_an_expired_grant_is_remembered_as_absent_not_as_present(
    reader, department_head, restricted_matter
):
    grant = grant_break_glass(
        user=reader,
        granted_by=department_head,
        reason="Tugijuhtum",
        duration=timedelta(hours=1),
    )
    grant.starts_at = timezone.now() - timedelta(hours=2)
    grant.expires_at = timezone.now() - timedelta(minutes=1)
    grant.save(update_fields=["starts_at", "expires_at", "updated_at"])

    with remember_grants_for_one_request():
        assert restricted_matter not in Matter.objects.visible_to(reader)
        assert restricted_matter not in Matter.objects.visible_to(reader)


# ---------------------------------------------------------------------------
# Budgets, so the cost cannot come back unnoticed
# ---------------------------------------------------------------------------

#: Deliberately loose enough that an ORM upgrade or an extra column does not go
#: red, and tight enough that the per-read lookup returning would. The measured
#: figures when these were written, on the seeded world below:
#:
#:     /osakond/                     head 52, reader 38   (was 94 / 65)
#:     /statistika/andmekvaliteet/   head 84, reader 85   (was 174 / 174)
#: Absolute ceilings, measured against `measured_world` and left roughly a fifth
#: of headroom so ordinary work does not trip them. They were 70 and 105 before
#: Wave 6 shared the department's work-model read and the reporting pages'
#: denominators; the measured costs at the time of writing are 40 / 23 / 52 /
#: 53 / 58. A ceiling that no longer bites is a ceiling that stopped saying
#: anything, so these are lowered whenever a round makes a page cheaper.
PAGE_BUDGETS = [
    ("/osakond/", 48),
    ("/osakond/?vaade=valdkonniti", 30),
    ("/statistika/andmekvaliteet/", 62),
    ("/statistika/ajalooline/", 63),
    ("/statistika/teemad/", 68),
]


@pytest.fixture
def measured_world(world):
    from tests import factories

    people = [factories.UserFactory(display_name=f"Mõõt {index}") for index in range(4)]
    for index in range(25):
        factories.MatterFactory(
            owner=people[index % len(people)],
            title=f"Mõõteteema {index}",
            reference_year=2026,
            reference_number=500 + index,
        )
    return world


@pytest.mark.parametrize(("path", "budget"), PAGE_BUDGETS)
@pytest.mark.parametrize("who", ["department_head", "reader"])
def test_a_page_stays_inside_its_query_budget(request, client, measured_world, path, budget, who):
    client.force_login(request.getfixturevalue(who))

    with CaptureQueriesContext(connection) as captured:
        response = client.get(path)

    assert response.status_code == 200
    assert len(captured) <= budget, (
        f"{path} cost {len(captured)} queries for a {who}, over the {budget} budget"
    )


@pytest.mark.parametrize("who", ["department_head", "specialist", "reader"])
def test_a_page_asks_about_a_grant_at_most_once(request, client, measured_world, who):
    """The property behind the budget, stated directly so a failure says why."""
    client.force_login(request.getfixturevalue(who))

    with CaptureQueriesContext(connection) as captured:
        assert client.get("/statistika/andmekvaliteet/").status_code == 200

    assert break_glass_queries(captured) <= 1
