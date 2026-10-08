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

## Amendment of 2026-10-07 — the owner's October round

**One migration**, `matters/0047`. It adds `MatterWebsiteOverview.kind`, with `""` as the database default. It is additive, and no row is rewritten.

**A, extended — a title before the save.** Every shared upload queue lists each chosen or dropped file with a `Pealkiri`, which defaults to the filename. The queue posts that title as `<field>__pealkiri`, in the order of the files. `UploadTitlesMiddleware` pairs it with its file once, for every form. A count that does not match is ignored.
- The title becomes `Document.title`, through `read_upload` → `AcceptedUpload.display_title` and through `file_incoming`.
- Staged Uus teema files carry it as `intake_title__<id>`.
- The filename stays the version's `original_filename`. The bytes, checksum, MIME type and storage are untouched.
- Since 2026-10-08 the title is shown as text with a ✎ beside it, not as a box per file. The ✎ makes only that file's title editable; Enter or leaving the box keeps it, Escape or an empty box restores the previous one. The posted names and order are unchanged: a hidden input per file carries the confirmed title (`static/js/ux.js` `openTitleEdit`, one behaviour for the queue rows and the staged rows).

**B §8, superseded — feedback finishes the round.** Saving `Lisa tagasiside` records the received feedback, files it, and closes the chosen round. These happen in one transaction, through the canonical `complete_engagement_feedback`.
- It behaves the same whether it is opened from `+ Kaasamine` or from `Tehtud` on the waiting line under `PRAEGUNE TEGEVUS`. Both are the same form and the same view.
- No `NextAction` is inferred from it.
- `Lõpeta kaasamine` remains for closing a round without a feedback record.

**C. `Ülevaade` and `Uudis` are told apart.** This narrows docs/adr/0085 §1. The kind is stored on the record.
- **Classification from the address.** `app.matters.publication_kind` reads it from koda.ee's own paths, verified 2026-10-07:
  - an overview is `/et/meie-moju/hetkel-kasil`, `/en/current-drafts` or `/ru/current-drafts`;
  - news is `/et/uudised`, `/en/news` or `/ru/novosti`.
- **Anything else is asked.** It is never guessed.
- **Older rows.** Rows recorded before the column read their kind from the address where it is known, and stay «Ülevaade / uudis» otherwise.
- **Chronology.** It reads «Ülevaade – …» or «Uudis – …».

**B, extended — the round's Ülevaade.** `Alusta kaasamist` asks for an `Ülevaate link`, which is prefilled with the newest Ülevaade the reader may see, and never with news. A save records that Ülevaade in the same transaction as the round:
- it reuses one already recorded at the same address;
- otherwise it publishes a plan the Matter owes;
- otherwise it records a new one with no publication day.

A news address is refused. The start form takes files again. They belong to the new round and inherit its restriction (docs/adr/0137).

## Second amendment of 2026-10-07 — the address first, and what it says before the save

**C, extended — `+ Ülevaade / uudis` previews.** The form asks `Link` first, then `Kuupäev`, `Pealkiri` and `Liik`.
- Once an address is pasted, `preview_website_overview` answers its kind, using the same `classify_publication_url` the save applies again. It also answers the koda.ee page's own title (`og:title`, else `<title>` without the site's name). The page shows both before `Lisa ülevaade / uudis` is pressed.
- The title is read by `app.matters.publication_title`, the one outbound request to a pasted address (docs/adr/0089 §4, amended). It goes only over `https`, only to `koda.ee` / `www.koda.ee`, with redirects checked by hand, a short timeout and a size cap. Any failure is an empty title, and the form says the title could not be read.
- **The person's answers stay theirs.**
  - A typed title is never replaced, and both boxes stay editable.
  - An answer for an address no longer in the box is dropped.
  - An unclassified address leaves `Liik` to the person.
- `Kuupäev` opens on today (Europe/Tallinn). No publication day is read off the page.
- `settings.PUBLICATION_TITLE_FETCH` is off in the test suite, CI and rehearsals.
