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
(`may_manage_archive_links`) and the reconciliation queues
(`may_use_opinion_queue`, `may_review_historical_matches`) are unchanged — but
what those administrator surfaces *show* of business content now needs the
administrator's grant (see Consequences).

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
  opinion reconciliation queues, archive filing) still open, but the Matters,
  letters and pages they display or link need a grant or a business role.
- Choosing *Ilma kasutajata* still drops the persona and records the change; the
  page it returns to now asks for a persona instead of showing a department
  view. The label could be retired in a later round (`app/accounts` and the
  persona templates belong to the authentication workstream).
- Visual baselines may move: a persona-less Osakond capture becomes the persona
  chooser, and the restricted banner's sentence changed.
- Tests that asserted the old internal secrecy (READER or a gate visitor
  refused RESTRICTED work; restricted children hidden from a reader) assert the
  new policy; every test of the external perimeter stays.
