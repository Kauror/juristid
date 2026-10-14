# 0162 — A record derived from a restricted `Kaasamine` round is never less restricted than it

**Status:** accepted for review — QA remediation of 2026-10-14 (QA-AUTHZ-01 P0, QA-AUTHZ-P1); applies the rule AGENTS.md already states («Restricted child records may be more restrictive than their Matter, never less restrictive») to the records a round produces.
**Date:** 2026-10-14

**No migrations**, no data migration, **no existing row is rewritten**, no
search-index rebuild.

## Context

docs/adr/0137, 0138, 0139 and 0146 each decided that a record created *because
of* a restricted record is never less restricted than it: a file captured for a
record, the completion entry of a step, the replacement of a step, a follow-up
check of an opinion. The records a `Kaasamine` produces were not named:

* `Meile saadetud tagasiside` tied to the round (`MatterExternalPosition.engagement`,
  written by `+ Kaasamine · Lisa tagasiside`, docs/adr/0142, and by every path into
  `workspace.add_matter_external_position(engagement=…)`);
* the files captured for that reply;
* the `Ülevaade` the round's `Ülevaate link` records (docs/adr/0142 §C).

`record_external_position` created the reply with no override, so the capture
(0137) created its file NORMAL too. Under a NORMAL Matter a READER or an
ADMINISTRATOR read the reply on the Teema page, Teema käik, Dokumendid and
`/otsing/`, and the shared-gate visitor downloaded its file — answers to a
consultation the same readers could not know existed (QA-AUTHZ-01). Latent in
production (all personas are lawyers, no panel restricts a round), but a
blocker for READER / ADMINISTRATOR accounts.

## Decision

### §1 At creation — copied, like 0137/0138

`record_external_position` creates the reply with
`restriction_inherited_from(engagement)` (`app/core/derived_visibility.py`):
the round's own `visibility_override` when it is `RESTRICTED`, empty otherwise.
The capture then restricts the reply's files through 0137 unchanged.
`correct_external_position` that re-points a reply at a restricted round
restricts it the same way; it never relaxes one.

### §2 The round's `Ülevaate link`

`_overview_for_engagement_link` receives the round. For a restricted round it
does not consume the Matter's unrestricted plan and records the overview with
the round's restriction. An overview already published at that address is a
record readers already see and is returned untouched. (Category B of 0138 §3 is
about a *step* finishing; the round's overview is the round's own field.)

### §3 At read time — live, like 0146 §10

Copying covers new rows only. Rows written before §1 keep an empty override and
are not rewritten (0137 §3, 0138 §5), so the round is also read **live**:

| Reader | Rule added |
| --- | --- |
| `MatterExternalPosition.visible_to` and `visibility_rule` (Teema käik, activity, overview, change events) | the child rule on `engagement__visibility_override` when a round is set |
| `Document.visible_to` and `visibility_rule`; `DocumentVersion.visibility_rule` (`EVIDENCE_VERSION_ADDED`) | not linked (`DocumentLink`) to a reply whose round is restricted |
| `DocumentLink.visible_to` | both of the above for its two ends |
| search projection (`visible_documents`) | the same two rules for `EXTERNAL_POSITION` and `DOCUMENT`/`DOCUMENT_FRAGMENT` rows |

Every route that serves a file (`download`, `open`, `thumbnail`,
`document_detail`) authorizes through `Document.visible_to`, so the gate visitor
and a READER get 404. The live half only narrows: a scope that sees all
restricted work gets no join, and the Matter's participants keep the
participation half exactly as `child_visibility_q` gives it. Relaxing the round
later shows a legacy reply again; a reply restricted by §1 stays restricted
until somebody relaxes it (fail closed, as 0137 §2).

The document half reads the link, never a filename, date or author. Every
`DocumentLink` is written by `capture_accepted_evidence` for a file created for
that record, so the file *is* the reply's evidence; 0137 §2's pinned case («a
normal file already on the Matter linked to a restricted record may be listed»)
is not reached by any door for a reply and is not changed for any other kind.

### §4 What the live read cannot cover

An overview carries no relation to the round, so a legacy overview recorded from
a restricted round's link cannot be derived at read time. A round created by
`Alusta kaasamist` is never restricted in the same save (no panel offers it), so
the expected count is zero; the read-only inventory is in the remediation
report. Files captured for the round *itself* before 0137 remain 0137 §3's
owner decision.

## Consequences

* `tests/test_derived_records_of_restricted_rounds.py` — new and legacy-shaped
  rows, READER, ADMINISTRATOR, gate visitor, SPECIALIST, DEPARTMENT_HEAD, owner,
  collaborating READER, break-glass and its expiry, counts, search, the
  overview, re-pointing.
* `tests/qa_audit/test_qa_authz_oracles.py` — two hidden worlds (a reply under a
  restricted round on a visible Matter, new and legacy-shaped) through the
  existence oracles for READER, collaborating READER and the gate visitor.
