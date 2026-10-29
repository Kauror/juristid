# 0191 — The Lovable pilot adds reviewed current Teemad beside the existing ones

**Status:** proposed (2026-10-26) — built and tested on synthetic data and
dry-run against an isolated copy; not merged. Applying it to the KOCORP
lawyer-testing database is a separate, owner-authorised operation behind the
Secure Pilot Gate.
**Date:** 2026-10-26

The owner's brief «Import 100 Real Lovable Matters for Lawyer Testing»
(2026-10-26). Lawyers testing Juristid need a realistic population of current
work — Minu asjad, the calendar, quick date moves, stages, deadlines, closed and
historical files — and the department's operational register lives in Lovable
(the «Loo Command Center» project's `public.topic` and its commencement,
change-history and Excel-source tables). OneNote stays the source of the files.

Lovable is still the operational source of truth. Nothing here writes back to it,
synchronises with it or makes Juristid authoritative for these records; a later
cutover will re-import from a fresh snapshot.

**No migration.** Provenance reuses `MatterSourceReference`, `ImportBatch` and
`EvidencePilotRecord` (with `pilot = "lovable-pilot"`; every reader of that table
already filters by its pilot).

## 1. Relation to 0157

0157 (on `feature/lovable-mirror`, PR #429, architecture accepted, activation not
approved) keeps Lovable data as a *mirror* and makes `jargmiseks`, `valja`,
`valja_kuupaev` and `hetkeseis` **NEVER_AUTO**: nothing may be derived from them
automatically, and promotion needs a new decision. This record is that decision,
for this pilot only, and it keeps 0157's rule intact: **nothing is read off the
wording at apply.** A person reviews each topic and the package states the
reading — step kind, date meaning, date, lifecycle, disposition, commencement
kind — with the reviewer's reason; `validate` refuses a step without an explicit
kind and date meaning, a WAIT or MONITOR that is not a review date, and a
deadline already past at the snapshot. The parser (`register_next_actions` 2.1) may
*propose* a kind while the package is being built; it never decides one at apply
(0173 §7). The mirror itself (PR #429) is not activated by this.

## 2. A reviewed package from a read-only snapshot

The package is one `package.json` outside the repository: the snapshot's
identity (time, SHA-256 of the export, the source schema's last migration, the
read-only REPEATABLE READ transaction it was read in, the selection manifest's
digest), the reviewed people map, the topics, and for each topic its decisions
plus the source row, its Excel-source row, its change history, commencements and
responsible changes exactly as read. The selection is deterministic and lives
with the package, not here.

## 3. What is written, and how

Through the ordinary services, with `actor=None`, stamped with the import's
moment — no backdated audit:

- **The Teema** — `create_imported_matter` with its own `YYYY_N`, title, owner,
  stage, `Saabus`, instrument, `menetlusliik` (Lovable's `koja_ettepanek` →
  `KODA_INITIATIVE`), and the addressee only when it resolves exactly to one
  catalogue entry.
- **Open work** — FULL, TIER_1. The reviewed current step through
  `set_next_action`: WAIT or MONITOR always `REVIEW_ON` on Lovable's structured
  `tegevuse_tahtaeg` (never a deadline, never overdue); DO only where reviewed.
- **A genuinely outstanding opinion** (open, nothing sent, a deadline) —
  `request_response_deadline`, so only an answer that names it discharges it,
  and the step «Koostan arvamuse» DO/DEADLINE on that day (0148 §10).
- **Commencements** — «Kindla kuupäevaga» on an adopted act (`jõustumise ootel`,
  `jõustunud`) → `MatterEffectiveDate` KNOWN_DATE; on a draft →
  `MatterImportantDate` PLANNED_COMMENCEMENT; «Eeldatava kuupäevaga» →
  ESTIMATED_COMMENCEMENT; «Üldises korras» → GENERAL_ORDER with no date. The
  qualifier (`täpsustus`) is kept. The import creates no check task; a
  KNOWN_DATE it records is an ordinary recorded commencement, so 0188's
  reminder to the Vastutaja applies to it exactly as to one typed in.
- **A finished file** — ARCHIVE, `is_open=False`, its disposition, **no
  `closed_at` and no `MATTER_CLOSED`** (0148 §5). «lõpetatud», which has no
  reviewed mapping in the vocabulary, is a package decision: COMPLETED, no stage.
  Its commencements are kept as source only, because the date services refuse a
  closed file and closing afterwards would invent a closure event.
- **A continuation** — `superseded_by` only when the successor is in the same
  package, set directly on the predecessor (as the Excel pilot does), so no
  change event claims somebody linked them. A successor outside the package
  stays source text: nothing already in Juristid is linked to, updated or read
  for writing.

## 4. What is never written

No SENT opinion and no document — the letters are in OneNote, which this round
does not import, and 0173's rule (no SENT without the real final letter) stands.
A historical `arvamuse_tahtaeg` whose opinion was sent, not sent, or belongs to a
finished file is **source only**: it is never a current deadline, so it cannot
read as overdue. No follow-up check, no stage history, no person (a departed
owner with no Juristid account stays source text and the closed file has no
owner; an open file whose owner has no account is not selected), no
organisation, no placeholder. Lovable's change history stays in the source
reference, labelled as the source's.

A Lovable row whose `valja` is «ei saatnud» but whose stage and next step are
live contradicts 0148's rule that «ei saatnud» concludes the work; such rows are
not selected for this pilot and the rule question is left to the owner.

## 5. Append, never replace

The Excel and evidence pilots need an empty database. This one needs **every
one of its references to be free** — native, imported, archived or deleted. One
taken reference stops the whole apply. Under `LOCK TABLE matters_matter IN SHARE
ROW EXCLUSIVE MODE` — taken after the year's reference-sequence rows, the order
native creation uses, and with a 10-second `lock_timeout`, so a lawyer creating
a Teema mid-import is neither deadlocked nor queued for long — the plan is
rebuilt and must hash to the approved plan digest (0184). `verify` runs inside
the same transaction before the commit; a difference rolls everything back. A fingerprint over every other Matter's identity and `updated_at` is
taken before and after the writes inside the transaction and must be identical.
That fingerprint catches a model save on another Matter; it does not see a
bulk `update()` or a related row. Those are excluded by construction instead:
the writer only creates rows hanging off its own new Matters, and the one
existing row it changes is the year's reference sequence, which the plan
digest binds. A reference a permanently deleted Matter once held (kept in its
security event) counts as taken, and an owner must be somebody business work
may be assigned to (0181). Because lawyers' saves on any Matter wait for the
table lock until the commit, the apply is run outside working hours.
The plan digest deliberately excludes the total Matter count: lawyers keep
working while an import is planned. The year's reference sequence is pushed to
Lovable's highest number so a Teema created during testing cannot take a number
Lovable already holds; `reserve_through` must equal the snapshot's own
`highest_reference`, is part of the plan digest and is printed by `plan`.

## 6. The OneNote link

`MatterSourceReference.onenote_url` keeps Lovable's link byte for byte. The
Dokumendid tab — where the owner put OneNote pages (20.10.2026) — shows an
«Allikas» section with one link labelled **OneNote**, only for an `https://`
address, opening in a new tab under the reader's own Microsoft sign-in, and the
sentence «Lehe sisu ja manuseid ei ole Juristidesse imporditud». Nothing is
fetched from OneNote.

## 7. Gates and lifecycle

`lovable_pilot validate | plan | dry-run | apply | verify`. `apply` refuses
unless `JURISTID_LOVABLE_PILOT` (never in an env file) and `REAL_DATA_ALLOWED`
are set, the operator types `lovable-pilot`, the package and plan digests match,
a backup set `sets/YYYYMMDDTHHMMSSZ` is named, and the plan has no problem. A
second run writes nothing. `dry-run` writes everything, runs `verify` and rolls
back; it needs only the pilot flag. `verify` compares every topic with the
database and fails on any difference.

## Consequences

- The pilot is replaceable: every record it wrote is findable by
  `EvidencePilotRecord(pilot="lovable-pilot", package_sha256=…)`, every Teema by
  its `MatterSourceReference(source_system="LOVABLE")`, and the snapshot digest
  says which Lovable state it reflects. Lawyers' edits during testing are test
  activity, not source changes.
- `Matter.shows_register_archive_notice` treats a Lovable-pilot ARCHIVE file as
  the Excel pilot's: routine finished work, not the historical archive.
- The Secure Pilot Gate still governs whether real data may be applied; this
  record does not pass it.

## Status update 2026-10-29

The status stays **proposed**: no formal acceptance is recorded. As the release
history states it, the one-time pilot was **applied** to the KOCORP
lawyer-testing database on 27.10.2026 (13 → 113 Teemad, `verify` 100/0) and is
complete. Its `lovable_pilot` commands and `LOVABLE_PILOT_ENABLED` flag remain in
the code, gated as described above, by the owner's decision of 2026-10-29: they
are not removed and not reactivated. They are a different control from the
nightly synchronisation's (ADR 0192: `LovableSyncControl`, `JURISTID_LOVABLE_SYNC`),
which follows these Teemad afterwards.
