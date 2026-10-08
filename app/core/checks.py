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
                    "Real Koda or member data may only exist behind a real authenticator "
                    "(AUTH_MODE shared_gate, cloudflare_access or local_password), never "
                    "behind the synthetic sign-in."
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
                    "Set AUTH_MODE to shared_gate, cloudflare_access or (after the activation "
                    "runbook) local_password. Real member material must not be served to "
                    "whoever reaches the port."
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

    if mode == AuthMode.LOCAL_PASSWORD:
        problems.extend(_local_password_problems())

    problems.extend(_account_email_problems(mode))

    return problems


#: OWASP's minimum Argon2id configuration: 19 MiB of memory, two passes.
ARGON2_MINIMUM_MEMORY_KIB = 19456
ARGON2_MINIMUM_TIME_COST = 2


def _local_password_problems() -> list[Error | Warning]:
    """Every safeguard personal sign-in depends on, actually present (docs/adr/0145).

    Two tiers, and the line is the one the rest of this file draws. What makes
    the mode unsafe *anywhere* — a password floor below NIST's, Argon2id not the
    hasher, a limit that is zero — refuses every process. What makes it unsafe
    *for real data* — a test-grade hash cost, no dedicated MFA key, no TLS —
    refuses a deployment marked REAL_DATA_ALLOWED, which is the only kind that
    could hold a real person's password.
    """
    from app.accounts.passwords import MINIMUM_LENGTH

    problems: list[Error | Warning] = []

    if int(getattr(settings, "LOCAL_AUTH_PASSWORD_MIN_LENGTH", 0)) < MINIMUM_LENGTH:
        problems.append(
            Error(
                f"LOCAL_AUTH_PASSWORD_MIN_LENGTH is below {MINIMUM_LENGTH}.",
                hint="NIST SP 800-63B-4 requires 15 characters for a password used on its own.",
                id="juristid.E030",
            )
        )

    first_hasher = (settings.PASSWORD_HASHERS or [""])[0]
    if "Argon2" not in first_hasher:
        problems.append(
            Error(
                "Personal passwords would not be hashed with Argon2id.",
                hint="The first entry of PASSWORD_HASHERS must be an Argon2 hasher.",
                id="juristid.E031",
            )
        )
    elif settings.REAL_DATA_ALLOWED and (
        int(settings.LOCAL_AUTH_ARGON2_MEMORY_KIB) < ARGON2_MINIMUM_MEMORY_KIB
        or int(settings.LOCAL_AUTH_ARGON2_TIME_COST) < ARGON2_MINIMUM_TIME_COST
        or int(settings.LOCAL_AUTH_ARGON2_PARALLELISM) < 1
    ):
        problems.append(
            Error(
                "The Argon2id cost is below OWASP's minimum for a real-data deployment.",
                hint=(
                    f"LOCAL_AUTH_ARGON2_MEMORY_KIB >= {ARGON2_MINIMUM_MEMORY_KIB}, "
                    f"LOCAL_AUTH_ARGON2_TIME_COST >= {ARGON2_MINIMUM_TIME_COST}, "
                    "LOCAL_AUTH_ARGON2_PARALLELISM >= 1."
                ),
                id="juristid.E031",
            )
        )

    limits = {
        "LOCAL_AUTH_SESSION_IDLE_SECONDS": settings.LOCAL_AUTH_SESSION_IDLE_SECONDS,
        "LOCAL_AUTH_SESSION_ABSOLUTE_SECONDS": settings.LOCAL_AUTH_SESSION_ABSOLUTE_SECONDS,
        "LOCAL_AUTH_REAUTH_SECONDS": settings.LOCAL_AUTH_REAUTH_SECONDS,
        "LOCAL_AUTH_ACTIVATION_LINK_SECONDS": settings.LOCAL_AUTH_ACTIVATION_LINK_SECONDS,
        "LOCAL_AUTH_RESET_LINK_SECONDS": settings.LOCAL_AUTH_RESET_LINK_SECONDS,
    }
    unbounded = sorted(name for name, value in limits.items() if int(value) <= 0)
    if unbounded:
        problems.append(
            Error(
                f"Personal sign-in limits that are not positive: {', '.join(unbounded)}.",
                hint="A zero limit is a session, a re-authentication or a link that never ends.",
                id="juristid.E032",
            )
        )

    if settings.REAL_DATA_ALLOWED and not getattr(settings, "LOCAL_AUTH_MFA_ENCRYPTION_KEY", ""):
        problems.append(
            Error(
                "LOCAL_AUTH_MFA_ENCRYPTION_KEY is empty on a real-data deployment.",
                hint=(
                    "TOTP secrets would be encrypted under a key derived from SECRET_KEY, so "
                    "rotating SECRET_KEY would silently unenrol every authenticator. Set a "
                    "dedicated host-side secret."
                ),
                id="juristid.E033",
            )
        )

    if not settings.DEBUG and not (
        settings.SESSION_COOKIE_SECURE
        and settings.CSRF_COOKIE_SECURE
        and settings.SESSION_COOKIE_HTTPONLY
    ):
        problems.append(
            Error(
                "Personal sign-in is on with a session or CSRF cookie that is not Secure, or "
                "a session cookie readable by scripts.",
                hint="SESSION_COOKIE_SECURE, CSRF_COOKIE_SECURE and SESSION_COOKIE_HTTPONLY.",
                id="juristid.E034",
            )
        )

    if settings.REAL_DATA_ALLOWED and not (
        getattr(settings, "SECURE_PROXY_SSL_HEADER", None) or settings.SECURE_SSL_REDIRECT
    ):
        problems.append(
            Error(
                "Personal sign-in on a real-data deployment with nothing enforcing HTTPS.",
                hint=(
                    "Set DJANGO_BEHIND_TLS_PROXY=1 behind the tunnel, or "
                    "DJANGO_SECURE_SSL_REDIRECT=1. A password must never cross the network in "
                    "clear."
                ),
                id="juristid.E035",
            )
        )

    breached = getattr(settings, "LOCAL_AUTH_BREACHED_PASSWORDS_PATH", "")
    if breached:
        from pathlib import Path

        if not Path(breached).is_file():
            problems.append(
                Warning(
                    "LOCAL_AUTH_BREACHED_PASSWORDS_PATH does not name a readable file.",
                    hint="The common-password list still applies; the breach list does not.",
                    id="juristid.W032",
                )
            )

    return problems


#: Backends that would write a message — and the one-time link in it — to a
#: console, a file or memory rather than to its recipient.
_NON_DELIVERING_BACKENDS = (
    "django.core.mail.backends.console.EmailBackend",
    "django.core.mail.backends.filebased.EmailBackend",
    "django.core.mail.backends.locmem.EmailBackend",
)


def _account_email_problems(mode: str) -> list[Error | Warning]:
    """Account mail is off unless it was turned on deliberately, in the one mode it belongs to."""
    from app.accounts.enums import AuthMode

    if not getattr(settings, "ACCOUNT_EMAIL_DELIVERY_ENABLED", False):
        return []
    problems: list[Error | Warning] = []
    if mode != AuthMode.LOCAL_PASSWORD:
        problems.append(
            Error(
                "ACCOUNT_EMAIL_DELIVERY_ENABLED is on in a mode that has no account e-mail.",
                hint=(
                    "Invitations and reset links exist only under AUTH_MODE=local_password. "
                    "Turning delivery on is a step of the activation runbook, not a setting to "
                    "leave on beside the shared gate."
                ),
                id="juristid.E036",
            )
        )
    base = getattr(settings, "ACCOUNT_LINK_BASE_URL", "") or ""
    if not base or (settings.REAL_DATA_ALLOWED and not base.startswith("https://")):
        problems.append(
            Error(
                "Account e-mail is on without an https ACCOUNT_LINK_BASE_URL.",
                hint=(
                    "Links are built from this setting, never from the request's Host header, "
                    "and on a real-data deployment it must be the final https address."
                ),
                id="juristid.E037",
            )
        )
    if not getattr(settings, "ACCOUNT_EMAIL_FROM", ""):
        problems.append(
            Error(
                "Account e-mail is on without ACCOUNT_EMAIL_FROM.",
                hint="State the sender address the Chamber's mail service accepts.",
                id="juristid.E037",
            )
        )
    if settings.REAL_DATA_ALLOWED and settings.EMAIL_BACKEND in _NON_DELIVERING_BACKENDS:
        problems.append(
            Error(
                "Account e-mail on a real-data deployment would be written to a console, a "
                "file or memory.",
                hint="The message carries a one-time credential; it may only go to its recipient.",
                id="juristid.E038",
            )
        )
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
