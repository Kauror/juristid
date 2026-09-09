# ADR 0067 — `Uus teema` asks two questions about one catalogue, and creates real work

- Status: accepted
- Date: 2026-09-09
- Stage: pre-QA (shared-gate development phase)
- Amends: ADR 0063 (one organisation catalogue — extended, not changed), and
  the Uus teema redesign decision that put the sender catalogue permanently on
  the page (reversed)
- Related: ADR 0025 (senders are a set), ADR 0029 (organisation identity),
  ADR 0032 (what `Uus teema` creates)

## Context

Three things were reported about `Uus teema`, and they turn out to be one
observation: the form asks its two counterparty questions as if they were about
different things.

**Saatja and Adressaat did not look alike.** Adressaat is chips, then
«Vali nimekirjast (N)» holding a search and the rest of the catalogue, then a
box for a body that does not exist yet. Saatja was chips, then a permanently
visible search, then a permanently visible scrolling catalogue, then the box.
A person filing a Teema learned one interaction for the field on the left and a
different one for the field on the right.

The permanent catalogue was a deliberate decision and its reasoning was sound as
far as it went: a door reading «Vali nimekirjast (15)» is a door somebody has to
guess is worth opening, and the search that would have found the other forty was
behind it. What it did not account for is how rarely the door is the right
answer. The shortlist is *chosen* to hold the bodies this department works with
and is *filled* to eight rather than left short, so on the overwhelming majority
of visits the sender is already on screen — and what the permanent catalogue
bought was a search box and a scrolling list occupying the Saatja column every
single time.

**Answering the sender was not offered.** Replying to whoever wrote to you is
the ordinary case. «Euroopa Komisjon» ticked as Saatja left the person to find
that same body again, from the top, under Adressaat — possibly behind a
disclosure — with no acknowledgement anywhere on the page that the two questions
are about one catalogue of institutions.

**And a body being typed for the first time could only be typed once.** `Uus
saatja: Euroopa Näidiskomisjon` has no primary key until `Loo teema` runs, so
there was nothing to offer under Adressaat, so the person typed the same name
into two boxes and had to hope.

Separately, and on the same row: **Andmeklass**. One checkbox reading
«Testandmed», on the page a lawyer uses every day, whose only possible effect
there was to be ticked by mistake.

## Decision

**Saatja takes Adressaat's shape.** Chips first; a `<details class="chipdetails">`
reading «Vali nimekirjast (N)» holding the search and the rest of the catalogue;
`Uus saatja` outside it. The same component, the same wording, the same
interaction, on all three surfaces that capture a sender — `Uus teema`,
`Muuda teemat` and `Saabunud`. One control, not a fork.

What does **not** go back is the sentence that used to close the block —

    Kui saatjat siin ei ole, tuleb asutus enne lisada asutuste alla —
    teema vormilt uut asutust ei teki.

— and `Uus saatja` stays *outside* the disclosure. That is the half of the
previous round that was load-bearing: the answer to "the body I need is not on
this page" must not itself be behind a click, because the workflow it replaced
was «abandon this Teema, go to Asutused, come back» and nobody performed it.
They filed the Teema with no sender (ADR 0063).

**A chosen sender is the first addressee offered — and is never chosen.**
Ordering is a suggestion; a counterparty is a fact. The promotion reorders the
choices and touches no `checked` state, and an addressee somebody picked by hand
survives any subsequent change to Saatja. Display order may change under a
person; their answer may not.

Done in both places, deliberately. The server orders the bound form, which is
what a refused save re-renders and what a browser with scripting off gets; the
browser does the same thing live, because the interaction has to work before a
round trip. One rule, stated twice, with a test holding each.

**A sender being typed is offered as an addressee too.** A chip standing for a
name rather than a record, dashed to say "not saved yet", which fills the
existing `addressee_name` free-text path when chosen. Nothing is created before
`Loo teema`. On save, one typed sender and one typed addressee resolve against
one catalogue inside one transaction, so the same spelling becomes **one**
`Organisation` row used on both relations — never two.

**Ordinary `Uus teema` creates real work, and does not ask.** `Andmeklass` and
its checkbox are gone from the create page, and `is_test_data` is gone from the
form — removed rather than hidden, so a forged `is_test_data=on` has nothing to
bind to and creates a REAL Matter. `data_class` is now a constant on this form.

Nothing downstream changes. `Matter.data_class`, the enum, the existing TEST
records, the REAL/TEST reporting filters, the purge tooling, the synthetic
fixtures and the rail's after-the-fact «Märgi testandmeteks» all stay exactly as
they were. What is gone is a way to create TEST work *from the ordinary capture
path*, which is not where it was ever created deliberately.

The freed column goes to Saatja, which is the field on that row that wanted it:
eight chips, a disclosure and a text box read badly in a third of a row and
well in two thirds.

## Consequences

- `sender_tail_count` returns to all three sender-bearing forms, and the two
  browser assertions that pinned "the sender control has no disclosure" are
  updated to the new contract rather than deleted.
- The sender search moves inside the disclosure and loses
  `data-choicefilter-compact` with the move. Compact meant "an empty box shows
  nothing", which was right for a list nobody opened deliberately; inside a
  disclosure the person has just asked to see the catalogue, and answering that
  with an empty panel would read as a broken control.
- A disclosure holding a ticked choice now opens itself, on both counterparty
  fields. A refused save that came back with the answer hidden looked like a
  form that had discarded it.
- No schema migration. No data migration. No search or archive version change.
- `_promote_selected_senders` runs in `__init__`, before validation, so it meets
  raw request data and discards anything that is not a UUID before it reaches a
  queryset. A malformed POST gets the ordinary refused form rather than a 500.

## What was considered and refused

**Making Adressaat a multi-select**, mirroring what ADR 0025 did for senders.
That is a schema change and a meaning change and it is still open; the approved
design offers the single-value chip group as the version to ship until it is
decided. Promoting the sender does not require it and must not be a back door to
it.

**Auto-selecting the sender as the addressee.** It is right often enough to be
tempting and wrong often enough to be a real defect — an opinion sent to the
ministry that merely forwarded the letter. Offering costs a click; guessing
costs a wrong counterparty on the record, discovered late or never.

**Copying the promoted radio into the quick row** rather than moving it. Two
controls with one name post twice and leave the browser deciding which counts.
