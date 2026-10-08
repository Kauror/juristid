# 0144 — October 8 follow-ups: planned «Tehtud», a next step read from the record, several sent files

**Status:** accepted
**Date:** 2026-10-08

The owner's follow-up round of 2026-10-08. **One migration**, `submissions/0009`:
an additive table for a sent opinion's further files and the evidence triggers
extended to it. No existing row is rewritten; every opinion recorded before it
reads exactly as it did.

## §1 A planned action can be marked «Tehtud» on its own

Every planned row offers `✓ Tehtud | Muuda | ×`.

- `✓ Tehtud` opens a compact form: what happened (required — «Kohtumist ei
  toimunud» and «Vajadus langes ära» are ordinary answers; there is no separate
  «jäta ära» state) and optional files. It works on any day, before the
  planned date or after it.
- The save is `workspace.complete_planned_action`: the note (an `Entry`,
  restricted with the action per docs/adr/0138), its files and the action's
  completion in one operation, so `Teema käik` reads one «✓» row. The action
  ends `COMPLETED` with `NEXT_ACTION_COMPLETED` carrying `planned: true`.
- **It finishes that action and nothing else.** The current action keeps its
  text and date, no other planned row moves, and nothing is promoted —
  promotion stays the current action's completion (docs/adr/0143 §A4). The
  action is named by the form and re-read under the Matter's lock; a stale tab
  is refused with nothing written.
- `Muuda` still changes the words and moves the day freely, earlier or later.
  `×` reads «Kustuta planeeritud tegevus» and still cancels with history kept.
- A refused `✓ Tehtud` or `Muuda` comes back into its own row, opened, with the
  field's message. (A `Muuda` refusal used to return 400 with nothing visible.)

## §2 `Järgmisena?` reads the record

The suggestion's heading is «Järgmisena?» (was «Soovitatud järgmisena»), and
it no longer offers work the Matter's canonical records already prove done.
`plan_view.RecordedWork` reads, per reader through each record's own
`visible_to`, and decides by the step's stable `template_step_key` /
`operation` — never by its words:

| step | proven by |
|---|---|
| `read-material` | a published `Ülevaade / uudis`, a `Kaasamine`, or a sent opinion |
| `website-overview` | a published `Ülevaade` (or a publication filed before the kind existed) |
| `consult-members` | a `Kaasamine`, waiting or finished |
| `form-position` | feedback recorded on a finished `Kaasamine`, or a sent opinion that stands |
| `send-opinion` | a sent opinion — **unless a request for an opinion is still outstanding** |

A repeat `Arvamuse tähtaeg` therefore brings the opinion step back: an earlier
opinion never answers a later request. Nothing is written by the reading; a
dismissal (`×`) persists as before, and a step the record stops proving comes
back. This amends docs/adr/0141 §2, which kept records from advancing the
suggestion.

## §3 Scheduled work is the next step

A round waiting for feedback or a planned action is next-step work. The Teema
page does not print «Järgmine samm on määramata» above it, and
`next_step.without_next_step` — behind «järgmise tegevuseta» on Minu asjad,
Osakond and the register filter — counts the same way. A current `Arvamuse
tähtaeg` is not a step: it is what the next step is for, and stays in the
header. No `NextAction` is created to make the page read right.

## §4 A new organisation survives the save

The organisation picker's `<noscript>` fallback carried a text box named like
the hidden carrier `+` writes into. A picker that arrives in an htmx swap is
parsed by DOMParser with scripting off, so the fallback became a live, empty
control posted after the carrier; the server read the empty one, and a new
organisation vanished on save with «Vali …». `bindOrganisationPickers` removes
the fallback when it binds. The resolution path (`resolve_organisation_name`,
`get_or_create_organisation`) is unchanged.

## §5 A Koja arvamus may go out as several files

`Saadetud failid` takes one or more files; the separate `Töödokumendid` box is
gone from the panel (working documents stay addable on the sent opinion's row,
docs/adr/0129 §7). All the files belong to **one** `Submission`:

- `Submission.final_version` is the first file, with every guarantee it had.
- `SubmissionSentFile` (`submissions/0009`) holds the second and later files,
  in order, each its own `Document` + `DocumentVersion` filed as the letter's
  role and restriction. Never one opinion per file.
- The same rules, both ways: `bind_further_sent_files` (only on a draft that
  has its first file, before the send is stamped) and `mark_submission_sent`
  check same Matter, not removed, not a working document, never less
  restricted; the send event names them (`sent_files`). The database backstops
  are a trigger on the new table and the four evidence triggers extended to it
  (`0002` submission and document visibility, `0005` Matter visibility, `0006`
  reparent); reversing the migration restores their earlier bodies.
- A further file cannot be revised, reclassified, removed or filed as a working
  document while the send stands; Dokumendid shows each with its send, the rail
  draws one line per opinion with every sent file on it, «Registreeri
  saatmine» never offers one, and `check_evidence_integrity` reads them.
- Repeat-deadline rules and the response-deadline → Submission link are
  unchanged: the answer names the one Submission.

This amends docs/adr/0129 §1 («still exactly one file»).

## §6 Every form's save reads «Salvesta»

A button that submits and persists a create/edit form reads «Salvesta»; where
one page holds several, the accessible name still says which
(`aria-label="Salvesta tegevus"`, «Salvesta uus versioon»…). Controls that do a
different act keep their verb: Alusta, Tehtud, Muuda, Märgi saadetuks, Lõpeta
tähtaeg, Kinnita…, Ava uuesti, Eemalda, Kustuta, search and filters. Labels
only — no lifecycle, confirmation or draft/sent semantics changed.

## §7 Smaller cleanups

- Uus teema and the `Saabunud` registration form end with their buttons
  («Ülejäänud andmeid saab lisada ka hiljem teema lehel.» and «Ülejäänud andmed
  saab lisada teema lehel.» are gone; the second added the same day).
- «Muuda teemat» no longer heads its form with «Kontrolli dokumendist leitud
  andmeid»; the assisted review is unchanged and stays in the Teema header's
  `⋯` menu.
- The Kaasamine start form asks «Keda kaasad» and refuses with «Kirjuta, keda
  kaasad.»; the correction form of a filed round keeps the past tense.
- The Dokumendid upload panel drops its two help sentences; formats, roles and
  validation are unchanged.
- Dokumendid `⋯` is a compact floating menu (`Muuda nime`, «Dokumendi leht»;
  on an opinion row also its send details) that never grows the row.
- The shared upload queue shows each title as text with a pencil; only that
  row becomes editable (Enter/blur keep, Escape cancels), and the original
  filename stays visible once the title differs.

## §8 The summary uses the left column

The Teema summary spans the main column up to the 300px rail, and the full
width once the rail stacks below 1100px; it no longer stops at 96ch.

## Amendment of 2026-10-08 — a planned check asks what it found

§1's `✓ Tehtud | Muuda | ×` changes for one kind of planned row, the
`Arvamuse järelkontroll` (docs/adr/0146 §5–§6):

- `✓ Tehtud` asks for a typed outcome instead of a free note: «Vastus saabunud»,
  «Vastust ei ole — kontrollin uuesti» with the next day, or «Lõpetan
  jälgimise» with a reason.
- `Muuda` moves the day only, in place.
- There is no `×`.

`complete_planned_action`, `change_planned_action` and `cancel_planned_action`
refuse a check. Every other planned row is exactly as §1 describes.
