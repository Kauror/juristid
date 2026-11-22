# 0219 — «Monitooring» gathers the new drafts, the Government's agenda and the registers' list

Date: 2026-11-22 · Status: proposed (owner brief 2026-11-22, «make a new tab up top and call it monitooring»; four answers the same day; release awaits owner review)

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
