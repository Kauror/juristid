from __future__ import annotations

from django.db import models


class UserRole(models.TextChoices):
    """Application roles (master specification 5.1).

    ADMINISTRATOR is a technical role. It does not carry business access to
    RESTRICTED content; see app/core/authorization.py.
    """

    SPECIALIST = "SPECIALIST", "Spetsialist / jurist"
    DEPARTMENT_HEAD = "DEPARTMENT_HEAD", "Osakonnajuht"
    ADMINISTRATOR = "ADMINISTRATOR", "Süsteemiadministraator"
    READER = "READER", "Lugeja"


class AuthMode(models.TextChoices):
    """How this deployment decides who is at the keyboard.

    The three are genuinely different claims, and the difference is the point:

    * ``NONE`` — nothing authenticates. Only a developer laptop and CI, where
      the synthetic sign-in provides an identity and the data is invented.
    * ``SHARED_GATE`` — one password, shared by the department, then a persona
      picked from a list. This **authenticates the door, not the person**: it
      proves somebody knows the shared password, and nothing more. It is a
      temporary development-phase mode and its limitation is recorded on every
      audit row it produces (docs/adr/0016).
    * ``CLOUDFLARE_ACCESS`` — Cloudflare authenticates the individual against
      the Chamber's identity provider and signs an assertion this application
      verifies. The only mode that can claim an individual identity.

    Business authorization does not change between them. All three end in the
    same `scope_for_user` chokepoint; what changes is how much the deployment
    is entitled to say about who that user is.
    """

    NONE = "none", "Autentimiseta (arendus)"
    SHARED_GATE = "shared_gate", "Jagatud parool"
    CLOUDFLARE_ACCESS = "cloudflare_access", "Cloudflare Access"


#: What an audit row records about how the actor arrived. Never "the person is
#: who they say they are" unless the mode can actually prove it.
AUTHENTICATED_VIA = {
    AuthMode.NONE: "NONE",
    AuthMode.SHARED_GATE: "SHARED_GATE",
    AuthMode.CLOUDFLARE_ACCESS: "CLOUDFLARE_ACCESS",
}
