# 0112 — An `Uus teema` form is saved at most once

**Status:** accepted
**Date:** 2026-09-24

**Extends ADR 0064's intake session to be the form's one-time submission token.**
Nothing else about staging changes.

## Context

A repeated POST of the same `Uus teema` form filed a second Teema and consumed a
second register number (ENG-074, reproduced on `7a626158`). Chromium happens not
to send a fast double click twice. A network retry, a replayed request or a
browser that behaves differently is a server problem, and disabling a button
cannot solve it.

ADR 0064's `MatterIntakeSession` already said a consumed session is kept "so
that a double submit finds a closed door rather than an empty one". The door did
not close in time: the session was consumed *after* the commit, and a form that
had staged nothing had no session at all.

## Decision

**Every rendered `Uus teema` form carries its own session**, created by the GET
(`intake_staging.open_session`) and posted back in the `intake` field the page
already had. It is still the staging area for the form's files. It is now also
the form's token.

**`Loo teema` consumes it first, inside the creating transaction**
(`claim_for_create`): one `UPDATE … SET consumed_at = now() WHERE consumed_at IS
NULL`, before any row is written and before a reference number is allocated.
Two submissions of the same form race on that row. The second waits for the
first to commit, finds the condition false, writes nothing, and is answered with
the Teema the first one created (`MatterIntakeSession.matter`, set in the same
transaction). The answer is a redirect with the sentence «See vorm on juba
salvestatud — teist teemat ei loodud.», or the register if that Teema is gone.
If the first submission rolls back, its claim goes with it. Staged files are
promoted only by the submission that claimed the session, so a replay cannot
promote them twice.

**Per form, not per content.** A new form is a new session, so the same words
submitted from a new form create a second Teema. A lawyer may mean that, and
guessing otherwise would be deduplication, which this is not.

**A refused save keeps its token.** The 400 answer re-renders with the same
session, or a new one if it arrived without one, so the corrected save is
protected like the first.

**Expiry does not block a claim.** An expired session stops offering its staged
files (ADR 0064, unchanged). It is still the form's token, and a form left open
past the grace period must still save exactly once.

**A POST naming no session of this person's has no token** and creates as
before. Every rendered form carries one, so such a POST is not a replay of a
rendered form. Treating it as a refusal would turn the guard into a rule about
which clients may create Teemas.

### Storage and cleanup

The session row holds an owner, an expiry, a consumption stamp and the created
Teema — no form text. An unsubmitted render leaves a row that expires and is
removed by `sweep_stale_sessions` with every other stale session. That sweep is
still an operator command, as ADR 0064 decided.

### Not covered

**Other additive forms** (`+ Märge`, `Järgmine tegevus`) still accept a repeated
raw POST as a second record. The audit's browser evidence shows a real double
click sends one request, and `set_next_action` supersedes rather than
duplicates. A general token for those forms would be a framework, not a fix, and
is left for a separate decision.

## Migrations

`matters/0039_intake_session_records_the_matter_it_created` adds one nullable
`matter` foreign key (`SET_NULL`) and its index to `matters_matterintakesession`.

* **Classification:** `migration_plan` reports it as additive.
* **Rolling deploy:** the release still serving never reads or writes the
  column, so it keeps working.
* **Rollback:** reversing the migration drops the column with nothing else
  depending on it.
* No data migration and no backfill.
