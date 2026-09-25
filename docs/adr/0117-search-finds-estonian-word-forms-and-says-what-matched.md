# 0117 — Search finds Estonian word forms, and says what matched

**Status:** accepted
**Date:** 2026-09-25

Builds on ADR 0116, whose indexed tiers these new ones join. **`INDEX_VERSION`
moves once**, from `DOKUMENT.1` to `SONAVORM.1`, and a production
`rebuild_search_index` is required after the release.

## Context

Four findings about what search finds and how a result explains itself,
confirmed on the code as of ADR 0116.

* **ENG-031 — Estonian recall.** The stemmer does not reduce many genitives to
  their nominative's stem: `seadus` becomes `seadu`, while `seaduse` stays
  `seaduse`. So a lawyer typing the base form missed the inflected one. There
  was no prefix matching and no diacritic-free matching outside a Matter's own
  aliases. The substring tiers also omitted the three newer kinds: `Kaasamine`,
  `Märge` and a received opinion.

  On a corpus of 51 lawyer-shaped queries (`tests/estonian_recall_corpus.py`),
  21 of the 45 that should find their row did.
* **ENG-081 — merged tags.** `Tag.merged_into` promises that a retired tag stays
  findable through its successor. The indexer never followed the merge, and a
  merge owed no refresh.
* **ENG-083 — what matched.** Several hits on one Teema were indistinguishable:
  a badge and the Teema's title.
  * A received opinion's summary was its *title*, so a match inside it could
    not be quoted.
  * An author's name shared the taxonomy column, so a search for a colleague
    ranked their entries as «Asutus, valdkond või silt».
* **ENG-100 — phone titles.** At 375 px a result title was one ellipsised line
  of about 135 px.

## Decision

**Two recall tiers, both answered by GIN, below the exact ones.**
* **«Täpitähtedeta» (48).** A new vector, `search_folded`, is every searchable
  column passed through `unaccent` and the `simple` configuration, written when
  the row is written. The query's words are folded by the same function. So
  «tahtaja» reaches «tähtaja», and the folding costs nothing per row at query
  time.
* **«Sõna algus» (45).** Each query word of four letters or more is matched as
  a prefix:
  * against the Estonian vector, as its *stem* — `seadus:*` is `seadu:*`, which
    begins `seaduse`;
  * against the folded vector, as the word typed.

  This is where a nominative finds the genitive the stemmer leaves alone. It is
  not a stemmer, and it does not try to be one.
* **The tsquery text is assembled only from `\w+` runs.** No user character
  reaches `to_tsquery` unparsed. A query written in websearch syntax (a quoted
  phrase, `-word`, `or`) is answered by the exact tiers as written.

**The missing kinds join the substring tiers.** `Kaasamine`, `Märge` and a
received opinion are matched by their own titles; a `Kaasamine`'s host and an
opinion's source by their aliases. Organisation names on child rows carry their
diacritic-free forms, as a Matter's always have.

**Authors get a column and a tier.** `people_text` holds an entry's author;
`MATCH_PERSON` («Autor», tier 68) replaces the taxonomy label for those hits,
just under the taxonomy tier they used to share. Organisation hits on an entry
stay «Asutus, valdkond või silt».

**A result says which record matched.**
* **Received opinions:** the row's title is who gave the opinion, and its body
  is the summary, so it is quoted.
* **Every child kind** except documents (which already show their name) carries
  its own title (`SearchResult.source_title`), shown under the Teema's title.
* **Excerpts** also mark words matched by their beginning.

**A merged tag is found through its successor, and a merge refreshes only what
it changes.**
* **Indexing:** a Matter's alias text includes the canonical tag's name and
  aliases beside the assigned tag's.
* **The merge:** changing `merged_into` refreshes, in the admin's own
  transaction, the Matters assigned this tag or any tag merged into it —
  not the whole corpus.
* **Renames and alias edits** of the canonical tag still owe the full rebuild
  they always did.

**Phone titles.** Below 40rem a result title takes the row, up to two lines,
with the badges beneath it; `overflow-wrap: anywhere` keeps a long unbroken
title from widening the page. At 768 px and wider the row is unchanged. The
title carries its full text as a `title` attribute.

## Consequences

* **Recall:** 45 of 45 should-find queries in the corpus now find their row, and
  the 6 documented residuals and negatives are still not found. The residuals
  are stem alternation — `tähtaeg`/`tähtaja`, `üleminekuaeg`/`üleminekuaja`,
  `riigihange`/`riigihanke`, `keskkond`/`keskkonna` — and a query longer than the
  text's form (`ettepaneku` against `ettepanek`). Solving those needs Estonian
  morphology this round does not have. They are listed rather than hidden.
* **Nothing the old query found is lost or demoted.**
  `tests/test_search_differential.py` now holds exactly that against the frozen
  reference, for every persona and 73 queries, and requires the recall tiers to
  have added something.
* **Authorization:** the new tiers go through the same chokepoint. Restricted
  sentinels, probed by the start of a word and without diacritics, are found by
  a lawyer and by no READER or administrator
  (`tests/test_search_pagination_and_access.py`).
* **What the recall costs, measured.** A selective query is unchanged: its
  candidates still come from the indexes (ADR 0116), and the rare, typo,
  document and no-hit probes on the LARGE and IMPORT synthetic corpora are
  within noise of ADR 0116's numbers. A *broad* term — a word in a fifth of
  the corpus, answered by a sequential scan by design — pays for the recall
  on every row. On the compacted LARGE corpus (81,633 rows), its count
  statement went from about 0.25 s to 0.40 s and its ranking statement from
  about 0.40 s to 0.63 s: one more vector per row that matches nothing, and
  about a third more rows that do match and are ranked. Two things keep that
  from being more:
  * **One `@@` per vector for eligibility.** `v @@ (a || b)` finds the same
    rows as `v @@ a OR v @@ b`, but PostgreSQL detoasts a vector afresh for
    every `@@` that reads it; before this, the broad queries read twice the
    buffers they had before the tiers. The tier `CASE` still tells the arms
    apart, on the matching rows only.
  * **Diacritics folded once per search**, through PostgreSQL's own
    `unaccent` in a round trip of its own, so every tsquery is a literal
    that PostgreSQL computes at planning. `unaccent` is only *stable*;
    written into the query, it ran for every row the scan tested.

  LZ4 compression of the vectors was tried and did not help. The residual is
  the price of the recall and is stated rather than hidden.
* **Rebuild required.** Rows built under `DOKUMENT.1` have no folded vector, no
  `people_text` and an opinion's summary in the wrong column, so they are
  ineligible until the one-time rebuild — search returns too little, never
  something confidential.
* **Migration `search/0012`.**
  * Two columns: `people_text`, with a *database* default so the release still
    serving can insert rows, and a nullable vector.
  * Two indexes, built `CONCURRENTLY` in a real migrate.
  * `migration_plan` flags the `index_version` default change as an
    `AlterField`. It emits no SQL; the constant is the contract.

## Alternatives considered

* **An Estonian `unaccent` text-search configuration.** A second stemmed vector
  over folded text would double the Estonian vector and still not solve
  alternation. Folding into a `simple` vector serves both new tiers with one
  column.
* **Trigram matching on body text.** The specification forbids trigram-indexing
  large extracted bodies (§14.1), and it would not solve alternation either.
* **Rewriting historical tag assignments on merge.** Canonical data is not
  rewritten for the sake of a projection. The projection follows the merge
  instead.
