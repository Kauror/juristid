# 0060 — A Matter's opinions are documents, and the per-Matter Arvamused page is retired

**Status:** accepted
**Date:** 2026-09-05

## Context

One Matter showed the Chamber's opinion in four places.

The facts rail had `Koja arvamus` with the file in it. `Dokumendid` had the same
file in the file table, marked `★ Lõplik`. A separate per-Matter Arvamused page
at `/teemad/<pk>/seisukoht/` showed the file a third time, inside a card that
also printed the submission's checksum, its byte size, its version number and —
for the 312 opinions reconstructed from the historical register — the importer's
match reasoning as prose under every row. And the global Arvamused workspace at
`/arvamused/` listed it a fourth time, which is the one that is not a
duplication: that surface answers *find an opinion across our work*, which is a
different question from *show this teema's material*.

Three of the four are the same answer to the same question, and the third was
the worst of them:

* it repeated the rail's heading in the main column, so a reader who arrived
  from `Koja arvamus →` saw the words they had just clicked;
* it put `Võta tagasi` — an act that retracts a formal submission — as the most
  prominent control under every sent opinion, on a page people opened to read;
* it printed `Tõend · arvamus.pdf · v1 · 656,0 kB` and
  `Täpne saadetud fail · SHA-256 8f2a…` beside a file whose version and size the
  file table three metres away already had columns for;
* it printed `Taastatud arvamuste arhiivist · teema tuvastus mitu sõltumatut
  täpset signaali · kuupäeva alus registri välja kuupäev · saaja alus registri
  kellele` under restored opinions — four facts that belong to reconciliation
  and to nobody reading a Matter.

`★ Lõplik` was the same defect one word long. *Lõplik* names an internal
lifecycle property of a piece of evidence. It does not tell a lawyer what the
file is.

## Decision

**An opinion is a document, and `Dokumendid` is where a Matter's opinions live.**

The per-Matter Arvamused page is retired. `matters:matter_position` stays as a
compatibility route and redirects to that Matter's `Dokumendid` filtered to
`Arvamus`, after checking authorization, so an unauthorized caller still gets
404 rather than a redirect confirming the Matter exists.

**The user-facing label is `Arvamus`.** `★ Lõplik` and its tinted row are gone.
The badge is text rather than a colour, so it reads the same for somebody who
cannot see the tint.

**`DocumentRole.KODA_SUBMISSION_FINAL` is unchanged.** The stored value, every
stored row and the enum's own label all stay where they are; `Arvamus` is a
presentation mapping applied on `Dokumendid` — the badge, the Roll column, the
role filter and the upload panel's select. No schema migration, no data
migration, no backfill.

**What counts as an opinion is a union, and it has one implementation.**
`app/submissions/opinions.py` answers it for the rail, the badge, the filter and
the per-row metadata:

* a `Document` whose role is `KODA_SUBMISSION_FINAL`; **or**
* a `Document` one of whose versions is the `final_version` of a **SENT**
  `Submission` on this Matter,

deduplicated by document, both sides scoped by `visible_to`. The role alone was
never the answer: `select_final_evidence` and `attach_final_evidence` handed an
existing document bind the evidence and deliberately leave the classification
alone, so a Matter could show `Koja arvamused 1 · Saadetud` in one column and
`Arvamust ei ole lisatud` in the rail beside it (UX-005). SENT and not merely
bound, because badging a draft's evidence `Arvamus` would be the same defect
pointing the other way.

**`Submission` remains canonical for the send.** Recipient, sent date and its
precision, kind, channel, reference, joint submitters, exact final evidence,
sent/withdrawn state, reporting and audit are all exactly where they were. The
database still refuses a SENT submission without a timestamp and its exact
evidence. What changed is which page a lawyer reads them on.

**Several opinions per Matter is the normal case.** An initial opinion, a
supplementary letter and a later joint submission are three files and three
rows. There is no `Matter.final_opinion` and there will not be one — a
single-valued shortcut could only ever name one of the three.

**The right rail is read-only.** Filenames linking to the exact bytes, and
nothing else: no `+ Lisa arvamus` (the file list has the upload panel), no
`Arvamused →` (the page it pointed at is retired), no version, size, checksum or
badge. Its empty state is one quiet sentence and no call to action, because most
of the register is legitimately in that state.

**Management is secondary and lives on `Dokumendid`.** The send's details and
`Võta tagasi` are behind the row's `⋯`; drafts, `+ Uus arvamus` and
`+ Registreeri saatmine` are in a collapsed `Arvamused` block under the table,
which opens by itself when a draft is waiting. `Võta tagasi` is still a POST,
still behind the business-write boundary, still through `withdraw_submission`,
and still writes its `ChangeEvent`.

**`register_sent_opinion` composes rather than reimplements.** Recording that a
file already on the Matter went out used to mean creating an empty draft,
finding it again, binding it to that file and then sending it. It is one form
and one transaction now, and every rule it touches is still decided in
`create_submission`, `select_final_evidence` and `mark_submission_sent`.
Uploading a file as `Arvamus` still asserts only that Koda holds it; a person
still has to say it was sent.

**Linked archive letters keep a home.** `Seotud arhiivikirjad` moves to a
collapsed section on `Dokumendid` — title, date, recipient, linking through the
existing protected archive route, with no match provenance. `may_read_archive`
decides it exactly as before: a reader without the corpus gets no rows, no count
and no hint that any exist.

**The global Arvamused workspace is untouched.** ADR 0047 stands. `/arvamused/`
keeps its Saadetud/Arhiiv strip, its filters, its pager, its reporting semantics
and its archive gate. Its rows now link to the Matter's `Dokumendid` rather than
to a retired address that would only redirect there.

**Technical provenance leaves the reading surfaces and stays in the database.**
`OpinionSubmissionImport`, the match class, the sent-date and recipient bases,
`DocumentVersion.sha256` and its check constraint are all untouched and all
still reachable from the document's own evidence page, the integrity tooling and
the admin. They are simply not printed under every opinion a lawyer reads.

**No search rebuild.** `INDEX_VERSION` and `ARCHIVE_INDEX_VERSION` are unchanged
and the projection recipe is unchanged. Only the URL a Submission result points
at moved, and that is computed at render time from `result.matter`, not stored.

## Consequences

A Submission whose final evidence a reader may not open no longer appears on
that Matter at all. It used to render as a card naming the submission with the
file suppressed. An opinion is a document row now, so a document outside the
reader's scope produces no row — no name, no badge, no anchor and no placeholder
admitting one was hidden. That is a reduction in what such a reader is told, in
the safe direction, and the fact is not lost from the product: the global
Arvamused workspace still lists the Submission under its own visibility rules.

A search result for a Submission lands on the Matter's opinion-filtered
`Dokumendid` rather than on an anchor for that submission. Anchoring on the
opinion file itself would need a query and an authorization decision per result
row — a Submission a reader may find can point at a document restricted below it
— and the filtered list answers the same question without either.

`?roll=KODA_SUBMISSION_FINAL` on `Dokumendid` is read as the union. It returns a
strict superset of what it used to, and the menu now agrees with the URL instead
of showing «Roll — kõik» over an active filter.

`matters:update_position` still has no native UI, exactly as ADR 0030 left it,
and stays inside the business-write boundary. Its redirect target moved with the
page it used to return to.

## Alternatives considered

**Change the `DocumentRole` label globally to `Arvamus`.** Django writes an
`AlterField` migration for a changed `choices`, and this change is meant to have
none. Presentation mapping gets the same words on the screen with the stored
value, the stored label and the migration history all untouched.

**Add `?roll=arvamus` as a second option beside the stored role.** Two entries
on one menu that look like synonyms and are not: the role cannot express «…or
the exact file of a sent opinion», which is half of what an opinion is.

**Keep the page and merely quieten it.** The page's problem was not its styling.
It was a third destination for a file that already had two, and every round of
tidying it would have had to re-decide which of the four surfaces a given fact
belongs on.

**Rebuild the old page as a section at the bottom of `Dokumendid`.** That is the
same duplication with a shorter URL: the sent opinions would be rows in the
table *and* cards underneath it.
