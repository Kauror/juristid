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
