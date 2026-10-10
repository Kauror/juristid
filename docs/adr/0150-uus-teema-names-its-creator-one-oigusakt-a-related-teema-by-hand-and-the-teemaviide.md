# ADR 0150 — `Uus teema` names its creator, takes one `Õigusakt`, links a Teema found by hand, and the Teemaviide is shown again

- **Status:** accepted
- **Date:** 2026-10-09
- **Context:** the product owner's brief of 2026-10-09 («Lawyer UX, legal
  instruments and workflow corrections»), R1–R4 and F5.
- **Amends:** 0070 §2 (one instrument, see its amendment), 0130 §7–§8 (the edit
  page draws the guidance, see its amendment), 0048's consequence on
  `Teemaviide`; extends 0062/0087 (a note on 0087).

Four owner decisions about creating and classifying a Teema, made in one round
because they meet on the same two forms. **No migration, no new model, no data
change.** Every stored owner, instrument set, relation and reference stays
exactly as it is.

## Decisions

### 1. The person filing a Teema is its Vastutaja unless they choose otherwise (R1)

`MatterCreateForm` opens with the signed-in person chosen as `Vastutaja`
(`default_owner_to_creator`).

- **Server-side, on the unbound form only.** The chip is rendered chosen, so it
  is visible the moment the page opens and needs no script. A bound form — a
  refused save — shows what was posted and nothing else: a colleague chosen
  before a refusal is still chosen after it, and nothing ever replaces a
  person's choice.
- **Only someone this form may name.** The default applies when the creator is
  in the offered population: a department worker (`assignable_users`), narrowed
  to oneself without `work.assign` (`restrict_owner_to_self`, ADR 0145 §3). An
  administrator who is not a department worker is not made owner.
- **Reassignment rights are unchanged.** With `work.assign` any department
  worker may be chosen instead; without it the only owner on offer is oneself
  and a crafted colleague is refused as an invalid choice.
- **Consequence.** The create page has no «Määramata» owner chip (it never had
  one, `MatterCreateForm.owner`), so with the default a Teema filed through the
  page has an owner. An unowned Teema is still what a request that names nobody
  creates, and «Vastutajata» on Ülevaade still lists imported ones.

### 2. One `Õigusakt` per Teema; history keeps its pairs (R2)

Recorded in full as ADR 0070's amendment of this date: radio chips with
«Määramata», two values refused by the form and by the two services a person
reaches, the relation still many-to-many, a historical pair kept by «Jäta
alles» on `Muuda teemat` until somebody deliberately chooses one, `Muu` and its
text unchanged.

### 3. A related Teema can be found and chosen by hand while filing (R3)

`Uus teema` gains `Seo olemasoleva teemaga` under `Sarnased teemad`.

- **A search, not a suggestion.** `GET /teemad/uus/seotud/otsi/?q=`
  (`related_materials:draft_picker`) answers with the header search's own
  ranking — title, `Teemaviide` (`2026_301`, `2026-301`, `2026 301`) or keyword
  (tags, organisations and areas, `alias_text`) — through the reader's scope
  before any ranking (`search_matters` → `visible_documents`). No threshold:
  finding the file the engine did not propose is the point, and it works with
  an empty form and with no suggestions at all. Five results; synthetic TEST
  work is never offered for a REAL Teema; Teemad already chosen are left out;
  closed ones are offered and say so. Writers only
  (`business_write_required`), like `picker`.
- **Nothing is linked until the Teema exists.** «Vali» adds a row with a hidden
  `seo_teemaga_valitud` and a × to the form's own list (`bindRelatedPicker`).
  `matter_create` links the union of these and the ticked suggestions
  (`seo_teemaga`) through `_link_ticked_similar_matters` →
  `link_related_matters`, the one canonical `MatterRelation` service, inside the
  transaction that creates the Teema: a link that cannot be made refuses the
  whole save, and a refused save takes no link with it. Duplicates collapse to
  one relation; the new Teema is never its own target; a target the creator may
  not open, or of the other data class, refuses the save
  (`SIMILAR_LINK_NOT_FOUND`).
- **Its own field name.** The suggestion cards post `seo_teemaga` back and forth
  on every re-read, so sharing the name would let a card resurrect a row
  somebody removed from the hand-picked list.
- **A refused save keeps both.** The hand-picked rows come back as rows
  (`related_chosen`) and the ticked suggestions as values the cards' re-read
  posts back (`similar_ticked`); both are re-resolved through what the reader
  may open, and a ticked card that the new answer no longer shows — including
  an answer with no cards — is still carried as a hidden value.
- **Scripted.** The block is rendered `hidden` and revealed by the script: a
  search box inside the create form would otherwise submit the whole Teema on
  Enter. With scripting off a Teema is linked after it exists, through
  `Seotud materjalid → + Lisa`.

### 4. The Teemaviide is shown again — once, in the header, copyable (R4)

The Teema page's header meta line ends with `Teemaviide 2026_301` and a
«Kopeeri» button (`bindCopyLink`, which now copies an element's text as well as
a field's value).

- **Discreet and secondary.** Last on the meta line, in the meta voice, tabular
  figures. The title is still the `<h1>`, the crumb and the tab title; the
  reference is in none of them.
- **Only a reference the record has.** `display_reference` is read; nothing is
  allocated, regenerated or invented. A historical Teema without one shows no
  `Teemaviide` at all.
- **Nowhere else.** Not on the rail (the owner's compact round of 2026-10-07
  stands for the rail), not in the register, dashboards, Minu asjad, Osakond or
  any timeline — those still name a Teema by its title.
- `tests/test_identifier_free_ui.py` now asserts the new contract: the
  reference exactly once, under its label, beside the copy button; never in the
  heading, crumb, tab title or rail; nothing reallocated by rendering it.

### 5. `Muuda teemat` draws the `Õigusakt -> Hetkeseis` guidance too (F5)

Recorded in full as ADR 0130's amendment of this date («Muuda teemat»).

## Alternatives considered

- **Default the owner in JavaScript.** Rejected: the brief asks for a server
  default, and a script default is invisible until it runs and absent without
  it.
- **Replace the relation with a single foreign key.** Rejected: it would force a
  destructive migration choosing one of every historical pair.
- **Trim a forged second instrument to the first.** Rejected: which one was
  meant is not the server's guess; refusing is honest.
- **Share `seo_teemaga` between cards and hand-picked rows.** Rejected for the
  resurrection defect in §3.
- **Put `Teemaviide` back on the rail.** Rejected: the rail decision of
  2026-10-07 was about a compact rail, and the header is where a reference is
  looked for beside the title.

## Consequences

- The visual baselines of `Uus teema`, `Muuda teemat` and every Teema page that
  shows the header change by design; they are adopted from CI's own candidates
  with the owner's approval only.
- Browser tests that ticked two instruments now choose one through the page and
  plant a historical pair through `manage.py shell` where the scenario is about
  reading one (`plant_historical_instruments`).

## Reversibility

Entirely, by code: no migration and no data change. Restoring checkboxes would
not need a data repair, because every stored pair is still there.
