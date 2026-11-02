# ADR 0199 — The «Majanduspoliitika ülevaade» report is composed from recorded events

- Status: Proposed (owner's report-builder brief of 2026-11-02, source rules «Decision B»; **no migrations**, no stored state)
- Related, unchanged: 0011 (a `VÄLJA` date is not a Submission), 0079 (dates at the precision they are known to), 0092 §3 (a withdrawn send still went out), 0102 (a removed record is absent everywhere), 0142 §C (`Ülevaade` vs `Uudis`), 0171 (one delegated browser layer), 0174 (a no-comments answer is a send, not an opinion), 0181 (one internal read policy)

## Context

Every month the department writes a «Majanduspoliitika ülevaade»: per topic, what
Koda sent and to whom, which meetings and hearings it took part in, what it said
in public. Today it is assembled by hand in Word from the register, OneNote and
memory. Juristid already holds most of those facts as canonical records with their
own business dates, but nothing turns them into the table the report is.

The owner asked for a compact composition page — not a web Office editor — that
proposes the month's events, lets a lawyer choose, order and annotate them, and
downloads a real Word document. The owner fixed which records count (Decision B)
and that the draft must not be stored («the smallest safe transient design»).

## Decision

### 1. The source map

`app/reporting/report_builder/sources.py` is the only place an event becomes
eligible. Read as the requesting user, from the reader's visible **real** Matters
(`Matter.objects.visible_to(user).real_data()` — TEST records «ei kuulu päris
aruandlusse»), on Europe/Tallinn days, both ends of the period inclusive:

| Event | Record | Rule |
|---|---|---|
| A send | `Submission.objects.visible_to(user).historically_sent()` | SENT, WITHDRAWN or SUPERSEDED with `sent_at`; never a DRAFT. A later state is said beside the kind («hiljem tagasi võetud»). `NO_OBJECTION_RESPONSE` keeps its own label «Vastus: märkusi ja ettepanekuid ei ole» and is never called an opinion. Date only, whatever `sent_at_precision` (a DATE send never shows a time). |
| A Chamber activity | `Entry.objects.visible_to(user)` (live) | MEETING, CALL, HEARING, WORKING_GROUP, JOINT_COORDINATION with `occurred_at` ≤ now. A future entry is a plan. |
| A public statement | `Entry` PUBLIC_STATEMENT; `MatterWebsiteOverview` PUBLISHED | The overview only when its effective kind is **Ülevaade** (Koda's own `Hetkel käsil` page) **and** `published_on` is recorded. An **Uudis** may be anybody's article about Koda; an undated publication has no date to report. |
| A historical Excel `VÄLJA` date | `CurrentRegisterState`, `opinion_sent_state = DATE` | Labelled «Ajalooline Exceli VÄLJA-kuupäev» with «leht …, rida …» (in the Word file too). Suppressed when a visible sent Submission on that Matter falls on the same Tallinn day, or when the 2026 pilot's placeholder Submission (`ExcelPilotImport.submission`) was imported from the same sheet and row. Otherwise kept — and, when the Matter has a visible send on another day, flagged in the preview «Võib korrata arvamust «…» (päev)». |

**Excluded, with reasons.** `NOTE` (narrative about the file) and `OTHER` («Muu»
is too broad to be read as substantive work without a person saying so; the
reader adds the topic by hand); every audit event, creation or modification time;
a draft; the absence of an opinion and «ei saatnud» / unreadable `VÄLJA` marks;
`MatterExternalPosition` and feedback replies (another organisation's position);
planned and cancelled overviews; removed records.

**Identity** is `(type, uuid)` — `S`, `E`, `W`, `V` — and the Matter's uuid; never
a title. A distinct event is never suppressed because the topic has some other
send: only a same-day send or the pilot's own row counts as the same send.

### 2. KELLELE and SELGITUS

**KELLELE** is the Submission's `ADDRESSEE` rows only (`addressee_prefetch`, in
organisation-name order), joined with commas. A «teadmiseks» copy is not a
recipient. For a Chamber activity (meeting, call, hearing, working group,
statement) KELLELE is the Entry's organisation — the one it was held with — as
the department's reports have always written it (owner, 2026-11-02, amending
the first decision B); an activity with no organisation has none. A `VÄLJA` event shows `Matter.addressee_organisation` only
when the same register row's `KELLELE` names exactly one organisation and it is
that one (`addressee_cardinality = SINGLE`, names equal ignoring case) — then the
canonical field and the row agree on who the opinion went to. Anything else is
blank; nothing is guessed.

**SELGITUS** is prefilled from human-authored text only: a Submission's
`summary`; an Entry's `body` as plain text (`richtext.plain_text`); an overview's title and address; nothing for a `VÄLJA` date.
At most 2000 characters. Nothing is generated, and «Kojal ei olnud kommentaare ega
muudatusettepanekuid.» is never written automatically. Editing it changes the
report only: no Submission, Entry or Matter is written (tested: `ChangeEvent`
count and every `updated_at` unchanged).

### 3. The transient draft

The draft is the composer's form. Hidden inputs carry the applied period, the
ordered Matter ids, the included and hand-added flags, each block's event refs,
the included events, the per-block source fingerprint and, beside every
explanation, the digest of the source text it was rendered from; text areas carry
the explanations. All of it is posted with every button. Nothing is stored on the
server, in the session or in browser storage, and no business text travels in a
URL (the «Lisa teema» search GET carries its term only). Leaving the page with a
composed or changed draft asks first (`data-report-dirty`, app.js); this is the
only state there is.

The server trusts the form for **choices** and never for **facts**: titles,
dates, kinds, recipients and source texts are re-read from canonical records on
every request. An explanation equal to its source digest means «use the source»,
so an unedited explanation follows its source; anything else is the reader's own
text and is kept exactly. Validation never discards a choice: a malformed or
duplicate id is dropped with a notice, an over-long text is kept and blocks only
the export, an impossible period keeps the previous one and says why. Bounds: 300
topics, 50 events per topic, 2000 characters per explanation, a period of at most
366 days.

The initial order is earliest event date, then title, then id. Order is the
posted order of the `teema` inputs, so the «↑ Üles» / «↓ Alla» buttons (keyboard
and no-script) and dragging a block by its handle (ux.js) produce the same draft.

Changing the period does not silently rebuild: «Uuenda ettepanekuid» shows what
would be added, which proposals would leave because they have no event in the new
period, and how many typed explanations belong to events outside it. «Rakenda uus
periood» then keeps hand-added topics, the order and every explanation whose
event is still in the period, and appends new proposals in the deterministic
order; «Loobu» restores the period boxes. An export waits while the boxes and the
applied period differ.

### 4. One representation for preview and DOCX

`draft.compose` resolves the draft into a `ComposedReport`, and
`ComposedReport.table_rows()` is what both the preview table and the DOCX writer
print: one row per included event, each carrying its topic, in the reader's
topic order, or one row for a topic with no included event — its title and the
reader's own explanation for it (`DraftBlock.text`, form field
`selgitus-<teema>-teema`, at most 2000 characters, report text only: no Entry,
Submission, Matter or `ChangeEvent` is written), with no date or recipient made
up. The field is offered whenever no event of the topic is in the report — a
hand-added topic with none in the period, or one whose events the reader all
left out — and is carried unseen while an event is in, so it is never a second
explanation beside the events' own. The day is «12.08» (an exact day in a
one-year period; a month or a year keeps its own label). Under it, in small
italic type in both the preview and the document (`TableRow.date_notes`): what
the event is («Ametlik arvamus», «Vastus: märkusi ja ettepanekuid ei ole»,
«Kohtumine» …), for a `VÄLJA` date «Ajalooline Exceli VÄLJA-kuupäev» with its
sheet and row, and where the preview flags a possible duplicate, «Võimalik
kordus: arvamus 2.10.2026». Owner's review of PR #585: a bare imported date in
the board's file would read as a confirmed send.

The DOCX (`report_builder/docx.py`, python-docx's own default document, nothing
fetched) is the department's report as it has been sent to the board (owner,
2026-11-02): A4 portrait, 2.5 cm margins, Times New Roman 12; the fixed opening
of the action plan **of the report's year** (`report_builder/plan_text.py`,
`PLANS` — two bold lines, «Tegevused ja eesmärgid» and the two-column plan
table; a newly approved year is one more `Plan` there). Only 2026 is
registered. A period in a year without a plan, or across New Year, says so on
the page and is refused for export (`plan_text.problem_for`), the draft kept —
no other year's plan is ever put in front of a period; «Täitmine: 01.10.2026 – 31.10.2026» in bold; one table
«Teema | Kuupäev | Kellele | Selgitus», nothing merged, the header row repeated
(`w:tblHeader`), no `cantSplit` on any row; then the day it was made and the
signers in bold. The signers are a field of the draft («Allkirjastajad», default
«Marko Udras, Mait Palts»), one line, at most 200 characters. Document language
et-EE.
The response is `private, no-store` with an RFC 6266 filename
(`Majanduspoliitika_ulevaade_2026-10.docx`).

### 5. Revalidation at preview and export

Every render recomputes each block's fingerprint (title and every shown event
fact) and compares it with the posted one. A preview names the blocks whose
sources changed. An export re-authorizes every topic and event as the requesting
user and refuses with 409 — returning the composer with the reasons and the
reader's draft intact, now carrying the new fingerprints — when any block changed,
any topic became unreadable (counted, never named), the period is pending, an
explanation is too long, the period has no action plan, or nothing is selected.

**The download is the report last previewed** (owner's review of PR #585). Every
render carries `eelvaade_tunnus`, the digest of exactly what its preview shows
(`draft.output_digest`: period, plan year, every row with its text and notes,
signers, the exported blocks' fingerprints) signed with Django's signer, salted
with the reader's id. The export recomputes the digest from what is posted and,
when it differs — a box ticked, a text typed, a topic moved or the signers
changed after the preview, with or without script, or a token from another
reader — refuses with 409 and the composer brought up to date, saying
«Ülevaadet on pärast eelvaadet muudetud…». The next press downloads exactly
that. The first page is a preview like any other, so an untouched report
downloads at once. Nothing is stored on the server. Every route is `@login_required`
and answers 404 to anybody `may_read_business_content` refuses (an administrator
without a business role); a persona-less shared-gate session is sent to choose a
persona.

## Consequences

* The monthly overview is assembled from the register's own records, with
  provenance on historical Excel dates and a visible flag where a duplicate cannot
  be ruled out.
* A draft is lost if the tab is closed after confirming the leave prompt. That is
  the accepted cost of storing nothing.
* `OTHER` entries and `Uudis` publications never appear on their own; the reader
  adds such a topic by hand. If the department wants either proposed, that is a
  change to `sources.py` and this record.
* No migration, no model, no new dependency.
