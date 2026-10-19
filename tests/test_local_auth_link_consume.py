"""Spending a one-time link: every way `tokens.consume` says no (docs/adr/0145 §7).

The pages never reach these refusals on their own: the set-password form asks
`tokens.peek` first, and peek already turns away a spent, expired or forged
link. `consume` repeats every check under the row's lock because the two calls
are not one moment — a link can be spent, superseded or run out between them —
so the refusals are proved here directly, and once through
`credentials.set_password_with_link` with the gap forced open.

Each refusal also leaves the row as it was: a refused link is not marked used.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from app.accounts import credentials, tokens
from app.accounts.enums import CredentialTokenPurpose, ProvisioningState
from app.accounts.models import AccountCredentialToken
from tests import factories
from tests.local_auth import OTHER_PASSWORD, PASSWORD

pytestmark = pytest.mark.django_db

ACTIVATION = CredentialTokenPurpose.ACTIVATION


def _invited():
    return factories.UserFactory(
        upn="link.kolleeg@koda.ee",
        email="link.kolleeg@koda.ee",
        is_active=False,
        provisioning_state=ProvisioningState.INVITED,
    )


def _issued():
    user = _invited()
    link = tokens.issue(user=user, purpose=ACTIVATION)
    return user, link, tokens.proof_of(link.token)


def _row(link):
    return AccountCredentialToken.objects.values("used_at", "invalidated_at").get(pk=link.record.pk)


def test_a_good_link_is_spent_once():
    _user, link, proof = _issued()

    assert tokens.consume(proof, purpose=ACTIVATION) == link.record
    assert _row(link)["used_at"] is not None


def test_a_spent_link_is_refused():
    _user, link, proof = _issued()
    tokens.consume(proof, purpose=ACTIVATION)
    spent = _row(link)

    assert tokens.consume(proof, purpose=ACTIVATION) is None
    assert _row(link) == spent


def test_an_expired_link_is_refused_and_stays_unspent():
    _user, link, proof = _issued()
    # Issued two days ago (the database keeps every expiry after its issue).
    AccountCredentialToken.objects.filter(pk=link.record.pk).update(
        created_at=timezone.now() - timedelta(days=2),
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    assert tokens.consume(proof, purpose=ACTIVATION) is None
    assert _row(link) == {"used_at": None, "invalidated_at": None}


def test_a_superseded_link_is_refused():
    user, old, old_proof = _issued()
    tokens.issue(user=user, purpose=ACTIVATION)

    assert tokens.consume(old_proof, purpose=ACTIVATION) is None
    assert _row(old)["used_at"] is None


def test_a_forged_verifier_is_refused():
    """The right selector with somebody else's digest: found, compared, refused."""
    _user, link, proof = _issued()
    selector, _ = proof.split(".")
    forged = f"{selector}.{'0' * 64}"

    assert tokens.consume(forged, purpose=ACTIVATION) is None
    assert _row(link) == {"used_at": None, "invalidated_at": None}


def test_a_link_for_another_purpose_is_refused():
    _user, link, proof = _issued()

    assert tokens.consume(proof, purpose=CredentialTokenPurpose.PASSWORD_RESET) is None
    assert _row(link)["used_at"] is None


@pytest.mark.parametrize(
    "value",
    ["", "pole-link", "0" * 32, f"{'0' * 32}.", f"{'g' * 32}.{'0' * 64}", f"{'0' * 32}.{'0' * 63}"],
    ids=["empty", "no-separator", "selector-only", "no-digest", "bad-selector", "short-digest"],
)
def test_a_malformed_value_is_refused_without_a_query(value, django_assert_num_queries):
    with django_assert_num_queries(0):
        assert tokens.consume(value, purpose=ACTIVATION) is None


def test_a_link_spent_between_the_form_and_the_save_sets_no_password(monkeypatch):
    """The window `consume` re-checks for: peek said yes, then the link was used."""
    user, _link, proof = _issued()
    user.set_password(PASSWORD)
    user.save(update_fields=["password"])
    seen = tokens.peek(proof, purpose=ACTIVATION)
    assert seen is not None
    tokens.consume(proof, purpose=ACTIVATION)  # the other tab got there first
    monkeypatch.setattr(tokens, "peek", lambda *_args, **_kwargs: seen)

    assert (
        credentials.set_password_with_link(proof=proof, purpose=ACTIVATION, password=OTHER_PASSWORD)
        is None
    )
    user.refresh_from_db()
    assert user.check_password(PASSWORD)
    assert user.provisioning_state == ProvisioningState.INVITED
