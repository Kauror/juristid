# ADR 0151 — `Koja ettepanek või pöördumine` is a Teema the Chamber starts, recorded as the `Koja algatus` track

- **Status:** accepted for §1–§4; §5 (the thirty-day check) **awaiting the
  product owner's choice of trigger**
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

`app/matters/initiative.py` is the one place that knows what the fact means
(`is_koda_initiative`, `set_koda_initiative`, `refuse_new_incoming_dates`).

### 2. No incoming dates are established, and none is erased

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
