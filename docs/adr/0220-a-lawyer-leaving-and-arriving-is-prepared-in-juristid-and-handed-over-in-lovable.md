# 0220 — A lawyer leaving and arriving is prepared in Juristid and handed over in Lovable

Date: 2026-11-23 · Status: proposed (owner brief 2026-11-23, «we have to build a system where we can add in new lawyers and move the old lawyer's topics»; owner chose B + C + D the same day; release awaits owner review)

## Context

A lawyer is leaving the department and a new one is arriving. The owner asked that
Juristid let us take in the new lawyer, move the leaver's work to them, and be ready for
the new lawyer's work arriving from Lovable.

On 23.11.2026 every live open Teema in Juristid is one the Lovable synchronisation
follows (206: 84, 64 and 58 for the three lawyers), and every open step is on its
owner's own Teema. Such a Teema's owner changes only in Lovable (ADR 0213 §10). Lovable
added «Teemade üleandmine» on 22.11.2026 (its migration 0039): an administrator chooses
a lawyer's in-progress topics and gives each a new lawyer in one transaction; each gets
«Uus vastutaja», one `topic_responsible_change` row dated that day, an audit entry and a
bell notice to the recipient.

Three things in Juristid would have gone wrong:

1. The night refuses more than 20 changed Teemad (`MAX_CHANGED_MATTERS`, ADR 0213 §10.8),
   and a hand-over of one lawyer is 58–84. Every night after it would stop until a
   supervised run.
2. Lovable names a lawyer by the text their account signs in with. The night resolves
   that to a person by the pairs earlier baselines agreed on, then by an exact display
   name (`OwnerResolver`). A new lawyer whose Lovable name differs from their Juristid
   name is held (`UNRESOLVED_OWNER`), the Teema stays with the leaver, and nothing says
   so loudly.
3. Switching off the leaver's account says nothing about the open Teemad and steps they
   still hold; nothing lists work left with nobody active.

A bulk hand-over inside Juristid (proposal A) moves nothing while Lovable owns every
open Teema; it is not built here.

## Decision

1. **The order of a change of lawyers.** Create the new lawyer in Juristid (Haldus →
   Kasutajad) and in Lovable; check the name on «Lovable'i nimed» (§3); hand the
   leaver's topics over in Lovable; the night brings them (§2); switch the leaver off in
   both systems, confirming in Juristid that nothing is left (§4).

2. **A hand-over has its own nightly limit.** A Teema counts as a hand-over when every
   source change tonight is its owner (or the «Endine vastutaja» that owner change
   clears), the owner resolves to a lawyer, and Lovable's latest responsible-change row
   (newest `alates`, the later row on a tie) names that same lawyer
   (`lovable_sync.is_handover`, `recorded_handover`). Hand-overs count against
   `MAX_HANDOVER_MATTERS` = 150, not against `MAX_CHANGED_MATTERS`; over 150 the night is
   REVIEW_ONLY (`BREAKER`) exactly as over 20. Anything else on the same Teema, a
   hand-over to «Vastutaja puudub», an owner cell edited with no responsible-change row,
   or a name that does not resolve is an ordinary change or a hold, as before. The run's
   counts record `changed_handovers` and `handover_limit`; `changed_source` no longer
   includes hand-overs, and the successor deferral counts the ordinary changes. The
   other limits are unchanged. The open step follows the file as every owner change
   does (`assign_matter`), and the new owner hears «Uus asi» (ADR 0051, amendment of
   2026-11-19).

3. **«Lovable'i nimed»** (Haldus → Andmete sünkroonimine, technical administrator,
   `may_open_system`):
   - every owner name in the newest stored reading, and every name its
     responsible-change rows hand work to, with how many topics name it, who the night
     takes it to be and by which rule (pinned, earlier sync, same name), names the night
     cannot place first;
   - a typed name checked the same way, so a new colleague's Lovable name can be tried
     before any topic carries it;
   - **a name pinned to a person** (`LovableOwnerAlias`, one row per `name_key`):
     the resolver reads pins first, then the pairs, then the display name. A pin to a
     person who is switched off or may not hold work is no answer. Pinning or removing a
     pin needs a fresh proof and is audited (`IMPORT_RUN`,
     `LOVABLE_OWNER_ALIAS_PINNED` / `_REMOVED`). A pin changes no Teema now: the resolver
     is asked only when an owner in Lovable changes or a new topic arrives.

4. **Work a person still holds** (`app.matters.held_work`): live open Teemad they own
   (and how many of those Lovable owns) and open or planned steps they answer for, as
   counts only.
   - Haldus → Kasutajad shows it on the person's page, linking to their open Teemad in
     the register.
   - `deactivate_account` refuses a person who still holds open work unless
     `leave_open_work` is confirmed (the page's checkbox «Lülitan välja ja töö jääb
     ootele»); the counts go into the `ACCOUNT_DEACTIVATED` audit row. The emergency
     command is a different path and is unchanged.
   - **«Töö ilma aktiivse vastutajata»** (Haldus → Kasutajad, `may_administer_accounts`)
     lists each switched-off person who still owns an open Teema or answers for a live
     step, with the counts, and the open Teemad with no owner. Counts and names only: no
     title or step text.

## Consequences

- A whole desk handed over in Lovable reaches Juristid the same night, up to 150 Teemad.
  A mass owner change Lovable did not record as a hand-over still stops at 20.
- The new lawyer's name is checked before the hand-over, and a difference is fixed by a
  pin rather than by renaming either account.
- A leaver can no longer be switched off without seeing what they hold.
- A Teema Juristid owns itself (none today; every one after a cut-over from Lovable) is
  still handed over one at a time on its own page. A bulk hand-over inside Juristid is
  left for when that matters.

**Migration:** `legacy_import/0033` (additive: `LovableOwnerAlias`).
