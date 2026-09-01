"""What `Piiratud` actually means, in one sentence, written once.

The pilot found three separate explanations of restricted visibility on three
surfaces, and all three said the same untrue thing: that a restricted Teema is
seen only by its owner, its collaborators and the department head. It has not
been true since docs/adr/0042, which made the confidentiality boundary the
*application* rather than the Matter: every lawyer in the department reads
department-wide, whether or not they are named on the file.

A lawyer deciding whether to mark confidential member feedback as `Piiratud`
was therefore being promised a narrower audience than the system provides,
which is the one direction this copy must never be wrong in (pilot QA F-01).

**The population, from the code rather than from memory.** A restricted Matter
is readable by:

* every account holding a legal-department role — `ROLES_WITH_RESTRICTED_ACCESS`
  in `app/core/authorization.py`, which is `SPECIALIST` and `DEPARTMENT_HEAD`;
* the Matter's owner and its named collaborators, whatever role they hold
  (`restricted_participation_q`);
* an actor holding an active break-glass grant, which is audited and is not an
  ordinary way in.

And by nobody else: not `READER`, not `ADMINISTRATOR`, and not somebody who is
behind the shared door without having said who they are.

**One string, three surfaces.** Three sentences maintained separately is how
they drifted apart in the first place, so the two templates read this through
`{% restricted_visibility_help %}` and the two forms use it as their
`help_text`. `tests/test_restricted_visibility_copy.py` refuses a fourth copy.

The wording deliberately names no role and no mechanism. "Osakonna juristid" is
what the department calls itself; `SPECIALIST` is what the database calls it,
and a lawyer reading a help line should not have to know that.
"""

from __future__ import annotations

#: The one explanation of `Piiratud`, and the only one.
#:
#: Three facts in three clauses: who does see it, who does not, and that the
#: child records follow. Nothing about how any of it is enforced.
RESTRICTED_VISIBILITY_HELP = (
    "Piiratud teemat näevad kõik osakonna juristid ja osakonnajuht, samuti "
    "teemale märgitud vastutaja ja kaastöötajad. Väljapoole osakonda teema ei "
    "paista. Sissekanded, arvamused ja tõendid pärivad sama piirangu."
)

#: The banner's opening, kept beside the sentence it introduces so the two are
#: read — and changed — together.
RESTRICTED_VISIBILITY_BANNER_TITLE = "Piiratud nähtavus."
