# 0219 — «Monitooring» gathers the new drafts, the Government's agenda and the registers' list

Date: 2026-11-22 · Status: proposed (owner brief 2026-11-22, «make a new tab up top and call it monitooring»; four answers the same day; release awaits owner review); amendment 2026-11-26 (r95) proposed

## Context

ADR 0218 put «Uued eelnõud» behind a link on Osakond. The owner found the placement
poor and the release notes about it hard to follow, and asked for one tab on the top
bar that holds the new monitoring work. The Chamber also runs an agent outside Juristid
that reads the Government's weekly «Valitsuse istungi kommenteeritud päevakord» from
Wednesday noon to Thursday noon, picks the items that may matter to the Chamber and
e-mails them; the owner asked for that to live in the same tab, the newest week shown
in full and older ones folded. Asked on 2026-11-22, the owner chose:

1. Juristid reads and judges the agenda itself (the outside agent is not kept in step).
2. No e-mail: the review is shown in Juristid and the bell says it arrived.
3. The whole tab is the department head's until the head opens it to everyone.
4. The registers' cross-Teema list («Uued dokumendid registrites») moves into the tab too.

## Decision

1. **«Monitooring» is a tab on the top bar** (`/monitooring/`), after Statistika, with
   three sections: «Uued eelnõud» (ADR 0218), «Valitsuse info» and «Registrite
   dokumendid» (the list ADR 0217 called «Uued dokumendid registrites»). The tab, its
   sections and the bar's link are shown to whoever holds
   `department.view_management` and, once the head presses «Näita kõigile» (on any of
   its pages), to every business reader — ADR 0218 §6's switch, now for the whole tab.
   The link on Osakond is gone. `/arengud/` and `/arengud/uued-eelnoud/` redirect.
   A lawyer still decides on a Teema's documents on its own «Registritest» tab, which
   is not part of Monitooring and stays open to every reader of the Teema.
2. **The agenda is read by Juristid** (`watch_government_agenda`, cron every half hour
   from Wednesday 12:00 to Thursday 12:00 Tallinn). valitsus.ee publishes it as a news
   page with no feed and a search answered by script, but its address follows the
   session date (`/uudised/valitsuse-191126-istungi-kommenteeritud-paevakord`, or
   `valitsuse-7-mai-2026-…`); a run tries those addresses for the week's Wednesday,
   Thursday and Friday through the fenced door (ADR 0217 §5, `valitsus.ee` added;
   robots.txt allows `/uudised/`). The page is parsed item by item — number, title,
   «Esitaja», «Tüüp» and the Government's explanation — and stored once
   (`GovernmentAgenda`, `AgendaItem`); a week already stored reads nothing again.
   A page that is not a commented agenda, or names another session, is an error,
   never an empty week. At release the previous three weeks are read once (`--weeks 4`).
3. **The model judges, it does not decide.** With `JURISTID_REGISTER_SWEEP_AI` on, one
   request per agenda gives every item a strength (Tugev, Võimalik, Nõrk = «Kontrollimist
   vajavad võimalikud seosed», Ei puutu), one Estonian line on why, and may name an open
   unrestricted Teema it is plainly about (only an id it was offered is kept). It
   receives the Government's public text, ADR 0218's Chamber profile with the outside
   agent's topic list, the open unrestricted Teemad's titles, and — as a signal of the
   Chamber's current work, never cited — the titles of the department's own Postipoiss
   letters of the last 120 days. A restricted Teema is never sent. A failure leaves the
   agenda «hindamata» for the next run.
4. **«Valitsuse info»** lists the agendas one fold per week, the newest open: the items
   judged Tugev or Võimalik with the Government's own text behind «Valitsuse selgitus»,
   then «Kontrollimist vajavad võimalikud seosed», then «Ülejäänud punktid», folded.
   Once Thursday 12:00 Tallinn has passed with no agenda stored for the week, the page
   says so and asks for a manual look (the outside agent's «päevakorda ei leitud»).
5. **The bell** (ADR 0218 §7's source, now labelled «Monitooring») counts new drafts
   and new agendas in one unread notice per person who may decide; an agenda with an
   item that may matter — or one the model could not judge yet — is told. A notice that
   carries an agenda is due until read; one with drafts only until the list is empty.
   It opens «Valitsuse info» when it carries only agendas, else «Uued eelnõud».

## Consequences

- One small query per page for anybody but the head (whether the tab is open, for the
  bar's link); the page budgets that measure non-heads move by one, each with its reason.
- The outside agent's other sources (SharePoint, koda.ee news, the seed index) are not
  read; the Postipoiss letters Juristid already holds stand in for «current work».
- No e-mail is sent; whoever wants the weekly review opens Monitooring or follows the bell.
- Migration `register_watch` 0005: two new tables and one column with a database default.

## Amendment 2026-11-22 — owner's notes on the released tab

- §1: «Näita kõigile» / «Näita ainult mulle» moved off Monitooring's pages to Haldus →
  Osakonna töökorraldus (the head's), which the switch returns to.
- Each tab carries its count instead of a section heading: «Uued eelnõud» the drafts on
  the list's open part, «Valitsuse info» the newest agenda's items judged to matter,
  «Registrite dokumendid» the **Teemad** with new documents waiting (not the documents).
  The intro sentences are gone; «Viimati otsitud …» stays. The page is tighter.
- «Registrite dokumendid» lists one shut fold per Teema (opened when filtered to one
  Teema), and its head offers «Jäta kõik vahele»: every document still waiting on that
  Teema is skipped through `watch.dismiss`, one by one, exactly as «Jäta vahele».

## Amendment 2026-11-22 (2) — agenda items onto their Teema (owner: «both»)

- An agenda item the model tied to a Teema (§3: only an offered open, unrestricted one) and
  judged Tugev, Võimalik or Nõrk is a **proposal on that Teema**: on «Valitsuse info» with
  «Lisa teemale» / «Jäta vahele», and on the Teema's own «Registritest» tab under
  «Valitsuse istungi päevakorrast» with «Lisa märkena» / «Jäta vahele».
- «Lisa teemale» writes a `Menetluse areng` through `add_procedural_development`: dated the
  session, titled «Valitsuse istung: <item>», its note the item's number, presenter, type, the
  Government's text and the agenda's link. «Jäta vahele» records the decision only. Only a
  waiting item on an open Teema the reader may see; a decided one is refused.
- The «Registritest» badge counts waiting proposals with the register documents (one query);
  a run that stores an agenda tells each tied Teema's owner in the bell («Registris uus
  dokument», ADR 0217 §6), and that notice stays due while a proposal waits.
- Migration `register_watch` 0006: `AgendaItem.status` (NOT NULL, database default PENDING),
  `decided_by`, `decided_at`, `development` (nullable).

## Amendment 2026-11-23 — the fold says it folds; files on an agenda note

- Each sitting's agenda heading on «Valitsuse info» carries a chevron that turns
  when the agenda is open, a hover highlight and a tooltip, like «Valitsuse
  selgitus» under each item (owner's round 4).
- An agenda item's «Lisa märkena» takes the lawyer's own files (`failid`), as a
  register document's does (ADR 0217 amendment 2026-11-23); «Märgi kõik loetuks»
  on the Teema's «Registritest» tab takes the Teema's waiting agenda items too.
- The agenda model's prompt carries the Koda house style and its reasons pass
  `house_style.fix` (docs/koda-house-style.md).

## Amendment 2026-11-23 (2) — bounded agenda verdicts, settled items untouched (audit HR-02)

§3's «A failure leaves the agenda «hindamata» for the next run» had no end: one item
the model left out kept `judged_at` empty, so the whole agenda was sent again every
half hour, and each pass rewrote the strength, reason and Teema of items a person had
already added or marked read. Now:

- **Only waiting, unjudged items are sent and written.** An item judged, added
  («Lisa teemale») or marked read is neither in the prompt nor saved over; the write
  re-reads the items under lock, so a decision made while the model answered stands.
- **`judged_at` means «an answer with verdicts was read»**, not «every item was
  answered». Items that answer left out, or answered with an unusable strength, stay
  «Hindamata» (`Strength.UNJUDGED`, no new status), are shown as such («N punkti
  hindamata» on the agenda, a «Hindamata» mark on the item) and are not asked again.
- **A failed request, a cut-off answer, an unreadable one and a readable one with no
  usable verdict are retried by later runs, at most three requests per agenda in all**
  (`agenda.MAX_ATTEMPTS`, counted in `GovernmentAgenda.ai_attempts`, migration
  `register_watch` 0008). An agenda stored before this may be asked at most three more
  times.
- §5's bell is unchanged in meaning: an agenda is told when an item may matter or
  when items remain unjudged; a Teema owner is told about a proposal from whichever
  run produced it, a retry included.

## Amendment 2026-11-23 (3) — what may be sent to the model (audit HR-01, HR-09)

§3's «A restricted Teema is never sent» now holds for the letters too, and TEST data
is kept out of every register-watch prompt (`app/register_watch/outbound.py`, the one
rule for this ADR's agenda, ADR 0218's new drafts and ADR 0217's nightly watch):

- **A letter title is sent only when the letter is reliably tied to a sendable
  Teema:** its pairing was applied (by the calibrated rule or a person) to a NORMAL,
  real, undeleted Teema, and the sent opinion it became, if any, is not restricted.
  Unpaired letters, letters waiting for a decision (whatever Teema was suggested),
  background letters and letters on a restricted, TEST or deleted Teema are left out,
  counted (`letters_left_out`) and never logged by title. Accepted cost: the letter
  signal of «current work» thins until letters are paired in Haldus.
- **No TEST Teema is offered, linked or rung about.** The open Teemad offered for
  `related` are NORMAL and real; a proposal stored on a TEST Teema before this is no
  proposal (not counted, listed, decidable or rung about); the nightly watch's model
  never reads a TEST Teema's findings. The watch itself still searches the registers
  for open TEST Teemad without the model, as before.
- **The cap on offered Teemad (400 newest) is counted**, logged as a number and,
  for the new-drafts sweep, written on its run; it is not raised.

## Amendment 2026-11-26 (r95, proposed) — an agenda not yet published is not a failure

**Context.**
- The first complete r94 night (26.11.2026) ended its agenda step PARTIAL.
- valitsus.ee answered **403** for the 26.11.26 agenda page the night before the session. A nonexistent page answered 404, and the 19.11.26 agenda answered 200. The page existed but was not public yet.
- `fetch` treated every answer but a 404 (matched in the error's text) as a source error.

**Decision.**
1. **The structured status is used.** `FetchError.status` decides, never the message text.
2. **One session day ends in one of eight states** (`sources.valitsus.look`):
   - FOUND;
   - NOT_FOUND (every address 404);
   - NOT_PUBLISHED;
   - INACCESSIBLE;
   - UNCONFIRMED;
   - ACCESS_REFUSED;
   - SOURCE_ERROR (429, 5xx, timeout, reading off);
   - PARSE_ERROR (a page that is not this session's commented agenda).
3. **Both address shapes are tried.** A 403 on the first does not stop the second.
4. **A 403 is not evidence by itself.** It is told apart by **one control read per run**, of the newest stored agenda's address:
   - control served (200), session still to come: **NOT_PUBLISHED**;
   - control served, session already past: **INACCESSIBLE** (a warning);
   - control refused (403): **ACCESS_REFUSED** (an error);
   - control unreadable or none stored: **UNCONFIRMED** (a warning).

   Only NOT_PUBLISHED counts as a clean night. Nothing else is ever reported as a successful check.
5. **Nothing is created for a week without an agenda.**
   - No `GovernmentAgenda` row, no model request, no notice.
   - `agenda_of_week` still reads `GovernmentAgenda` only, so the next night reads the week again.
6. **Each run writes one `GovernmentAgendaCheck` per week it considered** (migration register_watch 0010, additive).
   - The row holds: state, session day, HTTP status, address, a short explanation, whether it was the week's last reading with nothing stored (`missing`), and the run's model calls.
   - The step had no run ledger, so Haldus could not tell a delay from a failure. This is the only schema change, and r94 code ignores the table.
7. **Friday's cycle stays the week's last reading** (owner, 25.11.2026); there is still no daytime reading. A week with nothing stored after it is `missing`, and the nightly outcome is PARTIAL whatever the last state was. Valitsuse info's «ei leitud» warning after the window is unchanged.
8. **The machine line adds** `not_published`, `inaccessible`, `unconfirmed`, `missing` and a `note` (session day and HTTP status; never a title). `nightly_outcome` reads them as follows:
   - not published only: SUCCEEDED_NO_NEW «agenda not yet published (date, HTTP 403)»;
   - unconfirmed, inaccessible or missing: PARTIAL with the reason;
   - errors and model failures: PARTIAL as before.
9. **Haldus → Registrite jälgimine shows the eight latest checks.** Waiting states are plain chips; failures and a missing week are problem chips.

**Consequences.**
- The night before publication no longer turns the cycle and the morning monitor red. A refusal by the site, a 429, a 5xx, a timeout or an unreadable page still does.
- The control read costs one request, and only on a night that met a 403.
