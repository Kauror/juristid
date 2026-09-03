# Juristid — the real-data instance on the Unraid host

The Chamber's twenty-year legislative register and the OneNote material attached
to it, in one system. This is the environment that holds the real thing.

| | |
| --- | --- |
| LAN URL | **none — there is no host port** |
| Auth mode | `shared_gate` (temporary; see below) |
| Public URL | `https://juristid.orgusaar.ee` — behind the shared gate |
| Compose project | `juristid-main` |
| Containers | `juristid-main-web`, `juristid-main-db`, `juristid-main-extractor`, `juristid-main-searchindex`, `juristid-main-tunnel` |
| Network | `juristid-main-internal` (its own bridge) |
| Appdata | `/mnt/user/appdata/juristid-main/` |
| Evidence | `…/evidence` — **back this up** |
| Derivatives | `…/derivatives` — rebuildable, needs no backup |
| Page XML | `…/legacy-source` — **back this up**; source evidence |
| Source corpus | `/mnt/user/juristid-main/source/` — **read-only**, mounted `:ro` |
| Secrets | `/mnt/user/appdata/juristid-main/config/juristid.env`, mode 600, never in Git |
| Backup and recovery | [`RECOVERY.md`](RECOVERY.md) — how to back up, verify, restore and roll back |

The synthetic rehearsal at `juristid-test` keeps running, on its own project,
network, database and appdata tree. Nothing here touches it, and it must not be
stopped or removed as part of this deployment.

## Why this environment is allowed to hold real data

Four properties, each enforced somewhere a mistake would be visible rather than
silent.

**There is no host port.** No service in `compose.yml` has a `ports:` key. The
only route in is the Cloudflare tunnel. You cannot reach this by typing the
server's LAN address, and `tests/test_deployment_unraid_main.py` fails if a port
ever appears.

**There is an authenticator in front of it.** `AUTH_MODE` is `shared_gate`
today and `cloudflare_access` when the Access application exists. Real data with
`AUTH_MODE=none` refuses to start (`juristid.E006`).

**Nobody is provisioned automatically.** In `cloudflare_access` mode a verified
email that matches no active, non-synthetic account is refused. In `shared_gate`
mode the persona list is exactly the accounts an administrator created.

**The unsafe combinations refuse to start.** `manage.py check` fails on real data
with `DEBUG` (E004), real data with the synthetic sign-in (E003), real data with
no authenticator (E006), `cloudflare_access` unconfigured (E007), the synthetic
sign-in beside a real authenticator (E008), an unknown mode (E009), a missing or
too-short gate password (E010, E011), a disabled rate limit (E012), and a gate
session cookie that is not `Secure` (E013).

## The shared gate — what it is, and what it is not

**Temporary.** It exists so the development phase can proceed without waiting
for a Cloudflare dashboard, and it is replaced by `AUTH_MODE=cloudflare_access`
with no other change.

**It authenticates the door.** One password for the department, supplied
host-side in `JURISTID_SHARED_GATE_PASSWORD`, hashed once at process start,
compared in constant time, and rate limited per client with an escalating,
capped lockout. Passing it proves somebody knows the department's password.

**It does not authenticate the person.** Behind the gate you pick whose work you
are looking at. That selection drives `Minu töö`, ownership filters and profile
context — and it is not evidence that the named human is at the keyboard. Every
audit row records both:

```
authenticated_via = SHARED_GATE
acting_as_user    = <the selected persona>
```

Passing the gate is logged as `SHARED_GATE_PASSED`, deliberately not as
`AUTHENTICATION_SUCCEEDED`, so nobody reading the trail later mistakes "somebody
typed the department password" for "this person signed in". Persona changes are
logged every time, with the previous and the chosen persona.

**What this means in practice.** Anyone with the password sees everything
NORMAL and can select any persona, including one entitled to RESTRICTED
material. The gate is the perimeter; the persona is a lens. One secret is shared
by several people, so it cannot be revoked for one of them — only rotated for
all. Anything that later needs "who did this" as evidence needs
`cloudflare_access` first. See `docs/adr/0016`.

### The flow

```
juristid.orgusaar.ee
    ↓  shared password
Vali kasutaja                ← the root asks who is reading before showing work
    ↓  select a persona
selected persona → Minu asjad
```

Osakond is still there, still on the bar, and still the one page that renders
with **no** persona selected — reached by its own address, or by explicitly
switching to *Ilma kasutajata*. It renders for a *department scope*, not for an
arbitrary person's identity: NORMAL visibility, no participation, so nothing
RESTRICTED appears merely because the password was typed. Everything except
Osakond needs a persona, because authoring anything needs somebody to attribute
it to.

Changing persona does not ask for the password again. Signing out closes both.

### Rotating the password

Edit `JURISTID_SHARED_GATE_PASSWORD` in the host's environment file and restart
the web container. Nothing is stored anywhere else, so nothing else has to
change. Everyone's session survives until it ages out
(`SHARED_GATE_SESSION_SECONDS`); to end them immediately, also rotate
`DJANGO_SECRET_KEY`.

## First deployment

Everything below runs on the Unraid host. Nothing in it destroys anything; the
one step that could is called out.

### 1. The source corpus

Copy the three source trees to `/mnt/user/juristid-main/source/`:

```
source/
  excel/Tööd eelnõudega.xlsx
  onenote-desktop-archive/
  migration-audit/
```

Then verify the transfer against the audit's own manifest — every file, by
SHA-256, not by size or count:

```bash
docker compose -p juristid-main -f compose.yml run --rm web python manage.py historical_import inspect
```

`inspect` reads and writes nothing. It reports the register's digest, the
archive manifest's canonical digest, and whether the planned counts reconcile
with the audit baseline. **If any line does not say "reconciles", stop.** A
corpus that half-imported is indistinguishable from a complete one to everybody
who reads it later.

After the copy, treat that directory as read-only source material. It is mounted
`:ro` into both application containers so the importer cannot rewrite it.

### 2. Configuration

```bash
install -d -m 700 /mnt/user/appdata/juristid-main/config
cp .env.example /mnt/user/appdata/juristid-main/config/juristid.env
chmod 600 /mnt/user/appdata/juristid-main/config/juristid.env
```

Fill in `DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD`,
`JURISTID_SHARED_GATE_PASSWORD` and the hostname.

Leave `CF_ACCESS_TEAM_DOMAIN` and `CF_ACCESS_AUDIENCE` empty for now: they are
the two values `AUTH_MODE=cloudflare_access` needs, and this stack runs
`shared_gate`. When the Access application exists, the audience tag comes from
Cloudflare One → Access → Applications → this application → Overview →
*Application Audience (AUD) Tag*.

There is deliberately no `APPLICATION_REVISION` and no `APPLICATION_STAGE` in
the template. Both used to be copied here and both went stale where nobody
looked; the stage now comes from the code and the revision from the image. See
the template's own comments.

### 3. Cloudflare

Create the tunnel locally on the host, so the credential is generated here and
never leaves:

```bash
cloudflared tunnel login
cloudflared tunnel create juristid-main
```

Put its JSON and a `config.yml` routing the hostname to `http://web:8000` in
`/mnt/user/appdata/juristid-main/cloudflared/`.

`juristid.orgusaar.ee` is the live hostname. It served the synthetic rehearsal
first, so the cutover is a change of which tunnel claims it — see **Cutover**
below. There is no second live hostname.

Cloudflare Access is the next hardening step and is **not** required to run:
with `AUTH_MODE=shared_gate` the application authenticates its own requests. When
the Access application exists, set `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUDIENCE`
and `AUTH_MODE=cloudflare_access`, and restart. Nothing else changes.

### 4. Load the release image

This host does not build the image — not on the first deployment and not on any
later one. Build it off-host for the reviewed commit, transfer the artifact,
verify its digest and load it, exactly as **Deploying a release** parts A and B
describe below; the exports, the digest check and `docker load` are the same
commands. Come back here with the image loaded and the two variables exported
in this shell.

### 5. Start

```bash
docker compose -p juristid-main -f compose.yml up -d db
docker compose -p juristid-main -f compose.yml run --rm web python manage.py migrate
docker compose -p juristid-main -f compose.yml up -d --no-build
```

`JURISTID_GIT_SHA` and `JURISTID_IMAGE_TAG` must still be exported here.
Without them `run --rm web` resolves `juristid-main-web:local`, and since no
such image exists on a fresh host, Compose would *build* one — which is the
thing this host must not do. `--no-build` on the replacement is the same
guarantee stated on the command itself.

Migrations are a deliberate step, never container start-up work: on boot they
would run on every restart.

### 6. Accounts

Create the real people, by hand, once. There is no self-service and no
auto-provisioning:

```bash
docker compose -p juristid-main -f compose.yml run --rm web \
  python manage.py createsuperuser --upn <email> --display_name "<name>"
```

These become the persona list behind the gate, and later the accounts Cloudflare
Access asserts against. Use real addresses: do not invent `.invalid` identities
here and do not use somebody else's address as a placeholder.

## Importing the historical corpus

Six phases, in order. Each is a separate command rather than a flag, because
they have genuinely different consequences and a flag is easy to mistype.

```bash
C="docker compose -p juristid-main -f compose.yml run --rm web python manage.py"

$C historical_import plan          # reads everything, writes nothing
$C historical_import dry-run       # the real apply, rolled back
$C historical_import apply         # commits pages, links, Matters, the queue
$C historical_import materialise   # copies originals into evidence; resumable
$C historical_import status
$C historical_import verify
$C rebuild_search_index
```

`plan` needs no gate. `apply` refuses unless `REAL_DATA_ALLOWED` is on **and**
every baseline check reconciles.

`materialise` streams roughly 4.14 GiB and takes a while. It is resumable by
asking the database what is still missing, so interrupting it costs nothing:
re-run the same command. `--limit N` stops after N files, which is the right way
to watch the first few before committing to the rest.

Back up before `apply` and after `verify`. Not with a `pg_dump` pipeline — see
[`RECOVERY.md`](RECOVERY.md) for why that line could produce a truncated dump
and report success:

```bash
scripts/deploy/juristid-backup.sh --project juristid-main --compose-file deploy/unraid-main/compose.yml --data-root /mnt/user/appdata/juristid-main --backup-root /mnt/user/backups/juristid-main
```

## The register import

`historical_import` imports the OneNote side and links it to Matters that
already exist. The Excel register itself is imported by `register_import`
(ADR 0012) and must run first — otherwise every exact link reports its reference
as not found, which is correct behaviour and a wasted afternoon.

## Everyday operations

```bash
docker compose -p juristid-main -f compose.yml ps
docker compose -p juristid-main -f compose.yml logs -f web
docker compose -p juristid-main -f compose.yml logs -f extractor
docker compose -p juristid-main -f compose.yml logs -f searchindex
docker compose -p juristid-main -f compose.yml restart web
```

`searchindex` is the search freshness worker. It sleeps until a canonical change
owes the index a rebuild — an Organisation rename, an alias edit, a Tag or
PolicyArea rename, a person's display name — and then performs one atomic full
rebuild (ADR 0041). Nothing is owed most of the time, so an empty log is the
normal state. To ask the question directly:

```bash
docker compose -p juristid-main -f compose.yml exec -T web python manage.py check_search_freshness
```

If it says the index has been owed a rebuild for too long, the worker is what to
look at first. `rebuild_search_index` remains the manual answer and is always
safe to run.

## Recurring operations — refreshing the register

Everything above this point happens once, or happens when something is wrong.
This is the one operation that is *meant* to be repeated: the department edits
`Tööd eelnõudega.xlsx` every week, and reading a newer copy of it into Juristid
is a routine act with a fixed shape (ADR 0045). It has been performed three
times — the 21.08, 28.08 and 01.09 workbooks — and ADR 0053 is the worked
example of a repeat.

**Six steps, and the order is a dependency rather than a preference.**

| | Step | Command | Writes |
| --- | --- | --- | --- |
| 1 | What does the newer workbook say? | `register_snapshot_delta` | nothing, ever |
| 2 | Catalogue it | `import_legacy_register … --apply` | source references, and new Matters |
| 3 | Plan | `refresh_current_register plan` | nothing |
| 4 | Review | *a person* | — |
| 5 | Apply | `refresh_current_register apply` | currency, fields, actions, engagements |
| 6 | Verify | re-plan, and the checks in `docs/production-readiness.md` §4 | nothing |

Step 2 is the one people leave out, and leaving it out used to produce a plan
reporting zero changes everywhere — which reads as *the newer workbook changes
nothing*, and is the one wrong answer an operator would believe because it is
the answer they were hoping for. It now produces a refusal naming the command to
run (ADR 0045 §10). That refusal is the reason this table has six rows.

### Before any of it: two things that are not commands

**The workbook has to be approved, and approving it is a code change.**
`REVIEWED_SNAPSHOTS` in `app/legacy_import/final_cutover.py` names each approved
workbook by the SHA-256 of its bytes, with the sheet years it was approved for
and the day it was taken off somebody's desktop. There is no flag, no
environment variable and no argument that can approve one at the command line:
retiring or activating the department's whole portfolio from whatever file
happened to be on a desktop is not something a command line should be able to
do. Adding a digest is a pull request, reviewed and merged and deployed like any
other, and it is the department head's decision that those bytes are
authoritative — not the operator's.

A workbook that was superseded before anybody planned against it does **not**
get added afterwards to make the sequence look continuous. The 30 August file is
deliberately absent for exactly that reason (ADR 0053 §1). A reviewed snapshot
is a statement that somebody looked at those exact bytes.

**The snapshot date is not decoration.** It is what lets a `JÄRGMISEKS` cell
reading *vaata üle 15.09* resolve to 2026, and only on a sheet whose year matches
it (ADR 0045 §2). A digest added without one silently refuses every year-less
date in the workbook.

### The shell, and where the files live

Two prefixes, because two of these steps need a writable path and the rest do
not. Run them on the Unraid host, from the repository checkout:

```bash
# uid 10001 is the application user, fixed in the image so a rebuild cannot
# change it (Dockerfile). Created root-owned, the container cannot even traverse
# this directory, and `refresh_current_register plan` then dies *after* printing
# its whole report and *before* printing the plan digest step 5 needs — so the
# review happens and the digest never arrives. Same rule as RECOVERY.md.
install -d -m 700 -o 10001 -g 10001 /mnt/user/juristid-main/refresh
install -d -m 700 -o 10001 -g 10001 /mnt/user/juristid-main/refresh/2026-09-01

C="docker compose -p juristid-main -f compose.yml run --rm web python manage.py"
R="docker compose -p juristid-main -f compose.yml run --rm \
   -v /mnt/user/juristid-main/refresh:/refresh web python manage.py"
```

The workbook goes in the read-only source tree beside the one the historical
import reads, and is referenced by its path *inside* the container:

```
/mnt/user/juristid-main/source/excel/Tööd eelnõudega 01.09.xlsx
  → /srv/historical-source/excel/Tööd eelnõudega 01.09.xlsx
```

**Never overwrite `excel/Tööd eelnõudega.xlsx` with a newer snapshot.** That
exact filename is what `historical_import` resolves by convention
(`historical_import.py:325`), and replacing it changes what the historical
corpus import reads without anything saying so. New snapshots sit beside it
under their own names. The tree is mounted `:ro`, which is what you want: these
commands hash the file and never write to it.

`/refresh` is a working directory for review files and catalogue reports, and it
exists because **none of the container's writable mounts is a safe place for
them**. `--rows`, `--candidates` and `--report-dir` all write real files;
`--report-dir` defaults to `import-output/import`, which is relative to `/app`
and disappears with the `--rm` container. Writing them into `/app/evidence`
instead is the accident of 2026-08-24 repeated by hand — see *Never run the
application test suite through this stack* — and `check_evidence_integrity` has
been reporting the last one ever since.

Keep the campaign export in `/refresh` too, and **delete it when the refresh is
done**. It is member mailing data.

### 1. Ask what the newer workbook says

```bash
$C register_snapshot_delta \
  --workbook "/srv/historical-source/excel/Tööd eelnõudega 01.09.xlsx" \
  --expect-sha256 3db743ac9fe406e1cf837245895d07896d2fbf4e48d3eb80583fe7d244a86342
```

**Reads** the workbook, the immutable per-cell provenance in
`MatterSourceReference.source_row_raw`, the derived register state, and the
`ChangeEvent` rows people wrote after the last catalogue. **Writes nothing.**
There is no `--apply` and adding one would turn a report into the Excel-to-
Juristid bridge the cutover exists to remove. Every collection is sorted before
it is emitted, so two runs over one workbook produce byte-identical output and a
diff of two reports is a diff of the register.

Flags, in full: `--workbook PATH` (required), `--expect-sha256 SHA` (refuse
unless the bytes hash to exactly this), `--years 2025,2026` (the years the
portfolio arithmetic is scoped to; every other sheet is still compared cell by
cell), `--json`.

`--expect-sha256` is how you state which bytes you meant. Given a digest that
does not match, the command refuses rather than reporting on a file nobody
approved. Use it — the point of this step is to find out what a *named* file
says.

Read four blocks and stop on two of them:

- **Read** — `IDENTSED` / `MUUDETUD` / `UUED` / `KADUNUD`, and `sisulisi välju`,
  which counts the differences that mean something as opposed to formatting.
- **Jooksev töö** — ends either `identiteedid kattuvad tootmisega` or
  `identiteedid EI kattu tootmisega` followed by the references that would
  activate and retire. **Equal totals with a swapped membership is the failure a
  headline figure cannot show**, which is why the identities are named and not
  merely counted.
- **Omakirjed pärast kataloogimist** — what people have written here since.
- **Topeltkirjutuse konfliktid** — a Matter the workbook moved *and* somebody
  worked on here. **If this is not `ei leitud`, stop.** Nothing resolves a
  dual write automatically, and nothing should.

The last line reads `Andmebaasi ei kirjutatud.` If it does not, you ran
something else.

This step is optional in the sense that nothing enforces it, and it is the step
that tells you whether the rest is worth doing. It answers "does production
disagree with the register", which is the question, rather than "did somebody
edit the spreadsheet", which is not.

### 2. Back up, then catalogue

Back up first. Steps 2 to 5 are one data operation and this is the copy they are
rolled back to:

```bash
scripts/deploy/juristid-backup.sh --project juristid-main --compose-file deploy/unraid-main/compose.yml --data-root /mnt/user/appdata/juristid-main --backup-root /mnt/user/backups/juristid-main
```

Then the catalogue. Dry run first — it is free and it is where a changed header
surfaces:

```bash
$R import_legacy_register "/srv/historical-source/excel/Tööd eelnõudega 01.09.xlsx" \
   --dry-run --report-dir /refresh/2026-09-01/catalogue

$R import_legacy_register "/srv/historical-source/excel/Tööd eelnõudega 01.09.xlsx" \
   --apply --report-dir /refresh/2026-09-01/catalogue
```

**Reads** the workbook and the existing Matters. **Writes** an `ImportBatch`, an
`ImportRowLedger` row per non-blank row, a `MatterSourceReference` per matched or
created row, and **a new Matter for every row the register has that this database
does not**. That last one is why the backup is above and not below.

Flags, in full: positional `workbook`; exactly one of `--dry-run` / `--apply`
(the group is required — omitting both is an error rather than a safe guess,
because the safe guess is the one people stop reading); `--report-dir`
(default `import-output/import`, which is inside the container and lost with it —
always pass a path under `/refresh`); `--mapping-file` (reviewed owner /
organisation / record-mode mappings, TOML or JSON); `--accept-review-rows`;
`--notes`.

**Idempotent for what the refresh reads.** A row already recorded under this
exact digest comes back as `ALREADY_IMPORTED` and no second source reference is
written, so re-cataloguing the same workbook cannot double what step 3 reads. It
does add another `ImportBatch` and another ledger, so do not do it for fun.

`--apply` refuses unless `REAL_DATA_ALLOWED` is on, and refuses while any row
still needs review unless you say `--accept-review-rows` in as many words —
which imports the rest and leaves those rows out rather than guessing them.

**The gate here is the era contracts, and it is the one gate that can stop the
whole cycle.** A workbook whose headers no longer match the reviewed contract for
its sheet raises a `ContractError` in the dry run. That is a code change
(`docs/data-contracts/*.toml`), reviewed, and not something to work around. Do
**not** run `check_era_contracts` bare to investigate: without `--check` it
rewrites a generated Markdown file inside `/app`. Use `check_era_contracts
--check`, which validates and reports.

Verify afterwards: the dry run and the apply agree on `rows considered` and
`accounting complete: True`, and the apply's `already imported` plus `created`
plus `matched` accounts for every matter row. Step 3 checks the rest.

### 3. Plan

```bash
$R refresh_current_register plan \
  --workbook "/srv/historical-source/excel/Tööd eelnõudega 01.09.xlsx" \
  --rows /refresh/2026-09-01/rows.json
```

**Reads** the workbook's bytes (to hash them), the source references step 2
wrote, every current Matter, the derived register state, and the audit log.
**Writes nothing** — no Matter, no action, no engagement, no audit row. It says
so itself at the end, and it prints the plan digest, which is the only thing
`apply` will accept.

Flags, in full: positional `plan` (there is no `--dry-run` on this command; the
mode is the argument); `--workbook PATH` (required); `--campaigns PATH`;
`--links PATH`; `--expect-plan-sha256`; `--expect-mapping-sha256`; `--today
ISO`; `--json`; `--rows PATH`; `--candidates PATH`.

The first two lines are the gates, in order:

```
Workbook  Tööd eelnõudega 01.09.xlsx
          3db743ac…
Catalogue 2458 rows of 2460 source references
```

The two catalogue numbers are close by construction, not by coincidence:
`real_rows` is `references` minus the rows carrying no source title, so a large
gap between them means the catalogue is wrong rather than that the workbook is
small. The first pair is the workbook digest checked against `REVIEWED_SNAPSHOTS`; an
unreviewed file is refused here with a message telling you to record the digest
first. The `Catalogue` line is step 2's receipt: if it refuses instead, step 2
did not happen for *these bytes*.

> **A warning on that line you can ignore.** If the report prints *"an import
> batch for this snapshot recorded N source rows, more than the M references this
> database holds — the catalogue may be incomplete"*, that is expected on a
> complete catalogue and not a finding. The batch counts every row it read,
> including the 451 blank padding rows and the reserved numbers; the references
> count only the rows that became Matters. The two are never equal on this
> workbook. Judge the catalogue by the `Catalogue` line's own figures and by step
> 2's accounting, not by this warning.

`--today` exists because **staleness moves the digest**. A `JÄRGMISEKS` period
that ends yesterday is `STALE_SOURCE` today and `AUTO` the day before, and only
`AUTO` is inside the plan digest. So:

> **Plan and apply on the same Tallinn day, or pass the same `--today` to both.**
> Otherwise the apply refuses a plan nobody changed, and the refusal names the
> database rather than the calendar.

The report tells you which day it used: `evaluated on`.

`--rows` writes the per-Matter review file. It carries stable identifiers, the
reading and the outcome, and **no register prose**: a title, a `HETKESEIS`
wording and a `JÄRGMISEKS` sentence appear nowhere in it, and a source sentence
appears only as its hash. That is what makes the file safe to open on a laptop
and unsafe to treat as a substitute for the Matter page.

### 4. Review

The report is complete before anything is written — that is the property the
whole design is built around, and it cost real machinery: the enrichment reads
the derived state table, and at plan time that table still describes the
*previous* workbook, so the plan projects the rows the reconciliation would write
and plans over those. One derivation, so the report and the apply cannot drift.

Read, in this order:

**Current work.** `before` and `after`, then the six outcomes. `ACTIVATE` and
`RETIRE` are the portfolio moving. `REVIEW_REQUIRED` is a row where a person
decides, with the reason named — a recorded closure, an ambiguous continuation,
entries somebody wrote here, an open next action, a submission made here.

**Source-authoritative fields.** Five fields the register is allowed to move:
owner, stage, received date, response deadline, addressee. Below them, the
unresolved owner and organisation values with how many rows each is holding up —
that list is a work item, not an error — but a mapping file cannot move it. `refresh_current_register` takes no `--mapping-file`: it resolves with `MappingTables.empty()`, so the only routes open to it are `KnownPeople` for owners and an exact normalised name — or a reviewed alias — for organisations (`app/legacy_import/resolution.py`). The remedy is reference data or a real account, applied separately; `--mapping-file` belongs to step 2, which does take one. `multi-
addressee rows (canonical untouched)` counts cells naming more than one body:
the raw cell and the cardinality are kept, and the canonical singular field is
deliberately left alone rather than recording that Koda wrote to one ministry
when it wrote to three.

**VÄLJA.** Four answers — a date, *ei saatnud*, something else, empty — and
`Submissions created from VÄLJA`, which is `0` and is structurally always 0: a
spreadsheet cell is not final evidence and cannot become a canonical Submission
(ADR 0011).

**Member feedback.** Two columns, each split into a number, an explicit zero and
*not recorded*. Nothing divides one by the other; they are not subsets of one
another and the real data holds rows where more members answered than were asked
directly.

**JÄRGMISEKS.** This block decides whether you may proceed at all. See the gate
immediately below.

**Outreach candidates**, if `--campaigns` was given. See *Outreach* below.

### The JÄRGMISEKS gate — read this before step 5

`refresh_current_register apply` performs the reconciliation **and** the next-
action enrichment in one transaction. There is no flag that omits the second
half, and there should not be: the report an operator approved is a report about
both.

`docs/production-readiness.md` records the `JÄRGMISEKS` / NextAction enrichment
as **blocked, pending a decision by the department head and the lawyers**. The
plan reports an AUTO set; the real-data audit established that those proposals
are not all defensible, so the number is not an approval.

That gate binds this command. The test is arithmetic on the report you are
already reading:

> **In the plan's `JÄRGMISEKS` block, add `AUTO` + `REFRESH_IMPORTED` +
> `REMOVE_STALE_IMPORTED`. Those three are the only outcomes an apply writes.**
>
> - **Zero** — the refresh touches no next action. Proceed.
> - **Above zero** — the apply would create, supersede or withdraw that many
>   structured actions from register prose. **Do not run it.** Take the plan to
>   the department head and the lawyers, and record the outcome in
>   `docs/open-decisions.md`.

`IMPORTED_UP_TO_DATE`, `HUMAN_WINS`, `STALE_SOURCE`, `REVIEW_REQUIRED` and every
`SKIP_*` write nothing. `HUMAN_WINS` in particular is the guarantee that makes
re-running safe at all: one signed-in person anywhere in a Matter's action
history and the whole Matter is theirs, permanently.

For scale: on the 01.09 workbook, two 2026 rows convert that did not convert
before (ADR 0053). Two is above zero.

### 5. Apply

Back up again if anything has happened since step 2 — and something has, because
lawyers use this system every day:

```bash
scripts/deploy/juristid-backup.sh --project juristid-main --compose-file deploy/unraid-main/compose.yml --data-root /mnt/user/appdata/juristid-main --backup-root /mnt/user/backups/juristid-main
```

```bash
$R refresh_current_register apply \
  --workbook "/srv/historical-source/excel/Tööd eelnõudega 01.09.xlsx" \
  --expect-plan-sha256 <the digest step 3 printed>
```

**Reads** everything step 3 read. **Writes**, in one transaction: Matter currency
and the five source-authoritative fields; the derived `CurrentRegisterState`
rows; next actions created, superseded and withdrawn; and — only with `--links`
— `MatterEngagement` pointers. It also refreshes the search projection for every
Matter it touched, so no rebuild is owed afterwards.

**The plan is re-derived inside the transaction and the digest re-compared before
anything is written.** A plan is a photograph; between taking it and approving it
somebody may have closed a Matter, set an action or corrected an owner. A
difference aborts everything rather than applying the part that still matches —
a partial apply against an approved digest would leave a state neither the plan
nor the database describes. **A refusal here is the gate working.** Re-plan,
re-read, re-approve. Do not go looking for a way round it.

Four digests, each answering a different question:

| Digest | Says | Supplied by |
| --- | --- | --- |
| workbook | which bytes were reviewed | hashed from the file; checked against `REVIEWED_SNAPSHOTS` |
| plan | nothing in the database moved between deciding and writing | `--expect-plan-sha256` |
| campaign set | which campaigns the candidates came from | inside the plan digest; re-supply `--campaigns` |
| mapping | which links a person actually approved | `--expect-mapping-sha256` |

The campaign set is **inside** the plan digest, so a plan approved with campaigns
cannot be applied without them, or the reverse. Pass the same `--campaigns` file
to the apply that you passed to the plan.

The output is ten counts — became current, stayed current, left current work,
fields refreshed, derived state rows, next actions created / refreshed /
withdrawn, engagements created / corrected. Write them down beside the plan
digest; step 6 checks against them.

**Nothing here is attributed to you.** Every write records `CURRENT_REGISTER`
provenance and a null actor, deliberately: attributing a machine's reading of a
spreadsheet to whoever happened to run the command would put a person's name on
it, and it is precisely the null actor that tells the *next* refresh nobody has
touched these rows. That is what makes a re-run free.

### 6. Verify

**Convergence — the operation's own dry run reports no work.** Re-run step 3
unchanged. On a converged refresh it prints `ACTIVATE 0`, `RETIRE 0`, every
field change `0`, and the actions that were written now reading
`IMPORTED_UP_TO_DATE`. Running the apply again from that fresh plan changes
nothing at all, engagements and audit rows included — that is asserted by
`tests/test_current_register_refresh.py::test_a_second_identical_apply_changes_nothing_including_engagements`.

Note that you cannot replay the old digest: after an apply the plan is a
different plan, legitimately, and needs its own approval.

**The reconciliation, on its own:**

```bash
$C final_register_cutover --snapshot 3db743ac… --dry-run
```

Same answer, from the other direction: `ACTIVATE 0`, `RETIRE 0`, and the current
counts by sheet.

**The rest of §4 of `docs/production-readiness.md`,** with two adjustments for
this operation:

```bash
$C check_evidence_integrity --skip-storage-scan
$C check_search_freshness
$C check_era_contracts --check
```

`--skip-storage-scan` answers "has any row lost its bytes", which is the question
a register refresh can affect. The full scan also walks the store for
unreferenced objects, and this host still holds the 63 orphaned fixtures from the
2026-08-24 test-suite accident — a non-zero exit from that scan is that, not
this.

A `recovery_fingerprint --compare` against a fingerprint taken before the refresh
**will exit non-zero, and that is the correct result**: canonical counts moved,
which is what you asked for. Take one before and one after and read the
difference; do not treat the exit code as a verdict. That comparison is a restore
check, not a data-operation check.

**And look at the product.** Open Ülevaade and a Matter the plan said moved. The
register's own wording is on the page; the report deliberately never carried it.

### Outreach — the fourth phase, and the only thing that writes a Kaasamine

Optional, and separate because **the matcher writes nothing, ever**.

```bash
$R refresh_current_register plan \
  --workbook "/srv/historical-source/excel/Tööd eelnõudega 01.09.xlsx" \
  --campaigns /refresh/2026-09-01/campaigns.csv \
  --rows /refresh/2026-09-01/rows.json \
  --candidates /refresh/2026-09-01/candidates.json
```

The export is a semicolon-delimited CSV and **five columns are read**: `Section
name`, `Template name`, `Template preview`, `Due at`, `Enqueues`. Deliveries,
bounces, opens, open rate, views, clicks, click rate, unsubscribes, forwards and
complaints are all in the file and none of them is imported. They are engagement
analytics about identifiable members, and a legal file is not where they belong.
The allowlist is an allowlist so that a future export gaining a column cannot
become importable because nobody remembered to exclude it.

The candidate window is fixed in code at 1 January – 28 August 2026 and is
deliberately not a flag: which months of outreach are being placed is a reviewed
decision about a pilot. **It has not moved for the 01.09 snapshot** (ADR 0053), so
a September campaign is outside it. Widening it is a decision about outreach, not
about reading a newer register.

The report gives `HIGH_CONFIDENCE` and `CANDIDATE` counts and, always,
`written without a reviewed mapping   0`. Only a reviewed mapping file creates a
`MatterEngagement` — a JSON list, prepared by a person from `--candidates`, each
entry carrying `reference`, `channel` (`EMAIL_CAMPAIGN` or `PUBLIC_PAGE`),
`source_key` (the template URL or the public page URL), `title`, and optionally
`url`, `occurred_on`, `note`. Every one of the first four is required; nothing is
defaulted into existence, because a mapping missing a `source_key` would produce
a pointer with no import identity and a duplicate waiting for the next run.

```bash
$R refresh_current_register apply \
  --workbook "/srv/historical-source/excel/Tööd eelnõudega 01.09.xlsx" \
  --campaigns /refresh/2026-09-01/campaigns.csv \
  --expect-plan-sha256 <plan digest> \
  --links /refresh/2026-09-01/approved-links.json \
  --expect-mapping-sha256 <mapping digest, printed when --links is read>
```

Idempotent by construction rather than by comparison: identity lives in
`RegisterEngagementImport` on `(matter, channel, source_key)`, so a second run of
the same approval corrects the engagement it already wrote instead of adding a
second one — even if somebody has since edited that engagement's title, which is
the case title matching cannot survive.

**Sendsmaily enqueues are not the register's feedback count.** One 2026 file
records 273 members asked directly against 234 addresses enqueued. Both are true,
the mailing is one channel of several, and neither is ever substituted for the
other.

Delete the export from `/refresh` afterwards.

### Three commands in this family that are not part of the cycle

**`register_next_action_enrichment`** — `plan` / `apply`, with
`--expect-snapshot-sha256` (required), `--expect-plan-sha256`, `--json`,
`--rows`. It is the standalone form of the refresh's fourth phase and **is the
door the JÄRGMISEKS decision is actually written on**. Do not apply it; see the
gate above. `plan` is read-only and safe.

Two things will surprise you if you run `plan` after a refresh:

- It may refuse with *"Derived register state carries more than one snapshot
  digest"*. That is correct, not a fault: a Matter the newer workbook no longer
  names keeps its old derived row, legitimately, and this command fails closed
  rather than planning half from one workbook and half from another. The
  composed refresh handles it; the standalone command cannot.
- Its `apply` success message reports only *next actions created* and states that
  nothing was superseded. Since ADR 0045 that is not true of what it can do — it
  also refreshes and withdraws — so read the counts from
  `refresh_current_register` instead, which reports all three.

**`onenote_policy_area_enrichment`** — `inventory` / `plan` / `apply`, with
`--expect-plan-sha256` and `--json`. Nothing to do with the register refresh; it
proposes canonical `Valdkonnad` from where a lawyer filed a page in OneNote.
`inventory` and `plan` are read-only and are the right way to keep the numbers
current. **`apply` awaits a human review that has not happened** — the first
real-data plan exists (71 relations, 4 of 24 filing locations exact-matched) and
nobody has read it. Mappings enter production only through `REVIEWED_ALIAS_RULES`
in `app/legacy_import/onenote_policy_areas.py`, which is a code change and
therefore reviewed; there is deliberately no admin table, because an unreviewed
row in a database is exactly the guess the module exists to prevent. The apply is
additive — it never removes an area, never creates one, never creates a `Tag`,
never rewrites a captured section — so a second run is a genuine no-op.

**`resolve_archive_recipients`** — read-only unless `--apply`; also `--mappings
PATH` (a reviewed alias file, TOML or JSON) and `--show N` (default 25, `0` for
all). It attaches historical opinion recipients that improved reference data can
now resolve exactly. Idempotent: a Submission that already carries the
organisation is counted `juba seotud` and skipped before resolution is even
attempted. It never creates an `Organisation`, never creates a `Submission` and
never rewrites `recipient_raw`.

**On this instance it has nothing to do, and will have nothing to do for a
while.** It walks `OpinionSubmissionImport` rows, and no canonical opinion
Submission has been filed here — that operation is blocked by two independent
gates (see below). Run it without `--apply` if you like; the unresolved-value
list is the work list for reviewed aliases, ordered most-blocking first. Note
that `--mappings` is the one input in this whole family with **no digest gate**:
whatever file you point it at is what it believes. Keep it under review the way
you would keep a migration under review.

### What must not be run here, and why

- **`refresh_current_register apply` while the plan's `AUTO` +
  `REFRESH_IMPORTED` + `REMOVE_STALE_IMPORTED` is above zero.** The JÄRGMISEKS
  decision belongs to the department head and the lawyers and has not been made.
  There is no flag that separates the two halves of the apply, and adding one
  would be adding a way to approve one report and perform another.
- **`register_next_action_enrichment apply`.** Same decision, said plainly.
- **`opinion_archive apply`.** Canonical historical opinion Submissions are
  blocked by **two** gates and clearing either alone is not enough: filing needs
  an identified administrator, which `AUTH_MODE=shared_gate` does not honestly
  provide, *and* the production canonical-apply path still lacks its P4
  hardening. The plan proposes 244 Submissions. Changing the authentication mode
  would not make applying them correct.
- **`promote_current_register --apply`.** Superseded for the maintained years by
  the reconciliation this cycle runs. Promoting a year would activate rows the
  cutover has decided to retire, from a decision made in a different operation.
- **`historical_cutover_state --apply`** and **`backfill_legacy_owners`** as part
  of a refresh. Each is its own reviewed operation with its own gate. A refresh
  is not the occasion.
- **`final_register_cutover --apply` to advance to a newer snapshot.** The
  refresh composes it, and running it alone applies the reconciliation without
  the enrichment, without the plan digest and without the campaign pin — the
  plan/apply gate removed. It has one legitimate solo use, named in
  `docs/production-readiness.md` §3.9.4: rebuilding the derived state, against
  the snapshot *already* applied, after a migration adds a column to
  `CurrentRegisterState`.
- **`check_era_contracts` without `--check`.** It regenerates a Markdown file
  under the container's `/app`. Always `--check` here.
- **Writing `--rows`, `--candidates` or `--report-dir` anywhere under
  `/app/evidence`.** The evidence tree is the one thing in this system that
  cannot be regenerated, and a stray review file in it is a permanent finding in
  `check_evidence_integrity`.
- **Editing the workbook to make a plan come out differently.** The digest is
  over the bytes; an edited file is an unreviewed file and is refused. If the
  register is wrong, it is corrected at the source and re-approved.
- **Everything already in *What must not happen here* below**, which applies to
  this section unchanged — no `down -v`, no prunes, no test suite through this
  stack, no real data leaving this host.

### If it goes wrong

| | Situation | Answer |
| --- | --- | --- |
| | The plan digest no longer matches | The gate worked. Re-plan, re-read, re-approve. Never force it. |
| | The apply refused mid-cycle | Nothing was written; it is one transaction. |
| | The apply succeeded and the numbers are wrong | Restore from the set taken in step 2/5 and re-plan. A data operation is not undone by re-running it. |
| | The catalogue is wrong | Restore. Source references are immutable by design. |
| | Search looks stale | Rebuild — `rebuild_search_index` is always safe, and derived state is never a reason to restore. |

The decision tree for anything that needs a restore is in
[`RECOVERY.md`](RECOVERY.md), and `docs/production-readiness.md` §5 is the short
form.

## Deploying a release

Deploy a **commit**, never a branch. `git pull` deploys whatever `main` has
become since somebody decided to deploy, and on a repository several people and
several agents push to, that is routinely not the thing that was reviewed. The
difference is invisible until something unreviewed is serving members' material.

So the target is a full 40-character SHA, and the preflight refuses an
abbreviation — two commits can share a prefix and the resolution is silent.

A release is two halves in two places, and the boundary between them is a
contract rather than a preference:

| | Where | What happens |
| --- | --- | --- |
| **A** | off the host — GitHub Actions | the image is built for that one commit, saved, digested, and published as a release artifact |
| **B** | on the Unraid host | the artifact is verified, loaded, and the already-built image is deployed |

**The Unraid host never builds the production image.** Not `docker build`, not
`docker compose build`, not `up --build`. Its writable Docker storage sits
behind a USB-attached parity disk, an on-host build is slow enough that BuildKit
has died in the middle of one, and the machine serving the Chamber's real data
is the wrong place to find out how a half-finished build fails. The host's only
image operations are `docker load` and `docker compose up`. `compose.yml` still
carries a `build:` stanza — CI resolves and checks it — which is exactly why
every command below that could build says `--no-build`: the command, not the
operator's memory, is what keeps the contract.

### A. Build the release image — off the host

`.github/workflows/release-image.yml` is the build. It takes one input, the full
40-character commit, checks out exactly that commit, refuses a dirty tree,
builds `linux/amd64` with `GIT_SHA` baked in, asks the image which commit it is
and refuses one that answers wrongly, proves the application imports inside it,
and saves it. Run it from the Actions tab, or:

```bash
gh workflow run release-image.yml -f sha=<full-40-char-sha>
```

The artifact it uploads is named `release-image-<sha12>` and holds three files,
where `<sha12>` is the first twelve characters of the commit:

| File | What it is |
| --- | --- |
| `juristid-main-web-<sha12>.tar.gz` | the image, `docker save`d and gzipped, tagged `juristid-main-web:<sha12>` |
| `juristid-main-web-<sha12>.tar.gz.sha256` | the archive's SHA-256, in `sha256sum -c` format |
| `release-manifest-<sha12>.txt` | revision, image tag, image id, platform, build stamp, archive size and SHA-256, builder |

That is the whole artifact contract. There is no second format, no `latest`, and
no image built anywhere else that counts as a release. Download the artifact
from the workflow run and transfer all three files to the host — the path they
land in is the operator's; nothing below depends on it.

### B. Deploy the release — on the Unraid host

Everything from here runs on the host, in **one shell**, in this order.

### 1. Write down what is running now

```bash
curl -s https://juristid.orgusaar.ee/healthz
```

The `revision` this returns is the rollback target, and it is the one fact that
becomes unavailable the moment the deployment goes wrong. Read it before, not
after.

### 2. Preflight

Read-only. It changes nothing, moves nothing, and prints the commands to run.

```bash
scripts/deploy/juristid-deploy-preflight.sh --repo /mnt/user/appdata/juristid-main/repo --target <full-40-char-sha> --compose-file deploy/unraid-main/compose.yml --env-file /mnt/user/appdata/juristid-main/config/juristid.env --data-root /mnt/user/appdata/juristid-main --backup-root /mnt/user/backups/juristid-main
```

It verifies that the commit exists, that it is ahead of what is running, that
the checkout is clean, that the Compose file resolves with no host port and a
read-only corpus, that the environment file exists and is mode 600, and that
there is room to write a backup.

**If it says the checkout is dirty, stop.** Somebody changed something on the
server, and finding out what matters more than this release. Do not
`git reset --hard`, do not `git clean`, do not check out over it: whatever those
changes are, deleting them destroys the only record that they existed. Nothing
in a deployment is urgent enough to be worth that.

### 3. Move the checkout to the reviewed commit

```bash
git -C /mnt/user/appdata/juristid-main/repo checkout --detach <full-40-char-sha>
```

Detached on purpose. The deployment is at a commit, not on a branch that can
move underneath it.

The checkout is no longer where the application code comes from — that is
inside the loaded image. What the checkout supplies is `compose.yml` and the
scripts under `scripts/deploy/` at the reviewed revision, so that the Compose
file the stack is started from and the backup script that runs before the
migration are the ones that commit was reviewed with.

### 4. Name the release

Two variables, exported once, before the first command that resolves the
release image. Everything after this point — the identity check, the migration
plan, the migration, the replacement — reads them, and that is the point: one
shell, one identity, no step that can quietly resolve a different image.

```bash
export JURISTID_GIT_SHA=<full-40-char-sha>
```

```bash
export JURISTID_IMAGE_TAG=${JURISTID_GIT_SHA:0:12}
```

The SHA is what the image was built from and what it reports as its revision.
The tag names the image `docker load` puts on the host in the next step, so the
previous release stays on the host under its own name and a rollback is a tag
rather than a rebuild.

`juristid-main-web:local` is the fallback tag Compose uses when
`JURISTID_IMAGE_TAG` is unset, and it is deliberately the one tag that gets
overwritten. A `migrate` that runs against `:local` is a schema change made by
whatever was last hand-built on this host, which is not the thing that was
reviewed. Exporting both variables first is what stops that. And on this host
it stops one more thing: a `run --rm web` whose tag resolves to an image that
does not exist would make Compose build one, here, from the `build:` stanza.

### 5. Verify and load the release image

With the three files from part A in the directory they were transferred to,
check the digest **before** loading — the `.sha256` was written beside the
archive by the job that built it, and it is the only thing that proves the
bytes the host is about to load are the bytes that job produced:

```bash
sha256sum -c juristid-main-web-${JURISTID_IMAGE_TAG}.tar.gz.sha256
```

It must print `OK`. Compare the digest with the `archive_sha256` line of
`release-manifest-${JURISTID_IMAGE_TAG}.txt` as well, and the manifest's
`revision` line with `$JURISTID_GIT_SHA`. Anything else — a mismatch, a missing
file, a manifest for a different commit — means the transfer is not the
release, and nothing is loaded.

Then load it:

```bash
docker load < juristid-main-web-${JURISTID_IMAGE_TAG}.tar.gz
```

`docker load` prints the tag it restored, which must be
`juristid-main-web:${JURISTID_IMAGE_TAG}` — the same tag Compose resolves from
the variables exported in step 4. Then ask the image itself:

```bash
docker run --rm --entrypoint cat juristid-main-web:${JURISTID_IMAGE_TAG} /app/GIT_SHA
```

It must print the full `$JURISTID_GIT_SHA`. This is the line the preflight's
printed plan carries, and it is load-bearing: it is the last check before a
command that would *build* if the image were missing.

Loading writes no business data, mutates no database and does not replace the
running application — it only puts the candidate image on the host. Everything
that changes something still happens after the backup in step 8.

### 6. Read the migration plan — from the target image

```bash
docker compose -p juristid-main -f compose.yml run --rm web python manage.py migration_plan
```

`run --rm`, not `exec`. The distinction is the whole point of the step.

Application source is `COPY`ed into the image and this stack bind-mounts no
source into `/app` — only evidence, derivatives, the OneNote source and the
read-only corpus. So the container that `exec` would enter is still the
**previously deployed** image, and moving the checkout in step 3 changed nothing
inside it. Asked there, `migration_plan` reads the old release's migration graph
and can answer "No pending migrations." for a release that carries several,
which is the reassuring answer given at exactly the wrong moment.

`run --rm web` starts a one-off container from the image loaded in step 5 — the
target code — against the running database. That is the pair the question is
about: **new code, current schema.** It reports and never migrates.

If everything is additive, the old web process keeps working against the new
schema while it is replaced, which is what makes the sequence below safe.

If anything is not additive, this is not a rolling deployment. Decide first
whether the release now serving survives the new schema; if it does not, tell
the department, take the application down, migrate, and bring it back. Six users
and an announced ten minutes is better than a silent compatibility gamble.

### 7. Release-specific pre-migration audits

Most releases have none, and this step is then nothing. Some do, and the reason
they belong *here* rather than after the migration is that a target-image
one-off container can answer a question about the **current** database before
the schema moves — which is when a finding is still cheap to act on.

The same `run --rm web` shape as step 6, for the same reason: the audit has to
be the new release's audit, because an audit the old image does not have cannot
be run by entering the old image.

**A release that installs a new integrity constraint.** Where the target release
adds a database-level guarantee over relationships that already exist, run that
release's read-only integrity check against the still-unmigrated database
first — for example:

```bash
docker compose -p juristid-main -f compose.yml run --rm web python manage.py check_evidence_integrity --skip-storage-scan
```

`--skip-storage-scan` keeps it to the relational question — "does any row point
at something it should not" — instead of walking the evidence store, which is a
maintenance window rather than a deployment step. The command reads; it does not
migrate and it writes nothing.

**If it reports relationship findings, stop.** No migration, no repair, no
"fix it and carry on". Rows that a new constraint would reject are a question
about which of the two records is right, and that is a human decision taken with
the register in front of you — not something a deployment decides at half past
eight. Nothing here repairs anything automatically, and nothing here should
grow that ability.

**Read the findings, not the exit status.** The command exits non-zero when it
found *anything*, and what it found decides what to do — three different
answers, and only the first two have a summary paragraph naming them:

| It reported | Which means | What to do |
| --- | --- | --- |
| `foreign-final-evidence`, `evidence-less-restricted`, `foreign-current-version` | a relationship is wrong; the bytes are intact and point somewhere they may not | **Stop.** A human decision about the record of what Koda sent. No automatic repair, and **no restore** — a backup holds the same relationship. |
| `missing-object`, `size-mismatch`, `sha-mismatch`, `unreadable-object` | evidence is not intact | **Stop.** A restore, by [`RECOVERY.md`](RECOVERY.md). Never edit rows to match what the store now holds: a row corrected to fit missing bytes turns a detected loss into an undetectable one. |
| anything else — `version-number-gap`, `stuck-processing` | neither of the above, and neither summary line is printed | **Stop and find out what it is.** A version number gap means something reached the table outside the application; a stuck extraction is a worker question. Neither is caused by this release and neither is answered by continuing past it. |

A run that did not finish — the command missing from the image, the container
failing to start, the database unreachable, a traceback instead of a report —
is **not a pass**. It is an audit that did not happen, and the release waits
until it has.

Whether a given release needs this step is a property of that release, and the
release note says so. Do not make it unconditional: a check for a constraint the
target release does not contain is a command that either does not exist in the
image or answers a question nobody asked.

**Today that release exists.** Main carries `submissions/0005` and `0006`, the
two migrations that install exactly this kind of guarantee over relationships
already in the database. A production instance that has not yet crossed them
owes the audit above before it does — once, on the release that installs them.
It is named here rather than left to a release note because the condition is a
fact about the database in front of you: `migration_plan` from step 6 lists
them as pending, and that listing is what makes this step apply.

### 8. Back up

Always, and immediately before the migration rather than that morning:

```bash
scripts/deploy/juristid-backup.sh --project juristid-main --compose-file deploy/unraid-main/compose.yml --data-root /mnt/user/appdata/juristid-main --backup-root /mnt/user/backups/juristid-main
```

This is the copy a failed migration is rolled back to. Everything written
between it and the failure is lost in that rollback, which is why it is taken
now — after the image is loaded and the plan is read, immediately before the
first command that changes the database — and not earlier. Loading an image is
not a schema change, and the backup's job is to be the last thing before one.

### 9. Migrate, then replace

Still the same shell, so still the same two variables, so still the same image
that step 6 read the plan from.

```bash
docker compose -p juristid-main -f compose.yml run --rm web python manage.py migrate
```

```bash
docker compose -p juristid-main -f compose.yml up -d --no-build
```

`--no-build` is not decoration. `compose.yml` has a `build:` stanza, and a plain
`up -d` that found the tag missing — a typo in the export, a load that did not
happen — would build the image on this host, silently, from whatever the
checkout holds. With `--no-build` the same mistake is an error naming the
missing image, which is the honest outcome. Unqualified otherwise: `web`,
`extractor` and `searchindex` all run the release image and all move together.

Migrations are a deliberate step, never container start-up work: on boot they
would run on every restart, including the restart that happens at three in the
morning because the host rebooted.

### 10. Post-flight

```bash
docker compose -p juristid-main -f compose.yml exec -T web python manage.py deployment_readiness
```

`exec`, and here that is the correct word. Steps 6, 7 and 9 asked questions
about code that was not running yet, so they had to start a container from the
target image. This one asks about the process that is now serving, so it enters
it. The rule is not "`run` is safe and `exec` is not" — it is that the command
must be aimed at whichever image the question is about.

Fails closed on an unapplied migration, a migration the database has that this
build does not, a missing or wrongly-mounted storage root, a PostgreSQL major
that is too old, or a build that cannot say which commit it came from. It reads
and reports; it never migrates.

It also prints the authenticator this process resolved, and that line is worth
reading rather than scrolling past. `AUTH_MODE` lives in the host's environment
file, which is deliberately outside Git, so nothing in this repository can tell
you what it says — only the running process can, and this is where it does.
**Confirm it is the mode this deployment is supposed to be running** (`shared_gate`
today; `cloudflare_access` after the Access application exists). A real-data
process with no authenticator would already have been refused at step 9 by
`juristid.E006`, so what this catches is the subtler case: a mode that starts
cleanly and is not the one anybody intended.

Then confirm the running revision is the one that was deployed:

```bash
curl -s https://juristid.orgusaar.ee/healthz
```

`revision` should be `$JURISTID_GIT_SHA` — the exact one, compared in full, not
the twelve characters the image tag happens to share with it. The footer's build
time is the `build_stamp` from the release manifest and moves with the image on
its own, so a revision that changed beside a build time that did not is a
replacement that did not actually happen.

Anything beyond this is release-specific and the release note names it. A
release that changes how something is indexed, projected or derived may need a
rebuild afterwards; a release that changes none of those needs nothing here.
Run what that release asks for, against the running new image — so `exec`, like
the readiness check above. Step 11 is the one this repository currently owes.

### 11. The search index contract, when the release changes it

Conditional, like step 7, and conditional on the same kind of fact: a release
that leaves `app.search.models.INDEX_VERSION` alone needs nothing here.

**Why a release can need it at all.** Every `SearchDocument` records the
contract it was built under, and the query chokepoint reads only rows carrying
the current one (`app.search.services.visible_documents`). So a release that
changes `INDEX_VERSION` makes every existing row ineligible the moment it starts
serving — deliberately, and before any rebuild. A row indexed under the old
contract can hold text the new contract would not put in it, and no predicate
can take words back out of a stored vector, so the only safe answer is to stop
reading the row. Search then returns too little and the reader can tell, rather
than returning something they should not see and cannot.

**Why nothing does it for you.** `searchindex` is running by now — step 9's
unqualified `up -d` started it with everything else, and it needs no separate
command. But what it consumes is `SearchRebuildDebt`, and every row in that
table is recorded by a *vocabulary edit*: an Organisation renamed, an alias
changed, a Tag or a PolicyArea renamed, a person's display name changed
(`app/search/freshness.py`, ADR 0041). Nothing writes a row meaning "the
contract changed at deploy", and the table arrives from its own migration empty.

So the worker is healthy, `check_search_freshness` is green, and the whole
corpus is unreadable — all three at once, and truthfully. **A green
`searchindex` says nobody has renamed anything. It does not say the corpus was
rebuilt.** The two questions are answered by two different commands, and this is
the one that answers the second:

```bash
docker compose -p juristid-main -f compose.yml exec -T web python manage.py rebuild_search_index
```

`exec`, like the readiness check: by now the running image *is* the target
image. The rebuild empties and refills inside one transaction, so readers keep
the previous complete index for its whole run and a failure leaves that index in
place — which is why it is safe here and safe to run again. It is safe beside
the worker too: both take the same rebuild gate, so if they overlap one waits.

Then prove it, rather than assuming it:

```bash
docker compose -p juristid-main -f compose.yml exec -T web python manage.py check_search_integrity
```

**Any finding stops the release.** Rows left on an older index version means the
rebuild did not take and the corpus is still unreadable; a completeness finding
means a canonical record is not projected; a null vector is a row that exists
and can never match; a row claiming a Matter its own source does not belong to
is a projection that disagrees with the register. None of those is repaired
here, and none of them is a reason to declare the release done. `check_search_integrity`
reads and reports; `rebuild_search_index` is the repair, and it is the only one.

Last, the other question — is anything *owed* that has not been done:

```bash
docker compose -p juristid-main -f compose.yml exec -T web python manage.py check_search_freshness
```

```bash
docker compose -p juristid-main -f compose.yml ps
```

Nothing should be owed, and `ps` should show `juristid-main-searchindex`
running beside the other four. From here on the worker keeps the index fresh on
its own, and this step is not part of an ordinary release again until something
changes `INDEX_VERSION` a second time.

**Today that release exists.** `INDEX_VERSION` is `AUTH003.1`, set by ADR 0038,
and the `searchindex` service arrived with ADR 0041 — both in the range a
production instance still on an earlier revision has not crossed. Such an
instance owes this step once, on the release that first serves `AUTH003.1`.

It is named here rather than left to a release note for the same reason step 7
is: the condition is a fact about the deployment in front of you rather than a
claim this repository can make about it. Run the step. If the corpus was already
rebuilt under this contract, `rebuild_search_index` costs a few seconds and
`check_search_integrity` confirms it; if it was not, those few seconds are the
difference between a search that works and one that silently answers nothing.

### Rolling back

Code-only rollback is the same part-B sequence with the previous reviewed SHA.
Its image is still on the host under `juristid-main-web:<its sha12>` unless
somebody removed it, so step 5 is usually only the `docker run … /app/GIT_SHA`
check; if the tag is gone, re-download that commit's release artifact and load
it — never build it here. Rolling back *across* a migration is not the same
sequence, and it is not a command — [`RECOVERY.md`](RECOVERY.md) has the
decision tree.

### What a deployment must never do

A deployment carries code and schema migrations. It does **not** import the
register, promote the current register, apply an opinion archive, backfill
owners or run a historical cutover. Those write the Chamber's record, they each
have their own review gate, and none of them may happen because somebody
restarted a container. A test asserts that no service's start-up command can
reach one.

## Backup, restore, disaster recovery

All of it is in [`RECOVERY.md`](RECOVERY.md): what is canonical and what is
rebuildable, how a set is produced and verified, the restore order for a fresh
host, the rollback decision tree, and — stated plainly there — the fact that the
current backups are a local recovery copy rather than off-host disaster
recovery.

## What must not happen here

- **No `docker compose down -v`.** The `-v` removes volumes, and the evidence
  tree is the one thing in this system that cannot be regenerated.
- **No `docker system prune`, no `docker volume prune`, no host reboot as a
  troubleshooting step.** This host runs other people's services.
- **Nothing that stops or removes `juristid-test`** or any unrelated container.
- **No `git clean`** in the checkout.
- **No image build.** Not `docker build`, not `docker compose build`, not
  `up --build`. The release image comes from `release-image.yml`, verified and
  `docker load`ed; see **Deploying a release**.
- **No weakening of the safety checks** to get a process to start. If
  `manage.py check` refuses, the configuration is wrong, not the check.
- **No real data leaving this host.** Not into Git, not into CI, not into a PR
  comment, not into a screenshot, not into a log uploaded anywhere. The
  repository is public.
- **No test suite through this Compose project.** See below — this one has
  already happened.

### Never run the application test suite through this stack

Not `docker compose -p juristid-main run … pytest`, not `docker exec
juristid-main-web pytest`, not a one-off container attached to
`juristid-main-internal`. Tests belong in CI, in a development checkout, or in a
Compose project created for testing.

**This is not advice; it happened.** On 2026-08-24 a pytest run in a container
derived from this stack's image wrote 63 synthetic test files into
`/mnt/user/appdata/juristid-main/evidence`. Three things combined:

1. the image sets `DJANGO_SETTINGS_MODULE=config.settings`, and pytest-django
   reads the environment *before* `pyproject.toml` — so production settings won;
2. `docker compose run` inherits the service's environment and volumes, so
   `EVIDENCE_ROOT=/app/evidence` still pointed at the real evidence bind mount;
3. Django's test runner created and dropped its own `test_juristid` database, so
   the rows vanished and the bytes did not.

The consequence was 1,473 bytes of harmless fixtures and an integrity report
that has exited non-zero ever since. The same path was open to a larger file,
and the same misconfiguration decides which *database* a plain `manage.py`
command talks to — there the test runner would not have saved anything.

Three controls now make the mistake fail closed rather than write:

- `pyproject.toml` passes `--ds=config.test_settings`, the one form that beats
  an inherited `DJANGO_SETTINGS_MODULE`;
- `config/test_settings.py` refuses to finish importing when the environment has
  `REAL_DATA_ALLOWED` on, carries `JURISTID_RUNTIME`, or names a deployment's
  storage root — and both application services here set `JURISTID_RUNTIME`
  precisely so a test process started through them stops;
- `tests/conftest.py` gives every test its own temporary evidence, derivative
  and legacy-source directories, whether or not the test asks.

Any of the three alone would have prevented it. If a refusal ever gets in the
way, the environment is wrong, not the refusal.


## Cutover

`juristid.orgusaar.ee` is the live hostname and stays the live hostname. The
synthetic rehearsal used it first; moving it means pointing the DNS record at
this stack's tunnel, not creating a second name.

```bash
# The rehearsal keeps running, on its own project, network and database.
# Its data is preserved. Only the public name moves.
cloudflared tunnel route dns juristid-main juristid.orgusaar.ee
docker compose -p juristid-main -f compose.yml up -d tunnel
```

`juristid-test` is not stopped, not removed, and not migrated. If it needs a
public name of its own afterwards, give it an internal one — never a second
live one.

## Before calling it live

From a new private window, in this order:

| | Check |
| --- | --- |
| A | an unauthenticated visitor sees no Juristid data at all |
| B | a wrong password is rejected |
| C | repeated failures are rate limited |
| D | the correct password opens the department Ülevaade |
| E | the dashboard is useful with no persona selected |
| F | selecting a persona changes `Minu töö` |
| G | changing persona changes the profile context |
| H | persona switching appears in the security audit |
| I | RESTRICTED material does not appear in the department scope |
| J | there is no direct origin bypass — no host port answers |
| K | signing out ends both the persona and the gate |
| L | historical data survives a container restart |
| M | a search for a term that certainly matches returns results |

A–L are covered by `tests/test_shared_gate.py` as logic. Doing them in a real
browser is what catches the difference between the logic and the deployment.

M is not covered by anything here, and cannot be: every test suite builds its
own index, so the one state this catches — a real corpus projected under a
contract the running code will not read — is unreachable from a test. Search a
Matter title you know exists. An empty result is what step 11 exists to prevent,
and it is the only symptom it has: nothing is red, nothing is logged, and every
container is healthy.
