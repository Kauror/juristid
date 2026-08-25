# 0033 — Ülevaade drill-down parity, and what a number is allowed to open

- **Status:** Accepted
- **Date:** 2026-08-25
- **Depends on** ADR 0010 (server-rendered HTML, browser tests as the verifier),
  ADR 0021 (the register's VÄLJA column and what it does and does not mean) and
  ADR 0028 (the archive is evidence, never a canonical Submission). None is
  amended.
- **Extends** master specification §18.9 (a number and its list are one query).

## Context

Ülevaade is a page of counts. Each one is a promise that a list exists behind it
holding precisely that many things, and §18.9 already says the count and the
list must be one query rather than two similar ones.

A QA pass walked every clickable number on the page. Roughly half of them broke
the rule, and always the same way: the number came from one query and the link
from a second that resembled it.

- *N üle tähtaja* counted late **work items** and opened a list of **Matters**.
  Two missed deadlines on one file promised two rows and produced one.
- *N valdkonda vastutajata* counted **policy areas** and opened every ownerless
  **Matter** — a different population with a different number.
- *N inimest* on Minu tiim opened the whole register.
- The team strip summed per-person counts, which silently drops every unowned
  file, while its link opened a register list that includes them.
- *Esitatud arvamusi 2026* and *Suletud teemasid 2026* carried the year in the
  label and not in the link.
- *Näita kõiki 41* under Vajab sekkumist carried `sekkumine=hilinenud`.
- *Näita ülejäänud 3* under Tähtajad opened the whole register sorted by date.
- *Näita kõiki 23 valdkonda* opened the register, which lists no valdkond at all.

One of these had been argued for rather than overlooked. *Üle tähtaja* counts
late **work**, an `Oluline tähtaeg` past its day carries no open NextAction, and
the register's `?tegevus=` therefore could not express the population — so the
figure narrowed this page's own intervention list instead. The reasoning was
sound and the outcome was still wrong: a figure that behaves unlike every figure
beside it teaches people that the strip is unreliable, and it left the reader on
a summary page when they had asked for a list.

## Decision

### 1. A number opens a list of the same kind of thing it counts

A count of Matters opens Matters. A count of people opens the people. A count of
policy areas opens the areas. Where the thing counted is not something the
register lists — people, areas — the destination is the list of them on this
page, reached through a stable fragment (`#inimesed`,
`#vastutajata-valdkonnad`), not the register filtered by something adjacent.

Where the two counts genuinely differ — the Tähtajad table counts deadline rows
and its link opens Matters — **both** numbers are printed and each is labelled
for what it is. One file with two deadlines is two lines and one thing to open,
and a page that hides that distinction produces a link that looks broken.

### 2. `?too=` — the dated-work populations, addressable from a URL

The register gains one dimension: `?too=`, whose values are the named
populations of the shared read model in `app/matters/work_items.py`
(`hilinenud`, `ulevaatamiseks`, `tahtaeg-nadalal`, `tahtaeg-jargmisel`,
`sekkumist`), plus `?too_vastutaja=` to narrow to the person **responsible** for
that work.

It is not a second query language and it measures nothing new. The filter calls
`work_items.work_population_ids`, which is the function Ülevaade counts with.
The figure and the list are one selector called twice — the same construction
`register_filters` was created for, extended to the populations a NextAction
cannot express.

`?too_vastutaja=` is separate from `?vastutaja=` on purpose, and the two must
never be merged. Ownership and responsibility are different facts (§18.1): a
NextAction belongs to whoever must do it, an `Oluline tähtaeg` to the Matter's
owner, and Ülevaade prints them side by side rather than summing them.

The register also gains `?suletud=<year>` — the year a Matter was **closed**,
which is not `?aasta=`, the reporting year the file belongs to. A 2024
consultation closed in 2026 is one of 2026's completions.

### 3. Arriving from a number lands on the rows

Every register link from this page carries `#tulemused`. A filtered register
opens with a search box, a status strip and a narrowing panel that expands
itself whenever a filter is active; the rows are below all of it. The results
region is focusable, so the fragment moves focus as well as the viewport and a
keyboard user does not tab through every control to reach the list they clicked
a number to see.

### 4. *Arvamusi koostamisel* is canonical Submissions in DRAFT

The figure returns to Ülevaade, and it means what the canonical domain means:
`Submission` rows this reader may see whose status is DRAFT. Its destination is
`/arvamused/?olek=DRAFT`, and both halves are produced by
`submissions.workspace.drafting`.

Deliberately **not** the register's VÄLJA column, which is what the Excel era
recorded about a sent date (ADR 0021), and deliberately not the historical
archive, whose 767 letters are evidence of correspondence that was *sent* rather
than work in preparation, and which are not Submission rows at all (ADR 0028).
Production will therefore read nought until the department starts drafting here,
which is the honest answer rather than a fuller-looking page.

### 5. Saabunud leaves the primary navigation

The route, its models, its intake action and its data are untouched. It is a
triage surface somebody opens when they are triaging, not a destination in the
daily rotation, and holding a fifth fixed item on the bar compressed the four
that are. The way in is Ülevaade's *Uued teemad* rail, which is where the
question "what has arrived" actually occurs to somebody, and the browser suite
asserts both halves: absent from the bar, and still a working page.

### 6. "Ootab pahavarakontrolli" was a false statement to a reader

All files in the Juristid corpus are known to be free of malware. The state the
statistic reported is real — those versions are not yet offered to the
extraction queue — but naming it after the scanner told every reader that their
own archive might be infected and that somebody had an unresolved safety
question to answer.

The gate does not move. `malware_scan_state` is not written, `eligibility_q` is
unchanged, and no file becomes extractable because a label was rewritten. What
changed is the business-facing wording: **Ootab tekstitöötlust**, with the copy
saying out loud that the files are known to be malware-free and that this is a
technical extraction and indexing precondition rather than a safety finding.
`EXTRACTION_AWAITING_SCANNER` is version 2 — the wording moved, the population
did not.

The row also leaves the Andmekvaliteet *Järjekorrad* list. It is a property of
this system's pipeline rather than of the record, it is not a queue anybody
works through, and while it sat among the business queues readers took it for
outstanding work. It is reported in full in *Teksti eraldamine* on the same tab.

## Consequences

- One new register dimension and one new date filter, both offered in the
  narrowing panel as well as reachable from a link: a filter a figure can set
  and the panel cannot is one somebody can arrive at and never reproduce.
- `?too=` reads the dated work model per request (two queries) rather than
  filtering in SQL. That is the cost of having one definition instead of two,
  and the population is bounded by open FULL Matters this reader may see.
- `matters_without_action` now routes through the register's own
  `?tegevus=puudub` selector. It was reader-blind, so a Matter whose only open
  action is restricted below it counted as instructed on Ülevaade and as
  uninstructed in the list the figure linked to.
- Two suites hold the rule: `tests/test_overview_drilldowns.py` walks the page's
  object model and checks every `(count, destination)` pair against the real
  view, and `e2e/test_kpi_navigation.py` does the same in a browser, clicking
  each number and asserting the destination's own count, its visible filter
  chip, and that the results section is in the viewport and focused.
- The seeded browser world's dated work is anchored so that band membership does
  not depend on the weekday CI runs on. A row four days out fell in *Sel
  nädalal* on a Monday and *Hiljem* on a Thursday, which turned the Minu töö and
  Ülevaade visual baselines red for a reason that was the calendar.
