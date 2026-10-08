# ADR 0145 — Personal sign-in and account administration, built dormant

- Status: accepted
- Date: 2026-10-08
- Stage: owner's account-management round (implementation authorised; activation **not** authorised)
- Amends: ADR 0004 (authentication direction — the production direction), ADR 0016
  (authentication modes — the set of modes and "what replaces this")
- Related, unchanged: ADR 0005 (authorization and visibility), ADR 0034 (persona
  candidates), ADR 0036 (assignable department workers), ADR 0037 (the business-write
  HTTP boundary), ADR 0042 (department-wide lawyer access)
- Operational companion: [`docs/LOCAL_AUTH_ACTIVATION_RUNBOOK.md`](../LOCAL_AUTH_ACTIVATION_RUNBOOK.md)

## Context

Production runs `AUTH_MODE=shared_gate`: one department password, then a persona
picked from a list. ADR 0016 is explicit that this **authenticates the door and
not the person** — the persona is a choice, and the audit trail says so on every
row. That was always meant to be temporary.

Two things changed. The owner wants to run the department's people from the
application — add a lawyer, switch off one who left, hand somebody the
department head's management view, appoint a second administrator — instead of
from a host shell (the gap recorded on 2026-08-25). And the owner has decided
that the individual identity behind those decisions will be **a personal password
this application verifies**, with a second factor, rather than Microsoft Entra ID
through OIDC (ADR 0004) or Cloudflare Access (ADR 0015/0016) for now. Entra ID /
Microsoft 365 single sign-on is explicitly out of this round; the immutable
`entra_object_id` column and the Cloudflare Access code are kept for it.

The brief that authorised the work also drew the boundary that shapes every
decision below: **build it completely, test it in isolation, deploy it inactive,
and change nothing about how production authenticates today.** No real account
is created or activated, no password is assigned to an existing record, no mail
is sent, and the shared gate is not replaced.

## Decisions

### 1. Why personal passwords, and why Entra ID waits

Personal passwords need no tenant, no app registration and no identity-provider
project, and they give the department something the shared gate cannot: a record
that *this person* did *this*. With Argon2id, a NIST-shaped password policy,
throttling and a mandatory second factor for administrators, that claim is one
the deployment can stand behind.

Entra ID remains the better long-term answer for a Microsoft 365 organisation —
the identity lifecycle would follow the Chamber's own directory — and nothing
here forecloses it: `entra_object_id` is untouched and still immutable, the user
table needs no rewrite to add it, and a future OIDC mode would be a fifth value
of the same setting. It waits because the owner has put it outside this round,
not because the design rejects it.

### 2. A fourth mode, and still exactly one

`AuthMode.LOCAL_PASSWORD = "local_password"`, beside `none`, `shared_gate` and
`cloudflare_access`. ADR 0016's principle is kept as it was written: **one
setting decides how a deployment establishes identity, and exactly one mode is in
force.** There is no configuration that runs the shared gate and personal
passwords side by side — two identity-establishing doors would be two front
doors, and the trail could no longer say which one somebody came through.

What each mode now means:

| mode | establishes | claim on every audit row |
| --- | --- | --- |
| `none` | nothing (laptop, CI) | `NONE` |
| `shared_gate` | that somebody knows the department password; the persona is a choice | `SHARED_GATE` |
| `cloudflare_access` | an individual, proved by Cloudflare against the Chamber's IdP | `CLOUDFLARE_ACCESS` |
| `local_password` | an individual, proved here: password, and a second factor where required | `LOCAL_PASSWORD` |

Every route the new mode adds — sign-in, the second-factor step, the one-time
links, password and security pages, and all of Haldus → Kasutajad — answers
**404 in every other mode**, the shape the shared gate's own views already use
(`app/accounts/urls.py`). Under the shared gate the middleware is unchanged; under
`local_password` the middleware admits only sessions signed in *here*, and signs
out a persona session left over from another mode rather than promoting it.

Business authorization is identical in all four. Every read still resolves
through `scope_for_user`; nothing about RESTRICTED moves.

### 3. Business roles stay; capabilities are layered on top

The four roles keep their meaning. On top of them sits a **small, explicit
capability vocabulary** (`app/accounts/capabilities.py`), resolved in one place
(`app.core.authorization.has_capability`): the role's default, then the person's
own *allow* or *deny*, stored as a JSON column on the account so asking costs no
query.

| capability | default holders | what it guards | grantable to |
| --- | --- | --- | --- |
| `department.view_management` | DEPARTMENT_HEAD | Osakond's *Meeskond* and *Tehtud*; a colleague's Minu asjad page | SPECIALIST, DEPARTMENT_HEAD, READER |
| `work.assign` | SPECIALIST, DEPARTMENT_HEAD | changing a Teema's Vastutaja; naming somebody else as Vastutaja on new work | SPECIALIST, DEPARTMENT_HEAD |
| `work_victory.review` | DEPARTMENT_HEAD | confirming a Töövõit or marking it unrealised | SPECIALIST, DEPARTMENT_HEAD |
| `accounts.manage` | nobody | Haldus → Kasutajad: create, invite, edit, switch off and on, reset a second factor | any role |
| `accounts.delegate` | nobody (needs `accounts.manage`) | grant/withdraw administration; act on another administrator; external address exceptions; change a sign-in address | any role |

**The defaults are exactly the rules they replaced**, and a test asserts it role
by role (`tests/test_local_auth_capabilities.py`). With no overrides — which is
every deployment today — behaviour is unchanged. In particular `work.assign`
belongs to **both** lawyer roles by default: the brief suggested assignment as a
head's permission, but ADR 0037 and ADR 0042 made authorship department-wide and
an assignment is authorship, and the same brief forbids narrowing collaborative
access. The capability exists so one person's right to reassign can be withdrawn
without withdrawing their right to write.

Two guards make an override unable to stretch a role: a write capability
(`work.assign`, `work_victory.review`) needs the business-write role underneath
it, so a READER or the technical ADMINISTRATOR granted one still holds nothing;
and the management view cannot be lent to the technical ADMINISTRATOR, because a
team table of the department's open work is business content. A capability
nobody recognises, or a stored value other than `allow`/`deny`, is ignored.

**Where each is enforced.** Navigation (`can_assign_work`,
`may_view_department_management` and `may_administer_accounts` in the context
processor), the GET that would render a control (the Vastutaja chooser offers
only oneself; the edit form's owner field is disabled), the POST (the routes
already gated by `may_review_work_victory` and `may_open_person_work`, and every
owner route), and **the domain services**: `assign_matter` refuses a person
without `work.assign` (an operation's assignment carries `provenance` and no
person's authority), and `confirm_work_victory`/`reject_work_victory` refuse a
person without `work_victory.review`. ADR 0037's "view layer, not service layer"
rule is about business *write* authorship, which imports and seeds also exercise;
these two operations have no such caller, so enforcing them below the view adds
a lock without adding a bypass.

**The repository audit found these manager-only operations, and decided them
one by one:**

- *In the vocabulary:* the Osakond management sections and colleagues' desks
  (`is_department_head` → `department.view_management`); Töövõit review
  (`ROLES_WITH_WORK_VICTORY_REVIEW`, now read from the capability defaults);
  assignment (newly separable); account administration (new).
- *Left as role rules, and why:* break-glass grants (head or superuser; no UI,
  and ENG-069's policy is still open); the historical review and opinion
  reconciliation queues and the development-status page (technical
  ADMINISTRATOR tooling); archive link review (`may_manage_archive_links`, a
  mode-dependent role rule — under `local_password` it behaves as under
  Cloudflare Access, i.e. ADMINISTRATOR); the Django admin (`is_staff`). None of
  these is a department-management power, and folding technical tooling into the
  business vocabulary would let an account administrator hand out technical
  access by ticking a box.

There is no generic permission editor and no Django model permission reaches
the page.

### 4. Account identity and the address policy

An account keeps its UUID, its `upn` (now: the login address) and its immutable
`entra_object_id`. Addresses are normalised one way everywhere (NFKC, trimmed,
lower case) and are unique. Only `koda.ee` addresses are accepted
(`ACCOUNT_ALLOWED_EMAIL_DOMAINS`); an administrator with `accounts.delegate` may
approve **one** external address as an exception with a written reason, and the
audit records who approved it and why. Approving an address never approves its
domain. A `+tag` subaddress and a non-ASCII address are refused: both are a
second spelling of one mailbox, which is exactly the silent duplicate the unique
column exists to prevent.

Changing a sign-in address (`change_login_email`) is a delegation decision. The
account keeps its identity, its work and its history; outstanding links die and
sessions end. It is how the shared-gate period's placeholder identities
(`marko`, `ireen`, `sandra`) get their real addresses at activation — and it is
**refused once the account has a personal password or a second factor**, and
for one's own account. After that moment the address is where the person's own
reset links go; an administrator who could move it could receive the next link
and become them. A person whose address truly changes later gets a new account,
and the old one is switched off.

### 5. Creation is never activation

`User.provisioning_state` is `PENDING` → `INVITED` → `ACTIVATED`, separate from
`is_active`:

- an administrator **creates** an account: PENDING, inactive, no usable password,
  never a persona, never assignable;
- an administrator **approves and invites** it: an activation link is mailed; the
  account becomes INVITED only if the mail actually left;
- **the person** sets their own password through the link: ACTIVATED and active.

A database constraint (`accounts_user_unactivated_is_inactive`) makes "pending or
invited, yet active" unrepresentable, because every mode reads `is_active` — the
Cloudflare branch signs in *any* active account whose address matches. "Switched
off" is ACTIVATED with `is_active` false. An account is never deleted; the
register goes on naming a former colleague on the work they did.

Every existing account reads as ACTIVATED by the column's default — they were
provisioned by operators and are in use — and has no local password until its
owner sets one through an administrator's link.

### 6. Passwords (NIST SP 800-63B-4, OWASP)

- **Argon2id** through Django's `Argon2PasswordHasher` and `argon2-cffi`, with
  cost from settings (default 64 MiB, two passes, one lane — above OWASP's floor
  of 19 MiB × 2, which `juristid.E031` enforces on a real-data deployment). The
  shared gate now names PBKDF2 explicitly so the change of default hasher does
  not change it.
- **15 characters minimum** for a password-only account, up to 256, any
  characters. No composition rules, no scheduled expiry.
- **Never truncated or stripped**; compared after NFKC normalisation.
- **Blocklist**: Django's 20,000 common passwords, an optional operator-supplied
  breach list (`LOCAL_AUTH_BREACHED_PASSWORDS_PATH`), the product's own vocabulary,
  the person's own name and address, repetition and keyboard/alphabet runs.
  No online breach service: that would send something derived from a password
  off the host and is an activation-time decision.
- Paste and password managers work; the fields carry the right `autocomplete`.
- **One refusal sentence** for every failure, and an unknown address costs the
  same hash as a known one. A synthetic account never signs in on a real-data
  deployment.

### 7. One-time links

A link is `<selector>.<verifier>` from `secrets`; only the SHA-256 of the
256-bit verifier is stored. Single use (spent under the row lock, together with
the password it sets), short-lived (48 h activation, 1 h reset, both
configurable), superseded by a newer link of the same purpose, invalidated by a
password change, a deactivation, an address change or a cancelled invitation.

The token travels as `?kood=`; on arrival the session keeps only a *proof* —
the selector and the SHA-256 the row already stores, never the secret — and the
address bar is cleared: the production access log records paths and never queries
(ENG-071), and the page that uses the token is not the page whose URL holds it.
Links are built from `ACCOUNT_LINK_BASE_URL`, never from the request's Host
header. **The administrator never sees a link**, so cannot set a colleague's
password; when a link cannot be delivered it is invalidated at once. Forgotten
password answers the same sentence for every address, does the same
synchronous work for every address (the link is issued and mailed after the
response, so an SMTP round trip does not say which addresses are real), and is
rate-limited per
address and per network; an account that has never set a password here gets no
reset link — its first password comes from an administrator's decision.

### 8. The second factor

RFC 6238 TOTP via `pyotp`, enrolled from an inline-SVG QR code (`segno`) or a
typed key, and confirmed by a code before it counts. The secret is encrypted at
rest with Fernet under a key derived from `LOCAL_AUTH_MFA_ENCRYPTION_KEY`
(required on a real-data deployment, `juristid.E033`), and kept sealed in the
session during enrolment. Accepted time steps are recorded, so a code is spent
once. Ten recovery codes, 60 bits each, shown once and stored as salted digests.

**Mandatory for anybody who holds `accounts.manage`, and for any `is_staff` or
`is_superuser` account** (which can open the Django admin) — not configurable,
and the all-users policy (`LOCAL_AUTH_MFA_REQUIRED_FOR_ALL`) can only add to it.
Codes are ASCII digits only. A person
who must enrol reaches nothing but the enrolment page. Administration needs a
session that *proved* a second factor. An administrator cannot remove their own
factor (only replace it); an administrator may reset somebody else's — the
lost-phone path — which ends that person's sessions. An e-mail is never a second
factor.

### 9. Sessions, throttling, audit

- **Session invalidation is Django's own mechanism, extended.** `security_epoch`
  is folded into the session authentication hash; bumping it ends every session
  of that account in every worker at its next request. It is bumped on a
  password set or change, a role or capability change, a deactivation or
  reactivation, an address change, and a second factor enrolled or removed. **At
  epoch 0 the hash is byte-for-byte Django's**, so deploying this signs nobody
  out — the shared gate's persona sessions included.
- Rotation on sign-in; idle (1 h) and absolute (10 h) limits; a re-authentication
  window (15 min) before any administrative change, a second-factor replacement
  or a recovery-code reissue. Secure, HttpOnly, SameSite=Lax cookies; CSRF on
  every form; `juristid.E034`/`E035` refuse insecure cookies and plain HTTP.
- **Throttling** in a database table shared by every worker
  (`AuthenticationThrottle`), never the cache and never a process counter. Scopes:
  account+network (tight), account (loose — only a distributed attack reaches it),
  network, second factor, re-authentication, reset requests, password change.
  Nothing global; escalation doubles and is capped; failures decay. Keys are
  HMACs, so the table names no address, and an address that belongs to nobody is
  counted exactly like a real one. An IPv6 client is counted per /64. A network
  that has signed an address in before (`TrustedSignInSource`, an HMAC too) is
  not held back by the *account-wide* counter — the one a stranger can trip from
  many networks — so knowing a colleague's address is not a way to keep them
  out of their own desk; the account-and-network counter still holds it.
- **Audit**: the existing append-only `SecurityAuditEvent`, with new types for
  sign-out, session end, re-authentication, the account lifecycle, address
  changes and exceptions, capability changes (old and new role, overrides and
  resolved capabilities), bootstrap, password set/change/reset request, link
  issue and delivery outcome, MFA enrolment/removal/recovery, and refused
  privilege changes. Sign-in success and failure reuse the existing types with
  `path = local_password`. **No row carries a password, a link, a TOTP secret
  or a recovery code**, and a password typed into the address field is recorded
  as `(malformed)` — asserted over a whole lifecycle in
  `tests/test_local_auth_audit.py`.

### 10. Account e-mail is off

Two switches, both off by default: `ACCOUNT_EMAIL_DELIVERY_ENABLED` (refused by
`juristid.E036` in any mode but `local_password`) and `EMAIL_BACKEND`, whose
default is `DeliveryDisabledBackend` — it delivers nothing, so even a stray
`send_mail` reaches nobody. Delivery also needs an https link base and a sender
(`E037`), and a console, file or memory backend is refused on real data (`E038`)
because the message carries a credential. No SMTP configuration exists in this
release.

### 11. The first administrator

`manage.py bootstrap_account_admin --email … --confirm-email … --role … --note …`
— the only way an administrator exists without another administrator appointing
them. Refused outside `local_password`, refused once any administrator exists,
the address typed twice, the operator's note audited, no password set, no
`is_staff`. The activation link is mailed, or — with delivery off and only when
asked — printed once to an **interactive** terminal (refused into a pipe or a
log). If the link cannot reach anyone the whole appointment rolls back. Nothing
calls it but an operator: no migration, deployment step or seed, which a test
asserts.

### 12. Haldus → Kasutajad

Estonian, in the existing kit (`page`, `panel`, `card--form`, `field`,
`data-table`, `checkitem`, `dangerzone`, plus an `acct*` section in `app.css`).
A list with search and a status filter (name, address, role, status,
administrative power, last personal sign-in); a create page; an account page
with identity, role, permission toggles (resolved values, "rolli vaikimisi"
where a toggle merely follows the role), status actions, security status and
the administrative history, saved with one **Salvesta**. Untouched toggles follow
a changed role; a toggle the reader may not change is disabled and keeps its
value. Every page is behind `local_auth.may_administer_accounts` — local mode, a
session signed in here, a proved second factor, no pending enrolment, and
`accounts.manage` — and answers 404 otherwise. Every service re-reads the acting
administrator under the administration lock before asking its authority, and
any change to an account that holds administrative power — a rename or a
cancelled invitation included — is delegation work. Nothing there needs the
Django admin, which still cannot add a person, change a role or mint a
privilege, and under personal sign-in cannot switch an account on or off
either: that is account administration, with its final-administrator guard.

### 13. The release boundary

Production keeps `AUTH_MODE=shared_gate`, unchanged in behaviour. Under it, every
new page is a 404, a persona that *holds* `accounts.manage` administers nothing,
account mail cannot be switched on, the bar shows nothing new, and choosing a
persona is not a personal sign-in (`last_authenticated_at` is not touched).
`tests/test_local_auth_production_safety.py` pins each of these, and the shipped
settings and deployment templates.

### 14. Existing accounts and history

Two additive migrations: `accounts/0004` (new columns with defaults, five new
tables, one constraint, one index) and `audit/0035` (the event vocabulary). No
`RunPython`, no `RunSQL`, no row created, no row rewritten. No existing account
gains a password, a capability or a new state; no owner, collaborator or audit
actor changes; personas are not converted into anything; nothing is merged by
name.

### 15. The independent review before merge

A separate security review of the first draft found no authentication bypass and
no change under the shared gate, and nine defects in the administration and
sign-in paths, all fixed in this change and each pinned by a test in
`tests/test_local_auth_review_fixes.py`: an address change after activation
(§4), a dormant delegation allow that could wake up (`overrides_after` drops an
allow whose prerequisite is not held), the account-wide lockout as a
denial-of-service (§9), the Django admin's `is_active` checkbox and password-only
staff accounts (§8, §12), forgotten-password timing (§7), non-ASCII digits
reaching `compare_digest` (§8), a stale actor row (§12), a manager acting on an
administrator (§12), and the raw link in the session (§7).

## Alternatives considered

- **Entra ID now** — the owner placed it outside this round.
- **Cloudflare Access as the individual authenticator** — built and tested
  (ADR 0016) and still supported; it needs an Access application and
  dashboard ownership the round does not include, and it cannot carry
  in-application account administration on its own.
- **`django-otp`** — its static-token device stores recovery codes in clear, and
  it brings models and admin registrations for one device type. `pyotp` for the
  arithmetic plus our own encrypted device and hashed codes is smaller and
  stores less.
- **Capabilities as Django `Permission`s / groups** — the per-model permission
  matrix is what the brief asked not to expose, and groups would add a second
  role system beside `role`.
- **A separate overrides table** — cleaner relations, one query per page per
  question; the JSON column on the row the request already loaded costs none,
  and the who/why lives in the audit trail.

## Consequences

- The application can run its own people, once activated, with an audited trail
  that names individuals.
- Production is unchanged until a deliberate, separate activation following the
  runbook.
- The browser suite gains a third server (`config/e2e_local_auth_settings.py`, its
  own database, mail to files) and `e2e/test_local_auth.py`.
- Two new runtime dependencies (`argon2-cffi`, `pyotp`) and one rendering
  dependency (`segno`), each justified in `pyproject.toml`.

## Reversibility

High while dormant: nothing reads the new columns in `shared_gate` mode except
the capability resolution, whose defaults equal the old rules. After activation,
rolling back is `AUTH_MODE=shared_gate` (runbook §13); the migrations need not
be reversed.
