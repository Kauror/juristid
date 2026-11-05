# 0207 — A Teema names where its work continues

Status: accepted (owner brief «Coordinated corrective round», 2026-11-05; release awaits owner review)
Date: 2026-11-05
Builds on [0148](0148-the-2026-excel-pilot-imports-a-reviewed-sample-as-native-work.md) §5
(the «Jätkub mujal» work status), [0181](0181-one-internal-read-policy-for-all-business-content.md) (who reads
which Matter) and [0102](0102-a-mistaken-record-comes-off-the-active-file.md) (removed records).

## Context

A Teema closed because its work continues elsewhere showed the badge «Jätkub
mujal», a banner reading «Asendatud järgnenud teemaga», and a rail row with the
successor's truncated title. The badge said *that* the work continued. Nothing
near it said *where*, and nothing showed a chain of three or more stages. The
register rebuild imported Lovable's structured stage links into
`Matter.superseded_by`, so these chains are now common.

## Decision

1. **One edge, one walk.** `Matter.superseded_by` is the only continuation
   relationship. `app/matters/continuations.py` walks it in both directions,
   bounded (`MAX_HOPS` = 12) and with cycle detection. Every step is re-read
   through `Matter.objects.visible_to(viewer)`. Nothing is inferred from titles,
   shared OneNote pages, dates or the sentence «Jätkub teema … all».
2. **Header line, under the title** (`<nav aria-label="Teema jätkumine">`):
   «Jätkub teemas 2025_196» and «Eelnev teema: 2025_140», with the reference
   linking to that Teema. Several predecessors are a list: «Eelnevad teemad:
   …». With three or more readable stages it prints «Teema etapid
   2025_10 → 2026_55 → 2026_190» instead, earliest first. The current stage is
   marked (`aria-current`) and not linked. When the endpoint is known, the line
   adds «viimane etapp 2026_190 on avatud|suletud». Nothing redirects, and a
   historical stage keeps its own page and history.
3. **Closed banner**: «Teema on suletud. Jätkub teemas 2025_196 (pealkiri)»
   replaces the generic reason when the successor is readable.
4. **Rail «Seotud»**: the direct neighbours as «2025_196 · pealkiri». Its key
   «Jätkub teemana» becomes «Jätkub teemas», the header's words. The header
   gives references, the rail gives titles, and the banner names only the
   direct successor.
5. **What a reader cannot open (amended 2026-11-05, owner, QA-04/QA-05)** — a
   deleted Teema, or one outside the reader's scope — is never named, counted or
   titled, and never described as a permissions matter. Navigation surfaces
   (header, closed banner, rail, `get_matter`) say only *that* the history
   continues past it, as a yes/no: forward «Jätkub teemas, mida siit avada ei
   saa», backward «Varasem etapp, mida siit avada ei saa». `hidden_predecessors`
   is a yes/no. A reader who may not read the Teema itself is told nothing about
   its history (QA-06). Under 0181 this is a deleted Teema for a business reader.
   A deleted predecessor is not offered as history (0102), and the walk is not
   continued through a deleted stage (owner, QA-04: the cut is kept). A cycle
   reads «Ahel jõuab tagasi juba näidatud teemani, kaugemale seda ei näidata.»
   and a cut chain reads «Ahel on pikem kui 12 etappi, näidatud on lähimad.».
   Neither names an endpoint.
6. **One walk per request.** The `readable_continuations` tag caches the answer
   on the request, keyed on the Matter, its `superseded_by` and whether it is
   open. Header, banner and rail share one walk, and the HTMX re-renders
   (inline saves, a closure's out-of-band header) read the same scope. Amended
   2026-11-05 (QA-10): the backward history is walked once per Matter instance
   and reader (memo keyed on the viewer object and `(pk, superseded_by_id,
   is_open)`, never shared across readers), one query per level, and the header,
   «Seotud materjalid» and the Dokumendid earlier-stage pages share it. At most
   `MAX_PREDECESSORS` (12) direct predecessors are named; past that
   `more_predecessors` is set and the header points to «Varasemad etapid»
   (QA-08).
6a. **No cycle through a closure (2026-11-05, QA-03).** `close_matter` refuses a
   `Järglane` whose forward chain leads back to the closing Matter: the pair and
   the pre-read chain are locked `FOR NO KEY UPDATE` in ascending id order; a
   step changed meanwhile is locked as it is reached only with `NOWAIT`, so the
   closure never waits out of order (independent review E-1: a waited late lock
   could deadlock a legitimate closure), and a step somebody holds refuses it
   with «Valitud järglase jätkuahel muutus samal ajal. Proovi uuesti …». The walk
   is bounded at 200 hops, past which the closure is refused too. Every refusal
   comes before any write, and the memoised history of the successor instance is
   dropped after the write. The Lovable importer already refuses cycles at plan
   time.
7. **`get_matter`** (read-only MCP) carries `continuations`: `successor`,
   `successor_hidden`, `predecessors`, `hidden_predecessors`, `chain` (earliest
   first, this Teema included), `chain_starts_hidden`/`chain_continues_hidden`,
   `endpoint` (with `is_open`, only when known), `cyclic` and `truncated`. Each
   stage gives its id, reference and link, in the same viewer scope. There is
   no new tool and no write scope. Amended 2026-11-05: `hidden_predecessors` is a
   boolean and `more_predecessors` is added (QA-05, QA-08); a reader who may not
   read the Matter gets no continuation information at all (QA-06).
8. **On lists (amended 2026-11-05).** Rows on the Teemad register (every surface
   that draws the shared matter table: Teemad, Minu asjad, Saabunud) and Teema
   search results carry one quiet line under the title: «Jätkub teemas 2025_196».
   The reference is a link. When the chain's known endpoint is not the direct
   successor, it follows the arrow («→ 2026_190 (avatud)»). An unreadable or
   deleted successor reads «Jätkub mujal» without a reference.
   - Amended 2026-11-05 (QA-18/QA-19): a stage is named by reference and a short
     title (40 characters, title last so a narrow cell clips the title first); a
     stage without a reference is named by its short title once. With a known
     endpoint other than the successor the line reads «Jätkub teemas X → [ … ]
     viimane etapp Y (avatud|suletud) · pealkiri»; «viimane etapp» is visible,
     the «…» is decoration and a screen reader hears «vahel veel N etappi»
     (readable stages only).
   - One scoped batch query per page, plus one per chain level for the endpoints
     (`app/matters/continuation_lines.py`); never per row.
   - The status badge is unchanged.
   - Earlier stages are listed under «Seotud materjalid» (0209).

No schema change and no data change.
