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

    problems.extend(_authentication_problems())
    problems.extend(_environment_hygiene_problems())

    engine = settings.DATABASES["default"]["ENGINE"]
    if engine != "django.db.backends.postgresql":
        problems.append(
            Error(
                f"Unsupported database engine {engine!r}; PostgreSQL 18+ is required.",
                id="juristid.E005",
            )
        )

    return problems


def _environment_hygiene_problems() -> list[Error | Warning]:
    """A boolean nobody spelled the way `config/env.py` reads booleans.

    Anything unrecognised is read as false. That is the safe direction — every
    flag here is dangerous only when true — and it is a silent one:
    `REAL_DATA_ALLOWED=enabled` and `REAL_DATA_ALLOWED=0` behave identically and
    look nothing alike to the person who typed one of them.

    A warning rather than an error, because the fallback is safe and refusing to
    start over a typo in a flag that is already off would be worse than saying
    so. The values are flags, never secrets, so naming them is safe.
    """
    from app.core.deployment import unparseable_boolean_variables

    unparseable = unparseable_boolean_variables()
    if not unparseable:
        return []

    named = ", ".join(f"{name}={value!r}" for name, value in sorted(unparseable.items()))
    return [
        Warning(
            f"Environment variables that are neither true nor false: {named}.",
            hint=(
                "They are being read as false. Use 1/0, true/false, yes/no or on/off "
                "(config/env.py)."
            ),
            id="juristid.W015",
        )
    ]


def _authentication_problems() -> list[Error | Warning]:
    """Whether this deployment has an authenticator worth the data behind it.

    The rule is not "some authenticator is configured" but "the configured mode
    is one this data may sit behind, and every safeguard that mode depends on is
    actually present". The shared gate counts as an authenticator for real data
    *only* with all of its safeguards — a long secret supplied host-side, no
    debug output, and no synthetic sign-in beside it. Loosening any of those
    turns a door with a lock into a door with a sign on it
    (Stage-2D auth brief 3, docs/adr/0016).
    """
    from app.accounts import shared_gate
    from app.accounts.enums import AuthMode

    problems: list[Error | Warning] = []
    mode = shared_gate.current_mode()

    if (getattr(settings, "AUTH_MODE", "") or "").strip().lower() not in AuthMode.values:
        problems.append(
            Error(
                f"AUTH_MODE={settings.AUTH_MODE!r} is not a mode this application has.",
                hint=f"One of: {', '.join(AuthMode.values)}.",
                id="juristid.E009",
            )
        )

    if settings.REAL_DATA_ALLOWED and mode == AuthMode.NONE:
        problems.append(
            Error(
                "REAL_DATA_ALLOWED is on with no authenticator in front of it.",
                hint=(
                    "Set AUTH_MODE to shared_gate or cloudflare_access. Real member "
                    "material must not be served to whoever reaches the port."
                ),
                id="juristid.E006",
            )
        )

    if mode == AuthMode.CLOUDFLARE_ACCESS and not (
        settings.CF_ACCESS_TEAM_DOMAIN and settings.CF_ACCESS_AUDIENCE
    ):
        problems.append(
            Error(
                "AUTH_MODE is cloudflare_access but the team domain or audience is missing.",
                hint=(
                    "Without an audience tag, a token minted for any other application "
                    "on the same Cloudflare team would verify here."
                ),
                id="juristid.E007",
            )
        )

    if mode != AuthMode.NONE and settings.DEV_LOGIN_ENABLED:
        problems.append(
            Error(
                "DEV_LOGIN_ENABLED must not be combined with a real authenticator.",
                hint="A passwordless sign-in page behind a gate is a way around the gate.",
                id="juristid.E008",
            )
        )

    if mode == AuthMode.SHARED_GATE:
        problems.extend(_shared_gate_problems())

    return problems


#: Long enough that guessing is not the attack. Shorter than this and the
#: throttle is doing all the work, which is not what a throttle is for.
MINIMUM_SHARED_GATE_LENGTH = 12


def _shared_gate_problems() -> list[Error | Warning]:
    from app.accounts import shared_gate

    problems: list[Error | Warning] = []
    password = shared_gate.configured_password()

    if not password:
        problems.append(
            Error(
                "AUTH_MODE is shared_gate but JURISTID_SHARED_GATE_PASSWORD is empty.",
                hint=(
                    "The password is a host-side secret. It belongs in the deployment's "
                    "environment file and nowhere else — not in Git, not in Compose "
                    "defaults, not in the image."
                ),
                id="juristid.E010",
            )
        )
    elif len(password) < MINIMUM_SHARED_GATE_LENGTH:
        problems.append(
            Error(
                f"The shared gate password is shorter than {MINIMUM_SHARED_GATE_LENGTH} "
                "characters.",
                hint=(
                    "This replaced a four-digit PIN that was explicitly not good enough "
                    "for real data. A longer password is the reason the replacement is "
                    "acceptable; rate limiting is defence in depth, not the control."
                ),
                id="juristid.E011",
            )
        )

    if settings.SHARED_GATE_MAX_ATTEMPTS < 1 or settings.SHARED_GATE_LOCKOUT_SECONDS < 1:
        problems.append(
            Error(
                "The shared gate is configured with no working rate limit.",
                hint="SHARED_GATE_MAX_ATTEMPTS and SHARED_GATE_LOCKOUT_SECONDS must be positive.",
                id="juristid.E012",
            )
        )

    if not settings.DEBUG and not getattr(settings, "SECURE_PROXY_SSL_HEADER", None):
        problems.append(
            Warning(
                "The shared gate is on but nothing tells Django the connection is secure.",
                hint=(
                    "Set DJANGO_BEHIND_TLS_PROXY=1 where a proxy terminates TLS. Without "
                    "it no HSTS header is sent, request.is_secure() is False, and CSRF "
                    "skips its referer check."
                ),
                id="juristid.E014",
            )
        )

    if not settings.DEBUG and not settings.SESSION_COOKIE_SECURE:
        problems.append(
            Error(
                "The shared gate is on with a session cookie that is not Secure.",
                hint="One password guards everything here; its session must not travel in clear.",
                id="juristid.E013",
            )
        )

    return problems
