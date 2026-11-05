"""No continuation helper tells a viewer about the history of a Matter it cannot read.

QA-06 of the independent continuation audit (2026-11-05). Written from the
rule, independently of the implementation (agent A owns
`app/matters/continuations.py`):

    For a Matter the viewer may not read — an administrator without a grant,
    an anonymous visitor, a shared-gate session with no persona, or a deleted
    Matter for anybody — every continuation helper answers *nothing*: no
    predecessor, no successor, no hidden-stage flag or count, no earlier stage.
    A malformed or unknown id answers nothing and never errors.

The surfaces: `continuations_for`, `historical_predecessors`, the exclusion
ids `continuation_component_ids`, the selector `related_materials_for`, MCP
`get_matter` (and its continuation block), and the HTTP routes.

Some cases fail on 9eff0e9a, where the invariant is missing (they were
``xfail(strict=True)`` until agent A's change landed at integration); the rest
already held there and guard against regressions.
Synthetic data only.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.core.authorization import DEPARTMENT_VIEWER
from app.matters.continuations import (
    Continuations,
    continuation_component_ids,
    continuations_for,
    historical_predecessors,
)
from app.matters.models import Matter
from app.mcp_server import readers
from app.related_materials.selectors import related_materials_for
from app.workflow.enums import Disposition
from tests import factories
from tests.gate import PASSWORD, apply_shared_gate

pytestmark = pytest.mark.django_db

HX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture(autouse=True)
def public_origin(settings):
    settings.JURISTID_PUBLIC_ORIGIN = "https://juristid.example.test"


def _continues(earlier: Matter, later: Matter) -> None:
    Matter.all_objects.filter(pk=earlier.pk).update(
        superseded_by=later,
        is_open=False,
        closed_at=timezone.now(),
        disposition=Disposition.SUPERSEDED,
    )


def _fresh(matter: Matter) -> Matter:
    return Matter.all_objects.get(pk=matter.pk)


@pytest.fixture
def history(specialist) -> dict[str, Matter]:
    """first -> middle -> viewed -> later;  side -> viewed (converging)."""
    m = {
        name: factories.MatterFactory(owner=specialist, title=f"PA-C INV {name.upper()}")
        for name in ("first", "middle", "side", "viewed", "later")
    }
    _continues(m["first"], m["middle"])
    _continues(m["middle"], m["viewed"])
    _continues(m["side"], m["viewed"])
    _continues(m["viewed"], m["later"])
    return {name: _fresh(matter) for name, matter in m.items()}


@pytest.fixture
def deleted_root(history) -> Matter:
    """The viewed Matter, deleted; its stages stay live and readable."""
    Matter.all_objects.filter(pk=history["viewed"].pk).update(deleted_at=timezone.now())
    return _fresh(history["viewed"])


#: The three viewers ADR 0181 lets read nothing.
OUTSIDERS = ["administrator", "anonymous", "personaless"]


@pytest.fixture
def outsider(request) -> Any:
    kind = request.param
    if kind == "administrator":
        return factories.AdministratorFactory()
    if kind == "anonymous":
        return AnonymousUser()
    return DEPARTMENT_VIEWER


def _says_nothing(chain: Continuations) -> None:
    assert not chain, chain
    assert chain.predecessors == () and chain.after == () and chain.before == ()
    assert chain.successor is None
    assert not chain.hidden_predecessors
    assert not chain.hidden_after and not chain.hidden_before


def _mcp_says_nothing(block: dict[str, Any], viewed: Matter) -> None:
    assert block["successor"] is None and not block["successor_hidden"]
    assert block["predecessors"] == [] and not block["hidden_predecessors"]
    assert not block["chain_continues_hidden"] and not block["chain_starts_hidden"]
    assert [item["id"] for item in block["chain"]] in ([], [str(viewed.pk)])
    assert block["historical_predecessors"] == []
    assert block["endpoint"] is None


# -- an unreadable viewed Matter: the three outsiders -----------------------------------


@pytest.mark.parametrize("outsider", OUTSIDERS, indirect=True)
def test_continuations_for_says_nothing_to_an_outsider(history, outsider):
    _says_nothing(continuations_for(history["viewed"], outsider))


@pytest.mark.parametrize("outsider", OUTSIDERS, indirect=True)
def test_historical_predecessors_lists_nothing_for_an_outsider(history, outsider):
    earlier = historical_predecessors(history["viewed"], outsider)
    assert earlier.stages == () and not earlier.truncated and not earlier.cyclic


@pytest.mark.parametrize("outsider", OUTSIDERS, indirect=True)
def test_the_related_materials_selector_lists_nothing_for_an_outsider(history, outsider):
    related = related_materials_for(history["viewed"], outsider)
    assert related.is_empty and related.count == 0
    assert not related.stages_truncated and not related.stages_cyclic


@pytest.mark.parametrize("outsider", OUTSIDERS, indirect=True)
def test_the_mcp_continuation_block_says_nothing_to_an_outsider(history, outsider):
    """The block `get_matter` embeds, asked directly: the door in front of it is
    `resolve_matter`, and the block must not depend on that door alone."""
    _mcp_says_nothing(readers._continuations(outsider, history["viewed"]), history["viewed"])


@pytest.mark.parametrize("outsider", OUTSIDERS, indirect=True)
def test_mcp_get_matter_refuses_an_outsider_like_an_unknown_matter(history, outsider):
    for ask in (history["viewed"].display_reference, str(history["viewed"].pk)):
        with pytest.raises(readers.NotFound, match=r"No such record."):
            readers.matter_snapshot(outsider, ask)


def test_the_http_routes_name_no_stage_to_an_ungranted_administrator(client, history):
    client.force_login(factories.AdministratorFactory())
    viewed = history["viewed"]
    for response in (
        client.get(reverse("matters:matter_detail", kwargs={"pk": viewed.pk})),
        client.get(reverse("related_materials:section", kwargs={"pk": viewed.pk})),
        client.get(reverse("related_materials:section", kwargs={"pk": viewed.pk}), **HX),
        client.get(reverse("matters:matter_documents", kwargs={"pk": viewed.pk})),
    ):
        assert response.status_code == 404
        body = response.content.decode()
        for stage in history.values():
            if stage.pk != viewed.pk:
                assert stage.display_reference not in body and stage.title not in body
                assert str(stage.pk) not in body


def test_the_http_routes_name_no_stage_to_anonymous_or_a_personaless_gate(settings, history):
    viewed = history["viewed"]
    urls = [
        reverse("matters:matter_detail", kwargs={"pk": viewed.pk}),
        reverse("related_materials:section", kwargs={"pk": viewed.pk}),
    ]
    anonymous = Client()
    apply_shared_gate(settings)
    gate = Client()
    assert gate.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302
    for client in (anonymous, gate):
        for url in urls:
            for extra in ({}, HX):
                response = client.get(url, **extra)
                assert response.status_code in (302, 401), (url, response.status_code)
                body = response.content.decode()
                for stage in history.values():
                    assert stage.display_reference not in body and stage.title not in body


# -- a deleted viewed Matter: unreadable to everybody ----------------------------------


def test_continuations_for_says_nothing_about_a_deleted_root(deleted_root, specialist):
    _says_nothing(continuations_for(deleted_root, specialist))


def test_historical_predecessors_lists_nothing_behind_a_deleted_root(deleted_root, specialist):
    assert historical_predecessors(deleted_root, specialist).stages == ()


def test_the_selector_lists_no_stage_behind_a_deleted_root(deleted_root, specialist):
    assert related_materials_for(deleted_root, specialist).stages == ()


def test_the_mcp_continuation_block_says_nothing_about_a_deleted_root(deleted_root, specialist):
    _mcp_says_nothing(readers._continuations(specialist, deleted_root), deleted_root)


def test_the_exclusion_ids_of_a_deleted_root_are_empty(deleted_root, history):
    """Unscoped by design (exclusion only), so it must not even name the live
    stages around a Matter that is gone."""
    assert continuation_component_ids(deleted_root.pk) == set()


def test_mcp_and_http_treat_a_deleted_root_as_unknown(client, deleted_root, specialist):
    for ask in (deleted_root.display_reference, str(deleted_root.pk)):
        with pytest.raises(readers.NotFound, match=r"No such record."):
            readers.matter_snapshot(specialist, ask)
    client.force_login(specialist)
    for url in (
        reverse("matters:matter_detail", kwargs={"pk": deleted_root.pk}),
        reverse("related_materials:section", kwargs={"pk": deleted_root.pk}),
    ):
        response = client.get(url, **HX)
        assert response.status_code == 404
        assert "PA-C INV" not in response.content.decode()


# -- malformed and unknown ids -----------------------------------------------------------


def test_helpers_answer_nothing_for_a_matter_that_does_not_exist(history, specialist):
    ghost = Matter(pk=uuid.uuid4())
    _says_nothing(continuations_for(ghost, specialist))
    _says_nothing(continuations_for(None, specialist))
    assert historical_predecessors(ghost, specialist).stages == ()
    assert historical_predecessors(None, specialist).stages == ()
    assert continuation_component_ids(uuid.uuid4()) == set()
    assert related_materials_for(ghost, specialist).is_empty


@pytest.mark.parametrize(
    "ask", ["not-a-uuid", "matter:not-a-uuid", "0000_0", "²_1", str(uuid.UUID(int=0)), ""]
)
def test_mcp_get_matter_of_a_malformed_id_is_not_found(history, specialist, ask):
    with pytest.raises(readers.ReaderError):
        readers.matter_snapshot(specialist, ask)


@pytest.mark.parametrize(
    "path",
    [
        "/teemad/not-a-uuid/",
        "/teemad/not-a-uuid/seotud/",
        f"/teemad/{uuid.UUID(int=0)}/",
        f"/teemad/{uuid.UUID(int=0)}/seotud/",
    ],
)
def test_the_http_routes_answer_a_malformed_or_unknown_id_with_404(signed_in, history, path):
    for extra in ({}, HX):
        response = signed_in.get(path, **extra)
        assert response.status_code == 404
        body = response.content.decode()
        assert "Traceback" not in body and "PA-C INV" not in body


# -- the readable case still answers in full (so the tests above mean something) ------


def test_a_business_reader_still_gets_the_whole_answer(history, specialist):
    chain = continuations_for(history["viewed"], specialist)
    assert {m.pk for m in chain.predecessors} == {history["middle"].pk, history["side"].pk}
    assert chain.successor == history["later"]
    earlier = historical_predecessors(history["viewed"], specialist)
    assert {s.matter.pk for s in earlier.stages} == {
        history["first"].pk,
        history["middle"].pk,
        history["side"].pk,
    }
    assert continuation_component_ids(history["viewed"].pk) == {m.pk for m in history.values()}
