# ADR 0060 — Deterministic document-assisted intake: suggestions, not facts

- Status: accepted
- Date: 2026-09-05
- Stage: pre-QA (shared-gate development phase)
- Related: ADR 0014 (the derivatives this reads and the malware and extraction
  gates it sits behind), ADR 0025 (senders are a set), ADR 0026 (reading
  written text deterministically: closed word lists, written dates, refusal
  over guessing), ADR 0029 (organisation identity is exact name or reviewed
  alias, never similarity), ADR 0032 (what `Uus teema` and `Saabunud` do and
  do not create), ADR 0037 (the business-write HTTP boundary), ADR 0038
  (child visibility in every read)

## Context

Incoming work arrives as a covering letter, a draft, an explanatory
memorandum, annexes and the message they came in. `Saabunud` captures every
file as immutable evidence and opens the Matter with a title made
mechanically from the first filename; the extraction worker then reads the
files into rebuildable text and headers (ADR 0014). Everything the register
needs next — who sent this, by when Koda must answer, what kind of proceeding
it is, which Valdkond it belongs to, whom to write back to, under which
reference — is on the first page of the letter and in the message's own
headers, and a lawyer retyped it.

The master specification names the proposal list for this surface — title,
sender, received date, attachments, official reference, response deadline —
and the rule that governs it: *proposals require user confirmation*
(§7.3, §15.7). It also files AI extraction of the same facts under *later*
(§21). This record is about the deterministic half: rules, vocabularies and
the department's own catalogues, reading text the extraction system already
produced.

Three things made the shape of this non-negotiable before a line was written.

**The malware gate is deliberate.** In a real-data environment an unscanned
file is not opened by anything (ADR 0014, `is_eligible_for_extraction`). A
"just parse the PDF so the form can be pre-filled" path would be a second,
unguarded parser in the HTTP request. The analyser therefore reads only what
the worker already published, and where the worker has published nothing it
says so.

**A guess that looks like a fact is worse than no guess.** `Arvamuse tähtaeg`
is work on every deadline surface; a wrong one makes a file overdue by
Tuesday. A sender is provenance; a wrong one files a decade of correspondence
against the wrong ministry. The product already refuses the first date, the
first sender and the first title when the source is not explicit (ADR 0026),
and the same refusal governs here.

**There is no canonical field for half of what is useful.** The sender's name
and address, an EIS reference, a ministry's own document number — each is
worth showing, and none has a correct home on `Matter`: one Matter receives
mail from several people and carries several references, and the Matter's own
`reference_year`/`reference_number` is Juristid's human reference, not the
ministry's. Adding a single-valued column for either would be the wrong
cardinality written down in a migration.

## Decision

### The analyser reads derivatives and writes nothing

`app.matters.intake_suggestions` reads, for each document a viewer may see,
the current version's ACTIVE `EXTRACTED_TEXT` (or `OCR_TEXT`) fragments and
its `EMAIL_METADATA` record, through `Document.objects.visible_to(viewer)` and
nothing else. It never opens evidence bytes, never touches the parser
registry, never changes an extraction or scan state, and never queries
another Matter. Given the same text, catalogue and rules it returns the same
result. There is no model, no embedding, no network call and no dependency
added; a test asserts the package's import graph says so.

Two queries list the documents and their live derivatives with fragments
prefetched; two load the organisation catalogue; one loads the offered
Valdkonnad. The count does not grow with the number of fragments, and a test
holds it there. Per document, the first 400 000 characters are read and a
document that hits the ceiling is marked as read in part, on the page.

### Every proposal is a candidate with confidence, rule and provenance

A `Candidate` carries the field it is for, the canonical value the form
control takes, a display form, one of three named confidences, the rule that
produced it, the document and version it came from, the extraction system's
own locator (`lk 1`, `kirja päis`), whether the characters were native or
OCR, and a short excerpt. The panel answers *miks sa seda pakud* and *kust
see tuli* for every line; OCR-read text is labelled OCR in words, never a
tint; excerpts are text and are escaped as text (ADR 0014).

Confidence is three words with a contract, not a score with a threshold:

| | may do |
|---|---|
| `HIGH` | pre-fill an *empty* unsaved form control |
| `MEDIUM` | be offered with «Kasuta»; the person chooses |
| `LOW` | stay out of the primary form; appear under «Muud leiud» if useful |

A field with two `HIGH` answers that disagree is a **conflict**: both are
shown with their evidence and nothing pre-fills. Two dates each introduced by
*palume esitada hiljemalt*, two organisations each named as the sender, two
documents whose formal headings differ — the analyser does not pick.

### What is read, and how

- **Pealkiri.** A heading-shaped line near the start of a document that
  carries legislative language (*… seaduse muutmise seaduse eelnõu*, *…
  määruse eelnõu*, *väljatöötamiskavatsus*, *arengukava*, *strateegia*) is a
  formal heading. A message subject with its mail prefixes stripped is a
  weaker source. *Seletuskiri* alone is not a title; *Seletuskiri … juurde*
  yields what it wraps. A closed list of purpose words (*kooskõlastamiseks*,
  *arvamuse avaldamiseks*) is stripped from the end. A formal heading
  outranks a subject line; two different formal headings conflict.
- **Kellelt.** The department's `Organisation` names and recorded
  `OrganisationAlias` forms, matched in full with an Estonian case ending
  allowed (*Kliimaministeeriumile* is Kliimaministeerium; *Kliimaamet* is
  not), never a resemblance (ADR 0029). Evidence is graded by where the name
  stands: the message's own `From:` display name and *Saatja:* lines are
  strong; the letterhead — the opening block up to the salutation — is strong
  when it names exactly one body; the signature block and an abbreviation
  are suggestions; a name in the body is a mention and is listed under «Muud
  leiud». Koda's own `CHAMBER` organisation is never proposed as a sender.
  Nothing is created and nothing is merged.
- **Arvamuse tähtaeg.** The register parser's Estonian date scanner is reused
  (ADR 0026: `18.09.2026`, `18. september 2026`, `18. septembril 2026`,
  `2026-09-18`); only exact days are considered, because the field is a day.
  The words around a date decide what it is: *hiljemalt*, *tähtaeg*,
  *kuupäevaks*, the translative *-ks* on the month, *palume esitada …*,
  *ootame … kuni* make a response deadline; *jõustub*, *allkirjastatud*,
  *koostatud*, *vastu võetud*, *toimus*, *Teie 04.09.2026 nr …*, *Meie …* and
  a dateline in the first 200 characters make a decoy. A deadline stated in
  an explanatory memorandum is only a suggestion; the covering letter is where
  it is stated. *Kolme nädala jooksul* is shown as a finding and never
  computed into a day.
- **Menetlusliik.** Weighted cue tables per current `Track` value. EU signals
  (*COM(2026) 412*, *Euroopa Komisjoni ettepanek*) make `EU_INITIATIVE`;
  transposition needs both the act of transposing and a directive; a
  development plan or strategy makes `STRATEGY`; national legislative
  language makes `DOMESTIC` only when no EU signal is present; Koda's own
  initiative is never strong from an incoming document; `OTHER` is never
  proposed. No signal, no suggestion.
- **Valdkonnad.** A declarative table keyed by stable `PolicyArea.key`, in
  `vocabulary.py`, of words and weights: a law's name counts five, a legal
  term three or four, a domain word two, a generic word one, and each is
  capped at three hits so a term on every page of a draft is one piece of
  evidence. Up to three areas are offered; a generic word alone never
  carries one. A rule keyed on an area the vocabulary no longer offers is a
  diagnostic, never a remap.
- **Findings without a field.** The message's `from_name`, `from_email` and
  `sent_at`; a labelled or signature-block contact in a letter, shown as
  «Kontakt dokumendis» and never as the sender; explicitly labelled document
  numbers (*Meie … nr*, *Teie … nr*, *dokumendi nr*) kept verbatim; an
  EIS-labelled reference and any `eelnoud.valitsus.ee` link; `COM(…)` and EU
  act numbers; a Riigikogu proceeding number; other links under «Muud leiud».
  The grammar of an EIS number is deliberately not asserted — the repository
  holds no verified example — so an EIS reference is the token after an EIS
  label, and a date-shaped token is refused.

Not read, deliberately: `received_date` (a document date, a signature date
and a message's `sent_at` are three different facts, and the message's is
shown as what it is); `brief_summary` (no deterministic reading of *what this
changes and whom it affects* is honest); `stage` (a document describes
several stages).

### The surface is the existing edit page, and saving is the existing save

`Kontrolli dokumendist leitud andmeid` is a GET-only route,
`matters:matter_edit_assisted`, behind the same `@business_write_required`
gate as the edit page, offered from `Muuda teemat` and from the Teema page's
⋯ menu whenever the Matter has visible material. It renders `Muuda teemat`
with a panel above the form. A `HIGH` candidate for an empty control appears
already filled and is marked *vormil eeltäidetud*; a `MEDIUM` one carries
«Kasuta», which writes the value into the real control and nothing else; a
value the record already holds is marked *juba valitud*. The person's own
title is never replaced — a content title pre-fills only where the stored
title is the mechanical filename fallback `intake.title_from_filename`
wrote, and is otherwise offered beside it.

The form posts to `matters:matter_edit` exactly as it does from the plain
page. What is stored is what the person submitted, through
`set_matter_title`, `set_organisations`, `set_matter_dates`, `change_track`
and `set_policy_areas`, and the audit trail says the person changed the
Matter. A refused save re-renders what was typed and does not re-run the
analysis over it. There is no second write path, no route that accepts a
suggestion server-side, and no audit event claiming a classification.

Where extraction is pending, processing, failed or not applicable, the panel
says which and offers «Kontrolli uuesti». On a real-data instance today,
where extraction is blocked until the scanner lands, the panel is inert and
says so — it does not fake a suggestion and does not unblock anything.

### No schema

Suggestions are a deterministic function of derived content; caching one in
a table would be a second copy of a disposable thing. Sender contacts and
external references are shown, with provenance, and not stored. The models a
later brief should decide, with the cardinality this round measured:

- *one Matter → many external references*: type (`EIS`, ministry document
  number, `COM`, Riigikogu, EU act), exact value, URL, source document and
  version, observed timestamp — the `ExternalReference` the specification
  sketches (§11.2), which also needs a search-projection decision (ADR 0038);
- *one Matter or incoming document → many contact people*: name, address,
  organisation, role, retention class — which is the open contact-person and
  retention decision the Secure Pilot Gate requires (§16.4, §28), not a
  column.

`Organisation.source_reference` and `DocumentVersion.source_identifier` are
not used for any of this: the first describes the organisation, the second
how a binary was acquired.

### Measurement is a read-only command

`manage.py evaluate_intake_suggestions --limit N` scores the analyser against
what people filed on already-extracted Matters — senders, deadline, track,
areas: suggested, strong, conflicting, top-1 and top-k agreement, none — as
aggregate counts, through the shared-gate viewer (NORMAL content only). It
writes nothing, prints no document text or address, and refuses a real-data
environment unless told `--real-data`.

## Alternatives considered

- **Parse the upload in the intake request and pre-fill the intake form.**
  Faster to demonstrate, and a second parser outside the malware gate. Also
  structurally wrong: at intake time no derivative exists, and the intake form
  deliberately carries no Menetlusliik (ADR 0032). Rejected.
- **A suggestion table.** Would let the panel load without recomputing.
  Recomputation is bounded and cheap; a stored suggestion is a stored guess
  with a visibility column and a rebuild story (ADR 0014). Rejected for v1.
- **A numeric confidence score.** Every threshold becomes a number nobody
  remembers the meaning of; ADR 0019/0055 already chose named signals over
  scores for the same reason. Three words with a stated contract were kept.
- **Fuzzy organisation matching, or reading the sender off an e-mail domain
  as a fact.** ADR 0029 forbids the first. The second is kept as a `MEDIUM`
  suggestion only where the domain's own label spells a recorded alias in
  full, because a domain is not a name.
- **An LLM.** Out of scope by the brief and by the specification's phasing;
  and the failure mode — a polished wrong title, a confident wrong deadline —
  is exactly the one this record is written to avoid.

## Consequences

- A lawyer opening a Matter filed through `Saabunud` sees, once the worker
  has run, the deadline, sender, title, track and areas the documents state,
  each with the sentence it came from, and saves them with one confirmation.
- Nothing the analyser does can change a record. Every test in
  `tests/test_assisted_intake.py` that renders the review asserts the Matter,
  the organisations, the taxonomy and the audit log are byte-for-byte what
  they were.
- The rule vocabulary is data in one file with a version stamp. Improving a
  rule is an edit a reviewer can read; measuring it is one command.
- The feature is inert on the real-data instance until the scanner ships,
  and says so rather than pretending.

## Reversibility

Complete and cheap. No migration, no new model, no new dependency, no new
audit type. Removing the package, the route, the panel partial, the CSS
block and the script binder leaves every record exactly as it was, because
none of them ever wrote one.
