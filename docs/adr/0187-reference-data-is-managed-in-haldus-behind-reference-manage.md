# ADR 0187 — Reference data is managed in Haldus → Klassifikaatorid, behind `reference.manage`

- Status: Proposed (owner's brief «Admin & Workflow R1», workstream B, 2026-10-23)
- Related, unchanged: 0005 (authorization chokepoint), 0029 (reference data is governed and
  additive), 0037 (business-write boundary), 0041 (search freshness, SEARCH-001), 0063 (one
  organisation catalogue), 0070 (Õigusakt), 0093 (opinion keywords), 0145 (capabilities),
  0180 (audit provenance), 0181 (one internal read policy)
- Amends: 0145 §3 adds one capability to the vocabulary. Nothing else.

## Context

The department's reference vocabulary — the institution catalogue, the tag vocabulary and
the controlled vocabularies (Valdkond, Õigusakt, Hetkeseis) — had exactly two write paths:
the counterparty fields, which may only *reuse or create* an institution through the one
resolver (`app.organisations.services`), and Django's own admin, which no business role
reaches and which would let anybody who did reach it delete a row, point `Tag.merged_into`
in a circle or rename a reviewed ministry out from under `verify_reference_data`. Nobody
could correct a misspelt institution, record that «RIA» means the Riigi Infosüsteemi Amet,
or retire a tag, without a developer.

## Inventory (B1)

| Vocabulary | Editable today | Seeded / controlled | Referenced by | Reindex | Reporting | Delete policy |
| --- | --- | --- | --- | --- | --- | --- |
| `Organisation` + `OrganisationAlias` | Django admin (any field, inline aliases); quick-create and typed counterparty names through `get_or_create_organisation` (create only) | 27 reviewed public bodies via `manage.py reference_data plan/apply` (ADR 0029); the rest typed by lawyers | `Matter.addressee_organisation`, `MatterSourceOrganisation`, `Entry`, `MatterExternalPosition`, `SubmissionRecipient`, `SubmissionJointSubmitter` (all `PROTECT`), `predecessor` (`SET_NULL`) | Yes: rename → `ORGANISATION_RENAMED`, any alias write → `ORGANISATION_ALIAS_CHANGED` debt (SEARCH-001) | Statistics by institution, export | Never deleted or auto-merged; no `is_active`; `valid_to` is a fact about the body, not a switch |
| `Tag` + `TagAlias` | Django admin only | Department-governed (spec 14.7); none seeded in production | `TagAssignment` (Matter, `PROTECT`), `SubmissionTagAssignment`, `merged_into` | Yes: rename → `TAG_RENAMED`, alias → `TAG_ALIAS_CHANGED` debt; a merge reprojects its Matters in-transaction (`refresh_on_tag_merge`) | Statistics tag filter (active tags) | Never deleted once referenced; merge = `merged_into` + `is_active=False` (DB constraint); deprecate = `is_active=False` + `deprecated_at` |
| `PolicyArea` | Django admin | Reviewed migration `taxonomy/0002` (ADR 0029) | `Matter.policy_areas` (plain M2M) | Yes: rename and delete owe a rebuild | The primary statistic «Teemad valdkonniti» | Retired, never deleted |
| `LegalInstrumentType` | Not registered anywhere | Reviewed vocabulary from the register's ÕIGUSAKT column (ADR 0070) | `Matter.legal_instruments` | No | Statistics, export | Retired, never deleted |
| `StageVocabulary` + `LegacyStatusMapping` | Django admin | Reviewed with the lawyers, seeded by command | `Matter.stage`, stage episodes, legacy mappings (`PROTECT`) | No | Stage statistics, Osakond | Never deleted while referenced |
| Code enums (`OrganisationType`, `AliasType`, `Track`, `Disposition`, …) | Code only | Code | Columns | — | Yes | Code review |

Existing semantics this round reuses rather than reinvents: normalised-exact identity and the
identity advisory lock (`find_matches`, `lock_identities`), the reviewed baseline
(`PUBLIC_REFERENCE_ORGANISATIONS`), the read-only duplicate report (`identity_report`), the
model-graph reference counter (now public as `duplicates.references_to`), the tag merge and
deprecation fields with their check constraints, the SEARCH-001 debt handlers and the
`updated_at` optimistic-concurrency token.

What did **not** exist: an organisation merge, an organisation deactivation, any Haldus UI,
and any capability for this work.

## Decision

1. **One capability, `reference.manage`** («Klassifikaatorite haldus»). Default holder
   DEPARTMENT_HEAD; grantable to SPECIALIST, DEPARTMENT_HEAD and ADMINISTRATOR; not to
   READER. Listed with the business capabilities (granted without `accounts.delegate`,
   no administrator session needed), and deliberately **outside `REQUIRES_BUSINESS_WRITE`**
   so a technical administrator granted it holds it — while still writing no Teema, because
   no Matter write reads it. No other capability implies it.
2. **Pages at `/haldus/klassifikaatorid/`** (namespace `reference_data`), extending the
   Haldus shell. Every route is `login_required`, then `reference_actor(request)`; anybody
   without the capability gets a 404. The actor is `request.user`; a shared-gate persona
   that holds the capability acts as that persona with the provenance of ADR 0180
   (`acting_persona`, no `authenticated_principal`). The integrator adds a verified
   administrator fallback when Haldus has one.
3. **Organisations** (`app/organisations/administration.py`): search by folded name,
   spelling or registry code; create (a name or registry code the catalogue already answers
   to is *refused, naming the row*, under the identity lock); correct the name (old name kept
   as a `HISTORICAL_NAME` spelling unless the person says it was a typo; a name another body
   answers to is refused); add a spelling (refused when it is the body's own name, already
   its spelling, or another body's name or spelling); remove a mistaken spelling (never a
   historical name). References are shown per relation before anybody decides anything.
4. **The reviewed baseline is not edited here.** A body that *is* a baseline entry (by name
   or recorded spelling, the way `verify_reference_data` finds it) cannot be renamed and
   loses none of its reviewed spellings; the page says the change needs a reviewed one, and
   that a reorganisation is a predecessor/successor link, not a rename.
5. **No organisation merge or deactivation — a documented gap.** The catalogue has no
   supported semantics for either (no merge pointer, no `is_active`; rows are held by
   `PROTECT` from six tables). The pages show what points at each row and the identity
   report of rows the key calls one institution, and leave the merge to a reviewed change.
   A future merge needs its own ADR: which row survives, how every relation is repointed
   under the Matter locks, and how the loser's names become the survivor's spellings.
6. **Tags** (`app/taxonomy/administration.py`): browse with guidance and status; create
   (key slug, folded-name duplicate refusal); edit name and guidance (old name kept as a
   spelling by default; the key never changes); add/remove spellings; **merge** through
   `merged_into` + `is_active=False` + `deprecated_at`, into an active canonical tag only,
   both rows locked in primary-key order; **deactivate / reactivate** through `is_active`.
   No assignment is moved and nothing is deleted: a record keeps the word it was classified
   with, and search finds it through the canonical tag. A merged tag is frozen. All writes
   serialise on one transaction-scoped advisory lock (namespace 24603), taken first.
7. **Controlled vocabularies are read-only.** Valdkonnad, Õigusakti liigid and Hetkeseisud are
   listed with status and (for readers of Teemad) usage, under the sentence «Muudatus vajab
   arendaja kaudu ülevaadatud muudatust». Their changes stay migrations and reviewed seeds.
8. **Every change** is one service call: `transaction.atomic`, the row locked `FOR NO KEY
   UPDATE`, the page's `updated_at` token compared under the lock (missing token = stale),
   saved through the model so the SEARCH-001 handlers record their obligation, and one
   `ChangeEvent` (new types `ORGANISATION_CREATED`, `ORGANISATION_RENAMED`,
   `ORGANISATION_ALIAS_ADDED`, `ORGANISATION_ALIAS_REMOVED`, `TAG_CREATED`, `TAG_CHANGED`,
   `TAG_ALIAS_ADDED`, `TAG_ALIAS_REMOVED`, `TAG_MERGED`, `TAG_DEACTIVATED`,
   `TAG_REACTIVATED`; migration `audit/0039_r1_admin_reference_reminder_events`, choices only) with no
   Matter, the vocabulary row as `obj`, and identifiers plus before/after names in the payload.
9. **Usage counts are business information.** The number of Teemad and opinions carrying a
   value is shown only to somebody `may_read_business_content`; a technical administrator
   manages the words without seeing how the work is distributed (ADR 0181).

## Consequences

- The department head can keep the catalogue and the tag vocabulary clean without a
  developer, and every change says who made it.
- Renaming an institution leaves the corpus stale until the freshness consumer runs, exactly
  as before; the obligation is recorded in the same transaction (tested).
- Django admin still exposes the same models to whoever reaches it; narrowing it is a
  separate decision and is not taken here.
- Organisation merge and deactivation remain unavailable by design until a later ADR.
