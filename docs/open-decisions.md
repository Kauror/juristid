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

## Decisions taken by the development agent in Stage 2H

| Decision | Why | Reversibility |
| --- | --- | --- |
| Matching is a set of named evidence classes, never a score | A number can be tuned until it produces the coverage somebody wanted; a class can be argued with. Each candidate carries its individual signals and conflicts, and only `EXACT_BINARY_MATTER`, `EXCEL_ONENOTE_EXACT` and `STRICT_MULTI_SIGNAL` are applied without a person. | Low — it is the point of the design |
| Two exact signals are not identity; three are | The measured corpus contains a file whose date and ministry match a register row about an entirely different subject. Date + addressee picks a unique row 291 times and is wrong at least twice, so a third exact token — a Riigikogu proceeding number or a shared distinctive title word — is required. | Moderate — one function, `_third_signal` |
| A one-day date difference is a review signal, not a match | The register's `VÄLJA` falls on the letter's own date 326 times and the next day 227 times. Widening the window to one day would raise automatic matches from 291 to 462, and those extra 171 are exactly the ones a person should see. | Moderate |
| The archive's filename date is never a sent date | It is the letter's own date. Using it would put a confident wrong `sent_at` on roughly a third of the corpus. `SentDateBasis` also excludes file mtime, ZIP headers, import time and `created_at` by construction. | Low |
| Several archive files on one Matter on one day are **one** sent action | Measured: `2025_44` is a letter plus its `Lisa 1`, and `2024_139` is four earlier letters resent together. Filing those separately would overstate output by the number of attachments. Which file is the letter is a judgement, so the whole group goes to review. | Moderate |
| KodaDash binds to the archive by the producer's recorded SHA-256, or not at all | The producer workbook's `02_source_binding_audit` binds 759 of 759 rows exactly. The same data matched by (encoding-tolerant) filename produced three collisions and five wrong assignments. A workbook without hashes is refused rather than name-matched. | Low |
| KodaDash's normalised ministry never becomes a Juristid Organisation | The producer folds `Keskkonnaministeerium` into `Kliimaministeerium` in 52 rows and collapses comma-separated pairs to their first name. That is a defensible cross-era analytics bucket and an indefensible historical identity. Both are stored, under different names. | Low |
| The review queue records decisions; the importer executes them | A web request must not need a 105 MB archive mounted to write evidence bytes, and every byte-writing path should be one path. A reviewer's confirmation is stored on the candidate and consumed by the next `opinion_archive apply`. | Moderate |
| `sent_at_precision` rather than a second date column | `sent_at` stays a `DateTimeField` so native timestamps keep their precision. A date-only historical value is stored at a midnight anchor that the UI never renders, because "00:00" is a fact no source supplied. | Moderate — one field and one template branch |
| Coverage is reported per year, never as one corpus average | The archive holds nothing before 2020 while the register begins in 2011. A single "38% matched" headline would let a reader believe the 2014 opinions were merely unmatched rather than absent. | Low |

## New decisions Stage 2H raises for Koda

| Decision | Owner | When | Notes |
| --- | --- | --- | --- |
| What to do with a broad Chamber letter that genuinely concerns several Matters | Department head + lawyers | When the review queue surfaces one | `Submission` has one primary Matter. Four archive files currently sit in `EXACT_BINARY_MULTI_MATTER` for this reason and are not cloned across Matters. The options — a primary Matter plus a related-Matter relationship, an umbrella Matter, or something else — are a product decision, not a migration one. |
| The default `SubmissionKind` for the archive corpus | Department head | Before the reconstructed submissions are quoted | Every reconstructed record is `FORMAL_OPINION` unless the file's own title says `ühispöördumine`/`ühiskiri` (→ joint letter) or `täiendav arvamus` (→ supplementary). Nothing is inferred from the recipient, so an opinion sent to a Riigikogu committee is *not* automatically a parliamentary submission. If the Chamber wants that, it is a rule somebody has to state. |
| Whether an unmatched archive file may ever become an ARCHIVE Matter | Department head | When the 192 unmatched files are worked through | The queue offers reject / duplicate / not-an-opinion / defer, and deliberately does not offer "create a Matter from this file". Creating one would mean inventing an owner, a stage and a received date the source never had. |
| Whether a signed container or the PDF inside it is the sent evidence | Department head + whoever owns records management | If a container ever reaches this archive | The current corpus is 767 PDFs and no containers, so no rule was invented. ASiC-E bytes are already storable and are deliberately never unpacked. |
| How cross-era ministry grouping should work in analytics | Reporting owner | When somebody asks for a ministry trend across a rename | `SubmissionRecipient` answers "who did Koda formally write to" using the historical organisation. A modern ministry-family view is a separate, explicitly-named dimension and must not be built by rewriting the historical recipient. |
| The earliest year historical submission statistics may be published for | Department head + reporting owner | Before the first published trend | Measured coverage begins in 2020 and is partial in every year. `SUBMISSIONS_SENT_BY_PERIOD` now declares 2020 as its earliest reliable period; whether a partial year is publishable at all is a business call. |
| Whether the reconstructed submissions should be reviewed before the archive's remaining two thirds are worked | Department head | Next | 244 of 767 files clear the automatic threshold. The other 523 carry their evidence in the queue and are not lost — but they are also not counted, and a reader of the statistics needs to know which of those two facts they are looking at. |
