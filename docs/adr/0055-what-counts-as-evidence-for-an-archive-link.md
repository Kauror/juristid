# ADR 0055 — What counts as evidence that a letter belongs to a Matter

- Status: proposed
- Date: 2026-09-01
- Stage: opinion corpus reconciliation (production phase remains plan-only)
- Related: ADR 0019 (the reconciliation and its refusal to invent a recipient),
  ADR 0023 (the searchable archive, whose `CONTENT_MULTI_SIGNAL` class this
  does not promote), ADR 0028 (the archive workspace), ADR 0053 (the 1
  September snapshot this was measured against)
- Number: 0055. `ux/teen-ootan-jalgin` landed first and took 0054, and #114
  renumbered itself to 0057 to leave 0055 and 0056 to this branch. ADR 0056 is
  the archive-access half of this round.

## Context

Production holds 767 archive letters and 244 archive→Matter links — 31.8% of
the corpus. The other 523 have been sitting in the queue since the ingest
completed, and the department's actual complaint is the one the numbers hide:
*Teemad → Arvamused* shows `Saadetud 1`, because one canonical Submission
exists and the archive is a separate tab most people cannot open (ADR 0056
takes that half).

Re-measuring against the refreshed 1 September register (main `a8af5a3e`,
production the same) reproduced the historical figures exactly: 767
occurrences, 767 distinct binaries, 767 candidates all `PENDING`, 243
`STRICT_MULTI_SIGNAL` + 1 `EXACT_BINARY_MATTER` = the 244 links. `verify`
passes. The register refresh changed the Matter population and changed no
match, which is itself worth knowing.

Three things were found by measuring, and each is a defect rather than a
tuning opportunity.

**The stopword list excluded words the folding never produces.** It carried
`poordumine` and `prdumine` for *pöördumine*. `fold` replaces each non-ASCII
character with a space rather than transliterating, so the real token is
`rdumine` — seven characters, in no stopword, and therefore accepted as the
"distinctive" third signal. It appears in 207 register titles. It is the entire
third signal under a link production holds today between *"Pöördumine seoses
õigusloome ja bürokraatia vähendamisega"* and *"Väljatöötamiskavatsustega
seotud pöördumine"*, which are two different subjects. Four more words behave
the same way: `komisjoni` (394 register rows), `konsultatsioon` (396),
`valitsuse` (204), `euroopa` (746). Five existing automatic links rest on one
of them and nothing else.

**A recipient was compared as one opaque string.** 163 of the 192 `UNMATCHED`
files have a register row on their own date that this alone hid. The archive's
filename writes *Majandus- ja Kommunikatsiooniministeerium* and the register's
KELLELE writes *MKM*; `fold` cannot converge an abbreviation with the words it
abbreviates. Separately, a filename naming two ministries never matched a
register row naming one of them.

**A citation was only ever a tiebreaker.** A Riigikogu proceeding number is the
one thing in this corpus both sources wrote down *as an identifier*, and it was
reachable only as the third signal after date and addressee had already
narrowed to a single row. A letter whose VÄLJA is months from its own date — a
real shape, because Koda writes twice about one proceeding and the register
keeps the last dispatch — was invisible to every route.

## Decision

**The stopword list is written in Estonian and folded at import.**
`_STOPWORD_SOURCE` holds the words a reader recognises; `TITLE_STOPWORDS` is
what `fold` actually produces from them. Whether the folding mangles a word
stops being something the author has to predict, which is the whole defect. A
word that folds to nothing long enough to be a title token — `määruse` becomes
`m ruse` — is simply absent, correctly: `title_tokens` could never have emitted
it either. The five measured process words are added, each with its register
frequency written beside it.

`tests/test_opinion_archive.py` asserts the *property* rather than the
contents: every entry must satisfy `fold(word) == word`, must be long enough to
be a token, and must actually be excluded by `title_tokens`. An entry failing
any of those excludes nothing, and that is the bug that shipped.

**A recipient is a set of bodies, not a string.** `addressee_bodies` returns
the whole folded string *and* each comma-separated part *and* the expansion of
any reviewed abbreviation. Both sides of every comparison use it, so the
register index files a row under every name its KELLELE contains and a letter
naming two ministries reaches a row naming one.

It is additive over the old key — the exact whole-string match is still one of
the members — so this can only ever make two strings more comparable, never
less. Both sides being sets does introduce one new way to be wrong, and it bit
during development: a row filed under three names is found three times by a
letter addressed the same way, and reported as three competing rows where there
is one. `_rows_on` deduplicates by Matter, and a test holds it.

`ADDRESSEE_ALIASES` is a table, not a similarity. An abbreviation either is one
of these pairs or it is not, and adding one is a reviewed act. Nothing is
derived from the data at run time and nothing resolves to an `Organisation` —
this is a comparison key, and `opinion_recipients.py` keeps the reference-data
boundary it always kept. *Keskkonnaministeerium → Kliimaministeerium* is
deliberately absent: they look alike and are not the same ministry (ADR 0019).

**A citation is a route of its own**, `EXACT_LAW_REFERENCE_MATTER`, and it is
in `AUTOMATIC_MATCH_CLASSES`. A proceeding number resolving to exactly one
register Matter, corroborated by the addressee or by a date within a day, is
enough to file. It is never enough alone: 25 of the register's 165 distinct
proceeding numbers name more than one Matter, and a number that resolves to
several is returned as a question with the competing count intact.

**The citation route runs after the register route, not before it.** This is
the load-bearing ordering and the first attempt had it backwards, on the
reasoning that a citation is an identifier and a date is not. Measured against
the real corpus, citation-first **withdrew six links production already holds**:
six archive files carry an exact date, an exact addressee *and* a proceeding
number naming two Matters, and a citation-first pass sees the ambiguous number,
refuses, and throws away the two exact signals that resolve it. A seventh
matched `2020_69` on its own day and addressee while its proceeding number
appears only on a 2021 Matter, and citation-first filed it ten months from the
letter.

So the rule is not "citations outrank dates". It is **more independent exact
signals outrank fewer**, and it is written down in the code where the ordering
is chosen, with the measurement that produced it.

Where neither route reaches an automatic class, the citation's explanation is
preferred over `UNMATCHED` — "the proceeding number names two Matters" tells a
reviewer where to look and "no register row was sent that day to that
addressee" does not. Where the register named a candidate, its proposal is
kept: it carries the date and the addressee as well, and replacing it with a
proceeding number would be less evidence rather than more.

**A citation may file a link and still not file a dispatch.** The route exists
to reach Matters whose VÄLJA is nowhere near the letter's own date, so taking
that VÄLJA as the letter's `sent_at` is precisely the invention this stage
refuses — the link would be right and the date made up. `_plan_submissions`
therefore plans a Submission for an `EXACT_LAW_REFERENCE_MATTER` proposal only
where the matcher already recorded `EXACT_SENT_DATE` or
`SENT_DATE_WITHIN_ONE_DAY`. The link is unaffected in every case.

On the real corpus that is 16 of the 19 citation matches keeping a Submission
and **3 becoming link-only**. No other class is touched: they reached their
Matter *through* the date, so the question is already answered for them.

**An exact same-day tie is put to the third signal, not refused on sight.**
Found by re-measuring this branch against production before merging it, and
it is this round's own defect rather than an old one.

Widening the addressee comparison made a register row whose KELLELE names
three ministries reachable from a letter naming one of them — correct, and
the reason 76 files stopped being `UNMATCHED`. But `_from_register` then
refused the moment a second row appeared, and the row it had already earned
was discarded along with the tie. A row that becomes *comparable* arrives
holding two signals, the date and the addressee; the row it displaced held
three. Improving what the matcher can see demoted a match, which inverts the
one rule this document is ordered by.

So the tie is put to the same question `_single_exact` asks, once per
competing Matter, and the answers are **counted, never scored**:

- **exactly one** row carries a third signal — three exact signals against
  the others' two, and it is filed as `STRICT_MULTI_SIGNAL`;
- **none** does — nothing separates the rows, which is what this branch was
  written for, and it stays `REVIEW_REQUIRED` / `MULTIPLE_SOURCE_ROWS`;
- **more than one** does — each holds three, they are level on the only
  evidence accepted here, and a person decides.

Deliberately binary. Two shared title tokens do not beat one, and a law
reference does not beat a title token: `_third_signal` ranks those two kinds
*within* a row, and borrowing that to rank rows *against each other* would be
a hierarchy invented to break a tie rather than evidence that the tie is
broken. Nothing counts tokens, distances, frequencies or confidences.

**Only exact same-day rows.** A one-day gap stays review evidence. What is
resolved here is which of several equally-dated rows the letter is — not
whether a differently dated row is the same letter at all, and a third signal
does not answer that second question.

The invariant this states, and which the tests assert as a before/after:
**widening what the matcher can compare may only ever add matches.** A row
that becomes visible holding two signals must never unseat one holding three.

## What this does not change

- **No score, no threshold, no distance.** Every class still rests on tokens
  both sources wrote. Nothing here calls a model or accepts a similarity.
- **`CONTENT_MULTI_SIGNAL` is not promoted.** Extraction is blocked where the
  real archive lives, so nothing in that class has been produced from the real
  corpus, and promoting a class on unmeasured evidence is the move every other
  class was written to avoid (ADR 0023). The corpus *was* measured this round —
  all 767 files carry an embedded text layer, and content adds a proceeding
  number the filename lacks for 38 of them, resolving 12 more uniquely. That is
  a policy decision to take with the measurement in hand, not here.
- **A one-day date difference is still not automatic.** The register's VÄLJA is
  the next day in 227 of 767 cases, which is what makes a one-day window a
  suggestion. Asserted so it cannot drift.
- **`derive_links` still removes nothing.** The five links resting on a
  now-excluded word keep their Matter as a candidate and keep their existing
  link; withdrawing a link is a reviewer's act, and they go to the queue
  flagged rather than being deleted by a rerun.

## Consequences

Measured with the new matcher against the real production corpus, database and
1 September register, read-only:

| | before | after |
|---|---|---|
| archive files automatically linkable | 244 (31.8%) | 315 (41.1%) |
| `STRICT_MULTI_SIGNAL` | 243 | 295 |
| `EXACT_LAW_REFERENCE_MATTER` | 0 | 19 |
| `REVIEW_REQUIRED` | 327 | 332 |
| `UNMATCHED` | 192 | 116 |
| review queue naming a candidate Matter | 244 | 276 |
| existing 244 links proposed for a *different* Matter | — | 0 |

All 244 existing links re-derive to the Matter they already name — 239 through
an automatic class and 5 naming it from the review queue. None is left without
a proposal, and none moves. Current 2025/2026 Matters carrying an archive letter
go from 57 to 68. `CONTENT_MULTI_SIGNAL` is 0 and stays outside
`AUTOMATIC_MATCH_CLASSES`.

The exact-tie rule accounts for 29 of those files, 27 resolved by a title token
and 2 by a proceeding number, spread across all seven years and landing on 29
distinct Matters — no single Matter, alias or token collects a cluster. One
letter moves the other way: resolving a tie put a second file on one Matter on
one day, so the existing same-day-bundle guard now holds **10** files for review
rather than 8. That guard is unchanged and doing its job.

- **A migration, for choices only.** `0015` alters `match_class` on two models.
  No column changes; Django writes one because the choices moved.
- The production plan digest for this matcher, measured read-only against the
  live corpus and reproduced identically on a repeat run, is
  `90b3bbfd40c4135ba13132186d545a0e67d56638e7e55a81a6205f285f27ce40`. The
  baseline it replaces, measured on current main, is
  `c4b2e9c09151b738ab114ac1f1e8a11f2b51b432b4ec25970a0fb57de9bdc121`. Nothing
  has been applied: production still holds 767 candidates all `PENDING`, 244
  links and one canonical Submission.

## Alternatives rejected

**A document-frequency bound on the third signal.** The first design required
the shared token to appear in at most six register rows. It is deterministic
and it measured badly: 153 of the 244 existing links would stop being
re-derived, because the median shared token appears in 12 rows and
`tulumaksuseaduse` appears in 176. Raw frequency does not separate a subject
word from a process word — *tulumaksuseaduse* genuinely says both documents are
about the income tax act, and *komisjoni* says nothing — so a frequency bound
withdraws the good with the bad. Naming the process words is the smaller and
more honest instrument.

**Deriving the stopwords from the corpus at run time.** It would make the list
self-maintaining and make a match depend on which register snapshot was loaded,
so the same letter could match today and not tomorrow. Reproducibility is worth
more than the maintenance.

**Fuzzy recipient matching.** Rejected for ADR 0019's reason. A reviewed pair
is a decision somebody made; an edit distance is a decision nobody made.
