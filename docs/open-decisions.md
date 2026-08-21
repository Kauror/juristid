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

## Decisions taken by the development agent in Stage 2B

Recorded so they can be challenged rather than inherited silently. Parser
choices are ADR material (ADR 0014); these are the ones with a product or
security edge.

1. Extracted text is **not** evidence, and the two are visibly separated
   everywhere they appear together. A preview that reads as the source of
   record is the provenance defect the whole stage is arranged to avoid.
2. OCR is decided per page, not per file, and never runs where the page has its
   own text. Where both exist the author's characters win, and every fragment
   records which of the two it is.
3. A DOCX match reports a section, a paragraph range or a table, and **never a
   page**, because the format does not contain page boundaries.
4. Email attachments become Documents with role `EMAIL_ATTACHMENT` and nothing
   more specific. Mail arrives from ministries, members, associations and
   colleagues alike, and a guessed role files half of them wrongly.
5. Inline resources — signature logos, tracking pixels — are counted and do not
   become Documents.
6. Raw email HTML is never rendered. It is sanitised to text, and the original
   message is one download away for anybody who needs the formatting.
7. Every resource limit refuses rather than truncates. A partially extracted
   legal document marked complete is worse than one marked failed.
8. `SearchDocument` still stores no visibility. Child restrictions are read from
   the live child row through a foreign key, which is what made Stage 2A's
   deferral (item 8 above) safe to close.
9. The OneNote tool ends at a neutral archive plus a reconciliation report.
   Title similarity is a review queue and can never become an automatic match.

## New decisions Stage 2B raises for Koda

| Decision | Owner | When | Notes |
| --- | --- | --- | --- |
| Whether OCR text may be quoted in a submitted opinion, or must be checked against the original first | Department head | Before the Secure Pilot Gate | The system marks OCR text as OCR everywhere it appears. Whether that is a warning or a prohibition is a professional judgement, not a technical one. |
| Whether legacy `.doc` and `.xls` are worth a conversion stack | Department head | When the archive's real composition is known | They are stored and downloadable today, and their contents are not searchable. The answer depends on how many of them there actually are. |
| Whether a ZIP archive should ever be expanded into Documents | Department head + whoever owns security | Later | Stage 2B stores them whole and deliberately does not unpack. Expansion brings decompression limits, path handling and unclear business meaning, and nobody has yet asked for it. |
| Retention for derived content, if it differs from the evidence it came from | Privacy/legal | With the retention policy | Derivatives are deleted and rebuilt freely today. If extracted text of member correspondence is itself subject to a retention rule, that rule needs saying. |


## Decisions taken by the development agent in Stage 2E

| Decision | Why | Reversibility |
| --- | --- | --- |
| Metric definitions live in code, not in a table | A definition editable through the product is a definition nobody reviewed, and "who changed the population" becomes unanswerable exactly when somebody disputes a figure. `services.COMPUTERS` is asserted at import to cover the catalogue in both directions. | Low — it is the point of the design |
| A OneNote-only Matter has no *reporting* year | Its `reporting_year` is the page's own timestamp. The importer is right to record the only date it has; reporting is not entitled to call that a reporting year, so those Matters sit in **Teadmata aasta** and their page dates are analysed separately as source history. | Moderate — one tuple, `REGISTER_YEAR_ORIGINS` |
| Default period is the current year | The question a lawyer opens the page with is about now. Archive-wide metrics ignore the period and say *kogu korpus* on their own cards, so the default cannot hide the corpus. | High |
| A file occurrence is the headline material count | The same bytes on two pages are two occurrences, because the corpus contains the thing twice. Distinct SHA-256 is a separate metric shown beside it. | Moderate |
| Materialisation state is per occurrence, best state across visible links | The corpus-level question is whether a file has been brought across at all. A page shared between Matters can be materialised for one and not the other. | Moderate |
| CSV is semicolon-delimited UTF-8 with a byte-order mark | A comma-delimited UTF-8 file opens in Tallinn as one column of mojibake, and an export nobody can open is an export nobody uses. | High |
| No materialized views | None has been justified by a measurement, and adding one before measuring buys a refresh problem. | High |
| Six new register filters and a `puudub` sentinel | Every chart segment promises a list. Without them the promise could not be kept for the year axis, the origin bars, the *määramata* buckets or the next-action counts. | Moderate — other surfaces may come to depend on them |

## New decisions Stage 2E raises for Koda

| Decision | Owner | When | Notes |
| --- | --- | --- | --- |
| Which `SubmissionKind` values count towards the annual report's "kirjalikud arvamused" | Department head + reporting owner | Before the first annual report drawn from this system | The Statistika page shows submissions by kind and deliberately does not add them into one headline figure. The kinds exist; which of them the Chamber counts is a business decision. |
| Whether the register's `VÄLJA` dates should ever appear in annual reporting | Department head | When the historical numbers are next quoted | They are outbound-date observations with no evidence behind them. They are not Submissions and must never be merged with `Submission.sent_at`. Making them queryable at all is work nobody has asked for yet. |
| What completeness threshold a duration metric would need | Department head + reporting owner | Before any median response time is published | The result infrastructure supports medians and percentiles. Nothing ships until start and end semantics are precise, the population is homogeneous and the sample is large enough — otherwise `INSUFFICIENT_DATA` is the correct answer. |
| Which Statistika figures the DashKoda export must reproduce, and to what tolerance | DashKoda owner + reporting owner | Stage 4 | ADR 0007's contract predates the metric catalogue. Now that definitions exist in code, the export can be specified against them rather than against a prose description. |
| Whether member-feedback counts are worth persisting as columns | Department head | If anyone asks for the number | Deferred in Stage 2E because recovering them means re-parsing raw spreadsheet cells. They would still never become a response rate: asked and answered are independent observations, and the register has rows where more answered than were asked. |
| Whether the period picker should offer date ranges rather than whole years | Department head + lawyers | After the pages have been used | Whole years match the register's own reporting identity. A day-precision filter over a year-precision fact invites false precision, so it was not built speculatively. |

## Decisions taken by the development agent in Stage 2G

Recorded so they can be challenged rather than inherited silently. The
architecture reasoning is in ADR 0018; these are the ones with a product edge.

| Decision | Why | Reversibility |
| --- | --- | --- |
| `Oluline tähtaeg`, `Jõustumine` and `Töövõit` are three models, not tags and not one generic event table | Each carries validation, review state, provenance and audit that a tag assignment cannot; and every constraint that keeps a fabricated commencement date out of the database would become conditional on a `kind` column in a merged table. | Low — it is the point of the design |
| A period is stored as its first day **and** its last, plus its precision | Ordering and "has this passed" both become plain SQL, and the second question needs the end: II poolaasta 2027 has not passed on 2 July 2027. | Moderate — two derived columns, one writer |
| Only a `KNOWN_DATE` commencement may carry a date, enforced by a CHECK constraint | The brief allows an `UNKNOWN` row to have a null date; this goes further and forbids it having one at all. A date stored against "kuupäev täpsustamisel" is indistinguishable from a real one on every page downstream. | High — one constraint |
| Year filtering only, for approximate periods | Every precision offered sits inside one calendar year, so the year is exact. A day-level range filter over a quarter-level fact would expose false precision. | High |
| The work-victory form requires an explicit period answer, including "Teadmata periood" | Neither a silently unknown period nor a pre-filled current year is a fact somebody stated. Choosing costs one click and the record then says what a person meant. | High |
| Writes go through full-page forms that redirect back to the Matter anchor, not HTMX fragment swaps | The Matter overview is rendered by `app.matters.views` from its own context builders; swapping part of it from `app.intelligence` would couple the two apps for half a second of latency. | High |
| `Jälgimine` is one navigation item with three tabs | Three more top-level links would crowd a shell that already carries five. Statistika established the pattern. | High |
| Structured text is **not** yet in `SearchDocument` | A structured fact deserves its own `SearchSourceKind` row so a result can name what matched, which means new foreign keys and signal wiring in a module Stage 2E.1 is editing concurrently. The dedicated pages filter their own records meanwhile. | High — additive when 2E.1 has landed |

## New decisions Stage 2G raises for Koda

| Decision | Owner | When | Notes |
| --- | --- | --- | --- |
| Whether a confirmed `Töövõit` must later carry a Proposal/Outcome/Attribution record | Department head + management | Before any work-victory figure is published outside the department | Today a confirmed victory means a person judged it one. The specification's eventual outcome model (6.6) is more demanding, and `MatterWorkVictory` is shaped so those rows can be referenced or migrated rather than discarded. Nothing in this stage computes influence. |
| Whether a specialist may confirm a work victory, or only the department head | Department head | Before the pilot | Implemented as department-head only, because it is the Chamber's own claim about its influence. Specialists create and edit candidates freely on any Matter they can reach. One frozen set in `app.core.authorization` if the answer differs. |
| Whether `Jõustumine` should keep appearing inside the combined *Olulised tähtajad* view | Department head + lawyers | After the pages have been used | Implemented as a labelled presentation over one source of truth, with a selector that narrows to either kind. No row is duplicated, so turning it off is a default change. |
| The exact OneNote-list import and reconciliation procedure | Department head + whoever owns the archive | Before any import | Planned route: list entry → embedded OneNote page id → `LegacySourcePage` → `MatterSourcePage` → Matter → structured record with `legacy_source_page` and `source_text` set. Where a page relationship exists it is authoritative; where unique resolution fails the row goes to a review queue. **Title fuzzy-matching must never run automatically.** |
| How a legacy line that says only "2 töövõitu" should be reviewed | Department head + the lawyer who wrote it | With the import | The importer will create **one** candidate preserving the raw sentence in `source_text`, for a person to split. It will not invent two descriptions, and there is deliberately no quantity column to put a 2 in. |
| Whether these dates should eventually trigger reminders | Department head | After the pilot | Nothing schedules, mails or notifies in this stage. The structured dates make it possible; whether a deadline three weeks out should reach somebody's inbox is a working-practice decision, not a technical one. |
| Whether a confirmed work victory should require a period | Department head + reporting owner | Before the first annual figure | A candidate may honestly have no period. Whether confirming one should force the question is a reporting decision; the constraint would be one line. |
