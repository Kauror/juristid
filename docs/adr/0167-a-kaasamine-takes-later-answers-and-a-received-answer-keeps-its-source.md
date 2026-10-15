# 0167 — A `Kaasamine` takes later answers, and a received answer keeps its source

**Status:** accepted — owner decision D8 of 2026-10-14 (QA remediation round), and the fix of QA-SUBDOCS-02.
**Date:** 2026-10-14

**No migrations**, no data migration, no backfill, and no existing row is
rewritten. One search-projection change (§3) that reaches rows written before
it only when they are next saved or the index is rebuilt.

Amends docs/adr/0084 §3 (where the source rule is enforced), docs/adr/0086 §5
(«`Saadud tagasiside` is not indexed») and docs/adr/0142 §B as amended on
2026-10-07 (which rounds `Lisa tagasiside` offers, and what happens to a round
after its first answer). Everything else those records decided stands.

## Context

The independent QA of 2026-10-14 found three things about what comes back from
a consultation round:

* **QA-SUBDOCS-02 (P2).** `Lisa tagasiside` with only a PDF is a complete
  record — the file is its source (docs/adr/0084 §3, amended 2026-09-16).
  `Eemalda dokument` on that PDF then succeeded: `removal_refusal` asked only
  about opinions and legal hold. The `MatterExternalPosition` was left with no
  written position, no address and no file a reader can open — the one state
  0084 §3 says the record may never be in. `correct_external_position` then
  kept saving the empty record, because it counted every `DocumentLink` row,
  including links to removed documents.
* **DC-02.** The first `Lisa tagasiside` closed the round (0142, amended
  2026-10-07) and the form then offered only open rounds, so a second member's
  answer to the same consultation could not be tied to it.
* **DC-01.** The same member feedback was findable or not depending on the
  button: `Lisa tagasiside` files it as an indexed `MatterExternalPosition`
  (docs/adr/0091), `Lõpeta kaasamine` / `Muuda` writes the round's own
  `feedback_received`, which 0086 §5 kept out of search.

The owner decided (D8): later member feedback can be recorded against an
existing `Kaasamine` even after its first response; feedback is searchable and
displayed consistently whichever canonical form stored it; previous responses
are never discarded or overwritten.

## Decision

### §1 — A received answer keeps a source (QA-SUBDOCS-02)

1. **`Eemalda dokument` refuses the last live file of a position that has no
   written `Seisukoht` and no address.** `removal_refusal` asks
   `app.matters.position_sources.positions_resting_only_on(document)`: live
   (not removed) positions linked to this document, with an empty `summary`
   and `url`, and no *other* linked document that is still on the Matter. The
   refusal is the established one — the service's own sentence
   (`DOCUMENT_IS_LAST_POSITION_SOURCE`), stated on the document's page under
   the `Eemalda` control and raised by `remove_document` under the Matter's
   and the document's locks — and it names the way out: write the position or
   add the link through `Muuda` on the record, then remove the file.
2. **A correction counts only live files.** `correct_external_position` asks
   `live_source_document_count(position)` — links whose document has no
   `removed_at` — instead of `document_links.count()`. A position whose file
   was removed while it still had words cannot then have its words emptied.
3. **What does not block the removal:** a position that is itself removed
   (`Kustuta`), a position with words or an address, a position with another
   live file, and a link from any other record (a round, an entry). **What
   keeps the file a source:** a new version (`Lisa uus versioon`) — the
   document, not a version, is the source.
4. Both checks run under the Matter's lock, which every writer of a position's
   `summary`/`url` and every document removal already takes, so the two cannot
   race into an empty record.

### §2 — The first answer finishes the wait; later answers still belong to the round (D8)

1. **The round's state after its first answer is «finished, still taking
   answers».** No new state, column or flag: the round is *finished* exactly as
   before (`feedback_closed_at` set, one `ENGAGEMENT_FEEDBACK_CLOSED` event,
   off every work surface), and any finished tracked round may receive later
   answers.
2. **WAIT semantics are unchanged.** The first answer to an **open** round
   finishes its wait in the same transaction as the record, through the
   canonical `complete_engagement_feedback` — as since 2026-10-07, from
   `+ Kaasamine → Lisa tagasiside` and from `Ootame tagasisidet → Tehtud`
   alike. A later answer to a **finished** round records the answer only: the
   closure's time, actor and audit row are untouched, no second closing event
   is written, and the round is never reopened.
3. **The save contract** (`app.matters.engagement_replies.finish_wait_on_reply`,
   called by `views._record_external_position` for `EngagementReplyForm`):
   POST `engagement`, `summary`, `stated_on`, `attachments` to
   `matters:add_engagement_reply`. On success the reply is recorded and, if the
   named round was open (re-read under the Matter's lock), its wait is finished;
   the function returns `True` when it finished the wait and `False` when the
   round was already finished. The response is the re-rendered Teema view
   (200). A refusal is 400 with the form bound and finishes nothing. A round
   finished meanwhile by somebody else no longer refuses the save: the answer
   is recorded against the finished round.
4. **Which rounds are offered** (`engagement_replies.answerable_rounds`): this
   Matter's `lifecycle_tracked` rounds on an open `FULL` Matter, through the
   round's own `visible_to` (so restricted and removed rounds are neither
   offered nor accepted for a reader who may not open them) — open ones first,
   finished ones marked «lõpetatud». A round the register importer filed as
   history is not offered; `Ootan tagasisidet` starts it first. The panel still
   **opens** on `Lisa tagasiside` only while an open round exists (0142 §6).
   Preselection only when it is not a guess: exactly one open round, or none
   open and exactly one finished.
5. **Every answer is its own record.** Each reply is a new
   `MatterExternalPosition` (provenance `RECEIVED`) with its own date, files
   and `EXTERNAL_POSITION_RECORDED` event. Nothing updates, merges or replaces
   an earlier reply, and the round's own `feedback_received` is never written
   by a reply.
6. **A repeated submit is not a second answer** (D3). A reply identical to a
   live reply already on the same round — the same words, address, day **and**
   the same set of files by SHA-256 — is refused before anything is written
   (`REPLY_ALREADY_RECORDED`), under the Matter's lock. The same words on
   another day, or with another file, are another answer.
7. **Restriction.** A later answer to a restricted round is restricted exactly
   as a first one: the inheritance belongs to `record_external_position`
   (QA-AUTHZ-01, fixed in the same remediation round), which every reply goes
   through; this record adds no visibility scheme of its own.

### §3 — One answer, findable and readable whichever form stored it (D8)

1. **Search.** The `ENGAGEMENT` search row's body is the round's `Märkus`
   **and** its `Saadud tagasiside` (`child_indexing._engagement_values`).
   0086 §5 asked the visibility question first; it is answered by the row's
   own: the `ENGAGEMENT` kind is authorized at query time against the round's
   own `visibility_override` (AUTH-003), so a restricted round's feedback
   reaches nobody who may not open the round. The existing `post_save` refresh
   keeps it current on `Lõpeta kaasamine` and `Muuda`.
2. **No `INDEX_VERSION` bump.** The version is a fail-closed authorization gate
   (`search.services`, docs/adr/0057): bumping it makes every row ineligible
   until a rebuild, which is right when a stored vector may hold text a reader
   may not see. Here older rows hold **less** text than the current code
   writes, never more. **Search-index requirement for the release:** an
   `ENGAGEMENT` row written before this change carries no `feedback_received`
   until the round is next saved; for full parity on existing data run
   `rebuild_search_index` once after deploy (or accept that pre-existing
   `Lõpeta kaasamine` texts become findable on their next save).
3. **Display.** The round's chronology row already printed its own `Saadud
   tagasiside` and listed each linked answer as «who · date · provenance». It
   now prints each linked answer's words under its line too, in the same
   `uxtl__feedback` style, in stated order — so what came back reads in one
   place on the round, whichever form stored it. Each answer keeps its own
   chronology row as before. Only answers the reader may see are listed.

## Consequences

* `Lisa tagasiside` is the one door for every answer to a consultation, first
  or later; `+ Arvamus / tagasiside` still declares no `engagement` field
  (docs/adr/0095 §5) and files unsolicited feedback as before.
* `Lõpeta kaasamine` remains the way to finish a round without a reply record.
* No statistic changes: a reply is still not a response obligation or a count
  (0084 §4); `MatterEngagement.response_count` stays the only «vastajaid»
  figure.

## Reversibility

Code-only. Reverting §2 makes finished rounds unofferable again without
touching any stored reply; reverting §3.1 removes the text from the projection
on the next refresh.

## Amendment of 2026-10-15 — see docs/adr/0170

§2's rules apply when a round is linked; a standalone position touches no wait (docs/adr/0170-lisa-tagasiside-is-the-one-door-for-every-position.md).
