# ADR 0198 — Personal deadline notifications and one inbox

- Status: Proposed (owner's notifications package brief of 2026-10-30, features A, B and D; owner decisions D1–D4 of the same day); merged 2026-10-30 in #575 (merge `e94f3cff`) and deployed the same day
- Amends: 0188 §4 and §6 (owner decision D1: no seven-day reminder row is generated; the surfaces are unified) and 0051 («no bell, no badge»: «Uus asi» is now shown in the unified block and counted by the bell)
- Related, unchanged: 0050 (an open Järgmiseks outranks the Arvamuse tähtaeg as today's work), 0079 (dates at the precision they are known to), 0120 (informational milestones), 0181 (one internal read policy), 0194 (remembered blocks — the preference pattern)

## Context

Juristid knew every dated obligation — an `Arvamuse tähtaeg`, a feedback round's
reply-by date, a DO + DEADLINE step, a commencement, an `Oluline tähtaeg`, a review
date — and told nobody in advance. The only notices were «Uus asi» (ADR 0051) and
«Kontrolli jõustumist» (ADR 0188), in two separate rail blocks, and only the second
had the bell. The owner asked for personal, configurable advance notices, a
management notice the morning after a missed deadline, and one place to read them.

## Decision

### 1. One row is one delivery to one account

`notifications.DeadlineNotification`: `recipient`, `matter` (scoping, title, cascade),
`category`, `source_type` + `source_id`, `source_date` (the date it was generated
for), `purpose` (`ADVANCE`, `ON_DAY`, `ESCALATION` — a purpose rather than a magic
negative offset), `offset_days` (*N* for an advance row, 0 otherwise), `kind`
(`ACTION` «Vajab tegevust» / `INFO` «Teadmiseks»), `due_on`, `read_at`,
`dismissed_at`, `retired_at` + `retired_reason`. No business text is copied.

Unique on `(recipient, category, source_type, source_id, source_date, purpose,
offset_days)`, **not partial**: one row per interval per source date, ever, so a
rerun or a second worker never delivers an interval twice. A changed date is a new
`source_date` and a new schedule. **Revival (review of 2026-10-30):** when the
interval owed *today* is held by the recipient's own row retired as `REASSIGNED`,
`DATE_CHANGED`, `INELIGIBLE` or `DISCHARGED` — the Teema handed away and back, the
date moved away and back, the person briefly inactive, the step reopened — that row
is un-retired and shown as new (`read_at` cleared) instead of being lost. A hidden
(`dismissed_at`) row is never revived, nor a `SUPERSEDED` one, and only today's
interval is ever touched, so an expired interval is never replayed. Check constraints keep
the vocabularies, tie `offset_days` to `purpose`, allow `dismissed_at` only on INFO
rows, and require a reason with every retirement.

### 2. Six categories, one registry (`app/notifications/categories.py`)

| Category | Source | Population (as the recipient) | Recipient | Default |
|---|---|---|---|---|
| `OPINION_DEADLINE` | `Matter.response_deadline` | advance: `response_obligations` (the obligation, not the plan); the day itself: `outstanding_response_deadlines` (ADR 0050 precedence) | active owner | on, 7/3/1 + day |
| `FEEDBACK_DEADLINE` | `MatterEngagement.feedback_deadline` | `dated_feedback_waits` | active owner | on, 3 + day |
| `ACTION_DEADLINE` | `NextAction.target_date` | current step, DO + DEADLINE, day precision | the step's active `responsible`, else the active owner (D2) | on, 3/1 + day |
| `COMMENCEMENT_CHECK` | `MatterEffectiveDate` | ADR 0188 `eligibility_q` | active owner | on, 7; the day is the existing check (D1) |
| `IMPORTANT_DATE` | `MatterImportantDate` | `important_deadlines`, day precision | active owner | **off**, 7/1 + day |
| `REVIEW_DATE` | NextAction WAIT/MONITOR, `REVIEW_ON` / `EXPECTED_AROUND` | current step, day precision | as for a step | **off**, day only; INFO, «On aeg üle vaadata» |

Allowed intervals are {14, 7, 3, 2, 1} plus «tähtpäeval». Approximate precisions
(MONTH … YEAR) never produce a row. An unowned Matter, or an inactive person,
produces no personal row and is never broadcast. A planned (`PLANNED`) step is not
yet a deadline and notifies nobody.

Each population is a function of the reader and is **evaluated as the recipient**:
the generator asks it once per department worker, and every list asks it as the
signed-in reader to re-validate stored rows live. Under ADR 0181 that is the whole
department's population; asking as the recipient means a narrower read rule later
narrows notifications with it.

### 3. The schedule (Europe/Tallinn calendar, `timezone.localdate()`)

**Owner decision, 2026-10-30 («Hide under Järgmiseks»).** An `Arvamuse tähtaeg`'s
on-the-day «Vajab tegevust» row follows ADR 0050's precedence: while the Matter has
a visible open Järgmiseks other than a WAIT, the deadline is not today's work, so
the row is neither generated nor shown nor counted, and an existing one is retired
as `INELIGIBLE`. Its population is `outstanding_response_deadlines`, read, not
copied, by the generator and the live read path alike. If the step ends while the
deadline is still unanswered and the day is still inside its window, the row is
owed again and revived unread (§1). The advance reminders keep following
`response_obligations`; the escalation already uses the operational definition.

For a source on *D*, chosen intervals *N₁ > … > Nₖ* and today *t*:

- **Advance:** only the interval whose window holds *t* — the smallest *Nᵢ ≥ D − t*.
  With {7, 3, 1}: 7…4 days to go owe the 7-day row, 3…2 the 3-day row, 1 the 1-day
  row. A restart, first deployment, reassignment or late opt-in never replays
  expired intervals. A later interval, or the day itself, retires an earlier
  advance row as `SUPERSEDED`.
- **On the day:** one row on any day *D … D + 7* while the source is outstanding
  (ACTION; INFO for a review). Not for a commencement: the `CommencementReminder`
  `ON_DATE` check is that item (D1).
- **Escalation (D3):** `OPINION_DEADLINE` and `ACTION_DEADLINE` only, using the
  operational overdue definition (`outstanding_response_deadlines`, `overdue_q`). One
  INFO row «Tähtaeg ületatud» per holder of `department.view_management` (active,
  department worker) on any day *D + 1 … D + 7*, once per (source, date, holder).
  OWNER-DECISION: a holder who is themselves responsible gets no escalation of their
  own deadline — they have the on-the-day row. A holder can switch it off (its own
  preference key, shown only to holders, on by default).

- **No first-deployment flood (review of 2026-10-30):** a day *D* before the
  feature's start owes no on-the-day row and no escalation. The start is the Tallinn
  date of the earliest row ever created (rows are never deleted), or today while the
  table is empty — no setting, no new state. After the start, the seven-day catch-up
  for a worker that was down applies as above.
- **One commencement prompt at a time:** while a legacy open `SEVEN_DAYS`
  `CommencementReminder` exists for the same commencement and date, no
  `COMMENCEMENT_CHECK` advance row is generated or shown — the legacy row is that
  prompt.

### 4. Generation and retirement

`reconcile_deadline_notifications(today=None, *, dry_run=False)` under a new
transaction advisory lock `(24609, 1)` (24606–24608 are taken by vocabulary
administration, the Lovable sync and the daily Muudatused snapshot), inserting with
`ON CONFLICT DO NOTHING` and counting what it inserted, revived and retired from the
writes themselves;
idempotent and concurrency-safe. Windows are bounded (sources dated within
*today − 7 … today + 14*). Each run retires outstanding rows whose source no longer
matches, naming why: `DISCHARGED` (same date, same person, no longer outstanding —
sent, answered, completed, closed), `DATE_CHANGED`, `REASSIGNED` (another person is
responsible now), `INELIGIBLE` (no day any more, gone, or the recipient no longer
does department work or holds management), `SUPERSEDED`. Every list re-validates
live, so the state is right before the worker runs. Rows are never deleted except
with their Teema or account.

The existing `notifications` Compose service runs both reconciles in each loop
turn (`generate_commencement_reminders`, name kept); `--check` covers both. No new
service, broker or cron. The same turn then calls
`reporting.selectors.snapshots.capture_daily_if_missing` (D4, docs/adr/0159 §7):
the daily Muudatused photograph, once per Tallinn day from 03:00, in its own
transaction, so a failure there stops neither reconcile.

### 5. Preferences

`NotificationPreference(user, category, enabled, offsets, on_day)` and
`NotificationSettings(user, paused, email_requested)`. Only explicit choices are
stored (a save that restates the defaults stores nothing); «Taasta vaikimisi»
deletes the rows. A disabled category, or a pause, generates nothing for that
person. A disabled category **hides** its INFO rows and keeps its on-the-day ACTION
rows (still-true work); a pause hides every deadline row. Neither retires anything:
switching back shows what is still true. Pausing does not hide commencement checks
or «Uus asi» — they are workflow items. Business views are untouched.

E-mail is a stored wish. The control is disabled with «E-posti teavitused
lisanduvad isikupõhise sisselogimisega.»; `deliver_email` refuses unless
`AUTH_MODE` is an individual mode **and** `NOTIFICATION_EMAIL_ENABLED` (default off),
and no mail backend is called anywhere.

`/teavitused/seaded/` is gated like a remembered block (ADR 0194): `login_required`,
the signed-in person only; a READER may keep their own preferences.

### 6. One inbox

`app.notifications.inbox.inbox_for(user)` is the union of (1) the reader's live,
undismissed deadline rows, shown as their preferences say; (2) open commencement
checks (ADR 0188 rules: own, plus unowned for management); (3) unread «Uus asi»
(ADR 0051 rules). `inbox_count` counts the same three populations with three
`COUNT`s. The bell (its own async request, no zero badge, now «Teavitused»), the one
Minu asjad rail block (first five items + «Kõik teavitused») and `/teavitused/`
read it, so they agree by construction. Order: by date (the deadline; the day of a
hand-over), «Vajab tegevust» before «Teadmiseks» on the same date, then title. The
list page shows «Vajab tegevust», «Teadmiseks», the deferred checks (not counted)
and, for management, «Vastutaja puudub».

The Minu asjad block replaces the «Uus asi» and «Kontrolli jõustumist» blocks, in the
`is_self` branch only: a colleague's desk never queries it. Each item keeps its own
answers — «Uus asi» its POST «Ava» acknowledge (a GET still never acknowledges),
commencement checks «Lükka edasi» / «Sulge…», deadline rows «Märgi loetuks» and, for
INFO only, «Peida». Reading is not hiding; opening the Teema link is a GET and marks
nothing.

### 7. Authorization and audit

Read and hide resolve rows by `recipient = request.user` (another person's id is a
404), call no business service and write no `ChangeEvent`: they are presentation
state of the account, like a remembered block. An administrator without a business
role sees nothing (`may_read_business_content` and `visible_to`). The commencement
answers keep `@business_write_required` and their events.

## Consequences

- One additive migration, `notifications/0002` (three tables; a partial index on
  outstanding rows per recipient serves the generator and the bell). No audit vocabulary
  change. Reversible: drop the includes; the tables are history only.
- Existing `CommencementReminder` rows, including open seven-day rows, keep working
  and keep their history; only new seven-day rows stop.
- The bell costs three `COUNT`s per page view in its own request; Minu asjad gains
  the inbox's statements (one per source plus the correlated `Exists` re-validation).
- The Lovable sync (ADR 0192) changes owners and dates through ordinary services;
  notifications follow the canonical state with no special case.

## Amendment 2026-11-02 — «Teadmiseks» is read off the list; «Peida» retired

Owner's brief of 2026-11-02. Proposed, not yet accepted; unmerged at the time of writing. §6 and §7 change as follows; everything else stands.

- **«Märgi loetuks» on a «Teadmiseks» row takes it out of the list and the count.** `inbox.deadline_rows` leaves out an `INFO` row whose `read_at` is set, so the bell, the Minu asjad block and `/teavitused/` drop it together. The row is not deleted, hidden or retired — `read_at` is its history — and nothing about the work changes. The press is one conditional `UPDATE`, so a repeat is a no-op. Each interval is its own row (§1), so reading the 7-day notice leaves the 3-day one owed.
- **A «Vajab tegevust» row is unchanged**: «Märgi loetuks» clears its unread mark and it stays listed until its work is done.
- **«Peida» is retired.** Its route `notifications:hide` is removed, so a stale POST to `/teavitused/teade/<id>/peida/` is a 404 and writes nothing. Rows hidden before keep `dismissed_at`, stay out of every list and are still never revived by the generator (`deadlines._deliver`); `deadlines.dismiss` stays for that history and its tests.
- **Commencement checks**: «Märgi loetuks» and «Lükka edasi» are both personal state of the reader; «Sulge…» is retired (ADR 0188 amendment 2026-11-02). The «Edasi lükatud» section lists the reader's own postponements and any global deferral recorded before that day.
- **Copy**: the list page loses the line «Sinu tähtajad, jõustumise kontrollid ja sulle määratud teemad.» and the note explaining what a commencement check means; «Teavitused» and «Teavituste seaded» stay. Item actions sit on their own line directly under the item, not at the far edge of the page.
- **Surfaces still agree by construction**: every action is a CSRF POST that redirects (PRG), and the bell refetches its count on the next page load, so there is no client-side count to drift.

## Amendment 2026-11-06 — the bell panel (UI-01)

Superseded in part by [0212](0212-the-november-2026-ui-round-and-its-combined-release.md) §1: «Märgi loetuks» on a «Vajab tegevust» row now also takes it out of the bell, the Minu asjad block and the inbox sections (its deadline stays on the Teema and in the work lists); the bell opens a panel of unread items and history; «Märgi lugemata» and «Märgi kõik loetuks» are added. The rest of this ADR, the 2026-11-02 amendment included, stands.

## Amendment 2026-11-12 — `REVIEW_DATE` reads `REVIEW_KINDS`; the opinion deadline follows ADR 0050's SOURCE rule

The `REVIEW_DATE` row of the table in §2 named WAIT and MONITOR. Its population now reads `app.workflow.enums.REVIEW_KINDS`, which since r54 also holds Lovable's unclassified step (`ActionKind.SOURCE`, «Liik määramata», docs/adr/0213). Such a step is a review date like the other two: told on its day (INFO, «On aeg üle vaadata») and at any advance interval the person chose, never late, never escalated. The hand-written pair had left it out, so synchronised steps were never notified (2026-11-11 audit, DL-01). The category's help text now names «Liik määramata» beside «Ootel» and «Jälgin».

A syntax-tree test (`tests/test_review_due_rule.py::test_no_module_lists_the_review_kinds_by_hand`) now refuses, in every module of `app/` except `app/workflow/enums.py` and migrations: a literal tuple, list or set of two or more `ActionKind` review members (`.value` included); any literal naming a review kind passed to a `kind__in` or `…__kind__in` lookup, as a keyword or through `**{…}`, one member included; a membership test (`in` / `not in`) against a literal naming two review kinds, or one when the left side reads a kind; and a `Q(kind=…) | Q(kind=…)` chain naming two. A literal that also names `DO` is the whole vocabulary and is not refused. One listing is allowed by name, with its reason in the test: the finished evidence-pilot package validator (`app/legacy_import/lovable_pilot.py`, `{"WAIT", "MONITOR"}`), whose package format predates SOURCE.

**On the first run after release.** `REVIEW_DATE` stays off by default, so nobody who has not switched it on is affected. For a person who has, the first reconcile after the release delivers the day's row for every current SOURCE step of theirs whose `REVIEW_ON` day is today or one of the seven days before it (`CATCH_UP_DAYS`), as INFO rows at once, and any advance interval they chose for the days ahead. A one-time burst of information, never late and never escalated; the catch-up rule itself is unchanged.

`OPINION_DEADLINE`'s on-the-day row and its escalation already read `outstanding_response_deadlines`, so they follow ADR 0050's amendment of the same day without a change here: an open SOURCE step, like a WAIT, no longer takes an outstanding `Arvamuse tähtaeg` off the day or out of the escalation (DL-02). Defaults, wording and every other category are unchanged.

## Amendment 2026-11-12 (TL-02) — a seventh category, `CLOSURE_HOLD`

The owner's TL-02 decision of 12.11.2026 (docs/adr/0213 §10.13) holds a closure from Lovable while the Teema still has Juristid-only work, and escalates a hold left unresolved for 14 days to the responsible lawyer and the department head. It uses this package rather than a second notice mechanism:

| Category | Source | Population (as the recipient) | Recipient | Default |
|---|---|---|---|---|
| `CLOSURE_HOLD` «Ootel sulgemine» | the Teema (`MATTER`); its date is the hold's escalation day (`escalates_on`, the hold's first night + 14) | open FULL Teemad the recipient owns whose newest reading holds the closure (`lovable_closure_hold.with_escalation_day`) | active owner; escalation to every holder of `department.view_management` | on, the day only; ACTION «Lovable'i sulgemine ootab pooleli töö tõttu» |

- **The day** is the 14th day of the hold: the owner's on-the-day «Vajab tegevust» row, catch-up *D … D + 7* as for every category.
- **Escalation** follows D3 unchanged: one INFO row per holder on *D + 1 … D + 7*, «Sulgemine ootel üle 14 päeva» (`Category.escalation_label`, the other categories keep «Tähtaeg ületatud»), never to a holder who is the owner, behind the same `OVERDUE_ESCALATION` preference.
- **Keys and retirement** are §1 and §4 unchanged. The source date is the escalation day, carried night to night while the hold stands, so a re-run of the sync or of the reconcile never delivers twice; a new hold after a gap is a new date. When the hold ends (the work settled, the Teema closed, Lovable reopened) the rows retire as `DISCHARGED`: the category's `state` reads the newest hold's day, standing or not.
- **Migration** `notifications` 0004 widens the category CHECK constraint (additive). An image rollback after a hold has escalated retires those rows first, because an older image does not know the category.
