"""Who was behind an audit row, and how much the system can say about it.

Every `ChangeEvent` and `SecurityAuditEvent` names an ``actor``: the account
the work is associated with. Under the shared gate that account is the persona
somebody *picked from a list* behind one department password, and a row that
said only «Marko» read as «Marko did this» — a claim the deployment cannot
support (docs/adr/0016, docs/adr/0180).

So every row written from now on also records, centrally, in
`app.audit.services`:

* **the method** — shared gate, personal password, Cloudflare Access, the
  development sign-in, or *system* when no request is behind the write (a
  management command, an import, a background job);
* **the assurance** — what that method proves about the person at that
  moment: nothing, the shared secret only, a personal password, a personal
  password and a second factor, an external identity provider;
* **the authenticated principal** — the account an individual sign-in proved,
  where one did. Never set under the shared gate: picking a persona proves
  nobody;
* **the acting persona** — whoever the session was acting as when the row was
  written, independently of the ``actor`` a caller passed (the ``actor``
  foreign key itself is unchanged, so workflow association is unchanged);
* **the correlation id** — one value for every row one request wrote
  (:class:`AuditContextMiddleware`), so a reader can put a request's rows back
  together. A background job queued by a request carries that request's id
  (:func:`correlated`). Outside both, the composer operation if there is one.

**Read at the moment of writing, not at the start of the request.** A request
can change what it is during its own course — the gate view opens the gate, a
persona choice logs a person in, a personal sign-in completes — and the row
must describe the state it was written in. The middleware therefore keeps the
*request*, and the provenance is derived from it when a row is written.

**Nothing historical is touched.** Rows written before this existed keep empty
values, which every reader treats as «not recorded» (``AuthMethod`` docs); no
migration infers an authentication nobody recorded.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from django.http import HttpRequest, HttpResponse

from app.audit.enums import AuthAssurance, AuthMethod
from app.audit.operations import current_operation_id

#: The request this execution context is serving, if any.
_current_request: ContextVar[HttpRequest | None] = ContextVar("audit_current_request", default=None)

#: The correlation id of this request, or of the request a background job was
#: queued by.
_current_correlation: ContextVar[uuid.UUID | None] = ContextVar(
    "audit_current_correlation", default=None
)

#: What a history row adds after the name of a persona chosen behind the
#: shared gate. Says what happened — the name was *chosen* behind the shared
#: password — rather than claiming or denying who was at the keyboard.
SHARED_GATE_QUALIFIER = "valitud ühisparooliga"


@dataclass(frozen=True)
class Provenance:
    method: str
    assurance: str
    principal: Any = None
    persona: Any = None
    correlation_id: uuid.UUID | None = None


@contextmanager
def request_context(request: HttpRequest) -> Iterator[uuid.UUID]:
    """Make ``request`` the one every audit row written inside belongs to."""
    correlation = uuid.uuid4()
    request_token = _current_request.set(request)
    correlation_token = _current_correlation.set(correlation)
    try:
        yield correlation
    finally:
        _current_correlation.reset(correlation_token)
        _current_request.reset(request_token)


@contextmanager
def correlated(correlation_id: uuid.UUID | None) -> Iterator[None]:
    """Run outside a request — a background job — under a request's correlation id.

    The job is still *system* work: nobody's session is behind it. Only the id
    says which request queued it.
    """
    token = _current_correlation.set(correlation_id)
    try:
        yield
    finally:
        _current_correlation.reset(token)


def current_correlation_id() -> uuid.UUID | None:
    """This request's correlation id, a job's inherited one, or the composer operation."""
    return _current_correlation.get() or current_operation_id()


class AuditContextMiddleware:
    """Open the audit context for one request, and close it again.

    Wraps ``get_response`` rather than splitting set-up and tear-down across
    two hooks, for the reason `RequestScopeMiddleware` gives: the value lives in
    a `ContextVar`, a worker thread serves request after request, and a request
    left behind would put the next one's rows under its correlation id.
    """

    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        with request_context(request):
            return self.get_response(request)


def _authenticated_user(request: HttpRequest) -> Any:
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return user
    return None


def current_provenance() -> Provenance:
    """What this moment's audit row records about how its actor arrived."""
    correlation = current_correlation_id()
    request = _current_request.get()
    if request is None:
        return Provenance(
            method=AuthMethod.SYSTEM,
            assurance=AuthAssurance.SYSTEM,
            correlation_id=correlation,
        )

    # Imported here: `app.accounts` writes audit rows through
    # `app.audit.services`, so a module-level import would be circular.
    from app.accounts import local_auth, shared_gate
    from app.accounts.enums import AuthMode

    user = _authenticated_user(request)
    mode = shared_gate.current_mode()
    if mode == AuthMode.SHARED_GATE:
        # The persona is recorded; a principal never is. Passing the gate
        # proves the shared password was known, not by whom.
        passed = hasattr(request, "session") and shared_gate.has_passed(request)
        return Provenance(
            method=AuthMethod.SHARED_GATE,
            assurance=AuthAssurance.SHARED_SECRET if passed else AuthAssurance.NONE,
            persona=user,
            correlation_id=correlation,
        )
    if mode == AuthMode.LOCAL_PASSWORD:
        if (
            user is not None
            and hasattr(request, "session")
            and local_auth.is_local_session(request)
        ):
            strong = local_auth.has_second_factor_session(request)
            return Provenance(
                method=AuthMethod.LOCAL_PASSWORD,
                assurance=(
                    AuthAssurance.INDIVIDUAL_MFA if strong else AuthAssurance.INDIVIDUAL_PASSWORD
                ),
                principal=user,
                persona=user,
                correlation_id=correlation,
            )
        return Provenance(
            method=AuthMethod.LOCAL_PASSWORD,
            assurance=AuthAssurance.NONE,
            correlation_id=correlation,
        )
    if mode == AuthMode.CLOUDFLARE_ACCESS:
        return Provenance(
            method=AuthMethod.CLOUDFLARE_ACCESS,
            assurance=AuthAssurance.EXTERNAL_IDP if user is not None else AuthAssurance.NONE,
            principal=user,
            persona=user,
            correlation_id=correlation,
        )
    # `AUTH_MODE=none`: the synthetic development sign-in. It names an account
    # and proves nothing about a person, so it has a persona and no principal.
    return Provenance(
        method=AuthMethod.DEV_LOGIN,
        assurance=AuthAssurance.NONE,
        persona=user,
        correlation_id=correlation,
    )


def actor_qualifier(auth_method: str | None) -> str:
    """What a history row says after the actor's name, or "".

    Only for the shared gate: there the name is a persona somebody chose, and
    the row must not read as a personally verified act. Rows written before
    provenance was recorded say nothing extra — exactly as they read before.
    """
    return SHARED_GATE_QUALIFIER if auth_method == AuthMethod.SHARED_GATE else ""


def actor_label(name: str, auth_method: str | None) -> str:
    """«Marko Mets (valitud ühisparooliga)», or the name alone."""
    qualifier = actor_qualifier(auth_method)
    if not name or not qualifier:
        return name
    return f"{name} ({qualifier})"
