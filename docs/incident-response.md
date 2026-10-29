# Incident response — six scenarios, what exists and what does not

Written from the 2026-10-29 tabletop exercise of the security and resilience
review. Each scenario says how it would be detected **today**, what contains
it, what recovers it, how that is verified, who decides, roughly how long it
takes — and names the gaps as GAP rather than writing a procedure that does
not exist. The commands and sections cited are the ones that exist; nothing
here is new procedure.

Decision-makers throughout: the **owner** (the one administrator and host
operator) acts; the **department head** decides what the department is told;
the **privacy owner** (docs/open-decisions.md, "Required before the Secure
Pilot Gate") decides what members are told. Record every incident in one place
the owner names — GAP: no incident log exists.

## 1. The shared-gate password is compromised

- **Detection.** Nobody is told. `SHARED_GATE_PASSED`, `PERSONA_SELECTED`,
  `DOCUMENT_DOWNLOADED`, `EXPORT_GENERATED` rows carry the client address and
  user agent (`app/audit/enums.py`); `manage.py audit_failures` counts the
  failures and the monitor's `auth` row warns over the thresholds — a leak
  that is *used*, not guessed, shows in neither. Realistically a colleague
  reports it.
- **Containment.** README "Rotating the password": a **new** password (never a
  previous one) in `juristid.env`, then **recreate** the application services
  (`up -d --no-build --no-deps web intake-reader searchindex intake-sweeper
  notifications`; a `restart` keeps the old environment). ADR 0183 ends every
  gate session at its next request. MCP tokens and administrator sessions are
  independent and stay valid; decide separately whether `mcp_access
  revoke-all` is warranted (docs/mcp/OPERATIONS.md).
- **Recovery.** Tell the department out of band. Exposure review: audit rows
  in the window by addresses not seen before — GAP: no query; a `manage.py
  shell` session against `SecurityAuditEvent`.
- **Verification.** README "Before calling it live" A–D in a private window;
  the `SESSION_ENDED` burst with `reason = shared_password_changed`.
- **Time.** Rotation 15 min; exposure review hours.
- **Gaps.** No detection of use; no exposure-review command; the shared
  password cannot be revoked for one person (by design — ADR 0186 for
  administrators, `cloudflare_access` for readers, later); no communication
  template.

## 2. The administrator's MFA device is lost; separately, the MFA key is lost

**2a. The device.** The administrator knows. A stored recovery code at
`/haldus/sisene/`; failing that, on the host, `manage.py reset_second_factor
--email … --note …`, then re-enrol at the next step-up (ADR 0186 status
table). The in-app route — another administrator resets it — does not exist
with one administrator. Minutes with host access; **no path** if the recovery
codes are not stored and host access is lost too (accepted, ADR 0186). GAP:
whether the recovery codes are stored anywhere is not recorded; the leftover
`accounts.manage` override on a non-administrator account makes
`bootstrap_account_admin` refuse (`BOOTSTRAP_ALREADY_DONE`), so the emergency
route to a new first administrator is closed until it is resolved.

**2b. `LOCAL_AUTH_MFA_ENCRYPTION_KEY`.** The correct code is refused at step-up
(docs/LOCAL_AUTH_ACTIVATION_RUNBOOK.md §15). First get the key back: the
copies that exist are the host's nightly USB snapshot of `config/` and the
weekly Appdata Backup tar — plaintext, same host (RECOVERY.md "Host-level
copies"). GAP: no escrow outside the host (DR1-C); restoring one file from the
USB snapshot is written nowhere. Key truly gone: `reset_second_factor
--list-undecryptable`, then per account, re-enrol; recovery codes keep
working. `MCP_AUTHENTICATOR_KEY` lost the same way costs every colleague's AI
enrolment (RECOVERY.md "Secrets").

## 3. The KOCORP host is lost

- **Detection.** Lawyers find the site down (Cloudflare 530). Every check on
  the host dies with it. GAP: no Cloudflare tunnel-health notification, no
  dead-man's switch (docs/alerts.md).
- **Disk death, array intact, USB disk intact.** The array has no parity
  (confirmed 2026-10-29). Rebuild from the USB snapshot: it holds appdata
  (secrets, cloudflared credentials, evidence), `/mnt/user/backups` (every
  set and pool) and the source share. Order: RECOVERY.md "Restoring" — the
  release image from the GitHub artifact or a CI rebuild of the exact commit
  (never built on the host), secrets by hand from the snapshot, `db` only,
  evidence, the dump via `juristid-restore.sh`, readiness,
  `recovery_fingerprint --compare`, derivatives and search rebuilds, tunnel,
  then people. 1–2 working days; a whole-host rebuild has never been
  rehearsed.
- **Fire, theft, ransomware with root while the USB disk is mounted.**
  **Unrecoverable** today except the historical corpus by re-export and the
  100 pilot Teemad from Lovable. Everything written in Juristid since the
  2026-09-21 reset is lost. Accepted by the owner on 2026-10-13 (RECOVERY.md
  Scenario D, OFFHOST-BACKUP.md) — before the facts "no parity", "Appdata
  Backup failing", "no alert recipient" were established; the review asks for
  the acceptance to be re-confirmed or the OFFHOST list to be worked.
- **Decision-maker.** Owner rebuilds; management accepts the loss window and
  the off-host decision.

## 4. Evidence files are corrupted or deleted

- **Detection.** A lawyer opens a document and gets an error; the monitor's
  weekly `evidence` row (structural). `--verify-sha` reads every byte and is
  run by hand.
- **Containment.** If unexplained: stop the application services (keep `db`),
  take the appdata SMB share off the network, then `check_evidence_integrity`
  and `--verify-sha`.
- **Recovery.** RECOVERY.md Scenario C. The pool under
  `/mnt/user/backups/juristid-main/evidence/` is append-only and immutable,
  so the bytes are there if the backup tree survived. GAP:
  `juristid-restore.sh` restores into an *empty* deployment; restoring only
  the missing or mismatched objects into a live tree from the pool and the
  set's membership inventory is not written as a procedure.
- **Verification.** `check_evidence_integrity --verify-sha` exit 0; the
  evidence digest of `recovery_fingerprint` equal to the last release's when
  nothing was added since; the documents open.
- **Decision-maker.** Owner; the department head is told which Matters lost
  material if any object is unrecoverable.

## 5. An integration credential is compromised

| Credential | Detection | Containment and rotation | Gap |
| --- | --- | --- | --- |
| MCP OAuth client, a grant, tokens | `McpAccessEvent` rows (`REFRESH_REUSE_DETECTED`, `TOKEN_REFUSED`, `GRANT_SUSPENDED`), counted by `audit_failures` and the monitor's `auth` row | OPERATIONS.md: stop `mcp-tunnel` and `mcp`, `mcp_access revoke-all` (or `revoke --user`), `MCP_ENABLED=0`; writes: APPROVAL-RUNBOOK.md "Switching writes off". Keys: `juristid-mcp-web-keys.sh` (derivation key — one reconnect; authenticator key — every enrolment), `juristid-mcp-writer-keys.sh --rotate-gateway-key`, `mcp_db_role` / `mcp_writer_db_role --apply`, the OpenAI runtime key at platform.openai.com then recreate `mcp-tunnel` | well covered; detection now exists as a daily count |
| Lovable service login (`config/lovable-sync.env`) | Lovable's side only; the login is read-only there (ADR 0192) | `touch l100/sync/PAUSED` or `lovable_sync control --mode off`; the owner changes the login at Lovable and rewrites the file (`KEY=value`, no quotes, root 600); run `lovable_sync access-probe` before resuming | GAP until now: not written as a procedure; blast radius is read access to Lovable's copy of the pilot Teemad |
| Cloudflare tunnel credential (`cloudflared/*.json`) | the Cloudflare dashboard shows a second connector; nobody looks. A stolen credential receives traffic for `juristid.orgusaar.ee` — the gate password with it | RECOVERY.md "Cloudflare tunnel recovery" covers *loss*. For *compromise* add: **delete the old tunnel** (`cloudflared tunnel delete <old>` or the dashboard) so the stolen credential stops working; then rotate the gate password (scenario 1); then check the connector list | GAP: the delete step and the rotation are not in the runbook |
| GitHub account or token | an unreviewed commit on `origin/main` — caught only at a release by the OPERATOR.md STOP list and the provenance gate | rotate the token or password; `git log origin/main` since the last release; re-verify the deployed image's `/app/GIT_SHA`; the host never builds and verifies the artifact's SHA-256 | GAP: no procedure; whether a token lives on the host is not recorded |

## 6. A deployment fails and must be rolled back

- **Detection.** During the release: OPERATOR.md STOP list and README step
  10 post-flight; after it: a lawyer reports, or the monitor's `healthz`,
  `containers` and `status` rows.
- **Containment.** Stop where the sequence is; keep the output.
- **Recovery.** Code-only or additive schema: README "Rolling back" with the
  previous reviewed SHA, whose image is still on the host (OPERATOR.md
  "keep the previous release's image"); minutes. Across a non-additive
  migration: restore the set taken at README step 8 into an *empty*
  deployment, losing everything written since (RECOVERY.md "Rolling back a
  deployment", the `matters.0049` case, the partial-migration case); 1–3 h
  plus the lost window. A data migration that wrote wrong data: a backup or a
  reviewed corrective migration.
- **Verification.** `deployment_readiness`; `/healthz` revision equals the
  rolled-back SHA; `recovery_fingerprint --compare`; `/uuendused/`; README
  A–M; `production_status`.
- **Decision-maker.** Owner for code-only; owner and department head when a
  restore discards work.
- **Gaps.** DR1-A (no set after a deploy, so the newest set is always the
  previous release — the nightly wrapper in `deploy/unraid-main/cron/` closes
  it once scheduled); a rollback across a migration has never been rehearsed
  on production data; the release kits' rollback scripts live outside the
  repository.
