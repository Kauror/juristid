# 0137 — A file is never less restricted than the record it is evidence for

**Status:** accepted
**Date:** 2026-10-04

Extends docs/adr/0129 §4 from an opinion's working documents to every
record-attached evidence capture. **No migrations**, no new model or field, and
**no existing row is rewritten**.

1. **Every `Document` created as evidence for a record carries that record's
   own restriction.** `capture_accepted_evidence` — the one place every
   record-attached capture goes through, `capture_supporting_evidence`
   included — creates each file with `evidence_visibility_override(record)`:
   the record's `visibility_override` when it is `RESTRICTED`, empty otherwise.
2. **One rule, decided in the capture.** The `visibility_override` parameter is
   removed from both capture functions; no caller passes it, so none can forget
   it and none can create a file looser than its record.
3. **Existing documents keep their visibility.** Rewriting them is the owner's
   decision, not a side effect of this change.

## Context

`DocumentLink.visible_to` is the conjunction of the document's and the record's
visibility, so a link to a restricted record is hidden from a reader who may not
see the record (0129 §4). But the `Document` itself is authorized by its **own**
`visibility_override` and its Matter — that is what Dokumendid lists, what its
tab counts, and what the `DOCUMENT` and fragment search rows join.

Only two doors created the file with the record's override: an opinion's
`Töödokumendid` (0129 §4) and `+ Lisa fail` on an open round (PR #388). Every
other panel on the Teema workspace called `capture_supporting_evidence` without
it — `PRAEGUNE TEGEVUS` (the `Entry` it writes), `+ Kaasamine`,
`Lõpeta kaasamine`, `+ Oluline tähtaeg`, `+ Jõustumine`, `+ Töövõit`,
`+ Väline seisukoht` and its `+ Lisa fail`, `+ Menetluse areng` and its
`+ Lisa fail`. A file put on a record restricted below its normal Matter was
therefore created at the Matter's `NORMAL` visibility: listed by its filename,
counted, and found in search by readers who could not see the record it belonged
to. `tests/test_development_evidence.py` had pinned the gap as «a real decision
… that belongs to the evidence architecture».

No panel offers a restriction today; records are restricted by importers (the
archive apply restricts opinions), the admin and the shell. The doors that add a
file to an **existing** record — `Lõpeta kaasamine` and the three `+ Lisa fail`s
— are where the leak was reachable now. The creation doors are covered by the
same rule for the day a form, or an importer going through the workspace, writes
a restricted record.

## Decision

### §1 The rule

`app.documents.services.evidence_visibility_override(record)` returns the
record's `visibility_override` unless it is empty or `NORMAL`, in which case it
returns empty — so a file on an unrestricted record is created exactly as
before, inheriting the Matter. A record without the attribute adds nothing.
`capture_accepted_evidence` applies it to every file it creates; it reads the
record instance the door hands it, which every door re-reads under the Matter's
lock before capturing.

A restricted Matter needs nothing from this: the document inherits it already.

### §2 Copied at creation, not joined

The override is **copied** onto the new document's own column, the way
`Document.visible_to` has always worked, rather than joined live from the
record. Lifting a record's restriction later therefore leaves its files
restricted until somebody lifts theirs — the failure is closed, not open. Making
document visibility a live function of every linked record is a larger change
to the evidence architecture (a document may support several records) and is
not made here.

`link_document_to_record` is unchanged: linking a file **already on the Matter**
to a restricted record does not restrict it. That is the pinned «normal file on
a restricted record» case 0075 and 0129 keep — the file may be listed, the
relation may not — and no current door reaches it.

### §3 No backfill

Documents created before this change on a record that was restricted keep their
empty override. Whether to restrict them is the owner's decision. They can be
listed read-only with:

```sql
BEGIN READ ONLY;
SELECT kind, doc.id AS document_id, record_id, doc.matter_id,
       doc.created_at::date AS created, m.visibility AS matter_visibility,
       doc.removed_at IS NOT NULL AS doc_removed
FROM (
  SELECT l.document_id,
         CASE WHEN l.entry_id IS NOT NULL THEN 'Entry'
              WHEN l.engagement_id IS NOT NULL THEN 'MatterEngagement'
              WHEN l.important_date_id IS NOT NULL THEN 'MatterImportantDate'
              WHEN l.effective_date_id IS NOT NULL THEN 'MatterEffectiveDate'
              WHEN l.work_victory_id IS NOT NULL THEN 'MatterWorkVictory'
              WHEN l.external_position_id IS NOT NULL THEN 'MatterExternalPosition'
              WHEN l.procedural_development_id IS NOT NULL THEN 'MatterProceduralDevelopment'
              WHEN l.submission_id IS NOT NULL THEN 'Submission' END AS kind,
         COALESCE(l.entry_id, l.engagement_id, l.important_date_id, l.effective_date_id,
                  l.work_victory_id, l.external_position_id, l.procedural_development_id,
                  l.submission_id)::text AS record_id,
         COALESCE(e.visibility_override, g.visibility_override, i.visibility_override,
                  f.visibility_override, w.visibility_override, p.visibility_override,
                  d.visibility_override, s.visibility_override) AS record_override
  FROM documents_documentlink l
  LEFT JOIN matters_entry e ON e.id = l.entry_id
  LEFT JOIN matters_matterengagement g ON g.id = l.engagement_id
  LEFT JOIN intelligence_matterimportantdate i ON i.id = l.important_date_id
  LEFT JOIN intelligence_mattereffectivedate f ON f.id = l.effective_date_id
  LEFT JOIN intelligence_matterworkvictory w ON w.id = l.work_victory_id
  LEFT JOIN matters_matterexternalposition p ON p.id = l.external_position_id
  LEFT JOIN matters_matterproceduraldevelopment d ON d.id = l.procedural_development_id
  LEFT JOIN submissions_submission s ON s.id = l.submission_id
) links
JOIN documents_document doc ON doc.id = links.document_id
JOIN matters_matter m ON m.id = doc.matter_id
WHERE record_override = 'RESTRICTED' AND doc.visibility_override IN ('', 'NORMAL')
ORDER BY kind, doc.created_at;
ROLLBACK;
```

Rows on a `RESTRICTED` Matter are listed too, but are already invisible to
anybody who may not see the Matter; the exposure is the rows on a `NORMAL` one.

## Consequences

* A file on a restricted record is never listed on Dokumendid, counted, opened
  or found in search by somebody who may not see the record; the relation was
  already hidden (0129 §4), and now the file is too.
* `tests/test_record_evidence_inherits_restriction.py` asserts it per record
  kind and per door, with the unrestricted half beside it;
  `tests/test_development_evidence.py`'s pin of the gap is replaced by the new
  behaviour and the no-backfill half.
* The two doors that passed the override by hand (`add_engagement_evidence`,
  the opinion's working documents) now rely on the rule; their tests still hold.

## Not decided here

* Restricting existing documents (§3).
* A live, joined document visibility derived from linked records (§2).
* The visibility of the record a door *creates* — e.g. the `Entry` written by
  completing a restricted `NextAction` is created unrestricted. That is the
  record's own visibility, not its file's.
* A restriction control on any Teema panel.
