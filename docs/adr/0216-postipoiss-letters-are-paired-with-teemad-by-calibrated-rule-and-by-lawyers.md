# 0216 — Postipoiss letters are paired with Teemad by a calibrated rule and by lawyers

Date: 2026-11-17 · Status: accepted (owner, 2026-11-17)

## Context

Postipoiss is the Chamber's SharePoint register of outgoing letters, numbered
«V2014-0072» since 2009. In November 2026 every outgoing letter was exported
once, the signed containers (ASiC-E, BDOC, DDOC) opened, and the letters sorted
into opinions and other correspondence. Of 2,395 registered opinions, 1,840 have
their file — from Postipoiss itself (1,387), or recovered from the policy
department's folders, koda.ee and ministries' public document registers, each
recovery proven by the letter's own number and date («Meie 15.10.2010 nr
4/272»). 555 registered opinions have no file anywhere we could reach; most are
2009–2015, when Postipoiss stored only the row.

Juristid's Teemad come from the department's register («Tööd eelnõudega»,
reference «2014_85»). The opinion archive's matcher (ADR 0019, 0055) reads only
Teemad that carry an Excel register reference and file names in the opinions
archive's own format; since the Lovable register replacement (ADR 0203) most live
Teemad carry neither, and Postipoiss letters are not named that way.

## Decision

1. **Matching happens offline and arrives as a sealed package.** A manifest
   (`manifest.json`) lists each letter — Kirja ID, register row, registered and
   letter dates, title, recipient, text, files by SHA-256 — with the proposed
   Teema reference, a tier and the signals behind it. `manage.py
   postipoiss_import plan` prints its digest; `load` needs that digest, like
   every real-data import, and decides nothing.
2. **Letters are their own rows; files live in the archive's store.**
   `PostipoissLetter` holds a registered letter with or without bytes, because
   a registered letter without a file is still a fact. Files are
   `OpinionArchiveBinary` rows — same store, key scheme and download route as
   the opinions archive (ADR 0023). `PostipoissPairing` is the decision.
3. **Only the calibrated tier applies without a person.** Tier A = sent date
   within one day, the same recipient (reviewed alias table) and at least one
   shared topic word, with a single candidate. Calibrated on 2026-11-17 against
   independent ground truth — the sealed OneNote capture, where 318 letters sit
   on a Teema's page by identical bytes or by the letter's own number: no
   confirmed tier-A error in 208 testable cases (every disagreement was a
   OneNote page reused for a later stage of the same law), and 1 error in a
   100-pair hand sample of 2011–2019. Tier B (date and recipient alone) was
   about 1 in 10 right and is never automatic. `auto-apply` is its own phase,
   run after a lawyer confirms the sample, and has a `--dry-run`.
4. **Applying a pairing goes through the opinion archive's evidence path.** The
   Matter lock, the existing `DocumentVersion` when the Teema already holds the
   exact bytes, `create_document` + `add_evidence_version` otherwise, then a
   sent `Submission` dated with the letter's own date (date precision), the
   recipient resolved conservatively, and every file linked as an archive
   letter of the Teema. A letter without a file or date links its Teema and
   writes no Submission: a sent opinion without its final text is refused by
   the database, rightly. A TEST Teema is refused.
5. **Seven days is one act.** A Teema that already holds a sent opinion within
   seven days of the letter gets the letter attached to it, not a second
   opinion. Statistika's one reader applies the same window: an imported
   «välja saadetud» within seven days of a native fact on the same Teema is the
   same opinion, and the native fact keeps the register's request deadline
   when it has none of its own (owner, 2026-11-17).
6. **Lawyers decide the rest in Haldus → Ajaloolised kirjad.** Every business
   writer (SPECIALIST, DEPARTMENT_HEAD) may accept the proposal, choose another
   Teema by its reference, leave the letter as background, or take a
   background decision back (owner, 2026-11-17). Each decision names its
   person. Reading follows business read. An applied pairing is not reopened
   from this page: it wrote a sent opinion, and withdrawing that is the Teema
   page's act. Pairing is allowed on a closed Teema: almost every historical
   Teema is closed, and a letter records what was sent, not new work. The
   letters are department-wide records like the opinion archive; a Teema a
   pairing names is shown only through `Matter.objects.visible_to`.
7. **Background is a state, not a deletion.** A letter with no Teema stays
   listed and searchable by ID, title, recipient and text, with its files.
8. **The archive serves a file under the suffix of its recorded type.** The
   opinions archive was PDFs only; a Word letter served as `.pdf` opened broken.

## Consequences

- Statistika gains historical opinions only where they were not already
  counted; the merge rule can move a few existing figures where a native fact
  and an imported one were 2–7 days apart (previously two opinions, now one).
- The 555 letters without a file appear in Ajaloolised kirjad and can be paired,
  but never become sent opinions on their own.
- A future native upload of a letter for the same Teema and week joins the
  historical one rather than duplicating it.
