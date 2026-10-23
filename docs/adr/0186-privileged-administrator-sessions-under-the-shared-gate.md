# ADR 0186 — A verified administrator session under the shared gate, and two administrators always

- Status: Proposed (owner's brief «Admin & Workflow R1», workstream A, 2026-10-23)
- Amends: 0016 (authentication modes — «one mode in force», and what the persona
  may never do), 0145 §2, §11, §12, §13 (account administration only under
  `local_password`; the first administrator; the release boundary), 0180 (one new
  `auth_method`)
- Related, unchanged: 0034 (persona candidates), 0181 (one internal read policy),
  0183 (credential changes end their sessions), 0185 (the MCP authenticator under
  the gate — the precedent for a personal factor behind a shared password)

## Context

Production runs `AUTH_MODE=shared_gate` and stays there: one department password,
then a persona picked from a list. ADR 0016 is explicit that this authenticates
the door, not the person — anybody with the password can pick the department
head. ADR 0145 therefore built account administration (Haldus → Kasutajad) and
made every page and service answer *no* outside `local_password`, a mode no
deployment runs. The result: the department could not manage its own accounts at
all, and the owner's brief asks for exactly that — two independently
authenticated administrators (a technical one, possibly without any business
role, and the department head), usable today, without switching the whole
application to personal sign-in and without a second general login.

## Decision

### 1. A step-up, not a mode

Behind the shared gate, a person may open a **verified administrator session** at
`/haldus/sisene/`: their own e-mail address, the personal password they set
themselves through a one-time link (`tokens`, `credentials`, ADR 0145 §7), and a
current code from their own authenticator (TOTP, `mfa`) — **mandatory**; an
account without one enrols it there, after the password, before anything opens.
Recovery codes are accepted in place of the code, as in ADR 0145 §8.

It is held in the session under its own keys (`admin_session:*`, never a secret):
the principal's id; a keyed binding of the account's credential stamp (password
hash, address, active flag — ADR 0180 §5) and `security_epoch` (ADR 0183); the
time it was verified, last used and last re-proved; and the gate pass it was
opened behind. `app.accounts.admin_session.verified_administrator(request)`
re-checks all of it on every call and answers the principal or `None`.

**What it is not.** It does not call `login`, does not change `request.user` or
the persona, and grants no business read or write — `may_read_business_content`
still asks only about the persona (ADR 0181). A technical administrator with no
business role reaches Haldus with it and is still sent to the persona chooser by
every business page. It opens only Haldus administration. Without the gate it is
worth nothing.

**Who may hold one:** an active, activated, non-synthetic (on real data) account
with a personal password that holds `accounts.manage` (resolved centrally) or the
technical ADMINISTRATOR role. Every refusal — unknown address, wrong password, no
administrative standing — is one sentence and one throttle count; the audit says
which.

**How it ends:** idle `ADMIN_SESSION_IDLE_SECONDS` (default 900) and absolute
`ADMIN_SESSION_ABSOLUTE_SECONDS` (default 7200, never more than 12 h), with safe
defaults for unusable values; any change to the binding (password, address,
deactivation, a factor enrolled or reset, a role or capability change — all move
the epoch or the stamp); losing administrative standing; the gate closing —
«Välju», the 12 h/8 h gate limits, a rotated shared password (each recorded as
`ADMINISTRATOR_SESSION_ENDED` with its reason); and «Lõpeta halduri seanss»,
which ends only it. A copied state is worthless behind another pass of the gate.

**Fresh proof.** Every administrative change older than
`ADMIN_SESSION_REAUTH_SECONDS` (default 600) since the last proof asks for the
password **and** the code again (`/haldus/kinnita/`).

**Throttling and audit.** The existing `AuthenticationThrottle` counters
(sign-in pair/account/network, second factor, re-authentication) — a guess here
and a guess at the dormant personal sign-in are the same guess. Failures reuse
`AUTHENTICATION_FAILED` with `path = administrator_session`; new types
`ADMINISTRATOR_SESSION_STARTED`, `ADMINISTRATOR_SESSION_ENDED` (migration
`audit/0039_r1_admin_reference_reminder_events`, choices only).

**Provenance (amends 0180).** A request that *acts* through the session — an
administration view, the step-up itself — writes its rows with
`auth_method = administrator_session`, `auth_assurance = individual_mfa`,
`authenticated_principal` = the verified administrator and `acting_persona` =
whatever persona is chosen. Business pages viewed while one is open stay plain
shared-gate rows: the persona is not the administrator.

**Persona changes.** Defined and tested: choosing, switching or dropping a
persona (and the middleware dropping an ineligible one) carries the
administrator session unchanged — same principal, same clocks. The actor of
every administrative change stays the verified principal whoever the persona is.
A pending (password-only) step-up does not survive a persona change.

### 2. The services accept only a verified actor (the second lock)

`administration.*` take `actor: User | VerifiedActor`. A `VerifiedActor` is
minted only by `admin_session.administrator_actor(request)` (a private seal; a
hand-made one raises `TypeError`) and carries the epoch it was verified at.
Under the shared gate a bare account is refused (`NOT_VERIFIED`) before anything
is read, so a view that forgot its gate administers nothing on the strength of a
persona. Under the lock the actor is re-read and refused (`STALE_PROOF`) if its
epoch moved, it was switched off or its authority went — a power withdrawn by a
concurrent request ends the other administrator's authority before their change
is made. Under `local_password` the views pass a `VerifiedActor` too; a bare
account is still accepted there from code inside that mode (tests, a shell), as
before. `none` and `cloudflare_access` administer nothing. Every existing check
— delegation, no self-change, the final delegator, grantability — is unchanged.

### 3. Two usable full administrators

A *usable full administrator* is active, activated, has a personal password, a
confirmed authenticator whose secret the current key opens, both
`accounts.manage` and `accounts.delegate` (resolved centrally), and is not a
synthetic account on real data. Stored flags alone never count.

An ordinary change (role/capability change, deactivation) that leaves **fewer
usable full administrators than before and fewer than two** is refused
(`CONTINUITY`), computed after the write, inside its transaction, under the
existing advisory lock — two administrators removing each other at once take
turns and the second sees the first's result. While fewer than two exist (the
bootstrap phase) only changes that do not reduce the count pass. The existing
final-delegator guard stays as the floor. A second-factor reset by another
administrator is **not** held to the rule: it is the lost-phone recovery the
rule exists to keep possible (the person re-enrols at their next step-up).
`administration.continuity_status()` feeds Haldus → Ülevaade and Süsteemi seis.

### 4. The emergency route

`manage.py emergency_disable_account --email … --confirm-email … --reason …
[--dry-run]`, on the host only: switches the account off, invalidates its links
and ends every session (the administrator session included) even when that
breaks continuity, changes nothing else, records `EMERGENCY_ACCOUNT_DISABLED`
(`by: operator`, the reason, the counts before and after) and prints what it did.
No web route, persona or administrator session reaches it. Bootstrap is no
longer blocked by a switched-off administrator, so the operator can appoint a
first administrator again if nobody is left.

### 5. Enrolment under the gate

Account e-mail stays impossible beside the gate (`juristid.E036`, unchanged). So:

- `bootstrap_account_admin` now runs under `shared_gate` as well as
  `local_password` (still refused under `none`/`cloudflare_access`, still once,
  still no password, `--print-activation-link` to an interactive terminal);
- the second administrator is made one on Haldus → Kasutajad by the first (their
  decision, audited); «Kinnita ja saada kutse» records it and says mail is off;
- `manage.py issue_administrator_link --email … --confirm-email … --note …`
  prints that person's one-time link (activation, or reset if they have a
  password) to an interactive terminal — only for an account that may
  administer, a pending one only once approved — audited without the link;
- `/konto/aktiveeri/` and `/konto/parool/taasta/` answer under the gate **only
  for a link whose holder may administer**; anybody else, and no link, is the 404
  it always was. A link followed before the gate keeps only its proof in the
  session and comes back to the bare page after the password; the secret never
  travels in the gate's `next`.

### 6. How this amends 0016 and 0145

- *One mode in force* still holds: `AUTH_MODE` is unchanged and the step-up
  establishes nobody's identity for the application. It is a narrowly scoped
  privilege elevation for one purpose, beside the gate, exactly as the MCP
  authenticator is (0185).
- *The persona is never an identity* still holds, harder than before: no
  administrative capability is exercisable through a persona, and now none is
  exercisable without the person's own password and TOTP even when the account
  holds it.
- 0145 §12/§13: Haldus → Kasutajad answers under the shared gate to a verified
  administrator, and still 404 to everybody else, every persona included. The
  personal sign-in pages stay 404 under the gate except the two link pages, and
  those only for administrators.

## Haldus

`/haldus/` (namespace `haldus`) is one shell — Ülevaade · Kasutajad ja õigused ·
Klassifikaatorid · Osakonna töökorraldus · Süsteemi seis — built from
`app/haldus/sections.py`; each route enforces its own predicate. The bar gets one
«Haldus» item where a section opens (and, under the gate, for a persona whose
account may administer, a way to the sign-in); the persona page carries a quiet
link to the administrator sign-in. Süsteemi seis shows aggregate verdicts only
(version, search freshness, readiness, failed extraction/OneNote counts) — no
record, file name, person, setting value or secret, and it runs nothing.

## Consequences and residual risks

- Somebody who knows the shared password **and** an administrator's personal
  password and holds their phone can act as them — the assurance a second factor
  gives anywhere.
- Between a person setting their password and enrolling their authenticator,
  the password alone reaches enrolment (as in 0145 §8). The instruction is to do
  both in one sitting; the operator hands the link over in person.
- An administrator session open in a browser is usable by whoever sits at it
  until it idles out (15 min) — as any session is.
- The two real administrators must enrol themselves; nothing here creates an
  account, sets a password or enrols a factor.
