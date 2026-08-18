# Secure Pilot Gate checklist

**No real Koda, member or otherwise confidential data may enter any environment
until every item below is verified and signed off.** Until then the system runs
on synthetic data only.

The application enforces the boundary in code as far as it can: `REAL_DATA_ALLOWED`
defaults to off, and system checks refuse to start a process that combines it
with development sign-in or with `DEBUG` (`app/core/checks.py`, ids
`juristid.E002`–`E004`).

| # | Requirement | Owner | Status |
| --- | --- | --- | --- |
| 1 | Approved hosting and HTTPS/TLS end to end | IT/security | not started |
| 2 | Microsoft Entra ID authentication, no local password fallback | IT/security | not started |
| 3 | Central authorization verified across list, detail, search, counts, export and download paths | technical owner | partial — the chokepoint and its tests exist (ADR 0005); the surfaces do not exist yet |
| 4 | Encrypted managed PostgreSQL and encrypted document storage | IT/security | not started |
| 5 | Secret management; no secret in Git, image or client bundle | technical owner | in place for Stage 0 (`.env` gitignored, no secret in image) |
| 6 | Upload type/size controls plus a malware scanning and quarantine path | technical owner | partial — allow-list and size limit enforced; scanning state column exists, scanner not wired |
| 7 | Backup taken **and a restore successfully verified** | IT + technical owner | not started |
| 8 | Documented developer and support access policy | management + IT | not started |
| 9 | Retention and lawful-basis decision for raw email and member feedback | privacy/legal | open decision |
| 10 | No production or member data copy on developer or home machines | technical owner | in force |

## Additional production gates (beyond the pilot gate)

- MFA and Conditional Access as required by Koda.
- Offboarding drill.
- A second break-glass administrator and continuity path.
- Monitoring, security review and recovery runbooks.
- An independent security/penetration assessment appropriate to exposure.

## Sign-off

The gate is passed only when the business owner and the security/privacy owner
record their approval, with the date, against this checklist.

| Role | Name | Date | Signature/reference |
| --- | --- | --- | --- |
| Business owner | | | |
| Technical owner | | | |
| Security/privacy owner | | | |
