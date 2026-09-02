"""One rule about who may write, enforced in one place.

DUP-01. `may_write_business_content` is the rule and it was never in doubt. What
was in doubt is where it gets asked: `@business_write_required` asked it for
twenty-four routes, nine views in `app.matters.views` asked it by hand with nine
different Estonian refusal sentences, and `app.intelligence.views` asked it
through a module-local helper of its own for eight more. Three enforcements of
one rule, and the decorator's own docstring names the two properties the
hand-written ones broke:

* **one refusal sentence for every route**, because differing text "becomes a map
  of what the application can do";
* **the check runs before anything else the view does** — not after the object is
  fetched.

Seventeen routes moved onto the decorator. This file is what stops a
eighteenth arriving with its own copy.

The consequence that is worth reading before changing anything here
-------------------------------------------------------------------
Moving the gate in front of `get_visible_matter` has a second effect that no
review anticipated, and it is not a defect: **it makes the visibility rule
unobservable through those routes.**

The reason is structural. Every role that may write also sees RESTRICTED
content — `ROLES_WITH_BUSINESS_WRITE` is a subset of
`ROLES_WITH_RESTRICTED_ACCESS` since docs/adr/0042 — so there is no actor who
may write but may not see. An actor who fails the visibility check therefore
fails the write gate too, and now fails it *first*.

Three tests used to demonstrate visibility through these routes with a READER or
an ADMINISTRATOR. They still pass, and they still assert something true, but the
404 they receive now comes from the write gate. Rather than leave them quietly
proving less than their names claim, each says so, and the invariant they were
reaching for is asserted where it remains observable —
`tests/test_authorization.py`, at the selector.

`test_no_actor_may_write_without_being_able_to_see` below is the guard that
makes this reversible. The day somebody adds a writing role that cannot read
RESTRICTED content, it fails — and whoever sees it fail is told, in the failure
message, that route-level visibility coverage has to come back.
"""

from __future__ import annotations

import inspect
import pathlib
import re

import pytest
from django.urls import get_resolver, reverse

from app.core.authorization import (
    ROLES_WITH_BUSINESS_WRITE,
    ROLES_WITH_RESTRICTED_ACCESS,
    acting_role,
)
from app.core.decorators import WRITE_REFUSED
from tests import factories

pytestmark = pytest.mark.django_db

APP_ROOT = pathlib.Path(__file__).resolve().parent.parent / "app"


# ---------------------------------------------------------------------------
# The structural fact behind the whole wave
# ---------------------------------------------------------------------------


def test_no_actor_may_write_without_being_able_to_see():
    """Why the write gate may run before the visibility lookup at all.

    If these two sets ever come apart — a role that may write but may not read
    RESTRICTED content — then ordering the write gate first would start hiding a
    visibility failure behind a write failure for a *real* actor, and three
    tests that currently pass for the right reason would begin passing for the
    wrong one.
    """
    assert ROLES_WITH_BUSINESS_WRITE <= ROLES_WITH_RESTRICTED_ACCESS, (
        "a role may now write without being able to see RESTRICTED content. "
        "The write gate runs before `get_visible_matter` on seventeen routes, so "
        "that actor's visibility failure is now hidden behind a write failure. "
        "Restore route-level visibility coverage before shipping this."
    )


# ---------------------------------------------------------------------------
# No eighteenth copy
# ---------------------------------------------------------------------------


def test_the_rule_is_asked_in_exactly_one_place():
    """`may_write_business_content` is a gate only inside the decorator.

    Reading it to decide what to *render* is fine and several views do — a
    hidden button is a courtesy, not a control. What may not come back is a view
    raising its own refusal, because that is how nine different sentences
    happened.
    """
    offenders = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        if "migrations" in path.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if re.search(r"if not may_write_business_content", line):
                relative = path.relative_to(APP_ROOT.parent).as_posix()
                if relative != "app/core/decorators.py":
                    offenders.append(f"{relative}:{number}")

    assert offenders == [], (
        f"these ask the write rule by hand instead of using @business_write_required: {offenders}"
    )


def test_no_route_carries_its_own_refusal_sentence():
    """One sentence, everywhere. The decorator holds it; nobody else writes one."""
    phrase = "sisu muutmise"
    offenders = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        if "migrations" in path.parts:
            continue
        relative = path.relative_to(APP_ROOT.parent).as_posix()
        if relative == "app/core/decorators.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if phrase in line and "Http404" in line:
                offenders.append(f"{relative}:{number}")

    assert offenders == [], f"route-specific write refusals are back: {offenders}"
    assert "sisu muutmise" in WRITE_REFUSED


#: Every route this wave moved onto the decorator.
MOVED = [
    "matters:matter_create",
    "matters:compose",
    "matters:add_engagement",
    "matters:update_engagement",
    "matters:matter_edit",
    "matters:update_position",
    "matters:update_summary",
    "matters:add_working_document",
    "matters:close",
    "intelligence:add_important_date",
    "intelligence:edit_important_date",
    "intelligence:cancel_important_date",
    "intelligence:add_effective_date",
    "intelligence:edit_effective_date",
    "intelligence:cancel_effective_date",
    "intelligence:add_work_victory",
    "intelligence:edit_work_victory",
]


@pytest.mark.parametrize("name", MOVED)
def test_every_moved_route_is_wrapped_by_the_decorator(name):
    """Asserted on the view, not on a response, so a route that silently lost
    the decorator fails here rather than in whichever behaviour test noticed."""
    match = get_resolver().resolve(_any_url_for(name))
    source = inspect.getsource(inspect.unwrap(match.func))
    assert "business_write_required" in source, f"{name} lost its write gate"


def _any_url_for(name: str) -> str:
    """A URL for a route, filling in ids where the pattern needs them."""
    import uuid

    for kwargs in (
        {},
        {"pk": uuid.uuid4()},
        {"matter_id": uuid.uuid4()},
        {"matter_id": uuid.uuid4(), "pk": uuid.uuid4()},
        {"pk": uuid.uuid4(), "engagement_id": uuid.uuid4()},
    ):
        try:
            return reverse(name, kwargs=kwargs)
        except Exception:  # noqa: S112 - trying the next shape is the point
            continue
    raise AssertionError(f"could not build a URL for {name}")


# ---------------------------------------------------------------------------
# What a non-writer learns, which is nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verb", ["put", "patch", "delete"])
def test_a_non_writer_gets_the_same_answer_whatever_verb_they_try(client, verb):
    """405 became 404 for a non-writer, and that is the decorator's purpose.

    `@business_write_required` is composed outside `@require_http_methods`
    precisely so a refused caller cannot tell a real endpoint from an absent one
    by changing the verb. A writer still gets 405, so the method contract itself
    is unchanged for everybody entitled to use it.
    """
    matter = factories.MatterFactory()
    url = reverse("matters:close", kwargs={"pk": matter.pk})

    client.force_login(factories.ReaderFactory())
    assert getattr(client, verb)(url).status_code == 404

    client.force_login(factories.UserFactory())
    assert getattr(client, verb)(url).status_code == 405


def test_a_writer_still_writes(client, specialist):
    """The gate narrows nothing for the people it is not about."""
    client.force_login(specialist)
    matter = factories.MatterFactory(owner=specialist)

    response = client.post(
        reverse("matters:update_summary", kwargs={"pk": matter.pk}),
        {"brief_summary": "Sünteetiline kokkuvõte"},
    )

    assert response.status_code in (200, 302)
    matter.refresh_from_db()
    assert matter.brief_summary == "Sünteetiline kokkuvõte"


# ---------------------------------------------------------------------------
# DUP-03 / DEAD-03 — one answer to "which role is acting"
# ---------------------------------------------------------------------------


def test_the_archive_asks_the_canonical_acting_role():
    """`opinion_access` had its own copy, and the copy failed *open*.

    Both answered "" for the shared-gate sentinel, an anonymous visitor and a
    deactivated account — but on `is_active` the copy defaulted to ``True``
    where the canonical helper, and every other caller in the codebase,
    defaults to ``False``. No object reachable today lacks the attribute, so
    this was latent rather than live; it is also exactly the kind of divergence
    that stops being latent without anybody deciding it should.
    """
    from app.legacy_import import opinion_access

    assert "_acting_role" not in vars(opinion_access)
    assert opinion_access.acting_role is acting_role


class _NoActiveFlag:
    """An authenticated object that never learned about `is_active`."""

    is_authenticated = True
    pk = None
    role = "SPECIALIST"


def test_an_object_without_is_active_is_refused_rather_than_trusted():
    """The fail-closed default, which is the half the copy had backwards."""
    assert acting_role(_NoActiveFlag()) == ""


def test_the_dead_shared_gate_alias_is_gone():
    from app.legacy_import import opinion_access

    assert not hasattr(opinion_access, "SHARED_GATE_ARCHIVE_READERS")
    assert opinion_access.ARCHIVE_READERS


# ---------------------------------------------------------------------------
# DUP-05 — and the two sites deliberately left alone
# ---------------------------------------------------------------------------


def test_the_opinion_queue_link_is_offered_by_the_predicate_that_guards_it(
    reporting_context, administrator, reader
):
    """Offer and serve move together, or the reader gets a 403 button."""
    from app.reporting.selectors import opinions

    assert opinions._queue_url(reporting_context(administrator)) != ""
    assert opinions._queue_url(reporting_context(reader)) == ""


def test_the_historical_review_queue_keeps_its_own_rule():
    """Deliberately NOT folded into `may_use_opinion_queue`.

    Two queues, two consequences: this one's decisions create Matters, the
    opinion queue's may become Submissions. They already refuse differently on
    purpose — 404 here, 403 there — which is evidence they were argued
    separately rather than being one rule written twice. Binding the historical
    queue to a predicate named for the opinion queue would mean a future
    widening of the opinion archive silently widening Matter creation.
    """
    from app.legacy_import import historical_views
    from app.reporting.selectors import quality

    assert "may_use_opinion_queue" not in inspect.getsource(historical_views._require_administrator)
    assert "may_use_opinion_queue" not in inspect.getsource(quality.can_open_review_queue)
