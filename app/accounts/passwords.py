"""Personal passwords: how they are hashed, normalised and judged.

Written against NIST SP 800-63B-4 §3.1.1 and the OWASP password-storage and
authentication cheat sheets, and built from the framework's reviewed parts
rather than anything of our own (docs/adr/0145 §6):

* **Hashing is Argon2id**, through Django's own `Argon2PasswordHasher` and the
  `argon2-cffi` library — salted, adaptive, memory-hard. The cost parameters
  come from settings, so a test process can run cheaply while a deployment
  cannot run weaker than the floor `juristid.E031` enforces.
* **Length is the rule, composition is not.** At least 15 characters for a
  password that is the only factor, up to 256, any characters at all. No "one
  capital, one digit, one symbol": those rules push people towards
  `Password1!`, which is exactly what a blocklist exists to stop.
* **Nothing is truncated or stripped.** A password is compared as typed,
  after Unicode normalisation (NFKC), so the same passphrase typed on two
  keyboards that compose `õ` differently is still the same passphrase. One
  character over the limit is an error message, never a silent cut.
* **A blocklist, not a strength meter.** Django's list of 20,000 common
  passwords, an optional operator-supplied list of known-breached ones, the
  application's own vocabulary and the person's own name and address.
* **No expiry.** A password changes when it is known or suspected to be
  compromised, not because a calendar said so.
"""

from __future__ import annotations

import functools
import gzip
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any

from django.conf import settings
from django.contrib.auth.hashers import Argon2PasswordHasher
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

#: NIST's floor for a password used as the only authenticator (SP 800-63B-4
#: §3.1.1.2). Settings may raise it; `juristid.E030` refuses to start a
#: local-password deployment that lowers it.
MINIMUM_LENGTH = 15

#: NIST: verifiers SHOULD permit at least 64. Generous beyond that — a
#: passphrase manager's output is welcome — and bounded, so a megabyte posted
#: into the field is an error rather than work.
MAXIMUM_LENGTH = 256


class JuristidArgon2PasswordHasher(Argon2PasswordHasher):
    """Django's Argon2id hasher with its cost read from settings.

    Same algorithm name (`argon2`), same encoded format, same library. The only
    difference is that `time_cost`, `memory_cost` and `parallelism` come from
    `LOCAL_AUTH_ARGON2_*`, because the right numbers are a property of the
    machine and the threat rather than of this file. A hash records its own
    parameters, so changing them later re-hashes on the next successful
    sign-in (`must_update`) and never invalidates an existing password.
    """

    @property
    def time_cost(self) -> int:  # type: ignore[override]
        return int(getattr(settings, "LOCAL_AUTH_ARGON2_TIME_COST", 2))

    @property
    def memory_cost(self) -> int:  # type: ignore[override]
        return int(getattr(settings, "LOCAL_AUTH_ARGON2_MEMORY_KIB", 65536))

    @property
    def parallelism(self) -> int:  # type: ignore[override]
        return int(getattr(settings, "LOCAL_AUTH_ARGON2_PARALLELISM", 1))


def normalise(raw: str) -> str:
    """The form a password is hashed and compared in: NFKC, nothing else.

    Not stripped, not case-folded, not truncated. NFKC so that two input
    methods producing the same visible passphrase produce the same bytes
    (SP 800-63B-4 §3.1.1.2).
    """
    return unicodedata.normalize("NFKC", raw or "")


class LengthValidator:
    """At least `LOCAL_AUTH_PASSWORD_MIN_LENGTH`, at most `MAXIMUM_LENGTH`.

    Counted in characters after normalisation — what a person typed, not the
    bytes it encodes to.
    """

    def validate(self, password: str, user: Any = None) -> None:
        length = len(normalise(password))
        minimum = minimum_length()
        if length < minimum:
            raise ValidationError(
                f"Parool peab olema vähemalt {minimum} märki pikk. Pikk lause sobib hästi.",
                code="password_too_short",
            )
        if length > MAXIMUM_LENGTH:
            raise ValidationError(
                f"Parool võib olla kuni {MAXIMUM_LENGTH} märki pikk.",
                code="password_too_long",
            )

    def get_help_text(self) -> str:
        return f"Vähemalt {minimum_length()} märki. Kõik märgid on lubatud, ka tühikud."


def minimum_length() -> int:
    return max(MINIMUM_LENGTH, int(getattr(settings, "LOCAL_AUTH_PASSWORD_MIN_LENGTH", 15)))


#: The application's own vocabulary. A password built from the name of the
#: product it unlocks is the first thing anybody guessing it would try
#: (SP 800-63B-4 §3.1.1.2, "context-specific words").
CONTEXT_WORDS = (
    "juristid",
    "õigusloome",
    "oigusloome",
    "kaubanduskoda",
    "kaubandus-tööstuskoda",
    "kaubandustööstuskoda",
    "koda",
    "eestikaubandus",
    "parool",
    "salasõna",
    "password",
)


def _comparable(text: str) -> str:
    return re.sub(r"[\W_]+", "", normalise(text).casefold())


class ContextWordsValidator:
    """Not the product's name, the person's own name, or their address.

    Refused when the password, with spacing and punctuation removed, *is* one
    of those words or is one of them with a few characters wrapped around it —
    `Juristid2026!` and `kaur.koda.ee` both. A long passphrase that merely
    *contains* a short word is fine: refusing every sentence with "koda" in it
    would be a composition rule wearing a disguise.
    """

    #: How many characters besides the word itself make a password "built from"
    #: it rather than a phrase that happens to include it.
    SLACK = 6

    def validate(self, password: str, user: Any = None) -> None:
        candidate = _comparable(password)
        for word in self._words(user):
            if len(word) >= 3 and word in candidate and len(candidate) - len(word) <= self.SLACK:
                raise ValidationError(
                    "Parool on liiga lähedane sinu nimele, aadressile või rakenduse nimele.",
                    code="password_too_contextual",
                )

    def _words(self, user: Any) -> list[str]:
        words = [_comparable(word) for word in CONTEXT_WORDS]
        if user is not None:
            for attribute in ("display_name", "upn", "email"):
                value = str(getattr(user, attribute, "") or "")
                words.append(_comparable(value))
                words.extend(_comparable(part) for part in re.split(r"[\s@.\-_]+", value))
        return [word for word in words if word]

    def get_help_text(self) -> str:
        return "Ära kasuta oma nime, e-posti aadressi ega rakenduse nime."


class RepetitionValidator:
    """Not one character, or one short run, repeated: `aaaaaaaaaaaaaaa`, `abcabcabc…`.

    Also refuses an ascending or descending run of the keyboard or the
    alphabet long enough to be most of the password (`123456789012345`,
    `qwertyuiopasdfg`).
    """

    #: Read cyclically, so `…7890123…` is one run and not two.
    SEQUENCES = (
        "abcdefghijklmnopqrstuvwxyz",
        "abcdefghijklmnopqrsšzžtuvwõäöüxy",
        "0123456789",
        "qwertyuiopasdfghjklzxcvbnm",
        "qwertyuiopüõasdfghjklöäzxcvbnm",
    )

    def validate(self, password: str, user: Any = None) -> None:
        text = normalise(password).casefold()
        if not text:
            return
        for size in range(1, 5):
            unit = text[:size]
            if unit and (unit * (len(text) // size + 1))[: len(text)] == text:
                raise ValidationError(
                    "Parool ei tohi olla üks ja sama märk või lühike kordus.",
                    code="password_repetitive",
                )
        compact = re.sub(r"\s+", "", text)
        for sequence in self.SEQUENCES:
            for source in (sequence * 3, sequence[::-1] * 3):
                if len(compact) >= 8 and _longest_common_run(compact, source) >= len(compact) - 2:
                    raise ValidationError(
                        "Parool ei tohi olla klaviatuuri või tähestiku järjestus.",
                        code="password_sequential",
                    )

    def get_help_text(self) -> str:
        return "Ära kasuta kordusi ega järjestusi nagu 123456 või qwerty."


def _longest_common_run(text: str, source: str) -> int:
    """The longest stretch of ``text`` that appears in ``source`` as it is.

    Grows each candidate only while it still matches — a stretch that is not in
    the source cannot become one by getting longer — so a 256-character
    password costs a few thousand substring tests, not tens of thousands.
    """
    best = 0
    for start in range(len(text)):
        end = start + best + 1
        while end <= len(text) and text[start:end] in source:
            best = end - start
            end += 1
    return best


class BreachedPasswordValidator:
    """Not a password known from a breach corpus.

    The blocklist strategy the brief asked for, in two layers and with no
    network call: Django's own list of 20,000 common passwords (the
    `CommonPasswordValidator` beside this in settings), and — where the
    operator supplies one — a larger list of known-compromised passwords at
    `LOCAL_AUTH_BREACHED_PASSWORDS_PATH` (one per line, optionally gzipped).
    Checking against an online breach service would send something derived
    from a password off the host on every change, which is a decision for the
    activation, not a default (docs/LOCAL_AUTH_ACTIVATION_RUNBOOK.md).
    """

    def validate(self, password: str, user: Any = None) -> None:
        if normalise(password).casefold() in _breached_passwords(
            str(getattr(settings, "LOCAL_AUTH_BREACHED_PASSWORDS_PATH", "") or "")
        ):
            raise ValidationError(
                "See parool on teadaolevalt lekkinud. Vali mõni muu.",
                code="password_breached",
            )

    def get_help_text(self) -> str:
        return "Lekkinud paroolide nimekirjas olevaid paroole ei saa kasutada."


@functools.lru_cache(maxsize=2)
def _breached_passwords(path: str) -> frozenset[str]:
    if not path:
        return frozenset()
    source = Path(path)
    try:
        opener = gzip.open if source.suffix == ".gz" else open
        with opener(source, "rt", encoding="utf-8", errors="ignore") as handle:
            return frozenset(normalise(line.strip()).casefold() for line in handle if line.strip())
    except OSError:
        # Logged without the path's contents, and refusing nothing extra: the
        # configured list is defence in depth on top of Django's, and a missing
        # file is a deployment fault `juristid.W032` reports at start-up.
        logger.warning("The configured breached-password list could not be read.")
        return frozenset()


class EmptyNotAllowedValidator:
    """A password made only of whitespace is no password."""

    def validate(self, password: str, user: Any = None) -> None:
        if not normalise(password).strip():
            raise ValidationError("Parool ei saa olla tühi.", code="password_blank")

    def get_help_text(self) -> str:
        return ""
