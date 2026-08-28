from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from django.conf import settings
from django.http import HttpRequest
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from app.accounts import shared_gate
from app.accounts.selectors import persona_candidates
from app.core.authorization import is_department_head, may_write_business_content


@lru_cache(maxsize=8)
def parse_built_at(raw: str) -> datetime | None:
    """The build stamp as an aware datetime, or nothing at all.

    Cached on the string rather than computed once at import, so that a test
    or a management command can change the setting and get the new answer
    without reaching into module state. The value cannot change while the
    process lives anyway: it describes the image the process was started from.

    A stamp that will not parse becomes ``None`` rather than reaching the page
    as raw text. The footer's job is to answer "what is running here" quickly
    and correctly; half-legible output there costs more than a missing line,
    which at least prompts somebody to look.

    The image writes UTC. Rendering goes through Django's date filter, which
    converts to ``TIME_ZONE``, so the footer reads in Tallinn time without the
    build having to know where it would be read.
    """
    try:
        parsed = parse_datetime((raw or "").strip())
    except ValueError:
        # Shaped like a datetime but not one — `2026-13-45T99:99:99Z`.
        return None
    if parsed is None:
        return None
    return parsed if timezone.is_aware(parsed) else parsed.replace(tzinfo=UTC)


def application(request: HttpRequest) -> dict[str, Any]:
    return {
        "application_name": settings.APPLICATION_NAME,
        "application_stage": settings.APPLICATION_STAGE,
        "application_environment": settings.APPLICATION_ENVIRONMENT,
        "application_revision": settings.APPLICATION_REVISION,
        "application_built_at": parse_built_at(settings.APPLICATION_BUILT_AT),
        "real_data_allowed": settings.REAL_DATA_ALLOWED,
        "dev_login_enabled": settings.DEV_LOGIN_ENABLED,
        # The header renders differently for somebody who is past the shared
        # door but has chosen no persona: they get the department, and a clear
        # way to say whose work they want (Stage-2D auth brief 7).
        "auth_mode": shared_gate.current_mode(),
        "shared_gate_mode": shared_gate.is_shared_gate(),
        "gate_passed": shared_gate.has_passed(request),
        # The people the top-bar switcher may offer, and the one currently
        # selected. Both only ever reach a template in shared-gate mode, and the
        # queryset is lazy — the query runs when the popover is rendered and not
        # otherwise, so the other two modes pay nothing for it.
        #
        # The same central population the switch endpoint accepts a POST
        # against. Two lists that agree today are two lists that can stop
        # agreeing, and the one that matters is the endpoint's
        # (app/accounts/selectors.py, docs/adr/0034).
        "persona_candidates": persona_candidates() if shared_gate.is_shared_gate() else (),
        # The identifier rather than the object, because the popover compares it
        # against every row and `request.user` is already in the context under
        # its own name.
        "current_persona_id": _current_persona_id(request),
        # Whether to offer the department-head surface in the navigation.
        # Computed here rather than compared in the template: a template that
        # asks `user.role == "DEPARTMENT_HEAD"` puts a copy of an authorization
        # rule in a file nothing type-checks, and a typo there fails open by
        # rendering nothing rather than loudly. The route enforces the same
        # check again — this only decides whether the link is shown.
        "is_department_head": is_department_head(getattr(request, "user", None)),
        # `can_read_opinion_archive` used to live here, for the second bar item
        # that opened the administrative archive browse. That item is gone —
        # one `Arvamused` destination, with the held corpus as its own tab
        # inside the workspace — and the tab is decided by the workspace, which
        # calls `may_read_archive` itself with the viewer it resolved
        # (app/submissions/workspace_views.py, docs/adr/0044). Nothing about
        # access moved: every archive view asks again and refuses a crafted URL
        # with a 403 regardless (app/legacy_import/opinion_access.py).
        #
        # Whether to offer "+ Uus teema" on the bar and in the empty states.
        # Same reasoning again, and the same predicate `matter_create` now
        # calls: a READER may read the register and change nothing in it, and
        # offering them a button that answers 404 is a page telling somebody to
        # try something it knows will fail (app/matters/views.py).
        "can_write_business_content": may_write_business_content(getattr(request, "user", None)),
    }


def _current_persona_id(request: HttpRequest) -> Any:
    """The selected persona's primary key, or nothing.

    `None` rather than the empty string: the template compares it with a row's
    `pk`, and a comparison against "" would be false for every row — which is
    correct today and silently correct for the wrong reason.
    """
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return getattr(user, "pk", None)
