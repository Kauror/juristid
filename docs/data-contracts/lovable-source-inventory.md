# Lovable source inventory (docs/adr/0201)

Verified **2026-11-02** against the deployed Lovable database (Loo Command
Center) with read-only catalogue queries only (`BEGIN TRANSACTION READ ONLY`,
`information_schema`, `pg_policies`, counts). Nothing in Lovable was changed.
"Readable" means readable by the synchronisation's **profile-less signed-in
service identity** under the row-level security policies in force that day —
not by the owner's connector, which runs with far wider rights.

Counts that day: 2,511 live topics, 524 of them operational, 1 deleted; 65
commencements, 6 responsible changes, 52 stage links, 0 topic links, 2,497
source rows; 1,081 audit events since the ledger began on 2026-10-11 (144 of
them since 27.10, on 97 topics), with 1,723 field rows in the last 30 days.
Live topic rows total about 1.3 MB.

## Classification

| Status | Meaning |
|---|---|
| **Collected** | Read and stored (pilot snapshot and/or wider observation). |
| **Excluded** | Readable, deliberately not read. |
| **Unavailable** | Not readable by the service identity. |
| **Owner** | Collection needs an owner decision first. |

## `topic` (RLS: signed-in users read rows with `deleted_at IS NULL`)

| Column | Meaning | Type | Status | Personal / sensitive | Juristid counterpart | Mapping | Auto-sync ever safe? |
|---|---|---|---|---|---|---|---|
| id | Stable topic identity | uuid | Collected | no | `LovableSyncState.source_id`, `MatterSourceReference` | exact (pilot only) | n/a — identity |
| nr, aasta | Register number `YYYY_N`, year | text, int | Collected | no | `Matter.reference_year/number` | exact for the pilot; a match by number alone is a *candidate* | never re-linked automatically |
| teema | Title | text | Collected | no | `Matter.title` | exact when the pilot kept the source sentence | yes, when kept (ADR 0192 §5) |
| vastutaja, uus_vastutaja | Responsible lawyer (by name) | text | Collected | **personal (names)** | `Matter.owner` | exact name resolution only | yes, when exactly one assignable user |
| jargmiseks | Next step wording | text | Collected | no | `NextAction.text` + kind | approximate — DO/WAIT/MONITOR is a human reading | no (held) |
| tegevuse_tahtaeg | Next step day | date | Collected | no | `NextAction.due_on` | exact when wording unchanged | yes, under §5's conditions |
| hetkeseis | Stage | text | Collected | no | `Matter.stage` | none — no approved vocabulary mapping | owner decision |
| arvamuse_tahtaeg | Opinion deadline | date | Collected | no | `Matter.response_deadline` | approximate | no (held) |
| sisse | Received | date | Collected | no | intake date | approximate | no (held) |
| valja, valja_kuupaev | Sent mark and day | text, date | Collected | no | a sent `Submission` | none — a source assertion, never a send | **never** |
| kellele | Addressee | text | Collected | no | Submission addressees | approximate | no (held) |
| koja_ettepanek | Koda's own proposal | bool | Collected | no | Matter origin | approximate | no (held) |
| oigusakt | Legal act | text | Collected | no | legal instruments | approximate | no (held) |
| onenote_link | OneNote link | text | Collected | no | newest `MatterSourceReference` | exact (`https://`) | yes, as a new observation |
| operatiivne_ulatus | Operational scope | bool | Collected | no | — | none | no (held) |
| version, updated_at | Source version, time | int, tstz | Collected | no | provenance | exact | n/a |
| joustumised_hallatud | Commencements managed flag | bool | Collected (observation only) | no | — | none | no |
| markmed | Source's own notes | text | **Excluded** (ADR 0192 §5) | may hold personal remarks | — | — | never without a separate decision |
| mitteametlik_arvamus | Informal opinion flag | bool | **Excluded** (ADR 0192 §5) | — | — | — | never without a separate decision |
| vastutaja_user_id | Responsible user's Lovable id | uuid | Excluded | **personal identifier** | — | — | — |
| allikas, raw_hyperlink | Import origin, raw link | text | Excluded | — | — | — | — |
| created_at/by, updated_by, deleted_at/by | Who and when | tstz, uuid | Excluded | **personal identifiers** | — | — | — |

## Child tables

| Table | Columns read | RLS (signed-in) | Status | Notes |
|---|---|---|---|---|
| topic_commencement | id, topic_id, liik, kuupaev, tapsustus, position | rows of visible topics | Collected | an estimate never becomes confirmed legal effect (ADR 0120) |
| topic_responsible_change | id, topic_id, vana_vastutaja, uus_vastutaja, alates | rows of visible topics | Collected | names are personal; shown only for visible pilot Teemad |
| topic_stage_link | prev_topic_id, next_topic_id | both topics visible | Collected | continuation under a new number is a *candidate*, never a relink |
| topic_link | topic_id, linked_topic_id | both topics visible | Collected (observation only) | 0 rows on 2026-11-02 |
| topic_source_row | — | rows of visible topics | **Excluded** | raw import rows: not ingested merely because readable |
| topic_raw_rows | — | **RLS disabled; SELECT granted to `authenticated`** | **Excluded** | see *Source-side observations* |
| topics | — | `true` for signed-in users | Excluded | the legacy import dataset, not the live register |
| topic_deletion_notification | — | recipient only | Unavailable | personal notifications |

## Audit ledger (RLS: `can_access_admin_view(auth.uid())` only)

| Table | Columns | Status |
|---|---|---|
| topic_change_event | id, topic_id, action (`muutmine`, `lisamine`, `kustutamine`, `taastamine`), actor_kind, version, occurred_at | **Unavailable** to the service identity; ingestion built and **disabled** (`LOVABLE_AUDIT_ENABLED`). `actor_id` would not be read even with access. |
| topic_change_field | event_id, field, old_value (jsonb), new_value (jsonb) | **Unavailable**; when enabled, `markmed` and `mitteametlik_arvamus` are kept as *withheld* (that they changed, never their values), as is any field outside the allowlist. |

Field names seen in the ledger: tegevuse_tahtaeg (444), mitteametlik_arvamus
(436), jargmiseks (294), hetkeseis (85), joustumised (72),
joustumised_hallatud (59), menetlusetapid (56), onenote_link (49), valja,
sisse, teema, koja_ettepanek, arvamuse_tahtaeg, kellele, valja_kuupaev,
oigusakt, vastutaja, aasta, nr, deleted_at, markmed, uus_vastutaja, seosed.

Enabling the ledger needs an **owner-approved, Lovable-side** read path for
one identity (`lovable_sync audit-sql` prints the reviewed proposal). The
service identity is never made an administrator, no `service_role` key is
used, and RLS is never bypassed.

## Source-side observations (reported, not acted on)

- **`topic_raw_rows` has row-level security disabled and SELECT granted to
  `authenticated`.** Any signed-in Lovable account, the synchronisation's
  included, can read the raw historical import rows. Juristid does not read
  them. This is for the Lovable owner to decide; Juristid changes nothing.
- The ledger's `mitteametlik_arvamus` changes (436) outnumber every other field
  but one; they stay withheld unless a separate decision allows them.
