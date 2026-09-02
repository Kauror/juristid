"""The shared department gate, and the persona picked behind it.

A temporary development-phase authenticator with an honest name. It does two
separable things, and keeping them separable is the whole design:

**The gate** authenticates *the door*. One long password, supplied host-side,
hashed once at startup, compared in constant time, and rate limited with an
escalating per-client lockout. Passing it proves that somebody knows the
department's shared password.

**The persona** is a choice, not a claim. After the gate, a person picks whose
work they are looking at. That selection drives `Minu töö`, ownership filters
and profile context — and it is *not* evidence that the named human is at the
keyboard. Every audit row this mode produces says so, in the
``authenticated_via`` field, because a record that quietly reads as "Marko did
this" would be a lie the system told about itself (Stage-2D auth brief 5).

Nothing here changes business authorization. The persona goes into
`request.user` exactly as any other sign-in would, and `scope_for_user` decides
what they may see. With no persona selected there is a *department scope* —
NORMAL visibility and nothing that depends on knowing who you are — which is
what makes the landing dashboard useful before anybody has chosen a name
(app/core/authorization.py).

The password's plaintext lives in this process's environment and in nothing
else. It is not written to the database, not logged, not rendered, and not
reachable from the client.
"""

from __future__ import annotations

import functools
import hashlib
import hmac
import logging
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone

from app.accounts.enums import AUTHENTICATED_VIA, AuthMode

logger = logging.getLogger(__name__)

#: Session keys. Namespaced so nothing else can collide with them, and so a
#: `grep` for the gate finds every place its state is touched.
GATE_PASSED_AT = "shared_gate:passed_at"
GATE_PERSONA_CHOSEN_AT = "shared_gate:persona_chosen_at"

#: Paths that answer before the gate. Kept to exactly what the platform needs:
#: a health check the container runtime calls, and the static files Whitenoise
#: serves. Everything else is behind the password.
EXEMPT_PREFIXES = ("/healthz", "/static/")


class GateLocked(Exception):
    """This client has spent its attempts. Carries the wait, in seconds."""

    def __init__(self, seconds: int) -> None:
        super().__init__(f"locked for {seconds}s")
        self.seconds = seconds


# -- the mode --------------------------------------------------------------


def current_mode() -> str:
    raw = (getattr(settings, "AUTH_MODE", "") or AuthMode.NONE).strip().lower()
    return raw if raw in AuthMode.values else AuthMode.NONE


def is_shared_gate() -> bool:
    return current_mode() == AuthMode.SHARED_GATE


def authenticated_via() -> str:
    """What this deployment is entitled to say about how somebody arrived."""
    return AUTHENTICATED_VIA.get(AuthMode(current_mode()), "NONE")


# -- the password ----------------------------------------------------------


def configured_password() -> str:
    return getattr(settings, "SHARED_GATE_PASSWORD", "") or ""


@functools.lru_cache(maxsize=4)
def _hash_for(raw: str) -> str:
    """The configured password's hash, computed once per process.

    Django's hasher, so the comparison is the framework's constant-time one
    against a salted PBKDF2 digest rather than something hand-rolled. Cached on
    the plaintext rather than at import so that a test can change the setting
    and get the new answer, and so the (deliberately expensive) hashing happens
    once instead of on every request.

    The plaintext is a function argument and a cache key inside this process.
    It is never returned, stored, or rendered.
    """
    return make_password(raw)


def is_configured() -> bool:
    return bool(configured_password())


def verify_password(supplied: str) -> bool:
    """Constant-time check of one attempt against the configured password."""
    configured = configured_password()
    if not configured:
        # An unconfigured gate opens for nobody. A deployment that reaches this
        # is already refused by the system checks (juristid.E009); returning
        # False as well means a misconfiguration cannot become an open door.
        return False
    return check_password(supplied, _hash_for(configured))


# -- who is knocking -------------------------------------------------------


def client_key(request: HttpRequest) -> str:
    """A stable, non-identifying key for the throttle.

    Behind the tunnel every request arrives from the connector, so the
    forwarded address is the only thing that distinguishes callers. It is
    attacker-controlled, which would matter if this were *identity* — it is a
    rate limit, and an attacker who rotates the header to dodge it still has to
    guess a long password one request at a time.

    Hashed with the deployment's secret so the throttle table holds no address
    in the clear: it is operational state, not an access log, and the access log
    already exists next to it.
    """
    forwarded = request.META.get("HTTP_CF_CONNECTING_IP") or request.META.get(
        "HTTP_X_FORWARDED_FOR", ""
    )
    address = (
        forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR", "")
    ) or "unknown"
    digest = hmac.new(settings.SECRET_KEY.encode("utf-8"), address.encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[:64]


# -- throttling ------------------------------------------------------------


def lockout_seconds_remaining(request: HttpRequest) -> int:
    from app.accounts.models import SharedGateThrottle

    record = SharedGateThrottle.objects.filter(client_key=client_key(request)).first()
    if record is None:
        return 0
    return record.seconds_remaining()


def require_not_locked(request: HttpRequest) -> None:
    remaining = lockout_seconds_remaining(request)
    if remaining:
        raise GateLocked(remaining)


def record_failure(request: HttpRequest) -> int:
    """Count one wrong password, and return the resulting wait in seconds.

    Per client, never global. A global counter would let one attacker lock the
    department out of its own system, which is a denial-of-service primitive
    dressed as a control (Stage-2D auth brief 9).

    **The count is taken under the row's own lock.** It used to be read into
    Python, incremented and written back with nothing making two writers take
    turns, so attempts arriving together each read the same value and each wrote
    the same value: parallel guesses recorded one failure between them and never
    reached the threshold that arms the lockout. An attacker who opens twenty
    connections instead of sending twenty requests in a row was not slowed at
    all, which made the escalation this control exists for unreachable (SEC-01).

    The lock is on **one row**, and that is the same boundary that keeps the
    control per client: two attempts against the same client key take turns,
    while a different client key is a different row and waits for nobody. A
    table-level lock would fix the counter by recreating the denial-of-service
    primitive the design refuses.

    `FOR UPDATE` rather than the `FOR NO KEY UPDATE` that `app.matters.locks`
    argues for, because that argument does not apply here: it exists so that
    inserting a row which *references* the locked one does not queue behind it,
    and nothing references a throttle row. This is a counter nobody points at —
    the same shape, and the same lock, as `MatterReferenceSequence`
    (`app.matters.services.allocate_matter_reference`).

    The table sits outside the Matter → Submission → Document order entirely and
    nothing else in the application locks it, so this adds no edge to that graph.
    """
    with transaction.atomic():
        record = _locked_throttle(client_key(request))
        return record.register_failure(
            max_attempts=settings.SHARED_GATE_MAX_ATTEMPTS,
            base_seconds=settings.SHARED_GATE_LOCKOUT_SECONDS,
            ceiling_seconds=settings.SHARED_GATE_MAX_LOCKOUT_SECONDS,
        )


def _locked_throttle(key: str) -> Any:
    """This client's throttle row, created if absent, held under its own lock.

    Two statements rather than one, in this order, because each ordering closes
    a different race and only this one closes both.

    ``get_or_create`` first, so two *first* attempts from one client cannot lose
    each other. Django runs the insert in its own savepoint and re-reads on a
    unique-key collision, so the loser continues instead of surfacing a database
    error to somebody who merely mistyped a password — and the winner's row is
    then what both of them lock.

    The locking read second, because the row returned above was read without a
    lock and may already be superseded. A transaction that waited here was by
    definition waiting for another writer, and a lock that only delays a stale
    read closes nothing (`app.matters.locks`).

    Between those two statements the row can *disappear*: a correct password
    from the same client deletes it, which is `record_success` doing exactly
    what it is for. That must not become a 500 on the sign-in page, so absence
    is tolerated and the row is made again. The second pass cannot fail — a row
    this transaction created is one no other transaction can delete until this
    one commits — which is why the bound is two and not a retry loop.
    """
    from app.accounts.models import SharedGateThrottle

    for _ in range(2):
        SharedGateThrottle.objects.get_or_create(client_key=key)
        record = SharedGateThrottle.objects.select_for_update().filter(client_key=key).first()
        if record is not None:
            return record
    # Unreachable for the reason the docstring gives. Deliberately without the
    # client key in it: the key is a hash rather than an address, but an
    # exception message is the wrong place to carry either.
    raise SharedGateThrottle.DoesNotExist(  # pragma: no cover - see the docstring
        "the throttle row was deleted twice while this attempt was being counted"
    )


def record_success(request: HttpRequest) -> None:
    from app.accounts.models import SharedGateThrottle

    SharedGateThrottle.objects.filter(client_key=client_key(request)).delete()


# -- session state ---------------------------------------------------------


def has_passed(request: HttpRequest) -> bool:
    """Whether this session is behind the gate and has not aged out."""
    if not is_shared_gate():
        return False
    raw = request.session.get(GATE_PASSED_AT)
    if not raw:
        return False
    try:
        passed_at = datetime.fromisoformat(raw)
    except ValueError:
        # A session value that is not a timestamp is not a pass. Sessions are
        # signed, so this means a code change rather than tampering — and
        # either way the answer is "ask for the password again".
        return False
    if timezone.is_naive(passed_at):
        return False
    age = timezone.now() - passed_at
    return age < timedelta(seconds=settings.SHARED_GATE_SESSION_SECONDS)


def open_gate(request: HttpRequest) -> None:
    """Record that this session passed, and give it a new session identifier.

    `cycle_key` rotates the identifier while keeping the session's contents,
    which is what stops a fixated identifier handed to somebody beforehand from
    becoming an authenticated one afterwards.
    """
    request.session.cycle_key()
    request.session[GATE_PASSED_AT] = timezone.now().isoformat()
    request.session.set_expiry(settings.SHARED_GATE_SESSION_SECONDS)


def close_gate(request: HttpRequest) -> None:
    for key in (GATE_PASSED_AT, GATE_PERSONA_CHOSEN_AT):
        request.session.pop(key, None)


def note_persona_chosen(request: HttpRequest) -> None:
    request.session[GATE_PERSONA_CHOSEN_AT] = timezone.now().isoformat()


# -- what the request may see ----------------------------------------------


def viewer(request: HttpRequest) -> Any:
    """Who to authorize this request as.

    The selected persona if there is one. Otherwise the department viewer: a
    sentinel that `scope_for_user` maps to NORMAL visibility and no
    participation, so the landing dashboard is useful before anybody has picked
    a name and still cannot show RESTRICTED material (Stage-2D auth brief 6).

    Deliberately not `request.user`. The sentinel is not a `User`, cannot be
    written to a foreign key, and never becomes an actor on an audit row — a
    reader with no persona can read the department and author nothing.
    """
    from app.core.authorization import DEPARTMENT_VIEWER

    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return user
    return DEPARTMENT_VIEWER


def audit_detail(request: HttpRequest, **extra: Any) -> dict[str, Any]:
    """The provenance every audit row in this mode carries.

    `acting_as_user` is the persona somebody selected. `authenticated_via` says
    how much that is worth. Recording only the first would be a claim this
    deployment cannot support (Stage-2D auth brief 5).
    """
    user = getattr(request, "user", None)
    acting = str(user.pk) if user is not None and user.is_authenticated else None
    return {
        "authenticated_via": authenticated_via(),
        "acting_as_user": acting,
        **extra,
    }
