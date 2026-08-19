from __future__ import annotations

from typing import Any

from django.conf import settings
from django.http import HttpRequest


def application(request: HttpRequest) -> dict[str, Any]:
    return {
        "application_name": settings.APPLICATION_NAME,
        "application_stage": settings.APPLICATION_STAGE,
        "application_environment": settings.APPLICATION_ENVIRONMENT,
        "application_revision": settings.APPLICATION_REVISION,
        "real_data_allowed": settings.REAL_DATA_ALLOWED,
        "dev_login_enabled": settings.DEV_LOGIN_ENABLED,
    }
