"""What `Piiratud` actually means, in one sentence, written once.

The pilot found three separate explanations of restricted visibility on three
surfaces, and all three said the same untrue thing: that a restricted Teema is
seen only by its owner, its collaborators and the department head (pilot QA
F-01). The corrected sentence then said "every lawyer in the department", which
was true under docs/adr/0042 and stopped being the whole truth with
docs/adr/0181.

**What it means now.** Juristid is internal, and every authorized internal user
reads all business content: `ROLES_WITH_BUSINESS_READ` in
`app/core/authorization.py` — SPECIALIST, DEPARTMENT_HEAD and READER — and an
actor with an audited break-glass grant. `Piiratud` is no longer an access
boundary between those people. It is a label: the record says its material is
sensitive, and the people reading it should treat it so. Nobody outside the
application sees it, and neither does anybody inside the door who has not said
who they are.

A lawyer deciding whether to mark confidential member feedback as `Piiratud`
must not be promised a narrower audience than the system provides — the one
direction this copy must never be wrong in. So the sentence says plainly that
colleagues see it.

**One string, three surfaces.** The two templates read this through
`{% restricted_visibility_help %}` and the two forms use it as their
`help_text`. `tests/test_pilot_identity_and_visibility.py` pins the sentence.

The wording names no role and no mechanism. "Kõik rakenduse kasutajad" is what a
reader understands; `ROLES_WITH_BUSINESS_READ` is what the code calls it.
"""

from __future__ import annotations

#: The one explanation of `Piiratud`, and the only one.
#:
#: Three facts: what the mark is, who sees the record (everyone inside), and who
#: does not (anyone outside). Nothing about how any of it is enforced.
RESTRICTED_VISIBILITY_HELP = (
    "Piiratud on tundliku sisu märge, mitte ligipääsupiirang: teemat ja selle "
    "sissekandeid, arvamusi ja tõendeid näevad kõik rakenduse kasutajad. "
    "Väljapoole rakendust teema ei paista."
)
