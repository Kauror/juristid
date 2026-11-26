# 0235 — A multipart body is parsed only for a signed-in session

Date: 2026-11-26 · Status: proposed (security round 26.11.2026, SEC-013; prepared and tested only — nothing merged or deployed, no ingress or gunicorn setting changed)

## Context

The Codex Security scan of e49a0683 (finding SEC-013, MEDIUM; operationally high):
multipart bodies were parsed before the authentication state was established.

Django parses a ``multipart/form-data`` body the first time anything touches
``request.POST`` or ``request.FILES``, streaming every part through
``FILE_UPLOAD_HANDLERS`` and spooling files to temporary storage. In
`config/settings.py` the first such toucher was `UploadLimitMiddleware` (placed
early on purpose, ADR 0182: the upload policy answers 413 before any view), then
`RefuseNulMiddleware` (reads every POST field) and `UploadTitlesMiddleware` (reads
FILES and POST) — all before `AuthenticationMiddleware` and
`AuthenticationModeMiddleware`, the one that refuses an unauthenticated request.
`CsrfViewMiddleware` reads POST in ``process_view``, which Django runs after every
middleware's ``__call__`` preamble, i.e. after the mode middleware; on a CSRF
failure its reject path reads nothing more. So an anonymous request that could
reach the tunnel had its whole body (up to ``UPLOAD_MAX_REQUEST_BYTES``, 512 MiB,
100 files) received, parsed and written to temporary files by a web worker, and
was refused by the gate only afterwards.

Audit of every reader before authentication (26.11.2026):

| Reader | Reads | Before the mode middleware? |
|---|---|---|
| `UploadLimitMiddleware.__call__` | ``request.POST`` (multipart only) | yes |
| `RefuseNulMiddleware.__call__` | ``request.POST`` (every POST) | yes |
| `UploadTitlesMiddleware.__call__` | ``request.FILES``, ``request.POST`` | yes |
| `CsrfViewMiddleware.process_view` | ``request.POST`` for the token | no (runs after all ``__call__``s) |
| `AuditContextMiddleware`, `SessionMiddleware`, `LocaleMiddleware`, `CommonMiddleware`, `MethodNotAllowedPageMiddleware`, `BrowserPolicyMiddleware`, WhiteNoise | session, headers, response | read no body |
| the sign-in views (gate, persona, local sign-in, one-time links, admin sign-in, MCP OAuth) | ``request.POST`` | run unauthenticated by design |

## Decision

1. **`app.documents.multipart_boundary.MultipartAuthBoundaryMiddleware`**, placed
   after `SessionMiddleware` and before every body reader above, decides from the
   session and the headers alone — never the body — whether a multipart request
   (POST/PUT/PATCH/DELETE with ``Content-Type: multipart/form-data``) may be parsed.
2. **Elsewhere than the sign-in surfaces**, only a session that already carries an
   active signed-in person who passes the deployment mode's own test, read as
   `AuthenticationModeMiddleware` reads it: ``shared_gate`` — the gate still open
   (`shared_gate.has_passed`: both its limits and the current password);
   ``local_password`` — a session signed in here and inside its limits;
   ``cloudflare_access`` — a verified assertion naming the same person; ``none``
   (development and CI) — a signed-in person. Anything else is refused before a
   byte of the body is read: htmx gets **401** with ``X-Juristid-Failure:
   sign-in`` and ``X-Juristid-Sign-In`` (the shape `HtmxSignInMiddleware` already
   gives and `static/js/app.js` already tells as «session ended»), a navigation a
   **403** page. ``Cache-Control: no-store``.
3. **The sign-in surfaces** (`PUBLIC_AUTH_ROUTES`: dev sign-in, the gate, persona
   choice and switch, sign-out, local sign-in and its second factor, activation,
   forgotten and reset password, re-authentication, the admin's sign-in, the MCP
   OAuth authorize/token/revoke and enrolment pages) must answer before anybody is
   signed in, and none takes a file. A multipart body there is parsed only when it
   declares a ``Content-Length`` of at most **64 KiB** (`PUBLIC_FORM_MAX_BYTES`); a
   larger or undeclared (chunked) one is refused with **413** unread. Browsers send
   these forms urlencoded; the cap keeps a scripted or test client's small
   multipart form working while no unauthenticated request can make the server
   parse a large one.
4. The unread body is not drained: gunicorn's sync worker closes every connection
   after its answer (``resp.force_close()``). A WSGI application may not set the
   hop-by-hop ``Connection`` header itself, so none is set. Nothing is written, so
   there is no temporary file to clean up.
5. **Unchanged for a signed-in request**: the upload policy (ADR 0182) and its 413,
   CSRF (still refused as forged), the NUL check, the upload titles, file
   validation, the mode middleware's full checks, the HTMX answers, the shared-gate
   login and persona selection (urlencoded forms), admin authentication and the
   dormant modes. ``application/x-www-form-urlencoded`` is not covered: it is read
   whole, but only up to ``DATA_UPLOAD_MAX_MEMORY_SIZE`` (10 MiB), which Django
   checks against the declared length before reading.

## Defence in depth (reviewed, not changed)

* **Ingress.** Cloudflare's proxy limits a request body by plan (100 MB on the
  Free and Pro plans), below the application's 512 MiB policy; no change is
  proposed without evidence of a legitimate packet that size.
* **gunicorn.** 3 sync workers, ``--timeout 120``: a worker stuck receiving a body
  is killed after 120 s; `--limit-request-line`/`--limit-request-fields` keep their
  defaults (headers only). Sync workers do not keep connections alive.
* **Upload policy.** ``UPLOAD_MAX_REQUEST_BYTES`` 512 MiB, 100 files,
  ``MAX_EVIDENCE_UPLOAD_BYTES`` 100 MiB per file, unchanged: no capacity reduction
  without evidence and the owner's approval.
* A signed-in person can still make one worker parse up to the policy's limit; that
  is the upload feature itself.

## Residual risk

* An authenticated session (including the shared-gate persona, an accepted
  residual risk of the shared gate) can send large uploads; bounded by the policy.
* ``cloudflare_access`` verifies the assertion (RS256 against cached keys) before a
  multipart body is parsed; a request with a valid assertion for a person whose
  session has not yet been established is refused until a first non-multipart
  request signs them in. Every upload form is reached by a GET first.

## Deployment and rollback

Image-only: the middleware and its settings entry ship in the image; no migration,
no host change. Rollback is the previous image.
