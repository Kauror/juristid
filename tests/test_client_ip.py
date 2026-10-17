"""The client's address on audit rows and in the throttles: one rule (JOPS-008).

Behind the Cloudflare tunnel ``REMOTE_ADDR`` is the connector's address for
every request, so audit rows that recorded it all named the tunnel, while the
gate throttle read ``CF-Connecting-IP`` and named the client. The two now come
from `app.core.client_ip.client_ip`:

* the header is used only where the deployment trusts the proxy
  (``TRUST_CF_CONNECTING_IP``, from ``DJANGO_BEHIND_TLS_PROXY``);
* otherwise, and whenever the header is not one IP address, ``REMOTE_ADDR``;
* and the throttle key and the audit row are derived from the same address.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.test import RequestFactory
from django.urls import reverse

from app.accounts import shared_gate
from app.accounts import throttle as local_throttle
from app.accounts.models import SharedGateThrottle
from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.core.client_ip import client_ip
from tests.gate import PASSWORD, apply_shared_gate

TUNNEL = "172.18.0.9"
CLIENT = "203.0.113.7"


def _request(**meta: str):
    return RequestFactory().get("/", REMOTE_ADDR=TUNNEL, **meta)


# -- the helper ----------------------------------------------------------------


def test_with_trust_on_the_forwarded_address_is_used(settings):
    settings.TRUST_CF_CONNECTING_IP = True
    assert client_ip(_request(HTTP_CF_CONNECTING_IP=CLIENT)) == CLIENT


def test_with_trust_off_the_forwarded_address_is_ignored(settings):
    settings.TRUST_CF_CONNECTING_IP = False
    assert client_ip(_request(HTTP_CF_CONNECTING_IP=CLIENT)) == TUNNEL


@pytest.mark.parametrize(
    "header",
    ["", "not-an-address", f"{CLIENT}, 198.51.100.1", "203.0.113.999", "example.org", "::g"],
)
def test_a_malformed_forwarded_address_is_ignored(settings, header):
    settings.TRUST_CF_CONNECTING_IP = True
    assert client_ip(_request(HTTP_CF_CONNECTING_IP=header)) == TUNNEL


def test_x_forwarded_for_is_never_read(settings):
    settings.TRUST_CF_CONNECTING_IP = True
    assert client_ip(_request(HTTP_X_FORWARDED_FOR=CLIENT)) == TUNNEL


def test_an_ipv6_address_is_normalised(settings):
    settings.TRUST_CF_CONNECTING_IP = True
    assert client_ip(_request(HTTP_CF_CONNECTING_IP=" 2001:DB8::0:1 ")) == "2001:db8::1"


def test_no_valid_address_at_all_is_none(settings):
    settings.TRUST_CF_CONNECTING_IP = True
    request = RequestFactory().get("/", REMOTE_ADDR="unknown", HTTP_CF_CONNECTING_IP="nope")
    assert client_ip(request) is None


def test_the_trust_follows_the_tls_proxy_setting():
    """One switch for "a proxy stands in front": the forwarded scheme and address.

    Read from the source because the test settings pin the value itself.
    """
    source = (Path(django_settings.BASE_DIR) / "config" / "settings.py").read_text("utf-8")
    assert re.search(
        r'^BEHIND_TLS_PROXY = env_bool\("DJANGO_BEHIND_TLS_PROXY", default=False\)$',
        source,
        re.MULTILINE,
    )
    assert re.search(r"^TRUST_CF_CONNECTING_IP = BEHIND_TLS_PROXY$", source, re.MULTILINE)


# -- the audit row and the throttle agree -----------------------------------------


def _hmac(address: str) -> str:
    digest = hmac.new(
        django_settings.SECRET_KEY.encode("utf-8"), address.encode("utf-8"), hashlib.sha256
    )
    return digest.hexdigest()[:64]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("trust", "header", "expected"),
    [
        (True, CLIENT, CLIENT),
        (False, CLIENT, "127.0.0.1"),
        (True, "not-an-address", "127.0.0.1"),
    ],
)
def test_the_gate_throttle_and_the_audit_row_name_the_same_client(
    client, settings, trust, header, expected
):
    apply_shared_gate(settings)
    settings.TRUST_CF_CONNECTING_IP = trust

    response = client.post(
        reverse("accounts:shared_gate"), {"password": "vale"}, HTTP_CF_CONNECTING_IP=header
    )
    assert response.status_code == 400

    row = SecurityAuditEvent.objects.get(event_type=SecurityEventType.AUTHENTICATION_FAILED)
    assert row.ip_address == expected
    throttle = SharedGateThrottle.objects.get()
    assert throttle.client_key == _hmac(expected)
    assert throttle.client_key == shared_gate.key_for_address(row.ip_address)


@pytest.mark.django_db
@pytest.mark.parametrize(("trust", "expected"), [(True, CLIENT), (False, "127.0.0.1")])
def test_passing_the_gate_records_the_client_not_the_tunnel(client, settings, trust, expected):
    apply_shared_gate(settings)
    settings.TRUST_CF_CONNECTING_IP = trust

    client.post(
        reverse("accounts:shared_gate"), {"password": PASSWORD}, HTTP_CF_CONNECTING_IP=CLIENT
    )

    row = SecurityAuditEvent.objects.get(event_type=SecurityEventType.SHARED_GATE_PASSED)
    assert row.ip_address == expected


@pytest.mark.parametrize(("trust", "expected"), [(True, CLIENT), (False, TUNNEL)])
def test_the_personal_sign_in_throttle_counts_the_same_address(settings, trust, expected):
    settings.TRUST_CF_CONNECTING_IP = trust
    assert local_throttle.network_of(_request(HTTP_CF_CONNECTING_IP=CLIENT)) == expected


# -- one rule, everywhere ----------------------------------------------------------

#: Request keys that name a client address, as string literals: a read of
#: ``request.META``. Only the helper does that; prose may still name them.
ADDRESS_KEYS = re.compile(r"""["'](REMOTE_ADDR|HTTP_CF_CONNECTING_IP|HTTP_X_FORWARDED_FOR)["']""")


def test_no_application_code_reads_an_address_except_the_helper():
    app_root = Path(django_settings.BASE_DIR) / "app"
    readers = sorted(
        path.relative_to(app_root).as_posix()
        for path in app_root.rglob("*.py")
        if "tests" not in path.parts and ADDRESS_KEYS.search(path.read_text("utf-8"))
    )
    assert readers == ["core/client_ip.py"]
