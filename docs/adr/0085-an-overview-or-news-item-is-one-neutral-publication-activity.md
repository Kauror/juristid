# 0085 — An `Ülevaade / uudis` is one neutral publication activity, and any public web address will do

**Status:** accepted
**Date:** 2026-09-16

**Supersedes ADR 0081 §3 entirely** — the `koda.ee` trust boundary — and
**ADR 0081's name for the record**, which was `Kodulehe ülevaade` and appears in
its title, §1, §2, §4 and §5. **Narrows ADR 0083 §2** on one point: the
publication date now has a default, which arrives when the published path is
taken rather than on an untouched form. Everything else those two decided —
three states, two columns, both-or-neither, no title, no attachment, no work
item, the closed-Matter rules, the audit vocabulary, the optimistic
concurrency — stands exactly as written.

ADR 0083's own 2026-09-16 amendment (the primary action `Lisa ülevaade`) is
undisturbed in substance and is restated in §3: the button now reads
`Lisa ülevaade / uudis`, for the reason that amendment gives.

## Why it is being revisited at all

ADR 0081 built the record from one true observation: a lawyer finishing a round
of work decides the membership should be told about it on the Chamber's website.
It then wrote that observation into the record's **name** and into its
**validator**, and both turned out to be narrower than the thing people do.

The write-up does not only appear on koda.ee. It appears in a trade paper, in a
member association's newsletter, on a partner's site, in a ministry's news feed
and in whatever the communications team negotiated that month. It is the same
act each time — *this file, published where somebody can read it* — and the file
could record exactly one variant of it. A lawyer holding the address of a piece
about their own Matter in a magazine had nowhere to put it, which is the
condition ADR 0081 was written to end.

Two decisions follow, and a third that is a consequence of the first.

## 1 — One activity, and deliberately no type selector

**The record is `Ülevaade / uudis`.** One launcher chip, one panel, one
lifecycle, one strip, one chronology milestone kind. A Koda overview and a news
item are the same publication activity and are recorded by the same control.

**There is no `liik` column and no kind chip**, and that is the load-bearing
half of this section.

A type selector here would be a question asked at the *wrong moment*. The
commonest way this record starts is `Plaanis`: somebody decides the file should
be written up, and at that moment there is no address, no date, no headline and
frequently no decision yet about *where* — the communications conversation has
not happened. A required `liik` would force a guess; an optional one would be
blank on most rows and therefore useless to anything that read it.

And nothing needs it. **The link is sufficient.** A published row carries the
address, which is the one fact that actually distinguishes an overview from a
news item, and it is a fact rather than somebody's classification of one. A
reader who wants to know which they are looking at follows a labelled link and
finds out; a `liik` chip would tell them the same thing one step earlier and be
wrong the first time somebody picked the wrong one, with no way to notice.

This is the reasoning ADR 0054 gives for retiring `NextAction.kind` and ADR 0081
§1 gives for refusing a title, applied one more time: a stored classification
that no rule consumes is a field that decays, and the cost of decay is a page
that confidently states something nobody checked.

The alternative was considered, and the Alternatives section says why a `kind`
would have been worse than merely useless.

## 2 — Any public `http`/`https` address, and nothing else changes about links

ADR 0081 §3 allowed `https://koda.ee/…` and `https://<anything>.koda.ee/…` and
refused everything else, on the reasoning that this is «the Chamber's own page
on the Chamber's own site».

**That premise is now false**, and a boundary whose premise is false is not a
boundary. It would have to be widened one host at a time by whoever needed it
widened — `kaubanduskoda.ee` this month, a partner's domain the next — and a
list maintained under that pressure is a list that says nothing after a year.
`Väline seisukoht` reached the identical conclusion for the identical reason two
days earlier: the set of institutions that publish is «every institution in
Estonia and the EU», and it took no allow-list (ADR 0084 §3).

### Decision

**A published `Ülevaade / uudis` may point at any syntactically valid public
`http://` or `https://` address.** `normalize_overview_news_url` is the one door
every writer of the column passes through, and it enforces:

| rule | why it is kept |
| --- | --- |
| `http` or `https` only | `javascript:` and `data:` are script delivery dressed as an address; `file:` and `ftp:` point where the reader's browser cannot usefully follow |
| a **parsed host**, non-empty | `https://user:pw@/path` has an authority and no host at all — not an address anybody can follow (finding F-2) |
| **no userinfo** — `user:`/`:pw@` refused outright | there is no published page behind a credential, and a credential on the file is a credential in an audit payload, on a rendered page and in everybody's browser history |
| refused, never truncated, past 1000 characters | a link cut off is a link that no longer resolves, and a stored pointer that is quietly wrong is worse than a refusal naming the row (finding F-1) |
| a parsed comparison, never a substring | the rule that made `koda.ee.example.com` detectable is the rule that makes *any* host claim readable |

`http` joins the list because this address is now frequently somebody else's
site, and a 2019 piece served over plain HTTP is still a true record of what was
published. Refusing it would mean refusing to record what happened, which is the
mistake ADR 0081 §3 made in the other direction.

The safety half is shared with `normalize_engagement_url` and
`normalize_external_position_url` through `_normalize_public_link` rather than
copied, because it is not the kind of rule that may exist three times: it is the
difference between a clickable control on a page a lawyer trusts and a
script-delivery vector, and a second copy is a second place for `javascript:` to
be forgotten. Userinfo is the one rule this caller adds and the other two do not
take — an `Ülevaade / uudis` address is pasted *now*, from a page somebody has
open, where an engagement link may come out of a historical register nobody can
re-issue.

### What is given up, and why it was not protecting anything

Nothing about access. **This application never contacts the address.** It is not
fetched, not crawled, not resolved, not previewed and not used to authorise
anything; it is stored, and it is rendered as one labelled link. So there is no
request the widened rule lets through, no SSRF surface, no internal host
reachable that was not reachable before, and no permission decided by a string.

What the old rule did buy was a weak *provenance* signal — a reader could assume
a link went to koda.ee. That assumption is now stated instead of assumed: the
row prints a label, not an address, and the label says what the link is for
rather than where it goes.

### The link is still labelled, and never printed

`Ava ülevaade või uudis`, replacing `Ava kodulehel`, which named a site the
address no longer promises. `target="_blank"`, `rel="noopener noreferrer"`, and
a visually hidden «— avaneb uues aknas» so the new tab is announced rather than
merely happening. A raw URL as a row's own text is a line a reader has to parse
instead of read, and it is the one shape in which a look-alike address is
believed — which matters *more* now that the host is not fixed, not less.

**Nothing claims the page is still there.** A saved address is a record of what
somebody stated on the day they stated it. No link checker runs, no status is
stored, and a dead link years later is a fact about the web rather than an error
in this file.

## 3 — The date default arrives with the published path

ADR 0083 §2 gave the panel two optional boxes and **no `initial` on the date**,
because a pre-filled date would make «neither filled» unreachable: every plan
would arrive carrying a publication date nobody typed, and the panel would lose
its ability to say «this is only owed».

That is right, and it left the *other* path retyping today's date on every
publication — the friction ADR 0078 §2 named for `Kaasamine` and fixed there.

### Decision

**The date fills itself at the moment the published path is chosen, and not
before.** Typing into the link box is that moment: somebody with an address in
their clipboard has already decided this row is a publication.

* The value is **today as the server resolved it** (`timezone.localdate()`,
  rendered through `format_estonian_date`), carried to the browser in
  `data-publication-default`. Not the browser's clock: a laptop set to another
  day must not file a publication date this application would never have chosen,
  and the date is the one column on this record that is nobody's but the
  person's (ADR 0081 §2).
* It is **visible**, in the box, before saving. It can be read, changed, retyped
  and cleared.
* It fires on the transition from an empty link box to a non-empty one, **once**.
  A form nobody has touched still submits two empty boxes, so `PLANNED` stays
  reachable — the property ADR 0083 §2 refused an `initial` to protect.
* It never overwrites a date that is already there, and **never fires again once
  the person has touched the date box themselves**, clearing included. A date
  deliberately emptied stays empty.
* With scripting off the panel is what it was: two plain text boxes, typed by
  hand. Nothing here is required to reach either outcome.

### What it changes for somebody who pastes an address

With scripting on, **typing only an address now files a publication** where it
used to meet a refusal asking for a date. That is the point: the date it asks
for is today on the overwhelming majority of those saves, and the refusal was
making people type it by hand.

So «an address and no date» stops being a state somebody reaches by *not*
typing, and becomes one they reach by clearing the box — which is exactly the
gesture that means «not today». The other half, a date with no address, is
unchanged and still reached by leaving the link box alone.

Both halves are refused identically by the service, which sees a POST and not a
browser, so nothing about the rule moved — only how easy each half is to arrive
at by accident. `e2e/test_website_overview.py` keeps a test on each, and
`test_typing_only_an_address_now_records_a_publication` states the change
outright rather than leaving it to be discovered.

**It is a default, not a fallback.** No service, no form `initial` and no model
default supplies a publication date. `publish_website_overview` stores the
submitted value and derives nothing, so an empty date box is still a refusal
naming the missing date rather than a silent stamp of today. That distinction is
the whole of ADR 0078 §2 and it is unchanged: what is new is a box being filled
*on screen, in front of the person, on an action they took*.

The planned row's own `Lisa link ja avaldamiskuupäev` form keeps its ordinary
`initial=timezone.localdate`. That form only ever publishes — opening it *is*
choosing the published path — so the default has always been correct there.

### The words

| surface | reads |
| --- | --- |
| launcher chip | `+ Ülevaade / uudis` |
| panel note | «Märgib, et sellest teemast peaks tulema ülevaade või uudis.» |
| optional pair's legend | `Kui ülevaade või uudis on juba avaldatud` |
| link box | `Avaldatud ülevaate või uudise link` |
| date box | `Avaldamise kuupäev` |
| primary action | `Lisa ülevaade / uudis` |
| planned strip | `Ülevaated / uudised`, «Ülevaade või uudis on plaanis, aga veel avaldamata.» |
| planned row's disclosure | `Lisa link ja avaldamiskuupäev` |
| chronology milestone | `Ülevaade / uudis` |
| chronology link | `Ava ülevaade või uudis` |

The primary action follows ADR 0083's own amendment of 2026-09-16: a button
names the record the form creates, not one of the two states it can create it
in, because `planeeritud` is untrue of every submission that carries an address
and a day.

## 4 — Everything the lifecycle already decided is untouched

Stated explicitly, because a rename is exactly the kind of change under which a
rule quietly stops being enforced.

* **Three states and the same transitions.** `PLANNED → PUBLISHED`,
  `PLANNED → CANCELLED`, `PUBLISHED → PUBLISHED` as a correction. **No
  `PUBLISHED → CANCELLED` in v1** — the page is up, and a record denying it would
  be the file disagreeing with the world. `CANCELLED` is terminal.
* **`PUBLISHED` requires both an address and a date, and the database says so.**
  The two implication constraints, the published/cancelled timestamps, the
  closed status vocabulary and `(matter, url)` unique per published row are all
  exactly as ADR 0081 §6 wrote them.
* **Zero, one or many per Matter.** Nothing is unique on `matter`.
* **No deletion.** A mistaken plan is cancelled; a wrong address is corrected.
* **The four audit events**, their payloads and their absence from
  `TIMELINE_EVENT_TYPES`.
* **The closed-Matter rules, both halves.** Closure cancels every plan the file
  still owed, in `close_matter`'s own transaction, each with its own event naming
  the closure — and is never blocked by one. A closed Matter refuses a new
  record, a publication and a cancellation under
  `lock_open_matter_for_business_write`; a published address and date stay
  correctable, because closure has never meant that a fact recorded wrongly must
  stay wrong (ADR 0075 §12, ADR 0081 §5).
* **Optimistic concurrency.** `expected_revision` is compared after the row lock
  and before anything is decided, a stale form writes nothing, and the refusal
  does not advance the token.
* **Every absence ADR 0081 §4 listed.** No body text, no title, no person
  assignment, no document upload, no generic result field. No `NextAction`, no
  `MatterImportantDate`, no effect on `Matter.response_deadline`. No search row,
  no archive projection, no document listing, no Submission or work-victory
  metric, no count, no statistic, no filter, no register sort, no badge. Not in
  `Minu asjad`, `Tähtajad`, `Ülevaade` or the department work lists. **Still no
  option on `Uus teema`.**
* **Two surfaces and no third.** The planned strip, and the chronology's two
  completed milestones. A planned row is still not a chronology row.

## 5 — What the rename touched, and what it deliberately did not

A user-facing product rename, held to that.

**Renamed** — every string a person reads: the launcher chip, the panel and its
legend, note and button, both field labels, the strip's heading and its
sentence, the planned row's controls, the chronology milestone and its link, the
four audit event **labels**, the model's `verbose_name`, and every refusal
sentence in `app.matters.services`, each of which now names `ülevaate või
uudise` rather than merely «link» — a Teema page renders three kinds of address
and a refusal that does not say which one it means is a refusal the reader has
to locate first.

**Not renamed**, and each for the same reason — churn with no reader:

* **`MatterWebsiteOverview`, `WebsiteOverviewStatus`, `website_overviews`** and
  every service, selector and view name built on them. Renaming a model is a
  table rename, a `related_name` rename and a migration touching every consumer,
  in return for a word that appears on no screen.
* **The four `ChangeEventType` *values*.** `WEBSITE_OVERVIEW_PUBLISHED` is
  written into `ChangeEvent.event_type` on every row this record has ever
  produced. Renaming a stored value is a data migration, which this change does
  not take. The labels beside them are what a human sees.
* **The URL paths** (`lisa/koduleht/`, `kodulehe-ulevaade/<id>/…`) and the route
  names. These are HTMX endpoints no reader sees; every reference goes through
  `reverse()`.
* **The DOM ids and the launcher panel key** (`kodulehe-ulevaated`,
  `lisa-koduleht`). Ids are not announced and not read.
* **The CSS classes** (`webrow`, `uxtl__weblink`), which were never named after
  the product in the first place.

## 6 — The migrations, and what they do not do

Two, and **both are state-only**:

| migration | operation | what it touches in the database |
| --- | --- | --- |
| `matters/0024_overview_news_verbose_name` | one `AlterModelOptions` | nothing — `verbose_name` is Python metadata |
| `audit/0020_overview_news_event_labels` | one `AlterField` over a `choices` list | nothing — `choices` is validation and display in Django, never a database object |

**No schema change was needed to widen the address rule**, because the boundary
never lived in the database: no `CHECK`, no validator on the column and no
stored host. It was one function, and it is one function now. The constraints
that *do* exist — the both-or-neither pair, the timestamps, the vocabulary, the
per-Matter uniqueness of a published address — are lifecycle integrity and are
untouched.

**No `RunPython`, no `RunSQL`, no backfill, no data migration, no search
rebuild, no archive rebuild.** Every row already stored is a `koda.ee` address,
which the widened rule accepts unchanged; nothing needs rewriting, and nothing
infers a kind, a host or a publication date for a record that does not carry
one.

## Alternatives considered

**Keep `koda.ee` and add a second record for external publications.** Rejected:
two records for one act, which is the duplication ADR 0081 §1 spent seven
sections refusing. A lawyer would have to know which control to open before
knowing where the piece was going to appear.

**A `liik` column — `ÜLEVAADE` / `UUDIS` — with a chip on the panel.** Rejected
in §1, and it would have been worse than merely useless. It asks at planning
time for an answer that exists at publication time; it is blank or guessed on
most rows; nothing consumes it, so nothing corrects it; and the day somebody
built a count on it, the count would be of what people clicked rather than of
what was published. The address already answers the question, from a fact.

**A host allow-list with more hosts on it.** Rejected in §2: a list maintained
under pressure to widen it is a list nobody can rely on, and each addition is a
deploy in response to an ordinary editorial decision.

**Keep `https` only.** Rejected. Once the host is somebody else's, the scheme is
somebody else's too, and a plain-HTTP news page from 2019 is still a true record.
Nothing here is transmitted to the address, so the scheme protects nothing on
this side.

**Check the link, or store whether it last resolved.** Rejected outright. It
would make this application fetch an arbitrary address chosen by a user — the
one thing §2 relies on it not doing — and a «last seen alive» column is a claim
that goes stale silently.

**An `initial` on the panel's date after all, now that the published path is
common.** Rejected for ADR 0083 §2's reason, which has not changed: it would make
«neither filled» unreachable and every plan would carry a date nobody typed. The
default in §3 is reachable only by an action that already means «this is
published».

**Compute today in the browser.** Rejected: a reader whose machine is set to
another day would file a publication date this application would never have
chosen, silently, on the one column that is the person's own statement.

## Consequences

* `Lisa teemale` reads `+ Ülevaade / uudis` where it read `+ Kodulehe ülevaade`.
  The bar's stability contract is unchanged — the chip is a control, the form is
  a separate element, and the order is fixed (ADR 0078 §1) — but the chip is a
  different width, so the visual baselines that photograph the launcher move.
* One new isolated JavaScript island in `static/js/ux.js`, bound through the same
  `once`/`bindAll` machinery as the eight already there and degrading to nothing
  without scripting (AGENTS.md's «minimal isolated JavaScript islands only where
  UX requires client-side state»).
* A paste-and-click publication is one gesture shorter, and «address without a
  date» has to be chosen rather than fallen into. The refusal still exists, is
  still reachable and is still what the server does with half a publication.
* Historical rows read under the new name. A `koda.ee` overview recorded in
  September is an `Ülevaade / uudis` from today, which is true of it — the record
  did not change, only what it is called.
* Nothing that counts, reports, indexes or schedules changes at all.

## Reversibility

**The rename: high and cheap.** Strings, two state-only migrations, and no
stored value anywhere spells the new name.

**The widened address rule: high in the mechanism, and a decision to re-take in
substance.** Narrowing it again is the same function and one comparison, exactly
as ADR 0081 §3 promised in the other direction — but rows recorded in the
meantime may point anywhere, and a narrowing would have to say what happens to
them. It must not be a silent validator change: the honest shapes are «existing
rows are left alone and only new ones are checked» or «the narrowed rule is a
reviewed migration that names every row it would refuse», and choosing between
them is a product decision, not a patch.

**The date default: very high.** One function in `ux.js` and two widget
attributes. Removing it restores ADR 0083 §2's behaviour exactly, because the
server side never learned about it.
