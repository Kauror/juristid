"""Personal passwords against NIST SP 800-63B-4 and OWASP (docs/adr/0145 §6).

Length is the rule and composition is not; the blocklist refuses what is known;
nothing is truncated, stripped or expired; and the hash is Argon2id.
"""

from __future__ import annotations

import gzip
import unicodedata
from pathlib import Path

import pytest
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from app.accounts import passwords
from tests import factories

pytestmark = pytest.mark.django_db


def _codes(password, user=None) -> set[str]:
    try:
        validate_password(password, user=user)
    except ValidationError as error:
        return {e.code for e in error.error_list}
    return set()


def test_the_hash_is_argon2id():
    user = factories.UserFactory()
    user.set_password("ükski reegel ei nõua siin suurtähti")

    assert user.password.startswith("argon2$argon2id$")


def test_fifteen_characters_is_the_floor():
    assert "password_too_short" in _codes("lühike parool1")  # 14
    assert _codes("seitseteist märki") == set()  # 17, nothing else asked


def test_long_passphrases_are_welcome_and_never_truncated():
    user = factories.UserFactory()
    base = "pikk lause, mis jätkub ja jätkub " * 6
    first, second = base + "A", base + "B"
    assert len(first) > 190
    assert _codes(first, user) == set()

    user.set_password(passwords.normalise(first))
    assert user.check_password(passwords.normalise(first))
    assert not user.check_password(passwords.normalise(second))


def test_over_the_maximum_is_an_error_not_a_cut():
    assert "password_too_long" in _codes("x" * 100 + " " + "y" * 200)


def test_no_composition_rule_exists():
    for accepted in ("kõik väikesed tähed ja tühikud", "ALLESTRÄHT JA MITTE MIDAGI MUUD"):
        assert _codes(accepted) == set(), accepted


def test_a_common_password_is_refused():
    from django.contrib.auth.password_validation import CommonPasswordValidator

    common = sorted(
        (word for word in CommonPasswordValidator().passwords if len(word) >= 15), key=len
    )
    assert common, "Django's list holds long entries"
    assert "password_too_common" in _codes(common[0])


def test_a_configured_breach_list_is_refused(tmp_path, settings):
    listing = tmp_path / "breached.txt.gz"
    with gzip.open(listing, "wt", encoding="utf-8") as handle:
        handle.write("see lekkis kunagi ammu\nveel üks lekkinud parool\n")
    settings.LOCAL_AUTH_BREACHED_PASSWORDS_PATH = str(listing)
    passwords._breached_passwords.cache_clear()

    assert "password_breached" in _codes("See Lekkis Kunagi Ammu")
    assert _codes("see ei lekkinud mitte kunagi") == set()


def test_the_products_own_name_is_refused():
    assert "password_too_contextual" in _codes("Juristid2026!!!!")
    assert "password_too_contextual" in _codes("kaubanduskoda123")


def test_the_persons_own_name_and_address_are_refused():
    user = factories.UserFactory(display_name="Mari Maasikas", upn="mari.maasikas@koda.ee")

    assert "password_too_contextual" in _codes("MariMaasikas2026", user)
    assert _codes("päike paistab üle mere ja mari kasvab", user) == set()


def test_a_sentence_that_merely_mentions_koda_is_fine():
    assert _codes("ma töötan kojas ja koda on suur maja") == set()


@pytest.mark.parametrize(
    "repetitive",
    ["aaaaaaaaaaaaaaaa", "abcabcabcabcabcabc", "123456789012345678", "qwertyuiopasdfghj"],
)
def test_repetition_and_sequences_are_refused(repetitive):
    assert _codes(repetitive) & {
        "password_repetitive",
        "password_sequential",
        "password_too_common",
    }


def test_a_blank_password_is_refused():
    assert "password_blank" in _codes(" " * 20)


def test_two_spellings_of_one_letter_are_one_password():
    composed = "õhtu tuleb vaikselt üle järve"
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed
    user = factories.UserFactory()

    user.set_password(passwords.normalise(decomposed))

    assert user.check_password(passwords.normalise(composed))


def test_the_hash_cost_comes_from_settings(settings):
    settings.LOCAL_AUTH_ARGON2_MEMORY_KIB = 2048
    settings.LOCAL_AUTH_ARGON2_TIME_COST = 3
    user = factories.UserFactory()
    user.set_password("parameetrid tulevad seadistusest")

    assert "m=2048,t=3,p=1" in user.password


def test_raising_the_cost_rehashes_at_the_next_check(settings):
    user = factories.UserFactory()
    user.set_password("vana kulu parool on siin")
    user.save()
    settings.LOCAL_AUTH_ARGON2_MEMORY_KIB = 4096

    assert user.check_password("vana kulu parool on siin")
    user.refresh_from_db()
    assert "m=4096" in user.password


def test_no_scheduled_expiry_exists():
    """There is no setting, column or check that ages a password out."""
    source = (Path(__file__).resolve().parents[1] / "app" / "accounts").rglob("*.py")
    for path in source:
        text = path.read_text(encoding="utf-8")
        assert "PASSWORD_MAX_AGE" not in text and "password_expires" not in text, path
