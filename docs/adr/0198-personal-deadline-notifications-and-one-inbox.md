# ADR 0198 — Personal deadline notifications and one inbox

- Status: Proposed (owner's notifications package brief of 2026-10-30, features A, B and D; owner decisions D1–D4 of the same day)
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
service, broker or cron. A marked hook in the loop is where the daily Muudatused
snapshot (D4, another branch) is called at integration.

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
