"""The MCP authenticator's secret uses the MFA key under its own purpose (docs/adr/0185 §3).

`app.accounts.mfa._fernet` gained a ``purpose`` parameter. Its default must be
exactly the key the personal sign-in has always used — a device enrolled
before the change still opens — and the MCP purpose must be a different key, so
neither secret can stand in for the other.
"""

from __future__ import annotations

import base64

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.accounts import mfa
from app.mcp_access import credentials


def _the_key_as_it_always_was(settings) -> Fernet:
    material = (settings.LOCAL_AUTH_MFA_ENCRYPTION_KEY or settings.SECRET_KEY).encode("utf-8")
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"juristid-local-auth",
        info=b"totp-secret-encryption-v1",
    ).derive(material)
    return Fernet(base64.urlsafe_b64encode(derived))


@pytest.mark.parametrize("key", ["", "a-dedicated-mfa-key-for-this-test-only-0123456789"])
def test_the_personal_sign_in_key_is_unchanged(settings, key):
    settings.LOCAL_AUTH_MFA_ENCRYPTION_KEY = key
    before = _the_key_as_it_always_was(settings)
    sealed_before = before.encrypt(b"JBSWY3DPEHPK3PXP").decode("ascii")

    assert mfa._decrypt(sealed_before) == "JBSWY3DPEHPK3PXP"
    assert before.decrypt(mfa._encrypt("JBSWY3DPEHPK3PXP").encode("ascii")) == b"JBSWY3DPEHPK3PXP"


def test_an_mcp_secret_does_not_open_as_a_personal_sign_in_one_or_back():
    sealed_for_mcp = credentials.seal("JBSWY3DPEHPK3PXP")
    sealed_for_sign_in = mfa._encrypt("JBSWY3DPEHPK3PXP")

    assert credentials.unseal(sealed_for_mcp) == "JBSWY3DPEHPK3PXP"
    assert mfa._decrypt(sealed_for_mcp) is None
    assert credentials.unseal(sealed_for_sign_in) is None


def test_the_public_seal_refuses_the_personal_sign_in_purpose():
    with pytest.raises(ValueError, match="other than the personal sign-in"):
        mfa.seal("JBSWY3DPEHPK3PXP", purpose=mfa.LOCAL_AUTH_PURPOSE)
    with pytest.raises(ValueError, match="other than the personal sign-in"):
        mfa.unseal("x", purpose=mfa.LOCAL_AUTH_PURPOSE)


def test_the_public_step_matcher_is_the_private_one():
    import pyotp

    secret = pyotp.random_base32()
    code = pyotp.TOTP(secret).now()
    assert mfa.matching_step(secret, code, after_step=0) == mfa._matching_step(
        secret, code, after_step=0
    )
    assert mfa.matching_step(secret, "١٢٣٤٥٦", after_step=0) is None
