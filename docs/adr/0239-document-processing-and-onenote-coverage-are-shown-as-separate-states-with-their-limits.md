# 0239 — Document processing and OneNote coverage are shown as separate states, with their limits

Date: 2026-11-28 · Status: Implemented in the r98 candidate (R98-14, R98-15). **Not active in production**
until release 98. No migration, no reprocessing, no new job.

## Decisions

1. **Haldus → Dokumentide töötlus** (`app.documents.processing_health`), counts and ages only: waiting
   (with the oldest one's age), processing (a claim older than `EXTRACTION_STALE_CLAIM_MINUTES` is shown
   **interrupted**, not running), read, failed **by reason**, not processed on purpose (a signed ASiC-E
   container is intentional; other unsupported formats are counted separately), and read once while a Teema
   was created (`INTAKE_READ`, no persistent extraction). The states are mutually exclusive and are never
   added into one «problems» figure; an intentionally unprocessed file is never called a failure.
2. **Failure reasons** are read from the sentence the isolation layer wrote (`kestis üle` = wall-clock
   limit, `mälu` = memory limit, `ootamatult` = the reader died, …), not from a stored code — the schema keeps
   none. A reason that matches nothing is «Muu põhjus».
3. **Service marks.** The intake reader's loop mark (`Heartbeat`) says whether its loop is turning. The
   parser sockets are probed with a one-second connect **only when this process can see them**; otherwise the
   page says «ei kontrollitud». No socket or file path is ever shown.
4. **OneNote** (`legacy_import.onenote_diagnostics`) regroups `coverage_summary()` and `held_counts()` into
   imported / captured-not-applied / awaiting capture / held for review / changed since capture / failed, with
   a link to the existing review workflow. «Unknown coverage» is printed: Juristid keeps no complete list of
   the notebook, so «the archive is captured» is never claimed (`archive_complete` is False).

## Limits

* **No per-file duration is recorded**, so the page shows none and cannot say how a large scan fares against
  the 240-second limit; that needs a measurement on the production host (owner-accepted, still open) and an
  instrumentation change, both outside this release. Representative synthetic workloads were not measured on
  the target host in this round.
* No resource-exhaustion test was run against production, and no historical document was reprocessed.
