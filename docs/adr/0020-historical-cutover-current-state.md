# 0020 — The historical cutover, and what a closed archive row may claim

- **Status:** Accepted — implemented on the `stage-2i-historical-cutover-state` branch
- **Date:** 2026-08-21
- **Builds on** ADR 0012 (register import), ADR 0015 (historical corpus), ADR 0017 (metrics).

## The problem the sources handed us

After the owner backfill, one question was still unanswered: of the 2455
imported register Matters, which are actually current work?

The register's own answer does not survive contact with the data. Three
measurements, all taken read-only against the real corpus:

**Closure was not recorded before 2025.** Explicitly closed rows exist in 2026
(10) and 2025 (35). Every year from 2011 to 2024 contains **zero** — not one
closed row in fourteen consecutive years. That cannot mean nothing was ever
finished; it means the register had no closure concept then. So the mechanical
reading "not explicitly closed, therefore still open" is not a conservative
default, it is a false one: applied across all years it classifies **2354 of
2455 Matters (96%)** as promotable current work.

**No Matter appears in more than one register year.** 2455 Matters hold 2455
source references. The register opens a fresh row per sheet rather than
carrying a file forward, so there is no cross-year carry-over population to go
looking for, and "latest source year" is simply each Matter's only year.

**Deadlines are year-bounded.** Matters whose response deadline is still in the
future: 8 in 2026, and **0 in every other year**. Each older year's furthest
deadline lands inside its own year — 2024 ends at 2024-12-31, 2011 at
2011-12-28. Outside the current year there is no mechanical signal of live work
anywhere.

The two columns that might have carried such a signal cannot: `JÄRGMISEKS`
exists only in the 2025–2026 era contracts and `HETKESEIS` only from 2023, so
their absence on a 2014 row is a schema fact, not evidence about the work.

## The decision

A pre-cutover imported register Matter **defaults to historical**: no longer
current work, unless a person says otherwise. 2026 is the cutover year and the
only whole-year current-register activation reviewed for apply.

This is a *default*, not a discovery. It asserts nothing about the individual
file beyond "nobody has said this is still live".

## What the default may and may not assert

The operation moves exactly one field, `is_open`, and the resulting shape is

    record_mode = ARCHIVE
    is_open     = False
    disposition = ""
    closed_at   = NULL

which the existing closure constraint was written to permit — "an archive row
is never forced to invent a closure reason it does not have". It reads
**historical at cutover; exact closure fact unknown**.

It deliberately does **not** write a disposition, a `closed_at`, a `closed_by`,
a `NextAction`, a `Submission`, a sent date, an outcome or a work victory. Every
one of those would be a plausible-looking fabrication that nobody could later
distinguish from a recorded fact. In particular the cutover date is not the
closure date, and no rule may substitute one: not the year's final response
deadline, not 31 December, not the import timestamp, not a file or OneNote
timestamp.

`close_matter()` is untouched and is not used here. That operation means a
person is closing live work now, which is why it rightly demands a disposition
and stamps the current time; weakening it to accommodate migration history
would corrupt the meaning of every real closure.

## Audit

A dedicated `MATTER_HISTORICAL_CUTOVER_CLOSED` event, not `MATTER_CLOSED`,
whose label says "ajalooline kirje: enam mitte jooksev töö". Its payload
carries only operation metadata — operation, version, cutover year, source
year, rule, and the `is_open` transition — never a title, owner or source cell.

It stays out of `matters.timeline.TIMELINE_EVENT_TYPES`. The event's timestamp
is when the normalisation ran, so a line in the professional chronology reading
"closed" on the cutover day would assert exactly the fact this decision refuses
to claim. Audit visibility is enough.

## What is preserved

Archive rows remain fully preserved: owner, stage, dates, organisations,
documents, entries, submissions, structured facts, provenance and visibility
are untouched, and `reporting_year` keeps its original value so annual
reporting identity does not move. Retired Matters stay in the search
projection, stay visible in Teemad, and stay reachable on their own page
subject to normal authorization. Closing a row is not hiding it.

Rows that already carry a **real** closure are a strict no-op: an existing
disposition, reason, timestamp and closing person are never rewritten into a
cutover default. Rows somebody has activated to FULL are a `CURRENT_EXCEPTION`
and survive re-runs, so a manual attestation is never undone by running the
operation again.

## The exception path

An older Matter may still turn out to be live. That is decided **per Matter by
a person**, never by activating another whole year:

    reopen_matter()  →  promote_matter_to_full()

in that order, because promotion refuses a closed Matter. The narrow wrapper
`reactivate_historical_matter()` performs both atomically and requires a
written attestation. It refuses a Matter carrying a real recorded closure —
reversing somebody's professional decision is their call through the ordinary
reopen route — and it creates no `NextAction`, because what happens next is a
decision for whoever picks the file up. There is deliberately no bulk
"promote old records" API.

A row carrying an open structured `NextAction` is held back as
`REVIEW_REQUIRED` rather than retired. Today's production has no NextActions at
all, so this changes no current number; it protects re-runs and future
environments, where a bulk default has no business erasing explicit modern
operational work.

## Consequences

The measured production shape at the time of writing: 2263 pre-2026 register
Matters, of which roughly 2228 would become historical, 35 are already
explicitly closed, and none is a current exception or needs review. The
authoritative figures are whatever the production dry-run reports.

**Next year's rollover is a new decision, not an automatic rule.**
`REVIEWED_HISTORICAL_CUTOVER_YEARS` is a reviewed code constant for the same
reason `REVIEWED_CURRENT_YEARS` is: a moving-year window would quietly retire a
live file every January without anybody choosing to.

Historical reporting is unaffected — the archive stays in its reporting
population, under its original year.
