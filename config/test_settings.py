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

from config.test_safety import (
    assert_environment_is_safe_for_tests,
    assert_storage_is_isolated,
)

# First, before anything else in this module and before the real settings module
# is imported: refuse to be the test settings of a process that was started in a
# deployment's environment. It is checked here rather than in a system check
# because a check runs after settings are in force, and "in force" is already
# late enough for a test to have written a file (2026-08-24).
assert_environment_is_safe_for_tests()

# Must be set before the real settings module is imported: it refuses to load
# without a secret key, which is the runtime behaviour we want to keep.
os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-secret-key-not-used-anywhere-else")

from config.settings import *

#: Something the running process can be asked, rather than a module name that
#: has to be spelled right. The repository-root `conftest.py` reads it to prove
#: the settings actually in force are these ones.
IS_TEST_SETTINGS = True

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
#
# These are the floor rather than what a test normally writes to: the autouse
# `evidence_root` fixture in tests/conftest.py gives every test its own
# directory on top of this. Both layers exist on purpose — the fixture is what
# isolates one test from another, and these are what a process gets before any
# fixture has run, and what a settings module that is *not* this one would not
# have.
EVIDENCE_ROOT = Path(tempfile.mkdtemp(prefix="juristid-evidence-"))
DERIVATIVE_ROOT = Path(tempfile.mkdtemp(prefix="juristid-derivatives-"))
LEGACY_SOURCE_ROOT = Path(tempfile.mkdtemp(prefix="juristid-legacy-source-"))

# And having chosen them, prove they are not a deployment's. The lines above are
# unconditional today, so this can only fire if someone edits them — which is
# the point: the cost of getting this wrong is measured in files written to the
# Chamber's evidence store, and a comment saying "keep these temporary" is not a
# control.
assert_storage_is_isolated(
    {
        "EVIDENCE_ROOT": EVIDENCE_ROOT,
        "DERIVATIVE_ROOT": DERIVATIVE_ROOT,
        "LEGACY_SOURCE_ROOT": LEGACY_SOURCE_ROOT,
    }
)

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
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
