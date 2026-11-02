# The Lovable synchronisation tools (docs/adr/0201 §9)

Five read-only MCP tools over `app.legacy_import.lovable_observability`, the one
read service that Uuendused → Lovable, Haldus → Lovable sünkroonimine and
`manage.py lovable_sync report` also ask. They read what the pilot's nightly
synchronisation (ADR 0192) and the wider observation (ADR 0201) already
recorded in Juristid. **Lovable is never contacted**: no tool, reader or
command here has a transport. Nothing is written — the tools run in the `mcp`
process, as its SELECT-only role, on read-only connections.

## The tools

| Tool | Arguments | Answer |
| --- | --- | --- |
| `get_lovable_sync_status` | none | `status(user).as_dict()`: mode, APPLY authorised, cut-over, observation on, last scheduled / successful / failed run with the failure **code**, snapshot and observation freshness, the audit ledger's availability, pilot size, live and operational topic counts, pending review, Estonian warnings, the overview's indicators |
| `get_lovable_sync_history` | `since?`, `until?` (YYYY-MM-DD), `population?` `PILOT` (default) \| `OPERATIONAL` \| `ALL_LIVE`, `outcome?` (PILOT only: `APPLIED`, `NOT_APPLIED`, `REVIEW`, `CONFLICT`, `SAME`, `RESOLVED`, `NOT_COMPARED`, `LOCAL`, `FAILED`, `UNKNOWN`), `field?`, `page?` 1–100 | `nights(...)` as a page: 10 readings per page, newest first, each with its topics and fields |
| `get_lovable_sync_matter_changes` | `matter`: a reference such as `2026_243` (also `2026/243`), a Matter UUID or `matter:<uuid>` | `matter_changes(...)`: per field last applied / Juristid now / Lovable last observed, nights, decisions, source events, pending review — values whole |
| `get_lovable_observed_changes` | `since?`, `until?`, `population?` (default `ALL_LIVE`), `kind?` `NEW` \| `CHANGED` \| `MISSING` \| `BACK` \| `RENUMBERED`, `field?`, `mapping?` `PILOT_LINKED` \| `REFERENCE_CANDIDATE` \| `UNMATCHED`, `page?` 1–100 | `observed_changes(...)` as a page: 50 topics per page, totals by kind and mapping over every page |
| `get_lovable_sync_readiness` | none | `readiness(user).as_dict()`: source, identity, per-field rules and risks, coverage, the **simulated** plan, conflicts, blockers, owner decisions. An assessment, never an activation |

`field` is one of `lovable_review.FIELD_ORDER` (title, owner, stage,
next_action_text, next_action_date, opinion_deadline, received, sent,
sent_date, addressee, legal_act, koda_initiative, operational_scope,
commencements, onenote_link). Every vocabulary in the schema is read from the
code that owns it (`app/mcp_server/schemas.py`, `LOVABLE_VOCABULARY`).

Every input model forbids properties it does not name. A malformed argument is
refused before any read with the field and the rule only, never the value
(`Invalid arguments for get_lovable_sync_history: page: less_than_equal. …`).

## What the readers add — and what they do not

The readers (`app/mcp_server/readers.py`, end of file) compare, count and decide
nothing. They:

* check `may_read_business_content` first: a caller without a business role
  gets the same «Nothing with this id or reference is available to you in
  Juristid.» as from every other business tool;
* refuse, rather than let the service quietly change, a window wider than
  `MAX_WINDOW_DAYS` (31) — also an open-ended `since` more than 31 days ago —
  or one whose `since` is after its `until`; and `outcome` with a population
  other than PILOT;
* answer `get_lovable_sync_matter_changes` with the standard «not found» when
  the service answers None: an unknown Teema, one the caller may not open, a
  deleted one and one not in the pilot are indistinguishable;
* make every `…_url` the service built absolute (`JURISTID_PUBLIC_ORIGIN`);
* answer a page past the end with an empty page of that number
  (`has_more: false`), as every other paged tool does;
* hold every answer to `LOVABLE_ANSWER_MAX_CHARS` (60,000 characters of JSON)
  by leaving items out from the **end** of its lists — readings' topics oldest
  reading first, then readings; a Teema's decisions, source events, nights and
  pending items in that order — and say so: `truncated: true` and
  `omitted: {"topics": n}`. The page's `total` and `totals` still count
  everything. An answer that cannot fit is refused with a safe message asking
  for a narrower question.

Who sees what is the service's (ADR 0201 §6): a pilot Teema's title and values
only when `Matter.objects.visible_to(user)` allows; otherwise counted in
`hidden`, never named. A source topic with no approved link to a Juristid Teema
shows its source number, kind and field **names** only.

The call log line (`app.mcp_server.calls`) carries the tool, the user id, the
outcome, the duration and the item count — never a reference, title or value.

## `lovable_sync report`

The approved diagnostic sharing the same service:

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web python manage.py lovable_sync report --json
```

It prints `status()` and `readiness()` asked as nobody (no Teema is visible, no
plan row is listed) and reduced to numbers, booleans, codes (`COMPARE`,
`HTTP_401`, `FRESH`) and ISO times: lists become counts, or a mapping by
indicator key or field name; every sentence, title, reference and value is
dropped. It reads only.

## Deploying, and refreshing the connector

The tools ship with the release that carries migration `legacy_import` 0020.
After that release is deployed (the release runbook; step 9 replaces `mcp`):

1. **The `mcp` role reads the new tables.** Its default privilege on the
   owner's future tables already grants SELECT on tables a migration creates
   (*OPERATIONS.md*, step 5). Prove it as that role, from `mcp` itself:

   ```bash
   docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T mcp python manage.py lovable_sync report --json
   ```

   It must print one JSON object. `permission denied for table
   legacy_import_…` means the default privilege was missing: re-run the
   idempotent `mcp_db_role --apply --password-stdin` from `web`
   (*OPERATIONS.md*, step 5), then `restart mcp` and repeat.
2. `check_mcp_server` passes inside `mcp`, and `/readyz` of `mcp-tunnel` says
   `ready` (*After recreating `mcp`*).
3. **Refresh the connector** — ChatGPT keeps a frozen copy of the tool list
   until a workspace admin refreshes it: **Admin Console → Plugins → Juristid →
   Refresh**; check that the list now shows thirteen read tools (plus the write
   tools if writes are on), the five `get_lovable_*` among them and every one
   marked read-only; then **Publish** again. Until then nobody sees the new
   tools, and the existing ones are unchanged either way.
4. Smoke, from a granted colleague's ChatGPT: «Mis seis on Lovable'i
   sünkroonimisega?» must call `get_lovable_sync_status` and answer with the
   mode and the freshness; «Mis muutus Lovable'is teemas <a pilot reference>?»
   must call `get_lovable_sync_matter_changes`.

Rolling back the code removes the tools; refresh and publish the connector
again so ChatGPT stops offering them.
