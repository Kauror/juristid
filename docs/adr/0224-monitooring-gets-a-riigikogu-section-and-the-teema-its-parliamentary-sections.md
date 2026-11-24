# 0224 — Monitooring gets a Riigikogu section, and the Teema its parliamentary sections

Date: 2026-11-24 · Status: proposed (coordinator's brief, RK-2; development authorisation only)

## Context

ADR 0219 made Monitooring the department head's tab for external developments. The
coordinator's principle: external developments belong in Monitooring, accepted facts on
the Teema, actual work in Minu asjad.

## Decision

1. **Monitooring → Riigikogu** (`/monitooring/riigikogu/`) is a fourth section, between
   «Valitsuse info» and «Registrite dokumendid», for the same audience (the head, or every
   business reader once opened). No new top-bar destination. Its tab counts the invitations
   nobody has decided.
2. **The page, in order.** (1) «Kojale saadetud kutsed»: undecided invitations, the
   doubtful and the changed first, then by date; each with its deadline, the source
   sentence, the committee, the Teema (only if the reader may see it) or the candidate, and
   the comparison with the Teema's own deadline; below, «Kutsele vastamine vajab
   kontrollimist» (ADR 0226 §4). (2) «Komisjonides eesolev»: the next 14 days' committee
   agenda items on followed bills («Meie teemad», default) or all non-routine items («Kõik
   komisjonid»); an item is linked to a bill by the API's own ``relatedDraft`` first, the
   «(1095 SE)» in its title second; early signals (a bill being initiated or its draft
   presented) are folded under «Varajased signaalid»; housekeeping items are never shown.
   Agenda items are external events: they create no action. (3) «Muudatusettepanekute
   tähtajad». (4) «Varasemad kutsed», folded. Last, folded and read-only, «Muud koja
   mainimised täiskogus» (ADR 0226 §2). Remembered folds use the existing
   `{% disclosure %}` registry.
3. **The amendment deadline is the members' deadline.** One nightly search
   (`amendmentsDeadlineStartDate`) stores every bill with an amendment deadline in the
   next 45 days, its deadline a structured `ExternalDeadline` (AMENDMENTS); a move
   supersedes. Every surface says it is the deadline of the Riigikogu's members, factions
   and lead committee, and that koda cannot table an amendment. Deadlines of followed
   bills, invited bills and candidates come first; the rest are folded. **The Teema's
   «Registritest» tab** gains «Riigikogu», under the register findings, for that Teema's
   own followed bills only: the bills with «Seo eelnõu» / «Lõpeta jälgimine»; «Kutsed ja
   tähtajad» (decided there with the same actions); folded «Komisjoni arutelud ja
   protokollid»; «Teiste seisukohad»; «Eelnõu redaktsioonid ja koja ettepanekute
   arvestamine» (ADR 0225). The tab stays the register watch's as well. `Menetluse kulg`
   shows only dates a person took onto the Teema (ADR 0223 §3).

## Consequences

- A lawyer sees what the Riigikogu asks of koda on the Teema itself, and the head sees it
  across Teemad, without a second record of either.
- Visual baselines for the new section are to be generated from CI's visual job.
