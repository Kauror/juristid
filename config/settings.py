"""Django settings for Koda Õigusloome (juristid).

One settings module, driven by environment variables. Environment-specific
behaviour is expressed through env values rather than a settings inheritance
tree, so a deployed container is configured the same way the local container is.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from config.env import (
    database_config_from_url,
    env,
    env_bool,
    env_float,
    env_int,
    env_list,
)

BASE_DIR = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------

DEBUG = env_bool("DJANGO_DEBUG", default=False)

# The insecure fallback only applies with DEBUG on; a system check refuses to
# start a non-debug process that is still using it (see app/core/checks.py).
DEV_INSECURE_SECRET_KEY = "django-insecure-local-development-only-do-not-deploy"
SECRET_KEY = env("DJANGO_SECRET_KEY", DEV_INSECURE_SECRET_KEY if DEBUG else "", required=not DEBUG)

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]" if DEBUG else "")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Applications
# --------------------------------------------------------------------------

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
]

# Domain modules of the modular monolith. These are code boundaries inside one
# deployment and one database, not services.
LOCAL_APPS = [
    "app.core",
    "app.accounts",
    "app.organisations",
    "app.taxonomy",
    "app.workflow",
    "app.matters",
    "app.documents",
    "app.submissions",
    "app.audit",
    "app.search",
    "app.legacy_import",
    "app.reporting",
    "app.intelligence",
    "app.related_materials",
    "app.mcp_access",
    "app.mcp_server",
    "app.mcp_writes",
    "app.notifications",
]

INSTALLED_APPS = DJANGO_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Content-Security-Policy and Permissions-Policy, beside the headers
    # SecurityMiddleware sets. HTML responses only, and never over a policy a
    # view already chose, so a served document keeps its stricter one
    # (app/core/browser_policy.py, ENG-124).
    "app.core.browser_policy.BrowserPolicyMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    # Every audit row written while this request runs records how its actor
    # arrived and one correlation id for the request. Early, so it wraps every
    # middleware that writes a row; the provenance itself is read from the
    # session when the row is written (app/audit/provenance.py, docs/adr/0180).
    "app.audit.provenance.AuditContextMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    # A 405 a view returned with no body gets the Estonian error page; inside
    # BrowserPolicyMiddleware, so the page carries the same policy headers as
    # every other (app/core/middleware.py, 2026-10-17 audit JUX-013).
    "app.core.middleware.MethodNotAllowedPageMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    # The first thing that parses a multipart body: a request over the upload
    # policy (UPLOAD_MAX_*) is answered 413 here, its temporary files already
    # closed, before the NUL check, CSRF's own read or any view touches a field
    # (app/documents/upload_limits.py, docs/adr/0182).
    "app.documents.upload_limits.UploadLimitMiddleware",
    # After the CSRF check, so a forged request is still refused as one; before
    # anything that reads a parameter. A NUL byte is a 400 here rather than a
    # 500 in whichever view first compares it with a text column (ENG-046).
    "app.core.middleware.RefuseNulMiddleware",
    # Pairs each uploaded file with the display title typed beside it in the
    # shared upload queue (`<field>__pealkiri`), once for every form.
    "app.core.middleware.UploadTitlesMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Before anything that reads business content, and it wraps the rest of the
    # stack so its `finally` runs even when a view raises. It holds one
    # request's authorization lookups and nothing else
    # (app/core/authorization.py, `remember_grants_for_one_request`).
    "app.core.middleware.RequestScopeMiddleware",
    # Around the authenticator, so it sees the gate's own redirect as well as
    # `login_required`'s, and turns either into a 401 for an htmx request
    # rather than letting the browser follow it into a fragment target
    # (app/core/middleware.py `HtmxSignInMiddleware`, ENG-012).
    "app.core.middleware.HtmxSignInMiddleware",
    # After AuthenticationMiddleware, because it decides whether the session
    # Django just restored belongs to the person Cloudflare authenticated.
    # Inert unless AUTH_MODE says otherwise (app/accounts/middleware.py).
    "app.accounts.middleware.AuthenticationModeMiddleware",
    # After the authenticator, because it asks who the response turned out to
    # be for (app/core/middleware.py).
    "app.core.middleware.PrivateResponseMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "app.core.context_processors.application",
            ],
        },
    },
]

# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

DATABASE_URL = env("DATABASE_URL")
if DATABASE_URL:
    _database: dict[str, object] = database_config_from_url(DATABASE_URL)
else:
    _database = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", "juristid"),
        "USER": env("POSTGRES_USER", "juristid"),
        "PASSWORD": env("POSTGRES_PASSWORD", "juristid"),
        "HOST": env("POSTGRES_HOST", "127.0.0.1"),
        "PORT": env("POSTGRES_PORT", "5432"),
    }
_database["CONN_MAX_AGE"] = env_int("DJANGO_DB_CONN_MAX_AGE", 60)
_database["OPTIONS"] = {"sslmode": env("POSTGRES_SSLMODE", "prefer")}

# -- process role: the read-only MCP server (docs/adr/0185 §1) ---------------
#
# Which kind of process this is. Empty — the default, and what every service
# but one runs as — is the browser application and its workers, unchanged.
# `mcp` is the MCP server (`manage.py run_mcp_server`), and in that role:
#
# - every database session starts read-only (`default_transaction_read_only`),
#   so an INSERT, UPDATE or DELETE from this process — a future mistake, the
#   cache table, a `last_login` stamp — fails with ReadOnlySqlTransaction
#   instead of changing a row;
# - every statement is bounded by the tool-call timeout, so a call the runtime
#   has given up on does not keep its query running;
# - connections are not kept between requests: each tool call opens its own in
#   a worker thread and closes it before the thread is reused
#   (app/mcp_server/runtime.py).
#
# The system checks know the role too: they stop asking this process for the
# browser's secrets and start asking for the MCP configuration
# (app/core/checks.py, juristid.E040–E046). Any other value is refused.
JURISTID_PROCESS_ROLE = env("JURISTID_PROCESS_ROLE", "").strip().lower()
MCP_PROCESS_ROLE = "mcp"
if JURISTID_PROCESS_ROLE == MCP_PROCESS_ROLE:
    _statement_timeout_ms = max(1, env_int("MCP_CALL_TIMEOUT_SECONDS", 60)) * 1000
    _role_options = (
        f"-c default_transaction_read_only=on -c statement_timeout={_statement_timeout_ms}"
    )
    _connection_options = dict(cast(dict[str, object], _database["OPTIONS"]))
    _existing_options = str(_connection_options.get("options") or "").strip()
    _connection_options["options"] = f"{_existing_options} {_role_options}".strip()
    _database["OPTIONS"] = _connection_options
    _database["CONN_MAX_AGE"] = 0
# `mcp_writer` is the MCP write gateway (`manage.py run_mcp_write_gateway`,
# docs/adr/0189): the one process that executes an approved MCP proposal
# through the canonical services. It is *not* read-only — that is its whole
# job — but it connects as its own least-privilege role, `juristid_mcp_writer`
# (app/mcp_writes/db_role.py), every statement is bounded, and connections
# are not kept between calls, exactly as for `mcp`.
MCP_WRITER_PROCESS_ROLE = "mcp_writer"
if JURISTID_PROCESS_ROLE == MCP_WRITER_PROCESS_ROLE:
    _writer_timeout_ms = max(1, env_int("MCP_WRITE_CALL_TIMEOUT_SECONDS", 30)) * 1000
    _writer_options = dict(cast(dict[str, object], _database["OPTIONS"]))
    _writer_existing = str(_writer_options.get("options") or "").strip()
    _writer_options["options"] = (
        f"{_writer_existing} -c statement_timeout={_writer_timeout_ms}".strip()
    )
    _database["OPTIONS"] = _writer_options
    _database["CONN_MAX_AGE"] = 0

DATABASES = {"default": _database}

# PostgreSQL 18 is required at launch: the Estonian full-text search
# configuration the product depends on is not available on older majors.
# See docs/adr/0002-database-and-identifier-strategy.md.
MINIMUM_POSTGRESQL_VERSION = (18, 0)

# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"
# --------------------------------------------------------------------------
# Authentication mode
# --------------------------------------------------------------------------
#
# One setting decides how this deployment establishes who is at the keyboard,
# and it is deliberately a mode rather than a pile of independent booleans: the
# combinations that must never happen are then unrepresentable rather than
# merely checked (app/accounts/enums.py, docs/adr/0016).
#
#   none              a developer laptop and CI
#   shared_gate       one department password, then a persona from a list
#   cloudflare_access Cloudflare verifies the individual; we verify Cloudflare
#   local_password    the individual signs in here: e-mail, personal password,
#                     second factor where required (docs/adr/0145) — built,
#                     tested and dormant; no deployment runs it until the
#                     activation runbook has been followed
#                     (docs/LOCAL_AUTH_ACTIVATION_RUNBOOK.md)
#
# Business authorization is identical in all four. What differs is how much
# the deployment may claim about the identity it hands to that authorization.
# Exactly one is in force: there is no setting that runs two of them side by
# side, by design (docs/adr/0016, docs/adr/0145 §2).
AUTH_MODE = env("AUTH_MODE", "none")

# -- the shared gate --------------------------------------------------------
#
# A temporary development-phase authenticator: one password for the whole
# department, supplied host-side and never anywhere else. It is genuine
# authentication of the *door* — a long secret, hashed, constant-time compared,
# rate limited — and it is emphatically not authentication of the *person*.
#
# The plaintext exists in this process's environment and is turned into a hash
# once, at startup (app/accounts/shared_gate.py). It is never written to the
# database, never logged, never rendered, and never reaches the client.
SHARED_GATE_PASSWORD = env("JURISTID_SHARED_GATE_PASSWORD", "")

# Wrong attempts from one client before it is locked out, the first lockout's
# length, and the ceiling that stops escalation from becoming permanent. Each
# further lockout cycle doubles the wait, capped — an attacker slows to a
# standstill, and an operator who mistypes twice in a bad week does not lose
# the afternoon.
SHARED_GATE_MAX_ATTEMPTS = env_int("SHARED_GATE_MAX_ATTEMPTS", 5)
SHARED_GATE_LOCKOUT_SECONDS = env_int("SHARED_GATE_LOCKOUT_SECONDS", 300)
SHARED_GATE_MAX_LOCKOUT_SECONDS = env_int("SHARED_GATE_MAX_LOCKOUT_SECONDS", 3600)

# How long a gate session lasts before the password is asked for again,
# however busy it has been: the absolute limit.
SHARED_GATE_SESSION_SECONDS = env_int("SHARED_GATE_SESSION_SECONDS", 12 * 60 * 60)

# How long a gate session may go without a request before the password is asked
# for again: the idle limit. Eight hours, the owner's decision of 2026-10-17
# (JSEC-005, D11; docs/adr/0016 amendment). The absolute limit above still
# applies to a session that never goes quiet.
SHARED_GATE_IDLE_SECONDS = env_int("SHARED_GATE_IDLE_SECONDS", 8 * 60 * 60)

# Where `login_required` sends somebody who has no identity yet, which is a
# different place in each mode:
#
#   shared_gate     the persona selector — they are already past the door, they
#                   just have not said whose work they are looking at
#   local_password  the personal sign-in page
#   otherwise       the synthetic sign-in
#
# In shared-gate mode the middleware has already bounced anybody who is not
# behind the password, so this redirect is only ever reached by a reader with no
# persona (app/accounts/middleware.py).
LOGIN_URL = {
    "shared_gate": "accounts:choose_persona",
    "local_password": "accounts:sign_in",
}.get((AUTH_MODE or "").strip().lower(), "accounts:dev_login")
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "core:home"

# -- personal passwords (docs/adr/0145 §6) ----------------------------------
#
# Argon2id first, through Django's own hasher and `argon2-cffi`. The others stay
# listed only so that a hash some other tool once wrote can still be verified
# (and is then re-hashed as Argon2id on the next successful sign-in). Nobody in
# production has a usable password today: every account was provisioned with an
# unusable one, and the shared gate hashes its own secret with PBKDF2 in process
# memory, explicitly, so this order changes nothing it does
# (app/accounts/shared_gate.py).
PASSWORD_HASHERS = [
    "app.accounts.passwords.JuristidArgon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

# The Argon2id cost. OWASP's floor is 19 MiB of memory with two passes; this
# is above it, and `juristid.E031` refuses a real-data local-password deployment
# configured below it. A hash records its own parameters, so raising these later
# re-hashes on the next sign-in rather than invalidating anybody.
LOCAL_AUTH_ARGON2_TIME_COST = env_int("LOCAL_AUTH_ARGON2_TIME_COST", 2)
LOCAL_AUTH_ARGON2_MEMORY_KIB = env_int("LOCAL_AUTH_ARGON2_MEMORY_KIB", 65536)
LOCAL_AUTH_ARGON2_PARALLELISM = env_int("LOCAL_AUTH_ARGON2_PARALLELISM", 1)

# NIST SP 800-63B-4: length and a blocklist, never composition rules, never
# scheduled expiry. Django's MinimumLength and Numeric validators are not here:
# the first is replaced by the 15-character rule, and the second is a
# composition rule by another name.
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "app.accounts.passwords.EmptyNotAllowedValidator"},
    {"NAME": "app.accounts.passwords.LengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "app.accounts.passwords.BreachedPasswordValidator"},
    {"NAME": "app.accounts.passwords.ContextWordsValidator"},
    {"NAME": "app.accounts.passwords.RepetitionValidator"},
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
]
LOCAL_AUTH_PASSWORD_MIN_LENGTH = env_int("LOCAL_AUTH_PASSWORD_MIN_LENGTH", 15)
# An operator-supplied list of known-breached passwords, one per line,
# optionally gzipped. Empty means Django's 20,000 common passwords only.
LOCAL_AUTH_BREACHED_PASSWORDS_PATH = env("LOCAL_AUTH_BREACHED_PASSWORDS_PATH", "")

# -- the second factor -------------------------------------------------------
#
# The key TOTP secrets are encrypted under. A deployment secret of its own,
# rather than SECRET_KEY, so rotating the Django secret does not break every
# authenticator; `juristid.E033` requires it on a real-data local-password
# deployment. Empty falls back to SECRET_KEY, for laptops and CI only.
LOCAL_AUTH_MFA_ENCRYPTION_KEY = env("LOCAL_AUTH_MFA_ENCRYPTION_KEY", "")
# Whether everybody must enrol a second factor, or only those who administer
# accounts. Administrators always must; this cannot turn that off.
LOCAL_AUTH_MFA_REQUIRED_FOR_ALL = env_bool("LOCAL_AUTH_MFA_REQUIRED_FOR_ALL", default=False)

# -- sessions -----------------------------------------------------------------
#
# A local session ends after an hour without a request, and after ten hours
# whatever happens — a working day, not a working week. Critical changes (a
# privilege, a deactivation, a second factor removed) ask for the password and
# code again when the last proof is older than the re-authentication window.
LOCAL_AUTH_SESSION_IDLE_SECONDS = env_int("LOCAL_AUTH_SESSION_IDLE_SECONDS", 60 * 60)
LOCAL_AUTH_SESSION_ABSOLUTE_SECONDS = env_int("LOCAL_AUTH_SESSION_ABSOLUTE_SECONDS", 10 * 60 * 60)
LOCAL_AUTH_REAUTH_SECONDS = env_int("LOCAL_AUTH_REAUTH_SECONDS", 15 * 60)

# -- one-time links -----------------------------------------------------------
LOCAL_AUTH_ACTIVATION_LINK_SECONDS = env_int("LOCAL_AUTH_ACTIVATION_LINK_SECONDS", 48 * 60 * 60)
LOCAL_AUTH_RESET_LINK_SECONDS = env_int("LOCAL_AUTH_RESET_LINK_SECONDS", 60 * 60)

# -- who may have an account --------------------------------------------------
#
# The Chamber's own domain. An external address is an individual exception,
# approved with a reason by an administrator who may delegate administration —
# never a domain added here because one address needed it.
ACCOUNT_ALLOWED_EMAIL_DOMAINS = env_list("ACCOUNT_ALLOWED_EMAIL_DOMAINS", "koda.ee")

# -- account e-mail: OFF -------------------------------------------------------
#
# Two separate switches, both off. `ACCOUNT_EMAIL_DELIVERY_ENABLED` is the
# decision to send account mail at all, and `juristid.E036` refuses it in any
# mode but local_password. `EMAIL_BACKEND` is the transport, and its default
# delivers nothing — Django's own default would be SMTP to localhost. A later,
# approved activation step configures both (docs/LOCAL_AUTH_ACTIVATION_RUNBOOK.md).
ACCOUNT_EMAIL_DELIVERY_ENABLED = env_bool("ACCOUNT_EMAIL_DELIVERY_ENABLED", default=False)
EMAIL_BACKEND = env("DJANGO_EMAIL_BACKEND", "app.accounts.mail.DeliveryDisabledBackend")
EMAIL_HOST = env("DJANGO_EMAIL_HOST", "")
EMAIL_PORT = env_int("DJANGO_EMAIL_PORT", 587)
EMAIL_HOST_USER = env("DJANGO_EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("DJANGO_EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("DJANGO_EMAIL_USE_TLS", default=True)
EMAIL_TIMEOUT = env_int("DJANGO_EMAIL_TIMEOUT", 20)
ACCOUNT_EMAIL_FROM = env("ACCOUNT_EMAIL_FROM", "")
# «Unustasid parooli?» mails its link after the response, so the page takes the
# same time for every address. The test suite turns it off to assert inline.
ACCOUNT_EMAIL_IN_BACKGROUND = True
DEFAULT_FROM_EMAIL = ACCOUNT_EMAIL_FROM or "webmaster@localhost"
# The address one-time links point at. Configured rather than read from the
# request's Host header: a reset link built from a header an attacker can set is
# the classic way to have somebody else's reset mailed to your server.
ACCOUNT_LINK_BASE_URL = env("ACCOUNT_LINK_BASE_URL", "")

# Local synthetic-user sign-in. Never enabled outside an isolated developer
# environment; a real-data deployment authenticates through the shared gate
# today, and through personal sign-in (or Cloudflare Access) after an approved
# activation — never through this. See docs/adr/0004 as amended by 0145.
DEV_LOGIN_ENABLED = env_bool("DEV_LOGIN_ENABLED", default=False)

# `matters:add_note` has had no page since `+ Lisa · Tavaline` left on
# 2026-10-07; it stays only as a fixture door for the test suites until their
# uses move to the real doors (owner decision D9, 2026-10-17). It answers only
# where this is on, which by default is exactly where the synthetic sign-in is,
# and never beside real data: with REAL_DATA_ALLOWED the route is a 404 whatever
# this says.
ADD_NOTE_TEST_DOOR_ENABLED = env_bool("ADD_NOTE_TEST_DOOR_ENABLED", default=DEV_LOGIN_ENABLED)

# A shared PIN in front of the synthetic sign-in. Empty means no PIN, which is
# the right default for a laptop and for CI.
#
# It exists for one situation: the rehearsal instance is reachable from outside
# the LAN, and the synthetic sign-in has no password by design — you pick a user
# from a list. A short shared PIN is *not* authentication and is not pretending
# to be. It is a speed bump that keeps a passer-by out, and it is only defensible
# because the data behind it is invented.
#
# Anything reachable from the internet that holds real data needs a real
# authenticator in front of it — the shared gate today, personal sign-in or
# Cloudflare Access after activation (docs/adr/0004, 0016, 0145).
DEV_LOGIN_PIN = env("DEV_LOGIN_PIN", "")

# How many wrong PINs from one address before it is locked out, and for how
# long. A four-digit PIN is 10,000 guesses; without a limit that is seconds of
# scripted work.
DEV_LOGIN_PIN_MAX_ATTEMPTS = env_int("DEV_LOGIN_PIN_MAX_ATTEMPTS", 5)
DEV_LOGIN_PIN_LOCKOUT_SECONDS = env_int("DEV_LOGIN_PIN_LOCKOUT_SECONDS", 300)

# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------

# PostgreSQL, not local memory. The only thing this cache currently holds is the
# PIN lockout counter, and gunicorn runs several worker processes: with the
# default per-process cache each worker would keep its own count, so "five
# attempts" would really mean five per worker. A shared backend makes the limit
# the number it claims to be.
#
# Requires `python manage.py createcachetable`, which is idempotent and part of
# the deployment steps. Redis is deliberately not introduced for this
# (AGENTS.md): one small counter does not justify another service.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": env("CACHE_TABLE", "core_cache"),
    }
}

# --------------------------------------------------------------------------
# Internationalisation
# --------------------------------------------------------------------------

LANGUAGE_CODE = "et"

# The product has exactly one language (AGENTS.md, specification 3.10), so there
# is nothing to negotiate. Saying so explicitly matters: LocaleMiddleware
# otherwise honours the browser's Accept-Language header, and Django ships
# translations for dozens of languages. A lawyer with an English-language
# browser was served an Estonian interface with English dates and English form
# errors — "Wednesday, 19. August 2026" instead of "kolmapäev, 19. august 2026".
#
# CI could not catch this. The browser suite drives Chromium with no language
# preference, so it always fell back to LANGUAGE_CODE and always looked right.
# It took opening the deployed site in an ordinary browser to see it.
LANGUAGES = [("et", "Eesti")]

TIME_ZONE = "Europe/Tallinn"
USE_I18N = True
USE_TZ = True

# --------------------------------------------------------------------------
# Static and stored files
# --------------------------------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Hashed, compressed static assets require `collectstatic` to have produced a
# manifest. The container image does that at build time; test runs and ad-hoc
# processes have not, so they use the plain backend.
STATIC_MANIFEST = env_bool("DJANGO_STATIC_MANIFEST", default=not DEBUG)

MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "media/"

# Evidence binaries are addressed through a named storage alias so the
# production Azure Blob backend can replace the local filesystem backend
# without domain code changing. See docs/adr/0003-document-lifecycle.md.
EVIDENCE_STORAGE_ALIAS = "evidence"
EVIDENCE_ROOT = Path(env("EVIDENCE_ROOT", str(BASE_DIR / "evidence")))

# Derivatives are a different storage class, not a subdirectory of evidence.
# Previews and thumbnails may be deleted and rebuilt; evidence may not. Mixing
# them puts the operator one `rm -rf` away from destroying the half that cannot
# be regenerated, and makes "is the backup complete" impossible to answer by
# looking (docs/adr/0014, Stage-2B brief 9).
DERIVATIVE_STORAGE_ALIAS = "derivatives"
DERIVATIVE_ROOT = Path(env("DERIVATIVE_ROOT", str(BASE_DIR / "derivatives")))

# A third storage class, for the OneNote pages themselves. Their XML is source
# evidence — kept byte for byte, hashed, and never rendered to anybody — but it
# is not a Document, so putting it in the evidence store would mean rows in the
# document tables that no DocumentVersion owns. Small (a few MB for 755 pages)
# and **must be backed up**: it is the only copy the application controls
# (docs/adr/0015, Stage-2D brief 11).
LEGACY_SOURCE_STORAGE_ALIAS = "legacy_source"
LEGACY_SOURCE_ROOT = Path(env("LEGACY_SOURCE_ROOT", str(BASE_DIR / "legacy-source")))

# A fourth class, and the weakest one: files a form is holding because the save
# that would have stored them was refused, and — under `intake/` — the files a
# `Uus teema` is having read while it is still open. They describe nothing,
# nothing outside the staging tables points at them, and losing one costs
# somebody a second click rather than a piece of evidence, so this is
# deliberately **not** backed up. Separate from the evidence store for the same
# reason derivatives are: mixing what may be deleted with what may not is how an
# operator ends up one command away from destroying the half that cannot be
# regenerated (app/documents/pending.py, docs/adr/0014).
#
# It does need a *shared* volume in production, which is a different question
# from being worth keeping. Since docs/adr/0064 two containers use this path —
# `web` writes a staged file and the reader container opens it to parse it —
# and a container's writable layer is private, so an unmounted default leaves
# the worker looking for a file that only exists in another container. The
# production stack mounts one project-scoped named volume here, read-write for
# `web` and read-only for `intake-reader`, which replaced `extractor` in
# docs/adr/0072 (deploy/unraid-main/compose.yml). Being on
# a volume does not promote it: expiry and `prune_intake_staging` still bound it,
# and it stays out of every backup set.
PENDING_UPLOAD_STORAGE_ALIAS = "pending_uploads"
PENDING_UPLOAD_ROOT = Path(env("PENDING_UPLOAD_ROOT", str(BASE_DIR / "pending-uploads")))

# How long a held file survives without being claimed by a save. Long enough
# that somebody can be interrupted mid-form and finish afterwards; short enough
# that an abandoned form is not still on disk tomorrow.
PENDING_UPLOAD_GRACE_HOURS = env_int("PENDING_UPLOAD_GRACE_HOURS", 12)

# What `Uus teema` staging may hold before it refuses another file
# (csf_3942255b466a4e213b958b16, docs/adr/0177). The twenty-file cap is per form,
# and a form is one render, so without these one person could fill the volume
# with fresh forms. Checked under one transaction-scoped lock before a byte is
# written (`app.matters.intake_staging.stage_uploads`).
#
# "Active" is an unconsumed, unexpired form that already holds a file; an empty
# render is a row and no bytes, and refusing it would refuse the page itself.
# Bytes are every staged file of an unconsumed form, removed ones and expired
# ones included, because their bytes stay on the volume until the sweep.
INTAKE_STAGING_MAX_ACTIVE_SESSIONS_PER_OWNER = env_int(
    "INTAKE_STAGING_MAX_ACTIVE_SESSIONS_PER_OWNER", 20
)
INTAKE_STAGING_MAX_BYTES_PER_OWNER = env_int("INTAKE_STAGING_MAX_BYTES_PER_OWNER", 2 * 1024**3)
INTAKE_STAGING_MAX_ACTIVE_SESSIONS = env_int("INTAKE_STAGING_MAX_ACTIVE_SESSIONS", 200)
INTAKE_STAGING_MAX_BYTES = env_int("INTAKE_STAGING_MAX_BYTES", 10 * 1024**3)
# Free space the staging volume must still have *after* the file is written
# (`shutil.disk_usage` on PENDING_UPLOAD_ROOT). On the production host this is
# Docker's own storage, which every container shares.
INTAKE_STAGING_MIN_FREE_BYTES = env_int("INTAKE_STAGING_MIN_FREE_BYTES", 1024**3)
# Held uploads (a refused form's files, `app.documents.pending`) share the volume
# and have no rows, so they get a ceiling of their own, measured on disk and
# checked under the same lock as staging; the headroom above applies to both.
PENDING_UPLOAD_MAX_HELD_BYTES = env_int("PENDING_UPLOAD_MAX_HELD_BYTES", 2 * 1024**3)

# The scheduled sweep of expired and consumed staging (`prune_intake_staging
# --loop`, the `intake-sweeper` service). Bounded per run by sessions and by
# staged bytes; the health probe (`check_intake_staging`) goes red when a
# stale session has waited longer than the last setting.
INTAKE_STAGING_SWEEP_MAX_SESSIONS = env_int("INTAKE_STAGING_SWEEP_MAX_SESSIONS", 500)
INTAKE_STAGING_SWEEP_MAX_BYTES = env_int("INTAKE_STAGING_SWEEP_MAX_BYTES", 5 * 1024**3)
INTAKE_STAGING_SWEEP_INTERVAL_SECONDS = env_int("INTAKE_STAGING_SWEEP_INTERVAL_SECONDS", 900)
INTAKE_STAGING_SWEEP_STALE_SECONDS = env_int("INTAKE_STAGING_SWEEP_STALE_SECONDS", 3600)

# Where the read-only historical source material lives on the server. The
# importer reads from here and never writes to it (Stage-2D brief 54).
HISTORICAL_SOURCE_ROOT = env("HISTORICAL_SOURCE_ROOT", "")

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    EVIDENCE_STORAGE_ALIAS: {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": str(EVIDENCE_ROOT)},
    },
    DERIVATIVE_STORAGE_ALIAS: {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": str(DERIVATIVE_ROOT)},
    },
    LEGACY_SOURCE_STORAGE_ALIAS: {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": str(LEGACY_SOURCE_ROOT)},
    },
    PENDING_UPLOAD_STORAGE_ALIAS: {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": str(PENDING_UPLOAD_ROOT)},
    },
    "staticfiles": {
        "BACKEND": (
            "whitenoise.storage.CompressedManifestStaticFilesStorage"
            if STATIC_MANIFEST
            else "django.contrib.staticfiles.storage.StaticFilesStorage"
        )
    },
}

# Transport-level upload guardrails. Business rules for evidence capture live
# in app/documents.
DATA_UPLOAD_MAX_MEMORY_SIZE = env_int("DJANGO_DATA_UPLOAD_MAX_MEMORY_SIZE", 10 * 1024 * 1024)
# Django keeps a request's files in memory only when the *whole request* is at
# most this; anything larger goes to temporary files on disk, so this is also
# the most file bytes one request can hold in a worker's memory.
FILE_UPLOAD_MAX_MEMORY_SIZE = env_int("DJANGO_FILE_UPLOAD_MAX_MEMORY_SIZE", 10 * 1024 * 1024)
MAX_EVIDENCE_UPLOAD_BYTES = env_int("MAX_EVIDENCE_UPLOAD_BYTES", 100 * 1024 * 1024)

# The aggregate upload policy, one for every upload door
# (csf_35546c06657fae7b9935969d, docs/adr/0182). Checked at the request
# boundary by the first upload handler — the declared Content-Length before a
# byte of the body is read, then the files and bytes as they stream — and again
# by a door that files held uploads together with new ones
# (`app.documents.uploads.check_upload_batch`). Over either, the request is
# answered 413 before any view runs.
#
# A hundred files is the ceiling Django already applied to every request
# (DATA_UPLOAD_MAX_NUMBER_FILES' default), so no upload that worked before is
# refused for its count — a `Kaasamine` round's replies arrive by the dozen.
# With the files streamed rather than held in memory, the count bounds file
# handles and the bytes are what bound the worker: 512 MiB is five maximum-size
# files, and a real ministry packet is tens of megabytes.
UPLOAD_MAX_FILES_PER_REQUEST = env_int("UPLOAD_MAX_FILES_PER_REQUEST", 100)
UPLOAD_MAX_REQUEST_BYTES = env_int("UPLOAD_MAX_REQUEST_BYTES", 512 * 1024 * 1024)
# Django's own ceiling on file *parts* — empty file inputs included, which it
# counts and the policy does not — stays just above the policy's, so the
# policy's sentence (a 413) is what a person over the limit sees rather than
# Django's bare 400.
DATA_UPLOAD_MAX_NUMBER_FILES = UPLOAD_MAX_FILES_PER_REQUEST + 10
# The policy's own handler first; Django's two after it, unchanged.
FILE_UPLOAD_HANDLERS = [
    "app.documents.upload_limits.UploadLimitHandler",
    "django.core.files.uploadhandler.MemoryFileUploadHandler",
    "django.core.files.uploadhandler.TemporaryFileUploadHandler",
]

# How old an unreferenced stored object must be before `prune_orphaned_evidence`
# will delete it. Evidence bytes are written before the row that describes them,
# so a live upload is indistinguishable from an orphan until it commits. The
# default is deliberately far longer than any transaction could last.
EVIDENCE_ORPHAN_GRACE_HOURS = env_int("EVIDENCE_ORPHAN_GRACE_HOURS", 24)

# --------------------------------------------------------------------------
# Content extraction
# --------------------------------------------------------------------------
# Every limit below exists because a parser handed a pathological file will
# otherwise consume the worker rather than report the problem. They are
# configurable because the right number is a property of the corpus, and they
# are enforced as *refusals* rather than silent truncation: a legal document
# extracted halfway and marked complete is worse than one marked failed, since
# only one of those gets looked at again (Stage-2B brief 72).
EXTRACTION_MAX_PDF_PAGES = env_int("EXTRACTION_MAX_PDF_PAGES", 500)
EXTRACTION_MAX_CHARACTERS = env_int("EXTRACTION_MAX_CHARACTERS", 8_000_000)
EXTRACTION_MAX_ARCHIVE_MEMBERS = env_int("EXTRACTION_MAX_ARCHIVE_MEMBERS", 2_000)
EXTRACTION_MAX_UNCOMPRESSED_BYTES = env_int("EXTRACTION_MAX_UNCOMPRESSED_BYTES", 400 * 1024 * 1024)
EXTRACTION_MAX_COMPRESSION_RATIO = env_int("EXTRACTION_MAX_COMPRESSION_RATIO", 200)
EXTRACTION_MAX_IMAGE_PIXELS = env_int("EXTRACTION_MAX_IMAGE_PIXELS", 80_000_000)
EXTRACTION_MAX_EMAIL_ATTACHMENTS = env_int("EXTRACTION_MAX_EMAIL_ATTACHMENTS", 50)
EXTRACTION_MAX_EMAIL_DEPTH = env_int("EXTRACTION_MAX_EMAIL_DEPTH", 3)
EXTRACTION_MAX_XLSX_FRAGMENTS_PER_SHEET = env_int("EXTRACTION_MAX_XLSX_FRAGMENTS_PER_SHEET", 500)

# What reading a table may *visit*, empty cells included (docs/adr/0176). The
# character budget above only counts text that comes out, and a worksheet whose
# used range is a million blank rows produces none, so the walk is charged on
# its own. Declared XLSX dimensions are checked against these before the first
# row is read; CSV and XLSX rows are charged as they are read.
#
# Sized against the corpus with room to spare: the historical registers are a
# few thousand rows by a few dozen columns (~0.1 M cells), and the existing
# 500-fragments-per-sheet cap already stops a sheet at 25 000 rows *with text*.
# Rows stop at Excel's own last row, so no sheet Excel can save is refused on
# its height alone — a sheet somebody Ctrl+Down-ed to row 1 048 576 and touched
# there still reads. The walk budget is what bounds it: 100 M visited cells is
# about fifteen seconds of one worker, and a declared range past it (the
# A1:XFD1048576 attack is 17 000 M) is refused before the first row. 2 000
# columns is twice what LibreOffice writes for a full-width sheet (AMJ = 1 024).
EXTRACTION_MAX_TABLE_ROWS = env_int("EXTRACTION_MAX_TABLE_ROWS", 1_048_576)
EXTRACTION_MAX_TABLE_COLUMNS = env_int("EXTRACTION_MAX_TABLE_COLUMNS", 2_000)
EXTRACTION_MAX_TABLE_CELLS = env_int("EXTRACTION_MAX_TABLE_CELLS", 100_000_000)
# Excel itself stops a cell at 32 767 characters; 131 072 is the csv module's
# own long-standing field limit, so no file that read before is refused by it.
EXTRACTION_MAX_TABLE_CELL_CHARACTERS = env_int("EXTRACTION_MAX_TABLE_CELL_CHARACTERS", 131_072)
# A CSV is decoded whole, so its size is checked before decoding. Four times the
# character budget: a CSV larger than that cannot fit the budget unless three
# quarters of it are separators and blanks.
EXTRACTION_MAX_CSV_BYTES = env_int("EXTRACTION_MAX_CSV_BYTES", 32 * 1024 * 1024)
# Raw characters one CSV record may span, quoted line breaks included. Bounds
# the row the csv module builds before any of it can be inspected.
EXTRACTION_MAX_CSV_RECORD_CHARACTERS = env_int("EXTRACTION_MAX_CSV_RECORD_CHARACTERS", 1_000_000)
# The decoded body of one email, before it is flattened to text. The HTML
# sanitiser reads about 1 MB a second, so this caps one message at ~15 s; a real
# message body, quoted thread included, is a few hundred kilobytes.
EXTRACTION_MAX_EMAIL_BODY_CHARACTERS = env_int("EXTRACTION_MAX_EMAIL_BODY_CHARACTERS", 16_000_000)
# Fragment rows one file may produce, whatever parser made them. A 500-page PDF
# is 500; a register workbook of 30 full sheets is 15 000.
EXTRACTION_MAX_FRAGMENTS = env_int("EXTRACTION_MAX_FRAGMENTS", 50_000)

# Round 2 of the parser bounds (docs/adr/0182).
#
# A plain-text file is decoded and split in bounded chunks, never whole. Its
# raw size is checked first — the upload ceiling, so no file that could be
# uploaded is refused by it — and its lines are charged as they are counted:
# ten million is the most lines a file can have and still fit the fragment
# budget with text in every block of two hundred.
EXTRACTION_MAX_TEXT_BYTES = env_int("EXTRACTION_MAX_TEXT_BYTES", 100 * 1024 * 1024)
EXTRACTION_MAX_TEXT_LINES = env_int("EXTRACTION_MAX_TEXT_LINES", 10_000_000)
# An Office package's central directory, read and walked from the end of the
# file before `zipfile` allocates an entry for any of it. A real DOCX/XLSX/PPTX
# entry is about a hundred bytes; 16 MiB is eight kilobytes for each of the
# EXTRACTION_MAX_ARCHIVE_MEMBERS entries allowed.
EXTRACTION_MAX_ZIP_DIRECTORY_BYTES = env_int("EXTRACTION_MAX_ZIP_DIRECTORY_BYTES", 16 * 1024 * 1024)
# What an Office library (python-docx, python-pptx, openpyxl) may parse as XML:
# one part (a document body, a worksheet, shared strings, a slide, a `.rels`)
# and all XML parts of the package together, uncompressed, as the bounded
# central directory declares them. A tree is several times its XML in memory,
# so this is the knob that bounds the parsers' memory. A thousand-page draft's
# document.xml is a few tens of megabytes; a 64 MiB worksheet is about two
# million cells, twenty times the largest historical register.
EXTRACTION_MAX_OFFICE_XML_PART_BYTES = env_int(
    "EXTRACTION_MAX_OFFICE_XML_PART_BYTES", 64 * 1024 * 1024
)
EXTRACTION_MAX_OFFICE_XML_BYTES = env_int("EXTRACTION_MAX_OFFICE_XML_BYTES", 128 * 1024 * 1024)
# Body paragraphs one DOCX may have, counted before python-docx builds one
# object for each: a thousand-page draft has a few tens of thousands.
EXTRACTION_MAX_DOCX_PARAGRAPHS = env_int("EXTRACTION_MAX_DOCX_PARAGRAPHS", 1_000_000)
# MIME parts one `.eml` may have, nested messages included, counted while the
# standard parser builds them. A real message is a few dozen parts at most, and
# only fifty attachments are kept from it anyway (EXTRACTION_MAX_EMAIL_ATTACHMENTS).
EXTRACTION_MAX_EMAIL_PARTS = env_int("EXTRACTION_MAX_EMAIL_PARTS", 1_000)

# A page whose native text layer is thinner than this is treated as scanned.
# Not zero: real government PDFs carry a header, a page number and a stamp on an
# otherwise photographed page, and calling that "has text" leaves the body
# unsearchable while looking successful.
EXTRACTION_OCR_MIN_NATIVE_CHARACTERS = env_int("EXTRACTION_OCR_MIN_NATIVE_CHARACTERS", 120)
EXTRACTION_OCR_LANGUAGES = env("EXTRACTION_OCR_LANGUAGES", "est+eng")
EXTRACTION_OCR_DPI = env_int("EXTRACTION_OCR_DPI", 200)
EXTRACTION_OCR_ENABLED = env_bool("EXTRACTION_OCR_ENABLED", default=True)

# How long one Tesseract run may take before the worker abandons it. Tesseract
# is a separate process reading a bitmap this one rendered, and a bitmap it
# cannot make progress on holds that process open for as long as it likes —
# which holds the worker open with it, because the parse is synchronous. Without
# a ceiling the queue stops on one page and the only visible symptom is a
# heartbeat that went quiet. Per image, not per document: a 400-page scan is
# 400 separate runs and is supposed to take a long time in total.
EXTRACTION_OCR_TIMEOUT_SECONDS = env_int("EXTRACTION_OCR_TIMEOUT_SECONDS", 120)

# How long a PROCESSING claim may stand before another worker may take it. Long
# enough that an ordinary parse finishes well inside it; short enough that a
# killed worker's queue drains the same day.
#
# It is a heuristic and not a guarantee, which is worth saying plainly because
# the code used to assume otherwise. Nothing bounds how long a parse may take —
# a 500-page scan is 500 OCR runs — so a genuinely slow file *can* outlive its
# claim and be reclaimed by a worker doing exactly what it should. That is
# survivable rather than prevented: the claim is re-asserted at the moment of
# writing, so the pass that lost it writes nothing at all
# (app/documents/extraction/orchestrator.py).
EXTRACTION_STALE_CLAIM_MINUTES = env_int("EXTRACTION_STALE_CLAIM_MINUTES", 30)
# How many claims a file gets before a stale one is treated as the file's fault.
# A claim that never finishes is usually a worker the file itself killed (out of
# memory, a native crash); reclaiming it for ever kills the worker for ever. The
# corpus worker and the intake reader both count claims and settle the row
# FAILED instead of taking it again past this number (AUD-045).
EXTRACTION_MAX_ATTEMPTS = env_int("EXTRACTION_MAX_ATTEMPTS", 3)
EXTRACTION_WORKER_IDLE_SECONDS = env_int("EXTRACTION_WORKER_IDLE_SECONDS", 10)

# Where the worker marks that its loop turned, so a container healthcheck can
# tell a wedged worker from a busy one. Inside the container and per-container
# on purpose: it describes *this* process, and a restarted worker starts a new
# heartbeat (app/documents/extraction/heartbeat.py).
EXTRACTION_WORKER_HEARTBEAT_PATH = env(
    "EXTRACTION_WORKER_HEARTBEAT_PATH",
    # The system temporary directory rather than a shared volume: the mark
    # describes one process, and a durable location would leave a stopped
    # worker's heartbeat behind for the next container to be judged by.
    str(Path(tempfile.gettempdir()) / "juristid-extraction-worker.heartbeat"),
)

# --------------------------------------------------------------------------
# The Uus teema intake reader
# --------------------------------------------------------------------------
#
# Where the intake reader marks that its loop turned. Its own file, not the
# extraction worker's: the two are different processes answering different
# healthchecks, and one heartbeat serving both would let a stopped reader be
# reported alive by a corpus run somebody started by hand
# (app/documents/extraction/heartbeat.py, docs/adr/0072).
INTAKE_READER_HEARTBEAT_PATH = env(
    "INTAKE_READER_HEARTBEAT_PATH",
    str(Path(tempfile.gettempdir()) / "juristid-intake-reader.heartbeat"),
)

# How long the reader waits when no `Uus teema` form has a file waiting.
#
# Much shorter than the extraction worker's ten seconds, and the difference is
# the whole product decision: somebody is sitting in front of the form. A file
# staged at the wrong moment would otherwise wait most of a sleep before the
# loop even looked, which is latency for nothing — this queue is empty almost
# always, so a short poll costs one indexed query a second against a table with
# single-digit rows in it (docs/adr/0072 §Performance).
INTAKE_READER_IDLE_SECONDS = env_float("INTAKE_READER_IDLE_SECONDS", 0.5)

# How long a reader's claim may stand before another reader may take it.
#
# Its own number rather than EXTRACTION_STALE_CLAIM_MINUTES, because the two
# describe different work. A corpus parse may be a 500-page OCR run and needs
# half an hour of grace; a staged intake file is bounded by
# MAX_EVIDENCE_UPLOAD_BYTES (the ceiling `read_upload` puts on every staged
# file; there is no intake-specific limit, DX-5) and finishes in seconds, so a
# claim older than this belongs to a reader that died — and waiting thirty minutes to find that out
# would strand the form it was reading for long past the session's own life.
INTAKE_READER_STALE_CLAIM_MINUTES = env_int("INTAKE_READER_STALE_CLAIM_MINUTES", 5)

# Whether `Uus teema` offers what the reader found in the file.
#
# **Off, by product decision of 2026-09-16, and the code stays.** The first
# structured feedback from the lawyers using the demo was that the suggestion
# area on the creation form costs more attention than it returns: a panel of
# machine proposals over a form somebody is already filling in is one more thing
# to read, evaluate and dismiss on the page they use most. The capture path is
# manual-first again until the department asks for the reading back
# (docs/adr/0088).
#
# **What this switch governs, exactly.** Whether the document-derived
# suggestions reach the lawyer: the «Failist leitud» panel, the `data-prefill-*`
# markers the browser writes into empty controls, and the reading states the
# form prints while it waits for them. It governs nothing about *files*.
# Choosing a file still stages it, staging still promotes it into one Document
# with one immutable version, and a refused save still holds it
# (app/matters/views.py `_intake_context`, app/matters/intake_staging.py).
#
# **And it is a switch rather than a deletion** for the reason ADR 0072 gave
# for the capability in the first place: the rules, the vocabulary and the
# reader are reviewed work with a corpus behind them, and the decision withdrawn
# here is about where a lawyer meets them, not about whether they are right.
# Turning it on restores the feature; nothing is migrated in either direction.
MATTER_INTAKE_SUGGESTIONS_ENABLED = env_bool("MATTER_INTAKE_SUGGESTIONS_ENABLED", default=False)

# --------------------------------------------------------------------------
# Search freshness
# --------------------------------------------------------------------------
#
# A high-fanout canonical change — an Organisation rename, an alias edit, a Tag
# or PolicyArea rename — records a durable obligation rather than reindexing
# thousands of rows inside somebody's form submission. These two numbers are the
# whole runtime configuration of paying that obligation off
# (app/search/freshness.py, docs/adr/0041).

# How long the worker waits when nothing is owed. The same default as the
# extraction worker, because it is the same question: how long may a user's
# change sit before the loop notices it. A rename is rare and a rebuild is
# seconds, so there is nothing to gain from polling harder.
SEARCH_REFRESH_WORKER_IDLE_SECONDS = env_int("SEARCH_REFRESH_WORKER_IDLE_SECONDS", 10)

# How long an obligation may be outstanding before `check_search_freshness`
# calls it a fault. Comfortably longer than one idle period plus one rebuild, so
# an ordinary pending rename never trips it, and short enough that a stopped
# worker is red before anybody has searched for the new name and not found it.
SEARCH_REBUILD_DEBT_STALE_SECONDS = env_int("SEARCH_REBUILD_DEBT_STALE_SECONDS", 300)

# --------------------------------------------------------------------------
# Security
# --------------------------------------------------------------------------

SECURE_HSTS_SECONDS = env_int("DJANGO_SECURE_HSTS_SECONDS", 0 if DEBUG else 31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
# Deliberately off, and `security.W021` silenced below rather than left to
# accumulate as noise nobody reads.
#
# Preloading is submitted to a list browsers ship, and with
# `includeSubDomains` it commits *every* host under the registered domain to
# HTTPS — including ones this application knows nothing about and does not
# operate. Removal takes months. HSTS itself is on, which is what protects this
# application's own readers; preloading is a decision about somebody else's
# domain and belongs to whoever owns it, not to a default in this file.
SECURE_HSTS_PRELOAD = False
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", default=not DEBUG)

# The one deployment check this project answers "no" to on purpose. Silenced so
# that `check --deploy` is a list of things to fix rather than a list with one
# permanent entry at the top — a warning nobody may act on trains people to skim
# the whole output. See SECURE_HSTS_PRELOAD above for why the answer is no.
SILENCED_SYSTEM_CHECKS = ["security.W021"]
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = not DEBUG
# The page a refused CSRF check shows — Estonian, and a marked 403 for htmx so
# the page that sent it can say so. The check itself is unchanged
# (app/core/views.py `csrf_failure`, ENG-012).
CSRF_FAILURE_VIEW = "app.core.views.csrf_failure"
X_FRAME_OPTIONS = "DENY"

# Whether something in front terminates TLS and forwards the scheme.
#
# Not cosmetic. Without it Django believes every request arrived over plain
# HTTP, and then: HSTS is never sent, `request.is_secure()` is False, and CSRF
# skips its referer check. The first real deployment shipped without it and the
# missing `Strict-Transport-Security` header is how it was noticed.
#
# Only ever set where something really does terminate TLS. Trusting
# `X-Forwarded-Proto` from a client that can reach the application directly
# would let that client claim its own connection was secure.
BEHIND_TLS_PROXY = env_bool("DJANGO_BEHIND_TLS_PROXY", default=False)
if BEHIND_TLS_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Whether `CF-Connecting-IP` names the client on audit rows and in the throttles
# (app/core/client_ip.py, JOPS-008). The same trust as the forwarded scheme
# above, and for the same reason: behind the tunnel, which is the only way in,
# Cloudflare sets the header; anywhere a client reaches the application
# directly, the client does. Not a second switch, so the two cannot disagree.
TRUST_CF_CONNECTING_IP = BEHIND_TLS_PROXY

# --------------------------------------------------------------------------
# Application metadata and logging
# --------------------------------------------------------------------------

APPLICATION_NAME = "Koda Õigusloome"
# Shown in the footer and returned by /healthz. The default is what an
# unconfigured process claims about itself, so it has to be the truth: it sat at
# "Stage 2A" through five merged stages, telling every reader of the rehearsal
# footer that the build was six months older than it was.
APPLICATION_STAGE = env("APPLICATION_STAGE", "Stage 2I")
APPLICATION_ENVIRONMENT = env("APPLICATION_ENVIRONMENT", "local")
# `+ Ülevaade / uudis` reads a koda.ee page's title before the save — the one
# outbound request this application makes to a pasted address, fenced to the
# Chamber's own hosts (app/matters/publication_title.py, docs/adr/0089 §4
# amended). Off where no network is wanted: the suite, CI and rehearsals.
PUBLICATION_TITLE_FETCH = env_bool(
    "JURISTID_PUBLICATION_TITLE_FETCH",
    default=APPLICATION_ENVIRONMENT not in ("test", "ci", "recovery-rehearsal"),
)


def _revision() -> str:
    """Which commit this build came from.

    The environment variable wins, for a deployment that builds some other way
    or wants to say something more specific. Behind it is `GIT_SHA`, written
    into the image by the Dockerfile from a build argument, so the answer
    travels with the code it describes.

    The fallback matters more than it looks. `APPLICATION_REVISION` used to be
    the one field of the running build's identity that a human had to remember
    to update, and a field somebody has to remember is a field that eventually
    describes a different build than the one serving — the same failure the
    build stamp above exists to avoid. An image built without a SHA still says
    "unknown" rather than inventing one; `manage.py deployment_readiness`
    refuses a real-data deployment in that state.
    """
    explicit = env("APPLICATION_REVISION", "").strip()
    if explicit:
        return explicit
    try:
        baked = (BASE_DIR / "GIT_SHA").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    return baked or "unknown"


APPLICATION_REVISION = _revision()


def _build_stamp() -> str:
    """When the running build was made, as an ISO-8601 UTC string.

    The Dockerfile writes BUILD_STAMP into the image, so the answer travels
    with the code it describes and nobody has to remember to update it. The
    environment variable wins where it is set, for a deployment that builds
    some other way; outside a container neither exists and the footer simply
    does not claim a build time, which is more honest than inventing one from
    process start.
    """
    explicit = env("APPLICATION_BUILT_AT", "").strip()
    if explicit:
        return explicit
    try:
        return (BASE_DIR / "BUILD_STAMP").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


APPLICATION_BUILT_AT = _build_stamp()

# Real Koda, member or otherwise confidential data may only exist in an
# environment that has passed the Secure Pilot Gate (master specification 16.3).
REAL_DATA_ALLOWED = env_bool("REAL_DATA_ALLOWED", default=False)

# The 2026 Excel operational pilot (docs/adr/0148) may write placeholder evidence
# and schedule historical follow-up checks — two things no ordinary importer is
# allowed to do. Off everywhere by default, and never put in an env file: the
# operator sets it on the one-off container that runs `excel_pilot_2026 apply`
# against the hosted test-build, so the running application never carries it.
EXCEL_PILOT_ENABLED = env_bool("JURISTID_EXCEL_PILOT", default=False)

# The evidence pilot (docs/adr/0173) writes reconstructed professional history —
# sent opinions with their real signed letters, consultation rounds, received
# feedback — from a reviewed package into an otherwise empty database. Off by
# default and never in an env file, for the reason the flag above gives: only the
# one-off container that runs `evidence_pilot apply` carries it.
EVIDENCE_PILOT_ENABLED = env_bool("JURISTID_EVIDENCE_PILOT", default=False)

# -- Cloudflare Access ------------------------------------------------------
#
# The production authenticator. Cloudflare authenticates a person against the
# Chamber's identity provider and forwards a signed assertion; this application
# verifies that signature against the team's published keys before believing a
# word of it. A request header on its own is attacker-controlled and proves
# nothing (docs/adr/0016, app/accounts/cloudflare_access.py).
#
# Not the mode the real-data stack runs in today: it runs `shared_gate`, and
# this is the hardening step after it. Both are supported modes, and which one
# a deployment uses is `AUTH_MODE` and nothing else (docs/adr/0016).
#
# The audience tag identifies *this* application inside the Cloudflare account.
# Without it, a token minted for any other application on the same team would
# verify, which is why an enabled-but-unconfigured Access denies rather than
# defaults.
#
# There is no separate on/off switch: Access is on exactly when
# `AUTH_MODE=cloudflare_access`. Two switches for one decision is how a
# deployment ends up with an authenticator that is configured and not running.
CF_ACCESS_TEAM_DOMAIN = env("CF_ACCESS_TEAM_DOMAIN", "")
CF_ACCESS_AUDIENCE = env("CF_ACCESS_AUDIENCE", "")

# -- Read-only MCP server for AI clients (docs/adr/0185) ---------------------
#
# Off unless a deployment turns it on. With it off the OAuth routes answer 404,
# no grant can be made and the `mcp` process refuses to start, so a release
# that carries this code changes nothing until an operator decides otherwise.
MCP_ENABLED = env_bool("MCP_ENABLED", False)
# The origin colleagues open Juristid at. Every link the MCP server returns and
# the OAuth issuer are built from it, never from a request's Host header.
JURISTID_PUBLIC_ORIGIN = env("JURISTID_PUBLIC_ORIGIN", "").rstrip("/")
# The resource (audience) values a token may be issued for and accepted at:
# the public MCP URL and/or the Secure MCP Tunnel URL OpenAI rewrites
# discovery to. A token for any other resource is refused.
MCP_RESOURCE_URLS = env_list("MCP_RESOURCE_URLS")
MCP_SCOPE = "juristid.read"
MCP_ACCESS_TOKEN_SECONDS = env_int("MCP_ACCESS_TOKEN_SECONDS", 3600)
MCP_REFRESH_TOKEN_SECONDS = env_int("MCP_REFRESH_TOKEN_SECONDS", 30 * 24 * 3600)
# The absolute lifetime of a token family: however often it is refreshed, a
# connection ends this long after the person typed their authenticator code,
# and they authorize again (MCPSEC-08).
MCP_REFRESH_FAMILY_MAX_SECONDS = env_int("MCP_REFRESH_FAMILY_MAX_SECONDS", 90 * 24 * 3600)
# How long after a rotation the same client may present the just-rotated
# refresh token again — a retry after a timeout, or two refreshes in flight —
# and get the same new pair instead of a revoked family (MCPSEC-09).
MCP_REFRESH_GRACE_SECONDS = env_int("MCP_REFRESH_GRACE_SECONDS", 30)
MCP_AUTHORIZATION_CODE_SECONDS = 60
MCP_ENROLMENT_LINK_SECONDS = env_int("MCP_ENROLMENT_LINK_SECONDS", 30 * 60)
# The key the personal MCP authenticators' TOTP secrets are sealed under, on
# `web` only (MCPSEC-07). Unset, they fall back to the MFA key derivation —
# `LOCAL_AUTH_MFA_ENCRYPTION_KEY or SECRET_KEY` — which on the shared-gate
# stack is SECRET_KEY: rotating that would make every authenticator
# unreadable. mcp_access.E004 requires it on real data; the `mcp` process must
# not hold it (juristid.E046). Changing it means everybody enrols again.
MCP_AUTHENTICATOR_KEY = env("MCP_AUTHENTICATOR_KEY", "")
# The key a rotated refresh token's successor is derived under, on `web` only
# (NEW-01). Unset, SECRET_KEY stands in — tests and laptops only:
# mcp_access.E005 requires it on real data, and the `mcp` process must not
# hold it (juristid.E046). Changing it costs at most a retry inside the
# refresh grace window; nobody re-enrols.
MCP_TOKEN_DERIVATION_KEY = env("MCP_TOKEN_DERIVATION_KEY", "")
# Wrong authenticator codes one gate session, and one client address, may type
# at `/oauth/authorize` in a window, whichever personas they try (MCPSEC-01).
# The per-grant lock is in app/mcp_access/services.py; this stops one session
# guessing a little at everybody's. The address limit is higher because the
# office shares one address.
MCP_AUTHORIZE_THROTTLE_SECONDS = env_int("MCP_AUTHORIZE_THROTTLE_SECONDS", 15 * 60)
MCP_AUTHORIZE_MAX_FAILURES_PER_SESSION = env_int("MCP_AUTHORIZE_MAX_FAILURES_PER_SESSION", 10)
MCP_AUTHORIZE_MAX_FAILURES_PER_ADDRESS = env_int("MCP_AUTHORIZE_MAX_FAILURES_PER_ADDRESS", 30)
# Pre-registered OAuth clients, by client id (a client metadata document URL).
# Only ChatGPT's own metadata document by default; there is no open dynamic
# client registration.
MCP_OAUTH_CLIENTS: dict[str, dict[str, object]] = {
    "https://chatgpt.com/oauth/client.json": {
        "client_name": "ChatGPT",
        "redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"],
        # ChatGPT's client metadata document declares private_key_jwt, and
        # with `none` beside it a leaked refresh token could be rotated by
        # anybody, from anywhere (MCPSEC-08). Signed assertions only.
        "token_endpoint_auth_methods": ["private_key_jwt"],
        "jwks_uri": "https://chatgpt.com/oauth/jwks.json",
    },
}
# The `mcp` process: where it listens inside the container network, how much
# it does at once and how much it returns.
# Loopback by default; the Compose service sets the container-network address,
# and it publishes no host port (deploy/unraid-main/compose.yml).
MCP_LISTEN_HOST = env("MCP_LISTEN_HOST", "127.0.0.1")
MCP_LISTEN_PORT = env_int("MCP_LISTEN_PORT", 8001)
MCP_MAX_CONCURRENT_CALLS = env_int("MCP_MAX_CONCURRENT_CALLS", 4)
MCP_CALL_TIMEOUT_SECONDS = env_int("MCP_CALL_TIMEOUT_SECONDS", 60)
MCP_FETCH_MAX_CHARS = env_int("MCP_FETCH_MAX_CHARS", 20_000)
MCP_MAX_REQUEST_BYTES = env_int("MCP_MAX_REQUEST_BYTES", 64 * 1024)

# -- MCP writes: prepare, approve in Juristid, commit (docs/adr/0189) ---------
#
# Off unless a deployment turns it on, on every process that reads it: with it
# off no write tool is listed, the write scope is neither advertised nor
# granted, the approval page answers 404 and the gateway refuses every call.
# Reading is unaffected either way.
MCP_WRITES_ENABLED = env_bool("MCP_WRITES_ENABLED", False)
MCP_WRITE_SCOPE = "juristid.write"
# Where the `mcp` process reaches the write gateway (`mcp-writer`), over the
# internal `mcp_write` network only, and the key that proves to the gateway
# that the caller is the `mcp` container. Held by `mcp` and `mcp-writer`,
# never by `web`. The key is not authorization: the gateway verifies the
# forwarded bearer token itself.
MCP_WRITE_GATEWAY_URL = env("MCP_WRITE_GATEWAY_URL", "http://127.0.0.1:8002").rstrip("/")
MCP_WRITE_GATEWAY_KEY = env("MCP_WRITE_GATEWAY_KEY", "")
MCP_WRITE_LISTEN_HOST = env("MCP_WRITE_LISTEN_HOST", "127.0.0.1")
MCP_WRITE_LISTEN_PORT = env_int("MCP_WRITE_LISTEN_PORT", 8002)
MCP_WRITE_MAX_CONCURRENT_CALLS = env_int("MCP_WRITE_MAX_CONCURRENT_CALLS", 2)
MCP_WRITE_CALL_TIMEOUT_SECONDS = env_int("MCP_WRITE_CALL_TIMEOUT_SECONDS", 30)
MCP_WRITE_MAX_REQUEST_BYTES = env_int("MCP_WRITE_MAX_REQUEST_BYTES", 64 * 1024)
# How long a prepared change waits for its person's approval, how long an
# approval may wait for its commit, and how long one authenticator code in a
# gate session spares the person typing another for the next approval. Each
# approval is still its own button press for its own proposal.
MCP_WRITE_PROPOSAL_SECONDS = env_int("MCP_WRITE_PROPOSAL_SECONDS", 15 * 60)
MCP_WRITE_APPROVAL_SECONDS = env_int("MCP_WRITE_APPROVAL_SECONDS", 10 * 60)
MCP_WRITE_REVERIFY_SECONDS = env_int("MCP_WRITE_REVERIFY_SECONDS", 5 * 60)
# The longest free text one operation takes (a note, a meeting summary).
MCP_WRITE_MAX_TEXT_CHARS = env_int("MCP_WRITE_MAX_TEXT_CHARS", 8000)

# -- the `mcp` process's transport (workstream B, docs/adr/0185 §6) ----------
#
# The Host headers the MCP server answers. A request naming any other host is
# refused before anything reads it (421), which is what stops a DNS-rebinding
# page from talking to the server through somebody's browser. By default: the
# hosts of MCP_RESOURCE_URLS (the public and/or tunnel URL a client was given),
# the Compose service name the tunnel client dials (`mcp`, `mcp:<port>`), and
# loopback for the container's own healthcheck. `host:*` matches any port.
_MCP_DEFAULT_ALLOWED_HOSTS = [
    *dict.fromkeys(
        urlsplit(url).netloc.lower() for url in MCP_RESOURCE_URLS if urlsplit(url).netloc
    ),
    "mcp",
    f"mcp:{MCP_LISTEN_PORT}",
    "localhost",
    "localhost:*",
    "127.0.0.1",
    "127.0.0.1:*",
]
MCP_ALLOWED_HOSTS = env_list("MCP_ALLOWED_HOSTS") or _MCP_DEFAULT_ALLOWED_HOSTS
# The Origin headers it accepts when a request carries one. A server-to-server
# client sends none and is not affected; a browser page always sends its own,
# and only these may reach the endpoint. Bearer tokens are never ambient, so
# this is defence in depth rather than the control.
MCP_ALLOWED_ORIGINS = env_list("MCP_ALLOWED_ORIGINS") or [
    *dict.fromkeys(
        f"{urlsplit(url).scheme}://{urlsplit(url).netloc}".lower()
        for url in [*MCP_RESOURCE_URLS, JURISTID_PUBLIC_ORIGIN]
        if urlsplit(url).netloc
    ),
    "https://chatgpt.com",
]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", "INFO")},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        # The MCP SDK's INFO lines quote what the caller sent — a resource URI,
        # a prompt or tool name, up to the request body limit (NEW-10). The
        # `mcp` process logs each call itself, by tool name and outcome only
        # (`app.mcp_server.calls`); the SDK's warnings and errors still show.
        "mcp": {"level": "WARNING"},
    },
}
