# 0218 — New drafts in the registers are proposed to the department head as new Teemad

Date: 2026-11-21 · Status: proposed (owner brief 2026-11-21, «a looking into the future type of thing»; answers to four questions the same day; release awaits owner review)

## Context

The register watch (ADR 0217) reads the registers for documents on Teemad that
already exist. It never says that a draft exists which no Teema follows, so a
new law that may matter to entrepreneurs is found only when a ministry sends it
to the Chamber, or when a lawyer happens to look.

The owner asked for the other direction: every night, look through the same
registers for **new** drafts, judge whether each may touch entrepreneurs,
directly or indirectly, and put the result on one list for the head of the
department, who adds or dismisses each. Old drafts stay in the existing
workflows; only what is new is listed. Asked on 2026-11-21, the owner chose:

1. EIS, which forbids automated reading (ADR 0217), is left out; access is
   asked of its operator separately, and EIS is added only when allowed.
2. Claude judges every candidate against a written profile of what the Chamber
   follows, rather than a keyword list.
3. The list shows everything, with weak and unrelated candidates folded at its
   foot, so the judgement can be checked in the first weeks.
4. The head is told in the bell; no e-mail.

## Decision

1. **A nightly sweep** (`sweep_registers`, after `watch_registers`) reads the
   registers ADR 0217 already reads, through the same fenced door
   (`sources/http.py`): the Riigikogu API for the bills (SE, OE, AE) initiated
   in the last four days, and each ministry register (HTM and Kliima PDR, the
   six RIK ADR ministries) for documents from the last four days whose title
   names «eelnõu» or «väljatöötamiskavatsus». Documents older than the window
   are never brought in — not even by the first sweep. A register that fails is
   that night's error in the run's log (`SweepRun`); the others are still read.
   On wherever the register watch reads (production), off in tests, CI and rehearsals (`JURISTID_REGISTER_SWEEP` overrides); every service reads the switch, so the page and `production_status` know whether the sweep runs.
2. **What a Teema already follows is not new.** A document is left out when an
   open Teema's title names its bill («973 SE»), when the per-Teema watch
   already watches that bill, or when the watch already found that document.
   The rest are gathered into `TopicCandidate`s, one per draft: documents whose
   titles start with the same distinctive words (`candidates.match_key`, the
   title less the words every draft and covering letter carry, and less the
   sender ADR appends after «_»; the first three words decide) join one
   candidate, so «… eelnõu kooskõlastamiseks esitamine» and «Arvamus … eelnõule»
   are one row with two documents. Each document is stored once.

   The first real sweep (21.11.2026, live registers, no model: 9 registers,
   128 documents, 88 candidates) showed two kinds of noise, handled by rule:
   - **Administrative acts** on one place, permit or person — detail plans,
     building and environmental permits, design conditions, quarries, orders
     to sign (`candidates.ADMINISTRATIVE`, 27 of 88) — are listed as «Ei puutu»
     with a fixed reason, folded, and never sent to the model.
   - **Letters whose title names no draft** («Eelnõu», «Eelnõu
     kooskõlastamine», about 20) join nothing, are not sent to the model (it
     would have nothing to read) and are listed apart, folded, under «Pealkiri
     ei ütle, millise eelnõuga on tegu», with their register links.
3. **The model judges, it does not decide.** When `JURISTID_REGISTER_SWEEP_AI`
   is on and a key exists, Claude reads each new candidate's register entries
   (titles, dates, numbers, senders — public facts only) against a written
   profile of what the Chamber follows (`candidates_ai.PROFILE`: wide on
   purpose, indirect effects count) and answers a strength (Tugev, Võimalik,
   Nõrk, Ei puutu) and one Estonian line on how the draft may touch
   entrepreneurs. It is also shown the titles of the open **unrestricted**
   Teemad and may name one the draft is plainly tied to; only an id it was
   offered is kept. A restricted Teema is never sent. Any failure leaves the
   batch unjudged for the next night.
4. **Three answers, each by a person.** On «Uued eelnõud»
   (`/arengud/uued-eelnoud/`): «Lisa teemaks» opens Uus teema with the title
   (the Riigikogu's name with «NNN SE» when there is one, else the draft's name
   cut from the letter's title, `candidates.draft_name`), the sender ministry
   and the day it first appeared filled in as starting values; the candidate is
   carried through the form and closed only when the Teema is saved, and its
   register addresses (at most five) become the new Teema's `Menetluse link`s
   in the same transaction. «Seo» writes those links on an existing open Teema
   named by its number (the model's suggestion is pre-filled). «Jäta kõrvale»
   records the decision and writes nothing. From the next night the per-Teema
   watch (ADR 0217) follows a new Teema like any other. This is the one way a
   value reaches Uus teema from elsewhere; a `kandidaat=` that is not a waiting
   candidate this person may decide binds to nothing (amends 0130's
   «no pre-fill» only for this).
5. **A dismissed draft comes back once**: when it reaches the Riigikogu
   (a Riigikogu bill joins its candidate), it returns to the list marked
   «Tagasi nimekirjas». Nothing else brings it back.
6. **The page is the head's.** It is open to whoever holds
   `department.view_management` (the department head by default). The head can
   open it to every business reader and close it again (`CandidateAudience`,
   one row, changed on the page itself). Readers see it once opened; only those
   who may write business content answer. A Teema the reader cannot see is not
   named to them. The way in is «Uued eelnõud» on Osakond (not «Uued teemad», which Osakond already uses for newly filed Teemad); the bell, not the link, says how many wait, so Osakond reads nothing more for the head.
7. **The bell's sixth source.** A sweep that lists new candidates (not folded
   ones, not nameless letters) creates one «Uued eelnõud registrites» notice for each person who may
   decide, or adds to their unread one (`CandidateNotice`). It is read and
   unread like the register notice, and becomes history once nothing is left
   on the list.

## Consequences

- EIS, the Interior, Regional and Agriculture, and Foreign Affairs registers
  are not swept (ADR 0217 §7); a draft that appears only there is not found
  until it reaches a readable register or the Riigikogu.
- The search words find drafts and the letters about them; a ministry document
  titled without «eelnõu» or «väljatöötamiskavatsus» is not seen.
- The grouping is by words, so two drafts with the same distinctive words share
  a row, and one draft named two ways can be two rows. The person decides.
- Cost: one model call per twenty new candidates a night.
- The page is called «Uued eelnõud», not «Uued teemad»: Osakond already uses
  that name for newly filed Teemad (its rail and Muudatused).
- The administrative list is a word list: a draft named like one of them
  («… ehitusloa menetluse muutmine» as a law) is folded too, though it stays
  on the page.
- Nothing here creates a Teema, moves a stage or sets a deadline.

## Amendment 2026-11-22 — moved into «Monitooring» (ADR 0219)

§4 and §6: the page is Monitooring → «Uued eelnõud» at `/monitooring/eelnoud/`, not a
link on Osakond; the head's «Näita kõigile» now opens the whole Monitooring tab. §7: the
bell's source is labelled «Monitooring» and also counts the Government's agendas.

## Amendment 2026-11-24 — the verdict is written only on a candidate still waiting (post-r91 E1)

§3's verdict was saved on the candidates as they were read before the model was asked,
so a candidate the head dismissed, linked or added while the request was out got a
strength, reason and suggested Teema written onto it afterwards. The person's decision
itself always stood (the sweep never writes `status`, `matter` or `decided_*`). Now the
sweep re-reads the batch under lock (`select_for_update`) and writes only on candidates
still `NEW` and unjudged; a candidate decided or judged meanwhile is left as it is. The
same rule as ADR 0219's amendment 2026-11-23 (2) for the Government's agenda (audit HR-02).
