# 0076 — A closed Matter's Dokumendid surface is read-only for business work

Status: accepted
Date: 2026-09-12
Extends [0075](0075-teema-current-action-and-add-to-matter-workspace.md) §12
from the Teema workspace to the Dokumendid tab. Decides the question
[0061](0061-a-matters-opinions-live-under-dokumendid.md) left open when the
per-Matter Arvamused page was folded into the file list. Nothing in 0005, 0011
or 0019 changes.

## 1. Context

0075 §12 established that a closed Matter accepts no new canonical business
content, and stated the rule where it can be enforced: under the Matter's own
row lock, in `lock_open_matter_for_business_write`, because a page is not a
boundary. Every operation on the Teema workspace takes it.

The Dokumendid tab was not part of that round. Integrated QA on
`integration/post-qa-sep11` posted to it on a Matter that had been closed and
found three doors still open:

* `↑ Lae dokument` wrote a `Document` and an immutable `DocumentVersion`.
* `+ Uus arvamus` wrote a DRAFT `Submission` with its recipients.
* `+ Registreeri saatmine` wrote a SENT `Submission`, its recipients and its
  send event.

A fourth was reachable on a draft that had been left behind: `Lisa fail`,
choosing existing evidence, and `Märgi saadetuks` all advanced it.

None of this was a regression — the surface had always behaved this way, and
`Dokumendid`'s `can_write` asks only about the reader's role. It contradicted
the rule enforced three centimetres away on the tab beside it.

**Why it had not been closed already.** The services underneath are the same
ones the register import composes. `app.legacy_import.opinion_apply` files two
and a half thousand historical letters onto archive Matters, and most of those
are shut; `historical_apply` puts OneNote material there; `email_intake` and the
intake staging land files on Matters nobody asked about the state of. A guard in
`create_document`, `add_evidence_version`, `create_submission`,
`select_final_evidence` or `mark_submission_sent` would refuse the historical
record, which protects nothing and breaks the one import the product exists to
preserve.

## 2. Decision — read-only for *normal business work*, and reopening is the way out

A closed Matter is read-only for **normal interactive business work**.

A lawyer reading a closed Matter may read it, browse its chronology, see and
open its Documents, download its evidence, and inspect its opinions and their
send details. They may not **create or advance canonical business content** on
it. If ordinary legal work has to continue, the Matter is reopened first, the
work is done, and it is closed again — which leaves somebody's name on both
decisions.

The rule is `closed → no normal business writes`. It is not
`once closed, forever immutable`.

Five operations are refused on the Dokumendid surface:

| Control | Route | Refused because |
| --- | --- | --- |
| `↑ Lae dokument` | `documents:upload_evidence` | new canonical evidence |
| (a further version) | `documents:add_version` | new canonical evidence |
| `+ Uus arvamus` | `submissions:create` | new advocacy begins |
| `Lisa fail` / choose existing | `submissions:attach_evidence` | advancing a draft |
| `Märgi saadetuks` | `submissions:mark_sent` | advancing a draft, and it means *now* |
| `+ Registreeri saatmine` | `submissions:register_sent` | see §4 |

## 3. The boundary is a layer, not a flag

The primitives keep their generality. The rule is stated in a thin **interactive
use case** above each of them, which is what the HTTP routes call and what the
importers do not:

    app.documents.services.capture_evidence_on_open_matter
    app.documents.services.add_version_on_open_matter
    app.submissions.services.create_opinion_draft_on_open_matter
    app.submissions.services.attach_final_evidence_on_open_matter
    app.submissions.services.select_final_evidence_on_open_matter
    app.submissions.services.mark_submission_sent_on_open_matter
    app.submissions.services.register_sent_opinion_on_open_matter

This is the shape `record_engagement` already had over `add_engagement` for
exactly the same reason (0075 §12): the leaf has a second, legitimate writer.

Two things were deliberately **not** done.

**No `allow_closed=True`.** A caller-supplied escape hatch is a boundary with a
parameter, and the first crafted post that sets it is through. Import privilege
comes from using the import path.

**No role or admin exception.** Being a powerful user of the UI is not the same
as running an import, and an ADMINISTRATOR clicking `Registreeri saatmine` is
doing ordinary business work with more permissions, not migrating an archive.

## 4. `Registreeri saatmine` is the interesting one, and it is refused

Registering a send records something that already happened, which sounds like
precisely the retrospective act a finished file should still accept. At the
service it is: that is how the archive apply files a letter Koda really sent in
2019 onto a Matter closed in 2019.

What arrives on the Dokumendid surface is a different act. A person is in front
of the file, creating a canonical `Submission`, its recipients and its
`SUBMISSION_SENT` event on a Matter somebody has declared finished, with a date
they typed. That is ordinary business work with a backdated field.

So the two layers answer the same call differently, on purpose:
`register_sent_opinion` files it, `register_sent_opinion_on_open_matter` refuses
it. `tests/test_integration_post_qa_sep11.py` asserts both halves side by side.

## 5. Hiding the controls decides nothing

The fresh page renders read-only — the upload panel, the two opinion forms and
the draft's own controls are gone behind `can_add_content`, which is
`can_write` **and** `matter.is_open`. That is courtesy: a reader should not be
shown a button whose only answer is a refusal, and the closed banner above the
table already offers `Ava uuesti…`, which is the thing that would make it work.

The boundary is the lock. A browser that had Dokumendid open before somebody
else closed the Matter still has every field and every button on it, and its
POST reaches a server with no memory of which page it came from. Each use case
takes the Matter's row `FOR NO KEY UPDATE` and reads `is_open` **from the locked
row**, never from the instance the view arrived with — so the stale-tab race has
one outcome and not two. `tests/test_concurrency.py` runs it against real
PostgreSQL in both orderings.

Existing content is never hidden. The files, the sent opinions, their dates and
addressees, the archive letters, `Ava`, `↓` and the document's own page are all
exactly as they were.

## 6. What this does not decide

**Historical correction.** `Võta tagasi`, superseding, and the editing or
cancellation of a fact already recorded are corrections of history rather than
new business content. They keep their current behaviour and their current
`can_write` gate. Whether a closed Matter should accept them is a separate
product question and this ADR does not answer it.

**Working documents.** A SharePoint reference is a pointer to a living file and
explicitly not evidence of anything (0003). It is outside the canonical
business-write boundary, as it was under 0075, and `+ SharePointi viide`
therefore stays available on a closed Matter.

**Personal notes.** `Märkmed` are one person's own workspace and were
deliberately outside 0075's boundary. Untouched.

## 7. Consequences

A draft opinion left on a Matter that is then closed cannot be finished in
place. This is the intended cost: the alternative is a `Submission` whose send
event postdates the closure of the file it belongs to, which no report can read
honestly. Reopening is one POST from the banner.

No schema change, no migration, no backfill, and no change to
`SearchDocument.INDEX_VERSION` — nothing about what is stored or how it is
indexed moved.

## 8. Reversibility

High. The rule lives in seven one-line calls and one context flag. Removing a
call restores the previous behaviour for that operation alone, and the primitives
were never touched, so the import path is unaffected either way.
