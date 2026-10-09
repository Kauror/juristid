# 0148 — The 2026 Excel pilot imports a reviewed sample as native work

**Status:** accepted, amended 2026-10-09 (§10 — an opinion still being written
starts with «Koostan arvamust»; the 35-Matter sample)
**Date:** 2026-10-09

The owner's operational-pilot round. The hosted test-build is to be used for
real work on a small, real dataset: a reviewed sample of the department's 2026
register (`Tööd eelnõudega 08.10.26.xlsx`, worksheet `2026` only), replacing the
old development data. Excel is authoritative for that one snapshot; after the
import every new thing happens in Juristid, and nothing is synchronised back.
When the system is commissioned for real, this pilot database is discarded and a
new import is made from the newest register and genuine documents.

The 2026-10-08 readiness audit had measured why the existing machinery could not
simply be pointed at the workbook: the department's current `Hetkeseis` spellings
were unknown (53 of 237 rows without a stage, seven finished files left as live
work), the register-era read model turns unconverted `JÄRGMISEKS` text into
«EXCELIST» lines, a register date cannot become a sent opinion, and an importer
may not schedule follow-up checks. This round fixes the generic mappings and adds
one gated, test-build-only operation for the rest.

**Two migrations, both additive.** `workflow/0015` adds four generic
`LegacyStatusMapping` rows (data only). `legacy_import/0016` creates
`ExcelPilotImport`. Nothing existing changes.

## 1. One sheet, a reviewed sample, a reproducible manifest

`excel_pilot_2026` reads worksheet `2026` through its era contract and opens no
other sheet. The sample has three groups:

- **A — every genuinely active row whose `VÄLJA` is blank**, decided by rule: no
  terminal `Hetkeseis`, no continuation, a readable `VÄLJA`. A blank cell is not
  read as active on its own — a blank `VÄLJA` beside «rohkem ei tegele» is a
  finished file.
- **B — five sent opinions on files still being worked**, chosen by a person.
- **C — five finished or continued files**, chosen by a person.

`manifest` writes the selection, every source value it relies on, each row's
contracted-cell digest and the expected treatment of every field into a JSON
manifest, outside the repository. Its digest is the SHA-256 of canonical JSON.
`plan` re-derives the manifest from the workbook and refuses if a byte of a row,
the selection or a rule moved; `apply` demands that digest. Empty rows, the
`KOKKU` totals row and reserved numbers never become Matters, but the highest
reference the sheet speaks for (`2026_300`) is reserved, so a Teema created
during the pilot cannot collide with the rest of the register.

## 2. The department's current vocabulary

- **`Hetkeseis`.** «jõustumise ootel», «Eesti seisukoht koostamisel», «ELi õiguse
  ülevõtmise ootel» and «rohkem ei tegele» map to the stages whose reviewed
  labels they already are (`CURRENT_LABEL_TO_STAGE`, `workflow/0015`). The older
  spellings keep their rows. «rohkem ei tegele» maps to the `monitoring_stopped`
  *stage*, as current vocabulary going forward; «rohkem pole tegevusi plaanis»
  stays the disposition it has always been read as (0131 §9 — history is not
  reread). `register_semantics` adds «rohkem ei tegele» to the terminal labels.
- **Contract headers.** A column may name reviewed `header_aliases`; the 2025
  and 2026 contracts (version 1.2) accept `ANDSID TAGASISIDET` and
  `KÜSISIME TAGASISIDET` beside the long forms, which older snapshots still
  carry. `UUS VASTUTAJA` and `KOJA ETTEPANEK VÕI PÖÖRDUMINE` stay uncontracted
  and out of scope: never read, and never copied. The pilot's source reference
  keeps the contracted cells only — the same cells the row digest covers — so
  neither column reaches the database.

## 3. `Õigusakt` from the current vocabulary

The department's 2026 instrument list is version 2.0's offered list word for
word. `current_legal_instrument_keys` reads a cell against the offered labels
first, then version 1.0 aliases whose keys are still offered; a part that
resolves only to a retired key, or to nothing, leaves the whole value unmapped.
The pilot writes the result to `Matter.legal_instruments` — the Matters are new,
so no lawyer's classification can be overwritten, which is the precedence
question 0070 left open. **The generic importer is unchanged** and still writes
nothing canonical.

`Muu siseriiklik` and `Muu ELi dokument` are imported exactly. The register never
says *which* kind, so `Muuda teemat` asks for the free-text kind on the next save,
as it does for anyone who chooses a `Muu`.

## 4. Organisations

Reference data 1.1 adds Riigikantselei and the Riigikogu's eleven standing
committees, named as the register names them («Riigikogu majanduskomisjon»),
after the audit measured 33 of 237 2026 addressees addressed to a committee. The
core set and the ministries are unchanged. A source misspelling is answered by
the selection's reviewed `[organisations]` mapping, never by a permanent alias;
an ambiguous value («sotsiaalpartnerid», a bare «Komisjon») is reported and not
recorded.

## 5. Work status: the badge beside the title

The badge beside the title answers *is the Chamber working on this* in one of
four words, derived from columns the Matter already holds and never stored
(`app/matters/work_status.py`):

| Word | Colour | Decided by |
| --- | --- | --- |
| Aktiivne | green (`--status-success`) | `is_open` |
| Jätkub mujal | blue (`--status-info`) | closed with `SUPERSEDED` or a successor |
| Mitteaktiivne | neutral grey (`--text-secondary`) | closed with `COMPLETED` («Jõustunud»), `INITIATIVE_WITHDRAWN`, or no reason |
| Lõpetatud | subdued grey (`--text-muted`) | every other closure — the Chamber's decision |

`Hetkeseis`, work status and the closure decision stay three things. A sent
opinion closes nothing.

A finished imported row is closed in the register's own undated shape: ARCHIVE,
`is_open=False`, a `Disposition`, **no `closed_at`** (the closure constraint
permits that only for an archive record, and the register never says when). The
precedence is: a continuation to a selected Matter (`SUPERSEDED`, with
`superseded_by`); «rohkem ei tegele» (`MONITORING_STOPPED`); «ei saatnud»
(`NO_POSITION_FORMED`); «jõustunud» (`COMPLETED`). No `MATTER_CLOSED` event is
written, because no closure happened in Juristid.

Two display corrections follow:

- **The «Arhiivikirje. Imporditud registrist…» notice** is for the historical
  register archive. A pilot record is ARCHIVE only because of its undated
  closure, so the notice is not shown on it
  (`Matter.shows_register_archive_notice`).
- **A note with no author** shows its kind («Märkus») on its closed chronology
  line instead of a bare date.

## 6. `JÄRGMISEKS` becomes ordinary workflow

There is no register read model for pilot rows: no `CurrentRegisterState`, so no
«EXCELIST» line, no «registri vaatlus» and no second place for the same
instruction. Each cell becomes one of:

- **a dated action** — the register parser 2.1 understood it; kind, date meaning
  and precision as parsed;
- **an undated action** — the parser refused only on the date; kept word for
  word with the kind its words name (`instruction_kinds`), never a guessed day;
- **a note (`Märkus`)** — the sentence names nothing Koda does («jõustub
  eeldatavasti 1. aprillil»), or the file is finished. No task is ever created on
  a finished file.

Actions go through `set_next_action` with the pilot's provenance and the owner
responsible (a colleague only when the sentence begins with their unique given
name). Notes are `Entry` rows with no author. The one-current-action invariant
holds, and checks are planned beside the current step.

## 7. Sent opinions with placeholder evidence

A dated `VÄLJA` means the Chamber sent an opinion, so each selected dated row
becomes a canonical SENT `Submission` through `register_sent_opinion` — the
importers' door below the interactive wrappers. The send has date-only
precision, carries the addressees resolved from `KELLELE`, and has no human
sender. **The evidence rule is unchanged:** each send's final evidence is a
generated one-page PDF headed «PROOVIMPORT — ASENDUSDOKUMENT / SEE EI OLE TEGELIK
KOJA ARVAMUS». The page carries the register reference, the `VÄLJA` date and why
the file exists, and no legal content. Its file name and document title say
PROOVIMPORT.

`ExcelPilotImport.placeholder_version` and the version's `source_identifier`
(`excel-pilot-2026-placeholder:…`) mark every placeholder, and
`placeholder_versions()` finds them all. A placeholder is never swapped for a real
letter: commissioning replaces the database.

The row's `Arvamuse tähtaeg` is ended as **answered** by that opinion, or as
**decided not to answer** for «ei saatnud». A row still being written keeps its
deadline open.

## 8. Follow-up checks, only from the pilot

For every imported send on a file that stays active, the pilot calls
`schedule_first_check`, the one scheduler 0146 defines. That gives one `PLANNED`
check per opinion, due on the sending date + 30 calendar days and assigned to the
owner. It may already be overdue, and it is moved and finished through the
established check forms. A finished file gets no check. **0146 §9 is unchanged
for every other path:** the importers and the archive apply still schedule
nothing, and a test pins that only `excel_pilot.py` in `app/legacy_import`
reaches the scheduler.

## 9. Gates

`apply` refuses unless all of the following hold:

- `JURISTID_EXCEL_PILOT` is set on the one-off container that runs it (never in
  an env file);
- `REAL_DATA_ALLOWED` is on;
- the operator types `--operator-intent excel-pilot-2026`;
- the manifest's own digest is passed;
- the verified pre-reset backup set is named;
- the database holds no Matter the pilot did not create.

Everything is written in one transaction. Run again with the same manifest, it
writes nothing. Accounts, roles and authentication are untouched.

## Consequences

- **Measures that read a register field.** Osakond's ARVAMUS KOOSTAMISEL counts
  native drafts only, so group A shows through its `Arvamuse tähtaeg` instead.
  The feedback counts are kept in provenance but not displayed. «Uued teemad»
  counts natively created Matters by definition.
- **Records the pilot creates.** Pilot Matters have origin `LEGACY_IMPORT`, keep
  their `2026_N` reference internally (exact search finds it; nothing new shows
  it) and carry no import-ledger rows, so `Kustuta teema` works on them as on
  native work.
- **Amendments.**
  - 0070, for the pilot path only.
  - 0146 §9, a gated exception.
  - 0021 and 0045: a pilot row has no register read model.
  - 0131 §9: the current spelling of «rohkem ei tegele» is the stage.

## 10. Amendment, 2026-10-09 — an opinion still being written is the current step

The owner's 35-Matter round (pilot version 1.1). The first pilot read
`JÄRGMISEKS` as the only source of a current step, so an active row with a
blank `VÄLJA`, an `ARVAMUSE TÄHTAEG` and an empty `JÄRGMISEKS` — fourteen of the
twenty opinions being written in the 09.10 snapshot — became a Teema with no
current action, although the row says plainly that the opinion is being
written and by when.

**At import time only**, a row whose opinion is still being written — active
(§5), `VÄLJA` blank, `ARVAMUSE TÄHTAEG` present — gets one ordinary current
step through `set_next_action`:

| | |
| --- | --- |
| text | «Koostan arvamust» (`DRAFTING_TEXT`) |
| kind / date meaning / precision | `DO` / `DEADLINE` / `EXACT` |
| date | the row's `ARVAMUSE TÄHTAEG`, exactly — never moved, kept when past |
| responsible | the Matter's owner (`VASTUTAJA`) |
| history | the one `NEXT_ACTION_SET` event, no actor, provenance `OPINION_DRAFTING` |

The step and `Arvamuse tähtaeg` are one obligation seen twice: the Matter
keeps the deadline open (§7), the Teema's secondary obligation line stays quiet
because the step already shows that day, and the work queues list the step
rather than the bare deadline (0050). A sent opinion discharges the deadline as
it always has; the lawyer completes or changes the step through the ordinary
controls.

On such a row `JÄRGMISEKS`, when written, is **a note (`Märkus`) beside the
step**, word for word — never a second task, a second deadline or text
appended to the step. A cell that reads as an instruction is still a note, and
the manifest records `instruction-kept-as-note:<KIND>` so `plan` names it for
review; none of the 09.10 snapshot's six does.

Unchanged: sent and finished rows (§6, §8), the parser, the evidence rule, the
follow-up scheduler, every UI surface. A row with no `ARVAMUSE TÄHTAEG` gets no
drafting step, because the row names no day for it. **0133 §8 is unchanged for
native work:** a new Teema still gets no step from its deadline — the rule is
the import's reading of what an existing register row says, not a runtime rule,
and nothing creates a step on a Matter merely because it has none.

The sample is now 20 A + 6 B + 9 C = 35 (`pilot_35_selection.toml`, outside the
repository), from a derived A–L snapshot of 09.10 whose only difference from the
08.10 workbook is one unselected row's `VÄLJA`, since marked «ei saatnud».
`PILOT_VERSION` 1.1 is part of the manifest, so a manifest written under 1.0 no
longer re-derives and `plan` refuses it.
