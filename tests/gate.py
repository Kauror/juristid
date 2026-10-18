"""The shared-gate settings, in one place, applied explicitly.

Four test modules configured `AUTH_MODE=shared_gate` with their own copy of the
same seven lines. Three were byte-identical; the fourth had drifted, omitting
the three throttle settings and relying on `config/settings.py` happening to
default to the same 5 / 300 / 3600. That is a silent coupling between a test and
a production default: change the default and the drifted module starts testing
something else, quietly.

**Deliberately a helper module rather than a `conftest.py` fixture.** A conftest
fixture is what makes a fixture implicit, and implicitness is the failure mode
here, in both directions:

* as ``autouse=True`` it would put the whole ``tests/`` suite behind the gate,
  when `config/settings.py` defaults ``AUTH_MODE`` to ``"none"`` and almost every
  other test assumes exactly that;
* as a plain fixture it would silently *lift* the gate from
  `tests/test_persona_switch.py` and `tests/test_shared_gate.py`, whose tests
  rely on their own autouse form and never name it.

`tests/test_default_home.py` is the case that proves the point: its copy is
deliberately **not** autouse, because that module also tests ``AUTH_MODE=none``
and Entra, and a module-wide gate would make those unreachable.

So each module keeps its own three-line fixture, with its own decorator, and
calls this. The decision that varies stays where it is load-bearing and visible;
only the seven lines that do not vary move here.
"""

from __future__ import annotations

from typing import Any

from app.accounts.enums import AuthMode

#: The one password the gate tests use. Not a secret: it exists so a test can
#: prove the gate refuses everything else.
PASSWORD = "seda-parooli-ei-ole-kusagil-mujal"  # noqa: S105


def apply_shared_gate(settings: Any, password: str = PASSWORD) -> Any:
    """Put this test module behind the shared gate.

    The throttle is stated rather than defaulted. A test that reads the gate's
    lockout behaviour and a production setting that decides it are two different
    facts, and a test that silently inherits the second cannot fail when it
    changes (`tests/test_concurrency.py` is the suite that cares).
    """
    settings.AUTH_MODE = AuthMode.SHARED_GATE
    settings.SHARED_GATE_PASSWORD = password
    settings.SHARED_GATE_MAX_ATTEMPTS = 5
    settings.SHARED_GATE_LOCKOUT_SECONDS = 300
    settings.SHARED_GATE_MAX_LOCKOUT_SECONDS = 3600
    settings.DEV_LOGIN_ENABLED = False
    settings.LOGIN_URL = "accounts:choose_persona"
    return settings


def business_rows() -> dict[str, int]:
    """How many business records exist — what a refused write must leave as it was."""
    from app.audit.models import ChangeEvent
    from app.documents.models import Document
    from app.matters.models import Entry, MatterEngagement, MatterExternalPosition
    from app.workflow.models import NextAction

    return {
        "entries": Entry.objects.count(),
        "positions": MatterExternalPosition.objects.count(),
        "engagements": MatterEngagement.objects.count(),
        "documents": Document.objects.count(),
        "actions": NextAction.objects.count(),
        "events": ChangeEvent.objects.count(),
    }


def assert_asked_to_choose_a_persona(response: Any) -> None:
    """The exact refusal a gate visitor with no persona gets from an htmx write.

    `login_required` redirects to the persona choice and
    `HtmxSignInMiddleware` turns that redirect into a 401 naming where to go
    (ENG-012). A 500, a 403 or a 404 is a different defect, so `!= 200` is not
    enough.
    """
    from django.urls import reverse

    from app.core.middleware import FAILURE_HEADER, SIGN_IN_HEADER

    assert response.status_code == 401, response.status_code
    assert response[FAILURE_HEADER] == "sign-in"
    assert response[SIGN_IN_HEADER].startswith(reverse("accounts:choose_persona"))
