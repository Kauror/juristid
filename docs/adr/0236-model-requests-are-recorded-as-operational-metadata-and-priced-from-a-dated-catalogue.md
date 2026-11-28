# 0236 — Model requests are recorded as operational metadata and priced from a dated catalogue

Date: 2026-11-28 · Status: Implemented in the r98 candidate (Part I of the r98 brief: R98-01…R98-08).
**Not active in production**: needs release 98, migration `ai_usage/0001`, and the owner's decisions
listed under «Owner decisions». (Verification round R98-C01…C07 added below.) Nothing here switches a model on or widens what is sent to it.

Amends: nothing. Builds on 0217/0218/0219 (the callers), 0227 (Riigikogu document AI stays off),
0231 (the nightly deadlines the transport still obeys).

## Context

The register watch, the new-drafts sweep and the Government agenda each count tokens on
their own run row (`WatchRun.ai_*`, `GovernmentAgendaCheck.ai_*`). Those totals say nothing
about physical attempts (the SDK retries inside one `messages.create`), about cache tokens,
about the model that actually answered, about cost, or about a request that never left the
building. The owner asked to see, from Haldus, what the model was asked, what it cost and
whether it worked, without a new place where legal content is archived.

## 1. Inventory (R98-01) — as of `197eefb1`

| Service (ledger key) | Purpose | Boundary | Model setting | Switch (default) | Data sent | Retry / timeout |
|---|---|---|---|---|---|---|
| `watch_findings` | judge a Teema's register hits | `ai_call.call_items` ← `ai.judge` | `REGISTER_WATCH_AI_MODEL` | `JURISTID_REGISTER_WATCH_AI` (off in code; owner switch) | public facts about the finding, titles of sendable Teemad (`outbound.may_send`) | SDK `max_retries=2` (0 when under 3×timeout left, `_within`); timeout 120 s |
| `candidates` | how a new draft touches entrepreneurs | `call_items` ← `candidates_ai` | `REGISTER_SWEEP_AI_MODEL` | `JURISTID_REGISTER_SWEEP_AI` (off in code) | public draft facts + sendable Teema titles | same; 180 s |
| `government_agenda` | classify agenda items | `call_items` ← `agenda.judge` | `REGISTER_SWEEP_AI_MODEL` | same as sweep | public agenda text | same; 180 s; plus `ai_attempts` per agenda |
| `riigikogu_documents` | full-document parliamentary analysis | `AnthropicModel.answer` (own request, same client factory) | `RIIGIKOGU_DOCUMENT_AI_MODEL` | `JURISTID_RIIGIKOGU_DOCUMENT_AI` (**off; must stay off**, 0227) | would send document text; refused while off | SDK retries via the shared client; 180 s |

Provider: Anthropic only. Requested model: the setting (default `claude-opus-5-5`). Returned
model: `response.model`; the request carries `server-side-fallback` so it can differ. Which
switches are on in production is **not known from the repository**; the production environment
file was not read in this round (read-only inspection was not performed). No other client of any
model exists in `app/` (`grep anthropic` finds `ai_call` and `riigikogu/analysis` only).

## 2. The ledger (R98-02/03)

`AiOperation` — one logical request (stable UUID, service, requested model, run reference,
start/finish, outcome, items presented / classified). `AiAttempt` — one physical HTTP attempt
(sequence, timing, HTTP status, retry class, provider request id, requested and actual model,
input/output/cache-read/cache-creation tokens, stop reason, `fallback_observed`).
`UNIQUE(operation, sequence)`; attempt ids are `uuid5(operation, sequence)`, so a replayed
write is a no-op.

**Seeing retries without changing them.** `ai_call.make_client` hands the SDK a client
whose httpx event hooks call `Recorder.on_request/on_response`. The SDK's retry count, backoff
and the `_within` deadline logic are untouched; one request event is one attempt. The request
that returned an answer carries the measured tokens; an attempt that timed out, was refused
or was retried has `usage_status=UNAVAILABLE` and NULL tokens — never zero. A server-side
fallback inside the provider is not visible to the client; only the answering model is, and
`fallback_observed` is *inferred* from it.

**Outcomes** (operation): SUCCEEDED, SUCCEEDED_AFTER_RETRY, INCOMPLETE (stop reason other than
`end_turn`), UNREADABLE (HTTP ok, answer not usable), FAILED, SKIPPED_NO_TIME (no attempt row;
not billable), INTERRUPTED (still IN_PROGRESS after three hours — result unknown), IN_PROGRESS.
`items_classified` is what the caller accepted (the agenda sets 0 when no row had a usable
verdict), not what the model returned.

## 3. Privacy (R98-04)

Allow-listed columns only. No prompt, system prompt, answer, document text, header, token or
provider error body is stored. An error is reduced to `error_kind` from a fixed vocabulary
(`timeout`, `rate_limited`, `http_<status>`, `connection`, `deadline`, `other`) — the
exception message is never read. The provider request id is kept only if it matches
`[A-Za-z0-9_-]{1,80}`. `run_ref` is an opaque id. A test writes secrets into prompt and error
text and asserts none reaches any ledger column.

## 4. Failure behaviour (R98-08)

* The ledger never decides the business outcome: every recorder call is wrapped, writes in a
  short transaction of its own, and runs outside the callers' business transactions (the
  callers call the model before they open theirs).
* Ledger write fails before the request → the request goes ahead; the result is written at
  the end if the database is back.
* Request succeeds, accounting write fails → the answer is used, **no request is repeated**,
  one `AiLedgerGap` row is attempted and the page says its figures are incomplete.
* Process killed mid-request → the operation row written at `begin()` stays IN_PROGRESS and
  is shown INTERRUPTED (outcome unknown) by `reconcile_stale`, run at every later `begin()`.
* Duplicate event → idempotent (`update_or_create` by operation id; attempts unique).
* If the database itself is down, nothing can be recorded and nothing marks the gap;
  that is a residual limit, stated on the page only after the database returns.

## 4a. Verified against the real SDK (R98-C01, C02)

The hooks are exercised against the **installed** `anthropic 2.9.0` (whose HTTP layer is `httpx2`) over a mock
transport (`tests/test_ai_usage_real_sdk.py`): first-try success, a 529 then success (two attempts, tokens on
the last only), 429 until the SDK gives up (three attempts, all unknown usage), connection errors, timeouts,
and the same script run **with and without** the recorder — identical request bodies, request counts and
number of backoff sleeps. Provider-internal attempts are never invented. **Fallback** is recorded from the
provider's own evidence only: a `fallback_message` entry in `usage.iterations` (the fallback model and its
reported tokens); a different answering model alone is *not* evidence (an alias resolves to a dated id). The
top-level tokens of a fallback-served answer are not attributable to one model's price, so its cost is
unknown, with the reason shown.

**Interrupted.** Each operation stores `expected_end_at` = the step's remaining budget + the supervisor's
grace (300 s) and kill delay (60 s), or the client's worst case outside a bounded step. Before it, an
operation is *running*; after it with no result it is *interrupted* (shown so at once, stored so by the next
request). A supervisor SIGTERM (`StepTerminated`, a `BaseException`) is recorded as interrupted and re-raised
unchanged; a SIGKILL leaves the row to the window. No request is ever repeated because its record is missing.

## 5. Prices and cost (R98-05)

`AiPrice` (model, tier, currency, per-million prices, validity dates, source URL, verification
date) and `AiFxRate` (units per EUR, date, source). **The catalogue is loaded from a reviewed file** (`price_catalogue.toml`) by `seed_ai_prices` (dry run
unless `--apply`; idempotent; never run by a deploy). It holds only what the official page stated for
**Claude Opus 5.5** (USD per million tokens: input 4, output 20, cache read 0.20; 5-minute cache write 5 and
1-hour 8 are *not* loaded because the cache entry's TTL is not recorded per request, so a billed cache-creation
token stays unknown), the page's URL and the verification date 2026-11-28, plus one ECB rate (USD 1.0957 per
EUR, 2026-11-27) read once. The page is undated and does not print the API identifier — the mapping to
`claude-opus-5-5` rests on the session's model list — and says nothing about fallback billing or whether
`input_tokens` includes cache tokens (the usage fields are separate counters; Juristid sends no `cache_control`,
so none is expected). Until `seed_ai_prices --apply` is run on a database, every cost there reads
«Maksumus teadmata» and tokens are still shown. `set_ai_price` remains for corrections. A new price closes the previous row the day
before; the estimate for an attempt uses the row in force on its Europe/Tallinn day, so a later
price never rewrites it. Cost = Σ tokens × price ÷ 10⁶ (Decimal). Unknown cost when: usage not
measured; no price for the answering model on that day; a billed cache category has tokens but
no price; a non-EUR price has no stored rate on or before that day. Missing cache fields make
the figure a lower bound (flagged). Public list price ≠ invoice: negotiated discounts are not
inferred and the page says «hinnanguline».

## 6. Page and budget (R98-06/07)

Haldus → Integratsioonid → AI kasutus, for a verified technical administrator (the Lovable
predicate), with periods (today, 7 days, this month, previous month, custom), service / model /
outcome filters, daily, service and model tables, a paginated attempt table and a CSV through
`csv_safe_row`. Every aggregate and the table use the same `cost_of`; a test reconciles them
against an independent enumeration. Where the period precedes the first ledger row the page
says earlier use is *unknown*, not zero. Budget: `JURISTID_AI_BUDGET_WARN_EUR` and
`..._ESCALATE_EUR`, both empty by default (no value invented). Crossing one shows a notice;
**nothing is switched off**. Any unknown-cost attempt in the month marks the total incomplete.
No notification is sent, so there is nothing to duplicate.

## 7. Retention and growth (R98-08)

Proposed: keep detailed rows 13 months, then keep per-month aggregates and the price rows.
**Not implemented and nothing is deleted.** Growth: one operation per batch of 50
findings/candidates or per agenda, a handful per night → ≈ 10 operations and ≈ 10–30 attempts
per night, ≈ 4–11 thousand attempt rows a year (≈ 1–3 MB with indexes). The MCP audit retention
rules are unchanged.

## Owner decisions

1. Enter verified prices (and an EUR rate if a USD price) — until then no cost is shown.
2. Monthly warning / escalation thresholds (or none).
3. Approve the 13-month detail retention before any deletion job is written.

## Measured (R98-C07, synthetic, local PostgreSQL)

4 database writes per one-attempt operation (the begin upsert, its reconcile update, the finish upsert and the
attempt insert); the page runs 18 queries from 300 to 12,000 attempts; `summarise` over 10,950 attempts (a
year at ten times the expected volume) takes ≈ 0.6 s; the page renders 12,000 attempts in ≈ 0.8 s. Concurrent
finalization of one operation by eight threads leaves one row each and no gap. An accounting failure is shown
on the AI page and as a Haldus overview line; a database that is down cannot record its own absence.

## Retention design (R98-C05) — nothing is deleted

Target: 13 months of detail. `manage.py ai_usage_retention` is a **dry run only** (it has no apply mode): it
prints what would be affected and per-month token aggregates to keep. Prices, exchange rates and
`AiLedgerGap` are never candidates, and security audit evidence is a different system. Growth ≈ 4–11 thousand
attempt rows a year (a few MB). Deletion needs the owner's decision and a reviewed, separate step.

## Related records (r98)

0237 nightly history and freshness · 0238 Lovable parity · 0239 document processing and OneNote · 0240 backup
assurance and off-host options.
