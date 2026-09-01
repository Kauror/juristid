# ADR 0054 — The action kind is not a user-facing concept

**Status:** proposed, on a feature branch
**Date:** 2026-09-01
**Completes:** ADR 0052 (*The simplified Teema next-action workflow*)

## Context

ADR 0052 stopped the Teema composer asking a lawyer to classify their own work,
and stopped the `Järgmiseks` row printing the answer back. It said, in §6, that
`Minu töö`, the register and the reporting surfaces still read the stored kind —
and left them printing it.

That was half a decision. On `Minu asjad` the row read

    [JÄLGIN] vaata üle septembris kui enne pole infot

and

    [OOTAN] vaata üle septembris, ootan 2. lugemist

which is the same sentence twice: once as a category the reader has to decode,
and once as the words a lawyer actually wrote. The register table, the portfolio
block, a search result, the department overview's intervention list and the
`Statistika` workspace each carried a version of the same chip or the same
vocabulary. A person who has never been told what an `OOTAN` is — which is
everybody, now that no form asks for one — is being shown a classification with
nowhere to learn it.

Nothing about the domain was wrong. The kinds are exactly the distinctions the
work model needs, and it needs them where they already are: in the code.

## Decision

**The stored kind is never printed.** No product screen renders
`TEEN` / `OOTAN` / `JÄLGIN`, no chip stands in front of an action's text, and no
published statistic slices by the classification. The action's own sentence is
the whole of the action cell.

**Nothing replaces it.** No `tegevus`, no `samm`, no `jälgimine`, no `ootus`,
and no re-coloured chip carrying the same information in a quieter form. The
step already says what it is; a second label beside it was the defect.

**Date meaning stays, and is a different thing.** `TÄHTAEG`, `VAATAN ÜLE`,
`OODATAV AEG`, `OLULINE TÄHTAEG` and `ARVAMUSE TÄHTAEG` say what the *date*
means, which is the one thing a bare `27.08` cannot. They are not synonyms for
the action kind — a `DO` can carry a `Vaatan üle`, and the register's imported
rows routinely do — and they are untouched here.

**`ActionKind` is unchanged, and so is every row.** `DO`, `WAIT` and `MONITOR`
keep their values, their labels and their behaviour. Only `DO` + `DEADLINE` can
be overdue; a `WAIT` or `MONITOR` review date that has arrived is ripe for a
look and is still never described as late; an imported register instruction
keeps the kind the parser gave it. There is no migration, no backfill and no
conversion. `get_kind_display()` still returns *Teen*, *Ootan* and *Jälgin* —
nothing renders the result.

**`?tegevus=` keeps the two conditions and loses the three categories.** The
register filter offered a value per stored kind (`teen`, `ootan`, `jalgin`) and
two per-kind review values (`ootan-ulevaatus`, `jalgin-ulevaatus`). What survives
is what a reader can act on: `puudub`, `hilinenud` (*Tähtaeg möödas*) and a new
`ulevaatus` (*Ülevaatus käes*) covering both review kinds. The kind still decides
both conditions — inside the query, rather than in the URL.

**`Statistika` publishes one review number, not two, and no breakdown by kind.**
`NEXT_ACTION_BY_KIND` was the classification and nothing else; it is retired.
`WAIT_REVIEW_DUE` and `MONITOR_REVIEW_DUE` counted the same event and differed
only by the kind, so they become one `REVIEW_DUE` — *Ülevaatus käes* — whose
value is their sum and whose drill-through is `?tegevus=ulevaatus`.
`OVERDUE_DO_DEADLINE` keeps its key, its version and its population; only its
prose changes. Its `source_population_et` still names `DO` and `DEADLINE`,
deliberately: that field states the columns a reviewer would query, and the
stored values are exactly what did not change.

**The chip's CSS goes with its last renderer.** `.mode`, `.mode--do/wait/monitor`,
`.modechip*` and `.modeselect*` are removed. ADR 0052 kept `.modechip` and
`.modeselect` with no renderer, reasoning that a surface showing a historical
`WAIT` or `MONITOR` might want the shape language back. No surface may, so the
reasoning is void and a dead rule is how a removed component quietly returns.
`templates/matters/partials/work_row.html`, the pre-v2 work row that no view had
included since the `workrow2` rebuild, is deleted with them — it was the last
file able to render the chip.

## Relationship to the master specification

`docs/master-specification.md` §7.2 still describes `Minu töö` as grouped by
action semantics — a `Teen` section and an `Ootan / kontrollin` section. The
product left that grouping at ADR 0048, which redefined the bands by date in
`work_items.py`; this record does not depart from the specification any further
than that already-recorded departure, and does not amend it. What §7.2 asks the
page to answer — *what must I do, wait for or monitor now* — is unchanged and is
answered by the date bands.

The rule this record leans on is §18.8: only `DO` + `DEADLINE` may be described
as late, and a review date that has arrived may not. That rule lives in the code
and in the colouring of the row, and it does not require the kind to be printed.

## Alternatives considered

**Keep the chip only where a historical `WAIT` or `MONITOR` is shown.** This is
what ADR 0052 implied and it is the status quo this record replaces. It puts the
vocabulary in front of exactly the reader least able to interpret it: the rows
that carry a `WAIT` are the imported ones, whose kind was inferred by a parser
from a sentence the lawyer had already written in full.

**Replace the chip with a quieter mark — a dot, a border, an icon.** Rejected
for the same reason as the chip. Colour or shape alone is worse, not better: it
carries the same classification with no way to look it up, and the accessibility
rule that got the three shapes in the first place (status is never colour alone)
then applies to a distinction nobody needs.

**Relabel the statistics rather than merging them.** *Ootan — ülevaatus käes* and
*Jälgin — ülevaatus käes* cannot be renamed without the classification, because
the classification is the only thing that distinguishes them. Two figures whose
labels would have to be identical are one figure.

**Leave `Statistika` alone as an analytical surface.** Defensible — slicing by a
stored dimension is what a statistics page is for — but it leaves the one screen
where a reader would go to *learn* the vocabulary presenting it as current. The
rule chosen is the simple one: no normal product screen presents the
classification as a category the reader needs to understand.

## Consequences

- Work rows are shorter and the action text has the space the badge occupied.
  Visual baselines covering `minu-asjad`, the register table and the department
  overview change.
- `?tegevus=teen`, `?tegevus=ootan`, `?tegevus=jalgin`, `?tegevus=ootan-ulevaatus`
  and `?tegevus=jalgin-ulevaatus` are no longer valid values. A bookmark holding
  one resolves to an empty register, which is `filter_by_next_action`'s existing
  behaviour for an unknown condition. Nothing in the product produces those URLs
  any more.
- `WAIT_REVIEW_DUE` and `MONITOR_REVIEW_DUE` disappear from the metric catalogue
  and from `Definitsioonid`. Neither was ever placed on a card, and no exporter
  exists yet (ADR 0007), so no published series breaks.
- The behaviour that depends on the kind is unchanged and is now tested as such:
  `tests/test_action_kind_is_not_user_facing.py` pairs every "the label is gone"
  assertion with one that reads the stored kind back out of the database.
- `.workrow*` and `.workgroup*` in `static/css/app.css` are left in place. They
  were already dead before this branch — `work_row.html` had no caller either —
  and removing a legacy component block is a separate cleanup.

## Reversibility

High. Nothing was deleted from the domain: restoring any chip is a template line
reading `action.get_kind_display`, and restoring a filter value is one branch in
`_open_action_condition`. The statistics change is the least reversible part —
`REVIEW_DUE` would have to be split back into two keys — and even that is a
catalogue edit over an unchanged population.
