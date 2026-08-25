# 0032 — Uus teema: one screen, one chip control, and two Valdkonnad withdrawn

- **Status:** Accepted
- **Date:** 2026-08-25
- **Builds on** ADR 0009 (design-token foundation), ADR 0010 (Stage-1 interaction model and browser testing), ADR 0011 (NextAction modelling), ADR 0024 (real/test classification), ADR 0025 (multiple senders, singular addressee), ADR 0029 (reference data), ADR 0030 (the Teema workspace redesign and the twenty-three working Valdkonnad), ADR 0031 (what a working session on real data changed).
- **Amends** ADR 0030 §7 on one point: two of the twenty-three Valdkonnad it recorded are withdrawn from the offered vocabulary.

## Context

`Teema`, `Minu töö` and `Ülevaade` were rebuilt to the approved design over
2026-08-24. `Uus teema` was not, and it stayed the page the Stage-2E.1 round
left: nine full-width `fieldset`s stacked vertically, four of them holding a
row of checkboxes or radios, with the procedural half behind
`+ Täpsusta teema andmeid` and the next step behind `+ Järgmine tegevus`.
Nothing on it was subordinate to anything else, so it read as a survey rather
than as *file this*, and roughly two thousand pixels separated the title from
the submit.

The department designed a replacement and approved it (`design/mockups/Uus
teema.dc.html`, states 8a–8c, with the intent written up in
`design/UUS_TEEMA_PROMPT.md`). This ADR records what implementing it required,
what it deliberately refused, and the three places where the implementation
departs from the drawing on purpose.

## Decision

### 1. Density is the change. Nothing about the record moves.

Every field that was on the page is still on it, still posting the same name and
the same value, and still validated by the same form. Both disclosures are gone
and what they hid is on screen; the height came back from paired rows and from
the chip control, not from hiding anything. `tests/test_uus_teema_redesign.py`
asserts the canonical Matter a full POST produces rather than the markup that
produced it, because a legibility change that quietly stores something different
is a data bug wearing a UI change.

Two fields are genuinely new to the page, and both write to things that already
existed: `Matter.brief_summary` (`Millest teema räägib`) and
`MatterPersonalNote` (`Märkmed`, private to its author, on no timeline, in no
export, in no search index). Neither is required, and a blank note writes no
row.

### 2. One control, and its shape still promises what the model holds

`.chip` replaces `.choicecards .choicecard` and `.checkitems .checkitem` on this
page: the same radio and the same checkbox, with the native box moved out of
sight and the label carrying the state. Radios where the model holds one value
— Vastutaja, Hetkeseis, Menetlusliik, Adressaat — checkboxes where it holds
several, and never the reverse. A `<select>` was not reintroduced anywhere: for
a department of four, a dropdown is a click spent discovering the options.

Naming this control `.chip` collided with an existing rule of the same name, a
one-off badge on the Matter heading. That badge is now `.lockchip`, with the
values it always computed to. It was the earlier claim on a bare, generic name,
and the collision was invisible until it was not: every option on the create
form came out uppercase and heavy because the badge's rule was declared later in
the file and won.

### 3. Two Valdkonnad are withdrawn, and nothing is remapped

ADR 0030 recorded twenty-three working Valdkonnad. Hands-on use found two of
them doing damage rather than work, and the product owner withdrew both:

- **`Olulised tähtajad`** was never a subject area. It named a cross-cutting
  watch list — files whose timing matters — which is a workflow property. The
  product already holds that concept under a different model:
  `MatterImportantDate` is an operational date on one Matter and the *Olulised
  tähtajad* calendar is built from those rows. Two things sharing four words,
  one of them a taxonomy label, is how a tax deadline gets filed under a heading
  the tax report does not count.
- **`Muud teemad`** duplicated `Muu`. `Muu` on this form is not a `PolicyArea`
  at all: ticking it reveals a free-text box that writes
  `Matter.policy_area_other` and creates no taxonomy row. A label spelling the
  same idea gave two ways to answer *none of these*, one of which recorded
  nothing about the file.

The resulting offered vocabulary is twenty-one governed areas plus the free-text
`Muu` affordance — the twenty-two the approved design names.

**Withdrawn is `is_active`, never a delete and never a remap.** The rows stay,
the relations stay, statistics still count them, and the Teema header still
offers a retired area back under its "varasem valdkond" note so that correcting
one field on an old Matter cannot silently drop its filing. In particular
nothing rewrites `Muud teemad` to `Muu`: that is a guess about somebody else's
judgement, and the free-text box it would have to fill has nothing truthful to
put in it. `taxonomy/0004` moves one flag and refuses to run at all if somebody
has since renamed either row.

The change is made once, in `app.taxonomy.vocabulary`, which every surface that
offers a Valdkond already read. Two that did not — the reporting filters and the
Ülevaade *Valdkonniti* table — now do.

### 4. Hetkeseis explains itself, on the row

Which of `Kooskõlastusringil` and `Valitsuses` a file is in depends on an event
that has or has not happened. The department supplied a sentence per stage
saying which, and `workflow/0006` writes them onto `StageVocabulary.help_text`
— reference data on the row, not prose in a template, so a reword is one edit.

The affordance is a tooltip, not a modal and not a link. The chip itself is the
target: hovering it or tabbing on to it opens the bubble, moving away closes it,
Escape closes it, and the radio points at the bubble with `aria-describedby`, so
the text is part of the option's accessible description. A separate icon button
per chip was rejected — a radio group is one tab stop, and eleven buttons inside
it would make the keyboard path through this page longer than the mouse path.
The marker that says a chip explains itself is drawn in CSS rather than set as a
character, so it is in no accessible name and in no test's idea of what the chip
is called.

`app.workflow.selectors` is a new module rather than an addition to
`app.workflow.vocabulary`, which must stay importable without a database: the
offline register inspector runs where there is no PostgreSQL.

### 5. Creating a Matter requires the content-write role

`matter_create` was reachable — and worked — for a `READER`, who may read the
register and change nothing in it. It now makes the same check `matter_edit`,
the composer and the intake surface make, and answers 404 for the same reason
they do: a reader who may not write is not told which surfaces exist for those
who may. The button is hidden from them as well, because a page offering a
control it knows will fail is a page lying to somebody.

This is a defect fixed in passing, not a decision the redesign made.

## What this deliberately did not do

**Adressaat stays singular.** The approved design draws it as a multi-select
mirroring what ADR 0025 did for senders, on the argument that a matter can be
answered to a ministry and a committee at once — and offers the single-value
chip group as the version to ship if the migration is not wanted yet. That is
what shipped: the layout is the design's, the cardinality is the model's, and
`addressee_organisation` is untouched. ADR 0025 recorded the singular addressee
as a deliberate decision, and reversing it is a schema change across the edit
form, the header, the register, search and statistics — not a side effect of a
form redesign. It is open (see `docs/open-decisions.md`).

**`responsible` on the next step is kept and not rendered.** The step inherits
the Vastutaja chosen a few rows up; naming the same colleague twice on one form
is a question whose answer is already on the screen. Re-assignment happens on
the Teema page. The form field stays so that a POST naming somebody explicitly
still wins.

**No second creation service, no second uploader, no second NextAction path.**
`create_matter`, `create_document`/`add_evidence_version`, `set_next_action` and
`save_personal_note` are the same functions, called from the same view, inside
the same transaction. An uploaded creation file is `INCOMING_AUTHORITY`
evidence; nothing on this page can produce a `Submission`.

**No organisation is created from this form**, from either the sender control or
the new Adressaat chips.

## Three departures from the drawing, and why

1. **All twenty-two Valdkonnad are shown; there is no "Veel 15" affordance.**
   The mockup was drawn against a vocabulary of twenty-five. The withdrawal
   above leaves twenty-two, which wrap into three rows at 1440 and three at
   1024. A "Veel" affordance over twenty-two hides half the vocabulary behind a
   click, and deciding *which* half means ordering by usage — which this control
   deliberately stopped doing, because an order derived from records is a
   disclosure about those records (ADR 0030 §7.1).

2. **`Loo teema` renders inactive until there is a title.** The design prose
   specifies this (§7); artboard 8a does not draw it. It is `aria-disabled` and
   a flat fill rather than `disabled`, so the button still submits: somebody who
   presses it anyway gets the server's refusal beside the field rather than a
   control that does nothing and explains nothing.

3. **Dates keep the product's own rules, not the drawing's.** The artboards show
   `24.08.2026` and an empty `Arvamuse tähtaeg`. This application writes
   `D.M.YYYY` and starts every date box on today; both are decisions hands-on QA
   made and both are pinned by tests (Teema QA §5). A redesign is not the place
   to reverse them.

## Two things the product owner has to answer

1. **The supplied `muu` help text is word for word the supplied `ELi
   menetluses` text.** It ships as given rather than guessed at, because which
   of the two is wrong is not a migration's decision.
   `tests/test_uus_teema_redesign.py` pins the duplication so that resolving it
   is a deliberate edit.

2. **`rohkem pole tegevusi plaanis` was supplied with a description alongside
   the ten stage texts, and it is not a Hetkeseis.** `workflow/0004` reads it as
   the `MONITORING_STOPPED` disposition, because it says Koda stopped working on
   the file rather than where the external process stands. Adopting it as an
   eleventh chip would merge two different questions into one column. Its text
   is recorded here until the closure control is given one:

   > Kasuta 3 juhul: 1) Teema läheb edasi ja selle kohta tuleb Tööd eelnõudega
   > tabelisse uus teema. Näiteks VTK-le järgneb hiljem eelnõu või EK avalikule
   > konsultatsioonile järgneb direktiivi ettepanek. Siis märgi vana teema
   > hetkeseisuks "rohkem pole tegevusi plaanis" ning Järgmiseks märgi, et
   > jätkub uue teema all ja lisa teema nr. 2) Tuleb otsus, et teemaga ei minda
   > edasi (nt meie ideed ei viida ellu, VTK-le ei järgne eelnõud, eelnõud ei
   > saadeta valitsusele, valitsus ei saada eelnõud Riigikokku, Riigikogu ei
   > võta eelnõud vastu, EK avalikule konsultatsioonile ei järgne EK
   > ettepanekut, direktiivi/määruse ettepanekut ei võeta vastu jne). 3) Teema
   > läheb edasi, aga teema on meie jaoks ebaoluline ja me ei jälgi seda enam
   > edasi.

   The `idee` text as supplied carries an unbalanced parenthesis. It is
   transcribed as given; closing it would be a migration guessing where the
   sentence was meant to end.

## Consequences

- One migration per app and neither touches a schema: `taxonomy/0004` moves
  `is_active` on two rows, `workflow/0006` replaces ten help texts. Both fail
  closed on a row somebody has edited, and both reverse.
- The reference-data plan digest changes: `REFERENCE_POLICY_AREA_VERSION` is
  `3.0`, so a plan built under `2.0` can no longer be applied. That is the
  intended behaviour of the version, not a side effect to work around.
- `uus-teema.png` and `uus-teema-viga.png` are new visual baselines. They were
  regenerated on the CI image, read, and only then committed.
- The chip control is scoped to `Uus teema`. `Muuda teemat`, the Teema header's
  inline Valdkonnad editor, the composer and the rail keep `.checkitem`, where a
  visible native box beside a full-width label is the right density.
