"""Test environment defaults.

Loaded before Django is configured. Everything here describes an isolated test
environment: a throwaway secret, no TLS redirect, and no claim to hold real
data.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-secret-key-not-used-anywhere-else")
os.environ.setdefault("DJANGO_DEBUG", "0")
os.environ.setdefault("DJANGO_SECURE_SSL_REDIRECT", "0")
os.environ.setdefault("DJANGO_SECURE_HSTS_SECONDS", "0")
os.environ.setdefault("DEV_LOGIN_ENABLED", "0")
os.environ.setdefault("REAL_DATA_ALLOWED", "0")
os.environ.setdefault("POSTGRES_SSLMODE", "disable")
os.environ.setdefault("APPLICATION_ENVIRONMENT", "test")

# Evidence written by tests goes to a throwaway directory, never the checkout.
os.environ.setdefault("EVIDENCE_ROOT", tempfile.mkdtemp(prefix="juristid-evidence-"))
