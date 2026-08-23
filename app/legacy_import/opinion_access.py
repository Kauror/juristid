"""Who may read the opinion archive, and who may file its letters onto Matters.

The archive holds real outgoing Koda correspondence, including several hundred
letters nobody has yet filed onto a Matter. That combination is why its
authorization is written here rather than derived at each call site:

* there is **no Matter to inherit from**. Every other restricted surface in
  Juristid answers "may this person see this?" by joining the Matter and asking
  the live visibility rule. An unfiled letter has no such row, so the question
  has to be answered about the *corpus* instead of about the document;
* the answer therefore has to be **conservative and identical in every place**.
  A browse list, a detail page, a count in a header and a file download that
  each decided for themselves would eventually disagree, and the one that
  disagreed generously would be the one nobody noticed.

Three separate questions live here, and keeping them separate is the point.

**Reading the corpus** is administrative migration work — browsing, counting,
searching, opening the PDF.

**Filing a letter onto a Matter** is a business judgement. It says "this
evidence concerns this Matter", it is signed by whoever made it, and it is not
the same act as administering the system that stores it.

**Working the reconciliation queue** is a third thing entirely, unchanged by
this module's development-phase widening: that surface records candidate
decisions which a later apply may turn into canonical Submissions, and it stays
with the administrator in every mode.

The development-phase widening (docs/adr/0027) is the shared-gate branch below.
Until Cloudflare Access is in front of this deployment the department works
behind one shared password and a selected persona, and refusing the archive
outright left 767 held letters unreadable by anybody. What that mode may *say*
about who read a letter is limited, and the audit rows say so themselves rather
than the corpus being withheld to compensate: every archive download records
``authenticated_via`` beside the persona (app/accounts/shared_gate.py).

Outside shared gate nothing here is widened. The administrator remains the
archive reader and the link reviewer, exactly as before, so the Cloudflare
behaviour this deployment is heading for is decided later and on purpose.
"""

from __future__ import annotations

from typing import Any

from app.accounts.enums import UserRole
from app.accounts.shared_gate import is_shared_gate

#: Roles that may read the corpus while the department is behind the shared
#: gate. Both are people the department already trusts with the whole register:
#: the head by role, the administrator because operating the migration is the
#: job. Deliberately not a superset of anything — a specialist or a reader who
#: knows the shared password still gets nothing here.
SHARED_GATE_ARCHIVE_READERS: frozenset[str] = frozenset(
    {UserRole.DEPARTMENT_HEAD.value, UserRole.ADMINISTRATOR.value}
)

#: Who may add or withdraw a reviewed archive-to-Matter relationship while the
#: department is behind the shared gate. The department head alone, and the
#: administrator's absence is the whole decision: a link is a business claim
#: about what the Chamber's correspondence concerns, and technical
#: administration becoming business authorship by accident is exactly what
#: `ROLES_WITH_BUSINESS_WRITE` refuses elsewhere (app/core/authorization.py).
SHARED_GATE_LINK_REVIEWERS: frozenset[str] = frozenset({UserRole.DEPARTMENT_HEAD.value})


def _acting_role(user: Any) -> str:
    """The role a *person* is acting under, or "" when there is no person.

    The shared-gate sentinel, an anonymous visitor and a deactivated account all
    fall through to "", which is in none of the sets above. That is the property
    that keeps knowing the shared password from being enough on its own: a
    session that has passed the door but chosen no persona is a
    `DepartmentViewer`, which has an empty role and cannot be an audit actor.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return ""
    if not getattr(user, "is_active", True):
        return ""
    return str(getattr(user, "role", "") or "")


def may_read_archive(user: Any) -> bool:
    """Whether this person may see archive rows, text or files at all.

    All-or-nothing on purpose. There is no partial view of the archive to grant:
    a reader who may see the coverage figures can infer the corpus, and a reader
    who may see titles but not text can already read a letter's subject and
    recipient. Splitting it would produce boundaries that look like protection
    without being any.

    This answers a question about the **corpus**, never about a Matter. A reader
    who gets past it has learned nothing about which register entries they may
    open: everything the archive renders about a Matter is still filtered
    through `Matter.objects.visible_to` (docs/adr/0027).
    """
    role = _acting_role(user)
    if not role:
        return False
    if is_shared_gate():
        return role in SHARED_GATE_ARCHIVE_READERS
    return role == UserRole.ADMINISTRATOR


def may_manage_archive_links(user: Any) -> bool:
    """Whether this person may record or withdraw a reviewed archive link.

    Never wider than reading: a reviewer has to be able to open the letter they
    are filing. Under the shared gate it is deliberately *narrower*, because the
    two roles that may read the corpus are trusted with it for different
    reasons and only one of them is answerable for what the department's
    correspondence concerns.
    """
    if not may_read_archive(user):
        return False
    role = _acting_role(user)
    if is_shared_gate():
        return role in SHARED_GATE_LINK_REVIEWERS
    return role == UserRole.ADMINISTRATOR


def may_use_opinion_queue(user: Any) -> bool:
    """Whether this person may work the reconciliation queue.

    Unchanged in every mode, and it is not derived from archive access. The
    queue records decisions a later apply may turn into canonical Submissions —
    "the Chamber sent this opinion" — which is a stronger statement than
    anything the archive workspace can make. A department head who may read the
    archive and file its letters still does not get this, and the browse page
    asks this question before it renders the link rather than offering a button
    that only produces a 403 (Stage-2H brief 62, docs/adr/0027).
    """
    return _acting_role(user) == UserRole.ADMINISTRATOR


def require_archive_reader(user: Any) -> None:
    """Raise rather than return, for view code that must stop here."""
    from django.core.exceptions import PermissionDenied

    if not may_read_archive(user):
        raise PermissionDenied("Arvamuste arhiiv on halduse töövahend.")


def require_archive_link_reviewer(user: Any) -> None:
    """Raise unless this person may record archive-to-Matter relationships."""
    from django.core.exceptions import PermissionDenied

    if not may_manage_archive_links(user):
        raise PermissionDenied("Arhiivi seoseid saab muuta ülevaatuse õigusega kasutaja.")


def require_opinion_queue_operator(user: Any) -> None:
    """Raise unless this person may work the reconciliation queue."""
    from django.core.exceptions import PermissionDenied

    if not may_use_opinion_queue(user):
        raise PermissionDenied("Arvamuste ülevaatus on halduri töövahend.")
