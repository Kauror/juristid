# ADR 0061 — Seotud materjalid: derived suggestions, human-confirmed links

- Status: accepted
- Date: 2026-09-05
- Stage: pre-QA (shared-gate development phase)
- Related: master specification §11 (`MatterRelationship`), §14 (search) and
  §21 (assistive intelligence); ADR 0005 (authorization and visibility
  inheritance), ADR 0006 and ADR 0013 (the search projection), ADR 0019 and
  ADR 0023 (the opinion archive and its projection), ADR 0037 (the
  business-write boundary), ADR 0038 (child visibility in projections and the
  index-version gate), ADR 0041 (search freshness), ADR 0055 (what an archive
  link asserts), ADR 0056 (who reads the archive)
- Number: 0061. PR #143 (deterministic document-assisted intake) holds 0060.

## Context

Juristid holds fifteen years of Matters, several hundred sent opinions and the
whole historical opinion archive, and a lawyer opening a 2026 file about the
Packaging Act can find what the Chamber said about it in 2022 only if they
already know to search for it. The master specification names "previous Koda
position retrieval" and "suggest related Matters" as the highest-value
assistive features (§21.1) and, separately, a typed `MatterRelationship` with
`SUCCESSOR_OF`, `RELATED_TO`, `IMPLEMENTS_OR_TRANSPOSES` and `DUPLICATE_OF`
(§11). Neither existed. `Matter.superseded_by` carries the continuation
relationship and nothing else; `OpinionArchiveMatterLink` says an archive
letter *concerns* a Matter and is written by the reconciliation and its
reviewers; a `Submission` belongs to exactly one Matter.

The feature brief asked for a section on the Matter page — «Seotud
materjalid» — that quietly proposes related Matters, earlier opinions and
archive letters, lets the lawyer confirm, choose or dismiss, and otherwise
stays out of the way. It also asked that the recommendation be deterministic,
that nothing be linked automatically, and that no reader learn of anything
they may not open.

## Decision

### 1. Suggestions are derived; only decisions are stored

A suggestion is computed when «Võimalikud seosed» is opened and forgotten when
the page is closed. Nothing about it is persisted: no score, no
`last_recommended_at`, no cache table, no queue. Three tables hold what a
person decided, in a new app `app.related_materials`, whose migrations depend
on `matters`, `submissions` and `legacy_import` and on which none of those
depend back:

| Table | Statement | Written by |
| --- | --- | --- |
| `MatterRelation` | these two Matters are related | «Seo teemaga», «Lisa seotud teema» |
| `MatterBackgroundMaterial` | this existing opinion or archive letter is useful background here | «Lisa taustmaterjaliks» |
| `RelatedSuggestionDismissal` | do not suggest this candidate for this Matter | «Ei ole seotud» |

The migration creates the tables and nothing else. No relation is inferred,
backfilled or migrated from similarity; every existing Matter begins with zero
rows in each.

### 2. `MatterRelation` is the `RELATED_TO` slice of the specification, and only that

The specification describes a typed relationship. This feature implements one
symmetric, untyped statement — *related* — and this is an **approved scope
reduction**, not an accidental contradiction: the product owner chose it in
the decision that authorised this work.

- `SUCCESSOR_OF` already exists as `Matter.superseded_by`, written only by
  `close_matter`, and stays authoritative there. The engine excludes the
  successor and the predecessor from the candidate pool so the continuation is
  shown once, where it always was, and never offered as a "possibility".
- `IMPLEMENTS_OR_TRANSPOSES` and `DUPLICATE_OF` are **directional**. A
  directional relation cannot live in a table whose whole design is that A↔B is
  one row in one canonical order. When a real product need for them appears,
  they get a table shaped for direction; the rows here migrate to it as
  `RELATED_TO` without loss, because that is exactly what each of them says.
- No `type` column fixed to a single value. A column that can hold one value is
  not an abstraction, it is a promise nobody has asked for.

The pair is canonical: the smaller primary key is `matter_a`, a check
constraint refuses any other order, and a unique constraint holds one row per
pair. UUIDs compare the same way in Python and in PostgreSQL, so the service
and the constraint agree. `link_related_matters` is idempotent from either
side and atomic; two people pressing the button at once produce one row.

### 3. Background material is not an archive link, and not a move

`MatterBackgroundMaterial` holds exactly one of two explicit nullable keys — a
`Submission` or an `OpinionArchiveBinary` — under a check constraint, with a
partial unique constraint per source. No generic foreign key: a POST must not
be able to name a row of any table.

Choosing an opinion as background reads it and writes nothing to it. Its
Matter, its `sent_at`, its recipients, its evidence and its status are what
they were, and a test compares the row before and after. Choosing an archive
letter writes zero `OpinionArchiveMatterLink` rows, and withdrawing the
selection removes zero of them: that table asserts that a letter *concerns* a
Matter (ADR 0055) and this one says somebody found it useful here, which is a
weaker and different claim. Reconciliation, apply, derivation and the archive
projection recipe are untouched.

### 4. The engine: bounded pools in PostgreSQL, explained scores in Python

No model, no embedding, no external service, no click history. Given the same
Matter, projections and catalogues, the same ranked candidates come back with
the same reasons.

**Pools.** Each starts from the visibility chokepoint its surface already
uses — `visible_documents` for MATTER and SUBMISSION rows, `Matter.objects.
visible_to` for catalogue overlap, `visible_archive` for the archive — and
returns a few dozen rows. Text candidates are found through the existing
stored title vector, the `estonian` configuration and a prefix query against
the `simple` vector, plus the MATTER title trigram; catalogue candidates need a
shared tag or two overlapping facts to enter the pool at all. The projections
are read, not changed: `INDEX_VERSION` and `ARCHIVE_INDEX_VERSION` are what
they were and no rebuild is needed. Where a projection row is absent or stale
the suggestions are less complete and the page still works.

**Signals and weights** (`app/related_materials/engine.py`):

| Signal | Weight | Reason shown |
| --- | --- | --- |
| same named act | 6.0 each, at most two | «Sama õigusakt: pakendiseadus» |
| two or more subject words in the title | 5.0 | «Sarnane pealkiri: …» |
| one subject word in the title | 1.5 | «Pealkirjas kordub: …» |
| shared tag | 2.0 each, at most two | «Sama silt: pakend» |
| same sender or addressee body | 1.5, once | «Sama asutus: …» |
| shared policy area | 1.0, then 0.5 | «Sama valdkond: …» / «Samad valdkonnad: …» |
| subject word in the text only | 0.75 each, at most two | «Tekstis kattuvad: …» |
| same track | 0.5, only beside another signal | never shown |
| material on a confirmed related Matter | 5.0 | «Seotud teema arvamus» |

The threshold is 3.5. One act or one strong title clears it alone; a tag plus
a ministry clears it; an area alone (1.0), a ministry alone (1.5), a track
alone (0.5) or a year alone (nothing) never do, and the tests hold each of
those. The score is internal and is never shown, least of all as a percentage.
Five candidates are shown, «Näita veel» extends to fifteen, and the order is
score, then the newer file, then the title, then the key — never the planner.

**Named acts** are recognised in Python, conservatively: a compound ending in
`seadus`/`seadustik` with a stem in front of it, or a genitive attribute that
is not procedural boilerplate followed by the head noun. `seadus`, `eelnõu`,
`muutmise seadus`, `seletuskiri` and `kooskõlastamine` are scaffolding and
never a subject. The phrase is preserved for the reason line rather than
reconstructed from a stemmer's lexeme.

### 5. Authorization before ranking, including deduplication

Nothing a reader may not open is in any pool, so it cannot shape a count, a
rank, a reason, a tie or the hidden-candidates count. Matter candidates come
through `Matter.objects.visible_to`; opinions through the projection's live
child-visibility join; archive letters only for a reader `may_read_archive`
admits, all or nothing. A confirmed relation to a Matter the reader may not
open is not shown, not counted and not hinted at from the visible side; a
relation is a fact about two files, never a key to one of them.

The archive channel excludes four things, each a claim already made about the
letter that outranks a suggestion: a letter already filed onto *this* Matter; a
letter any reviewer called *not an opinion*; a letter already chosen or
dismissed here; and a letter whose canonical Submission **this reader can see**,
which is offered instead through the opinion channel. A Submission the reader
cannot see does **not** suppress the letter — the archive is legitimately
theirs to read, and a hidden row must not become an existence oracle by
silencing a visible one. `REJECTED`, `DUPLICATE` and `DEFERRED` are decisions
about one proposed Matter match, not about the letter, and do not exclude it.
An archive candidate is labelled «Arhiivimaterjal»; «Varasem arvamus» is used
only for a canonical Submission.

### 6. No automatic linking, and the human decides

No threshold creates a relation, a background row, an archive link, a
Submission, a tag or a policy area. Recommendations write nothing; opening,
expanding, paging and showing the hidden candidates write nothing; a test
counts every affected table before and after. Only the six POST routes write,
each `login_required`, `business_write_required`, POST-only and CSRF-protected,
resolving its target through the population the caller may see and answering
404 otherwise (ADR 0037). Confirmed relations and background selections record
their actor and time on the row and write a `ChangeEvent` per Matter concerned
(`MATTER_RELATION_ADDED`/`REMOVED`, `BACKGROUND_MATERIAL_ADDED`/`REMOVED`);
those types are deliberately not in the timeline allowlist or the department
feed, because a relation's other side may be a Matter the reader of *this*
timeline may not open. A dismissal keeps its actor and time on its own row and
writes no event. Removing a relation does not dismiss; adding a relation or a
background row clears the corresponding dismissal.

Manual linking («Lisa seotud teema») reuses the header search's authorized
five-row ranking and posts to the same `link` route as a suggestion's button.
A person's knowledge of a relationship needs no score.

### 7. Optional, lazy, secondary

The section sits below the structured facts and above the chronology. The
confirmed lists render with the page from two queries; the suggestions live
behind «Võimalikud seosed» with **no count on the closed control**, because
a count would cost the computation the control exists to defer. Opening it is
one `hx-get` that swaps the section; without scripting the same URL is a small
page of its own. No websocket, no worker, no queue. Nothing here is a warning,
a badge or a review obligation, and a Matter with nothing confirmed and nothing
opened is one row.

### 8. Deletion semantics

A relation or background row cascades with its Matter; a background row
cascades with its Submission and holds its archive binary under `PROTECT`,
because archive evidence is never deleted and the schema should say so;
dismissals cascade with whatever they name. The TEST-data purge inventory
discovers the new tables from model metadata and reports a relation between a
TEST and a REAL Matter as a straddling blocker rather than removing a business
relationship the REAL Matter carries; a test holds that.

## Alternatives considered

- **A typed `MatterRelationship` now.** Rejected for v1 by the product owner:
  one real product need (*related*) does not justify a directional framework,
  and the symmetric table migrates to a typed one without loss if that need
  arrives.
- **Reusing `OpinionArchiveMatterLink` for archive background.** Rejected: it
  would weaken what that table asserts and entangle a lawyer's reading choice
  with the reconciliation's evidence claims.
- **A generic foreign key on the background and dismissal rows.** Rejected: an
  arbitrary `(type, id)` pair from a POST is not a validated domain reference.
- **Running the recommendation through the interactive `search_documents`.**
  Rejected: its tiers answer "what did a person type", not "what resembles this
  known Matter", and its typed-query semantics would have to be faked.
- **A second full-text index, a new projection column, or an index-version
  bump.** Rejected: an optional feature must not manufacture a production
  rebuild. Everything it needs is already indexed or is in canonical tables.
- **Counting suggestions on the ordinary page render.** Rejected: it would put
  the whole computation on every Matter view for a number most readers will
  never act on.
- **Ranking by views, ownership or click history.** Rejected: the question is
  business similarity, and behaviour is not evidence of it.

## Consequences

- The Matter page gains one section and two queries; nothing else about its
  render changes. Two visual baselines that photograph the overview move by
  construction and are retaken through the documented candidate mechanism.
- Six new business-write routes join the boundary matrix.
- The archive, the reconciliation, the search recipe and the index versions are
  unchanged; deploying this needs a migration and no rebuild.
- The quality target is honest emptiness: «Praegu ei leitud piisavalt tugevaid
  võimalikke seoseid.» is a valid result, and the threshold is not lowered to
  fill the section. Real-world usefulness is judged after deployment, on real
  Matters, by the people who work them.

## Reversibility

High. The three tables are additive and can be dropped; the engine is one
module with no persisted state; the section is one include and one context
key on the overview. If a typed relationship is later required, `MatterRelation`
rows migrate to it as `RELATED_TO`.
