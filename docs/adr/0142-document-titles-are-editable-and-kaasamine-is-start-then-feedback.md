# 0142 — Document titles are editable, and `+ Kaasamine` is start, then feedback

**Status:** accepted
**Date:** 2026-10-06

Two small usability decisions from the owner. **One migration**, `audit/0034`:
one new `ChangeEvent.event_type` choice, no SQL. No model, field or lifecycle
changes, and no existing row is rewritten.

## A. A document's display title can be changed

1. **`Document.title` is the display title and is editable.** `Muuda` on a
   Dokumendid row opens one `Pealkiri` box in place (`Salvesta` / `Loobu`), and
   the document's own page offers the same box under `Paranda dokumenti`.
2. **Nothing about the evidence moves.** The original filename on each
   `DocumentVersion`, the stored object, the bytes, the checksum, the MIME type,
   the version history and the provenance are untouched, and no version is
   written. The filename keeps showing under the title where the list already
   showed it.
3. **Through a service, audited.** `rename_document` takes the Matter's and the
   document's locks like `change_document_role`, refuses a closed Matter, a
   removed document and an empty or over-long title, collapses whitespace, and
   writes nothing for the same title. It records `DOCUMENT_TITLE_CHANGED` with
   the old and new title in the payload; the event has the document's own
   visibility.
4. **Search follows the one document.** The save fires the existing
   `refresh_on_document_change`, which rewrites that document's rows under the
   new title; no rebuild.

## B. `+ Kaasamine` is two acts

5. **Two modes in one panel**, chosen by hand at any time:
   * `Alusta kaasamist` — `Keda kaasati`, `Kaasamise kuupäev`,
     `Tagasisidet ootame kuni`, `Veebileht`, `Smaily link`, `Alchemer link`,
     `Märkus`. It writes the canonical `MatterEngagement` exactly as before.
     `Vastuseid`, `Saadud tagasiside / arvamused` and the reply files are no
     longer on it; the count and the text are still corrected through `Muuda`
     on the round.
   * `Lisa tagasiside` — `Kaasamine`, `Saadud tagasiside`, `Kuupäev`, files,
     `Märkus`. It writes the existing canonical record for an answer to a round:
     a `Meile saadetud tagasiside` (`MatterExternalPosition`, provenance
     RECEIVED) tied to the round by `engagement`, through the same
     `add_matter_external_position`. The round row already counts these and
     their files follow them. No second engagement model and no new record.
6. **The default is the canonical lifecycle.** The panel opens on
   `Lisa tagasiside` when the Matter has an open round for this reader —
   `work_items.open_feedback_waits`, the same reading `PRAEGUNE TEGEVUS` lists
   (`lifecycle_tracked` and no `feedback_closed_at`, docs/adr/0132) — and on
   `Alusta kaasamist` otherwise, including when only completed rounds exist.
   Never from a deadline, a title or which round is newest. A refusal reopens
   the form it came from.
7. **The round is named, not guessed.** `Kaasamine` offers only this Matter's
   open rounds as this reader may see them, is required, and is preselected
   only when there is exactly one. The field's queryset is what validates a
   post. With no open round the mode says so and draws no form.
8. **Lifecycle unchanged.** Adding feedback does not close a round;
   `Lõpeta kaasamine` on the row is still the one act that does, with its own
   `Saadud tagasiside` and files (docs/adr/0086 §6, docs/adr/0132).
