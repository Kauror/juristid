# 0065 — Adding a structured fact happens on the Matter

**Status:** accepted
**Date:** 2026-09-08
**Supersedes** ADR 0018's alternative *«HTMX fragment swapping for the capture
forms»*, for the two add flows named below and for nothing else.

## Context

`Jõustumine` and `Töövõit` are recorded from the Teema page. Both controls —
`+ Jõustumine` and `+ Lisa jõustumine`, `+ Töövõit` and `+ Lisa töövõit` — took
the reader to a small page of their own under `app.intelligence`, which posted
and redirected back to the section anchor they had come from.

ADR 0018 chose that deliberately, and the reasoning was sound as far as it went:

> The Matter overview is rendered by `app.matters.views` from its own context
> builders, and swapping part of it from another app would couple the two.

What the decision did not account for is how small these records are. A
commencement is a kind, a date and a sentence. A work victory is a sentence, a
period and sometimes a link. The record takes about fifteen seconds to write and
the trip to write it — leave the file, look at a page with none of the file's
context on it, save, come back, find where you were — is most of the
interaction. Worse on a refusal: a commencement `üldises korras` that carries a
date is refused, and the refusal arrived on the separate page, so the person had
to read it there and leave again.

The product owner's own words for it were that these «open in a new window».
They do not literally open a window — the browser navigates in the same tab —
but that is a description of the experience rather than of the markup, and the
experience is the thing being reported.

`Kaasamine` already had the shape this wants. It is one section on the Matter
with its composer inside it, and it was not built by coupling anything to
anything: the section renders itself.

## Decision

**`+ Jõustumine` and `+ Töövõit` open a form inline on the Matter, under the
area the record lands in.** With records, that is under the list in the section;
with none, it is under the row of add chips at the bottom, which is where those
controls are.

**One route, one form, one service, two renderings.** `intelligence:add_effective_date`
and `intelligence:add_work_victory` are unchanged addresses behind unchanged
`@login_required` + `@business_write_required`. What the view now asks is
whether the request carries `HX-Request`:

- an ordinary request gets the standalone page and the redirect it always got —
  a bookmark, a deep link and a browser with scripting off are unaffected;
- an HTMX request gets `intelligence/partials/matter_facts.html`: this app's
  own fact block, built from this app's own selector.

The fields are one file, `intelligence/partials/fact_fields.html`, rendered by
both surfaces. There is no second form, no second validation and no second
write path; `EffectiveDateForm`, `WorkVictoryForm`, `services.add_effective_date`
and `services.add_confirmed_work_victory` are exactly the ones that were there.

**The accordion is the render, not a state machine.** The block is rendered with
at most one open form, so asking for `Töövõit` *is* closing `Jõustumine`.
Nothing is stored to remember which was open, opening or closing writes nothing,
and `Sulge` is a GET for the block with no form in it.

**A refusal comes back in place.** The inline branch answers 400 with the bound
form in the block — the values still in the boxes, the errors beside the fields
that caused them. The application's htmx configuration already swaps 400 and
422 for exactly this reason (`static/js/app.js`).

**The coupling ADR 0018 refused is still refused.** Nothing in
`app.intelligence` reads `app.matters.views`' context builders. `_facts_fragment`
calls `selectors.matter_intelligence` and the two authorization helpers this app
already used, and renders the same partial the Matter page includes. What
changed is narrower than the alternative ADR 0018 rejected: **a fact section may
re-render itself**, and the Matter view keeps rendering it by including it.

**The add flow only.** `Muuda`, `Tühista`, `Kinnita töövõiduks` and
`Ei realiseerunud` still open their own page. They are decisions about a record
that already exists, they carry a confirmation sentence naming that record, and
inlining them is a separate change nobody has asked for yet.

**`Oluline tähtaeg` is deliberately not part of this.** The composer already
offers it beside the note it belongs to, which is where somebody learns about a
milestone in the first place (Teema redesign §3, §13).

## Alternatives considered

**A modal, a drawer or a floating panel.** Refused. The form belongs in the
reading order of the page, under the thing it writes to. A layer over the Matter
is a second surface with its own focus trap, its own escape key and its own
scroll — for two fields.

**Server-rendering both forms closed inside `<details>` on every Matter.**
Tempting, because neither form costs a query to build. Refused for two reasons:
the exclusive-accordion behaviour would then be either the HTML `name` attribute
(too new to rely on across the browsers the department runs) or new JavaScript,
and a refused save would have to be re-rendered by the Matter view — which would
mean `app.matters.views` constructing `app.intelligence`'s forms. That *is* the
coupling ADR 0018 refused, and in the more expensive direction.

**A Matter-local POST route that writes the record.** Refused outright. A second
address that writes these models is a second thing to authorize, and the write
gate is the thing that must have exactly one implementation
(ADR 0037, master specification 12.4).

**A form `prefix` for the inline form**, as `app.submissions.forms` uses.
Refused in favour of `auto_id` alone. The collision to avoid is in HTML ids —
the Matter page's composer also has a field called `kind`, and `<label for>`
binds to the first element with a given id — and `auto_id` fixes exactly that
while leaving the field *names* identical, so the inline form and the standalone
page post the same request to the same view.

## Consequences

- Recording a commencement or a work victory no longer leaves the Teema page,
  and a refused one no longer strands the person on a form they must leave.
- `app.intelligence` gains one template wrapper (`matter_facts.html`), one form
  partial (`inline_form.html`) and one shared field block (`fact_fields.html`);
  `fact_form.html` loses the fields it used to declare and includes the shared
  block instead, so the two surfaces cannot drift into two forms.
- `.teemamain > .factsection` becomes two selectors, because the sections now
  sit inside the swap target. Named rather than loosened to a descendant
  selector.
- Rendering an ordinary Matter costs no additional query: with both forms
  closed nothing new is built, and the block is the same include it always was.
- The Matter's visual baselines move where the empty add row and the two open
  forms are captured, and nowhere else.

## Reversibility

High. The standalone pages are still there, still routed, still tested, and
still what a non-HTMX request gets; reverting is removing the `hx-*` attributes
from four controls and the inline branch from two views. Nothing about what a
`MatterEffectiveDate` or a `MatterWorkVictory` *is* was touched, so there is no
data to migrate back.
