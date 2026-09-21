# 0102 — A mistaken record comes off the active file

**Status:** accepted
**Date:** 2026-09-21

**Reverses the «a mistaken row is corrected» rule of ADR 0084 §8 for
user-created `Teema käik` blocks.** That rule has stood since `Väline
seisukoht` was written and was repeated into every record type built after it:
there is no delete, on an open Matter or a closed one, because what the file
recorded and who recorded it is part of the file.

It is right about a record of something that *happened*. It is wrong about a
row that should never have been on this Teema, and the owner met the difference
in ordinary use (OWNER-04):

> a lawyer files a `Märge` on the wrong file, and the only repair is to rewrite
> it into something else.

A correction leaves a sentence nobody wrote, dated a day nobody chose,
attributed to whoever happened to be fixing it. That is worse than the mistake.

---

## 1. Three meanings, kept apart

Confusing any two of them is how a file starts lying, so each keeps its own
column, its own service and its own audit event:

| | what it says | what the chronology does |
| --- | --- | --- |
| `FactStatus.CANCELLED` · «Tühistatud» | the plan changed | keeps the row, saying so |
| `SubmissionStatus.WITHDRAWN` · «Tagasi võetud» | the letter went out and was taken back | keeps the row, saying so |
| **removed** | this never belonged on this file | the row leaves |

A cancelled deadline is an expectation somebody called off: it was real, the
calling-off is itself history, and dropping it is how a reader concludes nobody
ever recorded anything (Stage-2G brief 5, 33). Removal has nothing to say,
because nothing happened.

## 2. A column, never a `DELETE`

`app.core.models.RemovableRecord` adds `removed_at` and `removed_by` to the
canonical business record. Nothing is destroyed:

* the `ChangeEvent` trail keeps what was recorded, who recorded it and who
  removed it — `audit_changeevent` is append-only by database trigger and this
  does not go near it;
* evidence stays in the immutable store under its retention rules;
* `EntryRevision` — the one removable record whose correction history is
  itself append-only — is untouched, which is precisely why removal is a column
  and not a row deletion. docs/adr/0096 §4 names a corrected `Entry` as a
  *blocker* for whole-Matter deletion for the same reason;
* the technical audit path reads through the plain manager, where the row is
  still there.

The lawyer's meaning is «take this off the active file», never «erase that it
ever existed» — and the second is not a thing this architecture offers anybody
(AGENTS.md: audit append-only, evidence immutable).

**Restoration is deliberately not a user-facing action.** A record removed in
error is re-recorded, which is one capture away and truthful about who put it
back. An undelete button implies a recycle bin this product does not have and
would have to authorize, scope, paginate and test as a second surface.

## 3. The filter lives in `visible_to`

Every removable model already routes its business reads through one chokepoint,
because that is where authorization is applied — before any grouping, counting,
ranking or projection. A removed row has to be absent from exactly the same
places for exactly the same reason, so the filter goes in the same method:

```python
def visible_to(self, user):
    scoped = apply_scope(self, child_visibility_q(scope_for_user(user)))
    return scoped.filter(removed_at__isnull=True)
```

Nothing is filtered at call sites, because a filter at a call site is a filter
the next call site forgets. The chronology, the rail, `Minu asjad`, the
register, the statistics, the exports and the search projection all pass
through this already and needed no change.

**Search follows on the write.** The four kinds with a row of their own in the
corpus are reprojected synchronously by the removal, the rule
`refresh_engagement` established: «found only after an operator runs a command»
is the same defect as «not indexed», with a longer fuse. The child refresh
functions delete for every row and insert only for the ones still on the file,
so the per-write path and a full rebuild converge on the same index.
`check_search_integrity` counts live rows, so a corrected Matter does not make
it report a permanent shortfall.

## 4. Eight families, written down

`app/matters/removal.py` holds one table, and adding a ninth row is a
deliberate line in that file reviewed with the model it names:

```
sissekanne   Entry                          kaasamine    MatterEngagement
marge        MatterProceduralDevelopment    seisukoht    MatterExternalPosition
ulevaade     MatterWebsiteOverview          tahtaeg      MatterImportantDate
joustumine   MatterEffectiveDate            toovoit      MatterWorkVictory
```

Keyed by the Estonian word the row already uses, because the address a lawyer
copies out of the browser should read like the product. Each row carries its
own `ChangeEventType`, so `Kõik muudatused` goes on saying *which* record left
the file: a shared `RECORD_REMOVED` would print «Kirje eemaldatud» eight
different ways, which is the reason `WEBSITE_OVERVIEW_PLANNED` is not an
`ENGAGEMENT_ADDED` in the first place.

**This is not a workflow engine.** There is no registry a model joins by
declaring an attribute, no generic `Event` table, no state machine and no
per-kind hook chain. `kind_of()` matches the exact class rather than
`isinstance`, so a future subclass cannot inherit the capability silently.

### What is not removable, and why

**A sent `Koja arvamus`.** The chronology projects submissions through
`historically_sent()` — a letter that left this office. Taking it off the file
would be the record claiming it never went out, and the domain already has the
true sentence for «we took it back»: `SubmissionStatus.WITHDRAWN`. A wrong
date, summary or recipient is a correction, not a removal.

**Audit and system history.** `Teema loodud`, assignment logs, stage
transitions, security events, import provenance. These are projected from
`ChangeEvent`s, which are append-only by database trigger; there is no
canonical business record under them to take off the file, and none of them is
a mistake anybody made.

**`Menetluse link`.** It is a fact about the Matter rather than something that
happened to it — asked on `Uus teema`, corrected on `Muuda teemat` — so it is
not a `Teema käik` block at all and ADR 0084 §8's original rule still holds for
it (docs/adr/0097 §5).

## 5. The act, and its refusals

One POST route for all eight families,
`teemad/<pk>/kirje/<liik>/<id>/eemalda/`:

* **POST only.** An address that removed a record on GET would be one a
  prefetcher could fire, and the confirmation is a native disclosure in the row
  rather than a second page.
* **`business_write_required`**, so a reader never reaches it and is never
  shown the control.
* **The record is fetched through its own `visible_to`**, so a restricted child
  inside a Matter this writer may open answers exactly as a guessed UUID does.
* **Refused on a closed Matter**, under the Matter's row lock rather than by
  whether the page drew a chip. Removing a row is editing the file's account of
  what happened, which is what closure stops; reopening is the way out and
  leaves somebody's name on both decisions (docs/adr/0076 §2).
* **Optimistic concurrency**, compared under the row lock against `updated_at`
  — the token every other editor on this product uses.
* **A double submit is not an error.** The second call finds the row already
  gone and returns it unchanged; a refusal would print an alarming sentence
  about exactly the state the person asked for.

The whole Teema view is re-rendered, header included, because taking a row off
the file can change the rail, the phase later rows are grouped under, and what
the header states.

## 6. Files, and phases

**A file stays; its claim of belonging does not.** Bytes that arrived on a
Matter are evidence of the Matter, and removing a mistaken `Märge` does not
unsend the ministry's draft. What must not survive is a file row still reading
«kuulub: …» under a record nobody can see, so `DocumentLink.visible_to` drops a
link whose target was removed. Active reference and retained evidence are two
different things and this is the line between them.

**A phase boundary is not replaced.** If a removed `Märge` was the explicit
boundary a later section of `Teema käik` was grouped under, those rows become
`Etapiga sidumata`. That is truthful, and inventing a replacement boundary
would be manufacturing the history this whole record exists to protect.

## Migrations

Three, all additive and none with a backfill:

* `matters/0036_removable_records.py` — the two columns on `Entry`,
  `MatterEngagement`, `MatterExternalPosition`, `MatterProceduralDevelopment`
  and `MatterWebsiteOverview`;
* `intelligence/0003_removable_records.py` — the same two on the three
  `MatterFact` subclasses;
* `audit/0028_removal_event_types.py` — the eight new `choices`, which is a
  state change with no SQL behind it.

Every existing row is `NULL`, which reads as «still on the file», which is what
every existing row is. There is nothing to backfill: removal is a decision a
person makes, and no column in this schema records a past one.
