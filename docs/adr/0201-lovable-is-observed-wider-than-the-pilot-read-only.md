# 0201 — Lovable is observed wider than the pilot, read only

- Status: Proposed (owner's brief «Lovable observability, change history and MCP
  expansion», 2026-11-02, with its absolute read-only requirement)
- Extends: 0192 (the pilot's nightly one-way synchronisation), 0185 (read-only MCP)
- Relates to: 0157 (Lovable mirror, PR #429 — still a draft, not merged, not
  replaced by this), 0181 (one internal read policy), 0189 (MCP writes)

## Context

The pilot synchronisation (0192) reads the 100 pilot topics and an index of
every live topic each night, in COMPARE mode; APPLY has never been switched on.
The owner wants to see far more of Lovable's change history inside Juristid —
operational topics, every live topic, and Lovable's own audit ledger — as
preparation for a possible future live synchronisation, **without changing a
single byte of Lovable** and without widening what is synchronised.

Three different populations and windows were already being confused in
conversation: Lovable's audit ledger (144 events on 97 topics since 27.10), the
nightly snapshot differences of the 100 pilot topics (84 changed fields over
four nights), and the pilot's reconciliation decisions (79 needing attention,
0 applied). This record keeps them apart by construction.

## Decision

### 1. Lovable is read-only, absolutely

The source transport refuses every method but `GET` and the password sign-in
`POST` (`NOT_READ_ONLY`, before anything is sent). `lovable_sync access-probe`
no longer sends write RPCs or `PATCH`/`DELETE` — not even against UUIDs that
cannot exist — so it can never prove write refusal over HTTP: that check is
always INDETERMINATE, and write refusal is shown only by the read-only
catalogue queries `lovable_sync access-sql` prints for the owner. The probe of
27.10 that "passed" with write-shaped calls is superseded. No Lovable code,
schema, policy, role or grant is changed by Juristid; where more access would
be needed, Juristid prints a reviewed proposal (`lovable_sync audit-sql`) and
does nothing else.

### 2. Populations

| Population | What | Eligible for APPLY |
|---|---|---|
| PILOT | the 100 Teemad of 0191, through their approved provenance | yes, unchanged (0192) — and APPLY is still off |
| OPERATIONAL | live topics with `operatiivne_ulatus` | no — observation only |
| ALL_LIVE | every live topic | no — observation only |
| SOURCE_HISTORY | Lovable's audit ledger | no — and disabled until access exists |

The pilot cohort, its baselines and its reconciliation are not changed. No
observation is ever a pilot baseline.

### 3. The wider reading

`app.legacy_import.lovable_observation`, run by `lovable_sync observe` after the
pilot's nightly run when `control --observe on` (off by default). Under the
pilot's run lock it reads every live topic's allowlisted columns and the child
tables (commencements, responsible changes, stage links, topic links) whole,
twice, compared by canonical hash (three attempts, then `INCONSISTENT`). The
stored document has an **index of every live topic** (identity, number,
version, operational flag, row and child hashes) and **detail only for
operational and pilot topics** (the reconciliation's own canonical extractors).
Other topics' values are not stored. `markmed`, `mitteametlik_arvamus`, raw
import rows and identity columns are never read (`docs/data-contracts/
lovable-source-inventory.md`).

Each verified reading is compared with the previous verified one and one
`LovableObservedChange` per changed topic is stored: NEW, CHANGED, MISSING,
BACK, RENUMBERED, an operational-scope flip, a child-only change. Each topic is
mapped `PILOT_LINKED` (approved provenance), `REFERENCE_CANDIDATE` (a Juristid
Teema carries the same number but nothing links them — a person confirms) or
`UNMATCHED`. Nothing is linked by title, and nothing is linked automatically.

### 4. The audit ledger

`topic_change_event` / `topic_change_field` are readable only by Lovable
administrators (`can_access_admin_view`). Ingestion is built and **disabled**
(`JURISTID_LOVABLE_AUDIT`, default off). Enabling it needs an owner-approved,
Lovable-side, SELECT-only path for the service identity; the service identity
is never made an administrator and no `service_role` key is used. Until then
every surface says the history is unavailable, and no event is inferred. As
built (corrected after the review of PR #586):

* **Excluded values are never collected, not only never stored.** Field rows
  are read in two GETs per chunk of at most 50 events:
  `select=event_id,field,old_value,new_value&event_id=in.(…)&field=in.(<allowlist>)`
  and `select=event_id,field&event_id=in.(…)&field=not.in.(<allowlist>)`. The
  allowlist (`lovable_source.AUDIT_VALUE_FIELDS`) is a constant — the topic's
  allowlisted columns without `id`/`version`/`updated_at`, plus `joustumised`,
  `joustumised_hallatud`, `menetlusetapid`, `seosed` — and never holds
  `markmed` or `mitteametlik_arvamus`. The transport refuses
  (`NOT_ALLOWLISTED`, before any request) a read that selects `old_value` or
  `new_value` without a positive `field=in.(…)` filter of allowlisted names
  only. Fields outside the allowlist are kept by name as *withheld*. An answer
  outside its own filter (a value row for an unlisted name, a name row for a
  listed one, a row for another event) is source misbehaviour: the page is
  dropped and the run says `UNAVAILABLE` / `SOURCE_FILTER_IGNORED`.
* **Completeness before the cursor moves.** Under row-level security a
  denied read is `200 []`. A page with edits whose field reads return no row
  at all stores nothing and says `FIELDS_NOT_VISIBLE`. An edit (`muutmine`,
  or any action other than `lisamine` / `kustutamine` / `taastamine`) with no
  visible field row stops the reading at that event: the events before it are
  stored, the cursor advances only to the last complete event, and the run
  says `UNAVAILABLE` / `INCOMPLETE_EVENT`. Once the fields are visible the
  same events are read again and stored with their fields.
* **Late-arriving events.** The cursor is (`occurred_at`, id) of the last
  complete event; each run reads again from 48 hours before it
  (`AUDIT_OVERLAP`) and skips events already stored by their source id, so an
  event committed late with an earlier `occurred_at` is still picked up
  (`late` in the counts, beside `events_new` and `events_seen`). **Residual
  limitation:** an event that becomes visible with an `occurred_at` more than
  48 hours behind the cursor is missed, and nothing in Juristid can detect it;
  only a full re-read (a lost cursor) would find it.
* **Bounded values are marked.** A value is kept as canonical JSON text up to
  2,000 characters (`AUDIT_VALUE_LIMIT`); a longer one is cut and its field row
  says `truncated`, wherever the value is shown.
* A → B → A is two events; the actor is kept only as its kind; an interrupted
  backfill resumes after the last committed page.

### 5. One read service

`app.legacy_import.lovable_observability` is the only reader of these records
for Uuendused → Lovable, Haldus, the MCP tools and `lovable_sync report`. It
keeps four facts apart in every answer: source events, snapshot differences,
reconciliation decisions and applied writes. It writes nothing — it does not
call `control()`, `sync_health()` or `due()`, which create the control row.

### 6. Who sees what

`may_read_business_content` first; a pilot Teema's title and values only
through `Matter.objects.visible_to`; otherwise counted, never named. A source
topic with no approved link to a Juristid Teema is shown by its source number,
change kind and field *names* only, never its title or values
(`UNMATCHED_DETAIL = "FIELD_NAMES_ONLY"`), until the owner decides a detail
policy. The same rules hold for HTML, XLSX, MCP, search and counts.

### 7. Readiness is an assessment, not an activation

`readiness()` plans what the *existing* pilot rules would do with the newest
reading (`plan_matter`, which is pure) and reports coverage, conflicts,
uncovered changes, blockers and owner decisions. It cannot write, enable APPLY
or approve anything, and it never widens synchronisation to new topics.

### 8. Retention

Raw observation (snapshots, per-topic changes, audit rows) is deleted after
30 days, the window approved for the pilot's snapshots, pending any longer
approval; the newest verified reading (a SUCCEEDED run's snapshot) is always
kept, however old. Run rows and their aggregate counts carry no business text
and are kept. Projected storage at today's size: about 1.5 MB per reading,
about 45 MB for 30 days.

Retention is its own job, independent of reading (review of PR #586):
`lovable_sync prune-observations` records a `LovableRetentionRun`
(`prune:<Tallinn date>`, once a night — a second firing is refused
`DUPLICATE`, exit 2; `--manual` for an extra run), `SUCCEEDED` with counts or
`FAILED` with a code. It never reads Lovable and needs no credentials, so it
runs whether the observation is on or off and whether Lovable is reachable. The
host script runs it every night right after the host lock and the web health
check, before the pause file, the credentials and the due check — so it holds
while the pilot is OFF or paused — and its failure is logged without changing
the pilot's exit status. The observation run itself deletes nothing.

### 9. MCP

Five read-only tools over the same service — `get_lovable_sync_status`,
`get_lovable_sync_history`, `get_lovable_sync_matter_changes`,
`get_lovable_observed_changes`, `get_lovable_sync_readiness` — with strict
typed arguments, bounded pages and windows, and the same permission rules. The
write gateway (0189) is unchanged. The `mcp` role must be granted SELECT on the
new tables (`manage.py mcp_db_role`) after the migration, and ChatGPT's
connector refreshed to list the new tools.

## Consequences

- Juristid observes about 2,500 live topics, not 100, with no Lovable change
  and no business write.
- Migration `legacy_import` 0020 is additive (six tables, one boolean).
- The 27.10 access probe no longer counts as proof; APPLY's gates now include
  the owner's recorded `access-sql` result.
- Source-side finding for the Lovable owner: `topic_raw_rows` has row-level
  security disabled and SELECT granted to `authenticated`.
