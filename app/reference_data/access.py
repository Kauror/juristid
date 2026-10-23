"""Who may manage reference data, and who a change is recorded against.

One capability, `reference.manage` (docs/adr/0187 §3). The department head
holds it by default; a specialist or the technical administrator may be granted
it by name; a READER may not. It is resolved like every other capability, by
`app.core.authorization.has_capability`, so an inactive account, an anonymous
visitor and the shared gate's persona-less sentinel hold nothing.

It is deliberately not an account-administration capability: maintaining the
institution catalogue is department work, so a shared-gate persona that holds it
may use it, attributed to that persona with the session's own provenance
(docs/adr/0180). What it never grants is authorship of a Teema — it is outside
`REQUIRES_BUSINESS_WRITE` precisely so that a technical administrator can hold
it without becoming a business writer.
"""

from __future__ import annotations

from django.http import HttpRequest

from app.accounts.enums import Capability
from app.accounts.models import User
from app.core.authorization import has_capability


def may_manage_reference_data(user: object | None) -> bool:
    """Whether this person may change the catalogue and the tag vocabulary."""
    return has_capability(user, Capability.MANAGE_REFERENCE_DATA)


def reference_actor(request: HttpRequest) -> User | None:
    """The person a reference-data change in this request is made by, or nobody.

    `request.user` when it is a real account holding the capability. Failing
    that, the session's verified administrator (docs/adr/0186) when *that*
    account holds it: a technical administrator granted `reference.manage`
    picks no lawyer persona to do this work. Acting through the verified
    principal marks the request, so the audit rows it writes record that
    principal and `administrator_session` rather than the persona. Nobody
    else has an actor, and every route answers them with a 404.
    """
    from app.accounts import admin_session

    user = getattr(request, "user", None)
    if isinstance(user, User) and may_manage_reference_data(user):
        return user
    principal = admin_session.verified_administrator(request)
    if principal is None or not may_manage_reference_data(principal):
        return None
    # Haldus administration done through the verified principal, so it keeps
    # the administrator session's idle clock running like any Haldus page.
    admin_session.note_activity(request)
    actor = admin_session.administrator_actor(request)
    return actor.user if actor is not None else None
