# Historical production snapshot — 2026-09-21, the pilot database reset

> **NOT CURRENT STATE.** This file records what one instance held before and
> after one operation on one day. It is evidence about that moment and nothing
> else. Do not read a number here as the number production holds now, and do
> not plan an operation from it. Current state comes from running the relevant
> read-only check against the target instance — `docs/production-readiness.md`
> says which check proves which property.

| | |
| --- | --- |
| Observed | 2026-09-21 |
| Operation | **operator reset of the pre-QA pilot database**, at the owner's request |
| Revision before | `0ab0335fd187d0c63136c6fb56c6004bcd3d8ae5` |
| Revision after | `0ab0335fd187d0c63136c6fb56c6004bcd3d8ae5` (unchanged — this was not a release) |
| Previous release | `7c466a18a8927c93446b98ed9eae2d39ee67ca7f`, replaced earlier the same day |
| Method | direct operator action on the real-data instance |
| Status | **historical** — superseded by whatever the instance answers today |

**This was an intentional operator reset, not ordinary user deletion.** The
owner asked for a clean slate to test against. Nothing here went through
`Kustuta teema`, which deliberately leaves a tombstone and keeps the
append-only audit history: eight tombstones and a preserved audit stream is
not a clean slate, and this operation is the honest way to get one rather than
a way around those invariants. No trigger was disabled, no `TRUNCATE … CASCADE`
was used, no row was reclassified `REAL` → `TEST`, and `purge_test_data` —
which is a planner for TEST Matters and has no apply path — was not involved.

## When

| | UTC | Europe/Tallinn |
| --- | --- | --- |
| Release deployed | 04:40 | 07:40 |
| Deployment backup | 04:44:23 | 07:44:23 |
| Writers held, **dedicated pre-reset backup** | 04:46:57 | 07:46:57 |
| Inventory taken | 04:48 | 07:48 |
| Reset performed | 10:34–10:38 | 13:34–13:38 |
| Verified, service restored | 10:45 | 13:45 |

The writers were held twice. The first hold ran from 04:46 to about 04:55 UTC
and ended by putting the instance back into service unchanged, because the
destructive step needed an approval that was not yet in hand; the evidence tree
was moved back and the still-intact database was served again at the deployed
revision. The second hold, 10:33 to 10:38 UTC, is the one the reset happened
in. Roughly thirteen minutes of held writers in total, on a Sunday morning, and
no request was served a wrong answer in either window — the application was
down, not wrong.

## The gate this reset had to pass

A reset is authorized only against a disposable dataset, so the inventory was
taken first, read-only, with the writers held so it could not move. All eight
conditions held:

| | Condition | What the instance answered |
| --- | --- | --- |
| 1 | no legacy register or import corpus | every `legacy_import` table 0 |
| 2 | no archive Matter corpus | `record_mode` FULL on 8 of 8; ARCHIVE 0 |
| 3 | no opinion archive meant to survive | every `OpinionArchive*` table 0 |
| 4 | every live Matter native to the pilot | `origin` NATIVE 8 of 8, `data_class` REAL 8 of 8 |
| 5 | no post-pilot operational use | `Submission` 0 — no opinion has ever been sent |
| 6 | no unexplained cross-boundary reference | `related_materials` 0, procedural links 0, source references 0 |
| 7 | restorable from the dedicated backup | set verified at levels 1 and 2 before the reset |
| 8 | identity and reference data reproducible | rehearsed end to end on a throwaway database first |

Condition 8 is the one worth a sentence: the rehearsal migrated an empty
database from zero at this revision and applied the reviewed reference data,
and the resulting plan digest was **byte-identical** to the one production
produced afterwards. That is what made the reference baseline a reproduction
rather than a hope.

## Before

Aggregates only. No titles, filenames, storage keys or names appear here or
appeared in the operator transcript.

```
Matter.objects                    8      MatterEngagement            5
Matter.all_objects                8      MatterExternalPosition      1
  data_class REAL                 8      MatterWebsiteOverview       4
  origin NATIVE                   8      MatterProceduralDevelopment 1
  record_mode FULL                8      MatterEffectiveDate         1
  open / closed               7 / 1      MatterAssignmentNotice      8
  tombstoned                      0      MatterSourceOrganisation    8
Entry                             4      MatterPersonalNote          2
EntryRevision                     0      PersonalScratchpad          1
NextAction                        7      MatterIntakeSession / File  7 / 13
Submission                        0      MatterTimelineStep          0
Document / DocumentVersion   17 / 17     MatterProceduralLink        0
DocumentLink                      6      MatterWorkVictory           0
evidence bytes              4 507 977    MatterImportantDate         0
distinct storage keys            17      OperationalMatterSnapshot   0

ChangeEvent                      74      SearchDocument             17
  all matter-linked              74      SearchRebuildDebt           0
SecurityAuditEvent              362      MatterReferenceSequence    16

every legacy_import table         0      Organisation / alias   17 / 13
every submissions table           0      PolicyArea                 28
                                         StageVocabulary            10
User (1 ADM, 1 HEAD, 2 SPEC)      4      LegalInstrumentType        22
sessions / gate throttle     199 / 3     LegacyStatusMapping        11
```

## After

```
every business table              0      Organisation / alias   15 / 13
  Matter.objects                  0      PolicyArea                 28
  Matter.all_objects              0      StageVocabulary            10
  tombstoned                      0      LegalInstrumentType        22
every legacy_import table         0      LegacyStatusMapping        11
every submissions table           0
ChangeEvent                       0      User (1 ADM, 1 HEAD, 2 SPEC)   4
SecurityAuditEvent                0        persona candidates            3
SearchDocument                    0        accounts with a usable password 0
SearchRebuildDebt                 0      sessions / gate throttle    0 / 0
MatterReferenceSequence           0      evidence versions / bytes   0 / 0
```

Three of those deserve a note rather than a number.

**`Organisation` fell from 17 to 15, and that is the reset working.** Fifteen
is the reviewed baseline `reference_data apply` creates; the other two were
institutions somebody typed during the pilot. A fresh database is supposed to
lose pilot cruft, and an institution a lawyer adds again is one `+` away.

**`MatterReferenceSequence` is empty, deliberately.** It held sixteen rows,
one per year from 2011 to 2026 — residue of a register import that is long
gone, plus 2026 sitting at 324. A fresh database starts the sequence over, and
that was kept rather than carried: numbering forward from a count of files that
no longer exist would make the first new Teema claim to be the 325th.

**The new database holds no pilot audit history, and must not.** `ChangeEvent`
and `SecurityAuditEvent` are 0 because this is a different database, not
because anything was erased from the old one. The old one is intact inside the
backup set below, which is the historical artefact.

## How

1. the writers were held: `stop web intake-reader searchindex`. PostgreSQL and
   the tunnel stayed up, so the inventory could be taken and nothing could move
   while it was;
2. the identity configuration was exported to the host's operator directory —
   UPN, address, display name, role, and the four flags `persona_candidates`
   filters on. **No password hash and no secret**: under `AUTH_MODE=shared_gate`
   no account has a usable password, and the one secret is a host-side gate
   password that was never read;
3. the pilot evidence and derivative trees were **moved aside**, not deleted,
   and empty ones created at the same bind paths owned by uid 10001 — so no
   configuration moved and the old bytes stayed on disk. `legacy-source` is
   import material rather than pilot data and was left alone;
4. the application database was dropped and recreated inside the running
   PostgreSQL container, `OWNER juristid TEMPLATE template1`. Encoding,
   collation and ctype were read before and after and are unchanged
   (`UTF8 / en_US.utf8 / en_US.utf8`), and the three extensions the schema needs
   — `pg_trgm`, `plpgsql`, `unaccent` — came back with the migrations;
5. `migrate` from zero at the deployed revision, then `migration_plan` to prove
   nothing was pending;
6. `reference_data plan`, read, then `apply --expect-plan-sha256 …`, then
   `verify`;
7. the four accounts were recreated through `UserManager.create_user`, which
   normalises the UPN and sets an unusable password — the state every account
   in this deployment was already in;
8. `up -d --no-build`, then the post-flight below.

**The Docker volume was never deleted**, nothing was pruned, no `down -v` ran,
nothing was built on this host, and `juristid-test` was not touched.

## Evidence

The active store is a **new, empty tree** at the same path rather than a pruned
one: nothing was deleted, so nothing had to be proved deletable. The pilot bytes
are in two places — `appdata/juristid-main/evidence-pre-reset-20260921T044827Z`
on the host, and the backup's append-only evidence pool, where the pre-reset set
names all seventeen of them as its own and level-2 verification found every one
present.

`check_evidence_integrity --verify-sha` against the fresh store: 0 versions,
0 stored objects, 0 bytes hashed, no integrity problems.

## Rollback

`/mnt/user/backups/juristid-main/sets/20260921T044657Z`, taken with the writers
already held so it is exactly the state that was discarded. Verified at levels
1 and 2 before the reset: checksums match, `pg_restore` read 1025 archive
entries, every required table present, and both byte pools hold what the
manifest recorded. Restoring it is `deploy/unraid-main/RECOVERY.md`; the
evidence tree is already beside the live one and needs only to be moved back.

The set from the deployment two minutes earlier, `20260921T044423Z`, is the
same data at the same revision and is a second copy of the same moment.

## Post-flight

```
/app/GIT_SHA and /healthz   0ab0335fd187d0c63136c6fb56c6004bcd3d8ae5
migrations                  no pending migrations
deployment_readiness        ready — auth shared_gate, real data yes, debug off
                            reference data 19/19 areas, 15/15 organisations
production_status           readiness / search integrity / search freshness /
                            era contracts / archive projection — all PASS
check_search_capabilities   extensions present, Estonian configuration present
check_search_freshness      index is current
check_evidence_integrity    no integrity problems (0 objects)
containers                  web, intake-reader, searchindex, db, tunnel —
                            all healthy, restart count 0
```

## What a smoke could and could not prove

The post-reset smoke was **read-only and created nothing** — no Matter, no
entry, no engagement, no document — because the point of the reset is to leave
the slate empty.

It also did not get past the shared gate, and that is worth recording. The
first attempt drove Django's test client with a logged-in persona and every
page came back **302**: `AUTH_MODE=shared_gate` puts a password in front of
every business URL, and authenticating a user is not passing it. Writing the
gate's own session flag by hand would have rendered the pages and would have
been an operator switching off the one control this deployment has, so the gate
stayed shut and the 302 stands as the finding.

What the pages would have said was asked of the data layer they read instead:
the register queryset, the four Minu asjad selectors, the search scope and the
search table all answer 0; every reviewed institution, policy area, stage and
instrument is present and offerable; and the templates those pages render load
from the image. The one thing left unproved is that a human opening the site
sees the empty states, which needs the gate password — and is the first thing
the owner does next.

## An unrelated observation

The PostgreSQL server also holds a database named `juristid_plan0109`, residue
of an earlier register-refresh exercise. It was not touched, is not referenced
by the application, and is noted here only so the next person reading
`pg_database` does not mistake it for something this operation left behind.
