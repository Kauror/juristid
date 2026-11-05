# 0209 — Earlier stages appear under «Seotud materjalid»

Status: accepted (owner brief «Continuation relationships and related materials», 2026-11-05; release awaits owner review)
Date: 2026-11-05
Builds on [0062](0062-related-materials-and-human-confirmed-links.md) (related
materials), [0181](0181-one-internal-read-policy-for-all-business-content.md)
(who reads which Matter) and [0207](0207-a-teema-names-where-its-work-continues.md)
(continuations).

## Context

A Teema that continues earlier work said so in its header (0207), but
«Seotud materjalid» — where a lawyer looks for what else bears on a file — listed
only what somebody had linked by hand. A history of two or three stages, or two
branches converging on one Teema, had to be found by walking the header one page
at a time, and a predecessor somebody had also linked by hand would have shown
up twice if both were listed.

## Decision

1. **`Varasemad etapid`, a read-only group inside «Seotud materjalid».** It lists
   `historical_predecessors(matter, viewer).stages` (app/matters/continuations.py):
   every live Matter whose `superseded_by` chain reaches this one, through every
   branch, earliest first. Each row is «2025_140 — pealkiri», linking to that
   Teema, with «avatud» or «suletud» under it. There is no «+ Lisa» and no
   control on it: the continuation is never edited here. Rows stay flat,
   earliest first; a direct predecessor adds «vahetu eelmine etapp», a deeper
   stage «jätkus teemana <viide>», naming the stage it continued as
   (`EarlierStage.continues_as`, always a row of the same list) (owner, QA-12).
2. **A predecessor also related by hand is one row.** It stays under
   `Varasemad etapid`, says «suletud · seotud ka käsitsi», and keeps the manual
   relation's «×» («Eemalda käsitsi lisatud seos: …»), which posts to the
   existing `related_materials:unlink`. That removes the `MatterRelation` only;
   `superseded_by` is untouched and the row stays as a stage. Both rows remain in
   the database while both bases hold. `RelatedMaterials.relations` still holds
   every confirmed relation; `manual_rows` leaves out the ones a stage row
   carries.
3. **Count and emptiness.** The count beside «Seotud materjalid» is distinct
   visible rows: manual relations not on a stage row, background material and
   stages. A Teema with only history is not empty.
4. **Scope.** Each level of the walk is re-read through
   `Matter.objects.visible_to(viewer)`. A stage the reader may not read, or a
   deleted one, is not a row, not a count and not a hint, and the walk does not
   pass behind it. A cycle reads «Ajalugu jõuab tagasi juba näidatud teemani,
   kaugemale seda ei näidata.» and a cut walk «Ajalugu on pikem, kui siin
   näidatakse; näidatud on lähimad etapid.». Neither names anything.
5. **Every surface draws the same section**: the Matter page, the HTMX section
   fragment, the write routes that re-render it, the overview re-renders and the
   no-script section page all render `related_materials/partials/section.html`
   from `related_materials_for`.
6. **Cost.** One query per level of history (the walk), none per row. A Teema
   with no history pays one query; the Teema page ceiling moves from 64 to 65.
7. **The rail «Seotud» card** (amended 2026-11-05) drops its «Eelnev teema» rows
   wherever «Seotud materjalid» is on the same rail, so the predecessor is not
   named twice. Every view that draws the rail today carries the section — the
   Teema page and every `#teema-vaade` re-render through `_overview_context`,
   and the inline «Saatja» save, which swaps the rail alone, through
   `_field_surface_context` — so those rows remain only as a fallback for a
   context without it (QA-17). The row for a predecessor this reader cannot open
   stays («Varasem etapp, mida siit avada ei saa»), yes/no only. «Jätkub
   teemas» stays on an open Teema; on a closed Teema whose closed banner already
   names the successor it is not repeated (owner, QA-16), while a successor that
   cannot be opened keeps «Jätkub teemas, mida siit avada ei saa». The header
   line (0207 §2) is unchanged.
8. **The «+ Lisa» picker (2026-11-05, QA-14).** A readable earlier stage (any
   branch) or later stage (the forward path) found by the search is shown
   labelled «Juba varasem etapp» / «Juba hilisem etapp», with no «Seo
   teemaga». `related_materials:link` refuses such a target: no relation is
   written and `superseded_by` is untouched. A converging sibling is not a stage
   of this Teema and stays an ordinary candidate. Manual relations made earlier
   keep displaying per §2.
9. **Keyboard focus (2026-11-05, QA-13).** After a write inside the section the
   focus stays in it — on the same control, the row that took the acted-on
   row's place, or the section heading — and the result is announced in a
   polite live region. Buttons name their target in the accessible name
   («Lisa 2025_140 — pealkiri», QA-15).

No schema change and no data change.
