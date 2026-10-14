# 0169 — Statistika filter chips narrow every figure they apply to

**Status:** accepted. This records owner decision D2 of the QA remediation
round of 2026-10-14. The decision itself is final; this record only says how the
code keeps it.
**Date:** 2026-10-14

**No migrations**, no data migration, no stored value. It reads the same
populations through the same register parameters as before.

## Context

The independent QA of 2026-10-14 found four ways in which a Statistika figure
and the filter bar above it disagreed:

- The Ülevaade strip and rail counted department-wide whatever chip was
  active. Under «Vastutaja: Sandra» the strip printed «avatud teemat 3» and the
  rail printed «avatud teemat 6» (QA-STATS-02).
- The rail block headed «Aruandlus 2026» printed the *page period's* sends
  under «arvamusi välja», while its two sibling rows counted 2026
  (QA-STATS-01).
- A card or queue link carried a fixed narrowing that *replaced* the chip its
  count had been taken under. Under «Kirje liik: Arhiiv», «Aktiivseid
  teemasid» showed 0 and its link (`liik=FULL`) opened six records
  (QA-STATS-08). The Andmekvaliteet queues «Aktiivne teema ilma valdkonnata /
  asutuseta» counted FULL Matters only, but their links did not say so
  (QA-STATS-06).
- Ülevaade's «Menetlusliigid · kokku 4» sat above rows adding up to 12
  (QA-STATS-07). «Inimesed» counted every Matter of the period under an empty
  state that spoke of «avatud teemasid» (QA-STATS-11).

Owner decision D2 says: visible filters, Vastutaja included, consistently
affect the figures they apply to. Period-specific sections keep their own
windows but label them accurately: «Aruandlus 2026» counts 2026 whatever the
general period is. Metrics are never forced to share a period just to make two
numbers equal.

## Decision

1. **The chips are one dictionary.** `base.chip_params(context)` holds the
   Matter dimensions `visible_matters` applies (liik, päritolu, vastutaja,
   valdkond, hetkeseis, menetlusliik, silt), written in register words.
   `register_url` builds every catalogued link from it. The Ülevaade strip's
   register figure and every rail row now start from it as well
   (`overview_strip._chipped`). The *period* is not a chip there: «Praegu» is
   a point in time, and «Aruandlus <aasta>» names its own year.
2. **A link may narrow a chip, never replace it.** `base.merge_chip_params`
   intersects a metric's own narrowing with the chip. The result is the chip's
   value when the metric allows it (`paritolu` may list several origins), and
   *no link* when the two cannot meet. FULL ∧ ARCHIVE is empty, and the register
   has no parameter for an empty intersection. A 0 with no link cannot
   contradict anything, while a 0 linked to six rows does. Every Statistika
   template already renders an empty link as text; the rail now does too. An
   unreadable owner, which empties every population, also draws no link.
3. **Section windows are labelled, not shared.** The rail's «arvamusi välja»
   is `SUBMISSIONS_SENT` computed on `context.with_period(<this year>)`, with
   every chip kept. On the default period this reuses the strip's own result.
   The strip's «arvamust välja <periood>» keeps the page period, which its
   caption names. «Inimesed» keeps its approved definition, `MATTERS_BY_OWNER`
   over «Nähtavad teemad» (every Matter of the period). Its heading now names
   the period, and its empty state no longer says «avatud».
4. **A table's «kokku» is what its rows are read against.** When the unknown
   bucket is a row, «kokku» is the population (`views._tables`). A table whose
   rows can overlap, such as a Matter in two policy areas, says so beneath it.
   Metric values are unchanged.
5. **Archive coverage is real data** (amends nothing, applies 0024):
   `OPINION_ARCHIVE_MATTER_COVERAGE`, `OPINION_ARCHIVE_UNRESOLVED` and
   `HISTORICAL_SUBMISSION_COVERAGE` ignore candidates and reconstructions on
   TEST Matters. The review queue refuses to settle an archive file on a TEST
   Matter («Arhiivifaili ei saa siduda testandmete teemaga.»). The apply
   skips a reviewed decision that names a TEST Matter, with a reason in its
   report.
6. ADR 0160 §5 is amended: the same-period comparison declines by *date*.

## Consequences

- Under a chip the rail and the strip are smaller. Under a chip that
  contradicts a row, that row is «0» without a link.
- The #435/#440 rule is unchanged: every rail and strip link still carries
  `andmed=paris` exactly once, and counts real data only.
- The Ülevaade screenshot moves (the «Inimesed» heading, «kokku», the overlap
  note). Baselines are adopted by the release coordinator, not here.
- Not changed here: the submission-count bucket metrics
  (`MATTERS_BY_SUBMISSION_COUNT`, owned by the STATS-03 fix) still build their
  links with `liik=FULL`. They inherit rule 2 through `register_url` without
  any edit. `quality.visible_candidates` (Ajaloo sidumine) is not narrowed to
  real data. Its links open the operational review queue, which lists TEST
  candidates, so narrowing only the count would create a count-to-list
  mismatch. No path creates such a candidate today, because the matcher only
  proposes register rows.

## Reversibility

Entirely by code. Reverting `chip_params`/`merge_chip_params` and the
`context=` argument of `strip`/`rail` restores the department-wide rail.
