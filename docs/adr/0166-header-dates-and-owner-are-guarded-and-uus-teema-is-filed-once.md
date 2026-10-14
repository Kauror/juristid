# 0166 — Header `Vastutaja` and `Saabus` are guarded, `Uus teema` is filed once, and dates are not invented

**Status:** accepted (owner's decisions D3, D5 and the date-order warning, 2026-10-14)
**Date:** 2026-10-14
**Context:** independent QA of 2026-10-14 — QA-WORKFLOW-03, QA-COORD-01, D-HIST
(subdocs DC-03), D-UUS (workflow D1/D2); QA remediation round, Agent D.
**Amends:** 0150 §1 (the creator default holds on the POST too), 0156 §4 (the
guarded set grows), Teema QA §5.2 (the `+ Saabus` prefill).

No migration, no new model, no data change.

## 1. `Vastutaja` and `Saabus` carry a field revision (QA-WORKFLOW-03)

`owner` and `received_date` join `GUARDED_MATTER_FIELDS`. The header's
`Vastutaja` and `Saabus` editors post `revision` =
`matter_field_revision(matter, field)` — a digest of the stored owner id or
date — and `update_field` compares it under the Matter lock
(`guard_matter_field_revision`). A mismatch **or a missing token** is answered
409 with the header redrawn from what is stored and «Seda välja on vahepeal
mujal muudetud…», exactly as for `stage`, the sets and the summary. Nothing is
written: no reassignment, no notice, no moved step.

The register row's `Määra ▾` (`views.assign_owner`, the Osakond/Teemad triage
control) posts the same `owner` revision (`owner_revision_for`, the same digest
without a query per row) and is refused the same way; being a plain form it
answers with a redirect back and the refusal as a message, the page-level form
of the 409.

Field-scoped, so unrelated edits survive: a colleague's stage move does not
make my `Vastutaja` stale, and my owner change does not make their `Saabus`
stale.

`UNGUARDED_MATTER_FIELDS` names, with reasons, the `update_field` fields left
without a revision (`response_deadline` — a first deadline only, the editor
that changes one carries `deadline_revision` — and `policy_area_other`).
`tests/test_header_field_guards.py` asserts every field `update_field` accepts
is in exactly one of the two sets, so a new header field cannot arrive without
deciding.

## 2. `Uus teema` is filed once by accident, and twice only on purpose (QA-COORD-01, D3)

The `intake` token already made one rendered form save once (ENG-074). Browser
Back defeated it: the page is `no-store` (privacy, `app/core/middleware.py`), so
Back re-fetches it with a **fresh** token while Chrome restores the typed title.

Not chosen: relaxing `no-store` so Back would reuse the cached HTML and its
token. That would weaken a deliberate privacy rule (a signed-out Back must not
show member material) for one page.

Chosen, two layers:

1. **The restored form names its earlier tokens.** `bindCreateFormHistory`
   (`static/js/app.js`) keeps every token the page was rendered with in its own
   history entry (`history.state`, which survives the `no-store` re-fetch) and
   posts the earlier ones as `intake_earlier`. A Teema one of them created, with
   the same normalised title, is recognised however long ago it was filed.
2. **A server-side net that needs no script.** A Teema the same person filed
   through `Uus teema` in the last 10 minutes (`RECENT_WINDOW`) with the same
   normalised title (case and spacing ignored) is recognised too. Read through
   `MatterIntakeSession` — the record a successful `Uus teema` save leaves — so
   imports, seeds and other doors never count.

Recognised, nothing is written (the token's claim rolls back) and the form
returns with «Sama pealkirjaga teema loodi just: «…». Ava see või loo siiski
uus.»: `Ava see` opens the existing Teema; `Loo siiski uus` posts the same form
with `loo_siiski_uus`, and a deliberate second Teema with the same words is
created. That button stands after `Salvesta`, so Enter never presses it. Only
the person's own recent work is consulted: **no global title uniqueness**.

## 3. The person filing is the Vastutaja on the POST too (D-UUS D2)

0150 §1 defaulted the owner on the unbound form only and accepted that «a
request that names nobody» files an unowned Teema. The page offers no
«Määramata» owner chip, so such a request never comes from it.
`MatterCreateForm.clean` now applies the same default when the POST names no
owner, with the same eligibility (`creator_if_nameable`: a department worker
this form may name; an administrator outside the department's work is still
not made owner). A chosen colleague is never replaced. `Saabunud`, which does
offer «Määramata», is unchanged.

## 4. `Arvamuse tähtaeg` before `Saabus` is a warning (D-UUS D1)

Approved: warned, not silently accepted, not refused. On `Uus teema`
(`MatterCreateForm`), the header `Saabus` editor (against the stored deadline)
and the header `Arvamuse tähtaeg` editors (against the stored `Saabus`), a pair
with the deadline earlier stops the save once with «Arvamuse tähtaeg on varasem
kui saabumise kuupäev…». `Uus teema` then shows a checkbox «Kuupäevad on õiged,
salvesta siiski» (`kinnita_kuupaevad`); the header shows one button that
re-posts the same save — same date, same revision — with that flag. Nothing is
stored about the confirmation: the dates are the record. `app/matters/date_order.py`
holds the rule, the sentence and the flag.

Not covered here: `Muuda teemat` and `Saabunud`, which set both dates on one
form and were outside the approved scope.

## 5. `+ Saabus` on closed and archive records starts empty (D5)

The header's `+ Saabus` prefilled today (Teema QA §5.2). On a closed Matter or
an `ARCHIVE` record that is never the arrival day, and one Salvesta stored an
invented historical date. There the box now starts empty and is `required`; the
date is entered explicitly. Ordinary open `FULL` Matters keep today.

## Consequences

- Tests posting `update_field` for `owner` or `received_date`, or
  `assign_owner`, send the revision.
- A second `Uus teema` with the same title by the same person within ten minutes
  asks once; tests that mean it send `loo_siiski_uus`.
- `app/matters/intake.py` is untouched (the initiative-deadline block near
  `response_requested_at` is Agent B's to change).

## Reversibility

By code. Dropping `owner`/`received_date` from `GUARDED_MATTER_FIELDS` restores
tokenless posts; `RECENT_WINDOW = timedelta(0)` and removing
`bindCreateFormHistory` restore the old create behaviour.
