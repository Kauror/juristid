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

    `request.user` when it is a real account holding the capability. The
    integrator adds the verified-administrator fallback once Haldus has one
    (Admin & Workflow R1 contract); until then a request without a capable
    account has no actor, and every route answers it with a 404.
    """
    user = getattr(request, "user", None)
    if isinstance(user, User) and may_manage_reference_data(user):
        return user
    return None
