"""Two security rows the trail promised and nobody wrote (B-01).

**EXPORT_GENERATED** — one row for every bulk export a reader is handed: the
register workbooks, the Statistika CSVs, the old-layout workbook, the Lovable
workbook and the Word overview. A single evidence file already writes
``DOCUMENT_DOWNLOADED`` where it is served, and is not an export.

**BREAK_GLASS_USED** — one row for every request whose business read was
authorized by a break-glass grant rather than by a business role
(`app.core.authorization`, docs/adr/0181).

**What a row may hold, and what it may not** (the owner's rule). It records
that the operation happened: which export, in which format, how many records
it selected, who asked, as which persona and how they had signed in
(`shared_gate.audit_detail` plus the provenance columns every row carries,
docs/adr/0180), and from where. It never records what the file *said*: no
title, no cell, no search text. The audit trail is read by people who were not
entitled to the export, and an audit row that quoted it would be a second copy
of it with weaker protection.

The filters are summarised the same way. Their *names* say what kind of slice
was taken, and only a name the export itself honours is written: each export
passes its own allowlist (``honoured``), built from the constants its view and
form read, beside the call. Any other name — one a caller made up, which can
itself carry text (``?salajane_eelnou=1``) — is counted in
``other_filter_keys`` and never written (2026-11 QA, B-01). Their *values* can
be anything a reader typed — a Teema title in a search box — so a value is
never stored. What is stored instead is a keyed digest of the whole parameter
set (``filters_hmac``): two rows with the same digest were the same slice,
which is what an exposure review needs, and nobody can compute it offline
without the deployment's secret key.

**The digest is not secret from whoever can make the application compute it.**
Anyone who can run an export with filters of their choosing gets the digest of
their guess written to the trail, and a digest equal to another row's confirms
the guess (2026-11 QA, B-01). That is harmless for an actor who may read
business content — they could run the same search on the page and read the
answer — and is the whole leak for one who may not: an administrator without a
grant is handed an empty file and would still be handed the digest. So the
digest is written only when the request may read business content
(`authorization.may_read_business_content`, the one predicate, docs/adr/0181);
otherwise the row keeps the filter names and the row count and no digest.

**HEAD writes nothing.** A HEAD asks what the file would be and is handed no
file, so nothing was exported (QA-07, `app.core.http.head_response`).
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any
from urllib.parse import urlencode

from django.http import HttpRequest, QueryDict
from django.utils.crypto import salted_hmac

from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.audit.services import record_security_event

#: The salt of the filter digest, so it is never comparable with any other
#: keyed hash the application makes.
_FILTER_SALT = "app.audit.access.export-filters"

#: Parameters that are not a filter: the CSRF token a posted form carries.
_NOT_FILTERS = frozenset({"csrfmiddlewaretoken"})


def filter_summary(
    params: QueryDict | None, *, honoured: Collection[str], keyed: bool = True
) -> dict[str, Any]:
    """The names of the filters applied, and a keyed digest of their values.

    Empty parameters are not filters: the page posts every field of its form,
    and «nothing chosen» is the same slice whether the field was sent or not.
    Only a name in ``honoured`` is written; the rest are counted, unnamed.
    ``keyed=False`` — the request may not read business content — leaves the
    digest empty: it would only confirm a guess (module docstring).
    """
    if params is None:
        return {"filter_keys": [], "filters_hmac": ""}
    applied = sorted(
        key
        for key, values in params.lists()
        if key not in _NOT_FILTERS and any(str(value).strip() for value in values)
    )
    names = [key for key in applied if key in honoured]
    summary: dict[str, Any] = {"filter_keys": names, "filters_hmac": ""}
    if len(names) != len(applied):
        summary["other_filter_keys"] = len(applied) - len(names)
    if applied and keyed:
        canonical = urlencode(
            [(key, str(value).strip()) for key in applied for value in params.getlist(key)]
        )
        summary["filters_hmac"] = salted_hmac(
            _FILTER_SALT, canonical, algorithm="sha256"
        ).hexdigest()
    return summary


def _request_actor(request: HttpRequest) -> Any:
    """The account the row is associated with, as ``DOCUMENT_DOWNLOADED`` names it.

    The persona if one was selected, and nobody otherwise. Only a saved account
    can be an actor: the shared-gate sentinel says it is authenticated and has
    no primary key.
    """
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    if getattr(user, "pk", None) is None:
        return None
    return user


def record_export(
    request: HttpRequest,
    *,
    export: str,
    file_format: str,
    rows: int,
    params: QueryDict | None,
    honoured: Collection[str] = (),
    **extra: Any,
) -> SecurityAuditEvent | None:
    """One ``EXPORT_GENERATED`` row for a file this request is about to hand over.

    Called by the view after the file is built and before it is returned, so a
    refusal, a 404 or a 409 — which build nothing — write nothing, and a file
    that was built is on the trail even if the reader's connection drops
    halfway through it.

    ``export`` is the export's own name (``matters.register_table``), never a
    filename that could carry a title. ``rows`` is how many records the export
    selected. ``honoured`` is the export's own allowlist of the parameter names
    it reads (empty when ``params`` is ``None``). ``extra`` is for further
    non-content facts (a period's dates, a view's name) and nothing else.
    """
    from app.accounts import shared_gate
    from app.core.authorization import may_read_business_content
    from app.core.client_ip import client_ip
    from app.core.http import is_head

    if is_head(request):
        return None
    # Asked of the request's own user, as the export's population was: a reader
    # (or a grant, already logged once for this request) gets the digest, an
    # administrator without one, the shared-gate sentinel and anyone else does not.
    keyed = may_read_business_content(getattr(request, "user", None))
    return record_security_event(
        event_type=SecurityEventType.EXPORT_GENERATED,
        actor=_request_actor(request),
        ip_address=client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
        detail=shared_gate.audit_detail(
            request,
            export=export,
            format=file_format,
            rows=int(rows),
            method=request.method,
            **filter_summary(params, honoured=honoured, keyed=keyed),
            **extra,
        ),
    )


#: The attribute a request carries the grants it has already logged on. On the
#: request itself rather than in a context variable: the request is the unit
#: «once per request» counts, and it is gone when the request is.
_LOGGED_GRANTS = "_juristid_break_glass_logged"


def record_break_glass_use(user: Any, grant_id: Any) -> SecurityAuditEvent | None:
    """``BREAK_GLASS_USED``, the first time this request relies on this grant.

    Written only while a request is being served (`provenance.current_request`).
    A management command, a worker or a test calling the authorization helpers
    directly serves nobody, and «somebody's request read through a grant» is
    the fact this row states. The row names the grant, the path and the route,
    never what was read: the grant's reason is already on BREAK_GLASS_GRANTED.
    """
    from app.accounts import shared_gate
    from app.audit.provenance import current_request
    from app.core.client_ip import client_ip

    request = current_request()
    if request is None or grant_id is None:
        return None
    logged: set[Any] | None = getattr(request, _LOGGED_GRANTS, None)
    if logged is None:
        logged = set()
        setattr(request, _LOGGED_GRANTS, logged)
    if grant_id in logged:
        return None
    # Marked before the write: the write itself reads provenance, which must
    # not come back here and log the same grant a second time.
    logged.add(grant_id)

    from django.apps import apps

    grant = apps.get_model("accounts", "BreakGlassGrant")(pk=grant_id)
    match = getattr(request, "resolver_match", None)
    actor = user if getattr(user, "pk", None) is not None else None
    return record_security_event(
        event_type=SecurityEventType.BREAK_GLASS_USED,
        actor=actor,
        subject=grant,
        ip_address=client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
        detail=shared_gate.audit_detail(
            request,
            grant=str(grant_id),
            path=request.path,
            route=match.view_name if match is not None else "",
            method=request.method,
        ),
    )
