"""An audited break-glass grant, for tests whose actor is technical administration.

Since docs/adr/0181 an administrator without a business role reads no business
content — not a Matter, not an archive letter, not a OneNote page — and the
reconciliation queues show nothing but business content. A test of the queues'
own behaviour therefore needs its operator to hold what a real operator would:
a time-bounded grant from the department head, made through the service that
audits it. One helper, so no test invents a cheaper way in.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from tests import factories


def grant_to(user: Any, *, granted_by: Any = None) -> Any:
    """Give ``user`` an active one-hour break-glass grant, and hand ``user`` back."""
    from app.accounts.services import grant_break_glass

    grant_break_glass(
        user=user,
        granted_by=granted_by or factories.DepartmentHeadFactory(),
        reason="Migratsiooni ülevaatus (test)",
        duration=timedelta(hours=1),
    )
    return user
