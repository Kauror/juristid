# 0030 — The Teema workspace: one page, two tabs, and one save per professional update

- **Status:** Accepted
- **Date:** 2026-08-24
- **Builds on** ADR 0009 (design-token foundation), ADR 0010 (Stage-1 interaction model and browser testing), ADR 0011 (NextAction and Submission modelling), ADR 0018 (structured Matter facts), ADR 0027 (`Kaasamine`), ADR 0029 (reference data).
- **Supersedes the UI decisions of** ADR 0010's three-tab Matter page. The interaction principles it established — server-rendered HTML, HTMX fragments, no SPA, browser tests as the verifier — are unchanged and this depends on them.

## Context

The Matter page had grown into a rendering of the schema.

A lawyer opening a file in the deployed product saw, in order: three tabs; a
header band of six labelled facts, four of which were usually em dashes; an
`AJALOOLINE MATERJAL` card; a `SAABUNUD MATERJALID` block; a `Järgmiseks` card
carrying a mode chip, a date, a date-meaning label, a responsible person, a note
explaining what WAIT means and two buttons; a composer offering "+
Organisatsioon", a type select and a "✓ Muudan Järgmiseks" checkbox as three
peer controls; then `OLULISED TÄHTAJAD`, `JÕUSTUMINE`, `TÖÖVÕIDUD` and
`KAASAMINE`, each a heading over a sentence reporting that nothing existed; then
the whole chronology, expanded, every event of it. The facts rail held a `Sulge
teema` panel. What Koda actually argued was behind a tab called `Seisukoht ja
kaasamine`.

On a Matter nobody had touched, roughly forty per cent of the page was reporting
absences. On a Matter with two hundred entries, the question a reader came with
— *what do I do next* — was above the fold and the answer to *what is this* was
not, because the formal title is frequently a poor description of the business
issue: "Käibemaksuseaduse muutmise seaduse eelnõu" says nothing about the four
hundred companies that would gain a quarterly reporting duty.

The department designed a replacement and approved it (`design/mockups/Teema
uus.dc.html`, states 2a–2d). This ADR records the decisions that implementing it
required, and — more importantly — the ones it deliberately refused to make.

## Decision

### 1. A Matter has exactly two tabs

`Teema` and `Dokumendid · N`. Everything that was a third tab moved into the
main view where it is read: the position into the reading flow, `Kaasamine` into
a collapsed accordion, sent opinions into a compact strip. Browsing forty files
is a genuinely different task from understanding an issue, and it is the only
one that justifies its own surface.

The formal Submission workflow — drafting, attaching the exact evidence that went
out, marking sent, withdrawing — keeps its page and loses its tab. It is reached
from a quiet link in the position block. Those acts carry recipients, a channel
and a reference; they are not the routine capture the composer exists for, and
they are not frequent enough to spend a tab on.

### 2. A section renders only when it holds something

The four permanently visible empty sections are gone. `Olulised tähtajad`,
`Jõustumine` and `Töövõidud` render when populated and are otherwise replaced by
one quiet row of add affordances; `Kaasamine` is a collapsed row whose summary
line says either what the latest engagement was or that there is none.
`SAABUNUD MATERJALID` is gone entirely — the arriving document has a date and an
author, so it belongs to the chronology and to `Dokumendid`.

### 3. One save is one professional update

`compose_update` became an orchestration. One submit may write an `Entry`, a
`DocumentVersion`, a `NextAction`, a `MatterImportantDate`, a
`MatterEngagement`, a canonical sent `Submission`, a `MatterWorkVictory` and a
closure — each through its own existing service, inside one transaction, with
closure last because it ends the open next action and refuses an already-closed
Matter.

Nothing about the domain was unified. Every invariant, every audit row and every
authorization check is the one that service already made. A unified surface is
not a unified rule set, and the composer view makes the same
`may_write_business_content` check the engagement and closure endpoints make,
once, before anything is parsed.

### 4. The description *is* the next step

The composer has one text box. Choosing TEEN, OOTAN or JÄLGIN turns the
description into the next action's text, verbatim — tags stripped, nothing else.
There is no NLP, no sentence splitting and no summarisation: what somebody wrote
is what the register says.

The separate next-step field is deleted rather than hidden. It was the duplicate
data entry the product exists to remove, and a second box asking the same
question in different words is how two records that describe one intention start
disagreeing.

### 5. `ChangeEvent.operation_id`, and grouping without merging

A save that writes six canonical facts produced six lines in a chronology meant
to read like a case file. The fix is not to write fewer facts.

`ChangeEvent` gained a nullable `operation_id`, set from a context variable bound
by the composer, so the rows a single action produced say so at the moment they
are written. The timeline renders them as one item carrying one sentence —
"Marko lisas märkuse ja määras järgmise sammu" — with the facts it produced
listed under it.

Every underlying event still exists, still says exactly what it said, and is
still queryable. Nothing is suppressed, nothing is merged, no existing row
changed, and a row written outside a composer save carries a null and stands
alone, which is what those rows have always meant.

A context variable rather than an `operation_id=` parameter on twenty service
signatures: the value is constant for the duration of a request handler, and the
day one of those twenty forgot to pass it on, the timeline would split an action
in half with nothing failing.

### 6. `Matter.brief_summary` — the one field the design needed

An optional plain-language summary. Deliberately not `position_summary` (what
Koda thinks), not `rationale_summary` (why) and not the first `Entry` (what
happened on a day) — none of those can be made to mean *what is this* without
being corrupted.

Never backfilled. It is exactly the thing an importer cannot invent, and a
generated summary would be indistinguishable from one somebody wrote.

Audited without its text: a working description that gets rewritten as the file
develops is not history to be copied into an audit table.

### 7. `Valdkonnad` is the twenty-three the department works to

`PolicyArea` version 1.0 was the nine public focus areas from koda.ee, on the
argument that it is the small stable axis a yearly report is cut along and that
anything narrower is a `Tag`. That argument does not survive contact with
filing: the nine are what Koda *campaigns on*, and they are not the words a
lawyer reaches for when asked which area a file belongs to.

Version 2.0 is the reviewed working vocabulary — twenty-three labels, in the
order the department gave them — and it is now the *user-facing* Valdkond
taxonomy. `Tag` is unaffected: Valdkond answers which area of law or policy,
Silt answers what specifically about it, and the two are never merged into one
list.

**No remapping, no deletion, no guessing.** Four of the nine — Energeetika,
Riigihanked, Äriõigus, Keskkond — appear in the new list under exactly the same
name and keep their key, their row, their primary key and every relation
pointing at them, including the seventy-one applied from the OneNote filing
structure. The other five carry names the new list does not contain, so they are
*deactivated* and nothing else. "Maksud" is not "Maksud ja toll"; "Haridus ja
ettevõtlikkus" is not "Haridus". Writing either equivalence down would rewrite a
decade of somebody else's filing on a coincidence of spelling.

`app.taxonomy.vocabulary.selectable_policy_areas` is the single governed source
every surface offers, in the reviewed order. That order replaces an ordering by
usage frequency: with twenty-three labels the department itself sequenced, a
stable order is learnable and a self-rearranging one is not.

`Olulised tähtajad` appears in the vocabulary as a subject label. It is not
`MatterImportantDate`. They share four words and nothing else.

### 8. `Matter.superseded_by` — `Järglane` as a relationship

`Disposition.SUPERSEDED` could always say *that* a file continued elsewhere;
nothing could say *where*, so the answer lived in a closure comment that no query
can follow. One nullable self-reference, `PROTECT`, written only by
`close_matter` when the person closing names a successor, cleared by
`reopen_matter`, and read in both directions by the `Seotud` block.

Never inferred. The register's imported `continues_under_reference` is free text
about a reference somebody typed, and resolving it to a row would manufacture a
relationship the source never asserted.

### 9. `MatterPersonalNote` — private, and privately queried

A per-person, per-Matter scratch pad. It is the only write in the product that
records no `ChangeEvent`, appears on no timeline, is not indexed, is not
evidence and is not exported.

Deliberately **not** a `VisibilityInheritingModel`: inheriting the Matter's
visibility would make it readable by whoever may read the Matter, which is
exactly wrong. It is scoped by author, and there is no product surface that
lists anybody else's.

One row per person per Matter, so two lawyers on the same file never overwrite
each other and the Matter's own columns keep meaning what they say.

### 10. Everything else reuses what exists

- **Fuzzy dates** — `NextAction.date_precision` and `app.workflow.dates` already
  express month and quarter honestly. The composer offers the same period
  control the Olulised tähtajad form uses, and both normalise through
  `bounds_for`, so a quarter entered in either place produces the same anchor.
- **The active deadline** — a selector, not a model. Nearest future, else
  nearest past, over `Matter.response_deadline` *and* active
  `MatterImportantDate` records; never the `NextAction` review date, which is
  when to look again and not a commitment.
- **SharePoint working references** — `Document` with `role=WORKING_DOCUMENT`
  and the `sharepoint_*` columns could always hold one. What was missing was a
  way for a person to create one, which is now a service and a form.
- **Engagement counts** — `MatterEngagement` stores no response count. The
  collapsed line therefore shows the type and the date, and no column was added
  to a five-field pointer model to decorate a summary string.
- **Töövõit at closure** — the same door the Matter page's own control already
  uses. This feature broadens nobody's authorization, and the department head's
  review of imported candidates is untouched.

## Alternatives considered

**Keep the three tabs and restyle.** Rejected: the tabs are what made a lawyer
navigate to find out whether anything had happened, and no amount of styling
answers "does this file have a position" without a click.

**Store `Valdkonnad` as a governed `Tag` subtype.** Rejected. It would need a
discriminator on `Tag` and would put Valdkond and Silt in one relation — which
is precisely the merge the taxonomy exists to prevent — for no gain over
activating and deactivating rows in the table that already holds the concept.

**Suppress the extra audit events instead of grouping them.** Rejected outright.
The events are the record. A chronology is a presentation of history and may
group it; it may not decide what history contains.

**Deterministic timestamp-window grouping instead of a correlation id.**
Considered as the fallback the design brief allows, and rejected for new writes:
"same actor, same Matter, ±2 s" groups two unrelated clicks a second apart and
fails to group a save whose evidence upload took three. It remains what legacy
rows get, which is nothing — a null operation means "this stands alone".

**Backfill `brief_summary` from the first `Entry`.** Rejected. An entry is what
happened on a day, and promoting one to a description of the Matter would put a
claim on every historical record that nobody made.

## Consequences

- Six additive migrations, none destructive: `Matter.brief_summary`,
  `MatterPersonalNote`, `Matter.superseded_by` + its self-reference constraint,
  `ChangeEvent.operation_id`, two new `ChangeEventType` values, and the
  `PolicyArea` vocabulary seed.
- Deployment writes no business data. The vocabulary migration creates nineteen
  reference rows and flips `is_active` on five; no Matter, relation, entry,
  submission or classification moves.
- Statistics cut along `Matter.policy_areas` now have a twenty-three-row axis
  where they had nine, and historical Matters keep whichever of the nine they
  were filed under. Any year-on-year comparison across the change has to say so.
- `matter_position` keeps its route and loses its tab. Every existing redirect
  into it still resolves.
- The visual baselines for every Matter surface are regenerated.

## Reversibility

High, and unusually so for a redesign of this size, because almost none of it is
storage.

The migrations are additive and each reverses cleanly:
`taxonomy/0003` deletes only the pristine unreferenced rows it created and
restores `is_active` on the five it retired; the three column additions drop
columns nothing else reads. Reverting the templates restores the previous page
without touching a row.

The one thing that does not reverse by dropping a column is what people write
into `brief_summary` and `MatterPersonalNote` — which is the ordinary sense in
which any new field is irreversible, and both are optional.
