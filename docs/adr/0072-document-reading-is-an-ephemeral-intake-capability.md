# ADR 0072 — Document reading is an ephemeral assisted-intake capability

- Status: accepted
- Date: 2026-09-10
- Stage: pre-QA (shared-gate development phase)
- Supersedes: ADR 0066 (the malware scan gate is a real scanner) — entirely
- Amends: ADR 0014 (which defined extraction as a persistent capability of every
  canonical document, and defined the malware gate the scanner sat behind),
  ADR 0064 (which promoted staged files at `PENDING` so the ordinary worker
  would parse them a second time)
- Related: ADR 0003 (evidence lifecycle), ADR 0041 (search freshness),
  ADR 0060 (the deterministic reader itself, unchanged),
  `JURISTID-PERFORMANCE-INCIDENT-2026-09-10.md`

## Context

Three things were true on the morning of 2026-09-10, and together they are this
record.

**One.** Production became unusable for writing. A single-row `INSERT` into a
264 kB audit table blocked for two minutes and gunicorn killed the worker
holding it; a lawyer choosing a persona watched a page that never loaded. The
diagnosis was storage, not code: PostgreSQL's data directory sits on a
parity-protected Unraid array whose parity device is a USB-attached 5400 rpm
disk, so every small write is a read-modify-write on a device already at 99.9 %
utilisation. Checkpoint `fsync` latency, which had never exceeded 2.5 s on any
day through 2026-09-08, reached **154.9 s**.

**Two.** What put it there was the extraction worker. ADR 0066 had just made the
`PENDING` backlog readable for the first time, so the worker began draining
16 000 historical attachments — text fragments, derivative files and search rows
for every one of them. `search_searchdocument` grew to 2 090 MB of a 2 583 MB
database. Buffers written per day went from hundreds to 338 322. The two days on
which `fsync` latency exploded are exactly the two days that backlog was
draining. Nothing was wrong with the worker; it was doing what it was built to
do, at a volume the storage cannot absorb.

**Three.** Nobody wanted the result. The corpus text feeds search over historical
material, which is a capability with real value — and it is not the capability
the department asked for. What they asked for is that a file dropped on
`Uus teema` fills in the form in front of them. That is one small file, read
once, while somebody watches.

So the architecture is carrying a persistent, corpus-wide, always-on document
pipeline in order to deliver an ephemeral, single-file, interactive one.

### And the gate in front of it was worse than the pipeline

ADR 0066 built a real ClamAV scanner because `is_scan_state_extractable` had
been refusing everything for months and nothing could ever write `CLEAN`. That
was the right fix for the defect as it stood. What it also did was add a
permanent container holding a signature database, a daemon reaching the internet
for updates, a system check that refuses to start the application, a
configuration key on every service running the image, a CI job that boots the
scanner and streams EICAR at it, and a topology guard — all of it in front of a
parser opening documents the Chamber sends itself.

It cost a crash-looping `searchindex` (three keys set on two of three services),
a deployment step that is easy to miss, and roughly a gigabyte of resident
memory on a host whose problem is I/O. The threat it addresses is real in
general and has never once been observed here; the file it protects is one a
lawyer received by email and would have opened on their own machine either way.

## Decision

**Document reading exists only while a Teema is being composed.**

1. **A dedicated worker, whose universe is one open form.**
   `run_intake_reader` reads `MatterIntakeFile` rows belonging to a live,
   unconsumed, unexpired staging session, and cannot express anything else.
2. **The malware subsystem is removed**, not disabled.
3. **Corpus-wide extraction is not a deployed service.** The worker survives as
   an operator command; no Compose file defines a service that runs it.
4. **A promoted file is never re-read.** `INTAKE_READ` is a terminal extraction
   state meaning *this was read on the form, and nothing is owed*.
5. **Nothing permanent is derived from an intake read.** No derivative row, no
   text fragment, no search projection — before the Matter exists or after.

### 1. The intake reader

The queue is PostgreSQL, as everywhere else here: `SELECT … FOR UPDATE SKIP
LOCKED`, a timestamped claim so a dead worker leaves evidence rather than a
lock, and a fence re-asserted at the moment of writing. No Redis and no Celery,
because the table holds single-digit rows belonging to sessions that expire.

**What makes it safe is what it cannot name.** `run_intake_reader` imports
`app.matters.intake_extraction.drain` and nothing from the orchestrator's queue;
`intake_extraction` holds no query against `DocumentVersion` and no import of
it. Both are asserted structurally, by parsing the modules' own imports, so a
future edit that reaches for the corpus queue fails a test rather than an array.
Behaviourally: ten thousand pending canonical rows beside one staged file, and
running the reader moves exactly one — the ten thousand are not merely
unprocessed but unclaimed, because a claim is a write and writes are the thing.

It keeps its own heartbeat and its own five-minute staleness window, against the
corpus worker's thirty. A staged file is bounded by `MAX_EVIDENCE_UPLOAD_BYTES`
and finishes in seconds; waiting half an hour to report that this loop had
stopped would outlast the form it stopped in front of.

### 2. Removing the scanner

`app/documents/scanning.py`, `check_malware_scanner`, `juristid.E015`, the
`MALWARE_SCANNER_*` settings, the `clamav` service on both stacks, the CI
detection job, the topology guard and the EICAR tests are gone. `parse_source`
no longer takes a scan state and `pending_versions` no longer filters on one.

**Upload validation is untouched and is now load-bearing on its own**: a size
ceiling, an extension allowlist, and a content-signature check that the bytes
begin the way the format claims — all in the request that receives the file
(`app/documents/uploads.py`). Removing a scanner is not the same as accepting
arbitrary bytes, and the three checks that remain are the ones that were always
doing the work a browser cannot be trusted to do.

**The columns stay.** `DocumentVersion.malware_scan_state` and
`MatterIntakeFile.malware_scan_state` are dead schema: nothing writes them,
nothing reads them, no gate stands on them. Dropping a column from a 19 000-row
production table is a destructive migration performed for neatness, and this
change is large enough already. They are documented as dead at the enum, at both
model fields, and here. If they are ever dropped, `MalwareScanState` goes with
them.

### 3. The corpus extractor is a command, not a service

`run_extraction_worker` still exists and still works. What is gone is the
`extractor` service in `deploy/unraid-main` and `deploy/unraid-test`, so
`docker compose up -d` cannot start it. That is deliberately a *structural*
answer rather than a documented one: the service on production was already
stopped by hand on the day of the incident, and a stopped service is one
`up -d` away from running again with nobody having decided so.

An operator who wants text out of imported material runs it deliberately,
outside working hours, with a limit, and watches the array.

### 4. `INTAKE_READ`

ADR 0064 promoted a staged file at `PENDING` so the ordinary worker would parse
the same bytes again, and gave a good argument: reusing a staged parse would
mean either duplicating the canonical publish path against a second source of
truth, or promoting a derivative later readers assume is complete. **That
argument is untouched — nothing derived is carried across.** What changed is the
other half: the second parse is not performed either.

The staged verdict maps onto the canonical column, and only one outcome is still
offered to a queue:

| staged | promoted | meaning |
|---|---|---|
| `DONE` | `INTAKE_READ` | read once, on the form; nothing owed |
| `NOT_APPLICABLE` | `NOT_APPLICABLE` | no parser opens this format, ever |
| `FAILED` | `FAILED` | a parser opened these exact bytes and could not read them |
| `PENDING`/`PROCESSING` | `PENDING` | nothing has read it — promoted before the reader reached it |

A new terminal state rather than reusing one, because both available lies are
expensive. `DONE` promises every derivative the format requires was written and
committed; `NOT_APPLICABLE` claims nothing will ever open the format. Something
did open it, and it worked, and it produced nothing permanent — which is a
fourth thing, and it now has a name.

### 5. The consequence, stated plainly

**A document filed through `Uus teema` is not searchable by its content.** Its
text was read into a staging row, put onto a form, and thrown away with the
session. Statistika reports this as its own segment, «Loetud teema loomisel»,
excluded from the searchability denominator with a note saying what the trade
is — because a reader who sees it as a coverage gap will ask somebody to fix it,
and fixing it means starting the corpus run this record exists to stop starting
by itself.

Full-text search over Matter *metadata* — titles, senders, areas, entries — is
unaffected; so is the opinions archive, which has its own projection. What is
lost is search inside the bytes of newly filed evidence, and it is lost on
purpose until the storage underneath can absorb the writes.

> **Amended by ADR 0113 (2026-09-25).** The sentence above was not true when it
> was written: a document's *name* reached search only through the rows of an
> extracted derivative, so a newly filed document could not be found by title
> or filename either (ENG-030). Every Document now has a search row of its own
> carrying its title and filenames. Content search is still withdrawn exactly as
> this record decided.

## Alternatives considered

**Rate-limit the extractor instead.** A sleep between documents, or a
concurrency of one. This was the performance report's own second recommendation
and it is a good operational mitigation — but it keeps a corpus-wide queue
running permanently beside an interactive one, which is the coupling that made a
storage problem into a form that never finished. A limit is a number somebody
tunes; a separate process with no import path to the corpus queue is a property.

**Keep the scanner, disabled.** Rejected in the strongest terms available. A
scanner that is present and does nothing is the exact state ADR 0066 was written
about: a column called `malware_scan_state` whose readers believe a claim
nothing is making. Removing it says what is true.

**Drop the dead columns.** A destructive migration on production tables, for
tidiness, in a change already touching the extraction pipeline, the deployment
topology and the reporting catalogue — and in parallel with two other branches.
Documented instead.

**Parse in the request.** Rejected by ADR 0060 and ADR 0064 and rejected again:
OCR on a scanned annex is minutes of a gunicorn worker that is serving nobody.

**Send documents to an external model.** Not implemented and not proposed. The
deterministic reader autofills five fields at 100 % precision on the evaluation
corpus; an LLM might improve summarisation or classification, but it would mean
the Chamber's incoming correspondence leaving the host, and that is a product
decision with a data-protection question attached, not an implementation detail
to be taken inside a performance fix.

## Consequences

- The deployed stack loses two containers (`extractor`, `clamav`) and gains one
  (`intake-reader`), which mounts the staging volume read-only and nothing else.
  The rehearsal stack matches, because the two differing in runtime shape is
  what hid the last defect for months.
- One migration per app, both `AlterField` on `extraction_state` choices. No
  data migration, no backfill, no search rebuild.
- Statistika's extraction section keeps five segments that still sum to the
  visible version count; `EXTRACTION_AWAITING_SCANNER` is retired for
  `EXTRACTION_INTAKE_READ` and `EXTRACTION_ELIGIBLE` is re-defined (version 2)
  as «formats some parser opens», which is also the searchability denominator.
- The reader gains an evaluation corpus of twelve invented envelopes with their
  right answers written down, scored as correct / missed / **wrong**, and a
  `evaluate_intake_corpus` command that prints the scorecard. Precision is
  asserted absolutely; recall is asserted as floors.
- `Pealkiri` is pre-filled on `Uus teema` — and only there — when exactly one
  high-confidence formal title exists, there is no conflict, and the browser
  finds the box empty and untouched. On a saved Matter no title is ever
  replaced, because the *record* cannot tell a person's title from a machine's;
  an unsaved form has no such record and the browser can see what it never
  could.

## What this does not do

It does not fix the storage. The database still lives on a parity-protected
array behind a saturated USB disk, and the honest primary fix — moving the
PostgreSQL data directory onto the NVMe — is an operational change with a
runbook of its own. This removes the workload that made that fault visible; it
does not remove the fault.

It does not deploy anything, and no production operation was performed as part
of it. The production extractor remains stopped.

## Amendment, 2026-10-10 — `INTAKE_READ` only when the form offered the reading

- Status: accepted, amending Decision 4 and §4's table (the `DONE` row) and
  §5's «put onto a form»; product decision D-6 of the 2026-10 audit (AUD-025,
  DX-4/5/6)
- Scope: the canonical state a promoted staged `DONE` receives; how long the
  staged extracted text lives; no schema change

### What was decided before

A staged file the intake reader read successfully was always promoted
`INTAKE_READ` — *read on the form, the answers went onto it, nothing owed* —
terminal, never offered to `pending_versions`, never extracted and never
searchable by content. The staged text was said to be «thrown away with the
session», but `consume_session` deleted only the bytes; the text and parsed mail
headers stayed on the staged row until an operator ran `prune_intake_staging`,
which nothing schedules.

### Why it is superseded

ADR 0088 withdrew the suggestions from `Uus teema` on 2026-09-16 and left the
reader running. From then on the reader read every staged file for nobody:
nothing it found reached the form, yet promotion still recorded that it had —
and on the strength of that claim the document was made permanently
unsearchable by content. The state's meaning was true only while the feature
it described was on. Separately, a full copy of each staged file's text (up to
eight million characters of incoming correspondence) outlived the form it was
read for by as long as nobody pruned.

### What is decided now

- The reader keeps operating with the suggestions off (D-6).
- Promotion maps a staged `DONE` to `INTAKE_READ` **only when
  `create_form_suggestions_offered()` is true at promotion time**; otherwise to
  `PENDING`, so the ordinary worker owes it like any other upload and it becomes
  searchable when that worker runs (`promoted_extraction_state`,
  `app/matters/intake_staging.py`). `NOT_APPLICABLE` and `FAILED` (with its
  carried reason) are verdicts about the bytes and are unchanged.
- `consume_session` blanks the staged rows' `text`, `text_character_count` and
  `email_metadata` as well as deleting the bytes; and the always-running reader
  blanks the same columns for sessions that are expired or consumed
  (`clear_expired_intake_text`), at most once every ten minutes and with no
  write when nothing holds text. Rows, bytes of unconsumed sessions, states and
  audit facts are not deleted by it; `prune_intake_staging` is unchanged and
  still an operator's.
- Statistika's wording for «Loetud teema loomisel» says the finding was
  *offered* on the form, and «Eraldamise järjekorras» says `Uus teema` files can
  be queued while the reading is withdrawn. The document page names
  `INTAKE_READ` by its label instead of printing the enum.
- §1's reference to `MAX_INTAKE_UPLOAD_BYTES` named a setting that never
  existed; the bound is `MAX_EVIDENCE_UPLOAD_BYTES`, which `read_upload` applies
  to every staged file, and the text above now says so.

### What this amendment does not change

The reader's universe and its structural separation from the corpus queue;
that nothing derived from a staged parse is carried across; `INTAKE_READ` as a
terminal state and its exclusion from `pending_versions` and from the
searchability denominator; the corpus extractor remaining an operator command.
**Existing rows are not touched**: versions promoted `INTAKE_READ` between
2026-09-16 and this change, while the suggestions were off, keep that state —
no data migration relabels them, because which of them a form actually offered
is not recorded anywhere.
