"""Settings for the browser suite's personal sign-in server.

`AUTH_MODE=local_password` exists in no deployment (docs/adr/0145), so the
browser job runs a third server in that mode beside the ordinary one and the
shared-gate one (`config/e2e_gate_settings.py`, whose reasoning this follows).
Three things set it apart, each deliberate:

* **Its own database.** `<POSTGRES_DB>_localauth`, not the seeded world. The
  suite creates accounts, sends invitations and appoints the first
  administrator — none of which may leak into the persona lists, the team table
  or the counts the rest of the browser suite asserts. The job creates and
  migrates it; `e2e/test_local_auth.py` flushes it before it starts, so a local
  re-run starts from nothing too.
* **Mail to files.** `ACCOUNT_EMAIL_DELIVERY_ENABLED` is on and the backend is
  Django's file backend into `E2E_LOCAL_AUTH_MAIL_DIR`, where the browser test
  reads the one-time links a person would read in their inbox. Nothing is sent
  anywhere: there is no SMTP configuration in this module or the job.
* **A refusal of real data.** Like the gate rehearsal, this is a throwaway
  server on localhost and raises before it can answer a request if the
  environment says otherwise.

Never point a deployment at this.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from config.settings import *  # noqa: F403

IS_E2E_LOCAL_AUTH_SETTINGS = True

AUTH_MODE = "local_password"
DEV_LOGIN_ENABLED = False
# Derived from AUTH_MODE at import in `config/settings.py`, before the line above.
LOGIN_URL = "accounts:sign_in"

DATABASES = {
    "default": {
        **DATABASES["default"],  # noqa: F405
        "NAME": os.environ.get(
            "E2E_LOCAL_AUTH_DATABASE",
            f"{DATABASES['default']['NAME']}_localauth",  # noqa: F405
        ),
    }
}

ACCOUNT_EMAIL_DELIVERY_ENABLED = True
EMAIL_BACKEND = "django.core.mail.backends.filebased.EmailBackend"
EMAIL_FILE_PATH = Path(
    os.environ.get(
        "E2E_LOCAL_AUTH_MAIL_DIR", str(Path(tempfile.gettempdir()) / "juristid-e2e-mail")
    )
)
ACCOUNT_EMAIL_FROM = "juristid-e2e@koda.ee"
ACCOUNT_LINK_BASE_URL = os.environ.get("E2E_LOCAL_AUTH_BASE_URL", "http://127.0.0.1:8002")

if os.environ.get("REAL_DATA_ALLOWED", "0") not in {"0", "", "false", "False"}:
    raise RuntimeError(
        "config.e2e_local_auth_settings is a browser-test rehearsal and must never run "
        "with REAL_DATA_ALLOWED."
    )
