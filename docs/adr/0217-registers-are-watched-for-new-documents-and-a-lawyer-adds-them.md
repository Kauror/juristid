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

## Consequences

- Teemad from the eight ministries above are watched by words; others (Interior,
  Regional and Agriculture, Foreign Affairs, the Government Office, Commission
  files) are not yet. Next steps, each its own
  decision: the model proposing better search words; EIS and an ADR API once their
  operators allow it; Commission consultations.
- A finding never moves `Hetkeseis`, makes no next step and is not a deadline.
- `MatterProceduralLink` stays a pointer (ADR 0089 §4): the watch reads nothing
  from it, and writes one only when a lawyer presses «Lisa link».
