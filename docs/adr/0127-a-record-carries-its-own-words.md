# 0127 — A record carries its own words: a write-up's title, and a round's page and note

**Status:** accepted
**Date:** 2026-10-01

**JUR-CASE-07, -08 and -09** from the Juristieksami living-dossier QA, and the
owner's decisions D3 and D4. Two activity records lacked words the real work
has, and the gap showed exactly where a living dossier is read. **One additive
migration** (`matters/0041`), no new model, no backfill, no search-index change.

1. **`Ülevaade / uudis` may carry a `Pealkiri`**, asked when a page is recorded
   as published and when it is corrected, never on a plan. The `Teema käik`
   headline reads `Ülevaade / uudis: <pealkiri>`; an untitled row reads exactly
   as before.
2. **`Kaasamine` asks `Veebileht` and `Märkus` again** — the existing `url` and
   `note` columns, on `+ Kaasamine` and on `Muuda` alike, printed in the open
   row. No new column.

---

## Context

**Two write-ups of one file read as one.** A file is often written up more than
once — «Koja seisukoht juristieksami VTK kohta», then «… seaduse eelnõu kohta».
docs/adr/0121 §5 made the link's text the name of what it opens, `Ülevaade /
uudis`, with the address behind it, and docs/adr/0074 §14 (amended 2026-09-27)
made every `Teema käik` row a closed accordion showing only its headline and its
day. So the two rows were two identical lines — `Ülevaade / uudis 9.9.2026`,
`Ülevaade / uudis 27.9.2026` — and the reader learned which was which only by
following the links. The record had no words of its own: docs/adr/0081 §2 had
refused a title on a *plan*, and nothing ever asked one on the page that exists.

**A round's public page had nowhere to go.** The Chamber's ordinary engagement
channel is a koda.ee «Hetkel käsil» page that the newsletter and the survey point
to. `+ Kaasamine` asks `Smaily link` and `Alchemer link`, and putting the koda.ee
page in either box claims a newsletter or a survey the round did not use — so
the QA pasted it into `Keda kaasati`, where it is not a link and where every work
list then reads a URL as the round's title. **And a caveat had nowhere to go**:
«Fail sisaldab kaasamise sihtrühma tööloendit; see ei tõenda, et kõigile loendis
olevatele ettevõtetele kiri saadeti» is the recorder's statement about what an
attached list proves, and the only free text was `Saadud tagasiside`, which is
what the people asked said back.

`MatterEngagement` has always had both columns: `url` (docs/adr/0027's generic
link, «optional, external») and `note`, whose own docstring describes this exact
use — «where the list came from, why it was sent late». docs/adr/0121 §4 took
both off the editor the day before the QA because `+ Kaasamine` never asked them
and the chronology never printed them. The QA is what they are for.

## 1. `Pealkiri` on `Ülevaade / uudis`

`MatterWebsiteOverview.title`: a `CharField(300)`, blank, default `""` with a
database default (the release still serving during `migrate` inserts plans
without naming it). Nothing derives it — not the address, the page or the
Matter — and no stored row is backfilled.

**Asked where a page exists.** `+ Ülevaade / uudis` asks `Kuupäev`, `Pealkiri`
(valikuline) and `Link`; the planned row's «Lisa link ja avaldamiskuupäev» and a
published row's `Muuda` ask it too. A plan is still asked nothing: docs/adr/0081
§2's reason — a headline invented before the page exists is contradicted by the
page a week later — holds, and the title arrives with the page.

**The headline, not the link.** `MatterWebsiteOverview.headline` is `Ülevaade /
uudis: <pealkiri>`, the `Kind: name` shape a `Kaasamine` row already has, and it
is the line a closed row shows and the name its toggle reads. The link keeps
saying `Ülevaade / uudis` and keeps following the stored address
(docs/adr/0121 §5 stands), so the title is not printed twice and an untitled row
is byte-for-byte what it was. `Muuda` swaps the headline out of band with the
date cell, so a renamed page renames its closed line and its toggle at once.

**Rules.** Trimmed, optional, refused past 300 characters rather than truncated
(`normalize_overview_title`). A correction that does not carry the box — a row
drawn before this release — leaves the name alone; an emptied box clears it.
`WEBSITE_OVERVIEW_PUBLISHED` carries `title`, `WEBSITE_OVERVIEW_LINK_CORRECTED`
lists `title` in `fields` with `title_from` / `title_to`, and both events'
summary names the page. The correction event keeps its label («… linki või
kuupäeva parandatud»): renaming an audit choice is a migration, and `fields`
already says which column moved.

**Not searchable yet.** Overviews have no search row at all (docs/adr/0085 §4);
indexing a title would be a new source kind, an `INDEX_VERSION` change and a
full rebuild, which this decision does not take. The opinion's overview chips
and its list of linked write-ups name the page where it has a title.

## 2. `Veebileht` and `Märkus` on `Kaasamine`

**The existing columns, under their real names.** `url` is asked as `Veebileht`
— the round's public page — and `note` as `Märkus`, on `+ Kaasamine` (as
`website_url` and `engagement_note`, because that panel keeps Django's default
ids beside the overview panel's `id_url`) and on `Muuda` (as `url` and `note`).
Create and edit ask the same things, which was docs/adr/0121 §4's rule and still
is.

**The same address rule as the provider links.** `normalize_engagement_url`:
`http`/`https` only, `www.koda.ee/…` gains its `https://`, refused past 1,000
characters, and the refusal lands under the box. The three links stay three
concepts: `Veebileht` is the page about the round, `Smaily` the mailing,
`Alchemer` the survey.

**Where they are read.** The open `Teema käik` row lists `Veebileht` first, then
`Smaily` and `Alchemer`, each a named link and never the address; `Märkus` is its
own labelled line (`own_note`, the shape `Väline seisukoht` and `Märge` use),
apart from `Saadud tagasiside`. **Nowhere else**: the closed line, `Praegune
tegevus`, `Minu asjad`, the rail and every work list read the audience
(`Keda kaasati`), so a long address or note never becomes a round's title. An
imported `Kirjade voor` stored its mailing's preview in `url`
(`register_outreach`), which is not a page about the round, so on that stored
channel the link reads `Link`, docs/adr/0027's word.

**`Muuda` names them only when the POST carries the box.** An emptied box clears
the column; a POST from a row drawn before this release has no such box and
asked nothing, so `_UNSET` keeps what the record holds. `ENGAGEMENT_ADDED`
records `has_note` beside `has_url`, never the words.

**Search and visibility are unchanged.** The search projection already read
`note` as body text and the hosts of all three links as aliases, scoped to the
engagement's own visibility; nothing about it moves and `INDEX_VERSION` is
unchanged. Both values live on the engagement row, so a restricted round's page
and note reach nobody who may not see the round.

## Not changed

The overview's lifecycle, address rule, uniqueness, closed-Matter rules,
optimistic concurrency and absences (no work, no deadline, no search row, no
statistic); the `Kaasamine` wait, `Lõpeta kaasamine`, `Ootan tagasisidet`, the
provider links, `Saadud tagasiside`, `Vastuseid` and both dates; docs/adr/0126's
`Märgi praegune tegevus tehtuks`; signed-container and upload validation; the
search index; permissions and the business-write boundary.

## Supersedes and narrows

* **docs/adr/0081 §2** and **docs/adr/0085 §1 / Consequences** — «no title»:
  a *published* write-up may carry an optional `Pealkiri`; a plan still has none.
* **docs/adr/0121 §4** — «no second link concept», «the generic `Link` and
  `Märkus` are not offered»: reversed. Both are offered on create and edit as
  `Veebileht` and `Märkus`, and printed. §4's parity rule stands.
* **docs/adr/0121 §5** — the overview link still reads `Ülevaade / uudis`; the
  row's headline now carries the title where there is one.

## Alternatives considered

* **The address as the link text when there is no title.** docs/adr/0121 §5 has
  just retired that on the owner's word; a closed row would also still show
  nothing, because the link is inside the row.
* **The title in a companion `Märge`.** An unrelated row, and a generic note used
  as storage for a structured fact.
* **New columns for the page and the note.** Duplicates of columns that already
  exist, leaving historical koda.ee pages stored in `url` unread.
* **The caveat in `Saadud tagasiside` or as a document description.** The first
  is what the respondents said and is deliberately not indexed; the caveat is
  about the round, not the file's bytes.

## Migration and rollback

`matters/0041_website_overview_title`: one `AddField` with a database default.
Instant on a table of single-digit rows, nothing rewritten. A rollback to the
previous release ignores the column; removing it drops only the names typed
since.
