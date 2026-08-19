"""Minimal, explicit environment reading.

Deliberately hand-rolled: the project needs a dozen settings, not a
configuration framework. Every lookup is visible in ``config/settings.py``.
"""

from __future__ import annotations

import os
from urllib.parse import unquote, urlparse

TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


class ImproperlyConfigured(Exception):
    """Raised when a required environment variable is missing."""


def env(name: str, default: str | None = None, *, required: bool = False) -> str:
    # An empty environment variable means "not configured", not "empty value".
    value = os.environ.get(name) or default
    if required and not value:
        raise ImproperlyConfigured(f"Environment variable {name} is required.")
    return value or ""


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in TRUE_VALUES


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def env_list(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def database_config_from_url(url: str) -> dict[str, object]:
    """Parse ``postgres://user:pass@host:port/name`` into Django DATABASES config.

    Only PostgreSQL is supported; the product requires PostgreSQL 18+.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ImproperlyConfigured(
            f"Unsupported DATABASE_URL scheme {parsed.scheme!r}; PostgreSQL is required."
        )
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "",
        "PORT": str(parsed.port or ""),
    }
