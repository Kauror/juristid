# ADR 0190 — Back to the results, a link to one row, and the row a save wrote

- Status: Proposed (owner's quality-of-life brief, PR C «navigation and editing usability», 2026-10-25)
- Amends: 0107 §2 (one sentence: leaving the page with unsaved text now asks first — §4)
- Related, unchanged: 0074 §14 (the `Tegevused` accordion), 0105 §1 (one order), 0131 §7
  (periods), 0181 (one internal read policy), AUD-008 / D-2 (the bounded grouped read)

**No migrations, no new model, no stored preference, no browser storage.**

## 1. Back to the filtered Teemad list

**Measured first.** The register keeps everything that shapes it in its address:
`q` (the live search pushes it with `hx-push-url`), every filter, `jarjestus`,
`kaupa` and `leht`. With `htmx.config.historyCacheSize = 0` and
`refreshOnHistoryMiss`, Back from a Teema re-renders that address. In Chromium
(Playwright, `e2e/test_navigation_qol.py`) a searched list, paged to `leht=2`
and scrolled, opened a Teema, and Back returned to the identical address, the
same search text, the same page and the scroll position. Native Back already
works, and nothing about it changed.

What Back cannot do is survive a second step on the Teema, and it is not a link
anybody sees. So the Teema page offers one quiet line, «← Tagasi tulemustesse»,
**only** when it was opened from a *narrowed* list (a non-empty query) on this
site. The source is the browser's `Referer`, which `SECURE_REFERRER_POLICY =
"same-origin"` sends in full for exactly that navigation. Nothing is added to the
Teema's address, so an ordinary deep link — and a link somebody copies — shows
no such line.

The return address is never echoed. `app.matters.return_link` accepts it only
when it is absolute, http(s), on this request's own host
(`url_has_allowed_host_and_scheme`), at most 2000 characters, and its path is
exactly the register's; the query is re-encoded from the parsed parameters.
Another host, `//evil…`, `javascript:`, `data:`, a relative address and any
other path of this site give no link. The line is on the full page only, above
the header and outside the column, so no save re-renders it away.

## 2. A link to one `Tegevused` row

Every row that can be opened carries «Kopeeri link» at the foot of its panel.
The link is the Teema page with `?kirje=<row key>#kaik-<row key>-rida`. The key
is `TimelineItem.row_key` — the record's id, else the id of the entry or event
the row was sorted by — so it names the same row after any amount of new
activity. Nothing reads a position, a page or a time.

`Tegevused` draws its newest rows (30 flat, `GROUPED_WINDOW` grouped). When the
linked row is not among them, the page asks **once, bounded at 300 rows**
(`LOCATE_LIMIT`), through the same reader-scoped `matter_timeline` the page
renders, where the row stands, and draws the smallest whole number of pages
that includes it — the flat list continues «Näita varasemaid» after them, the
grouped list opens the period holding it. The existing hash handling opens the
row and reveals its period; the row is shown for a moment (§3).

**An unknown key, a key from another Teema, a malformed key and a key past the
bound are one answer:** the page renders byte-for-byte as without a key (per-
request tokens aside). A row this reader may not see is not in this reader's
chronology, so it cannot be found either. A key that is not a UUID or a
`onenote:` key is ignored before any query.

The copy control reuses the register's copy binder (`data-copy-href`, the same
change as the documents PR's «Kopeeri link»). `Teema loodud` and other rows with
nothing behind their line still draw no toggle; the link does not count as
detail.

## 3. The row a save wrote

Every audit row carries the request's `correlation_id`. On a **successful POST**
that answers with the column (`_render_overview`), the ids of every record,
entry and event this request wrote on the Matter are compared with the rows
being drawn, and the matching rows carry `data-salvestatud`. A refusal, a page
view and a GET carry none; the ids are never printed. The script reads the mark
once, removes it, and gives the row the accent panel (`--surface-selected`,
`--accent-border`) for 2.4 s. No toast, no focus move, no scroll, no change to
the accordion, and no transition — it appears and goes in one step, the same
under `prefers-reduced-motion`.

Row corrections that swap one element in place (`Muuda` on a Kaasamine, a
Väline seisukoht, a Menetluse areng, a Koja arvamus or a Sissekanne) are marked
the same way: each renderer asks the same `correlation_id` question for its own
record (`_row_saved`), so only a successful correction — not opening the
editor, `Tühista` or a refusal — marks the swapped element, and the script
shows the row around it. Their existing focus landing is unchanged. In forced
colours the panel is an outline in the system highlight colour.

## 4. Leaving with unsaved text (amends 0107 §2)

0107 §2 keeps every draft host across a *swap* and stores nothing in the
browser, so «a reload still loses unsaved text». Measured: text typed into
`+ Lisa · Muu` and a click on `Minu asjad` lost it without a word. The page's one
`beforeunload` listener (0107 §3's, which flushes the note) now also asks the
browser's own «leave this page?» question when a draft host holds input that
differs from what the server rendered — the same test the swap uses — **or**
when a host the server answered with a refusal (4xx) still holds typed text: a
refused save re-renders the text into `value`, which the swap's test reads as
unchanged. That host is marked `data-draft-refused` until a 2xx answer to it.
What counts in it: words in a text, search, number, tel, url or email box or a
textarea (not dates the form fills in, hidden or read-only fields), a chosen
file, and a select, radio or checkbox the person had set before the save and
that is still set so. Not for the
note (it saves itself), not for the hosts of a form submitted natively in the
last two seconds, not for the one unload a plain same-tab click on a file
download starts (`…/tõend/<id>/`, `data-download` — the page stays; the excuse
is used up by that unload), not for the host whose own save
answered with `HX-Refresh` / `HX-Redirect`. A successful save replaces its host
with an empty one, so it stops counting; a clean page asks nothing on a link,
Back or reload. Nothing is stored. A browser test that registers a `dialog`
listener and then leaves typed text must answer the prompt
(`e2e/conftest.py` `accept_leave_prompts`).

## Consequences

- `?kirje=` is a new page parameter; the URL contract of `#kaik-…-rida` is now
  load-bearing for links people send — renaming the anchor breaks them.
- The deep link costs one extra bounded chronology read only when the row is
  not among the newest rows drawn; a GET with an unknown key costs the same read.
- Browser tests that type into a draft host and then leave without saving now
  meet a `beforeunload` dialog; Playwright accepts it by default.

## Amendment of 2026-10-26 — the row's actions are three icons, together

**Owner's UX corrections, fix 3.** A `Teema käik` row's controls were spread
over three places: `Kopeeri link` on a line of its own at the foot of the open
row, `Muuda` beside a link or the text, `Kustuta` by the heading. They are now
one group, in one order — **copy → edit → remove** — right after the record's
label or primary link (`templates/matters/partials/row_copy_link.html`,
`action_icon.html`, `row_remove.html`):

* **Copy** (§2's link, unchanged in what it copies): always this history
  entry's own address, `?kirje=<row key>#kaik-<row key>-rida` — never a web
  page the row links to. Drawn for every reader, on a closed Teema too: copying
  a link is reading. «Link kopeeritud» is a tooltip from `data-copied` and the
  shared status region; the icon's text and the line do not move.
* **Edit** (pencil): the record's own correction — the same HTMX swap targets,
  forms, revision checks and gates as `Muuda` had.
* **Remove** (X): opens the existing `Kustuta` confirmation; nothing is removed
  until `Eemalda`, and `Loobu` returns focus to the X.

Only what the record and reader actually have is drawn. Each icon carries its
word in a visually hidden span, the row by `aria-labelledby` (its own word,
then the row's line or headline), and its word in `title`; 28 px targets and
the focus ring. Other words keep their meaning and stay words: `Tühista` (calls
an `Oluline tähtaeg` off — not removal), `+ Lisa fail`, `+ Lisa töödokument`,
`Võta tagasi`. A row with no record to correct (a stage change, an imported
save) carries only the copy icon under its line. A copy-only group is not
«detail» for the accordion, so `Teema loodud` still draws no toggle.

The same amendment names a kind once in a headline: a koda.ee title that
already opens with its kind — «Koduka ülevaade: …», «Uudis: …», «Kaasamine: …»
— reads «Ülevaade: …», «Uudis: …» (`app/matters/headlines.py`, an allow-list of
the kind word and a few source words, so «Majanduse ülevaade: 2026» keeps every
word). Display only: stored titles are untouched. No migration.

## Status update 2026-10-29 — merged and deployed

This section records what happened after the decision; it does not amend the decision, and the status is unchanged (no formal acceptance was recorded).

- **Merged** 2026-10-25 in #546 (merge `e7e04973`, quality-of-life PR C).
- **Deployed** as production r28, `462838af` (2026-10-25 22:04 UTC, #543–#547); the amendment of 2026-10-26 was merged in #555 (`9ac7fd0d`) and deployed with r31, `de3564cb` (2026-10-26 16:17 UTC).
