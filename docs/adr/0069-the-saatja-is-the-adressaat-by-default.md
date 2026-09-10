# ADR 0069 — On `Uus teema` the Saatja is the Adressaat, and Adressaat folds away

- Status: accepted
- Date: 2026-09-10
- Stage: pre-QA (shared-gate development phase)
- Supersedes: ADR 0067's decision *«A chosen sender is the first addressee
  offered — and is never chosen»*, and with it the refusal recorded there under
  *«Auto-selecting the sender as the addressee»*. Everything else ADR 0067
  decided stands.
- Builds on: ADR 0063 (one organisation catalogue), ADR 0032 (`Uus teema`
  redesign), ADR 0025 (senders are a set)

## Context

`Uus teema` asks two questions about one catalogue of institutions: who a file
came from, and who it will be answered to. ADR 0063 established that these are
two relations onto one `Organisation` table and are never merged, because the
register's own counterparty column changed meaning from `KELLELT` to `KELLELE`
in 2020 and merging them would silently invert the direction of a decade of
records. That is unchanged and is not what this record is about.

What this record is about is that the two questions have the *same answer*
almost every time. A ministry sends a draft; the department answers the
ministry. ADR 0067 acknowledged this as far as ordering — the chosen sender was
moved to the front of the addressee choices — and then stopped:

> **A chosen sender is the first addressee offered — and is never chosen.**
> Ordering is a suggestion; a counterparty is a fact.

and refused the obvious next step in as many words:

> **Auto-selecting the sender as the addressee.** It is right often enough to be
> tempting and wrong often enough to be a real defect — an opinion sent to the
> ministry that merely forwarded the letter. Offering costs a click; guessing
> costs a wrong counterparty on the record, discovered late or never.

The risk named there is real. The trade is not the one it describes.

**«Offering costs a click» understates it.** The click is not the cost; finding
the thing to click is. Adressaat is a chip group over the whole catalogue with
its own «Vali nimekirjast» disclosure, so a person who has just ticked
«Kliimaministeerium» as the sender scans a second control for the same word,
possibly opens a second door to reach it, and answers a question they have
already answered — on the one page a lawyer uses every day.

**«Discovered late or never» is the half that changed.** A guessed counterparty
is invisible only if the page never says what it guessed. This round folds
Adressaat behind a summary that names the answer, so the field states its own
value on every visit whether or not anybody opens it. A wrong default is then
one line of text away from being noticed, at the moment the record is being
made, by the person making it.

So the department's question stands as they asked it: the ordinary workflow is
*incoming document came from X → we will normally answer X*, and a person should
not have to say X twice.

## Decision

**The Saatja is the Adressaat by default, and the default is not a lock.**

1. **One unambiguous sender answers Adressaat.** Ticking a body as Saatja
   selects that same `Organisation` as `addressee_organisation` — the primary
   key, never the name copied into free text, because the row already exists.
2. **A sender being typed answers it too.** `Uus saatja: Eesti Näidisliit` has
   no primary key until `Loo teema` runs, so the default is carried as
   `addressee_name`. Both halves then resolve through
   `resolve_organisation_name` inside the save's own transaction, so the same
   spelling becomes **one** `Organisation` row used on both relations — never
   two. A typed spelling the catalogue already holds answers with the row rather
   than with the word.
3. **A manual answer wins, for ever.** Once somebody has answered Adressaat
   themselves, nothing derives it again — not a sender added, removed,
   re-sorted, searched for or typed, and not a refused save re-rendering the
   form. This is carried explicitly in a hidden `addressee_is_manual` field
   rather than inferred from which chip is currently first, because moving chips
   is something this feature legitimately does.
4. **What was derived is taken back honestly.** Remove the sender the default
   came from and the default goes with it. An answer a person gave is never
   cleared by a change of sender.
5. **Several senders produce no default.** A Matter that arrived from two bodies
   has no unambiguous body to answer. The browser has one thing the server does
   not — it watched which sender was chosen *first* — and keeps that seed, so
   adding a second sender does not replace an existing default. A POST is a set
   with no order in it, so the server's rule is exactly: one named sender
   defaults, two or more do not.
6. **Adressaat arrives folded.** The whole control sits behind the same
   `<details class="chipdetails">` component the long tail already used, closed
   on a fresh visit, its summary reading «Adressaat» or «Adressaat ·
   Kliimaministeerium». It opens for a click and for an error on Adressaat
   itself — and deliberately **not** for the default, because a page that
   unfolded a section because it had answered a question itself would be
   reacting to its own writing.

**The server is authoritative.** The rule lives in `MatterCreateForm`, and the
derived value is written into the *bound data* rather than only into
`cleaned_data`. That is the one detail that keeps the two readers honest: a POST
naming one sender and no addressee saves a Matter answered to that sender with
no JavaScript anywhere, and a refused save re-renders with the derived answer
ticked and summarised, so nobody is shown an empty Adressaat beside a sender and
then handed a Matter that was answered anyway. The browser mirrors the same rule
live because the interaction has to work before a round trip; it duplicates no
organisation resolution, which stays in
`app.organisations.services.resolve_organisation_name`.

**Scope.** `Uus teema` only. `Muuda teemat` and `Saabunud` are unchanged: the
edit form is somebody correcting a record that already has an addressee, and
deriving one there would be the form quietly rewriting a fact nobody touched.

## Consequences

- ADR 0067's `_promote_selected_senders` survives, renamed
  `_promote_named_senders`, and stops being decoration. The chips are what the
  collapsed disclosure summarises and what opening it shows first, so a default
  left in the long tail would be an answer nobody could find. The promotion is
  still a *move* rather than a copy, for the reason ADR 0067 gave: two controls
  with one name post twice and leave the browser deciding which counts.
- `tests/test_counterparty_promotion.py::test_promoting_a_sender_never_selects_it`
  asserted the superseded decision and is replaced by
  `test_the_promoted_sender_is_also_the_answer`.
  `tests/test_addressee_defaults_to_sender.py` is new and owns the rest.
- `addressee_is_manual` is a new **form** field. There is no schema migration,
  no data migration, and no search or archive version change — `Matter`,
  `Organisation` and `MatterSourceOrganisation` are exactly as ADR 0063 left
  them.
- One state becomes reachable that was not: choosing a sender and deliberately
  leaving Adressaat «Määramata». Both that and *nobody answered yet* post an
  empty `addressee_organisation`, and `addressee_is_manual` is what separates
  them. With scripting off the field is absent and the default applies, which is
  correct rather than a gap — a browser that could not offer the default also
  could not have been used to reject it.
- `bindOpenChosenDetails` gains one exception, `data-stay-closed`. Its rule
  assumes a ticked choice is somebody's answer, and on Adressaat it now is not.
- The `uus-teema` and `uus-teema-viga` visual baselines change: the Adressaat
  row becomes one pill.

## What was considered and refused

**Deriving the default in `clean()` alone.** It would satisfy the save and
nothing else. A refused form would come back showing an unanswered Adressaat
beside a chosen sender, and the Matter would then be filed answered — a page
disagreeing with the record it is about to create.

**Inferring "the person answered this" from the rendered state.** Whether a
value is present, which chip is first, and which radio is checked are all things
the default and the promotion legitimately change, so none of them can carry the
distinction. Hence an explicit field.

**Guessing among several senders** — the first alphabetically, or the first the
database returned. That makes a counterparty depend on a body's spelling, which
is precisely the class of silent, plausible, late-discovered error the refusal in
ADR 0067 was written about. Where the rule cannot be unambiguous it declines.

**Making Adressaat a multi-select**, mirroring what ADR 0025 did for senders.
Still open, still a schema change and a meaning change, and still not something
to arrive at as a side effect of a form decision (ADR 0032, ADR 0067).

**Folding Saatja away as well.** Saatja is the question that actually gets
answered on this page, and it is what now answers the other one. It stays where
it is.
