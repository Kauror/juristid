"""Delegated sign-in by device code, with no secret anywhere.

Three properties, each chosen rather than inherited.

**Delegated, not app-only.** Not merely a preference: Microsoft's own
documentation states that the Graph OneNote API does not support app-only
authentication, and stopped doing so in March 2025. So the tool reads exactly
what the signed-in person can already read, and nothing else — which is also the
right answer for a notebook full of member correspondence.

**Device code, so there is no client secret.** A confidential client would need
a secret stored somewhere, rotated by somebody, and kept out of a public
repository forever. The device-code flow needs only a public client id: the
person signs in in their own browser, on their own machine, and this process
never sees a password.

**`Notes.Read` and nothing more.** The least-privileged scope that permits the
GET requests this tool makes. If a future need genuinely requires more, that is
a consent decision for the tenant's administrator to make knowingly — this tool
stops and says so rather than quietly asking for `Notes.ReadWrite`
(Stage-2B brief 52).

Nothing here writes a token to disk, prints one, or accepts one as an argument.
The access token lives in memory for the length of one export.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

#: Least privileged for reading OneNote. `offline_access` is deliberately
#: absent: a refresh token would let this tool act later, unattended, which is
#: exactly the capability a bounded read-only proof should not acquire.
SCOPES = ("https://graph.microsoft.com/Notes.Read",)

AUTHORITY = "https://login.microsoftonline.com"


class AuthError(RuntimeError):
    pass


class ConsentRequired(AuthError):
    """The tenant has not approved this scope, and a person must.

    Raised rather than retried with a broader scope. Escalating the request
    until something is granted is how an app ends up holding
    `Notes.ReadWrite.All` because nobody read the second consent screen.
    """


@dataclass(frozen=True)
class DeviceCodePrompt:
    verification_uri: str
    user_code: str
    expires_in: int
    message: str


@dataclass(frozen=True)
class AccessToken:
    value: str
    expires_at: float

    @property
    def is_valid(self) -> bool:
        return time.time() < self.expires_at - 60


def begin_device_code(
    *, client_id: str, tenant: str = "organizations"
) -> tuple[DeviceCodePrompt, dict]:
    """Ask Microsoft for a code the person will type into their own browser."""
    payload = _post(
        f"{AUTHORITY}/{tenant}/oauth2/v2.0/devicecode",
        {"client_id": client_id, "scope": " ".join(SCOPES)},
    )
    prompt = DeviceCodePrompt(
        verification_uri=payload["verification_uri"],
        user_code=payload["user_code"],
        expires_in=int(payload.get("expires_in", 900)),
        message=payload.get("message", ""),
    )
    return prompt, payload


def poll_for_token(
    *, client_id: str, device_code: dict, tenant: str = "organizations"
) -> AccessToken:
    """Wait for the person to finish signing in.

    ``authorization_pending`` is the normal state and is not an error. The two
    that *are* errors get distinct treatment: a declined sign-in is a decision
    to respect, and a missing consent is a message for whoever administers the
    tenant.
    """
    interval = max(int(device_code.get("interval", 5)), 5)
    deadline = time.time() + int(device_code.get("expires_in", 900))
    code = device_code["device_code"]

    while time.time() < deadline:
        time.sleep(interval)
        try:
            payload = _post(
                f"{AUTHORITY}/{tenant}/oauth2/v2.0/token",
                {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": client_id,
                    "device_code": code,
                },
            )
        except AuthError as error:
            reason = str(error)
            if "authorization_pending" in reason:
                continue
            if "slow_down" in reason:
                interval += 5
                continue
            if "consent" in reason or "AADSTS65001" in reason:
                raise ConsentRequired(
                    "Notes.Read has not been consented for this application in this tenant. "
                    "An administrator must approve it; this tool will not request a broader "
                    "scope to work around it."
                ) from error
            raise
        return AccessToken(
            value=payload["access_token"],
            expires_at=time.time() + int(payload.get("expires_in", 3600)),
        )

    raise AuthError("The device code expired before sign-in completed.")


def _post(url: str, form: dict[str, str]) -> dict:
    request = urllib.request.Request(  # noqa: S310 - constant HTTPS authority
        url,
        data=urllib.parse.urlencode(form).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        # The body carries the OAuth error code the caller branches on. It is
        # not secret — it is a state name like `authorization_pending` — and no
        # token can be in a failed response.
        raise AuthError(body) from error
