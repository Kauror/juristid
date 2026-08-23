# 0027 — The archive is readable behind the shared gate; the register is not

- **Status:** Accepted — implemented on the `feat/p33-development-archive-workspace` branch, pending integration
- **Number:** tentative. Agents F and H hold concurrent draft branches and may claim 0027 first; renumber on integration if so.
- **Date:** 2026-08-23
- **Builds on** ADR 0016 (authentication modes and the shared gate), ADR 0019 (the opinion archive and historical submissions), ADR 0023 (the searchable opinion archive).

## Context

Production holds the whole legacy opinion archive: 767 letters, 767 stored
binaries, 759 catalogue rows, 244 archive-to-Matter links, and 523 letters
nobody has filed onto anything. Every one of them is real outgoing Koda
correspondence.

The workspace for reading them was built in Stage 2H and 2H.2 and has been
complete for weeks — browse, filter, search the projection, open a detail page,
serve the PDF, add and withdraw a weak link. Nobody in the department has ever
seen it. `may_read_archive` opened with:

```python
if is_shared_gate():
    return False
```

That refusal was argued carefully and it was not wrong. This deployment runs in
`shared_gate` mode: one password the department shares, then a persona picked
from a list. It authenticates *the door*, not the person. Serving several
hundred unfiled letters against an audit row that names a persona rather than a
human looked like the kind of thing to refuse until Cloudflare Access is in
front of the application.

What that refusal cost was not visible in the code. Cloudflare Access has no
date. Meanwhile the archive's entire purpose — that somebody can find out what
the Chamber wrote about a subject, and that the 523 unfiled letters get filed —
was unreachable, and no other route to those bytes exists.

There is also a second, separate question the old code did not have to answer,
because refusing everybody answers it by accident. Once somebody *is* inside the
archive, the detail page renders Matters: the ones a letter is linked to, and
the ones reconciliation proposed. It loaded them straight off the foreign key.
For the one role that could reach the page — ADMINISTRATOR, which deliberately
does **not** carry RESTRICTED business access — that meant the archive was
printing the title, the reference and a working link for register entries its
reader could not open anywhere else in the product. The manual link form was
worse: it resolved a typed reference against `Matter` directly, so typing the
reference of a restricted entry produced a success message reading back its
title. Reading the archive had quietly become a route into the register.

## Decision

**Reading the corpus and reading the register are two different questions, and
P3.3 widens exactly one of them.**

### Who may read the corpus

`may_read_archive` is still the single predicate, still all-or-nothing, and now
branches on the mode:

| | shared gate | every other mode |
| --- | --- | --- |
| DEPARTMENT_HEAD | read | refused |
| ADMINISTRATOR | read | read |
| SPECIALIST, READER | refused | refused |
| no persona (`DepartmentViewer`) | refused | refused |
| inactive account | refused | refused |

The widening is temporary and its scope is the point: the two roles already
trusted with the whole register, and nobody else. A specialist who knows the
shared password gets nothing here, and neither does a session that has opened
the door and named nobody — which is the case that matters most, because
everybody in the department knows that password.

**Outside `shared_gate` nothing changed.** The department head is *not* granted
the archive under Cloudflare Access by this ADR. That grant exists because this
mode cannot say who is at the keyboard; under an identity provider the question
has a real answer and deserves to be decided on its own merits rather than
inherited from a workaround.

### The audit row carries the limitation instead of the corpus being withheld

The original objection — that an audit row naming a persona is not a record of
who read real correspondence — is still true. It is answered by making the row
say so rather than by keeping the letters unreadable. Every archive download now
goes through `shared_gate.audit_detail`, exactly as every other served file
does, recording `acting_as_user` (the selected persona) beside
`authenticated_via` (`SHARED_GATE`). The record is honest about how much
identity stands behind it. No schema was added and no new event type was
invented; the existing mechanism already said the true thing.

### Who may file a letter onto a Matter

Reading is one capability; recording that a letter *concerns* a Matter is
another, and it is a business claim. `may_manage_archive_links` is never wider
than `may_read_archive` and under the shared gate is deliberately narrower:

| | shared gate | every other mode |
| --- | --- | --- |
| DEPARTMENT_HEAD | read + link/unlink | refused |
| ADMINISTRATOR | read only | read + link/unlink |

The shared-gate administrator reads and does not file. This is the same
separation `ROLES_WITH_BUSINESS_WRITE` makes everywhere else in the product:
technical administration is not business authorship, and letting it become so by
accident — because the administrator happened to be the only role that could
already reach the page — is exactly the drift that rule exists to stop.

### What the archive may say about a Matter

**Nothing the register would not have said.** Archive access answers a question
about the corpus and confers no visibility on any register entry.

- The detail page resolves every linked and proposed Matter through
  `Matter.objects.visible_to(request.user)` — one query for the whole page, not
  one per relationship — and hands the template a `MatterView` that carries no
  object at all where the reader may not read it. A hidden Matter is not
  reachable from the template, so it cannot be rendered by mistake.
- A relationship the reader may not see is stated neutrally — *"Seos on olemas
  teemaga, mida selles kasutajavaates ei kuvata"* — and never suppressed. The
  page must not claim a letter is unfiled while the search projection records
  that it is filed; that would be a false statement rather than a discreet one.
- A candidate's archive-side facts (match class, state, explanation) stay
  visible, because they are the archive's own evidence. Its Matter's identity
  does not.
- Manual linking resolves inside the reviewer's visible population, by reference
  or by id. An unresolvable reference and an invisible one produce the same
  sentence, so the form cannot be used as an oracle to confirm a restricted
  entry exists.
- The department head reaches restricted Matters here for the ordinary reason —
  `ROLES_WITH_RESTRICTED_ACCESS` — and no archive-specific exception was written
  for them. `ROLES_WITH_RESTRICTED_ACCESS` is unchanged.

### What a link still is not

An `OpinionArchiveMatterLink` with basis `REVIEWED` says *this evidence concerns
this Matter*. It does not say Koda sent an opinion, and this workspace cannot be
made to say so: it creates no `Submission`, no `OpinionSubmissionImport`, no
sent date, and it does not move a candidate's state. Accepting a candidate's
suggestion with the one-click *Seo selle teemaga* files the letter and leaves
the candidate exactly as it was.

The 244 derived links production already holds keep their protection. They rest
on an exact byte identity, and a link a canonical Submission stands on cannot be
withdrawn here at all — a rule with nothing to defend today, because production
has zero canonical historical Submissions, and everything to defend once P4
creates some.

### The reconciliation queue did not move

`/haldus/arvamuste-ulevaatus/` is a different surface making a stronger claim:
its decisions include *confirm-sent*, which a later apply may turn into a
canonical Submission. It stays with the administrator in every mode. The archive
browse page asks `may_use_opinion_queue` before offering the link to it, rather
than assuming an archive reader is a queue operator and rendering a button whose
only possible outcome is a 403.

### Search says what it can do

Every one of the 767 held texts is in state `BLOCKED` with no body, so archive
search is metadata search. The page now says so: the *Sisu olemas* filter is not
rendered when no text exists anywhere, and the search box does not offer to look
inside letters it has not read. Nothing about extraction policy changed — the
controls reappear on their own wherever bodies exist. A reader who searched a
phrase, found nothing, and concluded the Chamber never wrote it would have been
misled by an interface promising a capability the corpus does not have.

## Consequences

- The department can read its own archive today rather than after an
  identity-provider migration with no date.
- Two capabilities and one queue predicate now live in
  `app/legacy_import/opinion_access.py`. Every view calls them; the navigation
  and the templates call the same functions to decide what to *offer*. Hiding a
  link is presentation — a crafted URL still gets a 403.
- No migration. No model, field, enum or audit event was added, which also keeps
  this branch off the migration graph two concurrent branches are editing.
- This is development-phase access with a scheduled end. When Cloudflare Access
  lands, the `is_shared_gate()` branch in `may_read_archive` and
  `may_manage_archive_links` is the whole thing to revisit, and the table above
  is what to argue against.
