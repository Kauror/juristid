"""No business route answers anybody who is not an authorized internal reader (docs/adr/0181).

Walks Django's own resolver — never a hand-kept list — and requests every route
with GET and an empty POST as each kind of caller the policy says must learn
nothing:

* an anonymous visitor (``AUTH_MODE=none``, the CI and development mode);
* a shared-gate session that typed the password and chose **no persona** — it
  read every NORMAL record through ``@gate_required`` until docs/adr/0181;
* an ADMINISTRATOR without a business role, signed in (GET only: the write
  boundary that refuses its POSTs is `tests/test_one_write_gate.py`'s).

A route is either on the short allowlist below, with the reason it answers
without a reader, or it must refuse: a redirect to sign in or to the persona
chooser, a 404 for a mode-bound route, a 405 for a verb it does not take. No
response may be a 5xx, no file may be served, and none may carry a word that
exists only on a business record. A new route that answers such a caller
anything else fails here, which is the point: a public endpoint cannot appear
by accident.
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlencode, urlsplit

import pytest
from django.test import Client
from django.urls import Resolver404, URLPattern, URLResolver, get_resolver, resolve, reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.submissions.enums import SubmissionStatus
from tests import factories
from tests.gate import PASSWORD, apply_shared_gate

pytestmark = pytest.mark.django_db

NORMAL_TITLE = "AVALIK-MARSRUUT-6610"
RESTRICTED_TITLE = "PIIRATUD-MARSRUUT-6611"
FILE_NAME = "marsruudi-fail-6612.pdf"
TOKENS = (NORMAL_TITLE, RESTRICTED_TITLE, FILE_NAME, "%PDF-1.4 marsruut")

#: Routes that answer a caller with no reader, and why each is harmless.
PUBLIC = {
    "core:healthz": "the container runtime's health probe",
    "core:favicon": "a redirect to a static brand image",
    "core:home": "a redirect to sign in, to the persona chooser or to Minu asjad",
    "core:release_notes": "Uuendused: product release notes, no business data",
    "core:keyboard_shortcuts": "Kiirklahvid: the keyboard-shortcut reference, no business data",
    "accounts:shared_gate": "the gate's own password form",
    "accounts:choose_persona": "the persona chooser itself",
    "accounts:act_as": "choosing a persona; refuses anybody not behind the gate",
    "accounts:sign_out": "ending the session",
    "accounts:dev_login": "the development sign-in; 404 unless DEV_LOGIN_ENABLED",
    "accounts:sign_in": "personal sign-in (dormant, docs/adr/0145)",
    "accounts:sign_in_second_factor": "personal sign-in (dormant)",
    "accounts:activate": "a one-time activation link (dormant)",
    "accounts:forgot_password": "a reset request (dormant)",
    "accounts:reset_password": "a one-time reset link (dormant)",
    # The verified administrator session's own pages (docs/adr/0186): behind
    # the gate, 404 in every other mode, and they show a sign-in, code or
    # enrolment form — never business content. What they open is Haldus
    # administration, which refuses by itself (tests/test_admin_session*.py).
    "haldus:sign_in": "the administrator step-up's own sign-in form",
    "haldus:sign_in_code": "the step-up's code form; else back to its sign-in",
    "haldus:enrol": "the step-up's authenticator enrolment; back to the sign-in without one",
    "haldus:reauthenticate": "the step-up's fresh proof; back to the sign-in without a session",
    "haldus:sign_out": "ends the administrator session only",
    # The MCP authorization server's machine endpoints (docs/adr/0185 §4): exempt
    # from the gate by exact path, they authenticate the *client* themselves and
    # answer only OAuth metadata or token JSON — never business content. Every
    # one is a 404 unless MCP_ENABLED. `/oauth/authorize` and the enrolment page
    # are not here: with MCP off they are a 404 like the rest, and with it on
    # they answer a persona-less gate session — on purpose (docs/adr/0185 §3) —
    # with a form that asks for the account's own address and renders no
    # business content, which
    # `test_the_mcp_browser_pages_ask_a_persona_less_gate_session_for_the_account`
    # below proves. Anonymous callers are still refused.
    "mcp_access:authorization_server_metadata": "RFC 8414 metadata built from settings only",
    "mcp_access:token": "OAuth token endpoint; authenticates the client, issues opaque tokens",
    "mcp_access:revoke": "OAuth revocation endpoint (RFC 7009); answers 200 with no body",
}

#: Ending or switching the session mid-sweep would change the caller under test.
SESSION_CHANGING = {"accounts:sign_out", "accounts:act_as", "accounts:dev_login"}

#: The answers a refused request may take, besides a redirect to the doorway.
QUIET = {404, 405}

FILE_ROUTES = {
    "documents:download",
    "documents:open",
    "documents:thumbnail",
    "legacy_import:source_xml",
    "legacy_import:opinion_archive_file",
}


def _routes() -> list[tuple[str, URLPattern]]:
    def walk(resolver: Any, namespace: str = ""):
        for pattern in resolver.url_patterns:
            if isinstance(pattern, URLResolver):
                inner = namespace + (f"{pattern.namespace}:" if pattern.namespace else "")
                yield from walk(pattern, inner)
            elif isinstance(pattern, URLPattern) and pattern.name:
                yield namespace + pattern.name, pattern

    # Django's own admin is reached only through the mode's sign-in and scoped
    # by `visible_to` (app/core/admin.py, tests/test_admin_*).
    return [(name, p) for name, p in walk(get_resolver()) if not name.startswith("admin:")]


@pytest.fixture
def ids(capture_evidence) -> dict[str, Any]:
    owner = factories.UserFactory()
    normal = factories.MatterFactory(owner=owner, title=NORMAL_TITLE)
    restricted = factories.MatterFactory(
        owner=owner, title=RESTRICTED_TITLE, visibility=Visibility.RESTRICTED
    )
    version = capture_evidence(
        restricted,
        b"%PDF-1.4 marsruut",
        FILE_NAME,
        "application/pdf",
        visibility_override=Visibility.RESTRICTED,
    )
    submission = factories.SubmissionFactory(
        matter=restricted,
        status=SubmissionStatus.SENT,
        sent_at=timezone.now(),
        final_version=version,
    )
    return {
        "normal": normal.pk,
        "restricted": restricted.pk,
        "version": version.pk,
        "document": version.document_id,
        "submission": submission.pk,
        "owner": owner.pk,
    }


#: Document routes that name the Document rather than one of its versions.
DOCUMENT_ROUTES = {
    "documents:document_detail",
    "documents:add_version",
    "documents:change_role",
    "documents:rename",
    "documents:remove",
}


def _url(name: str, pattern: URLPattern, ids: dict[str, Any]) -> str:
    kwargs: dict[str, Any] = {}
    for key, converter in pattern.pattern.converters.items():
        kind = type(converter).__name__
        if kind == "UUIDConverter":
            if name.startswith("documents:") and key == "pk":
                kwargs[key] = ids["document"] if name in DOCUMENT_ROUTES else ids["version"]
            elif name.startswith("submissions:") and key == "pk":
                kwargs[key] = ids["submission"]
            elif name == "matters:person_work" or name.startswith("account_admin:"):
                kwargs[key] = ids["owner"]
            elif key in ("pk", "matter_id"):
                kwargs[key] = ids["restricted"]
            else:
                kwargs[key] = uuid.uuid4()
        elif kind == "SlugConverter":
            kwargs[key] = "teemad"
        elif kind == "IntConverter":
            kwargs[key] = 1
        else:
            kwargs[key] = "x"
    return reverse(name, kwargs=kwargs)


def _body(response: Any) -> str:
    raw = b"".join(response.streaming_content) if response.streaming else response.content
    return raw.decode("utf-8", errors="replace")


def _sweep(client: Client, ids: dict[str, Any], *, refused: Any, post: bool = True) -> list:
    """Every non-public route, as ``client``; the problems found."""
    problems = []
    for name, pattern in _routes():
        if name in PUBLIC:
            continue
        url = _url(name, pattern, ids)
        for method in ("get", "post") if post else ("get",):
            response = getattr(client, method)(url)
            where = (name, method, response.status_code)
            if response.status_code >= 500:
                problems.append((*where, "5xx"))
            found = [token for token in TOKENS if token in _body(response)]
            if found:
                problems.append((*where, f"tokens {found}"))
            if name in FILE_ROUTES and response.status_code == 200:
                problems.append((*where, "file served"))
            reason = refused(name, response)
            if reason:
                problems.append((*where, reason))
    return problems


def _forwards_to_a_swept_route(location: str) -> bool:
    """A retired address that only forwards to another route this sweep also checks.

    `/ulevaade/` → Osakond, `/olulised-tahtajad/` → the register: a fixed
    redirect that reads nothing and names no record. The route it names is
    refused in its own right, so the forward cannot be a way round it.
    """
    path = urlsplit(location).path
    if not path.startswith("/"):
        return False
    try:
        match = resolve(path)
    except Resolver404:
        return False
    return match.view_name not in PUBLIC and not any(token in location for token in TOKENS)


def _redirected_to(target: str) -> Any:
    def refused(name: str, response: Any) -> str:
        if response.status_code in QUIET:
            return ""
        location = response.get("Location", "") if response.status_code in (301, 302) else ""
        if response.status_code == 302 and location.startswith(target):
            return ""
        if location and _forwards_to_a_swept_route(location):
            return ""
        return f"not refused (Location {location!r})"

    return refused


def test_every_allowlisted_route_still_exists():
    """An allowlist entry for a route that is gone would silently allow its successor."""
    names = {name for name, _ in _routes()}
    assert set(PUBLIC) <= names, set(PUBLIC) - names
    assert len(names - set(PUBLIC)) > 150


def test_an_anonymous_visitor_reaches_no_business_route(client, settings, ids):
    settings.AUTH_MODE = "none"
    problems = _sweep(client, ids, refused=_redirected_to(reverse(settings.LOGIN_URL)))
    assert problems == [], "\n".join(map(str, problems))


def test_the_shared_gate_without_a_persona_reaches_no_business_route(client, settings, ids):
    """The shared password alone is not an authorized internal user (docs/adr/0181)."""
    apply_shared_gate(settings)
    assert client.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302

    problems = _sweep(client, ids, refused=_redirected_to(reverse("accounts:choose_persona")))

    assert problems == [], "\n".join(map(str, problems))
    # Still behind the door, still without a persona: the sweep changed nothing.
    assert not client.get(reverse("accounts:choose_persona")).wsgi_request.user.is_authenticated

    # The surfaces `@gate_required` used to open to this session, by name: each
    # now sends it to choose a persona, and an htmx request gets the 401 that
    # names where to go instead of a fragment.
    chooser = reverse("accounts:choose_persona")
    for name, kwargs in (
        ("matters:department", {}),
        ("reporting:overview", {}),
        ("reporting:export", {"slug": "teemad"}),
        ("submissions:sent", {}),
        ("documents:download", {"pk": ids["version"]}),
        ("documents:document_detail", {"pk": ids["document"]}),
    ):
        response = client.get(reverse(name, kwargs=kwargs))
        assert response.status_code == 302 and response["Location"].startswith(chooser), name
        htmx = client.get(reverse(name, kwargs=kwargs), headers={"HX-Request": "true"})
        assert htmx.status_code == 401, name


def test_the_persona_chooser_and_the_release_notes_stay_open_behind_the_gate(client, settings):
    apply_shared_gate(settings)
    assert client.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302
    assert client.get(reverse("accounts:choose_persona")).status_code == 200
    assert client.get(reverse("core:release_notes")).status_code == 200
    assert client.get(reverse("core:keyboard_shortcuts")).status_code == 200
    assert client.get(reverse("core:healthz")).status_code == 200


def test_an_administrator_without_a_business_role_reads_nothing_anywhere(client, ids):
    """Signed in, so pages answer — empty. Technical administration is not business access."""
    client.force_login(factories.AdministratorFactory())

    def refused(name: str, response: Any) -> str:
        return ""

    problems = _sweep(client, ids, refused=refused, post=False)
    assert problems == [], "\n".join(map(str, problems))


def test_the_sweep_reaches_the_records_for_a_reader(client, ids):
    """The ids are real: a READER, inside the policy, opens what the sweep named."""
    client.force_login(factories.ReaderFactory())
    for name, kwargs in (
        ("matters:matter_detail", {"pk": ids["restricted"]}),
        ("documents:document_detail", {"pk": ids["document"]}),
        ("documents:download", {"pk": ids["version"]}),
    ):
        response = client.get(reverse(name, kwargs=kwargs))
        assert response.status_code == 200, name
    detail = client.get(reverse("matters:matter_detail", kwargs={"pk": ids["restricted"]}))
    assert RESTRICTED_TITLE in _body(detail)


def test_the_mcp_browser_pages_ask_a_persona_less_gate_session_for_the_account(
    client, settings, ids
):
    """docs/adr/0185 §3: behind the gate, no persona; the page asks who, and shows no record."""
    from tests import mcp_access_support

    apply_shared_gate(settings)
    mcp_access_support.enable(settings)
    authorize = (
        f"{reverse('mcp_access:authorize')}?{urlencode(mcp_access_support.authorize_params())}"
    )
    enrol = reverse("mcp_access:enrol")
    for path in (authorize, enrol):
        anonymous = Client().get(path)
        assert anonymous.status_code == 302, path
        assert anonymous["Location"].startswith(reverse("accounts:shared_gate")), path

    assert client.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302
    for path in (authorize, enrol):
        for method in ("get", "post"):
            response = getattr(client, method)(path)
            assert response.status_code in (200, 400), (path, method, response.status_code)
            assert not [token for token in TOKENS if token in _body(response)], (path, method)
    assert 'name="epost"' in _body(client.get(authorize))
    assert not client.get(authorize).wsgi_request.user.is_authenticated
