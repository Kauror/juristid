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
from app.core.authorization import acting_role, may_read_business_content

#: Who may read the corpus: since docs/adr/0181, exactly who may read any other
#: business content — `may_read_business_content`, asked in every mode.
#:
#: There used to be a set of its own here, `ARCHIVE_READERS`: the two lawyer
#: roles plus ADMINISTRATOR, "because operating the migration is the job", and
#: without READER. One internal read policy settles both halves. A READER is an
#: authorized internal user and reads the Chamber's own correspondence like
#: every other one. An administrator without a business role is technical
#: administration, which is not business access — the outgoing letters are the
#: department's work product, not a migration artefact — and reads the corpus
#: through an audited break-glass grant like any other business content. A
#: second set would have been a second answer to the same question, and the one
#: that disagreed would have been the one nobody noticed.
#:
#: The reconciliation queue (`may_use_opinion_queue`) and filing a letter onto
#: a Matter (`may_manage_archive_links`) are not reading, and are unchanged.


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
    return may_read_business_content(user)


def may_manage_archive_links(user: Any) -> bool:
    """Whether this person may record or withdraw a reviewed archive link.

    Never wider than reading: a reviewer has to be able to open the letter they
    are filing. It is deliberately much *narrower*, and the gap widened when
    reading did: every authorized internal user may now read the corpus, and
    only one role is answerable for what the department's correspondence
    concerns. A specialist reading a historical letter is doing their job; a
    specialist asserting which Matter it belongs to is making a claim the
    department signs.

    Outside the shared gate the reviewer is the administrator, and since
    docs/adr/0181 an administrator reads business content only through a
    break-glass grant — so the reading prerequisite below, unchanged, now asks
    for one. The link itself also needs the Matter, which `archive_link` reads
    through `visible_to` and which needs the same grant.
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
