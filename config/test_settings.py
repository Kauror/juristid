"""Settings for the test suite.

Tests must behave identically wherever they run, so this module states the
values it depends on explicitly rather than inheriting whatever the surrounding
environment happens to export. It is the module `pytest` is configured to use.

Never point a running process at it.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Must be set before the real settings module is imported: it refuses to load
# without a secret key, which is the runtime behaviour we want to keep.
os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-secret-key-not-used-anywhere-else")

from config.settings import *

DEBUG = False
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# The environment gates are exercised through the `settings` fixture, so the
# suite starts from the safe position.
DEV_LOGIN_ENABLED = False
REAL_DATA_ALLOWED = False
APPLICATION_ENVIRONMENT = "test"

# No `collectstatic` has run for a test process, so the hashed manifest the
# production backend expects does not exist.
STATIC_MANIFEST = False

# In-process cache. The deployed settings use a database cache so that the PIN
# lockout counter is shared between gunicorn workers; a test process has one
# worker, so the reason does not apply, and this avoids needing
# `createcachetable` before the suite can run.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# Evidence and derivatives written by tests go to throwaway directories, never
# the checkout — and to *separate* ones, because keeping the two storage classes
# apart is itself a property under test (docs/adr/0014).
#
# A fresh directory per process, which is worth knowing about: anything that
# shells out to `manage.py` from inside the suite must pass
# DJANGO_SETTINGS_MODULE=config.settings explicitly, or the child gets its own
# empty evidence directory and every file it looks for is missing. The browser
# suite learned this the hard way.
EVIDENCE_ROOT = Path(tempfile.mkdtemp(prefix="juristid-evidence-"))
DERIVATIVE_ROOT = Path(tempfile.mkdtemp(prefix="juristid-derivatives-"))

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
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
