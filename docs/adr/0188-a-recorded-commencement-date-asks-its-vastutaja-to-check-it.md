# ADR 0188 — A recorded commencement date asks its Vastutaja to check it

- Status: Proposed (owner's brief «Admin & Workflow R1», workstream C, 2026-10-23)
- Amends: 0051 (one sentence: «no bell and no counter anywhere else» — §6), 0158 §6 and
  0071 («a commencement is nobody's deadline … reaches Minu asjad» — §7)
- Related, unchanged: 0018 (structured facts, precision vocabulary), 0079 (dates at the
  precision they are known to; `INFERRED` behaves as a day), 0120 (informational
  milestones), 0180 (audit provenance), 0181 (one internal read policy)

## Context

`MatterEffectiveDate` records when an act, or part of it, commences. Nothing in
Juristid ever asked anybody whether that actually happened: the date passes, the
register moves the row to «Möödunud», and whether the act entered into force on
it — or was amended, postponed, never adopted — is checked only if a lawyer
remembers. The owner asked for a prompt to *check*, never an automatic legal
determination (brief §5).

## Decision

### 1. One small app, one model

`app.notifications` holds `CommencementReminder`: the source commencement, the
`source_date` it was generated for, the `stage`, `due_on`, `status`,
`deferred_until`, who answered and when, a dismissal reason and an obsolescence
reason. Unique on `(effective_date, source_date, stage)` among rows that are not
`OBSOLETE`, so a date edited away and restored (D1 → D2 → D1, EXACT → MONTH →
EXACT) is asked about again beside its retired history. No recipient column, no
copy of any title or description, no generic event table, no channel, no
preference, no frequency setting, no e-mail or push.

### 2. Which commencements are eligible

`KNOWN_DATE`, `ACTIVE`, not removed, on a Teema that is not a tombstone, at a
**day** precision — `EXACT`, or `INFERRED` (ADR 0079 §8). Never `GENERAL_ORDER`
or `UNKNOWN` (no date by constraint), never a month, quarter, half-year or year
(no day to remind on, and its first day is an anchor, not a fact). Open and
closed Teemad alike (ADR 0158 §2: commencements often land after closure).
Stated once, `services.eligibility_q`, and read by the generator and every list.

### 3. Draft and estimated commencements are left out

`Oluline tähtaeg` rows of kind `PLANNED_COMMENCEMENT` / `ESTIMATED_COMMENCEMENT`
get no reminder. ADR 0120 (amendments of 2026-10-19/20) made those milestones
*inform* — never the next step, never late. A reminder would be the first work
they generate, which is a product decision, not an implementation detail. If the
owner wants «Kontrolli kavandatud jõustumist» later it is additive: a nullable
second source column, a one-of check constraint and its own label.

### 4. Two stages on the Europe/Tallinn calendar

For a commencement on day *D*: `SEVEN_DAYS`, due *D − 7*, generated on any day
*D − 7 … D − 1*; `ON_DATE`, due *D*, generated *D … D + 7* only while nobody has
answered that date. The on-the-day row replaces an unanswered seven-day row
(`SUPERSEDED`, kept) and starts undeferred. The seven-day
catch-up makes a generator that was down over the day still ask, without the
first deployment prompting for every historical commencement. «Today» is
`timezone.localdate()` (Europe/Tallinn), never the UTC date.

An answer — `ACKNOWLEDGED` («Kontrollitud») or `DISMISSED` (with a reason) — is
about the *date*: it closes every open stage of that `(commencement, date)` and
no later stage is generated for it. A moved commencement is a new date and is
asked about again; the old rows become `OBSOLETE` (`DATE_CHANGED`), kept. A
cancelled, superseded or removed commencement, or one made approximate, retires
its open rows (`NOT_ACTIVE`, `REMOVED`, `NOT_EXACT`). Nothing is deleted except by
the permanent deletion of the whole Teema, which cascades.

Defer: hides an open prompt until a Tallinn date, at most 30 days ahead; it stays
`OPEN`. A seven-day prompt is deferred at most until *D* (a later date is capped),
so the on-the-day prompt is never swallowed.

### 5. Reminders follow current responsibility

The recipient is the Matter's *active* `owner`, resolved when a list is drawn.
Reassigning the Matter moves its open reminders on the next request, with no
write. No owner, or an inactive one: nobody is guessed; the prompt is listed for
holders of `department.view_management` (`/teavitused/#vastutajata`, the bell's
count, and `_department_block.html` in the Osakond rail).

Every list is also re-validated live (eligibility and `source_date = date_value`),
so a moved or cancelled commencement disappears at once, before the generator
retires it. Every population starts from `MatterEffectiveDate.objects.visible_to`.

### 6. Surfaces

- **Bell** (`{% commencement_bell %}`, `templates/notifications/_topbar_bell.html`):
  a link to `/teavitused/`. Its number is a second request (`notifications:bell`)
  fired `hx-trigger="load"`, so the bar adds **no query** to the render of any page.
  Nothing is shown for zero. This amends ADR 0051's «no bell and no counter
  anywhere else»: that sentence described «Uus asi», which stays as it is.
- **List** `/teavitused/` (namespace `notifications`): own prompts, own deferred
  prompts, and — for management — unowned prompts. Each row: «Kontrolli
  jõustumist», the Teema (link to `#joustumine`), what commences, «Kirjas olev
  jõustumise kuupäev», how far off, the official source link if recorded.
- **Minu asjad** (`{% commencement_reminders_block %}`): a rail block in the
  `is_self` branch only; absent when empty.
- Answers are POST forms (Kontrollitud / Lükka edasi / Sulge…), PRG to a fixed
  return map — never a caller-supplied URL.

No surface says the act entered into force. The past is «kirjas olev kuupäev on
möödunud».

### 7. Not a deadline

ADR 0158 §6 / 0071 said a commencement «reaches no Minu asjad». It now reaches
Minu asjad as a *check to do*, nothing more: no `NextAction`, not «üle aja», not
in any overdue or this-week figure, not in statistics.

### 8. Authorization and audit

Reading: `@login_required` (ADR 0181) — a persona-less gate session is sent to
choose a persona; an administrator without a business role reads empty lists.
Answering: `@business_write_required` — READER and ADMINISTRATOR get 404, like
every business write; any business writer who can read the Matter may answer
(collaborative maintenance, `may_write_business_content`), and the department
head can answer the unowned ones. Each answer writes a `ChangeEvent`
(`COMMENCEMENT_CHECK_ACKNOWLEDGED` / `_DEFERRED` / `_DISMISSED`) whose subject is
the `MatterEffectiveDate` — scoped with its family in `app.audit.visibility`,
absent from `Teema käik` (audit, not chronology), shown in «Kõik muudatused».
Provenance is ADR 0180's: under the shared gate the actor is the *chosen* persona
(`shared_gate` / `shared_secret`, no principal) — attributable, not verified.

### 9. Generation and concurrency

`reconcile_commencement_reminders(today=None, *, dry_run=False) -> ReconcileResult`
is pure and idempotent. It and every answer take one transaction-scoped advisory
lock (`24605, 1`); the unique constraint backs it. Two workers, a restart or a
person answering during a run serialise; the second finds the work done. An
answer on a seven-day row the generator just replaced answers the open
on-the-day row.

No lazy generation on page views: a GET never writes. The command
`generate_commencement_reminders` (one summary line; `--check` exits 1 when a run
is owed; `--loop` every 600 s) runs in a small `notifications` Compose service,
the same shape as `intake-sweeper` — no host cron (the Unraid cron is generated
from the flash drive), no broker. With the live re-validation of §5, the only
latency is for *new* prompts: at most one interval after midnight or after a
date is recorded.

## Consequences

- One additive table (`notifications/0001`), one AlterField on
  `ChangeEvent.event_type` choices (`audit/0039_r1_admin_reference_reminder_events`). No change to
  `app/matters` or `app/intelligence` schema.
- The bell costs one `COUNT` per page view in its own request; Minu asjad gains
  one or two statements.
- If the worker is not deployed nothing is generated, and nothing else breaks.
- Reversible: drop the service and the includes; the table is history only.

## Status update 2026-10-29 — merged and deployed

This section records what happened after the decision; it does not amend the decision, and the status is unchanged (no formal acceptance was recorded).

- **Merged** 2026-10-23 in #522 (merge `6e195631`, Admin & Workflow R1; migrations `notifications/0001`, `audit/0039`).
- **Deployed** as production `6e195631` (2026-10-23).
- **Activated**: the `notifications` worker is a service in `deploy/unraid-main/compose.yml` and was one of the five application services of that release.
