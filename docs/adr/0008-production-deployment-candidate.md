# 0008 — Production deployment candidate

- **Status:** Proposed, deliberately reversible (Stage 0)
- **Date:** 2026-08-18
- **Scope note (2026-08-22):** this ADR is about the eventual *hosting platform*
  and that question is still open. It does not describe how the system is
  deployed today. The real-data instance runs on an Unraid host, and how it is
  deployed, backed up and restored is
  [ADR 0021](0021-deployment-backup-and-recovery.md) with
  `deploy/unraid-main/README.md` and `deploy/unraid-main/RECOVERY.md`.

## Context

The specification names Azure Container Apps as a strong default and Azure App
Service as a valid simpler alternative, and says the choice should be made on
Koda's subscription and operations reality rather than on product logic. That
reality — subscription ownership, networking posture, whether the application is
internet-facing or VPN-only — is an open business decision.

## Decision

**Keep the choice open, and make it cheap to make later.** Stage 0 commits only
to what both options need:

- one immutable container image as the deployment unit;
- all configuration through environment variables (`config/env.py`), no
  environment-specific settings modules;
- no secret in the image; a build-time revision stamp (`APPLICATION_REVISION`)
  only;
- migrations applied as a **separate controlled deployment step**, never on
  container start (the container `CMD` runs gunicorn; only the development
  compose entrypoint migrates);
- a `/healthz` endpoint that checks database connectivity, wired to the image
  `HEALTHCHECK`;
- scheduled work as Django management commands run from the same image, so
  either platform's job runner can invoke them.

**Current leaning:** Azure Container Apps, because scheduled jobs, revision-based
rollback and scale-to-low fit a six-user internal system with nightly exports.
App Service remains viable if Koda's operations team already runs App Service
and prefers not to add another resource type.

**Decision owner and timing:** management/IT, required before the Secure Pilot
Gate resources are allocated. This ADR is superseded then.

**Fixed regardless of platform:** managed PostgreSQL, Azure Blob for evidence,
Entra identity, TLS everywhere, an independent backup copy with a failure
boundary different from the primary tenant where practical.

**Not adopted:** Kubernetes, service mesh, Dapr, an event bus, Celery/Redis.
None has a measured need at this scale.

## Consequences

- Nothing in the codebase assumes a hosting service.
- The eventual choice is an infrastructure exercise, not an application change.

## Reversibility

High, and preserved intentionally.
