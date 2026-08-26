# ADR 0037 — Where business-write authorization is enforced

- Status: accepted
- Date: 2026-08-26
- Stage: pre-QA (shared-gate development phase)
- Related: ADR 0005 (authorization and visibility inheritance), ADR 0016
  (authentication modes and the shared gate), ADR 0018 (structured Matter facts
  — where `may_write_business_content` was introduced), ADR 0034 (who may be a
  persona), ADR 0036 (who work may be assigned to)

## Context

The rule was never in doubt. `app/core/authorization.py` has said for a long
time that SPECIALIST and DEPARTMENT_HEAD may author business content, that
READER may not, and — the part that matters most — that ADMINISTRATOR may not
either, because technical administration is not business authorship
(master specification 5.1, 5.2). It is one function, `may_write_business_content`,
and it is correct.

What was missing was that most mutating HTTP routes never called it.

A security revalidation reproduced, against current `main`:

```
ADMINISTRATOR → POST /teemad/<pk>/vali/owner/       → owner changed
ADMINISTRATOR → POST /teemad/<pk>/jargmiseks/       → NextAction created
READER        → POST /teemad/<pk>/vali/owner/       → owner changed
READER        → POST /teemad/<pk>/jargmiseks/       → NextAction created
```

An inventory of Django's own routing table found the shape of it: of 42
POST-capable application routes, **fifteen** class-A business mutations had no
actor check at all. Not a subtle bug in the rule — a door with no lock, next to
seventeen doors that had one. A READER could register incoming material, upload
evidence, create a Chamber opinion and mark it sent.

Three things had been masking it. The shared-gate persona list already refuses
ADMINISTRATOR and READER (ADR 0034), so those accounts do not appear in ordinary
navigation. The templates hide most write controls behind `can_write`. And the
register's own visibility rules stop an actor reaching a Matter they may not
read. None of the three is an authorization boundary: the first is UX and will
stop applying the day individual authentication resolves a real `User`, the
second is a rendering decision that a crafted POST ignores, and the third
answers a different question.

## Decision

**One HTTP boundary, holding no rule of its own.**
`app/core/decorators.py` gains `@business_write_required`. It calls
`may_write_business_content` and nothing else. The policy stays in
`app/core/authorization.py`; the decorator is only where it is applied. No view
copies the role set, and no view invents a second answer to the same question.

**Applied at the route, before the view body.** Not after the object is fetched,
not after an upload is parsed, not after a form validates. An unauthorized
caller must not reach a partially-applied write, and must not be able to spend
the server's time on one.

**404, not 403.** `app/matters/views.py` had already made this choice nine times
and written down why: a reader who may not write is not told which surfaces
exist for those who may. A 403 answers "you could do this with another role",
which is a description of the application handed to the one caller who should
learn nothing from it. `app/intelligence/views.py` had been answering 403 for
the identical question; it now answers 404 too. Both were secure — the pair was
not, because the difference between them was itself information.

**Composed inside `@login_required` and outside `@require_http_methods`.**
Anonymous still gets a sign-in redirect rather than a 404, because an expired
session is not an authorization failure. And a non-writer gets the same 404 for
every verb — a 405 on a POST-only route would confirm the endpoint exists.

**Three questions stay three questions.** May this actor write at all
(this ADR); may this actor see this object (`visible_to`, ADR 0005); may this
person receive work (`app/accounts/selectors.py`, ADR 0036). The gate is an
*additional* check, never a replacement: every route still fetches its object
through the visibility chokepoint, and child records still carry their own
overrides.

**The view layer, not the service layer.** Domain services are also called by
the legacy import, the register cutover, the JÄRGMISEKS enrichment, the owner
backfill and the seed tooling, none of which has an HTTP actor and none of which
should have to argue with an authorization check to reconstruct a historical
fact. Putting the gate in the services would have forced a `bypass=True` flag,
and a bypass flag is an authorization boundary with a documented way through it.
The defect was native HTTP mutation by unauthorized actors, so the fix is at the
native HTTP boundary.

**Class B keeps its stricter rule and its 403.** Confirming or rejecting a work
victory is department-head only via `may_review_work_victory`, and because
DEPARTMENT_HEAD is a subset of the business writers that check cannot be
satisfied by anybody this ADR would otherwise have had to stop. It answers a
different question — to somebody who may already write and may already see the
record — so it keeps a different answer.

## Consequences

Fifteen routes that were writable by a READER or an ADMINISTRATOR no longer are:
`intake`, `set_action`, `complete_action`, `review_action`, `update_field`,
`set_data_class`, `save_note`, `reopen`, `upload_evidence`, `add_version`,
`submissions.create`, `attach_evidence`, `mark_sent`, `withdraw`, and
`organisations.quick_create`.

READER remains a reader — the role keeps every read surface it had, which is
tested, because closing this finding by locking the role out of the application
would have been fixing it by deleting the feature. ADMINISTRATOR keeps its
technical reach; it is narrowed on authorship only.

`tests/test_business_write_boundary.py` is the durable part. It fires every
forbidden actor at every class-A route and asserts three things each time: the
refusal, that the state did not move, and that no `ChangeEvent` claims the
action happened. Thirty-six of those cases fail on the commit before this one,
which is what makes them a regression test rather than a description.

It also walks the URL resolver and fails if any POST-capable route is neither in
the matrix nor in an explicitly classified exemption. A future mutating view
cannot arrive unguarded by accident: whoever adds it has to either gate it or
write down, in that file, why it does not need gating.

## What this does not do

It does not touch AUTH-003 (restricted child detail in projections), DATA-001 or
DATA-002 (final-evidence integrity), or SEARCH-001 (index freshness). It does
not change role definitions, read visibility, assignment eligibility, closure
semantics or any import path. It adds no migration.
