# 0063 — One Organisation catalogue behind Saatja and Adressaat, and a sender may be named where it is used

**Status:** accepted
**Date:** 2026-09-08
**Extended by** ADR 0069 — the two relations stay distinct exactly as decided
here, and on `Uus teema` the answer to one now fills in the answer to the other
by default. Nothing in this record is withdrawn.
**Superseded in part by** ADR 0073 (2026-09-11), and only on `/teemad/uus/`:
the reasoning below under *«Two controls, on purpose»* is right about the
*distinction* and wrong about the *shape*. Finding an institution and naming
one are still two form fields with two different meanings, and the search box
still posts nothing — but they are one visible control there now, a box and a
`+` attached to it, because three affordances for one question made the person
choose between them before they had looked. Everything this record decides
about identity — one catalogue, normalised-exact reuse, alias reuse, refusal of
an ambiguous spelling, resolution inside the save's own transaction — is
untouched and is restated in 0073. `Muuda teemat` and `Saabunud` still render
exactly the shape described here.
**Amends** ADR 0025 (multiple Matter senders), §"The control matches the model".
**Builds on** ADR 0029 (reference data foundation), ADR 0032 (Uus teema redesign).

## Context

The department asked for one thing, and said it plainly:

> Saatja and Adressaat should not feel like two separate databases. There should
> be one place/catalogue containing organisations.

Architecturally there already was one. `app.organisations.models.Organisation`
is the only institution table, and both `Matter.source_organisations` and
`Matter.addressee_organisation` point at it. Nothing needed migrating.

What made it *feel* like two was that the two fields had opposite contracts.
Adressaat accepted a typed name — reuse an exact or alias match, otherwise
create, refuse an ambiguous spelling, all inside the save's own transaction.
Saatja refused, and every sender surface carried the sentence saying so:

> Kui saatjat siin ei ole, tuleb asutus enne lisada asutuste alla — teema
> vormilt uut asutust ei teki.

ADR 0025 wrote that rule down as deliberate: "creating a Matter is never a way
to create an institution", resting on master specification 14.7 — adding an
institution is an act on reference data, not a side effect of filing.

The reasoning was sound and the outcome was not. The workflow it prescribed —
abandon the half-filled Teema, navigate to Asutused, create the body, come back,
find your place, find the body again — is one nobody performed. What people did
instead was file the Teema with no sender at all, so the register lost the fact
rather than gaining a considered one. The rule protected the catalogue from a
duplicate by costing the register a fact, which is the wrong trade: a duplicate
institution is visible and mergeable, and a missing sender is neither.

Two further problems sat on the same control.

**The shortlist could collapse.** `organisations_by_usage` ranked bodies by
sender usage on visible Matters and fell back to the alphabet only when there
was *no* usage at all. A department whose new-system records happened to name
one sender got a quick-choice row holding exactly one chip — the nearly-empty
case, which is the case a young dataset is actually in, was the one case not
handled.

**The search was behind a door.** The box that would have found the other forty
bodies lived inside a closed `<details>` labelled «Vali nimekirjast (15)», and
the sentence saying creation was impossible was inside it too — so the answer to
"the body I need is not here" was only visible to somebody who had already
opened the thing that did not contain it.

## Decision

**One catalogue, two questions, one way of naming a body.**

`Organisation` remains the single catalogue. `Matter.source_organisations`
(plural, "who sent this") and `Matter.addressee_organisation` (singular, "who
this is answered to") remain semantically distinct relations and are never
merged — the register's own history is the argument, since the counterparty
column changed meaning from `KELLELT` to `KELLELE` in 2020 and merging the two
would silently invert the direction of a decade of records.

What changes is only how a body gets *named*:

1. **Saatja accepts a typed name**, through `Uus saatja`, on `Uus teema`,
   `Muuda teemat` and `Saabunud`. Resolution is
   `app.organisations.services.resolve_organisation_name` — the same function
   the addressee field and the closing composer's recipients already use, so
   normalised-exact reuse, creation of a genuinely new body, and refusal of a
   spelling that already names two are one definition rather than three.
2. **The typed sender is unioned, not preferred.** This is the one place the
   sender and addressee contracts legitimately differ, and it follows from the
   cardinality rather than from taste. A typed addressee must *win* over the
   chosen chip, because on `Muuda teemat` the radio group always carries the
   addressee the Matter already has and nothing could otherwise be replaced by
   typing. A sender does not need replacing: «Euroopa Komisjon» ticked and
   «Eesti Näidisliit» typed is a Matter that arrived from both. A body reached
   twice — ticked and then typed — is one sender, which is also what
   `MatterSourceOrganisation` enforces.
3. **Creation is inside the save's transaction.** A refused save leaves no
   institution behind, on every surface. `register_incoming` resolves the typed
   sender inside its own atomic block for exactly this reason.
4. **The shortlist is filled, not merely ranked.** `organisations_by_usage`
   layers three sources in descending order of what they say about this reader's
   work: sender usage on visible Matters, then addressee usage on visible
   Matters, then the catalogue alphabetically. It targets eight. Both usage
   passes are scoped by `Matter.objects.visible_to(viewer)`, so a restricted
   Matter cannot move a chip or disclose a body through the order of a row.
5. **The disclosure is gone.** The shortlist, the search over the whole
   catalogue, and `Uus saatja` are all on the page at rest. The catalogue list
   is height-capped and scrollable rather than hidden, so it stays a row rather
   than becoming a wall as the catalogue grows.

**The search box still posts nothing.** It filters choices already rendered and
has no `name`. `Uus saatja` is a separate control, and that separation is load
bearing: somebody who types «Kliima», watches the list narrow to
`Kliimaministeerium` and ticks it must not also file a new institution called
«Kliima».

## Consequences

Master specification 14.7 is not repealed. Naming an institution is still a
deliberate act — it now takes typing into a box labelled `Uus saatja` rather
than a journey to another page. What is repealed is the claim that the
deliberate act has to happen somewhere else.

ADR 0025's sentence "creating a Matter is never a way to create an institution"
no longer holds and is amended there rather than quietly contradicted. Its other
decisions — the plural relation, the through model, the union of the two sender
controls, the header band's clipping — are untouched.

No schema change. No migration. `Organisation`, `MatterSourceOrganisation` and
`Matter.addressee_organisation` are exactly as ADR 0025 left them.

The risk this accepts is near-duplicate institutions: two spellings of one body
that are not exact matches and not recorded aliases become two rows. That is the
same risk the addressee side has carried since typed addressees shipped, and it
is deliberate — `resolve_organisation_name` never merges on similarity, because
`Keskkonnaministeerium` and `Kliimaministeerium` score highly against each other
and are different institutions. A duplicate is recoverable by a person with the
evidence in front of them; a wrong merge takes a decade of filing with it.
