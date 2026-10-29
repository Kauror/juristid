# ADR 0181 — One internal read policy for all business content

- Status: Proposed (security hardening round 2, 2026-10-22; Codex Security
  findings csf_5e02eab006c718a477f410e0, csf_0e7751e7ba29ea88bd84e0f4,
  csf_a37ab56bd8c3c9f369703fc8)
- Owner decision: Juristid is internal. Every authorized internal user reads all
  business content; everybody else sees nothing. Read is not write.
- Amends: 0016 (the department scope), 0028 (who reads the opinion archive),
  0034 (*Ilma kasutajata*), 0042 (READER, ADMINISTRATOR), 0137–0139, 0146 §10
  and 0162 (restriction inheritance — kept as labels, no longer a boundary
  between authorized users), 0175 (the archive reporting guard)
- Does not change: authentication, the shared gate, personas and persona
  selection, every write, administrative, delete and victory-review capability,
  audit provenance (0180), the database (no migrations), the search index

## Context

Since 0042 the two lawyer roles read department-wide and `RESTRICTED` divided
the legal team from everybody else. Three groups remained on the far side of
that line, each by a separate rule:

- **READER** saw NORMAL records only. 0042 left what a READER may see as "a
  separate decision nobody has taken".
- **ADMINISTRATOR** saw NORMAL records, and the whole opinion archive through its
  own set (`ARCHIVE_READERS`, 0028), because "operating the migration is the
  job".
- **A shared-gate session with no persona** (`DepartmentViewer`, 0016) saw every
  NORMAL record — Osakond, all of Statistika and its CSV exports, Arvamused, and
  every document download, preview and thumbnail route, which were
  `@gate_required` rather than `@login_required`. The shared password alone
  opened the department's NORMAL work.

A round-2 static scan then reported two places where the NORMAL/RESTRICTED line
leaked between *authorized* readers: an email attachment that could lose the
restriction of its parent email (csf_0e7751e7…), and historical OneNote
projections whose metadata and counts included restricted documents
(csf_a37ab56b…). A third finding (csf_5e02eab0…) is that the shared password
lets anybody assume any persona.

Fixing the first two literally would mean a nested restriction engine —
attachment inherits email inherits Matter, counts minus every restricted
descendant — to protect a line the owner does not want between colleagues.

## Decision

### 1. One predicate

`app.core.authorization.may_read_business_content(user)` answers "is this an
authorized internal user" and nothing else asks it differently:

| Who | Reads business content |
|---|---|
| active account acting as SPECIALIST, DEPARTMENT_HEAD or READER — the chosen persona under the shared gate; the account Cloudflare Access or the dormant personal sign-in authenticated | **everything**, NORMAL and RESTRICTED |
| any other active account (ADMINISTRATOR, `is_superuser` without a business role, an unknown role) with an active break-glass grant | **everything**, while the grant lasts (audited, unchanged) |
| the same without a grant | **nothing** |
| anonymous; inactive, pending or invited account; the shared-gate sentinel (password typed, no persona) | **nothing** |

`ROLES_WITH_BUSINESS_READ` = {SPECIALIST, DEPARTMENT_HEAD, READER} replaces
`ROLES_WITH_RESTRICTED_ACCESS`. READER is an internal read-only role and the
owner's policy decides what 0042 left open: it reads.
`ROLES_WITH_BUSINESS_WRITE` is unchanged and READER is still not in it.

### 2. The scope is all-or-nothing, through the existing chokepoint

`scope_for_user` builds its `Scope` from the same facts in the same order: no
acting role → `no_scope()` (every `Q` builder answers `NOTHING`); a business-read
role → sees all (every builder answers `Q()`, no break-glass lookup); any other
role → sees all with a grant, nothing without. Every `visible_to` manager, the
derived-record rules (`app/core/derived_visibility.py`), the search projection's
`projected_visibility_q`, the change-event scope, the reporting selectors
(including `historical.py`) and the CSV exports already resolve through
`scope_for_user`, so they follow without an edit. No `.all()` was added, no
decorator removed, no builder rewritten: their NORMAL-only and participation
branches remain for a hand-built scope and are unreachable from a request.

`department_scope()` is replaced by `no_scope()`.

### 3. The opinion archive and its statistics ask the same question

`may_read_archive` is `may_read_business_content`; `ARCHIVE_READERS` is gone.
`reads_opinion_archive` (0175) calls `may_read_archive`, so the Statistika
archive metrics follow too. A READER now reads the archive; an administrator
reads it only through break-glass. Filing a letter onto a Matter
(`may_manage_archive_links`) and who works the reconciliation queues
(`may_use_opinion_queue`, `may_review_historical_matches`) are unchanged.

**The reconciliation queues are business content too.** Their rows are OneNote
pages (title, section, size, file count), archive letters (title, recipient,
date, hash) and the Matters proposed for them. So every queue route — both lists
and both decision routes — asks two questions: who works the queue (unchanged,
the administrator), and `may_read_business_content`. An administrator without a
grant gets a 403 (`QUEUE_NEEDS_A_READER`) before anything is read; with an
active break-glass grant the queue works as before. The links that offer a
queue — Statistika's reconciliation rows (`can_open_review_queue` →
`may_open_historical_review`), the opinion-coverage drill-through and the
archive's «Ava ülevaatus» (`may_open_opinion_queue`) — ask the same pair, so
none is a door to a refusal. The queue's own «Ava arhiiv» is offered only when
the archive would open.

### 4. A persona is required before any business page

`@gate_required` is retired. Its 20 views (Osakond and Muudatused, every
Statistika tab, drill-through and export, Arvamused Saadetud/Arhiivikirjad and
the embedded block, document detail, download, inline open and thumbnail) are
`@login_required`, like every other view. A gate-passed session with no persona
is redirected to the persona chooser (`LOGIN_URL` under the shared gate; an
htmx request gets the existing 401 from `HtmxSignInMiddleware`). The sentinel
still exists because `shared_gate.viewer` hands it out, and it now maps to the
empty scope, so a view that forgot its decorator would render nothing.

What stays reachable without a persona: the gate, the persona chooser and
`act_as`, sign-out, `healthz`, static files, the favicon and **Uuendused**
(`/uuendused/`) — product release notes, which hold no business data and were
never `login_required`. No edit to `app/accounts/*` was needed.
`tests/test_business_routes_need_a_reader.py` walks the URL resolver and holds
this for an anonymous visitor, a persona-less gate session and an administrator
without a business role.

### 5. `RESTRICTED` stays — as a label

`Matter.visibility` and every `visibility_override` column stay, with their
values, constraints and the evidence-integrity triggers (0040); 0137–0139 and
0162 keep copying a restriction onto derived records at creation. Nothing reads
them as an access boundary between authorized users any more. No row is
migrated or rewritten.

The UI keeps the `Piiratud` badge, chip and banner: the mark is true and useful
— it says the material is sensitive. What changes is the one sentence that
explains it (`app/core/visibility_help.py`), which used to promise an audience
of "all the department's lawyers and the department head": it now says that
`Piiratud` marks sensitive content, that every user of the application sees it,
and that nothing outside the application does. This is the smallest honest
change — removing the control would discard a sensitivity mark the department
uses, and keeping the old sentence would promise a narrower audience than the
system provides.

### 6. Search needs no rebuild

`SearchDocument` stores no visibility (`app/search/models.py`); the projection
is filtered by a live join through `projected_visibility_q`. `INDEX_VERSION` is
unchanged.

## The three findings

- **csf_5e02eab0… (persona assumption)** — accepted design, unchanged. The shared
  gate proves membership, not identity (0016); 0180 records how every actor
  arrived. Under this policy a persona choice changes *whose desk* is shown and
  *who is attributed* for a write, never *what may be read*: every persona reads
  the same content. Write and review capabilities still follow the persona's
  role, which is the residual risk 0016 and 0180 already accept.
- **csf_0e7751e7… (attachment loses its email's restriction)** — not an internal
  breach under this policy: every authorized user reads both the email and the
  attachment. Tests prove that every authorized role gets identical results for
  a NORMAL parent with a previously RESTRICTED child and for an attachment of a
  previously RESTRICTED email, and that every unauthorized actor gets neither.
- **csf_a37ab56b… (historical metadata and counts)** — the same: the historical
  page, its raw XML, the resource counts, the Statistika historical metrics and
  their CSV are identical for every authorized user and empty or refused for
  everybody else.

## Consequences

- A READER sees RESTRICTED work and the opinion archive; still writes nothing,
  still has no victory review.
- An administrator without a business role sees **nothing** of the business —
  not even NORMAL records — unless a break-glass grant is active. Under the
  shared gate this changes nothing (an administrator is never a persona, 0034).
  In the dormant modes, the administrator's migration tools (historical and
  opinion reconciliation queues, archive filing) refuse with a 403 until a
  break-glass grant is active, and then work as before.
- *Ilma kasutajata* is no longer offered as a choice — neither as a row on the
  persona page nor in the top-bar popover. Without a persona nothing of the
  department is shown, so a persona-less "view" would only open the persona
  page again; offering it as a destination was the one place the interface
  still claimed otherwise. Selecting a persona is unchanged, and switching
  between colleagues stays where it was. Ending the session is «Välju». The
  heading «Praegu: Ilma kasutajata» and the dashed pill still *name* the state
  while nobody is chosen — a state, not an offer. Template-only: `act_as`
  still accepts an empty choice (it is the authentication workstream's, and a
  stale form or bookmark must not 500), and it lands on the persona page.
- Visual baselines that move: the persona page with somebody selected
  (`persona-leht-valitud`) and with nobody (`persona-leht-ilma`) lose the
  dashed row; the open popover (`persona-popover-avatud`,
  `persona-popover-aktiivne`, `hele-persona-popover-avatud`,
  `persona-ilma-popover`) loses its last option; the restricted banner's
  sentence changed on any Teema capture of a RESTRICTED Matter.
- Tests that asserted the old internal secrecy (READER or a gate visitor
  refused RESTRICTED work; restricted children hidden from a reader) assert the
  new policy; every test of the external perimeter stays.

## Consistency note 2026-10-25

Re-checked against `app/core/authorization.py` at main `c32608155e05`: the
policy above is what the code does. `may_read_business_content` answers yes
for an acting role in `ROLES_WITH_BUSINESS_READ` (SPECIALIST, DEPARTMENT_HEAD,
READER) or an active break-glass grant, and no for every other case through
`acting_role`; READER is still outside `ROLES_WITH_BUSINESS_WRITE`.

One reading of §1's table needs a qualifier: a READER is never *offered* as a
persona behind the shared gate — the persona list is SPECIALIST and
DEPARTMENT_HEAD only (`PERSONA_ROLES`, `app/accounts/selectors.py`). Behind the
gate a READER account reads through its own individually verified MCP grant
(ADR 0185 as revised 2026-10-23), or in a mode that authenticates the person.
Technical administration still grants no business read: the verified
administrator session of ADR 0186 changes neither `request.user` nor the
persona, so it opens Haldus and nothing of the business.

## Status update 2026-10-29 — merged and deployed

This section records what happened after the decision; it does not amend the decision, and the status is unchanged (no formal acceptance was recorded).

- **Merged** in security hardening round 2 (merge `b6b27abc, #519 = #515–#518, 2026-10-22`).
- **Deployed** to production as `1270ca01` (2026-10-23, the release of #521, which carried the security baseline merged before it).
- The code still matches the policy (see the consistency note of 2026-10-25 above); the master specification's §5.1/§5.2 now describe it and point here (2026-10-29).
