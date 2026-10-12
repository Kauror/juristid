# 0155 — A retired user-facing control stays retired

**Status:** accepted
**Date:** 2026-10-12

## Context

Removed UI has come back more than once. A control the product owner took off a
surface returned during a later refactor or consolidation, because the backend
capability behind it still existed and a shared partial still knew how to draw
it — a `Menetluse link` box reappearing on a form after it had been reduced, a
retired `Tööplaan`-shaped block, a helper sentence restored with a template.
Each time the code was internally consistent; what it contradicted was a
product decision that lived only in an ADR nobody re-read.

The owner's round of 2026-10-12 retired five things from `Uus teema` (the
separate «Koja ettepanek või pöördumine» checkbox and its note, `Menetluse
link`, the `Hetkeseis` prefill sentence and the related-Teema helper sentence)
and moved `Teemaviide` off the header without its «Kopeeri». The capabilities
behind them all remain.

## Decision

**A refactor or consolidation may not resurrect a retired user-facing control
merely because the underlying backend capability still exists. Reintroducing a
retired control requires an explicit new product-owner decision.**

1. **A retirement is a product contract.** When the owner retires a control or
   a sentence from a surface, the change that does it adds a test that the
   surface does not render it — rendered HTML for what the server draws, the
   browser suite for what only a script shows. They are collected in
   `tests/test_retired_ui_contract.py` and, for the 2026-10-12 round,
   `e2e/test_koda_proposal_create_flow.py`; older rounds' absence tests stay
   where they are.
2. **The capability is not the contract.** These tests assert the absence of
   the *control*, never the absence of the model, service or route behind it.
   Backend capability is not deleted to make an absence test pass, and an
   absence test is not deleted to bring a control back.
3. **Bringing one back is a decision, recorded as one.** A future change that
   needs a retired control again amends the ADR that retired it (or writes a
   new one) with the owner's decision, and changes the contract test in the
   same pull request, citing that decision.
4. **Where a test supersedes an old assertion**, it says so in its docstring
   and cites the amended decision, so the history of the contract is readable
   from the test file.

## Consequences

- Consolidation work that touches `Uus teema`, `Muuda teemat` or the Teema
  header fails loudly if it redraws a retired control, instead of passing
  review because the code is self-consistent.
- The contract file grows with every retirement; it is cheap (server-rendered
  checks) and its failures name the decision to read.

## Reversibility

By decision only — which is the point.
