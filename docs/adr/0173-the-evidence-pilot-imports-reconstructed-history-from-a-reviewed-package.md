# 0173 — The evidence pilot imports reconstructed history from a reviewed package

**Status:** proposed (2026-10-19) — built and tested on synthetic data; merged on
2026-10-20 (#501, released with #500–#504) and run against production once, by the
owner's separate authorisation, the same day (see the status update at the end).
**Date:** 2026-10-19

The owner's twelve-topic brief of 2026-10-19. The hosted test-build is to be
replaced, in a later session, by a small dataset that a lawyer can test end to
end: the twelve topics a source system (Lovable) recorded as opinions still being
written, each with its owner, `Hetkeseis`, deadlines and next step **and** the
professional history behind it — earlier consultation rounds, member feedback,
other organisations' positions, the Chamber's sent opinions with their real
signed letters, koda.ee overviews and news, procedural developments — rebuilt
from the original OneNote dossier and its attachments.

0148's Excel pilot cannot carry this: it reads one register row per Matter, its
sent opinions stand on a generated placeholder, and it has no notion of a
document, a round or a position. The historical corpus import (0015/0026) carries
files but writes no domain records. This is the smallest addition that does both:
one module, one command, one provenance table.

**One additive migration.** `legacy_import/0017` creates `EvidencePilotRecord`.
Nothing existing changes. (Amended in §6: the row's Matter is empty for a
reviewed organisation, and only then.)

## 1. A reviewed package, outside the repository

The input is a directory: `package.json` and an `onenote-desktop-archive/` in the
layout `onenote_archive.OneNoteArchive` already reads. The package names every
Teema (raw source title, alternative titles, owner, `Õigusakt`, `Hetkeseis` label,
`Saabus`, `Arvamuse tähtaeg`, addressee, next step), every record to write with
its target, date, date precision, documents and evidence, the documents that
belong to no record, and every file **not** imported with its reason. Its digest
is the SHA-256 of canonical JSON; `apply` demands it, so the reviewed package and
the applied one are the same bytes. Real packages live in an approved real-data
workspace, never in Git, CI or an ordinary development database. Tests build a
synthetic one.

## 2. Validate, plan, apply, verify

- **`validate`** — no database. Format and version; unique `YYYY_N` references in
  the package's year; the expected topic count and owner distribution; a known
  `Hetkeseis` label; ISO dates and a stated precision (DAY, MONTH, QUARTER as
  `YYYY-Qn`, YEAR) that reads; a review state of READY, NEEDS_REVIEW or
  REJECTED, a rejected record saying why (`decision_reason`); **every file on every page accounted for exactly once**, by a record, as
  an unlinked document, or as not imported with a reason; every file to be
  imported has bytes and **is re-hashed** against the archive; no READY record is
  dated after the snapshot (`snapshot.as_of`) — a newer fact is reported, never
  imported — except a commencement or a planned milestone, which is when
  something is to happen; the target-specific minimum below.
- **`plan`** — reads only. Owners through `resolve_owner` (a lone given name held
  by exactly one account, or a name the package maps it to); stages through the
  reviewed label maps; `Õigusakt` through `current_legal_instrument_keys`
  (0148 §3); organisations through `resolve_organisation` and the package's
  **APPROVED** mappings only — a PROPOSED mapping is the owner's to decide. An
  addressee outside the catalogue is a warning and is not recorded; a recipient or
  a discovered position's organisation outside it is a **problem**, because a
  sent opinion with no addressee and a position with no author cannot be written.
  The database state is `fresh` (empty), `applied` (exactly this package) or
  `foreign`.
- **`apply`** — refuses unless `JURISTID_EVIDENCE_PILOT` is set on the one-off
  container, `REAL_DATA_ALLOWED` is on, the operator types
  `--operator-intent evidence-pilot`, passes the package digest and names the
  verified pre-reset backup set, the plan has no problems and the database is not
  `foreign`. One transaction. Run again, it writes nothing.
- **`verify`** — reads back every provenance row's target, the Teema fields, the
  stored bytes of every document against the archive, every sent opinion's final
  letter, that no change event on a pilot Matter carries an actor, and that every
  page file is accounted for.

## 3. What it writes, and through what

Every write goes through the service a person's save or another importer uses,
with `actor=None`:

| Package target | Service | Business date kept in |
| --- | --- | --- |
| the Teema | `create_imported_matter` (FULL, `LEGACY_IMPORT`, the current stage as one undated IMPORTED period) | `received_date`, `response_deadline` |
| the current step | `set_next_action` (DO, DEADLINE, the source's own words) | `target_date` |
| `ProceduralDevelopment` | `record_procedural_development` (+ `…_document`) | `occurred_on` + precision |
| `MatterEngagement` | `add_engagement`; a past round is filed as history (`lifecycle_tracked=False`) | `occurred_on` + precision |
| `MatterExternalPosition.RECEIVED/DISCOVERED` | `record_external_position` (+ `…_document`), a reply tied to its round | `stated_on` + precision |
| `Submission.SENT` | `register_sent_opinion`, final evidence the **real** signed letter (`KODA_SUBMISSION_FINAL`), further files as `further_versions` | `sent_at`, DATE precision |
| `MatterWebsiteOverview.OVERVIEW/NEWS` | `plan_website_overview` → `publish_website_overview` | `published_on` |
| `Entry.NOTE/MEETING` | `add_entry`, no author | `occurred_at` (start of the Tallinn day) |
| `MatterEffectiveDate` | `add_effective_date` | `date_value`/`period_end` |
| `MatterImportantDate` | `add_important_date` (a planned milestone, never a commencement) | `date_value`/`period_end` over the stated period |
| `organisations[]` (APPROVED) | `get_or_create_organisation` — §6 | — |
| `answered_by` on a Teema | `resolve_response_deadline(ANSWERED, submission=…)` — §6 | the reply's `sent_at` |
| files | `create_document` + `add_evidence_version` (SHA-256 compared with the archive) + `link_document_to_record`; `LegacySourcePage`, `MatterSourcePage` (REVIEWED_MATCH), `LegacySourceResource`, `LegacySourceResourceImport` as the corpus import writes them | — |

A record the package marks `NEEDS_REVIEW` or `REJECTED`, a `ReviewOnly`,
`WorkVictoryCandidate` or `MatterField` item, and the files of a held record are
not written; the files are recorded `SKIPPED` with the reason
(`RECORD_HELD_FOR_REVIEW`, or `RECORD_REJECTED_FOR_IMPORT` with the decision), so
the page's catalogue stays complete. The evidence is never deleted.

## 4. What it never does

- **No backdated or invented audit.** The services write their ordinary events,
  stamped with the import's moment and no actor — which is true. The business date
  is on the record. `EvidencePilotRecord` — package digest, record key, target,
  source page and the evidence (blocks, files, web captures, the date's basis,
  confidence) — is what says *imported historical fact*.
- **No stage history.** The current stage only, as 0148 does; earlier rounds are
  records under that one period, not periods with invented dates (0131 §3).
- **No closure, no follow-up check** (0146 §9 unchanged: `schedule_first_check`
  is still called by `excel_pilot.py` alone), **no person and no alias** created,
  and **no organisation** except a reviewed package entry (§6).
- **No sent opinion without its letter**, no member reply filed as an opinion, no
  signed container unpacked (0125: the container is the evidence).
- **No partial apply** over an existing or half-written database.

## 5. Known mapping gaps (reported by the plan, not papered over)

- **Expected commencement** has no "estimated" state: `MatterEffectiveDate` is
  `KNOWN_DATE` with the description saying «eeldatav». A first-class expected
  date is a separate decision.
- **Historical organisation names** (a ministry before a rename) and bodies
  outside the reviewed baseline (an agency, a central bank, an association) are
  not in the catalogue. A sent opinion or a discovered position naming one is
  held until a reviewed package entry (§6) or an approved mapping exists.
- **Related Teemad** (`link_related_matters`) needs a signed-in person; a
  previous-stage reference is kept in provenance and reported.
- **A month-precise note or meeting** cannot be stored (`Entry.occurred_at` is a
  moment); such an item is held.

## 6. Amendment — the reconciliation round (2026-10-19)

The owner's second brief asked for the positions and earlier opinions the first
package held only because their organisation was missing, a reply that arrived
after the snapshot, and a planned Government milestone. Five package fields, no
new service:

- **`organisations[]`** — a reviewed reference-data entry: name, type
  (`OrganisationType`), registry code, notes, `valid_from`/`valid_to`, the
  evidence for its identity (a letterhead, a signature block, a registry code)
  and a status. Only **APPROVED** entries are written, before any Teema, through
  `get_or_create_organisation` — the resolver every counterparty field uses: an
  exact or alias match is reused and **left as it was**, two catalogue matches
  refuse the plan, nothing is merged on similarity. Notes, validity and source
  are set on a row this apply created, never on one that existed. Each is
  remembered as an `ORGANISATION` provenance row whose `matter` is empty — the
  one kind allowed to be (a check constraint). A historical name (a ministry
  before its rename) is its own entry with a note, never relabelled as its
  successor.
- **`shared_documents_from`** — a joint letter is one position per signatory;
  the letter is filed once, on the first, and links to each of the others
  (`DocumentLink` is unique per document *and* record). A sharing position lists
  no files of its own.
- **`REJECTED`** with `decision_reason` — an item the owner decided not to import
  (a member's reply filed as an opinion, a later fact without corroboration). It
  is reported apart from `NEEDS_REVIEW`; its files stay accounted and skipped.
- **`MatterImportantDate`** — a planned milestone (a Government adoption) is an
  `Oluline tähtaeg` over its stated period (QUARTER, MONTH, …) with its source
  words, never a `MatterEffectiveDate`.
- **`answered_by`** — the owner's reconciliation that an imported reply answers
  the source's current request. The request is still recorded first
  (`request_response_deadline`); then `resolve_response_deadline` moves it to
  history as ANSWERED, naming that opinion. An earlier round's opinion never
  answers it unless the package says so. `submission_kind` and `reference`
  (the letter number) are carried onto the opinion.

`verify` checks the answered request, a created addressee and every approved
organisation.

**Amended 2026-10-19, after an independent review of the implementation.**
Each rule closes a way a later package could write an untrue fact; none
occurred in the reviewed package:
- a note or meeting needs its exact day (undated, it read as the import day);
- a publication date is a day, or the overview is undated;
- a past round carries no reply-by day;
- a name is never merged into another by its registry code: two entries on one
  code are refused at validate, a code held by a differently named catalogue row
  at plan, and a code-only match at write;
- the package pins every file's SHA-256 (the digest covers `package.json`, not
  the archive) and the stored bytes match it;
- the apply locks `matters_matter` in its transaction and re-reads the database
  state, so a Teema created after the plan stops it;
- lengths the database would refuse mid-apply are refused at validate.

A refused apply rolls back every row but can leave stored files with no
document; the runbook prunes them (`prune_orphaned_evidence`) after any failure. The migration is still the one additive `legacy_import/0017`
(regenerated before the merge; merged 2026-10-20).

## 7. Amendment — reviewed step kinds and organisation continuity (2026-10-20)

The owner's decisions of 20.10.2026 (evidence pilot v2.3) need two package
contracts that v2.2 could not state. Both are explicit, reviewed fields and are
never read off the wording.

**A source step states its kind.** `next_action` may carry:
- `kind`: DO, WAIT or MONITOR;
- `date_semantics`;
- `target_date` **or** `review_date`;
- `date_precision`.

`step_shape` reads them. A package without them (v2.2 and earlier) gets the old
shape: DO, DEADLINE, exact.

The apply refuses:
- a WAIT or MONITOR with DEADLINE semantics, because a wait is never missed;
- a `review_date` on a DO;
- both dates on one step;
- an `INFERRED` precision;
- a precision on an undated step;
- an unknown kind.

So 2026_225's «Ootan Riigikogu II lugemist» is imported as a WAIT, with no
invented date, its source words in `source_text` and `kind_basis:
REVIEWED_PACKAGE` in its provenance. With PR #502's amendment of ADR 0050, the
WAIT does not take the 04.11 `Arvamuse tähtaeg` off the work lists.

**Organisation continuity.**
- An approved organisation may carry `aliases`, each with a reviewed
  `alias_type` and evidence. An alias is added on that body's own identity, and
  never when another body already answers to it.
- The package's `organisation_relationships` may state a **CONTINUATION**
  `{predecessor, successor, effective_from, evidence}`: the same body under a new
  name or remit. The apply sets the successor's existing
  `Organisation.predecessor` link, only when it is empty, and an existing
  different predecessor stops the plan. A split, a merger or a transferred task
  is refused, because a predecessor link would over-claim it.

Nothing is renamed, merged or re-addressed. A 2021 letter to
«Justiitsministeerium» keeps that addressee. Each alias and each link gets its
own provenance row (`ORGANISATION_ALIAS`, `ORGANISATION_RELATIONSHIP`), and the
row's check constraint (`legacy_import/0017`, merged 2026-10-20) allows both
without a Teema. `verify` reads both back.

**Search learns the link, not a new name.** `continuity_names` (one hop each
way along `predecessor`) is added to a Matter's, a sent letter's and an external
position's alias text. A search for either name therefore finds the other's
records, while each record keeps its own addressee. Organisation saves already
owe a search rebuild (ADR 0041), so the corpus converges.

The v2.3 package uses this for:
- «Justiitsministeerium» → «Justiits- ja Digiministeerium». The basis is VVS
  § 105¹⁹ lg 10: «Justiitsministeerium korraldatakse ümber Justiits- ja
  Digiministeeriumiks alates 2025. aasta 1. jaanuarist», RT I, 30.12.2024, 1.
- the reviewed abbreviation «EAHÜ».

## Consequences

- One new model, one command (`evidence_pilot validate|plan|apply|verify`), one
  setting (`EVIDENCE_PILOT_ENABLED` from `JURISTID_EVIDENCE_PILOT`).
- **Amends** `add_engagement`'s statement that one caller files rounds as
  history: the evidence pilot is the second.
- The future replacement of the hosted dataset follows 0148's reset discipline
  (backup set, old database kept, fingerprint), and is out of scope here.

## Status update 2026-10-29 — merged, applied once, gated

This section records what happened after the decision; it does not amend the
decision, and its status is unchanged (no formal acceptance was recorded).

- **Merged** 2026-10-20 in #501 (`b2850d6f` added this record; merge `a821c4e3`),
  with the additive migration `legacy_import/0017_evidence_pilot_record.py` (and
  `0018`) now on `main`.
- **Deployed** as production release `f0795fe5` (2026-10-20 15:36 UTC, #500–#504);
  the four migrations of that release, `legacy_import/0017` among them, were
  applied there. Backup set `sets/20261020T153421Z` before the swap.
- **Run once** on production the same day, by the owner's explicit decision: the
  reviewed v2.3 twelve-topic package was applied and `verify` passed; the earlier
  pilot world was kept as a separate database and backup set.
- **Now**: the operation is complete. `evidence_pilot` and its gate
  (`EVIDENCE_PILOT_ENABLED`, from `JURISTID_EVIDENCE_PILOT`, together with
  `REAL_DATA_ALLOWED`, the intent, digest, backup-set and empty-database checks)
  remain in the code, and the flag stays unset in every deploy file — the command
  refuses without it. Keeping or removing the command is an owner decision
  (audit 2026-10-28, F-12: kept).

