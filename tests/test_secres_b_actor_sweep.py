"""Assessor B (WS1, 2026-10-29): two actors the resolver-wide sweeps did not fire.

`tests/test_business_routes_need_a_reader.py` walks every route as an anonymous
visitor, a persona-less gate session and an ADMINISTRATOR (GET only).
`tests/test_business_write_boundary.py` fires an inactive SPECIALIST and a
superuser-without-business-role at the write matrix only. This module closes
the two gaps the actor x route matrix still had:

* an **inactive** account — a session that was valid yesterday — over every
  route, GET and POST: Django's backend refuses it a user at all, so every
  business route must send it to sign in exactly as it sends a stranger;
* a **superuser with no business role** over every route, GET and POST:
  signed in, so pages answer — empty — and no POST may move a row.

Both reuse the route walk, the seeded ids and the hidden tokens of the
need-a-reader sweep, so a new route is swept here the day it appears there.

Status: boundary verified sound (passes on main 359e34bf).
"""

from __future__ import annotations

from typing import Any

import pytest
from django.apps import apps
from django.db.models import Max
from django.urls import reverse

from tests import factories
from tests.test_business_routes_need_a_reader import (  # noqa: F401 - `ids` is a fixture
    PUBLIC,
    SESSION_CHANGING,
    _redirected_to,
    _routes,
    _sweep,
    _url,
    ids,
)
from tests.test_idor_sweep_restricted_world import WATCHED

pytestmark = pytest.mark.django_db


def _fingerprint() -> list[tuple[str, int, Any]]:
    out = []
    for label in WATCHED:
        model = apps.get_model(label)
        stamp = "updated_at" if hasattr(model, "updated_at") else "created_at"
        out.append((label, model.objects.count(), model.objects.aggregate(m=Max(stamp))["m"]))
    return out


def test_an_inactive_account_is_a_stranger_on_every_route(client, settings, ids):  # noqa: F811
    settings.AUTH_MODE = "none"
    inactive = factories.UserFactory(is_active=False)
    client.force_login(inactive)
    # The backend hands the session no user: the request is anonymous.
    assert not client.get(reverse("core:healthz")).wsgi_request.user.is_authenticated

    problems = _sweep(client, ids, refused=_redirected_to(reverse(settings.LOGIN_URL)))

    assert problems == [], "\n".join(map(str, problems))


def test_a_superuser_without_a_business_role_reads_nothing_and_writes_nothing(
    client,
    ids,  # noqa: F811
):
    """`is_superuser` is a Django flag, not a business role (docs/adr/0181 §1)."""
    superuser = factories.AdministratorFactory(is_superuser=True, is_staff=True)
    client.force_login(superuser)

    def refused(name: str, response: Any) -> str:
        return ""

    before = _fingerprint()
    problems = _sweep(client, ids, refused=refused, post=True)
    assert problems == [], "\n".join(map(str, problems))
    assert _fingerprint() == before, "a superuser's POST changed a business row"

    # The counts a signed-in shell draws are empty too, not merely token-free:
    # one open commencement reminder exists, and the bell says nothing of it.
    from datetime import timedelta

    from django.utils import timezone

    from app.notifications.models import CommencementReminder

    # The bell counts the reader's own Matters' prompts, and the unowned ones
    # for whoever holds `department.view_management` (app/notifications/selectors.py).
    unowned = factories.MatterFactory(owner=None)
    effective = factories.EffectiveDateFactory(
        matter=unowned, date_value=timezone.localdate() + timedelta(days=3)
    )
    CommencementReminder.objects.create(
        effective_date=effective,
        source_date=effective.date_value,
        stage="SEVEN_DAYS",
        due_on=timezone.localdate() - timedelta(days=1),
    )
    assert client.get(reverse("notifications:bell")).context["count"] == 0
    client.force_login(factories.DepartmentHeadFactory())
    assert client.get(reverse("notifications:bell")).context["count"] == 1


def test_every_route_the_sweeps_skip_is_still_the_short_public_list():
    """A session-changing route is excluded from the walk, not from the policy."""
    names = {name for name, _ in _routes()}
    assert SESSION_CHANGING <= set(PUBLIC)
    assert set(PUBLIC) <= names
