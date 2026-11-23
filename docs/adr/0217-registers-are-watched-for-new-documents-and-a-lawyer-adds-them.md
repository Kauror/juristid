# 0217 — Registers are watched for new documents, and a lawyer adds them

Date: 2026-11-17 · Status: accepted (owner, 2026-11-17: «build 2+3 combined»)

## Context

A Teema follows a proceeding that lives somewhere else: a ministry's document
register, EIS, the Riigikogu. Lawyers learn about a new version, an amendment
or a committee decision by looking there themselves, and until now nothing in
Juristid looked for them. On production on 2026-11-17 the 230 open Teemad held
no `Menetluse link`, no entry URL and no `Menetluse areng`; what they do hold is
the title, and 28 titles carry the Riigikogu bill number («Kutse- ja
oskusseaduse eelnõu (973 SE)»).

What a program may read (checked 2026-11-17):

- **Riigikogu** — an official open-data API (`api.riigikogu.ee`): a bill by its
  number, its proceeding events, its texts and the opinions sent to it. It
  answers 429 to quick bursts, so calls are spaced.
- **Ministry registers on HiA PDR** — the Ministry of Education and Research
  (`dok.hm.ee`) and the Ministry of Climate (`adr.envir.ee`) answer a JSON
  search over document titles, newest first.
- **The RIK ADR registers** (Justice and Digital, Economic Affairs, Finance,
  Social Affairs) answer only an HTML form posted by a browser, and **EIS**
  forbids automated access (`robots.txt`: `Disallow: /`) and sits behind a
  bot check. Neither is read; access is a question for their operators.

## Decision

1. **The application checks the registers itself**, nightly, with a management
   command (`watch_registers`) run by the host's cron. A Teema is watched where
   it can be identified without guessing: its title names a Riigikogu bill
   («NNN SE»), and the same number is searched in the two PDR registers. The
   Riigikogu bill is the one of the current membership with that number.
2. **What is found is a proposal.** Each new document or step is one
   `register_watch.WatchFinding` on its Teema. Nothing is written to the Teema
   until a lawyer presses «Lisa märkena», which records a `Menetluse areng`
   through `add_procedural_development` — audited, on the locked open Teema,
   exactly as `+ Märge` does. «Lisa link» keeps only the address, as the
   Teema's `Menetluse link` through `+ Menetluse link`'s use case, and writes
   nothing on the timeline. «Jäta vahele» records the decision and writes
   nothing. A finding is as visible as its Teema.
3. **The first check of a Teema does not flood it.** What a register already
   held more than `REGISTER_WATCH_BASELINE_DAYS` (45) days before the first check
   is kept as seen and never offered; newer documents are offered.
4. **An AI model reads, it does not search the web and it does not write.**
   When switched on (`JURISTID_REGISTER_WATCH_AI` and an Anthropic API key),
   Claude judges whether each register document found by a title search belongs
   to the Teema and writes one Estonian line saying what it is. The judgement is
   shown beside the proposal; the lawyer still decides. It receives only public
   facts — the Teema's title and the found documents' titles, dates and
   senders — and **never anything about a restricted Teema**, a note, a file or
   a person. Riigikogu items belong to the bill by construction and are not
   sent.
5. **Every request is fenced** like the koda.ee title preview (ADR 0089 §4):
   `https`, a fixed list of hosts, a timeout, a size cap, a spacing between
   calls to one host, and a switch (`JURISTID_REGISTER_WATCH_FETCH`) that is off
   in tests and CI. Each run is recorded (`WatchRun`) with what it checked,
   found, failed and what the model cost.
6. **The owner is told in the bell.** A run that offers new documents on a
   Teema creates one «Registris uus dokument» notice for the Teema's owner, or
   adds to their unread one (`RegisterNotice`, the inbox's fifth source). It
   opens the review page for that Teema; it is read and unread like a
   «Meeldetuletus», and it leaves the inbox once every document it points to has
   been added, linked or skipped. Documents the model called unrelated do not
   count. A Teema with no owner tells nobody.

7. **Six more ministries, by words** (owner, 2026-11-17: «test the RIK ADR form and
   build it»). Justice and Digital, Economic Affairs, Finance, Social Affairs,
   Culture and Defence publish on RIK's ADR
   (`adr.rik.ee/<jm|mkm|ram|som|kum|kaitseministeerium>`), which has no API: the
   watch posts its search form (UTF-8, whole words, all of them, from a date) and
   reads the results table; a page that is not a results page is an error, never
   an empty answer. A bill Teema («NNN SE») is searched there too. A Teema with
   no bill number is searched in its own ministry's register — a confirmed sender
   first, else the proposed one — by the first two distinctive words of its title
   (`app.register_watch.phrases`). A lawyer replaces the words, or switches the
   search off, on the review page («Otsingusõnad»); the title is not read for that
   register again. Not read: the Interior ministry (a bot challenge), Regional and
   Agriculture (address unknown), Foreign Affairs (not on ADR; not found), and the
   Government Office (its ADR instance is empty).

8. **A log in Haldus** (owner, 2026-11-18: «so it's easy to monitor what it's
   doing … it has to be in the setting … just a log basically»). Haldus →
   Registrite jälgimine lists every run — when, how long, sources read,
   documents new and offered, errors, and the model's use: which model answered,
   calls, documents judged, failed calls and tokens — with the last thirty days in
   sum; a run opens to its errors and the documents it found, with the model's
   verdict and what a lawyer did; «Allikad» lists every search with its last check
   and error. Read-only, for whoever reads business content; a Teema is shown only
   through `visible_to`. A failed model call is counted on the run and named by
   its kind and HTTP status, never its message. The key stays in the nightly job's
   own environment file on the host; the page shows the model a run used, never
   whether a key exists.

9. **A stopped or missing night is red** (quality audit 2026-11-18, QA26-BUG-04
   and OPS-01). One register's unexpected answer is that source's error and the
   night goes on; a run that itself fails is closed as stopped («katkes»), and one
   killed from outside counts as stopped after three hours. One run at a time.
   `production_status` has a «Register watch» row — red when the newest run
   stopped, when no run finished in 30 hours, or when the last run read no
   register — so the host monitor's daily status check tells someone, and Haldus
   says «Katkes» and why. A watch switched on that has never run is green «not run
   yet» until its first scheduled run was due — the first 03:30 UTC after it was
   switched on, plus six hours — then red «never run» (coordinator QA 2026-11-20,
   QA-NEW-05: a schedule never installed stayed green for ever). «Switched on» is
   the later of the release that applied `register_watch` 0001 and
   `JURISTID_REGISTER_WATCH_ENABLED_AT`, which the operator sets only when
   `JURISTID_REGISTER_WATCH_FETCH` (on by default) is turned on after that
   release; without it a late switch-on reads red from the first night. The
   grace also covers a first run that is still going (green «first run in
   progress»; the daily monitor looks at 04:05 UTC), and a switch-on moment in
   the future is refused (red), never trusted. The model is asked in batches of 25 findings, so a cut
   answer costs one batch. «Registris uus dokument» belongs to the Teema's current
   owner, like every other inbox source.

## Consequences

- Teemad from the eight ministries above are watched by words; others (Interior,
  Regional and Agriculture, Foreign Affairs, the Government Office, Commission
  files) are not yet. Next steps, each its own
  decision: the model proposing better search words; EIS and an ADR API once their
  operators allow it; Commission consultations.
- A finding never moves `Hetkeseis`, makes no next step and is not a deadline.
- `MatterProceduralLink` stays a pointer (ADR 0089 §4): the watch reads nothing
  from it, and writes one only when a lawyer presses «Lisa link».

## Amendment 2026-11-21 — the Teema's own «Registritest» tab

Owner, 2026-11-21: the waiting count above the title and «Registrite jälgimine»
in the rail were in odd places and both led away from the Teema. The watch on
one Teema is now the third tab of the Teema page, «Registritest»
(`matters:matter_registers`), beside Teema and Dokumendid:

- the tab is always there; its badge is the count that stood above the title
  (pending, not what the model called unrelated, none on a closed Teema);
- it lists the waiting documents with the same three decisions (§2), which
  post to the same routes and come back to the tab (`tagasi=teema`, the
  redirect built from the finding's own Teema), and the sources with
  «Otsingusõnad» (§7);
- it says in one line whether the Teema is covered: watched (an active source,
  or one the next run would create from the title or the ministry); not read
  (the sender is a ministry in the Consequences' unwatched list, so «kontrolli
  registrit käsitsi»); or nothing to search by (choose a register and words);
- the bell's notice (§6) opens this tab instead of the review page. The review
  page stays as the view over every Teema; the rail link is gone.

The three tabs swap the area under them in place (htmx, `#teema-tabsisu`,
address pushed), so the header and the rail stay where they were.

## Amendment 2026-11-22 — «Lisa märkena» brings the file and the lawyer's words; «Lisa link» goes

Owner, 2026-11-22: a document added with «Lisa märkena» reached the Teema as a
note pointing back at the register, so the lawyer still had to open the register,
download it and attach it by hand, and had nowhere to say why it mattered.

- **The file comes with it.** The press fetches the document's public files
  (`app/register_watch/register_files.py`) through the same door as the nightly
  read (§5: the fixed hosts, no redirects, a timeout, a 25 MB cap per file) and
  hands them to `+ Märge`'s use case as its uploads, so they pass the same type
  check, evidence store and malware scan as a file a person attaches. Where each
  register keeps them: PDR (HTM, Kliima) `/api/v1/documents/<id>/file`; adr.rik.ee
  the page's `/<code>/fail/<id>/<name>` links; the Riigikogu `/api/documents/<uuid>`
  files marked `PUBLIC`. A sitting and the bill itself have none. Fetched before
  the save, so no row lock waits on a register. Nothing here can stop the note: a
  register that does not answer, a type the store refuses (a Riigikogu opinion is
  often `.html`) or a file too large is left out and named in a message, and the
  note keeps its link. This is the first time the watch reads a document's
  content rather than its listing; it happens only on a person's press.
- **The lawyer's words.** «Lisa märkena» opens a «Märkus» field; what is written
  there opens the note, before the source and link (§2's provenance).
- **«Lisa link» is gone.** Keeping only the address was rarely wanted beside a
  note that carries the address and the file; `watch.add_link` went with it
  (status `LINKED` stays for rows already decided).
- The decision words are «Märgi loetuks» / «Märgi kõik loetuks» (was «Jäta vahele»).

## Amendment 2026-11-23 — file links, the lawyer's files, the house style

The owner's round 4 notes.

- **Two links per row.** The title still opens the register's own page; under it
  each `Fail: <name>` opens the document's file directly, because the page does
  not always show it. `WatchFinding.files` (`[{name, url}]`, `None` = not looked
  up, `[]` = none) is filled by `register_files.remember`: after each nightly run
  (`watch_registers` prints `FILES located=N`) and once by
  `manage.py locate_register_files` after the deploy. It reads the listing only —
  the same `locate` the press uses, through the same door — never a file's bytes,
  so §5's "the press is the only content read" still holds. A register that does
  not answer leaves the finding for the next pass.
- **The lawyer's own files.** «Lisa märkena»'s panel is a drop zone; `failid`
  are attached before the register's files, checked as one request's batch
  (`check_upload_batch`) and then by `+ Märge`'s own validation. The same field
  works on an agenda item's «Lisa märkena» (ADR 0219).
- **«Märgi kõik loetuks» on the Teema's tab** (`tagasi=teema`) takes every
  waiting document of the Teema — the folded unrelated ones too — and its
  waiting Government agenda items, and returns to the tab. From Monitooring it
  still takes the register documents only.
- **The house style.** Every model prompt of the watch carries
  `house_style.KODA_STYLE` and every line it returns passes `house_style.fix`
  before it is saved (docs/koda-house-style.md); `manage.py fix_koda_style`
  corrected the lines saved before.

## Amendment 2026-11-23 — the press has a time budget (HR-04)

Health review HR-04: the press fetched the listing and up to five files with 25 s
per socket wait and no total limit, inside the web request, so a slow register
could hold one of the three web workers past gunicorn's 120 s; the worker was
killed and the lawyer's note was lost with it.

- **One deadline per press.** The listing and every file share
  `register_files.FETCH_BUDGET_SECONDS` (40 s). The door (`sources.http`) takes an
  optional `deadline`: the host spacing is not slept past it, each socket wait is
  at most what is left, and a file is read in pieces with the clock checked
  between them. Calls without a deadline (the nightly run, `remember`) read as
  before. The allowlist, no redirects and the size caps are unchanged.
- **Late files are missed like refused ones, and keep their address.** A file
  not brought in time, refused or past `MAX_FILES` is named in the message as
  before, and its own address is now written under the note's source
  («Registri failid, mida ei saanud lisada:», `name: url`). When the register did
  not answer at all, the addresses the nightly look-up stored
  (`WatchFinding.files`) are written instead.
- **Unchanged:** the note and the fetched files are still saved together in one
  transaction; the fetch still runs before `watch.add_to_teema` takes its row
  lock; a second press finds the document decided and writes nothing. No queue.
