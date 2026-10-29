"""Security/resilience review 2026-10-29, WS1 assessor A: rate limits and lockouts.

What each limit is keyed on, and what the production topology — every reader
behind one Cloudflare edge, most of them behind one office egress address —
does to it.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from app.accounts.enums import ThrottleScope
from app.accounts.models import AuthenticationThrottle, SharedGateThrottle
from tests.admin_session import apply_admin_gate, full_admin, pass_gate
from tests.gate import PASSWORD

GATE = "/konto/varav/"
OFFICE = "198.51.100.7"  # one NAT egress address for the whole department


@pytest.fixture(autouse=True)
def gate_settings(settings):
    apply_admin_gate(settings)
    settings.TRUST_CF_CONNECTING_IP = True
    return settings


# ---------------------------------------------------------------------------
# A-04  The gate lockout is per client *address*. Behind one office egress the
#       address is the department: five wrong passwords from any device on the
#       office network (a visitor on the Wi-Fi, malware, a colleague's script)
#       lock every colleague out of the register for 5 min, then 10, 20, 40,
#       60 — repeatable for as long as the attacker keeps five wrong guesses
#       per window. The correct password is not even checked while locked.
#       This is the "denial-of-service primitive" the design says it refuses
#       (shared_gate.record_failure), reappearing through the NAT.
#       OWNER DECISION, not a code fix: options are (a) accept, (b) exempt a
#       browser that already passed the gate recently (a signed "known
#       device" cookie, the `TrustedSignInSource` idea applied to the gate),
#       (c) let a correct password through a locked bucket at a slower rate.
#       This test documents the behaviour; it passes.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a04_five_wrong_passwords_from_the_office_egress_lock_every_colleague_out():
    attacker = Client()
    for _ in range(5):
        attacker.post(GATE, {"password": "vale"}, HTTP_CF_CONNECTING_IP=OFFICE)
    colleague = Client()
    response = colleague.post(GATE, {"password": PASSWORD}, HTTP_CF_CONNECTING_IP=OFFICE)
    assert response.status_code == 429, "documented: the office is one throttle bucket"
    assert "shared_gate:passed_at" not in colleague.session
    # A colleague at home (another address) is unaffected — the per-client
    # design holds exactly as far as addresses differ.
    remote = Client().post(GATE, {"password": PASSWORD}, HTTP_CF_CONNECTING_IP="203.0.113.9")
    assert remote.status_code == 302


@pytest.mark.django_db
def test_a04_the_step_up_network_counter_is_the_office_too():
    """The administrator step-up charges a network counter (30/5 min) shared by the egress."""
    admin, _secret = full_admin()
    attacker = pass_gate(Client())
    # The pair counter (address + network) locks after five misses and a
    # locked attempt is not counted further, so the network counter is only
    # reached by trying several addresses: six addresses, five misses each.
    for n in range(30):
        attacker.post(
            reverse("haldus:sign_in"),
            {"email": f"keegi{n // 5}@koda.ee", "password": "vale"},
            HTTP_CF_CONNECTING_IP=OFFICE,
        )
    administrator = pass_gate(Client())
    response = administrator.post(
        reverse("haldus:sign_in"),
        {"email": admin.upn, "password": "kolm hobust jooksevad üle silla 2026"},
        HTTP_CF_CONNECTING_IP=OFFICE,
    )
    assert response.status_code == 429, (
        "documented: 30 misses spread over six addresses lock the egress"
    )
    assert AuthenticationThrottle.objects.filter(scope=ThrottleScope.SIGN_IN_NETWORK).count() == 1


# ---------------------------------------------------------------------------
# A-05  `ThrottleScope.CREDENTIAL_LINK` has a policy (20 / 300 s / 3600 s) and
#       no caller: opening one-time links is not counted anywhere. The links
#       carry 128 + 256 random bits, so guessing is not a practical attack —
#       but the enum promises a control that does not exist, and each probe
#       costs a session write (A-03) and a database lookup.
#       Fix: charge `throttle.network_counter(ThrottleScope.CREDENTIAL_LINK, request)`
#       in `local_views._link_view` before `tokens.peek`, or delete the scope.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a05_link_probes_are_not_counted_by_the_credential_link_scope():
    """A-05 (P3, CONFIRMED when this fails): twenty-five bad links, no counter row."""
    client = pass_gate(Client())
    selector = "0" * 32
    verifier = "A" * 43
    for n in range(25):
        response = client.get(
            reverse("accounts:activate"),
            {"kood": f"{selector[:-2]}{n:02d}.{verifier}"},
            HTTP_CF_CONNECTING_IP=OFFICE,
        )
        assert response.status_code == 302  # the proof is kept, the bare page is next
        page = client.get(reverse("accounts:activate"), HTTP_CF_CONNECTING_IP=OFFICE)
        assert page.status_code in (404, 429), page.status_code
    assert AuthenticationThrottle.objects.filter(scope=ThrottleScope.CREDENTIAL_LINK).exists(), (
        "no CREDENTIAL_LINK counter was ever charged"
    )


# ---------------------------------------------------------------------------
# Sound: what the gate throttle table holds, and that one wrong password per
# address makes one row (the growth bound is the number of distinct addresses,
# see A-01 for why IPv6 makes that bound large).
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_sound_one_row_per_address_and_no_address_in_it():
    for host in range(1, 4):
        Client().post(GATE, {"password": "vale"}, HTTP_CF_CONNECTING_IP=f"203.0.113.{host}")
    rows = list(SharedGateThrottle.objects.values_list("client_key", flat=True))
    assert len(rows) == 3
    assert all("203.0.113" not in key for key in rows)
