"""Who may read the opinion archive. One predicate, used everywhere.

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

The rule is the reconciliation queue's rule, and deliberately not one step
looser: reading the archive is administrative migration work. It is *stricter*
in one respect — the queue shows filenames, dates and references, whereas this
surface serves document text and the bytes themselves, so a shared department
password is not enough identity to stand behind it.
"""

from __future__ import annotations

from typing import Any

from app.accounts.enums import UserRole
from app.accounts.shared_gate import is_shared_gate


def may_read_archive(user: Any) -> bool:
    """Whether this person may see archive rows, text or files at all.

    All-or-nothing on purpose. There is no partial view of the archive to grant:
    a reader who may see the coverage figures can infer the corpus, and a reader
    who may see titles but not text can already read a letter's subject and
    recipient. Splitting it would produce boundaries that look like protection
    without being any.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if is_shared_gate():
        # A persona behind one shared password is a workable identity for
        # reading the Chamber's own filed work. It is not one for serving
        # unfiled real correspondence, because the audit row it produces names
        # a persona rather than a person.
        return False
    if not getattr(user, "is_active", True):
        return False
    return getattr(user, "role", "") == UserRole.ADMINISTRATOR


def require_archive_reader(user: Any) -> None:
    """Raise rather than return, for view code that must stop here."""
    from django.core.exceptions import PermissionDenied

    if not may_read_archive(user):
        raise PermissionDenied("Arvamuste arhiiv on halduri töövahend.")
