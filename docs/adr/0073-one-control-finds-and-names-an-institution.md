# ADR 0073 — On `Uus teema`, one control finds an institution and names a new one

- Status: accepted
- Date: 2026-09-11
- Stage: pre-QA (shared-gate development phase)
- Supersedes: ADR 0063's and ADR 0067's shared decision that finding an
  institution and naming one are **two visible controls on purpose**, and ADR
  0067's «Vali nimekirjast (N)» disclosure on both counterparty fields. What
  those records decided about *identity* — one catalogue, normalised-exact
  reuse, alias reuse, refusal of an ambiguous spelling, resolution inside the
  save's own transaction — is untouched and is restated below because it is the
  load-bearing half.
- Builds on: ADR 0069 (the Saatja is the Adressaat by default, and Adressaat
  folds away), ADR 0025 (senders are a set), ADR 0032 (`Uus teema` redesign)
- Scope: `/teemad/uus/` only. `Muuda teemat` and `Saabunud` keep
  `sender_control.html` unchanged.

## Context

Saatja on `Uus teema` offered three ways to answer one question, and they had to
be found in order:

1. a row of about eight quick chips;
2. «Vali nimekirjast (N)» — a `<details>` holding a search box and the rest of
   the catalogue;
3. `Uus saatja` — a text field, outside that disclosure, for a body the
   catalogue does not hold.

Adressaat offered the same three, nested one disclosure deeper: its own fold,
then a chip row, then a second «Vali nimekirjast» with a second search box, then
`Uus adressaat`.

Every one of those three exists for a reason this project argued for in writing,
and each argument is still correct about the thing it was about. The shortlist
is one click for the ordinary case. The disclosure gave the Saatja column back
to the chips that answer the question on almost every visit (ADR 0067). The
separate typed box is the answer to «the body I need is not on this page», which
may not be behind a click, because the workflow it replaced was «abandon this
Teema, go to Asutused, come back» and nobody performed it (ADR 0063).

**What none of those arguments weighed is that the person has to choose between
them before they can type a letter.** Three correct affordances are not a
correct control. Somebody who knows the ministry's name has to decide whether it
is likely to be a chip, whether it is worth opening a door labelled with a
number, or whether this is a body the register does not have — and they have to
decide that *before* they have looked, which is exactly backwards. The
distinction between «find» and «create» is real and belongs to the server; it
was being asked of the person at the wrong moment, in the form of three boxes.

The strongest existing argument against merging them is worth quoting, because
it is the one this record has to answer rather than dismiss:

> **Two controls, on purpose.** The search box posts nothing. If it did,
> somebody who typed «Kliima», watched the list narrow to `Kliimaministeerium`
> and ticked it would also have filed a new institution called «Kliima». One
> control filters what exists; the other says «this is a body you do not have».

That is right, and nothing here weakens it. What it conflates is *two fields*
with *two controls on the screen*. The distinction it protects is a distinction
between two **intentions**, and an intention can be expressed by an action
instead of by a second box.

## Decision

**One visible control per counterparty field: a search box, and a `+` attached
to it.**

```
SAATJA

[ Otsi või lisa asutus…                             + ]

[ Näidisministeerium ] [ Kliimaministeerium ] [ … ]
```

The same component renders Adressaat when its fold is opened, configured for one
value instead of many. There is no «Vali nimekirjast» and no separate
new-name box anywhere in the scripted UI of this page.

**Typing is a query; `+` is a proposal; the save is the only thing that
creates.** The three are separate states and stay separate:

- the box has **no `name`** and posts nothing, so a half-typed «Kliima» left
  behind after somebody chose `Kliimaministeerium` cannot become an institution;
- `+` moves what was typed into `sender_name` / `addressee_name` — the fields
  that have always carried a typed institution — and draws a dashed provisional
  chip for it. No request is made and no row is written;
- `Loo teema` resolves that spelling through
  `app.organisations.services.resolve_organisation_name` inside the save's own
  transaction: reuse an exact or alias match, create only a genuinely new body,
  refuse a spelling that already names two.

**`+` on a spelling the catalogue already holds selects that body.** Canonically
or through a recorded alias, decided in the browser as *feedback* and on the
server as *fact*. Somebody who presses `+` on a ministry that exists sees it
become a selected chip rather than a proposal, and the server would have reached
the same answer either way.

**Every institution is a real form control in the document, and only the
shortlist is on screen.** The search reveals the chip for a match rather than
describing a row: selecting an existing body is therefore a tick on a control
that was already there, which is what makes three separate things work with no
round trip — the search, a refused save coming back with its answer visible, and
the intake reader ticking a ministry that is not in the quick row.

**An answer is never hidden.** Not because the shortlist did not reach it, not
while somebody searches for the next sender, not after a refused save, and not
because a machine rather than a person chose it.

**Cardinality is unchanged.** Saatja is 0..N checkboxes; Adressaat is 0..1
radios. Whether a file may be answered to two bodies at once remains a decision
to be taken rather than a side effect of a form redesign (ADR 0025, ADR 0032).

**ADR 0069 is untouched.** Adressaat stays folded, its summary still names the
answer, the sender still fills it, and a person's own answer still outranks that
for ever. What changed is only what is behind the fold.

**Ranking is presentation.** The shortlist is `organisations_by_usage` /
`addressees_by_usage`, both scoped by `visible_to`; search results rank exact
canonical, exact alias, prefix, substring, alias substring, then by label. No
usage count is rendered and no restricted Matter can move a chip.

**No schema change.** `Organisation`, both relations, the typed-name fields and
`addressee_is_manual` all already existed; this round adds no migration.

## Alternatives considered

**A server-backed autocomplete endpoint.** Genuinely better as the catalogue
grows, and the shape this would take if it did. Rejected for now because the
production catalogue is small, because a synchronous in-document search makes
«typing filters immediately» true rather than nearly true, and — decisively —
because the intake reader and «Kasuta» both work by ticking the control that
carries an `Organisation`'s primary key. With an endpoint, every one of those
paths would have to learn to build a control that does not exist yet. The
rendering is already bounded where it matters: at most eight chips are visible,
the rest are `hidden` entries, and the search result list is capped at twenty.

**Keeping the typed box and only removing the disclosure.** Halfway. It leaves
two boxes on the page, which is the thing being reported.

**Making the search box itself post its contents.** The defect ADR 0063 named,
restated here so nobody re-invents it: it turns an abandoned query into an
institution.

**Enter as «add new».** Refused. Somebody typing «näidis» and arrowing onto
`Näidisministeerium` is choosing it; an Enter that fell through to add-new would
file a second body called «näidis». Enter takes the highlighted result and
nothing else — and never submits the form, which an unguarded search box inside
this page would do.

**Deciding exact/alias identity in JavaScript.** The browser tells the person
what it thinks; the server decides. Two definitions of what an institution *is*
is how a catalogue acquires duplicates.

## Consequences

- `Uus teema` renders `matters/partials/organisation_picker.html` twice.
  `sender_control.html` survives unchanged for `Muuda teemat` and `Saabunud`,
  whose interaction contract is *not* identical — those forms arrive with the
  answer already given — and which were not part of this review. Consistency is
  desirable; redesigning two unreviewed surfaces as a side effect is not.
- With scripting off, a `<noscript>` block carries the old pair: the rest of the
  catalogue, and a labelled box for a body that is not in it. It is inert markup
  in a scripted browser, so none of it reaches the ordinary UX. The hidden
  carrier is written **before** that block, because with scripting off both post
  under one name and Django reads the last value.
- Aliases reach the browser as `data-aliases`, already normalised by
  `OrganisationAlias.save`. One query for the whole catalogue; nothing on the
  page re-implements `normalize_for_matching`.
- The visual baselines for `Uus teema` move. No other surface's do.
- `Vali nimekirjast` remains the shape used elsewhere in the application. This
  record is not an argument against disclosures; it is an argument against
  making somebody pick between three of them to answer one question.

## Reversibility

High. The partial, one CSS block and one JavaScript module are additive and
narrowly named; `sender_control.html` is still in the tree and still rendering
the previous shape on two surfaces. Reverting is a template swap plus the CSS
and JS deletions, with no data to migrate in either direction.
