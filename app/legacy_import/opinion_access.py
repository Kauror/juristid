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

**Reading is no longer conditional on how the door was answered.** It used to
be: the shared gate widened the corpus to the department head, and outside it
only the administrator could read anything. That produced two problems the
department actually hit. A specialist — who reads every RESTRICTED Matter in
the department under ADR 0042, including the ones these letters are filed onto
— could not open the Chamber's own letter about a file they were working. And
the narrowing outside the shared gate meant the whole department would silently
lose the archive on the day Cloudflare Access replaced the shared password,
which is backwards: Access authenticates the individual *better*.

So `ARCHIVE_READERS` is one set, asked in every mode. What the shared gate can
honestly *say* about who read a letter is still limited, and the audit rows say
so themselves rather than the corpus being withheld to compensate: every
archive download records ``authenticated_via`` beside the persona
(app/accounts/shared_gate.py).

Writing did not move. Filing a letter onto a Matter and working the
reconciliation queue are unchanged in every mode, and they are now the only
things this module narrows — which is the shape it should have had:
**reading the department's own correspondence is not a privilege; asserting
what it concerns is** (docs/adr/0028, docs/adr/0042).
"""

from __future__ import annotations

from typing import Any

from app.accounts.enums import UserRole
from app.accounts.shared_gate import is_shared_gate
from app.core.authorization import acting_role

#: Roles that may read the corpus, in every authentication mode.
#:
#: The two lawyer roles, plus the administrator because operating the migration
#: is the job. It is deliberately `ROLES_WITH_RESTRICTED_ACCESS` **plus**
#: ADMINISTRATOR, and both halves of that are decisions.
#:
#: *Why the specialist is here.* ADR 0042 settled that the confidentiality
#: boundary is the application rather than the Matter, and put SPECIALIST and
#: DEPARTMENT_HEAD in one set that reads department-wide — including every
#: RESTRICTED Matter these letters are filed onto. This module predates that
#: and was left behind by it, and the gap was not theoretical: a specialist
#: could open the Matter, its timeline, its documents and their filenames, and
#: could not open the Chamber's own outgoing letter about it. Historical
#: outgoing opinions are ordinary department work product, not a migration
#: tool, and 767 of them were unreadable by the people whose work they are.
#:
#: *Why the administrator is still here, and what it is not.* Operating the
#: reconciliation requires reading what is being reconciled. It stays what it
#: always was — a statement about the **corpus**, never about a Matter — and
#: ADMINISTRATOR remains outside `ROLES_WITH_RESTRICTED_ACCESS`, so an
#: administrator who reaches an archive row still learns nothing about which
#: register entries they may open. Everything the archive renders about a
#: Matter goes through `Matter.objects.visible_to` exactly as before.
#:
#: *Why this is no longer a shared-gate special case.* The old rule widened
#: under the shared gate and narrowed to the administrator alone outside it,
#: which meant the department would silently lose the archive on the day
#: Cloudflare Access replaced the shared door — the opposite of what that
#: change is for. Access authenticates the individual *better*; it does not
#: make a lawyer less entitled to their own department's correspondence. One
#: set, asked in every mode, is also one fewer thing that can disagree with
#: itself.
#:
#: `READER` is absent, deliberately and for ADR 0042's reason: it is a
#: different audience with a different question behind it, and widening it is a
#: separate decision that is not taken here. `DepartmentViewer` — the shared
#: password with no persona chosen — has an empty role and is in no set.
ARCHIVE_READERS: frozenset[str] = frozenset(
    {
        UserRole.SPECIALIST.value,
        UserRole.DEPARTMENT_HEAD.value,
        UserRole.ADMINISTRATOR.value,
    }
)


#: Who may add or withdraw a reviewed archive-to-Matter relationship while the
#: department is behind the shared gate. The department head alone, and the
#: administrator's absence is the whole decision: a link is a business claim
#: about what the Chamber's correspondence concerns, and technical
#: administration becoming business authorship by accident is exactly what
#: `ROLES_WITH_BUSINESS_WRITE` refuses elsewhere (app/core/authorization.py).
SHARED_GATE_LINK_REVIEWERS: frozenset[str] = frozenset({UserRole.DEPARTMENT_HEAD.value})


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
    through `Matter.objects.visible_to` (docs/adr/0028).
    """
    role = acting_role(user)
    if not role:
        return False
    return role in ARCHIVE_READERS


def may_manage_archive_links(user: Any) -> bool:
    """Whether this person may record or withdraw a reviewed archive link.

    Never wider than reading: a reviewer has to be able to open the letter they
    are filing. It is deliberately much *narrower*, and the gap widened when
    reading did: three roles may now read the corpus, and only one of them is
    answerable for what the department's correspondence concerns. A specialist
    reading a historical letter is doing their job; a specialist asserting
    which Matter it belongs to is making a claim the department signs.
    """
    if not may_read_archive(user):
        return False
    role = acting_role(user)
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
    that only produces a 403 (Stage-2H brief 62, docs/adr/0028).
    """
    return acting_role(user) == UserRole.ADMINISTRATOR


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
