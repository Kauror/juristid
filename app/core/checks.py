"""Deployment-safety system checks.

These exist so a misconfigured process refuses to start rather than quietly
running with a development shortcut enabled.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register

#: The roles `JURISTID_PROCESS_ROLE` may name (config/settings.py). Empty is the
#: browser application and its workers; `web` and `worker` say the same thing
#: explicitly; `mcp` is the read-only MCP server (docs/adr/0185).
KNOWN_PROCESS_ROLES = frozenset({"", "web", "worker", "mcp"})


def _process_role() -> str:
    return (getattr(settings, "JURISTID_PROCESS_ROLE", "") or "").strip().lower()


def _is_mcp_process() -> bool:
    """The read-only MCP server, which serves no browser and holds no browser secret.

    Only the checks about what the *browser* depends on — the gate password and
    its throttle, the second factor's key — are skipped for it, because it has
    neither a gate nor a sign-in page: it answers bearer tokens and nothing else.
    Every other check still applies, and `_process_role_problems` adds what this
    process depends on instead (docs/adr/0185 §6).
    """
    return _process_role() == "mcp"


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
    problems.extend(_real_data_secret_problems())
    problems.extend(_environment_hygiene_problems())
    problems.extend(_process_role_problems())

    engine = settings.DATABASES["default"]["ENGINE"]
    if engine != "django.db.backends.postgresql":
        problems.append(
            Error(
                f"Unsupported database engine {engine!r}; PostgreSQL 18+ is required.",
                id="juristid.E005",
            )
        )

    return problems


#: Every secret in the deployment templates (`deploy/*/.env.example`) is a
#: placeholder starting with this. The repository is public, so a placeholder
#: left in a real environment file is a secret the whole internet knows.
TEMPLATE_PLACEHOLDER_PREFIX = "replace-me"

#: Django's own `--deploy` floor for SECRET_KEY (security.W009), as an error
#: where real data is. The template generates 88 characters.
MINIMUM_REAL_DATA_SECRET_KEY_LENGTH = 50

#: The gate password on real data. `juristid.E011`'s 12 stays the floor for the
#: mode everywhere; real data asks for more, and the template generates 32.
MINIMUM_REAL_DATA_SHARED_GATE_LENGTH = 16

#: The database password on real data. The template generates 48.
MINIMUM_REAL_DATA_DATABASE_PASSWORD_LENGTH = 16


def _real_data_secrets() -> dict[str, str]:
    """The host-side secrets a real-data process holds, by environment variable.

    Every message built from these names the variable, never its value.
    """
    from app.accounts import shared_gate

    return {
        "DJANGO_SECRET_KEY": settings.SECRET_KEY or "",
        "JURISTID_SHARED_GATE_PASSWORD": shared_gate.configured_password(),
        "POSTGRES_PASSWORD": str(settings.DATABASES["default"].get("PASSWORD") or ""),
        "LOCAL_AUTH_MFA_ENCRYPTION_KEY": getattr(settings, "LOCAL_AUTH_MFA_ENCRYPTION_KEY", "")
        or "",
        "DJANGO_EMAIL_HOST_PASSWORD": getattr(settings, "EMAIL_HOST_PASSWORD", "") or "",
    }


def _real_data_secret_problems() -> list[Error | Warning]:
    """Real data behind secrets that are real (JOPS-003).

    The template's placeholders are long enough to pass `juristid.E011` and are
    not the development key `juristid.E001` knows, so a copied `.env.example`
    with one value left unfilled passed every check and served real data behind
    a password published in the repository. Only with REAL_DATA_ALLOWED: the
    development and rehearsal stacks keep their short synthetic values.
    """
    if not settings.REAL_DATA_ALLOWED:
        return []

    from app.accounts import shared_gate
    from app.accounts.enums import AuthMode

    problems: list[Error | Warning] = []
    secrets = _real_data_secrets()

    placeholders = sorted(
        name
        for name, value in secrets.items()
        if value.strip().lower().startswith(TEMPLATE_PLACEHOLDER_PREFIX)
    )
    if placeholders:
        problems.append(
            Error(
                f"Template placeholders are in use on a real-data deployment: "
                f"{', '.join(placeholders)}.",
                hint=(
                    "Every `replace-me…` value in .env.example is public. Generate each "
                    "secret as the template's header says and put it in the host-side "
                    "environment file."
                ),
                id="juristid.E016",
            )
        )

    if len(secrets["DJANGO_SECRET_KEY"]) < MINIMUM_REAL_DATA_SECRET_KEY_LENGTH:
        problems.append(
            Error(
                f"DJANGO_SECRET_KEY is shorter than {MINIMUM_REAL_DATA_SECRET_KEY_LENGTH} "
                "characters on a real-data deployment.",
                hint="Generate it with `openssl rand -base64 64`.",
                id="juristid.E017",
            )
        )

    gate = secrets["JURISTID_SHARED_GATE_PASSWORD"]
    if (
        shared_gate.current_mode() == AuthMode.SHARED_GATE
        and gate
        and len(gate) < MINIMUM_REAL_DATA_SHARED_GATE_LENGTH
    ):
        problems.append(
            Error(
                f"The shared gate password is shorter than {MINIMUM_REAL_DATA_SHARED_GATE_LENGTH} "
                "characters on a real-data deployment.",
                hint=(
                    "One password stands in front of the whole register. Generate it with "
                    "`openssl rand -base64 24`."
                ),
                id="juristid.E018",
            )
        )

    if len(secrets["POSTGRES_PASSWORD"]) < MINIMUM_REAL_DATA_DATABASE_PASSWORD_LENGTH:
        problems.append(
            Error(
                f"POSTGRES_PASSWORD is shorter than {MINIMUM_REAL_DATA_DATABASE_PASSWORD_LENGTH} "
                "characters on a real-data deployment.",
                hint="Generate it with `openssl rand -base64 36`.",
                id="juristid.E019",
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

    # The gate and its per-client throttle live in the browser application. The
    # MCP process serves neither, holds no gate password (juristid.E046 refuses
    # one) and is reached only through bearer tokens, so neither set applies.
    if mode == AuthMode.SHARED_GATE and not _is_mcp_process():
        problems.extend(_shared_gate_problems())

    if mode == AuthMode.LOCAL_PASSWORD:
        problems.extend(_local_password_problems())

    if not _is_mcp_process():
        problems.extend(_client_address_problems(mode))
    problems.extend(_account_email_problems(mode))

    return problems


def _throttle_needs_the_client_address(mode: str) -> bool:
    """Real data behind a password throttle keyed on the client address.

    `shared_gate` and `local_password` count wrong passwords per client
    (`app/accounts/shared_gate.py`, `app/accounts/throttle.py`), and the client is
    `app.core.client_ip.client_ip`. `cloudflare_access` has no password and no
    such throttle; `none` serves no real data (E006).
    """
    from app.accounts.enums import AuthMode

    return bool(settings.REAL_DATA_ALLOWED) and mode in (
        AuthMode.SHARED_GATE,
        AuthMode.LOCAL_PASSWORD,
    )


def _client_address_problems(mode: str) -> list[Error | Warning]:
    """The throttle must see the client, not the tunnel (JRF-02).

    Behind `cloudflared` every request reaches gunicorn from the connector, and
    `CF-Connecting-IP` is believed only with `TRUST_CF_CONNECTING_IP`, which is
    `DJANGO_BEHIND_TLS_PROXY` (JOPS-008). Without it the per-client throttle is
    one bucket for the whole department: five wrong passwords from anybody lock
    everybody out. On real data that is refused rather than warned about, so
    release step 6 (`run --rm web … migration_plan`) stops on it before the
    backup and the swap. Elsewhere `juristid.E014` still warns.
    """
    if not _throttle_needs_the_client_address(mode):
        return []
    if getattr(settings, "TRUST_CF_CONNECTING_IP", False):
        return []
    return [
        Error(
            "Real data behind a password throttle, and the client's address is not trusted "
            "from the proxy.",
            hint=(
                "Set DJANGO_BEHIND_TLS_PROXY=1 (the tunnel is the only way in). Without it "
                "every request arrives from the tunnel's address, the per-client throttle "
                "becomes one bucket for everybody, and a few wrong passwords lock the whole "
                "department out (app/core/client_ip.py)."
            ),
            id="juristid.E020",
        )
    ]


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

    # The MCP process never reads or writes a second factor, and must not hold
    # the key that decrypts them (juristid.E046).
    if (
        settings.REAL_DATA_ALLOWED
        and not getattr(settings, "LOCAL_AUTH_MFA_ENCRYPTION_KEY", "")
        and not _is_mcp_process()
    ):
        problems.append(
            Error(
                "LOCAL_AUTH_MFA_ENCRYPTION_KEY is empty on a real-data deployment.",
                hint=(
                    "TOTP secrets would be encrypted under a key derived from SECRET_KEY, so "
                    "rotating SECRET_KEY would silently break every enrolled authenticator. Set a "
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

#: The shortest idle limit the gate can keep: its last-request stamp is written
#: at most this often (`shared_gate.LAST_SEEN_RESOLUTION`, five minutes).
MINIMUM_SHARED_GATE_IDLE_SECONDS = 300


def _shared_gate_problems() -> list[Error | Warning]:
    from app.accounts import shared_gate
    from app.accounts.enums import AuthMode

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

    if settings.SHARED_GATE_IDLE_SECONDS < MINIMUM_SHARED_GATE_IDLE_SECONDS or (
        settings.SHARED_GATE_IDLE_SECONDS > settings.SHARED_GATE_SESSION_SECONDS
    ):
        problems.append(
            Error(
                f"SHARED_GATE_IDLE_SECONDS is outside {MINIMUM_SHARED_GATE_IDLE_SECONDS}–"
                "SHARED_GATE_SESSION_SECONDS.",
                hint=(
                    "The idle clock is stamped at most every five minutes, so a shorter "
                    "limit asks an active reader for the password; a limit beyond the "
                    "absolute one never fires. The decided value is 28800 (8 hours)."
                ),
                id="juristid.E021",
            )
        )

    if (
        not settings.DEBUG
        and not getattr(settings, "SECURE_PROXY_SSL_HEADER", None)
        # On real data the same missing flag is juristid.E020, an error; said once.
        and not (
            _throttle_needs_the_client_address(AuthMode.SHARED_GATE)
            and not getattr(settings, "TRUST_CF_CONNECTING_IP", False)
        )
    ):
        problems.append(
            Warning(
                "The shared gate is on but nothing tells Django the connection is secure.",
                hint=(
                    "Set DJANGO_BEHIND_TLS_PROXY=1 where a proxy terminates TLS. Without "
                    "it no HSTS header is sent, request.is_secure() is False, CSRF "
                    "skips its referer check, and audit rows and the gate throttle see "
                    "the tunnel's address instead of the client's (app/core/client_ip.py)."
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


#: The browser's secrets, which the MCP process must not hold (docs/adr/0185 §6):
#: the gate password, the key second factors are encrypted under, and the mail
#: password. By environment variable, so a refusal names the variable and never
#: its value.
MCP_FORBIDDEN_SECRETS = {
    "JURISTID_SHARED_GATE_PASSWORD": "SHARED_GATE_PASSWORD",
    "LOCAL_AUTH_MFA_ENCRYPTION_KEY": "LOCAL_AUTH_MFA_ENCRYPTION_KEY",
    "DJANGO_EMAIL_HOST_PASSWORD": "EMAIL_HOST_PASSWORD",
    # Seals the MCP authenticators; only `web` opens them (MCPSEC-07).
    "MCP_AUTHENTICATOR_KEY": "MCP_AUTHENTICATOR_KEY",
}


def _is_https_url(value: str, *, origin_only: bool) -> bool:
    parts = urlsplit(value or "")
    if parts.scheme != "https" or not parts.hostname:
        return False
    if parts.username or parts.password or parts.query or parts.fragment:
        return False
    return not (origin_only and parts.path not in ("", "/"))


def _process_role_problems() -> list[Error | Warning]:
    """The process role is one this application has, and the MCP role is configured.

    The MCP process answers an AI client on a colleague's behalf, outside the
    browser, so what it depends on is different and is refused here rather than
    discovered on the first call (docs/adr/0185):

    - E040: a role nobody wrote — a typo would otherwise run as the browser;
    - E041: MCP_ENABLED off — a release carrying the code changes nothing;
    - E042: JURISTID_PUBLIC_ORIGIN, the OAuth issuer and the base of every link
      the tools return, is not one https origin;
    - E043: MCP_RESOURCE_URLS, the audiences a token may carry, is empty or not
      all https;
    - E044: DEBUG, whose error pages would answer a tool's failure with the
      code and the data around it;
    - E045: the database connection would not start read-only;
    - E046: the process holds a browser secret it has no use for.
    """
    role = _process_role()
    if role not in KNOWN_PROCESS_ROLES:
        return [
            Error(
                f"JURISTID_PROCESS_ROLE={role!r} is not a role this application has.",
                hint="Leave it empty for the browser application and its workers, or set mcp.",
                id="juristid.E040",
            )
        ]
    if role != "mcp":
        return []

    problems: list[Error | Warning] = []
    if not getattr(settings, "MCP_ENABLED", False):
        problems.append(
            Error(
                "The MCP process is starting with MCP_ENABLED off.",
                hint=(
                    "Set MCP_ENABLED=1 in its environment, and on `web` too — or do not start "
                    "the `mcp` service. Off is the default (docs/adr/0185)."
                ),
                id="juristid.E041",
            )
        )
    if not _is_https_url(getattr(settings, "JURISTID_PUBLIC_ORIGIN", ""), origin_only=True):
        problems.append(
            Error(
                "JURISTID_PUBLIC_ORIGIN is not an https origin.",
                hint=(
                    "The OAuth issuer and every link the tools return are built from it, never "
                    "from a request's Host header: e.g. https://juristid.orgusaar.ee, no path."
                ),
                id="juristid.E042",
            )
        )
    resources = list(getattr(settings, "MCP_RESOURCE_URLS", []) or [])
    if not resources or not all(_is_https_url(url, origin_only=False) for url in resources):
        problems.append(
            Error(
                "MCP_RESOURCE_URLS is empty or names a URL that is not https.",
                hint=(
                    "List the MCP endpoint URL(s) a token may be issued for — the Secure MCP "
                    "Tunnel URL and/or the public one — comma-separated, each https."
                ),
                id="juristid.E043",
            )
        )
    if settings.DEBUG:
        problems.append(
            Error(
                "The MCP process is starting with DJANGO_DEBUG on.",
                hint="It answers outside the browser, on a colleague's behalf; never in debug.",
                id="juristid.E044",
            )
        )
    database: dict[str, Any] = settings.DATABASES["default"]
    connection_options = database.get("OPTIONS") or {}
    options = (
        str(connection_options.get("options") or "") if isinstance(connection_options, dict) else ""
    )
    if "default_transaction_read_only=on" not in options.replace(" ", ""):
        problems.append(
            Error(
                "The MCP process's database connection does not start read-only.",
                hint=(
                    "config/settings.py adds `-c default_transaction_read_only=on` in the mcp "
                    "role; something replaced DATABASES['default']['OPTIONS'] after it."
                ),
                id="juristid.E045",
            )
        )
    held = sorted(
        name for name, setting in MCP_FORBIDDEN_SECRETS.items() if getattr(settings, setting, "")
    )
    if held:
        problems.append(
            Error(
                f"The MCP process holds browser secrets it has no use for: {', '.join(held)}.",
                hint=(
                    "Give the `mcp` service its own minimal environment file, not the web's "
                    "(docs/adr/0185 §6)."
                ),
                id="juristid.E046",
            )
        )
    return problems


@register(Tags.database)
def check_mcp_database_role(app_configs: Any = None, **kwargs: Any) -> list[Error | Warning]:
    """juristid.E047: the MCP process is connected as a SELECT-only role (MCPSEC-03).

    `default_transaction_read_only` (E045) is a session default that any code in
    the process can switch off; the role's privileges are what no code can.
    So in the `mcp` role the connected role must hold no superuser or
    role-creating attribute, belong to no other role, own nothing, hold no
    INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER or CREATE privilege in
    schema public, read the token tables, not read `django_session`, start
    read-only, and carry its bounds as role settings — `temp_file_limit`,
    `statement_timeout`, `idle_in_transaction_session_timeout`, read-only by
    default (NEW-02) — read from the catalogue, not from this session's options
    (`app.mcp_server.db_role.privilege_problems`).

    An error wherever real data is allowed; a warning elsewhere, where a laptop
    or CI connects as the one role it has. A database check: `run_mcp_server`
    runs it before serving (`manage.py check --database default` does too).
    Only the catalogue is read.
    """
    if _process_role() != "mcp":
        return []
    from django.db import connection

    from app.mcp_server.db_role import privilege_problems

    try:
        with connection.cursor() as cursor:
            problems = privilege_problems(cursor)
    except Exception as error:  # pragma: no cover - the database is down: E047 cannot tell
        problems = [f"the role's privileges could not be read ({type(error).__name__})"]
    if not problems:
        return []
    kind = Error if getattr(settings, "REAL_DATA_ALLOWED", False) else Warning
    return [
        kind(
            "The MCP process's database role is not SELECT-only: " + "; ".join(problems) + ".",
            hint=(
                "Create the role from web with `manage.py mcp_db_role --apply --password-stdin` "
                "and give `mcp` its name and password (POSTGRES_USER=juristid_mcp in "
                "juristid-mcp.env; docs/mcp/OPERATIONS.md)."
            ),
            id="juristid.E047",
        )
    ]
