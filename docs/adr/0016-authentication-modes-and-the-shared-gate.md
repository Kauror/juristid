# ADR 0016 — Authentication modes, and what a shared password is allowed to claim

- Status: accepted
- Date: 2026-08-21
- Stage: 2D (deployment)
- Related: ADR 0004 (authentication direction), ADR 0005 (authorization),
  ADR 0015 (historical corpus integration)

## Context

The real-data deployment needs somebody in front of it. ADR 0015 built
Cloudflare Access for exactly that: Cloudflare authenticates an individual
against the Chamber's identity provider, signs an assertion, and this
application verifies the signature before believing a word of it.

Setting that up requires an Access application, a team domain, an audience tag
and an email allow-list — a dashboard task with a person on the other end of it.
The corpus is on the server, verified; the register is twenty years of the
Chamber's work; and the development phase needs the system usable **now**, by a
handful of people who already share an office.

The obvious shortcut was to turn on the rehearsal's `DEV_LOGIN_ENABLED` and put
its four-digit PIN in front. That was rejected, and rejecting it is the reason
this ADR exists: the synthetic sign-in was *explicitly* forbidden with real
data, and quietly re-enabling it would have converted a deliberate prohibition
into an inconvenience somebody worked around.

## Decisions

### One mode setting, not a pile of booleans

`AUTH_MODE` is `none`, `shared_gate` or `cloudflare_access`. The combinations
that must never happen are unrepresentable rather than merely checked, and
`CF_ACCESS_ENABLED` — a second switch for the same decision — is gone. Two
switches for one decision is how a deployment ends up with an authenticator that
is configured and not running.

**Superseded on 2026-10-08 for the set of modes — see the amendment at the end of
this document.** There are four: `local_password` (personal sign-in, ADR 0145)
joined them, dormant. The principle in this section — one setting, exactly one
mode in force — is unchanged and now covers four values.

Business authorization is identical in all three. Every read still resolves
through `scope_for_user` and the `Q` builders in `app/core/authorization.py`.
What changes between modes is *how much the deployment may claim about the
identity it hands to that authorization* — nothing else. Switching
`shared_gate` → `cloudflare_access` later is one environment variable and no
code.

### The shared gate is a distinct authenticator, not a longer PIN

It is its own mode, with its own safeguards, and it earns the right to sit in
front of real data only when all of them hold:

- the password comes from `JURISTID_SHARED_GATE_PASSWORD`, host-side, and is
  at least 12 characters (`juristid.E010`, `E011`) — **amended 2026-10-18:** at
  least 16 with `REAL_DATA_ALLOWED`, and never a template placeholder
  (`E018`, `E016`); see the amendment at the end of this document
- it is hashed once at process start with Django's hasher and compared with the
  framework's constant-time check — the plaintext is never written to the
  database, a log, a template or the client
- failures are throttled per client with an escalating, **capped** lockout
  (`E012`)
- the session cookie is `Secure` (`E013`), `HttpOnly`, `SameSite=Lax`, and the
  session identifier is rotated on success
- the session ends 12 hours after the password was typed
  (`SHARED_GATE_SESSION_SECONDS`) — **amended 2026-10-17:** and after 8 hours
  without a request (`SHARED_GATE_IDLE_SECONDS`), whichever comes first; see the
  amendment at the end of this document
- `DJANGO_DEBUG=0` and `DEV_LOGIN_ENABLED=0` (`E004`, `E008`)

`REAL_DATA_ALLOWED=1` with `AUTH_MODE=none` still fails (`E006`). The check was
not loosened; a second mode was added to the small set it accepts.

### The throttle is a table, and it is per client

Django's database cache would be shared across gunicorn workers and would
survive a restart — but it is *evictable*: it culls a third of its rows when it
grows past `MAX_ENTRIES`. A lockout an attacker can flush by making noise is not
a lockout. So `SharedGateThrottle` is a table.

It is keyed on an HMAC of the forwarded address, never global. (**Amended
2026-10-17:** the address is the one every audit row records, from
`app/core/client_ip.py`; see the amendment at the end of this document.) A global counter
would let one attacker lock the department out of its own system: a
denial-of-service primitive wearing a control's clothes. Escalation doubles each
cycle and stops at a ceiling for the same reason — long enough that guessing
stops being viable, bounded so nothing becomes permanent.

The honest limit: behind the tunnel the forwarded address is attacker-controlled,
so somebody rotating it dodges the throttle. That would matter if this were
identity. It is a rate limit, the password is long, and an attacker who rotates
headers still guesses one request at a time.

Refusals never say *which* refusal they were. "Wrong password" and "locked out"
are two different facts, and only somebody probing the door benefits from being
able to tell them apart.

### **The persona selector is not authentication, and the audit says so**

This is the load-bearing decision.

After the gate, somebody picks whose work they are looking at — Kaur, Marko,
Ireen. That selection drives `Minu töö`, ownership filters and profile context.
It is **not** evidence that the named human is at the keyboard, and the system
must not accumulate a record that later reads as though it were.

So every audit row this mode writes carries both:

```
authenticated_via = SHARED_GATE
acting_as_user    = <the selected persona>
```

Passing the gate is recorded as `SHARED_GATE_PASSED`, deliberately *not* as
`AUTHENTICATION_SUCCEEDED` — a distinct event type, so a later reader of the
trail cannot mistake "somebody typed the department password" for "this person
signed in". Persona changes are `PERSONA_SELECTED`, with the previous and the
chosen persona, every time.

The interface says the same thing where somebody can read it: the gate page and
the selector both state that the password proves membership of the department
and not which member. When `AUTH_MODE=cloudflare_access`, `authenticated_via`
becomes `CLOUDFLARE_ACCESS` and the claim becomes true.

### A department scope, so the landing page needs no borrowed identity

The gate landed somebody on Ülevaade before they had chosen a persona, and that
page has to be worth looking at. It must not become worth looking at by
rendering "as Marko": that would show Marko's restricted files to whoever knew
the shared password.

(Since 2026-08-30 the gate sends somebody to the persona selector instead — see
the superseding note at the end of this section. The requirement stated here is
unchanged: Ülevaade is still reachable with no persona selected, and still must
not render "as somebody".)

`DepartmentViewer` is a sentinel — not a `User`, no primary key, cannot be
written to a foreign key, never an audit actor — that `scope_for_user` maps to
a scope with `user=None` and NORMAL visibility. Every dashboard selector already
went through `visible_to(user)`, so the whole page works unchanged.

One line makes it safe, and it is worth naming because the bug it prevents is
silent:

```python
# restricted_participation_q
if not scope.is_authenticated or scope.user is None:
    return NOTHING
```

Without it, `Q(owner=None)` compiles to `owner IS NULL` — which matches every
ownerless archive row, including the RESTRICTED ones, and the historical import
creates thousands of them. A participation clause built for a scope that knows
nobody would have handed the archive to anybody who typed the password. It is
tested directly.

Everything except Ülevaade stays `@login_required`. Without a persona,
`LOGIN_URL` is the selector, so the flow is: password → department overview →
pick a persona → your work. Authoring anything requires a persona, because
authoring needs somebody to attribute it to.

> **Superseded, 2026-08-30 — the entry step only.** Minu asjad is now a
> person's default home, so `/` no longer chooses Ülevaade. An authenticated
> request is redirected to `matters:my_work`; a request that is behind the gate
> with no persona selected is redirected to the selector, making the flow
> **password → pick a persona → your work**. Everything else in this section
> stands: `/ulevaade/` is unchanged, it is still the one `@gate_required` page,
> and `DepartmentViewer` is still what makes it renderable for a reader who has
> chosen no persona — both for the explicit *Ilma kasutajata* switch (ADR 0034)
> and for anybody who navigates there. Only the default destination moved
> (`app/core/views.py::home`).

### No public origin bypass

Unchanged from ADR 0015 and still the property the rest rests on: the
`juristid-main` stack publishes no host port. The only route in is the
Cloudflare tunnel. The shared gate is application authentication, not perimeter
authentication, and there is no unauthenticated alternate endpoint beside it.

## Consequences

- The deployment proceeds without waiting for a Cloudflare dashboard.
- Individual accountability is **not** available in this mode, and the system
  says so rather than implying otherwise. Anything that later needs "who did
  this" as evidence needs `cloudflare_access` first.
- Anyone with the shared password sees everything NORMAL, and can select any
  persona — including one entitled to RESTRICTED material. The gate is the
  perimeter; the persona is a lens. That is acceptable for a department that
  already shares an office and is not acceptable indefinitely.
- The password is one secret shared by several people: it cannot be revoked for
  one of them, only rotated for all.
- Migrating to Cloudflare Access is one environment variable. The verification
  code, its tests, and the middleware branch are already written and green.

## What replaces this

**Superseded on 2026-10-08 — see the amendment at the end of this document.**
The planned replacement is now `AUTH_MODE=local_password` (ADR 0145), after an
approved activation; `cloudflare_access` remains a supported alternative.

`AUTH_MODE=cloudflare_access`, once the Access application exists. At that
point `authenticated_via` becomes a claim the deployment can support, the
persona selector goes away, and this ADR becomes history rather than
description.

---

## Amendment, 2026-10-08 — a fourth mode, and a different planned replacement

- Status: accepted, amending «One mode setting, not a pile of booleans» (the set
  of values) and «What replaces this»
- Scope: the modes this application has and which one is to replace the shared
  gate; nothing about how the shared gate itself works

### What was decided before

Three modes — `none`, `shared_gate`, `cloudflare_access` — and the shared gate to
be replaced by Cloudflare Access once the Access application existed.

### Why it is superseded

The owner chose personal sign-in verified by this application, with a second
factor, as the individual authenticator for the real-data deployment, and asked
for account administration inside the application (ADR 0145, amending ADR 0004).

### What is decided now

- `AUTH_MODE=local_password` is a fourth value of the same setting:
  `authenticated_via = LOCAL_PASSWORD`, an individual identity this application
  proves. It is **built, tested and dormant** — no deployment runs it, and every
  route it adds answers 404 in the other three modes.
- **One mode in force** still holds. There is no way to run the shared gate and
  personal sign-in together; activation *switches* the mode, and the gate's
  routes, its password and its personas leave with it
  (`docs/LOCAL_AUTH_ACTIVATION_RUNBOOK.md`).
- The planned replacement for the shared gate is `local_password`. Cloudflare
  Access stays a supported, tested mode — the code, its tests and `juristid.E007`
  are untouched — and could still sit *in front of* the application at the
  perimeter without being the mode that establishes identity.

### What this amendment does not change

- **The shared gate, exactly.** Its password check (now PBKDF2 by name, so a new
  default hasher cannot move it), throttle, session, persona rule
  (`persona_candidates`), audit rows and every refusal are as described above.
  `tests/test_local_auth_production_safety.py` runs it end to end beside the new
  code.
- **The persona is still not an identity.** No administrative capability is ever
  exercisable through a persona, whatever the account holds: account
  administration requires a session signed in under `local_password` with a
  proved second factor (ADR 0145 §12).
- **The department scope** (`DepartmentViewer`) and the no-public-origin rule.
- **`REAL_DATA_ALLOWED` with `AUTH_MODE=none` still refuses to start** (`E006`);
  `local_password` joins the modes that satisfy it, under its own checks
  (`E030`–`E038`).

---

## Amendment, 2026-10-17 — an idle limit, and the client's real address

- Status: accepted, amending «The shared gate is a distinct authenticator» (the
  session limits) and «The throttle is a table, and it is per client» (which
  address)
- Findings: JSEC-005 and JOPS-008 of the 2026-10-17 audit
- Owner decision: **D11, 2026-10-17** — «8 h idle; the 12 h absolute limit
  stays» (`C:\CCudit-results\juristid-2026-10-17\OWNER-DECISIONS.md`)

### What was decided before

A gate session lasted 12 hours from the password (`SHARED_GATE_SESSION_SECONDS`),
however long it then sat unused. Audit rows recorded `REMOTE_ADDR`; the gate
throttle (and the personal sign-in and development PIN throttles) read
`CF-Connecting-IP`, falling back to `X-Forwarded-For`, unconditionally.

### Why it is amended

- A browser left open on a shared desk stayed behind the gate for the rest of
  the 12 hours (JSEC-005). The owner chose an 8-hour idle limit on 2026-10-17.
- Behind `cloudflared` every request reaches gunicorn from the connector, so
  every audit row carried the same internal address, while the throttle named
  the client. The trail and the lockout disagreed about who was knocking
  (JOPS-008).
- Persona changes re-opened the gate with a fresh pass time, so switching
  persona restarted the 12-hour clock. Picking a name is not typing the
  password.

### What is decided now

- **Two limits.** A gate session passes while it is within 12 hours of the
  password (`SHARED_GATE_SESSION_SECONDS`, unchanged) **and** within 8 hours of
  its last request (`SHARED_GATE_IDLE_SECONDS`, env override, default 28800).
  Either one failing asks for the password again, and drops the persona with it.
- The last-request time is the session key `shared_gate:last_seen`, written by
  the middleware for a request that is behind the gate, **at most once every
  five minutes** (`shared_gate.LAST_SEEN_RESOLUTION`) so a page view does not
  save the session every time. The idle limit can therefore fire up to five
  minutes early, never late. A session opened before this release has no stamp
  and is measured from its pass; an unreadable stamp is no pass.
- A persona change (`act_as`, and the middleware dropping an ineligible
  persona) re-opens the gate with the **original** pass time. The idle clock
  restarts; the absolute clock does not.
- **One address rule** (`app/core/client_ip.py`): `CF-Connecting-IP` when the
  deployment trusts its proxy — `TRUST_CF_CONNECTING_IP`, which is
  `DJANGO_BEHIND_TLS_PROXY`, the same trust the forwarded scheme already rests
  on — otherwise `REMOTE_ADDR`; either only when it parses as one IP address.
  `X-Forwarded-For` is no longer read. Every audit row's `ip_address` and every
  throttle key use it, so the row and the lockout name the same client.
- The deployed gunicorn access-log format adds `%({cf-connecting-ip}i)s` after
  `%(h)s`, so the access log names the client as well as the connector.

### What this amendment does not change

- The 12-hour absolute limit, the password, the throttle's per-client table and
  escalation, the persona rule, and the audit event types.
- A forwarded address is still a rate-limit key and an audit fact, never an
  identity or an authorization input. Trusting it rests on the tunnel being the
  only way in («No public origin bypass»).
- The session cookie is still not a browser-session cookie: a browser restart
  within both limits keeps the session. That part of JSEC-005 was not decided.

---

## Amendment, 2026-10-18 — real secrets, and serving only after the checks

- Status: accepted, amending «The shared gate is a distinct authenticator» (the
  password floor on real data) and the safeguards' claim that a mixed-up
  configuration «refuses to start»
- Findings: JOPS-003 and JOPS-004 of the 2026-10-17 audit

### What was decided before

The checks refused the development `SECRET_KEY` (`E001`) and a gate password
under 12 characters (`E011`), and nothing else about a secret's value. They ran
before every `manage.py` command, which is what the workers are; gunicorn,
which serves the web process, runs none.

### Why it is amended

- `manage.py check` with `REAL_DATA_ALLOWED=1` and the template's own
  `replace-me-…` values said "no issues": the gate placeholder is 42
  characters, and the `SECRET_KEY` placeholder is not the development key. The
  repository is public, so one value left unfilled is a secret anybody can read
  (JOPS-003).
- With `REAL_DATA_ALLOWED=1` and `AUTH_MODE=none`, `check` failed on `E006`
  while the web container answered `/healthz` with 200 and served pages. The
  deployment README and the Compose header said such a process refuses to
  start; for the web process that was not true (JOPS-004).

### What is decided now

- With `REAL_DATA_ALLOWED`, Error-level checks (`app/core/checks.py`):
  - `juristid.E016` — any of `DJANGO_SECRET_KEY`,
    `JURISTID_SHARED_GATE_PASSWORD`, `POSTGRES_PASSWORD`,
    `LOCAL_AUTH_MFA_ENCRYPTION_KEY`, `DJANGO_EMAIL_HOST_PASSWORD` starts with
    `replace-me`. The message names the variables, never the values.
  - `juristid.E017` — `SECRET_KEY` shorter than 50 (Django's own `--deploy`
    floor, `security.W009`, made an error where real data is).
  - `juristid.E018` — under `AUTH_MODE=shared_gate`, a gate password shorter
    than 16. `E011`'s 12 stays the mode's floor everywhere.
  - `juristid.E019` — the database password shorter than 16.
  The templates generate 88, 32 and 48 characters. Synthetic stacks
  (`REAL_DATA_ALLOWED=0`) are unchanged. `E015` stays retired (ADR 0072).
- **The web process serves only after the checks.** The image's `CMD` and both
  Unraid stacks' `web` command start gunicorn through
  `scripts/serve-after-checks.sh`: `manage.py check --deploy --fail-level
  ERROR`, then `exec` gunicorn. A refused configuration exits non-zero before
  anything listens, so Compose restarts it and it never reports healthy.
  Warnings, the `--deploy` ones included, are printed and do not stop it. The
  intake reader, the search worker and their healthchecks are `manage.py`
  commands, which already ran the checks; a test now holds them to that.
- The container job in CI starts the built image both ways: a refused
  configuration must exit before gunicorn starts, and the CI configuration
  must reach gunicorn.

### What this amendment does not change

- No migration, and nothing on container start touches the database: the check
  reads settings only. Migrations stay a deliberate deployment step.
- `docker compose run --rm web python manage.py …` is unchanged: a command
  given on the command line replaces the start script, and `manage.py` checks
  on its own.
- An existing real-data deployment whose secrets are below the new floors
  stops serving on the release that carries this. Step 6 of the release
  runbook (`run --rm web python manage.py migration_plan`) runs the new checks
  against the live environment file first, before the backup and before
  anything is replaced, so that is where it shows.

---

## Amendment, 2026-10-18 — the throttle must see the client, and the idle limit has bounds

- Status: accepted, amending «The throttle is a table, and it is per client»
  and the amendment of 2026-10-17 (the idle limit)
- Finding: JRF-02 of the 2026-10-18 re-audit

### What was decided before

Since the amendment of 2026-10-17 `CF-Connecting-IP` names the client only
where `TRUST_CF_CONNECTING_IP` (that is, `DJANGO_BEHIND_TLS_PROXY`) is set. A
missing flag behind the shared gate was the warning `juristid.E014`.
`SHARED_GATE_IDLE_SECONDS` took any integer.

### Why it is amended

Without the flag every request behind the tunnel arrives from the connector's
address, so the per-client throttle is one bucket for the whole department:
five wrong passwords from anybody lock everybody out. A warning is printed and
the process serves anyway — the department would find out by being locked out.

### What is decided now

- `juristid.E020` (Error): `REAL_DATA_ALLOWED` on, `AUTH_MODE` `shared_gate`
  or `local_password` (both throttle wrong passwords by client address), and
  `TRUST_CF_CONNECTING_IP` off. Release step 6 (`run --rm web python manage.py
  migration_plan`) runs it against the host's environment file before the
  backup and the swap. Where E020 applies, E014 is not repeated beside it; on a
  synthetic stack E014 stays a warning. `cloudflare_access` has no password
  throttle and is unaffected.
- `juristid.E021` (Error), under `shared_gate`: `SHARED_GATE_IDLE_SECONDS` below
  300 (the last-request stamp is written at most every five minutes,
  `shared_gate.LAST_SEEN_RESOLUTION`) or above `SHARED_GATE_SESSION_SECONDS`
  (it could never fire).
- `deploy/unraid-main/.env.example` documents `DJANGO_BEHIND_TLS_PROXY=1` as
  required; both real-data templates set it, and a test holds them to that.

### What this amendment does not change

The address rule itself, the throttle's per-client table and escalation, the 8 h
idle and 12 h absolute limits, and the development and test configurations.
