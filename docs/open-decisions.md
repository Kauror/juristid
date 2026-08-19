# Open business decisions

These belong to named people, not to the development agent. Stage 0 has been
built so that each one lands cheaply when it is made; where a decision was
needed to make progress, the placeholder is marked as provisional in the code
and named here.

## Blocking or shaping Stage 1

| Decision | Owner | Why it matters now | Where it lands |
| --- | --- | --- | --- |
| Official CVI package, permitted web fonts, dark-mode interpretation | Communications/CVI | Every colour in `static/css/tokens.css` is a marked placeholder. Components must not be built on placeholders. | ADR 0009, one file |
| Final `Hetkeseis` vocabulary, help text, track applicability and closure mapping | Department head + lawyers | `StageVocabulary` and `LegacyStatusMapping` are empty; the dev seed uses provisional labels flagged `is_provisional`. The workbook's 11 authoritative labels must be transcribed from the live file, not reconstructed. | Reviewed data migration |
| Matter numbering and successor reference rules | Department head | `YYYY_N` is implemented as the default. Allocation is isolated in `allocate_matter_reference()`. | One function |
| Submission kinds, recipients, and what counts as a reportable written opinion | Department head + reporting owner | Drives `submission_sent_count` in the export contract and the Stage-1 Submission model. | ADR 0007 + Stage-1 schema |
| Initial PolicyArea list, controlled Tag seed, taxonomy owner | Department head + lawyers | Taxonomy tables ship empty on purpose; the dev seed uses only the tag concepts the specification itself names. | Reviewed data migration |
| DashKoda export format and consumer owner | DashKoda owner | The v1 contract is drafted and awaiting agreement. | `docs/data-contracts/dashkoda-export-v1.md` |

## Required before the Secure Pilot Gate

| Decision | Owner |
| --- | --- |
| Restricted-content business roles and break-glass policy | Department head + privacy/security |
| Retention, legal hold, raw email and contact-person treatment | Privacy/legal |
| SharePoint working-document permission rules for restricted Matters | Department + IT/security |
| Production Azure subscription, resource ownership and billing | Management/IT |
| Internet-facing versus internal/VPN, and Conditional Access | IT/security |

## Later

| Decision | Owner | Needed by |
| --- | --- | --- |
| Submission/opinion approval and the role of juhatus | Department leadership | Before any structured approval feature |
| `Töövõit` threshold, evidence and approver | Management + communications/legal | Phase 2 |
| Independent backup destination and key custodians | Management/IT | Before production go-live |
| Support/absence cover and second administrator | Management | Before production go-live |
| Metric catalogue owner and coverage thresholds | Department head + reporting owner | Stage 4 |
| Whether multi-year objectives justify a PolicyThread UI | Department + pilot evidence | Phase 2 |

## Decisions taken by the development agent in Stage 0

Recorded so they can be challenged rather than inherited silently:

1. Python 3.13 + Django 5.2 LTS, `uv` for locking, `ruff` + `mypy` + `pytest`
   (ADR 0001).
2. UUIDv7 primary keys generated in Python; `display_reference` derived rather
   than stored (ADR 0002).
3. Child visibility is derived at query time and never stored, so no write path
   can leave a stale value that reads as less restrictive than the truth
   (ADR 0005).
4. Tailwind and HTMX deferred to Stage 1; Stage 0 ships plain CSS custom
   properties (ADR 0009).
5. `DocumentDerivative` and `SearchDocument` deferred to Stage 2, because both
   are rebuildable and their shape depends on models that do not exist yet
   (ADR 0003, ADR 0006).
6. A closed **archive** Matter is not required to carry a closure reason, so
   that the import never invents one. Closed **full** Matters are.

## Raised during Stage 1

| Decision | Owner | Needed by | Why it is open |
| --- | --- | --- | --- |
| Final `Hetkeseis` wording, help text and track applicability | Department head + lawyers | Stage 3 pilot | The ten canonical stages now match the live workbook's raw values and are seeded by a reviewed migration, but every row is still flagged `is_provisional`. The list is right; the phrasing and which stages apply to which track have not been confirmed in a workshop. |
| Which submission kinds count as a reportable written opinion | Department head + reporting owner | Stage 4 | `Submission.kind` has six values. Which of them the annual opinion count includes is a reporting decision, and the export contract cannot be finalised without it. |
| Whether `Saabunud` should hold machine intake | Department + IT | Stage 2 or later | Stage 1 makes it a triage entry point over unassigned Matters. `IntakeItem` was deliberately not built, because a queue model with no feed to consume would be a guess about a workflow nobody has run yet. |
| Whether the composer needs a formatting toolbar | Pilot users | After Stage 3 | The editor is a plain textarea with server-side sanitising. Pasted Word and Outlook content survives correctly; typing bold and lists by hand does not. Pilot evidence should decide whether that friction is real before a client-side editor is introduced. |
| Response-deadline attention rule | Department head | Stage 3 pilot | Minu töö flags an open Matter whose response deadline has passed with no submission sent. Whether that is always meaningful — some matters legitimately conclude with monitoring only — needs a lawyer's judgement before it becomes a managed metric. |

## Raised during Stage 2A

Every one of these came out of reading the live workbook. They are business
questions the importer deliberately refuses to answer for itself, and each one
has a place already built for the answer to land.

| Decision | Owner | Needed by | Why it is open |
| --- | --- | --- | --- |
| Business sign-off on the sixteen era contracts | Department head + lawyers | Before the first real import | Every column of every year sheet was verified against the supplied snapshot and the parser rules are settled, but nobody in the department has confirmed the *meanings*. `reviewed_by` in each `.toml` says as much. This is a reading session, not a workshop. |
| What the five free-text `HETKESEIS` variants mean | Lawyers | Before the first real import | The rows use `Riigikogus 2. lugemisel`, `riigikogus 2. lugemisel`, `kinnitatud`, `rohkem tegevusi pole` and `rohkem tegevusi pole plaanis`, none of which are in the controlled eleven. The last two sit one word from the controlled `rohkem pole tegevusi plaanis`. **The importer will not decide they are the same value.** Each answer becomes one reviewed `LegacyStatusMapping` row. |
| What the unlabelled 2022 column K holds | Whoever kept the 2022 sheet | Before the first real import | 27 non-blank values, no header, no established semantics. Preserved raw and flagged. Assigning it a meaning without evidence is exactly what the era contracts exist to prevent. |
| Whether `VÄLJA` (the sent date) should become a Submission | Department head + reporting owner | Stage 2B | The register records when an opinion went out. Juristid's canonical outbound record is `Submission`, whose `SENT` state requires both a timestamp and an immutable final evidence document. Importing a bare date would create a sent opinion with no evidence and break a Stage-1 invariant, so the value is preserved raw and no Submission is created. The alternative — a `SENT_HISTORICAL` state that does not require evidence — is a real option and a real weakening, and it is not the coding agent's call. |
| Whether `ÕIGUSAKT` (instrument type) becomes a canonical field | Department head + reporting owner | Stage 4 | Present in every year and used in every row. It is not a Track and not a stage. Its notation changed from single letters (`S`, `M`, `D`) to words (`seadus`, `määrus`, `direktiiv`, `VTK`, `muu`), so any canonical field needs a reviewed mapping across eras. Preserved raw meanwhile. |
| Who attests the active set, and how | Department head | Stage 2.5 / cutover | The planner proposes `FULL_CANDIDATE` for recent rows carrying real signals, and those still land as ARCHIVE records. `FULL` is reachable only through a reviewed override file. Somebody has to own that file and the review that fills it (master specification 19.5). |
| Whether pre-numbered references should become placeholder Matters | Department head | Before the first real import | The 2026 sheet is numbered to `2026_300` while 192 rows carry a matter. Stage 2A treats the other 108 as reserved numbers: no Matter, but the reference sequence is pushed past them so native creation cannot collide. The alternative — creating 108 empty Matters — was rejected as manufacturing records, but it is a defensible choice if the department wants the numbers visible in Teemad. |
| Whether the `JÄRGMISEKS` candidates should be applied at cutover | Department head + lawyers | Stage 2B or cutover | 159 cells are populated in the snapshot; 13 produce a deterministic candidate. The rest is prose that does not state its own meaning. Nothing is converted automatically. A reviewed candidate file could create the approved ones at cutover without anybody retyping them. |

## Decisions taken by the development agent in Stage 2A

Recorded so they can be challenged rather than inherited silently.

1. Five separated layers — parse, extract, plan, apply, report — with the plan
   object shared by the dry run and the apply, so what is approved is what runs
   (ADR 0012).
2. Contracts are TOML and validated on load against closed vocabularies; the
   Markdown overview is generated from them and CI fails if it drifts.
3. A header that does not match its contract is a review finding. The parser
   never shifts columns to make a sheet fit.
4. Exact matching or nothing for owners, organisations and statuses. Normalised
   comparison is allowed because it changes spelling, not identity; similarity
   scoring is not (ADR 0012).
5. Reserved reference numbers are their own outcome: no Matter, but the
   sequence is advanced past them.
6. Raw provenance immutability moved into the database as a trigger, because
   `QuerySet.update()` never called the model guard that was protecting it.
7. `SearchDocument` stores **no** visibility, departing from the conceptual
   field list in specification 11.3 for the reason ADR 0005 already established
   (ADR 0013).
8. Entry and Submission text is not indexed in Stage 2A; the authorization work
   it needs belongs with the stage that brings document text (ADR 0013).
