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

# Evidence written by tests goes to a throwaway directory, never the checkout.
EVIDENCE_ROOT = Path(tempfile.mkdtemp(prefix="juristid-evidence-"))

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    EVIDENCE_STORAGE_ALIAS: {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": str(EVIDENCE_ROOT)},
    },
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
