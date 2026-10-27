# 0192 — The Lovable pilot's Teemad follow Lovable nightly, one way

- Status: Proposed (owner's nightly-synchronisation brief, 2026-10-27; owner
  answers in chat the same day: a profile-less service login, recurring transfer
  approved narrowly, comparison-only first)
- Extends: 0191 (the 100-Matter pilot), 0184 (import integrity and approvals)
- Relates to: 0157 (Lovable mirror, PR #429 — still on hold, not merged), 0120
  (informational commencements are not work; amended 2026-10-27)

## Context

0191 imported 100 current Teemad from a reviewed snapshot of the department's
Lovable register, once. Lawyers keep working in Lovable until the final cutover;
the owner wants their changes to reach Juristid's test environment the next
morning wherever a change can be mapped safely, and nowhere else. **This
changes 0191's one-time import into a bounded, recurring, one-way
synchronisation** of exactly that cohort.

Until cutover: Lovable is authoritative for operational register data, OneNote
for historical documents and evidence, and Juristid is a synchronised test
environment. Nothing flows back to Lovable.

## Decision

### 1. Scope

The cohort is the pilot's 100 Teemad, found by their pilot provenance
(`EvidencePilotRecord(pilot="lovable-pilot", record_kind="MATTER")` and the
canonical `MatterSourceReference(source_system="LOVABLE")`). The 13 Teemad that
existed before are never in it, whatever their references. A run whose cohort is
not exactly 100 stops. No Teema is created, closed, reopened or deleted.

The reader also **counts** — never acts on — live Lovable rows outside the
cohort, rows new since the previous read, operational rows with no Juristid
Teema, cohort rows missing from the source and cohort rows whose `nr` changed.
Adding a cohort later is a different selection, not a different engine.

### 2. The source connection

`app.legacy_import.lovable_source`: Lovable's own HTTPS API (PostgREST), signed in
as a **dedicated service login with no `user_profiles` row**. Checked against the
deployed source on 2026-10-27:

- the row-level rules let any signed-in user read live topics, their
  commencements, responsible changes and stage links (`topic_source_row` is not
  read);
- every write path (`save_topic`, `save_topic_with_stages`, `delete_topic`,
  `restore_topic` and the `testala_*` functions) goes through `topic_writer()`
  or `caller_is_admin()`, which refuse a caller with no profile; there are no
  INSERT/UPDATE/DELETE policies on the topic tables;
- change history (`topic_change_event`, `topic_change_field`) and deleted
  topics are admin-only.

So the identity reads what the job needs and nothing more, and **no Lovable code
changes**. The reader issues `GET` against a fixed allowlist of tables and
columns (`markmed` and `mitteametlik_arvamus` are not read), filters only by
UUID lists, refuses a token whose `role` is not `authenticated` and a
`sb_secret_` or non-anon key, and bounds every request (HTTPS only, 30 s
timeout, page size, 8 MB response cap, row cap).

`lovable_sync access-probe`, run with the real account before activation,
proves the owner's four conditions: it reads the topics and their children;
every write RPC and a PATCH/DELETE on `topic` are refused (each aimed at a
random UUID that cannot exist, so even a wrongly permitted path finds nothing to
change); admin history and deleted topics stay hidden; `can_access_admin_view`
is false and `topic_writer` refuses. If any check fails the job is not enabled
and the account is not widened.

The credentials live in `/mnt/user/appdata/juristid-main/config/lovable-sync.env`
(root, 0600) on KOCORP, written by the owner. Docker reads the file as root
(`--env-file`) for the one-off container; it is never in Git, an image, CI, a
log or a report.

### 3. Snapshots

Each run reads the cohort's rows and children and an index of every live
topic, then reads both again; a version or content hash that moved between the
passes is an edit made while reading, and the whole reading is repeated (three
attempts, then the run fails `INCONSISTENT`). `topic.updated_at` is never relied
on alone. The snapshot keeps each row's UUID, `nr`, version and timestamps, the
allowlisted fields, the child rows' identities, the time, the mapping version
and a canonical SHA-256.

Snapshots are stored in the Juristid database (`LovableSyncSnapshot`), which
`Kontrolli muudatusi` reads without reaching Lovable, and are deleted after 30
days (the newest is always kept) — the retention the owner approved for
recurring transfer on 2026-10-27, scoped to this cohort. Host-side files under
`l100/sync` hold only run logs with counts.

### 4. Three-way reconciliation

Per field: **A** the source value Juristid last accepted, **B** Juristid now,
**C** the source tonight. A is seeded from the pilot's immutable provenance —
the raw row in `MatterSourceReference.source_row_raw`, the reviewed title, the
pilot's step as its own `NEXT_ACTION_SET` event recorded it (kind, date meaning,
day, package treatment — a step whose day was since changed in place by «Vaatasin
üle» or a reschedule reads as a local change) and the import owner (the first
hand-over's `from`, or the owner if never handed over) — never from the current
row, which may already carry a lawyer's edit.

| | |
|---|---|
| A = C, B = A | UNCHANGED |
| A = C, B ≠ A | LOCAL_TEST_CHANGE — kept |
| A ≠ C, B = A, mapping exact | ELIGIBLE → APPLIED |
| A ≠ C, B = C | ALREADY_CONVERGED — the baseline advances, no write |
| A ≠ C, B ≠ A, B ≠ C | CONFLICT — nothing overwritten |
| no baseline, no mapping, held field | REVIEW_REQUIRED |

Comparison uses canonical values: whitespace-collapsed NFC text, ISO dates, and
for names Lovable's own `raw_norm`. **Last applied and last observed are kept
apart** (`LovableSyncState.applied` / `.observed`): a held change stays pending
every night until the source returns, Juristid converges, or an operator
resolves it (`lovable_sync resolve`, which records a decision). A held field
whose Juristid value already equals the source's converges like any other. A
converged next-step sentence makes the lawyer's step the baseline; an operator's
resolution does so only when the step reads as the source's sentence, so the
source can never move a lawyer's own step's day. A resolved owner is never
learnt as a name → person pair. Fields the pilot
snapshot did not carry start their baseline at first observation as report-only
fields.

### 5. The allowlist

Automatic, when A is reliable, B = A and the existing service supports it:

| Field | Rule | Service |
|---|---|---|
| Title (`teema`) | only when the pilot kept the source's sentence as the title | `set_matter_title(provenance=…)` |
| Owner (operative `vastutaja`/`uus_vastutaja`) | resolves to exactly one active, assignable user — first through the pilot's reviewed name pairs, then an exact display name | `assign_matter(provenance=…)` (moves the step that follows the file) |
| Day of the current step (`tegevuse_tahtaeg`) | `jargmiseks` unchanged; the open step is still the pilot's `SOURCE_NEXT_STEP` step with its date precision `EXACT`; the new day is not cleared and not before today | `set_next_action` with the same text, kind, date meaning, responsible, plan step and restriction (`carry_*`) |
| OneNote link | an `https://` link | a **new** `MatterSourceReference` observation; the pilot's stays as it was; the Teema links the newest |

Held for a person, always: new `jargmiseks` wording (a new sentence is a new
DO/WAIT/MONITOR reading, and the old kind is never carried over) and its day;
`hetkeseis` (no approved stage mapping exists — 0191 keeps it NEVER_AUTO; a
deterministic, owner-approved mapping may later join the allowlist without
changing the engine); `arvamuse_tahtaeg`; `valja`/`valja_kuupaev` (a source
assertion, never a SENT opinion); `kellele`; `koja_ettepanek`; `oigusakt`;
`sisse`; commencements (an estimate never becomes work — 0120); operational
scope; a cleared OneNote link; anything on a closed or deleted Teema; source
deletion and identifier changes, which stop that Teema only.

Not read or reconciled: `markmed` (source context, never `Märkmed`),
`mitteametlik_arvamus`, the change history (no fictional user events).

### 6. Writes and audit

One Teema at a time, all of its eligible fields or none, in a transaction that
first takes the Matter's lock and **plans again under it**: a save that landed
after the first reading turns the write into a CONFLICT or a LOCAL_TEST_CHANGE.
Order: title, owner, step. Every write uses `actor=None` and a provenance of
`provenance_source="LOVABLE_SYNC"`, the run, the source reference and version,
the snapshot digest and the mapping version, on the service's own event — no
event is backdated, and no task is completed, reminder acknowledged, opinion
sent or activity created. Each applied field gets a `LovableSyncDecision` with
a unique idempotency key (source id, field, source version, C): a value may come
back, a Lovable version never does. Search projections are
refreshed for the written Teemad (`refresh_matters`). After commit each written
Teema is read back against its new baseline; a mismatch fails the run.

### 7. Idempotency, concurrency and breakers

The same snapshot twice writes nothing the second time: the baseline already
says C. One runner at a time (`pg_try_advisory_lock(24607, 1)`, and `flock` on
the host). The nightly run key is the Tallinn date, so a second scheduled run
the same date refuses. One Teema's failure is isolated — rolled back whole — and
the run is then `FAILED` (`PARTIAL_FAILURE`), never a success; a broken snapshot,
a cohort that is not 100, more than 5 missing rows, or a failed read-back stops
the run. A night with more than **20** Teemad to write, or more than **10**
conflicts **more than the previous night** (they persist until resolved), writes
nothing (`REVIEW_ONLY`, `CIRCUIT_BREAKER`).

### 8. Schedule and backups

A cron file on the KOCORP flash (`/boot/config/plugins/dynamix/
juristid-lovable-sync.cron`) runs `scripts/deploy/juristid-lovable-sync.sh` at
**21:15 and 22:15 UTC**; the host keeps UTC with no Tallinn zone, so the
container (`lovable_sync due`) says which firing is 00:15 Europe/Tallinn —
summer or winter — and the other exits. The script: one runner, the host pause
file, no release in progress, web healthy, the credentials file root-only, due,
then in APPLY mode a fresh backup set **and its level-2 verification**, then a
short-lived container of the image the site is serving (`--cpus 1 --memory 1g
--pids-limit 256`, 20-minute timeout) on the existing internal network. No
compose service, database, tunnel or other KOCORP service is touched. The run
accepts only a set named for a moment at most two hours before it started;
without one it writes nothing (`REVIEW_ONLY`, `NO_VERIFIED_BACKUP`). The script
takes the release host lock for its duration, so no release starts under a run,
and runs the serving image by id.
GitHub Actions never runs the job.

### 9. Control, visibility, cutover

`LovableSyncControl` (one row): `OFF` (paused), `COMPARE` (report only) or
`APPLY`. Activation is COMPARE first; APPLY only after the comparison has been
reviewed (§10). `lovable_sync control --cut-over` records final cutover: mode
OFF for good — `save` refuses to clear it and a database constraint keeps a cut
over row OFF; then the cron file is removed.

Haldus → **Lovable sünkroonimine** (technical administrator, like Süsteemi seis):
the verdict, last attempt and last success, snapshot time, verification, the
night's counts, `Kontrolli muudatusi` (compares the newest stored snapshot with
Juristid and records only its own findings: no Teema, baseline, observation or
snapshot changes), and the server commands. A pending change's wording
is shown only for a Teema the reader may open (`Matter.objects.visible_to`);
otherwise it is counted. `production_status` gains a *Lovable sync* row: red
when the last run failed or was held, when a run never finished, or when there
was no success in 26 hours — an earlier success is never shown as current.

### 10. Activation

A. synthetic tests (CI never sees real data) → B. a read-only production
comparison (COMPARE run; no business writes) → C. the same run in APPLY mode on
an isolated clone of the production database, checked for provenance,
idempotency and rollback → D. deploy, cron installed in COMPARE; APPLY after the
owner has seen B and C, with a supervised first execution.

### 11. Uuendused → Lovable (amendment, 2026-10-27)

Lawyers asked to see what changed in Lovable, night by night, beside Juristid's
own release notes. `/uuendused/` keeps the Juristid notes as its default section
and gains a second, **Lovable** (`/uuendused/lovable/`, its own address).

It is an extension of this ADR, not a new mechanism: no table, job, poll or
source request. `app.legacy_import.lovable_review` reads the records §3–§7 already
keep and answers two questions apart:

- **What changed in Lovable?** Each *scheduled* night's stored snapshot against
  the snapshot of the nearest earlier scheduled night, source against source,
  through the reconciliation's own canonical extractors (`SOURCE_FIELDS`; a
  name by `raw_norm`). A held change that stays pending for five nights changed
  once and is shown once. A topic gone from the reading, back in it, or
  re-numbered is an event of its own, never an invented field edit; a Teema is
  always linked through its state's Matter, never through Lovable's `nr`. The
  first night is a starting point; a night that read nothing, a reading that
  no longer verifies and one past the 30-day retention say so instead of
  showing nothing.
- **What did Juristid do with it?** The night's `LovableSyncDecision`. "Uuendatud"
  only where an `APPLIED` decision exists — written in the transaction of the
  write it records (§6) — in this night's run or a manual run since the
  previous night, which is then named. Otherwise the decision's own outcome
  (Rakendamata, Vajab ülevaatamist, Vastuolu, Juristides juba sama), and with
  no decision the reconciliation's finding that Juristid already holds the value.

Manual runs and `Kontrolli muudatusi` are not nights; Haldus keeps showing them.
Only `SOURCE_FIELDS` are compared, so `markmed` and `mitteametlik_arvamus` — never
in a snapshot — cannot appear.

Unlike the Juristid notes this is business content, so the page asks what every
business read asks (docs/adr/0181): `login_required` sends a caller with no
persona to the doorway, `may_read_business_content` refuses a signed-in account
without a business role (the technical administrator) with a 403, and each
Teema passes `Matter.objects.visible_to`; one that does not is counted, never
named, and a tombstoned Teema is left out (docs/adr/0096). GET and HEAD only.

## Consequences

- The pilot's Teemad keep up with Lovable for the fields a machine can carry
  safely; everything else is listed for a person, and stays listed.
- A lawyer's edit in Juristid is never overwritten silently: it is kept, or it
  conflicts and is shown.
- Five new tables (`legacy_import` 0019); no change to existing data.
- `set_matter_title` takes an optional `provenance`; the Teema's OneNote link is
  the newest observation's.
- The sync dies with cutover; it cannot be turned back on.
- Uuendused has a Lovable section for business readers (§11); its history is
  as long as the snapshot retention, 30 days.
