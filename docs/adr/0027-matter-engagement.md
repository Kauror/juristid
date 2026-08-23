# 0027 — `Kaasamine` is a pointer to outreach, not an engagement system

- **Status:** Accepted — implemented on the `feat/matter-engagement` branch
- **Date:** 2026-08-23
- **Builds on** ADR 0003 (immutable evidence), ADR 0005 (authorization and visibility inheritance), ADR 0011 (NextAction and Submission modelling), ADR 0018 (structured Matter facts), ADR 0026 (source-aware activity).

## Context

A Matter file could not answer *did we ask anybody about this, and where did we
ask*. The department publishes consultation requests on koda.ee, sends mailings
through a campaign tool, and runs questionnaires in a survey tool. All three
live in people's memory and in mail folders, so six months later nobody can find
the link, and nobody can say whether members were consulted at all.

The obvious failure mode when building this is to keep going: recipients, then
responses, then open rates, then segments. That is a product the Chamber already
buys, and building a worse copy of it inside the case file would bury the one
fact the file actually needs.

## Decision

### One small Matter-owned child

`matters.MatterEngagement`, 0..N per Matter:

| field | meaning |
| --- | --- |
| `kind` | which channel — `WEB_CALL`, `EMAIL_CAMPAIGN`, `SURVEY`, `OTHER` |
| `title` | required, what it was called |
| `url` | optional, external, `http`/`https` only |
| `note` | optional, short context |
| `occurred_on` | optional, the date this engagement is about |
| `created_by` | who recorded it, `PROTECT` |

Nothing else. No recipient list, no response store, no click tracking, no
provider integration, no attachment field.

### Channels, not vendors

`SendSmaily`, `Alchemer` and `koda.ee` are this year's tools. A stored value
naming one of them is wrong the day a contract changes, and every historical row
then describes a service nobody recognises. The channel survives that; which
concrete service was used is in the title and the link, where a person can read
and correct it.

There is deliberately **no `MEETING`**. `Entry` already records a meeting with a
date, an author and a body. Adding it here would create two places to write down
one fact and guarantee they eventually disagree.

### A neutral date

`occurred_on`, not `sent_at`, `published_at` or `survey_opened_at`: one model
carries all three kinds, so each of those names would be wrong for two thirds of
the rows. It is optional, because somebody recording a consultation from 2019
may genuinely not know the day, and a required date would simply make the record
not get written. It is a plain `DateField` — no precision machinery, because
nobody has needed to say "some time in Q2" about outreach.

Rows sort by `occurred_on DESC NULLS LAST`, so an undated record sorts last
rather than reading as though it happened today.

### The boundaries it must not cross

- **Documents remain the evidence store.** Supporting material goes to
  `Document`/`DocumentVersion` as it always has. A second place to attach bytes
  is a second place to lose them.
- **Entries remain the chronology.** Adding an engagement writes **no** `Entry`.
  One action must not become two records that can later disagree; a lawyer who
  wants narrative writes it.
- **Submissions remain formal outbound positions.** Asking members what they
  think is not Koda's written opinion, and modelling it as one would corrupt
  every submission statistic.
- **No reference data.** Nothing here creates or requires an `Organisation`, a
  `PolicyArea` or a `Tag`. Production currently has none of the first two, and
  this feature works regardless.

### Activity

A dated engagement is genuine work on the file, so it becomes an eligible fact
in ADR 0026's *Viimane tegevus* under a new `ENGAGEMENT` basis. It competes on
its date like everything else and gets no priority: an `Entry` written later
still wins.

**An undated engagement contributes nothing**, and `created_at` is deliberately
not offered as a substitute. Somebody entering a 2019 consultation today would
otherwise move the file's last activity to today — which is exactly the
import-timestamp mistake ADR 0026 removed, arriving through a different door.

The annotation is a subquery on the existing `annotate_last_activity`, so the
cost stays bounded by the page rather than by the number of engagements.

### Audit, and not the timeline

`ENGAGEMENT_ADDED` and `ENGAGEMENT_CHANGED`. Their own types, because
`ENTRY_ADDED` would claim somebody wrote a note, `MATTER_DATE_CHANGED` would
claim a Matter field moved and `IMPORT_APPLIED` would claim an importer did it.

They are **not** in `TIMELINE_EVENT_TYPES`. The Kaasamine section already shows
the fact in a form a reader can act on; echoing each one into the professional
narrative is the noise the structured-facts architecture deliberately avoids.

The change event names the fields that moved and carries values only for the
small ones. A note can run to paragraphs, and copying every version of it into
the audit table would make the history a second, worse copy of the notes.

### No delete in v1

Create and edit. A mistaken row is corrected; a soft-delete state machine for a
five-field record would be more machinery than the fact deserves. If a genuine
need to remove one appears, it is a decision to take then, with a reason.

### URL safety

Only `http` and `https`, enforced in the service (which an importer or a shell
also goes through) and again in the form (which is where a person sees the
message). `javascript:` and `data:` are script delivery dressed as an address.

Nothing fetches the link, checks whether it resolves, or reads metadata from it.
An engagement recorded in 2019 whose campaign has since been archived is still a
true record of what the Chamber did.

The row prints the title as the link and the **host** beside it, never the
address: campaign URLs are mostly tracking parameters and would push the title
off the row.

## Consequences

- Engagement title, note and link host join the Matter's `body_text` in the
  search projection, sorted before joining so the projection hash does not move
  between rebuilds. Engagements get no projection row of their own — a pointer
  is not a document, and a result saying "the matter matched" is the honest
  locator here.
- The relation is Matter-owned, so the TEST purge planner reaches the rows and
  never any reference data, and testness derives from the parent Matter with no
  field of its own.
- `Matter` CSV export is unchanged. The export has no repeated-record
  representation, and folding an arbitrary number of engagements into one cell
  to be able to say they are exported would produce something nobody can read.
  Left out of v1, deliberately.
- No new filter and no new reporting metric. The requested feature is recording
  and viewing.

## Alternatives considered

**Fields on `Matter`.** A Matter can have several engagements, and five columns
that only sometimes apply is how a table starts growing `url_2`.

**Reuse `Entry` with a new `EntryKind`.** An entry is authored prose with a
body; an engagement is a structured pointer with a link. Folding them would make
the chronology's own vocabulary mean two things.

**A vendor enum.** Rejected above; the title and URL carry the vendor.

## Reversibility

One additive table and one choices-only audit migration. Nothing existing
changes shape, so dropping the feature is dropping the table.
