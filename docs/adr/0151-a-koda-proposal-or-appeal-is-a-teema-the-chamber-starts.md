# ADR 0151 — `Koja ettepanek või pöördumine` is a Teema the Chamber starts, recorded as the `Koja algatus` track

- **Status:** accepted for §1–§4; §5 (the thirty-day check) **awaiting the
  product owner's choice of trigger** — **amended 2026-10-12**: the checkbox
  is retired, `Õigusakt` «Koja ettepanek või pöördumine» establishes the track
  on a new Teema, `Uus teema` can record an already-sent proposal in the same
  save, and §5 is resolved (trigger A, the owner's wording). See the amendment
  at the end of this document.
- **Date:** 2026-10-09
- **Context:** the product owner's brief of 2026-10-09, R5.
- **Narrows:** 0097 §3 (`Menetlusliik` left the ordinary UI — one of its values
  is asked again, as its own question); 0094 §5 / 0133 §8 (`Arvamuse tähtaeg` is
  the obligation — a Chamber initiative has none).

Most Teemad begin with something arriving — a draft sent for comment, a question
asked of Koda — with the day it arrived (`Saabus`) and, often, the day Koda owes
its opinion by (`Arvamuse tähtaeg`). The owner wants the other kind recorded and
recognisable: work the Chamber initiates itself, a proposal or an appeal, where
nothing arrived and nobody asked. Such a Teema has neither date, and it is
followed up thirty days after it goes out.

## Decisions

### 1. One stored fact, and it already existed: `Matter.track` «Koja algatus»

Three things in the model are near this fact. They were weighed before choosing.

| | what it answers | for R5 |
| --- | --- | --- |
| `Õigusakt` «Koja ettepanek või pöördumine» (`koja-ettepanek`) | what kind of *instrument* the Teema concerns | not it: the Chamber may propose a change to a regulation, which is `Õigusakt` «Määrus»; and ADR 0070 §1 keeps instrument and procedure independent |
| `Menetlusliik` / `Matter.track` «Koja algatus» (`KODA_INITIATIVE`) | what kind of *process* this is — always in the vocabulary, in reporting's track breakdown, and dormant in the UI since 0097 §3 | exactly it: a process the Chamber started, whatever instrument it concerns |
| a new boolean | — | would say what `KODA_INITIATIVE` says, and could disagree with it |

So R5 is recorded as `Matter.track = KODA_INITIATIVE`. No column is added and no
migration is needed. `Õigusakt` «Koja ettepanek või pöördumine» stays the kind of
instrument and implies nothing (0090 §4: no track is derived from an
instrument); a Chamber proposal about its own idea simply carries both.

**Superseded on 2026-10-12 for «implies nothing» — see the amendment at the end
of this document.** The stored fact is unchanged (`Matter.track` «Koja
algatus», no new column), but on a new Teema choosing `Õigusakt` «Koja
ettepanek või pöördumine» now establishes it, and on `Muuda teemat` a change of
that instrument moves it.

`app/matters/initiative.py` is the one place that knows what the fact means
(`is_koda_initiative`, `set_koda_initiative`, `refuse_new_incoming_dates`).

### 2. No incoming dates are established, and none is erased

- **Superseded on 2026-10-12 — the checkbox is retired; see the amendment.**
  The two dates are still set aside, now when the instrument is chosen.
- **`Uus teema`** asks «Koja ettepanek või pöördumine» as a checkbox beside the
  two dates. Ticked, both date boxes are hidden and emptied by the page
  (`bindInitiative`) and set aside by the form (`MatterCreateForm.clean`):
  `Saabus` arrives holding today's default, and no default is ever recorded as
  a fact. `create_matter` itself refuses either date on native work on this
  track; an importer still records what a register says.
- **No response obligation can begin.** With no `response_deadline` and no
  `response_requested_at`, `response_obligation_of` is not outstanding, so the
  Teema never reads «arvamust koostamisel» in Osakond, Minu asjad or the
  register (`opinion_state_q`'s step branch needs an outstanding obligation).
- **Every writer refuses to establish one** on an initiative, with the same
  sentence: `set_matter_dates` (the edit form and the header's `Saabus`
  editor), `change_response_deadline` (the header's deadline editor),
  `request_response_deadline` (`+ Lisa → Arvamuse tähtaeg`).
- **History is not erased.** A Teema marked later keeps the dates it already
  holds; moving or clearing one is the ordinary, audited correction
  (`MATTER_DATE_CHANGED`, the response-deadline history). `Muuda teemat`
  (`data-initiative-mode="keep"`) shows a held date beside the note and hides
  only an empty box; its form refuses a new one (`clean_initiative_dates`).

### 3. Reviewable on the Teema, correctable on `Muuda teemat`

- The header's meta line says «Koja ettepanek või pöördumine» where `Saabus`
  would stand, and offers no `+ Saabus`, no `+ Arvamuse tähtaeg` and no
  `+ Lisa → Arvamuse tähtaeg`. A held date is still shown and still editable.
- **Superseded on 2026-10-12 — the checkbox is retired on this page too; a
  change of `Õigusakt` to or from «Koja ettepanek või pöördumine» marks or
  unmarks, and nothing else does. See the amendment.**
- `Muuda teemat` carries the same checkbox, arriving ticked for an initiative.
  Marking sets the track through `change_track` (`MATTER_TRACK_CHANGED`, the
  previous track in `from`); unmarking clears only «Koja algatus» — a Teema on
  another track is never touched by a save about something else. Marking over
  another track replaces it, and the event says what it replaced.
- Business-write authorization is unchanged: only those who may write the
  Teema can mark or unmark it.

### 4. An ordinary Teema is exactly as it was

Unticked — the default — nothing changes: `Saabus` today, `Arvamuse tähtaeg` as
typed, the request recorded, the obligation outstanding.

### 5. The thirty-day check — the trigger is the owner's decision

**Resolved on 2026-10-12: trigger A, the recorded send, and the check reads
«Kontrolli, mis on pöördumisest saanud». See the amendment at the end of this
document.**

The owner asked for a check thirty calendar days after «the relevant
initiating/outgoing event» and has not yet said which event. The existing
workflow was inspected (ADR 0146, `app/workflow/follow_ups.py`):

- **The only recorded, verifiable outgoing event is a sent `Submission`** — SENT,
  `sent_at`, `final_version` evidence, `SUBMISSION_SENT` — and **every
  interactive send already schedules the check at its sending date + 30**
  (`schedule_follow_up_of`, «every kind counts», 0146 §1). A Chamber proposal
  registered as sent through `+ Koja arvamus` or Dokumendid «Registreeri
  saatmine» gets its check today, idempotently, visible in Minu asjad, Osakond
  and the Teema, reschedulable, finishable, and ended by closure — with no new
  scheduler and no invented date.
- What it lacks is only words: `+ Koja arvamus` records the kind «Ametlik
  arvamus» and the check reads «…Koja arvamusele vastanud».

The choice, and its consequences:

| trigger | what it needs | truthfulness |
| --- | --- | --- |
| **A. The recorded send of the proposal** (recommended) | nothing new to schedule; optionally a `SubmissionKind` «Koja ettepanek või pöördumine» used on an initiative and the check's wording for it (a choices-only migration) | the date is a recorded, evidenced dispatch |
| B. The day the Teema was created | a second scheduler not tied to a `Submission`, or a follow-up without one | creation is not proof that anything went out; a draft proposal would be "followed up" |
| C. A separate «Saadetud» date on the Teema | a new field and a scheduler | a date with no evidence behind it, beside a `Submission` that may say otherwise |

**Nothing for B or C is built**, and no check is created for an initiative
except through the existing send path. A's optional wording is prepared as a
separate commit and is not merged until the owner chooses:

- `SubmissionKind.KODA_PROPOSAL` «Koja ettepanek või pöördumine»
  (`submissions/0010`, a choices change with no SQL and no row touched);
- `+ Koja arvamus` on an initiative records that kind; every other Teema still
  records «Ametlik arvamus»;
- the check reads «Kontrolli, kas adressaat on Koja ettepanekule vastanud» (and
  the plural) for that kind; every existing check keeps its words;
- creating an initiative schedules nothing.

Consequence to weigh with it: Osakond's «arvamust välja sel nädalal» counts
every SENT `Submission` (ADR 0149), so a sent proposal is counted there too —
as it already is today when a lawyer registers one through `+ Koja arvamus`.

## Alternatives considered

- **A new `is_koda_initiative` boolean.** Rejected: redundant with
  `Track.KODA_INITIATIVE`, and two stored answers to one question will
  eventually disagree.
- **Read the initiative off `Õigusakt` «Koja ettepanek või pöördumine».**
  Rejected: a proposal can concern any instrument, and 0090 §4 forbids
  deriving a procedure from an instrument.
- **Clear held dates when a Teema is marked.** Rejected by the brief: erasing a
  recorded fact needs an explicit, audited correction, which the ordinary date
  editors already are.
- **Refuse the create-form dates instead of setting them aside.** Rejected:
  `Saabus` is pre-filled with today on every new form, so a refusal would fire
  on every initiative filed without scripting.

## Consequences

- Reporting's «Menetlusliik» breakdown starts counting new initiatives under
  «Koja algatus», which is what that row has always meant.
- `Uus teema`, `Muuda teemat` and the Teema header gain one control or one line;
  their visual baselines change by design and are adopted from CI candidates
  with the owner's approval only.

## Reversibility

Entirely, by code: no migration. Unmarking an initiative restores the ordinary
behaviour at once; every date and every track change is in the audit trail.

---

## Amendment, 2026-10-12 — the instrument is the control, a sent proposal can be filed in one save, and §5 is resolved

- Status: accepted (product owner's decisions of 2026-10-12), amending §1's
  «`Õigusakt` … implies nothing», §2's and §3's checkbox, and resolving §5.
  Narrows docs/adr/0090 §4 for this one instrument.
- Scope: `Uus teema`, `Muuda teemat`, `app/matters/initiative.py`, the
  thirty-day check's wording for `KODA_PROPOSAL`. **No migration** and no
  stored flag: `Track.KODA_INITIATIVE`, `SubmissionKind.KODA_PROPOSAL`, the SENT
  `Submission`, `SubmissionSentFile`, `OpinionFollowUp` and `NextAction`
  already represent every fact.

### What was decided before

- §1: the initiative is `Matter.track` «Koja algatus», and `Õigusakt` «Koja
  ettepanek või pöördumine» implies nothing (0090 §4).
- §2/§3: a separate checkbox «Koja ettepanek või pöördumine» on `Uus teema`
  (with the note «Koda algatab selle ise: saabumise kuupäeva ega arvamuse
  tähtaega ei ole.») and on `Muuda teemat`.
- §5: the thirty-day check's trigger awaited the owner; A (the recorded send)
  was recommended and the wording «Kontrolli, kas adressaat on Koja
  ettepanekule vastanud» prepared.

### Why it is superseded

The form asked the same thing twice: `Õigusakt` offers «Koja ettepanek või
pöördumine» and a checkbox below asked «Koja ettepanek või pöördumine» again.
Duplicate input is what the product exists to remove. And the lawyers file
Chamber proposals two ways — as work still to be prepared («prepare an appeal
in two weeks»), and as a proposal that has already gone out — and the second
had to be entered as a Teema and then sent again through `+ Koja arvamus`.

### What is decided now

1. **The standalone «Koja ettepanek või pöördumine» checkbox is retired** from
   `Uus teema` and `Muuda teemat`, and with it the note «Koda algatab selle
   ise: …» — replaced by nothing. A forged `koda_initiative=on` binds to
   nothing. No other visible «Koja algatus» control is added.
2. **On a new Teema, `Õigusakt` «Koja ettepanek või pöördumine» establishes
   `Track.KODA_INITIATIVE`** (`initiative_on_create`). The stored fact is still
   the track; there is no new boolean. `Saabus` and `Arvamuse tähtaeg` are set
   aside exactly as §2 says (the page hides both and their row; the form
   drops whatever they held), so no response obligation begins. A two-week
   internal target belongs to the ordinary `NextAction`, never to `Arvamuse
   tähtaeg`.
3. **On `Muuda teemat` only a change of that instrument in the same save moves
   the track** (`initiative_after_edit`, `apply_initiative_after_edit`):
   - changed *to* «Koja ettepanek või pöördumine» → `Koja algatus` (replacing
     another track, audited with what it replaced; nothing written when it
     already is one);
   - changed *away from* it on a Teema that is `Koja algatus` → the track is
     cleared (`MATTER_TRACK_CHANGED` from `KODA_INITIATIVE`). On such a Teema
     the instrument and the track said the same thing and the instrument is the
     only control left for it, so correcting one corrects the other;
   - any other save — the instrument unchanged, or changed between two other
     kinds — leaves the track exactly as it is. **A historical `Koja algatus`
     on another instrument (a `Määrus`, say) survives every such save**, and no
     historical row is rewritten or migrated. The page carries the stored
     answer (`data-initiative-held`, `data-initiative-held-instrument`) so its
     script reaches the same answer the server will, and `clean_initiative_dates`
     still refuses to establish a date on the answer the save gives.
4. **Two entry paths, one architecture.**
   - **A — still to be prepared.** Nothing else ticked: a working Teema, no
     `Submission`, no invented send date or recipient, no check; the files are
     ordinary documents (`role_for`); the standard plan is offered as for any
     new Teema. When the proposal goes out, `+ Koja arvamus` records a
     `KODA_PROPOSAL` (unchanged since §5's prepared commit) with the real day,
     addressees and evidence, and schedules the check.
   - **B — already sent.** «Märgi saadetud Koja arvamuseks», offered only for
     that instrument *with at least one file* (and refused by the server
     otherwise: `SENT_PROPOSAL_NEEDS_THE_INSTRUMENT`,
     `SENT_PROPOSAL_NEEDS_A_FILE`). It reveals `Saadetud` (today's
     Europe/Tallinn date, editable, never in the future — the person has just
     said it went) and `Adressaat` (the `+ Koja arvamus` organisation picker
     and `resolve_addressee`, with no default from `Saatja`). The whole file
     package is **one** `Submission`: the files are filed once, as `Koja
     arvamus` documents, and `record_sent_proposal_on_new_matter` stands the
     send on them through `register_sent_opinion_on_open_matter` — the
     Dokumendid send — with the first file as `final_version` and the rest as
     `SubmissionSentFile`s, kind `KODA_PROPOSAL`, `SentAtPrecision.DATE`.
     Everything is inside the creation transaction and the same operation as
     the files (`Teema käik` reads one act); the form's one-time token keeps a
     double submit to one Teema, one send and one check. A closing `Hetkeseis`
     is refused with it (the check would end at once).
5. **The send, not the creation, triggers the check** (§5 resolved, trigger A),
   through ADR 0146's one scheduler: `Submission.sent_at`'s business day + 30
   calendar days. Creating a Teema schedules nothing.
6. **The wording for `KODA_PROPOSAL` is «Kontrolli, mis on pöördumisest
   saanud»** — the owner's words, one sentence for one addressee and several
   (`FOLLOW_UP_PROPOSAL_TEXT`). The check stays linked to its `OpinionFollowUp`
   and `Submission`, never matched by text. Checks scheduled before keep the
   words they were written with.
7. **On path B the check is the current work.** No standard plan is seeded, so
   no «Tutvu materjaliga» competes; the check is promoted by ADR 0143 §A4's
   `promote_next_planned_action`, and `PRAEGUNE TEGEVUS` reads it rather than
   «Järgmine samm on määramata».
8. **Reporting needs nothing new.** The Teema counts under `Menetlusliik` «Koja
   algatus», the send under SENT submissions and under «Arvamused liigi järgi»
   as «Koja ettepanek või pöördumine», its addressee under the recipient
   breakdown and its day in the send-date reporting — the same rows a later
   `+ Koja arvamus` writes (tests/test_koda_proposal_create_flow.py proves the
   two paths converge).

### What this amendment does not change

- The stored model: no migration, no new flag, no rewrite of a historical
  `KODA_INITIATIVE` row or of a historical combination of track and instrument.
- §2's protections: every writer still refuses to *establish* `Saabus` or
  `Arvamuse tähtaeg` on an initiative (`refuse_new_incoming_dates`), held dates
  are still kept and correctable, and imports still record what a register
  says.
- §3's header line «Koja ettepanek või pöördumine» and the absent `+ Saabus` /
  `+ Arvamuse tähtaeg`.
- ADR 0146's scheduler, rules and outcomes for every kind; the wording of every
  `FORMAL_OPINION` check.
- `+ Koja arvamus`, `Märgi saadetuks` and `Registreeri saatmine` — path B
  composes the latter's service and adds none.
- Authorization: only those who may write business content may create a Teema
  or record a send.

## Amendment, 2026-10-13 — an initiative may have an `Arvamuse tähtaeg`

- Status: accepted (product owner's decision of 2026-10-13), superseding §2's
  «no `Arvamuse tähtaeg` is established» and the 2026-10-12 amendment's «both
  dates are set aside». **No migration.**
- Scope: `Uus teema`, `Muuda teemat`, the Teema header,
  `app/matters/initiative.py`, `app/matters/response_deadlines.py`,
  `create_matter`, `record_sent_proposal_on_new_matter`.

### What was decided before

A Chamber initiative could never be given an `Arvamuse tähtaeg`: `Uus teema`
discarded one, and every writer refused to establish one
(`INITIATIVE_HAS_NO_RESPONSE_DEADLINE`).

### Why it is superseded

A proposal or appeal the Chamber starts may still have a date it is due by. The
blanket rule threw away a date the lawyer had typed.

### What is decided now

1. **`Saabus` is unchanged.** Nothing arrived, so none is recorded or
   established; the box is hidden on `Uus teema`, and a held one is still kept
   and correctable.
2. **`Arvamuse tähtaeg` is optional and explicit.** On `Uus teema` it stays
   visible and empty when «Koja ettepanek või pöördumine» is chosen; a date
   given is saved as `Matter.response_deadline` through the ordinary writers,
   and an empty box is no deadline. Nothing is derived from the creation day.
   The header offers `+ Arvamuse tähtaeg`, and the deadline is set, moved and
   cleared in the header and on `Muuda teemat` like any other.
3. **A deadline is not a request.** The two acts that mean Koda was asked —
   `+ Lisa → Arvamuse tähtaeg` (`request_response_deadline`) and «Uus arvamuse
   küsimine» (`REPLACED`) — stay refused on an initiative
   (`INITIATIVE_HAS_NO_NEW_REQUEST`), and «Uus arvamuse küsimine» is not
   offered there. No addressee, incoming document or external request is
   created. The document assist still proposes no deadline for an initiative.
4. **A deadline is not a send.** Until the proposal goes out the deadline is
   outstanding work like any other. The thirty-day check still starts only
   from an actual SENT `Submission`. When «Märgi saadetud Koja arvamuseks»
   records the send in the same save as a deadline, that send **answers** the
   deadline through `answer_current_deadline_with` (ended `ANSWERED`, linked to
   the `Submission`), so nothing is left outstanding.

### What this amendment does not change

The track, the two `Uus teema` paths, `SubmissionKind.KODA_PROPOSAL`, the
thirty-day check and its wording, `initiative_after_edit`, the ordinary
deadline lifecycle and every ordinary Teema.

---

## Note, 2026-10-14 — §4 holds for every send door

The 2026-10-13 amendment's §4 was implemented only on the fast path. ADR 0163 §1
makes every interactive send on a `Koja algatus` Teema answer its own current
deadline (QA-WORKFLOW-04); nothing above changes.
