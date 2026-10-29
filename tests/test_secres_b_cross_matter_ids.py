"""Assessor B (WS1, 2026-10-29): a child id reached through the wrong Matter's route.

Every route that names a Matter *and* one of its children in the path
(`/teemad/<pk>/kaasamine/<engagement_id>/…`, `/teemad/<matter_id>/toovoidud/<pk>/…`,
`/teemad/<pk>/kirje/<kind>/<record_id>/…`) is fired with a **real child of
Matter A under Matter B's address**, as the owner who may read both. Since
docs/adr/0181 every business user reads every Matter, so this is not a
confidentiality question; it is an integrity one: a lookup that forgets
`matter=matter` would let a crafted POST complete, move, correct or remove a
record of one file while the lock, the audit row and the redirect belong to
another.

Expected: the same quiet 404 the route gives for an unknown id (405 for a verb
the route does not take), and no row changed. Existing coverage
(`tests/test_idor_sweep_restricted_world.py`) proves the matching pair reaches
its record and that outsiders get nothing; it never crosses the pair.

Status: boundary verified sound (every case passes on main 359e34bf).
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client

from tests import factories
from tests.test_idor_sweep_restricted_world import (  # noqa: F401 - fixtures re-exported
    Candidate,
    _body,
    _call,
    _fingerprint,
    _signed_in,
    _sweep_list,
    hidden,
    world,
)

pytestmark = pytest.mark.django_db

#: Path parameters that are not ids of a child record.
NOT_A_CHILD = {"kind", "field", "key"}


def _matter_param(candidate: Candidate) -> str | None:
    if "matter_id" in candidate.kwargs:
        return "matter_id"
    namespace = candidate.route.split(":")[0]
    if namespace in ("matters", "related_materials") and "pk" in candidate.kwargs:
        return "pk"
    return None


def _crossed(hidden_worlds: dict[str, dict[str, Any]]) -> list[tuple[str, Candidate, dict]]:
    """Each two-id candidate of one world, with the *other* world's Matter in the path."""
    names = list(hidden_worlds)
    assert len(names) == 2
    out = []
    for world_name, candidate in _sweep_list(hidden_worlds):
        matter_param = _matter_param(candidate)
        if matter_param is None:
            continue
        children = [k for k in candidate.kwargs if k != matter_param and k not in NOT_A_CHILD]
        if not children:
            continue
        other = next(n for n in names if n != world_name)
        crossed = dict(candidate.kwargs)
        crossed[matter_param] = str(hidden_worlds[other]["matter"].pk)
        assert crossed[matter_param] != candidate.kwargs[matter_param]
        out.append((world_name, candidate, crossed))
    return out


def test_there_are_crossed_cases_to_run(world, hidden):  # noqa: F811
    assert len(_crossed(hidden)) >= 60


def test_a_child_of_one_matter_is_not_reached_through_another_matters_route(world, hidden):  # noqa: F811
    # A DEPARTMENT_HEAD: a business writer who also passes the class-B
    # victory-review check (docs/adr/0037), so every route reaches its lookup
    # rather than its role refusal. The owner (a SPECIALIST) is answered the
    # designed 403 by `confirm_work_victory`/`reject_work_victory` before any id
    # is read, which says nothing about the crossing.
    owner: Client = _signed_in(factories.DepartmentHeadFactory())
    problems = []
    for world_name, candidate, crossed in _crossed(hidden):
        for method in ("get", "post"):
            before = _fingerprint() if method == "post" else None
            response = _call(owner, method, candidate, crossed)
            where = (world_name, method, candidate.route, crossed)
            if method == "post" and response.status_code != 404:
                problems.append((*where, response.status_code, "POST not a quiet 404"))
            if method == "get" and response.status_code not in (404, 405):
                problems.append((*where, response.status_code, "GET not a quiet refusal"))
            if response.status_code >= 500:
                problems.append((*where, response.status_code, "5xx"))
            if before is not None and _fingerprint() != before:
                problems.append((*where, response.status_code, "wrote"))
    assert problems == [], "\n".join(map(str, problems))
