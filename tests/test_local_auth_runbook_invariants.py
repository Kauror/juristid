"""What the activation runbook says about the code, held to the code (AUD-051 AU-7).

Only statements that are executable invariants are pinned here — never the
runbook's wording:

* archive link review moves from the department head (shared gate) to the
  ADMINISTRATOR (`local_password`): an activation decision, runbook §1;
* business capability overrides stay *effective* after a rollback to the gate,
  because `has_capability` does not ask the mode: runbook §13;
* rotating `SECRET_KEY` does not invalidate one-time links (an unkeyed SHA-256
  is stored) and does reset the throttle's keys: runbook §3;
* every `LOCAL_AUTH_ACTIVATION_RUNBOOK.md §N` the code points to exists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings as django_settings

from app.accounts import capabilities, throttle, tokens
from app.accounts.enums import Capability, CredentialTokenPurpose, UserRole
from app.core.authorization import has_capability
from app.legacy_import.opinion_access import may_manage_archive_links, may_read_archive
from tests import factories
from tests.gate import apply_shared_gate
from tests.local_auth import apply_local_password

ROOT = Path(django_settings.BASE_DIR)
RUNBOOK = ROOT / "docs" / "LOCAL_AUTH_ACTIVATION_RUNBOOK.md"
ROTATED_SECRET_KEY = "a-rotated-secret-key-that-is-nothing-like-the-old-one"  # noqa: S105


@pytest.mark.django_db
def test_archive_link_review_moves_from_the_head_to_the_administrator(settings):
    head = factories.UserFactory(role=UserRole.DEPARTMENT_HEAD)
    administrator = factories.UserFactory(role=UserRole.ADMINISTRATOR)

    apply_shared_gate(settings)
    assert may_manage_archive_links(head)
    assert not may_manage_archive_links(administrator)

    apply_local_password(settings)
    assert not may_manage_archive_links(head)
    assert may_manage_archive_links(administrator)
    # Reading did not move: only filing changes hands.
    assert may_read_archive(head) and may_read_archive(administrator)


@pytest.mark.django_db
def test_business_capability_overrides_stay_effective_under_the_gate(settings):
    """A rollback does not make an override inert: decide each one (runbook §13)."""
    deputy = factories.UserFactory(
        role=UserRole.SPECIALIST,
        capability_overrides={Capability.VIEW_DEPARTMENT_MANAGEMENT.value: capabilities.ALLOW},
    )
    withdrawn = factories.UserFactory(
        role=UserRole.SPECIALIST,
        capability_overrides={Capability.ASSIGN_WORK.value: capabilities.DENY},
    )
    apply_shared_gate(settings)

    assert has_capability(deputy, Capability.VIEW_DEPARTMENT_MANAGEMENT)
    assert not has_capability(withdrawn, Capability.ASSIGN_WORK)
    assert has_capability(factories.UserFactory(role=UserRole.SPECIALIST), Capability.ASSIGN_WORK)


@pytest.mark.django_db
def test_a_one_time_link_survives_a_secret_key_rotation(settings):
    user = factories.UserFactory()
    link = tokens.issue(user=user, purpose=CredentialTokenPurpose.ACTIVATION)
    proof = tokens.proof_of(link.token)

    settings.SECRET_KEY = ROTATED_SECRET_KEY

    assert tokens.peek(proof, purpose=CredentialTokenPurpose.ACTIVATION) is not None


def test_a_secret_key_rotation_rekeys_the_throttle(settings):
    before = throttle.digest("kasutaja@koda.ee", "192.0.2.1")
    settings.SECRET_KEY = ROTATED_SECRET_KEY

    assert throttle.digest("kasutaja@koda.ee", "192.0.2.1") != before


_POINTER = re.compile(r"LOCAL_AUTH_ACTIVATION_RUNBOOK\.md[\s#]*§(\d+)")


def _pointers() -> list[tuple[str, str]]:
    found = []
    for folder in ("app", "config"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            for number in _POINTER.findall(path.read_text(encoding="utf-8")):
                found.append((path.relative_to(ROOT).as_posix(), number))
    return found


def test_every_runbook_section_the_code_points_to_exists():
    sections = set(re.findall(r"^## (\d+)\.", RUNBOOK.read_text(encoding="utf-8"), re.M))
    pointers = _pointers()

    assert ("app/accounts/mfa.py", "15") in pointers, "the lost-key remedy is pointed to"
    missing = [(where, number) for where, number in pointers if number not in sections]
    assert not missing, f"pointers to runbook sections that do not exist: {missing}"
