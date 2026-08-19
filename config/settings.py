"""Django settings for Koda Õigusloome (juristid).

One settings module, driven by environment variables. Environment-specific
behaviour is expressed through env values rather than a settings inheritance
tree, so a deployed container is configured the same way the local container is.
"""

from __future__ import annotations

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
LOGIN_URL = "accounts:dev_login"
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

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    EVIDENCE_STORAGE_ALIAS: {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": str(EVIDENCE_ROOT)},
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
# Security
# --------------------------------------------------------------------------

SECURE_HSTS_SECONDS = env_int("DJANGO_SECURE_HSTS_SECONDS", 0 if DEBUG else 31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = False
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", default=not DEBUG)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = not DEBUG
X_FRAME_OPTIONS = "DENY"

if env_bool("DJANGO_BEHIND_TLS_PROXY", default=False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# --------------------------------------------------------------------------
# Application metadata and logging
# --------------------------------------------------------------------------

APPLICATION_NAME = "Koda Õigusloome"
APPLICATION_STAGE = env("APPLICATION_STAGE", "Stage 2A")
APPLICATION_ENVIRONMENT = env("APPLICATION_ENVIRONMENT", "local")
APPLICATION_REVISION = env("APPLICATION_REVISION", "unknown")

# Real Koda, member or otherwise confidential data may only exist in an
# environment that has passed the Secure Pilot Gate (master specification 16.3).
REAL_DATA_ALLOWED = env_bool("REAL_DATA_ALLOWED", default=False)

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
