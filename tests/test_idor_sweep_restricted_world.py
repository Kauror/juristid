"""Every route that takes an id, fed real hidden ids, as everybody who must learn nothing.

The resolver-wide sweep in `tests/qa_audit/test_qa_authz_routes.py` fills a
child id it has no object for with a random UUID, and its world holds nothing
RESTRICTED, so for the routes that act on a child record it could not fail
(audit 2026-10-17, JTEST-009). This sweep is built the other way round, after
the audit's own probe (`test_b10_idor_sweep.py`):

* **The routes come from the resolver**, not from a hand list. Every pattern
  with a converter is either filled from the world (`_candidates`) or named in
  `NOT_MATTER_DATA` with the reason; `test_every_id_route_is_swept_or_named`
  fails on a new route that is neither.
* **The ids are real, and hidden.** The world is the one
  `tests/test_read_side_existence_oracles.py` builds — a RESTRICTED Matter with
  every kind of child, and every kind of child RESTRICTED under a visible
  Matter — plus what those children do not cover: an opinion follow-up and its
  check, a suggested plan step, an assignment notice and a thumbnail. Every
  route gets the object of the kind it acts on; `remove_record` and
  `update_field` are swept once per kind and once per field.
* **The ids are proved real** (`test_the_sweep_ids_reach_their_records`): the
  owner, who may see all of it, gets no 404 on any of the requests. Without
  that half an id of the wrong kind would 404 for everybody and the sweep would
  pass for the wrong reason.

Each route is then called with GET and an empty POST as an ADMINISTRATOR and
the shared-gate visitor with no persona. (A READER — collaborating or not — was
swept as an outsider until docs/adr/0181 made it an authorized internal user;
`test_a_reader_reads_every_hidden_record_the_owner_reads` now holds that it
reads what the owner reads.) A request fails the sweep when:

* the response carries any hidden token, or a file route answers 200;
* it is a 5xx;
* a POST changed any row (`_fingerprint`);
* for a hidden id, the answer is not a refusal that says nothing about the
  record — 404, 405, or the gate visitor's redirect to the persona choice —
  *and* differs from the answer to the same request with a random id.

Single-threaded, no extra connections. The world is built once per persona.
"""

from __future__ import annotations

import contextlib
import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from django.apps import apps
from django.db import transaction
from django.db.models import Max
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from app.accounts.enums import UserRole
from app.core.enums import Visibility
from app.documents.derivatives import DerivativeKind, DerivativeStatus, DocumentDerivative
from app.documents.models import Document, DocumentVersion
from app.matters.models import MatterAssignmentNotice
from app.matters.removal import removable_kinds
from app.workflow.models import MatterPlanStep, NextAction, OpinionFollowUp
from tests import factories
from tests.follow_ups import mark_sent
from tests.gate import PASSWORD, apply_shared_gate
from tests.test_read_side_existence_oracles import (  # noqa: F401 - `world` is a fixture
    HIDDEN_ACTION,
    HIDDEN_ENGAGEMENT,
    HIDDEN_FILE,
    HIDDEN_LINK_HOST,
    HIDDEN_OVERVIEW_HOST,
    HIDDEN_PHRASE,
    HIDDEN_SMAILY_HOST,
    HIDDEN_TITLE,
    add_restricted_children,
    add_restricted_matter,
    world,
)

pytestmark = pytest.mark.django_db

#: Words that exist only on hidden records.
TOKENS = (
    HIDDEN_TITLE,
    HIDDEN_PHRASE,
    HIDDEN_ACTION,
    HIDDEN_ENGAGEMENT,
    HIDDEN_FILE,
    "salajane.pdf",
    HIDDEN_LINK_HOST,
    HIDDEN_OVERVIEW_HOST,
    HIDDEN_SMAILY_HOST,
)

FILE_ROUTES = {"documents:download", "documents:open", "documents:thumbnail"}

#: Routes with an id that is not a Matter's or a Matter's child's, and why the
#: sweep leaves them to their own tests.
NOT_MATTER_DATA = {
    # Accounts, 404 in every mode but local_password (tests/test_local_auth_*).
    "account_admin:detail": "accounts",
    "account_admin:send_link": "accounts",
    "account_admin:cancel_invitation": "accounts",
    "account_admin:deactivate": "accounts",
    "account_admin:reactivate": "accounts",
    "account_admin:reset_second_factor": "accounts",
    "account_admin:change_email": "accounts",
    # The opinion archive and the historical review: department-wide records
    # with no visibility of their own (tests/test_opinion_archive_*,
    # tests/test_historical_*).
    "legacy_import:opinion_archive_detail": "archive",
    "legacy_import:opinion_archive_file": "archive",
    "legacy_import:opinion_archive_link": "archive",
    "legacy_import:opinion_decide": "archive",
    "legacy_import:review_decide": "historical review",
    "legacy_import:source_page": "historical source",
    "legacy_import:source_xml": "historical source",
    # A person, not a Matter (tests/test_person_work*).
    "matters:person_work": "person",
    # A metric slug, not an id (tests/test_reporting_*).
    "reporting:export": "metric",
}

#: `<kind>` of `matters:remove_record` -> the world's key for a record of it.
REMOVABLE = {
    "sissekanne": "entry",
    "marge": "development",
    "kaasamine": "engagement",
    "seisukoht": "position",
    "ulevaade": "overview",
    "tahtaeg": "important_date",
    "joustumine": "effective_date",
    "toovoit": "work_victory",
}

#: A URL parameter -> the world's key for the object it names.
PARAMETER = {
    "matter_id": "matter",
    "entry_id": "entry",
    "engagement_id": "engagement",
    "position_id": "position",
    "development_id": "development",
    "link_id": "link",
    "overview_id": "overview",
    "submission_id": "submission",
    "step_id": "step",
    "notice_id": "notice",
    "follow_up_id": "follow_up",
}

#: Objects that carry no visibility of their own: they are hidden exactly when
#: their Matter is. A notice is the recipient's alone, so it is hidden from
#: everybody the sweep sends.
OWN_VISIBILITY = {
    "matter": False,
    "step": False,
    "notice": True,
}


def _routes() -> list[tuple[str, list[str]]]:
    found: list[tuple[str, list[str]]] = []

    def walk(patterns: Any, namespace: str | None) -> None:
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                walk(pattern.url_patterns, pattern.namespace or namespace)
            elif isinstance(pattern, URLPattern) and pattern.name:
                converters = getattr(pattern.pattern, "converters", {}) or {}
                if converters:
                    name = f"{namespace}:{pattern.name}" if namespace else pattern.name
                    found.append((name, sorted(converters)))

    walk(get_resolver().url_patterns, None)
    return [(name, params) for name, params in found if not name.startswith("admin:")]


#: Routes that name a second record in the POST body rather than the path.
#: The sweep sends one of the world's own, so the owner's half reaches the view
#: rather than stopping at "which record?".
BODY = {
    "related_materials:link": {"teema": "other_matter"},
    "related_materials:unlink": {"teema": "other_matter"},
    "related_materials:add_background": {"liik": "=arvamus", "kandidaat": "submission"},
    "related_materials:remove_background": {"liik": "=arvamus", "kandidaat": "submission"},
    "related_materials:dismiss": {"liik": "=teema", "kandidaat": "other_matter"},
    "related_materials:restore": {"liik": "=teema", "kandidaat": "other_matter"},
}


def _field_names() -> list[str]:
    from app.matters.views import FIELD_SERVICES

    return sorted(FIELD_SERVICES)


@dataclass(frozen=True)
class Candidate:
    route: str
    kwargs: dict[str, str]
    #: The parameters whose object the denied personas may not see.
    hidden: frozenset[str]
    #: What a POST carries.
    data: tuple[tuple[str, str], ...] = ()


def _key_for(route: str, param: str) -> str | None:
    """Which object of the world a route's parameter names."""
    if param in PARAMETER:
        return PARAMETER[param]
    if param == "action_id":
        follow_up_routes = ("matters:check_follow_up", "matters:reschedule_follow_up")
        return "check" if route in follow_up_routes else "action"
    if param != "pk":
        return None
    namespace, name = route.split(":")
    if namespace in ("matters", "related_materials"):
        return "matter"
    if namespace == "submissions":
        return "submission"
    if namespace == "intelligence":
        return name.split("_", 1)[1]
    if route in ("documents:download", "documents:open"):
        return "version"
    if route == "documents:thumbnail":
        return "thumbnail"
    if namespace == "documents":
        return "document"
    return None


def _candidates(route: str, params: list[str], objects: dict[str, Any]) -> list[Candidate]:
    expansions: list[dict[str, str]] = [{}]
    if "kind" in params:
        expansions = [{"kind": kind} for kind in REMOVABLE]
    if "field" in params:
        expansions = [{"field": field} for field in _field_names()]
    out = []
    for fixed in expansions:
        kwargs = dict(fixed)
        hidden = set()
        for param in params:
            if param in fixed:
                continue
            key = REMOVABLE[fixed["kind"]] if param == "record_id" else _key_for(route, param)
            if key is None:
                raise AssertionError(f"{route}: no object for <{param}>; name it or sweep it")
            kwargs[param] = str(objects[key].pk)
            if objects["restricted_matter"] or OWN_VISIBILITY.get(key, True):
                hidden.add(param)
        data = tuple(
            (field, value[1:] if value.startswith("=") else str(objects[value].pk))
            for field, value in BODY.get(route, {}).items()
        )
        out.append(Candidate(route, kwargs, frozenset(hidden), data))
    return out


def _sweep_list(worlds: dict[str, dict[str, Any]]) -> list[tuple[str, Candidate]]:
    swept = []
    for route, params in _routes():
        if route in NOT_MATTER_DATA:
            continue
        for name, objects in worlds.items():
            swept.extend((name, candidate) for candidate in _candidates(route, params, objects))
    return swept


# ---------------------------------------------------------------------------
# The hidden world
# ---------------------------------------------------------------------------


def _thumbnail(version: DocumentVersion) -> DocumentDerivative:
    """The version's preview, made here if extraction drew none on this machine."""
    from django.core.files.base import ContentFile

    from app.documents.extraction.orchestrator import derivative_storage

    existing = (
        DocumentDerivative.objects.filter(
            version=version, kind=DerivativeKind.THUMBNAIL, status=DerivativeStatus.ACTIVE
        )
        .exclude(storage_key="")
        .first()
    )
    if existing is not None:
        return existing
    key = derivative_storage().save(
        f"sweep/{uuid.uuid4().hex}.png", ContentFile(b"\x89PNG\r\n\x1a\nsweep")
    )
    return DocumentDerivative.objects.create(
        version=version,
        kind=DerivativeKind.THUMBNAIL,
        generator="sweep",
        generator_version="1",
        status=DerivativeStatus.ACTIVE,
        storage_key=key,
    )


def _objects(matter: Any, made: dict[str, Any], world: dict[str, Any], *, restrict: bool):  # noqa: F811
    """One object of every kind a route can name, all of them on ``matter``."""
    owner = world["owner"]
    sent = mark_sent(matter, owner, [world["hidden_org"]], title=HIDDEN_PHRASE)
    follow_up = OpinionFollowUp.objects.get(submission=sent)
    check = NextAction.objects.get(follow_up=follow_up)
    if restrict:
        restricted = Visibility.RESTRICTED.value
        # The file first: final evidence may never be less restricted than its
        # submission (the database refuses the other order).
        Document.objects.filter(pk=sent.final_version.document_id).update(
            visibility_override=restricted
        )
        type(sent).objects.filter(pk=sent.pk).update(visibility_override=restricted)
        NextAction.objects.filter(pk=check.pk).update(visibility_override=restricted)
    model = apps.get_model
    return {
        "restricted_matter": not restrict,
        "matter": matter,
        "other_matter": world["first"],
        "entry": model("matters", "Entry").objects.get(matter=matter, body__contains=HIDDEN_PHRASE),
        "engagement": model("matters", "MatterEngagement").objects.get(
            matter=matter, title__startswith=HIDDEN_ENGAGEMENT
        ),
        "position": made["position"],
        "development": made["development"],
        "link": made["link"],
        "overview": made["overview"],
        "submission": made["submission"],
        "document": made["document"],
        "version": made["version"],
        "thumbnail": _thumbnail(made["version"]),
        "action": NextAction.objects.get(matter=matter, text=HIDDEN_ACTION),
        "check": check,
        "follow_up": follow_up,
        "step": MatterPlanStep.objects.create(matter=matter, title="Sammu soovitus", position=0),
        "notice": MatterAssignmentNotice.objects.create(
            matter=matter, recipient=owner, assigned_by=world["colleague"]
        ),
        "important_date": model("intelligence", "MatterImportantDate").objects.get(
            matter=matter, title=HIDDEN_PHRASE
        ),
        "effective_date": model("intelligence", "MatterEffectiveDate").objects.get(matter=matter),
        "work_victory": model("intelligence", "MatterWorkVictory").objects.get(
            matter=matter, title=HIDDEN_PHRASE
        ),
    }


@pytest.fixture
def hidden(world, capture_evidence, extract):  # noqa: F811
    restricted = add_restricted_matter(world, capture_evidence, extract)
    children = add_restricted_children(world, capture_evidence, extract)
    return {
        "restricted-matter": _objects(restricted["matter"], restricted, world, restrict=False),
        "restricted-children": _objects(children["matter"], children, world, restrict=True),
    }


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

#: What may change during a denied request: nothing, in any of these.
WATCHED = (
    "matters.Matter",
    "matters.Entry",
    "matters.MatterEngagement",
    "matters.MatterExternalPosition",
    "matters.MatterProceduralDevelopment",
    "matters.MatterProceduralLink",
    "matters.MatterWebsiteOverview",
    "matters.MatterAssignmentNotice",
    "workflow.NextAction",
    "workflow.MatterPlanStep",
    "workflow.OpinionFollowUp",
    "documents.Document",
    "documents.DocumentVersion",
    "submissions.Submission",
    "intelligence.MatterImportantDate",
    "intelligence.MatterEffectiveDate",
    "intelligence.MatterWorkVictory",
    "audit.ChangeEvent",
)


def _fingerprint() -> list[tuple[str, int, Any]]:
    out = []
    for label in WATCHED:
        model = apps.get_model(label)
        stamp = "updated_at" if hasattr(model, "updated_at") else "created_at"
        row = model.objects.aggregate(last=Max(stamp))
        out.append((label, model.objects.count(), row["last"]))
    return out


def _body(response: Any) -> str:
    if getattr(response, "streaming", False):
        raw = b"".join(response.streaming_content)
    else:
        raw = response.content
    return raw.decode("utf-8", errors="replace")


def _call(client: Client, method: str, candidate: Candidate, kwargs: dict[str, str] | None = None):
    url = reverse(candidate.route, kwargs=kwargs or candidate.kwargs)
    if method == "post":
        return client.post(url, dict(candidate.data))
    return client.get(url)


def _is_quiet_refusal(response: Any, persona: str) -> bool:
    """A refusal that is the same whether or not the record exists."""
    if response.status_code in (404, 405):
        return True
    if persona == "gate" and response.status_code == 302:
        return response["Location"].startswith(reverse("accounts:choose_persona"))
    return False


def _random(candidate: Candidate) -> dict[str, str]:
    return {
        key: (str(uuid.uuid4()) if key in candidate.hidden else value)
        for key, value in candidate.kwargs.items()
    }


def _shape(response: Any) -> tuple[int, str]:
    location = response.get("Location", "") if response.status_code in (301, 302) else ""
    return response.status_code, location.split("?")[0]


def _denied(client: Client, persona: str, worlds: dict[str, dict[str, Any]]) -> tuple[int, list]:
    requests, problems = 0, []
    for world_name, candidate in _sweep_list(worlds):
        for method in ("get", "post"):
            before = _fingerprint() if method == "post" else None
            response = _call(client, method, candidate)
            requests += 1
            where = (persona, world_name, method, candidate.route, candidate.kwargs)
            body = _body(response)
            found = [token for token in TOKENS if token in body]
            if found:
                problems.append((*where, response.status_code, f"tokens {found}"))
            if candidate.route in FILE_ROUTES and response.status_code == 200:
                problems.append((*where, 200, "file served"))
            if response.status_code >= 500:
                problems.append((*where, response.status_code, "5xx"))
            if before is not None and _fingerprint() != before:
                problems.append((*where, response.status_code, "wrote"))
            if candidate.hidden and not _is_quiet_refusal(response, persona):
                other = _call(client, method, candidate, _random(candidate))
                requests += 1
                if _shape(other) != _shape(response):
                    problems.append(
                        (*where, response.status_code, f"random id answers {other.status_code}")
                    )
    return requests, problems


def _signed_in(user: Any) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _persona(name: str, world: dict[str, Any], settings: Any) -> Client:  # noqa: F811
    if name == "gate":
        apply_shared_gate(settings)
        gate = Client()
        assert gate.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302
        return gate
    if name == "collaborating_reader":
        reader = factories.UserFactory(role=UserRole.READER, display_name="Kaasatud Lugeja")
        own = factories.MatterFactory(owner=world["owner"], visibility=Visibility.RESTRICTED)
        own.collaborators.set([reader])
        return _signed_in(reader)
    return _signed_in(world[name])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_every_id_route_is_swept_or_named():
    """A new route with an id is either filled from the world or named with a reason."""
    names = {route for route, _ in _routes()}
    assert set(NOT_MATTER_DATA) <= names, set(NOT_MATTER_DATA) - names
    assert set(REMOVABLE) == set(removable_kinds())

    class AnyRow(dict):
        def __missing__(self, key: str) -> Any:
            return type("Row", (), {"pk": uuid.uuid4()})()

    swept = _sweep_list({"any": AnyRow(restricted_matter=True)})  # raises on an unknown <param>
    assert {candidate.route for _, candidate in swept} == names - set(NOT_MATTER_DATA)


@pytest.mark.parametrize("persona", ["administrator", "gate"])
def test_no_hidden_id_reaches_anybody_outside(world, hidden, settings, persona):  # noqa: F811
    client = _persona(persona, world, settings)

    requests, problems = _denied(client, persona, hidden)

    assert requests > 2 * len(_sweep_list(hidden)) - 1
    assert problems == [], "\n".join(map(str, problems))


def _is_write_gated(candidate: Candidate) -> bool:
    """Whether the route's view is wrapped in `business_write_required`."""
    from urllib.parse import unquote

    from django.urls import resolve

    view = resolve(unquote(reverse(candidate.route, kwargs=candidate.kwargs))).func
    while view is not None:
        if view.__code__.co_qualname == "business_write_required.<locals>.wrapper":
            return True
        view = getattr(view, "__wrapped__", None)
    return False


@pytest.mark.parametrize("persona", ["reader", "collaborating_reader"])
def test_a_reader_reads_every_hidden_record_the_owner_reads(world, hidden, settings, persona):  # noqa: F811
    """docs/adr/0181: a READER is an authorized internal user.

    Every swept GET the owner is answered 200 on, the READER is answered 200 on
    too, with the same hidden tokens in the body — RESTRICTED is a label, not a
    boundary between the two. Write routes are not compared — their GET is the
    edit form, which a READER is refused because it still writes nothing
    (`business_write_required`) — so they are identified by that decorator.
    """
    owner = _signed_in(world["owner"])
    client = _persona(persona, world, settings)
    compared, mismatches = 0, []
    for world_name, candidate in _sweep_list(hidden):
        if _is_write_gated(candidate):
            continue
        as_owner = _call(owner, "get", candidate)
        if as_owner.status_code != 200:
            continue
        as_reader = _call(client, "get", candidate)
        compared += 1
        owner_tokens = [token for token in TOKENS if token in _body(as_owner)]
        reader_tokens = [token for token in TOKENS if token in _body(as_reader)]
        if as_reader.status_code != 200 or reader_tokens != owner_tokens:
            mismatches.append((world_name, candidate.route, as_reader.status_code))
    assert compared > 0
    assert mismatches == [], "\n".join(map(str, mismatches))


def test_the_sweep_ids_reach_their_records(world, hidden):  # noqa: F811
    """The owner gets no 404 on any swept request, so every id names a real record.

    Each POST runs inside a savepoint that is rolled back, so one route's write
    cannot change what the next one finds.
    """
    owner = _signed_in(world["owner"])
    misses = []
    for world_name, candidate in _sweep_list(hidden):
        for method in ("get", "post"):
            with contextlib.suppress(_RolledBack), transaction.atomic():
                response = _call(owner, method, candidate)
                if response.status_code == 404:
                    misses.append((world_name, method, candidate.route, candidate.kwargs))
                raise _RolledBack
    assert misses == [], "\n".join(map(str, misses))


class _RolledBack(Exception):
    pass
