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
  at least 12 characters (`juristid.E010`, `E011`)
- it is hashed once at process start with Django's hasher and compared with the
  framework's constant-time check — the plaintext is never written to the
  database, a log, a template or the client
- failures are throttled per client with an escalating, **capped** lockout
  (`E012`)
- the session cookie is `Secure` (`E013`), `HttpOnly`, `SameSite=Lax`, and the
  session identifier is rotated on success
- `DJANGO_DEBUG=0` and `DEV_LOGIN_ENABLED=0` (`E004`, `E008`)

`REAL_DATA_ALLOWED=1` with `AUTH_MODE=none` still fails (`E006`). The check was
not loosened; a second mode was added to the small set it accepts.

### The throttle is a table, and it is per client

Django's database cache would be shared across gunicorn workers and would
survive a restart — but it is *evictable*: it culls a third of its rows when it
grows past `MAX_ENTRIES`. A lockout an attacker can flush by making noise is not
a lockout. So `SharedGateThrottle` is a table.

It is keyed on an HMAC of the forwarded address, never global. A global counter
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

`AUTH_MODE=cloudflare_access`, once the Access application exists. At that
point `authenticated_via` becomes a claim the deployment can support, the
persona selector goes away, and this ADR becomes history rather than
description.
