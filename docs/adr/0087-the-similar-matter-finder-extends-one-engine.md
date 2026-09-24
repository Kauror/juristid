# ADR 0087 — The similar-Matter finder extends one engine, and reaches `Uus teema`

- Status: accepted; §4's GET and the rejected «post the draft form» alternative
  amended by [ADR 0108](0108-similar-matters-asks-by-post-with-only-its-deciding-fields.md)
- Date: 2026-09-16
- Stage: pre-QA (shared-gate development phase)
- Related: ADR 0062 (`Seotud materjalid`: derived suggestions, human-confirmed
  links — **this record extends it and reverses none of it**), ADR 0005
  (authorization and visibility inheritance), ADR 0038 (child visibility in
  projections and the index-version gate), ADR 0037 (the business-write
  boundary), ADR 0070 (`Õigusakt` is a reviewed vocabulary, not a tag and not
  the track), ADR 0006 and ADR 0013 (the search projection)
- Number: 0087. 0085 is held by the overview/news publication branch and 0086
  by the Kaasamine waiting workflow, both taken before this work began.

## Context

A lawyer opening a 2026 file about the Packaging Act can find what the Chamber
said about it in 2022 only if they already know to search for it. That was the
context of ADR 0062, and ADR 0062 answered it: `app/related_materials` holds a
deterministic, explainable, permission-filtered engine that proposes related
Matters, earlier opinions and archive letters, says why each appears, shows
five, never shows a score, writes nothing, and lets a person confirm or dismiss.

The brief for this round asked for a similar-Matter finder with those same
properties. **Most of it already existed.** What did not was three things, and
one of them is the reason the round is worth doing at all.

1. **The engine could not see `Õigusakt`.** It recognises a *named act* out of
   a title — «pakendiseadus» — which is the strongest signal it has. It has
   never read `Matter.legal_instruments`, the reviewed seventeen-row instrument
   vocabulary ADR 0070 established, because that field arrived after ADR 0062
   shipped. Two files both classified as a `Määrus` shared a structured fact the
   recommendation could not use.

2. **A candidate card said what a Matter *is*, never what Koda *did***. Title,
   reference, state, addressee and the reasons. The next question a lawyer asks
   — «is there an opinion in there, did it go out, did we win» — was answerable
   only by opening the file, which is the click the card exists to inform.

3. **The help arrived one step too late.** Suggestions existed on a saved
   Matter. The moment a lawyer most needs to know whether the Chamber has done
   this before is while they are deciding to open the file, on `Uus teema` — and
   that surface had nothing.

## Decision

### 1. One engine, extended. No second matcher and no second section

Everything here is added to `app/related_materials/engine.py` and rendered
through the same `.relatedcard`. There is no parallel `Sarnased teemad`
implementation on the Matter page, no second set of weights and no second
reason vocabulary.

This is the whole architectural decision and it is worth stating plainly: a
lawyer must not have to learn that the cards under the create form and the cards
in the Matter rail answer the same question by different rules. A candidate that
qualifies while a file is being typed still qualifies the moment it is saved,
because it is the same function with the same threshold, and a test holds the
two answers word for word.

The threshold, the caps, the tie-break, the internal-only score, the
authorization-before-ranking rule, the pool bounds and the
nothing-is-linked-automatically rule are ADR 0062's and are unchanged.

### 2. `Õigusakt` is a signal, and a weak one

A shared `LegalInstrumentType` scores `W_INSTRUMENT_FIRST` 1.0 and
`W_INSTRUMENT_SECOND` 0.5, capped at two, and reads «Sama õigusakti liik:
Määrus» or «Samad õigusakti liigid: Määrus, Direktiiv».

**Weighted like a policy area, and for the same reason.** Seventeen values cover
the register and `Seadus` covers much of it by itself, so on its own the fact
says close to nothing: an instrument type alone is 1.5 at most against a
threshold of 3.5, and instrument-plus-ministry is 2.5. Both stay below the line
by construction and both have a test. «Same ministry, and both are laws»
describes the department's ordinary week, not a reason to read a file.

**It is a different claim from the named act, and both may appear.** `W_ACT` 6.0
says these two files are about *pakendiseadus*; this says they are both a
*määrus*. The labels are deliberately distinct — «Sama õigusakt: …» and «Sama
õigusakti liik: …» — so a card carrying both is telling a reader two true
things rather than the same thing twice.

**`Muu` is not a match.** It is the vocabulary's escape hatch; what it means
lives in `Matter.legal_instrument_other`, which is one Matter's own free text.
Two files that both failed to fit the list have agreed about nothing, and
offering «Sama õigusakt: Muu» would report a shared absence as a shared fact.
The free text is not matched either, here or in the pool: comparing two people's
prose for equality is the kind of similarity this engine does not do.

In the pool query a shared instrument type is worth one, exactly as an area is,
so it cannot reach `structured__gte=2` and admit a candidate on its own.

### 3. The card says what Koda did there — existence, never content

`MatterOutcome` carries three booleans: a recorded Koda opinion, a sent
Submission, a confirmed `Töövõit`. Each renders as one badge with a dot and a
word, so none is carried by colour alone.

**No count, no date, no title and above all no generated summary.** The card's
job is to say whether opening the file is worth the click and to link to where
it is read. Producing a précis of an earlier Koda position is explicitly outside
v1: it would be this application telling a lawyer what the Chamber thinks, and
an earlier position is never to be reused unchanged.

**Each is computed under the reader's own child visibility, not the Matter's.**
A visible Matter may carry a restricted opinion, and a badge saying one exists
is exactly the existence disclosure ADR 0038 forbids. `outcomes_for` runs each
of the three through the record's own `visible_to`, and a test restricts an
opinion on a visible Matter and asserts both that the reader gets no badge and
that the owner still does — so the test measures visibility rather than a broken
query.

**Three queries, whatever the candidate count**, asked once per kind of record
over the keys that will actually be rendered, after the list has been cut to its
limit. The indicators decide nothing — not a score, not an order, not the
threshold — so computing them late cannot change which candidates appear.

### 4. `Uus teema` asks the same engine, and hides when it has nothing

`GET /teemad/uus/sarnased/` answers the create form with up to five cards.

**No draft Matter is created.** The form's current answers arrive as query
parameters and leave as a fragment; a Matter saved to compute a suggestion would
be a file in the register nobody meant to open. The route writes nothing at all
— no relation, no dismissal, no audit event, no `last_recommended_at` — and a
test counts the tables before and after.

**Nothing is proposed and nothing can be accepted.** The fragment contains no
form and no button. There is no Matter yet for a relation to attach to, so there
is no `Lisa` and no `Ei ole seotud`; the card is a title, its reasons, what Koda
did there, and a link that opens the file **in a new tab**, because the person
is in the middle of an unsaved form and navigating away would throw their work
out to answer a question they asked in passing.

**The form's values are preserved structurally, not carefully.** The response
replaces `#sarnased-teemad` and nothing else, so it cannot name a control it does
not render. A refusal, an intake swap or a chip being picked leaves everything
typed exactly where it is. The route does not echo the form's values back, so it
cannot be used to reflect content, and the catalogue keys it reads are resolved
against the reference vocabularies rather than trusted from the request.

**It recomputes on the four fields that decide the answer** — title, summary,
`Valdkonnad`, `Õigusakt`, and the two organisation controls — at 600ms, and on
nothing else. `Märkmed`, `Hetkeseis` and the deadlines are not matching signals,
and a round trip on a field that cannot change the result is a round trip that
only costs.

**Below `DRAFT_MIN_TERMS` subject words and `DRAFT_MIN_FACTS` structured facts
the answer is silence** — an empty response, so the heading goes with the cards
and a form with nothing to suggest carries no empty box and no «midagi ei
leitud». A title alone is not enough: «Eelnõu kooskõlastamine» is a title half
the register shares, so the test is *subject* words through the same
generic-word filter the saved path uses.

A draft carries no tag, because `Uus teema` has no `Sildid` control and a tag is
the department's own vocabulary applied to a file that exists.

**Matters only.** The opinion and archive channels answer «what belongs on this
file», which needs a file.

### 5. Hiding when empty is the create surface's rule, not the Matter page's

The brief asked that the section disappear when nothing clears the threshold.
That is done on `Uus teema` and deliberately **not** on the saved Matter.

ADR 0062 §7 rejected computing suggestions on the ordinary page render, and the
reason has not changed: the disclosure exists to defer that cost, its closed
control carries no count precisely because a count would pay it, and hiding a
section requires knowing it is empty. So «Võimalikud seosed» keeps its deferred
disclosure and its honest-emptiness message. The create form has no such
problem — its region is empty until a request answers it — so there it hides.

### 6. Nothing about the record changes

No model, no migration, no schema. `INDEX_VERSION` stays `AUTH003.1` and
`ARCHIVE_INDEX_VERSION` stays `1`; both projections are read and neither is
rewritten, so this deploys with no rebuild. No new search index: the instrument
signal is a join on a table that already exists, and the pools are the ones
ADR 0062 bounded.

`app/matters/models.py` is deliberately untouched, so the three workflow
branches open beside this one integrate without meeting it.

### 7. Deliberate exclusions

- **No generative AI, no embeddings, no external service, no click history.**
  Unchanged from ADR 0062 §4 and restated because the brief asked for it.
- **No raw document-content similarity.** The engine reads the projection's
  `body_text` for subject *words* and has always done; comparing documents as
  documents is a different feature with a different cost.
- **No «sektor» signal.** There is no sector model. `app/taxonomy/models.py`
  records that a sector was deliberately not squeezed into `Tag`, and inventing
  one to satisfy a label would be manufacturing a taxonomy from a wish.
- **No structured Koda-position signal.** There is no structured
  support/oppose/partial field anywhere: `Matter.position_summary` and
  `rationale_summary` are prose. Matching on a stance the record does not hold
  would be inferring one.
- **No new dismissal mechanism.** `RelatedSuggestionDismissal` already exists
  and already covers Matters; the create surface has no Matter to hang a
  dismissal on, and durable create-time feedback is a v2 question.
- **No automatic linking, still.** Nothing here creates a relation, a tag, a
  policy area or a next action, and no suggestion implies an earlier position
  should be reused.

## Alternatives considered

- **A separate `Sarnased teemad` section on the Matter page beside «Võimalikud
  seosed».** Rejected: two controls on one page proposing related Matters by
  rules a reader cannot distinguish. The product owner chose extension.
- **Replacing «Võimalikud seosed» with the briefed section.** Rejected for this
  round: it reverses ADR 0062 §7's placement and drags the confirmed-links UI
  and six write routes into a read-only change.
- **Weighting `Õigusakt` like a tag.** Rejected. A tag is the department's own
  specific vocabulary applied deliberately; an instrument type is a
  seventeen-value classification most of the register shares a value of. At 2.0
  a shared type plus a ministry would have qualified, and «both are laws from
  the same ministry» is not a recommendation.
- **Matching `legal_instrument_other` as text.** Rejected: uncontrolled free
  text, one Matter's own, compared for equality.
- **Computing the indicators inside the pool query.** Rejected: three joins
  onto forty rows to decorate five, and the aggregate would have had to be
  scoped per reader anyway.
- **Posting the draft form to a suggestion endpoint.** Rejected: GET states that
  it writes nothing, and the parameters are short enough for a query string.
- **Creating a draft Matter to reuse `suggestions_for` unchanged.** Rejected
  outright: a row in the register nobody asked to create, needing a cleanup
  nobody would run.
- **Triggering on every form field.** Rejected: a round trip per keystroke on
  `Märkmed` costs several pool queries to produce an identical answer.

## Consequences

- One reason line more on some cards, one badge row more on others.
- `Uus teema` gains one region, which costs nothing until the form holds enough
  to ask, and one route.
- The create page gains a visual baseline change if the seeded world's form
  reaches the threshold; `uus-teema` and `uus-teema-viga` are inspected.
- `SubjectProfile.matter` is now optional, and the two channels that need a
  saved Matter say so through `saved_id` rather than by dereferencing a null.
- The quality target is ADR 0062's, unchanged: honest emptiness. The threshold
  is not lowered to fill either surface.

## Reversibility

High. The instrument signal is two weights and one block in `_matter_signals`;
the indicators are one dataclass, one batched read and one template block; the
create surface is one route, one template and one `<div>`. Nothing is stored, no
migration exists to reverse, and removing any of the three leaves ADR 0062's
engine exactly as it was.
