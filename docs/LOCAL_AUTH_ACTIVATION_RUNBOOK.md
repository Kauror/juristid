# Local authentication — activation runbook

**Status: DORMANT.** Personal sign-in (`AUTH_MODE=local_password`) and account
administration (Haldus → Kasutajad) are built, tested and deployed **inactive**
(docs/adr/0145). This document is the procedure for a *later, separately
approved* activation. **Nothing in it has been done.** Do not perform any step
without the owner's explicit approval for the activation itself.

---

## 0. The exact inactive state after the 2026-10-08 release

| | state |
| --- | --- |
| `AUTH_MODE` | `shared_gate` in `/mnt/user/appdata/juristid-main/config/juristid.env` — unchanged |
| Personal sign-in pages (`/konto/sisene/`, `/konto/aktiveeri/`, `/konto/parool/…`, `/konto/profiil/`, `/konto/turvalisus/…`, `/konto/kinnita/`) | 404 |
| Haldus → Kasutajad (`/haldus/kasutajad/…`) | 404 — for every persona, including one whose account holds `accounts.manage` |
| Shared gate, persona selection, persona list | exactly as before (`tests/test_local_auth_production_safety.py`) |
| Account e-mail | off: `ACCOUNT_EMAIL_DELIVERY_ENABLED` unset (false); `EMAIL_BACKEND` = `DeliveryDisabledBackend` (delivers nothing); no SMTP settings exist; `juristid.E036` refuses delivery beside the shared gate |
| Accounts | unchanged: every existing row reads `provisioning_state=ACTIVATED`, no capability overrides, `security_epoch=0`, no local password, `last_authenticated_at` empty |
| Administrators | none. No account holds `accounts.manage` / `accounts.delegate`; `bootstrap_account_admin` has not been run and refuses outside `local_password` |
| Schema | `accounts/0004_local_authentication`, `audit/0035_local_authentication_events` applied — additive, no data rewritten |
| Sessions | untouched: at epoch 0 the session hash is Django's own, so the deploy signed nobody out |
| Business access | unchanged: capability defaults equal the role rules they replaced |

Proving it on the live host, read-only (inside `juristid-main-web`):

```bash
docker exec juristid-main-web python manage.py shell -c "from django.conf import settings; print(settings.AUTH_MODE, settings.ACCOUNT_EMAIL_DELIVERY_ENABLED, settings.EMAIL_BACKEND)"
```

```bash
docker exec juristid-main-web python manage.py shell -c "from app.accounts.models import User; print(User.objects.exclude(capability_overrides={}).count(), User.objects.exclude(local_password_set_at=None).count(), User.objects.exclude(security_epoch=0).count())"
```

Expected: `shared_gate False app.accounts.mail.DeliveryDisabledBackend`, then `0 0 0`.

---

## 1. Decisions that must exist before activation starts

Write each down, with who decided it:

1. **Owner approval** for switching production authentication, with a date and
   a maintenance window. The shared gate stops working at the switch.
2. **The final HTTPS address** the department will use (today
   `https://juristid.orgusaar.ee` behind the Cloudflare tunnel). One-time links
   are built from it.
3. **The mail transport**: which SMTP relay (Microsoft 365 / Exchange Online
   authenticated SMTP, or a relay the Chamber operates), which sender address,
   and who owns its credentials. SPF/DKIM/DMARC for the sender domain must allow
   it, or invitations land in spam.
4. **The approved real identities**: for every person, their real `@koda.ee`
   address, display name and business role. The three shared-gate accounts with
   placeholder sign-in names (`marko`, `ireen`, `sandra`) get their real
   addresses in step 8. Ann still has no account.
5. **The first administrator** (one person), and at least one **second**
   delegating administrator appointed straight after, so the department is never
   one lost phone away from having nobody who can administer accounts.
6. **The second-factor policy**: administrators always; whether
   `LOCAL_AUTH_MFA_REQUIRED_FOR_ALL=1` for everybody (recommended for a
   confidential legal register).
7. Whether to supply a **breached-password list**
   (`LOCAL_AUTH_BREACHED_PASSWORDS_PATH`, e.g. a top-N extract of a public breach
   corpus, one password per line, gzipped). Django's 20,000-entry list applies
   regardless. An online breach API is a separate decision (it sends a hash
   prefix off the host on every password change).

---

## 2. Verified backups (OPERATOR.md, deploy/unraid-main/README.md)

Before touching the environment file:

```bash
/mnt/user/appdata/juristid-main/repo/scripts/deploy/juristid-backup.sh
```

Verify the new set at level 2 as the release runbook does, and record the set
name. Keep a copy of the current environment file beside it:

```bash
cp -p /mnt/user/appdata/juristid-main/config/juristid.env /mnt/user/appdata/juristid-main/config/juristid.env.bak-local-auth-$(date -u +%Y%m%dT%H%M%SZ)
```

---

## 3. Secrets (host-side only — never in Git, Compose, the image, CI or a log)

Generate each on the host and put it **only** in `juristid.env` (mode 600):

```bash
openssl rand -base64 48
```

| variable | what | note |
| --- | --- | --- |
| `LOCAL_AUTH_MFA_ENCRYPTION_KEY` | encrypts TOTP secrets at rest | Required on real data (`juristid.E033`). **Losing or changing it unenrols every authenticator** — every person must enrol again. Back it up with the same care as the database dump, separately from it. Rotating `DJANGO_SECRET_KEY` does *not* affect it. |
| `DJANGO_EMAIL_HOST_PASSWORD` | SMTP credential | From the mail transport owner. |

`DJANGO_SECRET_KEY` stays as it is. Rotating it later invalidates sessions and
outstanding one-time links (and the throttle counters), not passwords or
authenticators.

---

## 4. Mail transport (configured, verified, then enabled)

Add to `juristid.env` — **with delivery still off**:

```
DJANGO_EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
DJANGO_EMAIL_HOST=<relay host>
DJANGO_EMAIL_PORT=587
DJANGO_EMAIL_USE_TLS=1
DJANGO_EMAIL_HOST_USER=<relay user>
DJANGO_EMAIL_HOST_PASSWORD=replace-me-from-step-3
ACCOUNT_EMAIL_FROM=<approved sender, e.g. juristid@koda.ee>
ACCOUNT_LINK_BASE_URL=https://<final address from step 1>
ACCOUNT_EMAIL_DELIVERY_ENABLED=0
```

Verify the transport *before* the switch with one message to an approved test
mailbox the operator controls (not a colleague):

```bash
docker exec juristid-main-web python manage.py sendtestemail <approved-test-mailbox>
```

`ACCOUNT_EMAIL_DELIVERY_ENABLED=1` is set in step 6, together with the mode —
`juristid.E036` refuses it while the mode is still `shared_gate`.

---

## 5. Settings for the switch

The complete diff to `juristid.env` at the switch (everything else unchanged):

```
AUTH_MODE=local_password
ACCOUNT_EMAIL_DELIVERY_ENABLED=1
LOCAL_AUTH_MFA_ENCRYPTION_KEY=replace-me-from-step-3
LOCAL_AUTH_MFA_REQUIRED_FOR_ALL=<0 or 1, from step 1>
# optional:
# LOCAL_AUTH_BREACHED_PASSWORDS_PATH=/app/config/breached-passwords.txt.gz
```

Keep `DJANGO_BEHIND_TLS_PROXY=1` (HTTPS enforcement; `juristid.E035`). Leave the
defaults for the limits unless there is a reason:

| setting | default |
| --- | --- |
| `LOCAL_AUTH_SESSION_IDLE_SECONDS` | 3600 |
| `LOCAL_AUTH_SESSION_ABSOLUTE_SECONDS` | 36000 |
| `LOCAL_AUTH_REAUTH_SECONDS` | 900 |
| `LOCAL_AUTH_ACTIVATION_LINK_SECONDS` | 172800 |
| `LOCAL_AUTH_RESET_LINK_SECONDS` | 3600 |
| `LOCAL_AUTH_ARGON2_MEMORY_KIB` / `_TIME_COST` / `_PARALLELISM` | 65536 / 2 / 1 |

**Remove** `JURISTID_SHARED_GATE_PASSWORD` from the file at the switch (comment
it out with the date): it no longer guards anything, and a retired secret left in
place is one somebody may reuse.

Before restarting, check the configuration in a throwaway container from the
running image with the new file (the same pattern as the release runbook's
`run --rm`):

```bash
docker compose -p juristid-main -f /mnt/user/appdata/juristid-main/repo/deploy/unraid-main/compose.yml run --rm --no-deps web python manage.py check --deploy
```

It must report no `juristid.E0xx` error. Typical refusals and their meaning:
`E033` (no MFA key), `E035` (no HTTPS signal), `E036` (delivery on in the wrong
mode), `E037` (link base not https / no sender), `E038` (non-delivering backend).

---

## 6. The switch (maintenance window)

1. Announce the window; the shared gate stops at step 3.
2. Fresh backup (step 2) if the last one is older than the window.
3. Apply the step-5 environment, then restart only the application services, the
   way the release runbook does (never an unqualified `up -d`; db and tunnel stay):

   ```bash
   JURISTID_IMAGE_TAG=<current tag> docker compose -p juristid-main -f /mnt/user/appdata/juristid-main/repo/deploy/unraid-main/compose.yml up -d --no-build --no-deps web intake-reader searchindex
   ```

4. Confirm the mode from the wire: `/konto/sisene/` → 200 with the e-mail and
   password form; `/konto/varav/` no longer offers the department password (it
   redirects to `/konto/sisene/` while signed out and is a 404 once signed in).
   Every persona session is signed out at its next request (the middleware ends
   any session not signed in under `local_password`, audited as `SESSION_ENDED`
   `not_signed_in_here`).

---

## 7. The first administrator

```bash
docker exec -it juristid-main-web python manage.py bootstrap_account_admin --email <first-admin>@koda.ee --confirm-email <first-admin>@koda.ee --display-name "<Name>" --role <their business role> --note "<operator name>, activation <date>"
```

- With delivery on, the activation link is mailed to that address.
- Without delivery, add `--print-activation-link` and run it from an
  **interactive** terminal (`docker exec -it`); it refuses a pipe or a captured
  output. Hand the link to the person directly; it is single-use and expires.
- If the address belongs to an existing account (e.g. after its address has
  been corrected), that account is appointed in place: same identity, same work,
  no password set.

The administrator then: opens the link → sets their own password → signs in →
**must enrol an authenticator** before anything else → stores the ten recovery
codes offline.

Then, immediately: appoint the **second delegating administrator** (step 1.5)
through Haldus → Kasutajad, and let them complete activation and enrolment.

---

## 8. Existing people, and new ones

In Haldus → Kasutajad, as an administrator:

1. For each shared-gate account with a placeholder sign-in name, **Muuda**
   the address to the person's real `@koda.ee` address (delegating
   administrators only; the account keeps its identity, its work and its
   history).
2. For each existing account: **Saada parooli seadistamise link**. The person
   sets their first password; nothing else about the account changes.
3. New people: **+ Uus kasutaja** → **Kinnita ja saada kutse**.
4. Set capabilities where the defaults are not wanted (e.g. lend
   `Osakonna juhtimisvaade` to a deputy).
5. Accounts nobody should use: **Lülita välja** — never deleted; the register
   keeps naming them on their work.

---

## 9. Second-factor readiness

- Every administrator enrolled, recovery codes stored offline.
- If `LOCAL_AUTH_MFA_REQUIRED_FOR_ALL=1`: tell the department beforehand that the
  first sign-in asks for an authenticator app (Microsoft Authenticator, Google
  Authenticator or any RFC 6238 app).
- Lost phone: an administrator uses **Lähtesta teine tegur** on that account; the
  person enrols again at the next sign-in. An administrator's own factor is reset
  by *another* delegating administrator — hence step 7's second administrator.

---

## 10. Testing with the approved identities

With the first two administrators and one approved colleague, on production
after the switch, in this order:

- sign in with password + code; sign out; sign in again;
- a wrong password, then the right one (one refusal sentence for both);
- forgotten password → link → new password → sign in;
- administrator creates a test account **with an approved test address**,
  invites it, the link arrives, activation works; then switch that account off;
- grant and withdraw a capability on a colleague and see their open session end;
- try Haldus → Kasutajad as a colleague without the capability → 404.

---

## 11. Removing shared-persona access

The switch itself removes it: every shared-gate route is a 404 under
`local_password`, persona sessions are signed out, and the gate password was
removed from the environment in step 5. Confirm:

- `/konto/varav/` and `/konto/kasutaja/` offer nothing: a redirect to
  `/konto/sisene/` while signed out, a 404 once signed in;
- no `SHARED_GATE_PASSED` / `PERSONA_SELECTED` audit rows after the switch time;
- the persona pill is gone from the bar.

---

## 12. Production smoke after the switch

- `/healthz` 200; `manage.py production_status` all PASS; `deployment_readiness` clean.
- `/konto/sisene/` renders; a signed-in person reaches Minu asjad, Osakond, Teemad.
- `SecurityAuditEvent` shows `AUTHENTICATION_SUCCEEDED` with
  `authenticated_via = LOCAL_PASSWORD` for the test sign-ins.
- No `juristid.E0xx` in `docker logs juristid-main-web` since the restart.
- No one-time link, password or code in `docker logs` (spot-check: the access log
  records paths only; `?kood=` never appears).

---

## 13. Rollback

Rollback is a configuration change; the schema stays (it is additive and the
shared gate ignores it).

1. Restore the pre-switch environment file copy from step 2 (it has
   `AUTH_MODE=shared_gate` and the gate password; `ACCOUNT_EMAIL_DELIVERY_ENABLED`
   unset).
2. Restart the same services as in step 6.
3. Confirm `/konto/varav/` answers with the password form and `/konto/sisene/`
   offers nothing (a redirect to the gate, then a 404 behind it). Personal sessions
   are signed out by the shared-gate middleware (a persona-less session without
   the gate flag is logged out).
4. Passwords, authenticators, capabilities and the audit trail stay in the
   database, inert, for a later retry. If the rollback is permanent, an operator
   may clear capability overrides with the owner's approval; nothing needs
   deleting.

A database restore is **not** part of rollback: nothing in the switch writes
business data.

---

## 14. Never

- Never run the shared gate and personal sign-in together; there is no such
  configuration, and do not try to build one.
- Never set a password for somebody else, read one out, or send one by e-mail.
- Never give account administration `is_staff`/`is_superuser`, or use the Django
  admin to change roles or capabilities.
- Never point `config.e2e_local_auth_settings` or `config.e2e_gate_settings` at a
  deployment.
- Never run `bootstrap_account_admin` with its output captured to a file or a
  log when printing a link.
