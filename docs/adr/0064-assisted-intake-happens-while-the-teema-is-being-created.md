# ADR 0064 — Assisted intake happens while the Teema is being created

> **Amended by ADR 0069 (2026-09-10)** in two places, and only two. The
> staged queue is now drained by a dedicated `intake-reader` service rather than
> by the general extraction worker, which no longer runs as a service at all;
> and a promoted file is marked `INTAKE_READ` rather than left `PENDING` for a
> second parse. Everything else here — the staging tables, the expiry, the
> `SKIP LOCKED` claims, `parse_source` as the one parser stack, the pre-fill
> being decided server-side and applied by the browser only to an empty
> untouched control — stands exactly as written.

- Status: accepted (amended by ADR 0069)
- Date: 2026-09-08
- Stage: pre-QA (shared-gate development phase)
- Amends: ADR 0060 (which put the same analyser on the *edit* surface, and
  rejected reading an upload at intake time — that rejection is narrowed here,
  not reversed)
- Related: ADR 0014 (the malware and extraction gates this stays behind),
  ADR 0025 (senders are a set), ADR 0029 (organisation identity is exact name
  or reviewed alias), ADR 0032 (what `Uus teema` and `Saabunud` do and do not
  create), ADR 0037 (the business-write HTTP boundary), ADR 0063 (one
  organisation catalogue behind Saatja and Adressaat)

## Context

ADR 0060 built a deterministic reader for incoming material and put it on
`Muuda teemat`. The rules are right and are not touched here. Where it sits is
not.

The workflow it produced, written out, is:

    create the Teema
      → upload the document
        → wait for the worker
          → open Muuda teemat
            → press «Kontrolli dokumendist leitud andmeid»
              → review

Six steps, of which the person actually wanted one. And the review arrives
*after* the record has been created from whatever they could type before
reading anything — so the analyser's job on that page is to correct a Matter
rather than to help make one. A lawyer with the letter open in front of them
retypes the deadline into `Uus teema`, files the Teema, and is then offered the
same deadline back on a different page.

The moment the answers are worth having is while the form is still open.

**ADR 0060 considered exactly this and rejected it**, and the rejection was
correct for the design it was rejecting:

> **Parse the upload in the intake request and pre-fill the intake form.**
> Faster to demonstrate, and a second parser outside the malware gate. Also
> structurally wrong: at intake time no derivative exists […]

Both objections are about *parsing in the request*. Neither is an argument
against reading the file earlier; they are arguments against reading it
*synchronously, in the web process, outside the gate*. This record keeps every
one of those constraints and moves the reading anyway, by giving the file
somewhere to exist before the Matter does.

## Decision

### The file is staged before the Teema exists, and staging is not business data

Two additive tables, `MatterIntakeSession` and `MatterIntakeFile`
(`app/matters/staging.py`). A session belongs to one person, has an expiry and
a consumed stamp. A file carries the exact bytes' storage key, the original
filename, the MIME type, the size, the SHA-256, the role intake would give it,
a scan state, an extraction state and — once read — the parser's fragments as
JSON.

That is the whole of it, and the shape is chosen by what it must *not* be able
to become. It is in no register, no search index, no statistic, no timeline,
no Dokumendid list, no Jälgimine and no department count. It has no visibility
column, because it is not a record about anything: it is one person's
unfinished form, and `MatterIntakeSession.objects.owned_by` is the entire
authorization story. It has no audit trail, because nothing has happened that a
colleague could need to know about.

Three alternatives were rejected, and the reasons are the useful part:

- **A hidden or provisional Matter**, so that a Document has a parent. There is
  no such thing in this product, and creating one would mean a row that every
  list, count, search projection and statistic had to learn to filter out — the
  same defect as a visible half-made Matter, written in SQL where it is harder
  to see. ADR 0032 says what `Uus teema` creates; this keeps that list exactly
  as it is.
- **Making `Document.matter` nullable.** A Document with no Matter is a
  canonical row in a state the authorization model assumes is impossible:
  `visible_to` reads a Matter's visibility *through* it. The migration is one
  line and the consequences are everywhere.
- **Bytes in the Django session, or unmanaged temporary files.** The first has
  a size limit and a serialization story nobody wants for a 20 MB draft; the
  second has no owner, no expiry and no way to be swept correctly.

**The bytes live in the held-uploads storage class** (`app.documents.pending`),
under an `intake/` prefix. Same class deliberately: both are a form's unsaved
working state, neither describes anything, and losing all of it costs somebody
one re-pick. So it inherits a decision already made and documented for
operators — not backed up, no volume of its own — rather than adding a fourth
storage root. The prefix is load-bearing: `pending._sweep` deletes objects at
the *root* of that store by age and lists names rather than walking the tree,
so staged objects are out of its reach and are swept from the rows that
describe them instead. A test holds that boundary.

### One parser stack, one scan gate, two publication targets

`app.documents.extraction.orchestrator.parse_source` is the pure half of
extraction, factored out of `_run` without changing a line of what it decides:
the scan gate, the parser lookup, the parse, and the name every failure is
given. It commits nothing.

`_run` now calls it and publishes as it always did — derivative rows, text
fragments, attachment Documents, the search projection. `app.matters.
intake_extraction` calls the same function and publishes a little JSON on the
staged row and nothing else.

So the answer to "could a staged file be read by something weaker than the
evidence path?" is that there is nothing weaker to read it with. There is no
second PDF parser, no second DOCX parser, no second mail parser, no
browser-side parsing, no LLM, no network call and no new dependency. `load` is
a callable rather than bytes precisely so that a file waiting on a scanner and
a format no parser claims are still decided before a single byte is fetched.

**The staged queue is a queue, not a request.** Same shape as the canonical
one: PostgreSQL is the broker, `SELECT … FOR UPDATE SKIP LOCKED` makes a claim
atomic, the claim is a timestamped row state so a dead worker leaves evidence
rather than a lock, and the claim is re-asserted in the conditional UPDATE that
writes the outcome. Three clauses the canonical queue does not need keep it
bounded: a removed file, a consumed session and an expired session are not
offered. `run_extraction_worker` drains both queues, staged first and at most
five per turn — somebody is sitting in front of the form waiting for those, and
nobody is watching an evidence backlog — so a deployment still runs one
process.

**Attachments inside a staged message are deliberately not unpacked.** The
canonical path turns an `.eml`'s attachments into Documents of their own and
there is nowhere to put them yet. The message's headers and body are read,
which is where the sender, the subject and the sent time are; the attachments
become Documents the moment `Loo teema` promotes the message and the ordinary
worker reads it.

### One suggestion engine, and it did not have to change

`analyse()` already took an `AnalysisInput` and returned an `IntakeAnalysis`.
Only `build_analysis_input` and `CurrentValues.of` were database-facing. So the
new surface is one more builder, `build_intake_analysis_input`, and one more
three-line wrapper, `analyse_intake`.

Everything downstream is shared and unchanged: the title rules, the sender
matching against the organisation catalogue and its recorded aliases, the
deadline scanner and its decoys, the Menetlusliik cue tables, the Valdkonnad
vocabulary, the contacts, the references, the confidence contract, the conflict
rule, and the excerpt and provenance under every line. The candidate rows are
literally the same template (`intake_suggestion_rows.html`), because a
suggestion that read differently depending on which page you met it on would be
a second product.

The budget is shared too, arithmetic and all (`_admit`):
`MAX_CHARACTERS_PER_DOCUMENT`, `MAX_TOTAL_ANALYSIS_CHARACTERS` and
`MAX_TEXT_DOCUMENTS_ANALYSED` bound a staged analysis exactly as they bound a
Matter's, in the same priority order — the message first, then the covering
letter — and the budget still bounds the *loading*: the plan is made from a
stored `text_character_count` with the text deferred, and only admitted rows
are fetched.

### Manual input wins, and the browser is what enforces it

This is the one place where the create surface genuinely cannot reuse the edit
surface's mechanism, and the difference is worth stating precisely.

On `Muuda teemat` the analysis runs during the GET, so `prefill_initial` can
merge `HIGH` suggestions into an unbound form's initial data and the rendered
page is already correct. On `Uus teema` the form is on screen with somebody
typing into it, and the answer arrives seconds later. There is no unbound form
left to give an initial to.

So the *decision* is still `prefill_initial`'s — same confidences, same
one-`HIGH`-and-no-conflict rule, same absolute refusal to pre-fill a title —
and `prefill_controls` translates what it decided into `(control name, control
value)` pairs the fragment carries as data. The browser then applies each only
where the control is **empty and untouched**, and treats a control still
holding exactly what it last wrote there as still empty, so a later answer may
replace an earlier *answer* while never replacing a *person*.

"Touched" is deliberately generous: any real interaction anywhere inside the
field a control lives in counts, and pressing «Kasuta» settles that field
outright. Being wrong in that direction costs one suggestion nobody was
offered. Being wrong in the other direction costs somebody's typing, which is
the failure this whole feature must not have.

**And the panel renders the *unannotated* analysis**, which is the visible half
of the same distinction. `prefill_initial` marks the candidates it chose so the
edit page can print «vormil eeltäidetud» beside exactly those — true there,
because that page filled the control itself. Here the server proposes and the
browser decides, and it declines wherever somebody has already typed. Printing
«vormil eeltäidetud» over a box holding a person's own value would be the page
stating something it cannot know, and it would take away the «Kasuta» they need
to change their mind. So every candidate is offered, and the button itself says
what happened: it is bound to the live control, so one the browser filled reads
as chosen and one it declined reads as available.

`CurrentValues()` is therefore empty on this surface, and that is honest rather
than lazy: on an existing Matter the record is the thing a suggestion must not
overwrite and the analyser is told what it holds; here the thing that must not
be overwritten is what somebody is typing *now*, which no GET can see.

### `Loo teema` is still the only thing that creates anything

Before it: no Matter, no `MatterAssignmentNotice`, no ChangeEvent, no
`SearchDocument`, no Document, no DocumentVersion, no Submission, no Related
Material, and no Organisation — the analyser proposes a sender by matching the
catalogue and never creates one, which is ADR 0029 and ADR 0063 unchanged.

On it, inside the existing transaction: every file still on the form becomes
one Document with one immutable version, through `create_document` and
`add_evidence_version`, in the order the files were offered, before the held
and freshly-chosen ones. The bytes are read back from staging and hashed again
against the checksum recorded at upload; a mismatch refuses the save. Nothing
is re-uploaded by the browser and nothing is reconstructed from extracted text.

**Extraction is not promoted with the file, and that is a deliberate cost.**
The staged parse is thrown away and the new version is queued like any other.
The canonical publish path also writes attachment Documents, derivative
binaries and the search projection, so reusing a staged parse would mean either
duplicating that against a second source of truth or publishing a derivative
that every later reader assumes is complete. One extra parse of a file the
department uploads once is the cheaper mistake, and it keeps canonical
derivatives with exactly one producer.

A refused save keeps everything: the staged files, the suggestions, and what
was typed. The staging session is named by one hidden `intake` field, which is
the same shape as the `pending` keys a refusal already carries.

### Only the initial files, and only once

Automatic reading applies to the files chosen on `Uus teema`. It is not a
watcher over a Matter. A document uploaded to an existing Matter is processed
exactly as it was: extraction, search, and no suggestion workflow, no prefill,
and no silent change to the title, the sender, the deadline, the track or the
areas. A test locks that down.

`Kontrolli dokumendist leitud andmeid` stays, unchanged, as the explicit
re-check: for a Matter created before this shipped, for one whose files arrived
later, and for anybody who wants to look again. It is no longer the primary
workflow and is still never triggered automatically.

### Reading is never a gate

A valid file may be unreadable — a scanned annex whose OCR fails, a format no
parser claims, a worker that is not running. None of that may stop a Teema
being filed, so none of it does: `Loo teema` is pressable throughout, the file
becomes canonical evidence either way, and the panel says so in one calm
sentence rather than as a per-file error.

The states a person sees are «Loen faili…», «Loetud», «Ei saanud lugeda» and
«Sisu ei loeta». `PENDING`, `PROCESSING` and «Teksti eraldamine ei kohaldu» are
how the extraction system talks to an operator and are not shown here.
«Jätka ilma automaatse lugemiseta» stops the page claiming an answer is coming;
it cancels nothing, because there is nothing to cancel.

Polling is 1.2 seconds, stops on ready, failed, removed, consumed or abandoned,
and gives up after sixty attempts. The status route returns the same fragment
every other staging route does — two indexed queries and, once something has
been read, the analysis — and never touches file bytes.

### Lifecycle

`created` is the row, `consumed_at` is the Teema that took it, and `expires_at`
is the grace period the held-uploads store already uses. A consumed session is
a closed door rather than an empty one, so a double submit finds it.

`manage.py prune_intake_staging` deletes expired and consumed staging and the
objects it held, rows first as the plan and bytes first as the order. It cannot
reach evidence: it imports neither store nor either canonical model.

**Nothing schedules it, deliberately.** Putting a deletion loop into a
production timer is a decision of its own and is not made by writing one.

## Consequences

- A lawyer with a ministry's letter opens `Uus teema`, drops the file, and the
  deadline, the sender, the Menetlusliik and the Valdkonnad appear beside the
  boxes they belong in, each with the sentence it came from. They correct what
  is wrong and press one button.
- The security boundary is where it was. There is one parser stack, one scan
  gate, and no path from an HTTP request to a parser.
- Two additive tables, one additive migration, no data migration, no backfill,
  no search-version change, no rebuild, and no change to what a `Document`,
  a `DocumentVersion` or a `Matter` means.
- The old review surface still works, for the Matters that need it.

## Reversibility

Complete. Removing the two models, the three routes, the two commands, the
staged builder, the fragment templates, the CSS block and the script island
leaves `Uus teema` exactly as ADR 0063 left it and `Muuda teemat` exactly as
ADR 0060 left it — because neither of them was changed to make this work. The
one shared edit, `parse_source`, is a refactor that stands on its own: it makes
the orchestrator's pure half nameable and testable, and the canonical path
through it is byte-for-byte the decisions it made before.
