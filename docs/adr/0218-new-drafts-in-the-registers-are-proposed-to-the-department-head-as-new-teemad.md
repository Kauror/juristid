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
   Off unless `JURISTID_REGISTER_SWEEP` is on.
2. **What a Teema already follows is not new.** A document is left out when an
   open Teema's title names its bill («973 SE»), when the per-Teema watch
   already watches that bill, or when the watch already found that document.
   The rest are gathered into `TopicCandidate`s, one per draft: documents whose
   titles share the same distinctive words (`candidates.match_key`, the
   title less the words every draft and covering letter carry) join one
   candidate, so «… eelnõu kooskõlastamiseks esitamine» and «Arvamus … eelnõule»
   are one row with two documents. Each document is stored once.
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
4. **Three answers, each by a person.** On «Uued teemad»
   (`/arengud/uued-teemad/`): «Lisa teemaks» opens Uus teema with the title
   (the Riigikogu's name with «NNN SE» when there is one), the sender ministry
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
   named to them. The way in is «Uued teemad (N)» on Osakond.
7. **The bell's sixth source.** A sweep that lists new candidates (not folded
   ones) creates one «Uued teemad registrites» notice for each person who may
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
- Nothing here creates a Teema, moves a stage or sets a deadline.
