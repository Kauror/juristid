"""Deployment-safety system checks.

These exist so a misconfigured process refuses to start rather than quietly
running with a development shortcut enabled.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.checks import Error, Warning, register


@register()
def check_runtime_safety(app_configs: Any, **kwargs: Any) -> list[Error | Warning]:
    problems: list[Error | Warning] = []

    if not settings.DEBUG and settings.SECRET_KEY == settings.DEV_INSECURE_SECRET_KEY:
        problems.append(
            Error(
                "The development SECRET_KEY is in use with DEBUG off.",
                hint="Set DJANGO_SECRET_KEY to a real secret.",
                id="juristid.E001",
            )
        )

    if settings.DEV_LOGIN_ENABLED and not settings.DEBUG:
        problems.append(
            Error(
                "DEV_LOGIN_ENABLED is on outside a development environment.",
                hint="Synthetic local sign-in is only permitted with DJANGO_DEBUG on.",
                id="juristid.E002",
            )
        )

    if settings.DEV_LOGIN_ENABLED and settings.REAL_DATA_ALLOWED:
        problems.append(
            Error(
                "DEV_LOGIN_ENABLED must never be combined with REAL_DATA_ALLOWED.",
                hint=(
                    "Real Koda or member data may only exist in an environment that has "
                    "passed the Secure Pilot Gate and authenticates through Entra ID."
                ),
                id="juristid.E003",
            )
        )

    if settings.REAL_DATA_ALLOWED and settings.DEBUG:
        problems.append(
            Error(
                "REAL_DATA_ALLOWED must never be combined with DJANGO_DEBUG.",
                id="juristid.E004",
            )
        )

    engine = settings.DATABASES["default"]["ENGINE"]
    if engine != "django.db.backends.postgresql":
        problems.append(
            Error(
                f"Unsupported database engine {engine!r}; PostgreSQL 18+ is required.",
                id="juristid.E005",
            )
        )

    return problems
