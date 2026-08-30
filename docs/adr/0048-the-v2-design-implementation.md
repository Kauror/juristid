# ADR 0048 — Implementing the v2 design over the application that exists

- Status: proposed
- Date: 2026-08-29
- Stage: pre-QA (shared-gate development phase)
- Related: ADR 0039 (retiring *Minu tiim*, which this keeps retired), ADR 0046
  (the deadline windows on Ülevaade and Osakonna töö, which this does not
  change), ADR 0047 (Arvamused as a section of Teemad, whose IA this keeps),
  ADR 0036 (assignable department workers, which decides who the person
  switcher walks), ADR 0038 (child visibility in projections)
- Companion: `docs/design-v2-compatibility.md` (what existed before each change)
  and `app/core/development_status.py` (what the design left unresolved)

## Context

The design review delivered a full prototype of the product — twenty-six
screens, production markup, production stylesheet — with the instruction that it
is now the visual and UX authority. The prototype was drawn from `main` at
`3d3a5e6b`, and several statements in the accompanying documents describe the
application as it was some months ago rather than as it is.

The risk in that shape is specific and it is not a design risk. A handoff that
says «Jälgimine on kolm uut lehte» and «Statistika on uus» invites a second
tracking subsystem beside `app.intelligence` and a second statistics application
beside `app.reporting` — two places where one business fact would then live, and
two places to keep true. The same invitation exists in miniature everywhere the
prototype draws a control the current application already has.

## Decision

**The design decides what the product looks like and how it behaves. The
repository decides what the code is.** Concretely:

1. **No new domain model was created for anything that already has one.**
   `03-BACKEND` §6 asks whether `ImportantDeadline` / `EntryIntoForce` / `Win`
   exist and says to reuse them if so. They do —
   `MatterImportantDate`, `MatterEffectiveDate` and `MatterWorkVictory`, with
   richer semantics than the handoff describes: stored date precision,
   supersession, cancellation, and a confirmation step on a work victory. The
   three Jälgimine pages were re-shaped over those; nothing was duplicated.

2. **Statistika was redesigned, not rebuilt.** The overview's charts became
   tables over the *same* catalogued metrics, read for their value and their own
   drill-through. The undesigned sub-tabs were left exactly as they are, and
   `Andmekvaliteet` — which the new tab strip does not name — was kept rather
   than hidden.

3. **One read model serves both modes of Minu asjad.** `build_my_work(user,
   subject=…)` selects a person's work and authorizes as the reader:
   `responsible=subject` decides *whose*, `visible_to(user)` decides *what*. A
   department head reading a colleague's desk therefore sees it through their
   own entitlement and never through the colleague's.

4. **The bands were redefined once, in `work_items.py`.** Four instead of five:
   *Üle tähtaja*, *Sel nädalal*, *Järgmised 30 päeva*, *Hiljem*. Reviews that
   have come round are ordinary dated work and are merged into *Sel nädalal*
   with a neutral `N p`. The semantics that block existed to protect are
   unchanged and are asserted directly: a WAIT or a MONITOR is never overdue,
   never red and never worded «üle».

5. **`PersonalScratchpad` is the only schema change**, and its privacy is
   structural rather than checked. One row per person, a service whose signature
   takes the user and cannot take anybody else, an endpoint that reads
   `request.user`, and a manager's response in which the block is absent rather
   than hidden.

6. **Every address that resolved before still resolves.** `/minu-too/` and the
   three ungrouped Jälgimine paths are permanent redirects carrying their query
   strings; the whole Arvamused workspace is untouched. The handoff asks for the
   Arvamused routes to redirect into the Teemad section instead; that would
   discard their filters and their pager, so it is recorded as an open decision
   rather than made.

7. **A number is shown only where a list exists.** Where the prototype draws a
   figure this application cannot express as a population — deadlines with no
   owner, work victories this month — the figure is not printed and the gap is
   recorded. Two register parameters were added rather than faked:
   `?loodud_alates=` / `?loodud_kuni=`, which is what gives Saabunud's two
   creation counts a destination.

**Where the handoff contradicts itself, `01-EHITUSJUHIS` wins**, and the
contradiction is recorded rather than silently resolved: bare band counters and
no «SEIS» label, against a prototype that still draws both.

## Alternatives considered

**Build the prototype as specified and reconcile later.** Rejected: it would
have created a second tracking subsystem and a second statistics application,
and the reconciliation would have had to decide which of two live copies of a
business fact is true.

**Keep the old bands and restyle only.** Rejected: the band change is what the
design is *for*. A reviewed WAIT sitting in its own block headed «Ülevaatamiseks
küps», under a sentence explaining that waiting is not lateness, is the page
apologising for its own taxonomy.

**Add the missing metrics so every prototype figure could be drawn.** Rejected
for this round: a metric is a definition with a version and a population, and
inventing three of them to fill a strip is exactly the kind of product decision
the brief says to record rather than improvise.

## Consequences

- The reference `2026_47` returns to exactly one ordinary surface, the «Teema
  andmed» rail, under the label `Teemaviide`. The identifier-free rule is
  unchanged — a topic is *named* by its title in every heading, crumb and list —
  and the tests now assert both halves rather than one.
- The one-click «✓ tehtuks» and its `X` shortcut are gone from the UI. The
  route and the service behind them are untouched and now have no caller; that
  is recorded as a decision to make, not left as a discovery.
- `Muuda teemat` and `Uus teema` share one visual language and still have two
  form classes and two services, because creating a record and correcting one
  are different transactions.
- The register shows twelve rows by default and offers 30 / 50 / kõik. Anything
  that assumed twenty-five had to be updated, which is four tests and no
  behaviour.
- The visual-regression baselines for every redesigned screen were stale by
  construction, and refreshing them found two defects in the harness rather
  than in the design. Masks were being painted for elements inside a closed
  `details.pw-more`, in the page's own colour, over headings and rows on
  Ülevaade and the Teema pages; and the register's Arvamused timestamp moved
  the meta line it sits on, so a baseline taken from one run was red on the
  next. Masks are now built through `visible()`, that one value is held still
  before the capture, and a closed disclosure contributing a mask is a test of
  its own. Twenty-eight baselines — the ones that intentionally changed — were
  then taken from a Linux CI run whose screens were read one at a time; the
  seven that still matched were left alone. Visual regression passes.

## Reversibility

High for the UI, low for nothing. Every change is a template, a stylesheet
declaration or a selector; the one migration is additive and carries no
backfill. The redirects mean the previous addresses keep working either way.
