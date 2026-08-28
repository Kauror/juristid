# Secure Pilot Gate checklist

**No real Koda, member or otherwise confidential data may enter an environment
that has not satisfied every item below.** Nothing here is retired by being
inconvenient, and nothing here is satisfied by an engineer's opinion of it.

The application enforces the boundary in code as far as it can: `REAL_DATA_ALLOWED`
defaults to off, and system checks refuse to start a process that combines it
with development sign-in, with `DEBUG`, or with no authenticator at all
(`app/core/checks.py`, ids `juristid.E002`–`E004`, `juristid.E006`).

### How to read the statuses

This file was written before there was anything to deploy, and several of its
statuses said "not started" about work that had since been done — which is worse
than saying nothing, because a checklist nobody trusts is a checklist nobody
reads. Each row now says what the **repository can establish**, and stops there.

*In the repository* means a reviewed control exists here and its tests run in
CI. **Deployment-time verification required** means the control is somebody's to
confirm against the environment, and this repository cannot see it — no status
here is evidence about a running host, and none should be written as if it were.
`deploy/unraid-main/README.md` §"Before calling it live" is where the
environment side is actually checked.

| # | Requirement | Owner | Status |
| --- | --- | --- | --- |
| 1 | Approved hosting and HTTPS/TLS end to end | IT/security | partial — TLS terminates at Cloudflare and the stack publishes no host port (`deploy/unraid-main/compose.yml`); HSTS, secure cookies and the forwarded-scheme header are configured and checked (`juristid.E013`, `juristid.E014`). Approval of the hosting arrangement itself: **deployment-time verification required** |
| 2 | An authenticator that identifies the person, with no local password fallback | IT/security | partial — ADR 0016 replaced the Entra-only plan with `AUTH_MODE`. `cloudflare_access` is implemented and verifies a signed assertion against the team's published keys, provisions nobody, and refuses an unconfigured audience (`juristid.E007`). It is **not** the mode the real-data stack runs: that is `shared_gate`, which authenticates the door and not the person and is temporary by design. Which mode a deployment runs is **deployment-time verification required** |
| 3 | Central authorization verified across list, detail, search, counts, export and download paths | technical owner | in the repository — the chokepoint (ADR 0005), the child-projection rule (ADR 0038), the HTTP write boundary (ADR 0037) and the surfaces themselves all exist, with authorization suites over each |
| 4 | Encrypted managed PostgreSQL and encrypted document storage | IT/security | **deployment-time verification required** — a property of the host's disks, which this repository cannot see and must not claim |
| 5 | Secret management; no secret in Git, image or client bundle | technical owner | in the repository — no secret in Git, the image, a Compose default or a backup set; the preflight refuses a `.env` beside the Compose file. Where the environment file is *kept* is an open decision (`docs/open-decisions.md`) |
| 6 | Upload type/size controls plus a malware scanning and quarantine path | technical owner | partial — allow-list and size limit enforced; scanning state column exists, scanner not wired. Real-archive text extraction stays blocked on this (`docs/production-readiness.md` 3.7) |
| 7 | Backup taken **and a restore successfully verified** | IT + technical owner | partial — the *procedure* is proven on every pull request: CI's `recovery` job creates a set with the production script, destroys the database and both storage trees, restores with the production script, and compares the canonical fingerprint. That proves the procedure and **no particular production set**; restoring one of those is **deployment-time verification required**. Separately, the backups are a local recovery copy on the same failure boundary as the data — an off-host destination has not been chosen and this file does not imply one ([`RECOVERY.md`](../deploy/unraid-main/RECOVERY.md)) |
| 8 | Documented developer and support access policy | management + IT | not started — named in `docs/open-decisions.md` with an owner |
| 9 | Retention and lawful-basis decision for raw email and member feedback | privacy/legal | open decision |
| 10 | No production or member data copy on developer or home machines | technical owner | in force — and the controls that keep it so are named in `deploy/unraid-main/README.md` §"Never run the application test suite through this stack" |

## Additional production gates (beyond the pilot gate)

- MFA and Conditional Access as required by Koda.
- Offboarding drill.
- A second break-glass administrator and continuity path.
- Monitoring, security review and recovery runbooks.
- An independent security/penetration assessment appropriate to exposure.

## Sign-off

The gate is passed only when the business owner and the security/privacy owner
record their approval, with the date, against this checklist. No row above
records such an approval, and an unsigned checklist is not a passed one however
many of its rows read well.

| Role | Name | Date | Signature/reference |
| --- | --- | --- | --- |
| Business owner | | | |
| Technical owner | | | |
| Security/privacy owner | | | |
