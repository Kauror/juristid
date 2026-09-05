"""Django settings for Koda Õigusloome (juristid).

One settings module, driven by environment variables. Environment-specific
behaviour is expressed through env values rather than a settings inheritance
tree, so a deployed container is configured the same way the local container is.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from config.env import database_config_from_url, env, env_bool, env_int, env_list

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
]

INSTALLED_APPS = DJANGO_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Before anything that reads business content, and it wraps the rest of the
    # stack so its `finally` runs even when a view raises. It holds one
    # request's authorization lookups and nothing else
    # (app/core/authorization.py, `remember_grants_for_one_request`).
    "app.core.middleware.RequestScopeMiddleware",
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
#
# Business authorization is identical in all three. What differs is how much
# the deployment may claim about the identity it hands to that authorization.
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

# How long a gate session lasts before the password is asked for again.
SHARED_GATE_SESSION_SECONDS = env_int("SHARED_GATE_SESSION_SECONDS", 12 * 60 * 60)

# Where `login_required` sends somebody who has no identity yet, which is a
# different place in each mode:
#
#   shared_gate   the persona selector — they are already past the door, they
#                 just have not said whose work they are looking at
#   otherwise     the synthetic sign-in
#
# In shared-gate mode the middleware has already bounced anybody who is not
# behind the password, so this redirect is only ever reached by a reader with no
# persona (app/accounts/middleware.py).
LOGIN_URL = (
    "accounts:choose_persona"
    if (AUTH_MODE or "").strip().lower() == "shared_gate"
    else "accounts:dev_login"
)
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "core:home"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Local synthetic-user sign-in. Never enabled outside an isolated developer
# environment; the real-data pilot and production authenticate through
# Microsoft Entra ID. See docs/adr/0004-authentication-direction.md.
DEV_LOGIN_ENABLED = env_bool("DEV_LOGIN_ENABLED", default=False)

# A shared PIN in front of the synthetic sign-in. Empty means no PIN, which is
# the right default for a laptop and for CI.
#
# It exists for one situation: the rehearsal instance is reachable from outside
# the LAN, and the synthetic sign-in has no password by design — you pick a user
# from a list. A short shared PIN is *not* authentication and is not pretending
# to be. It is a speed bump that keeps a passer-by out, and it is only defensible
# because the data behind it is invented.
#
# Anything reachable from the internet that holds real data needs Entra ID, or
# an authenticating proxy, or both (docs/adr/0004).
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
FILE_UPLOAD_MAX_MEMORY_SIZE = env_int("DJANGO_FILE_UPLOAD_MAX_MEMORY_SIZE", 10 * 1024 * 1024)
MAX_EVIDENCE_UPLOAD_BYTES = env_int("MAX_EVIDENCE_UPLOAD_BYTES", 100 * 1024 * 1024)

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
if env_bool("DJANGO_BEHIND_TLS_PROXY", default=False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

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
    },
}
