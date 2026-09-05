"""Deterministic text signals for related-material suggestions.

Everything here is pure: strings in, strings out, no database, no network and
no model. It exists so that the *explanation* a lawyer reads — «Sama õigusakt:
pakendiseadus» — is produced by code a test can hold in one hand, rather than
by whatever PostgreSQL's stemmer happens to do with a genitive.

Two ideas carry the module.

**Legal titles are mostly scaffolding.** «Jäätmeseaduse muutmise seaduse
eelnõu» has one word that says what the file is about. `eelnõu`, `muutmise`,
`seaduse`, `seletuskiri` and `kooskõlastamine` appear on hundreds of register
rows and mean nothing about the subject, so they never count as shared content
(`GENERIC_WORDS`) and a bare `seadus` is never an act (docs/adr/0061 §4).

**A named act is the strongest thing two titles can share.** Estonian writes
most acts as one compound — `pakendiseadus`, `äriseadustik` — and some as a
genitive attribute plus the head noun — `töölepingu seadus`, `riigihangete
seadus`. Both shapes are recognised conservatively: a compound needs a stem in
front of `seadus`/`seadustik`, and a two-word act needs an attribute that is not
boilerplate. Nothing is looked up in a catalogue of acts, because a catalogue
is wrong the first time somebody files a Matter about an act it does not list.

Stemming is deliberately crude. `stem()` strips a short list of case endings so
`pakendijäätmete` and `pakendijäätmed` meet in the middle; two stems are the
same word when one is a prefix of the other and both are long enough to mean
something (`same_word`). It misses the consonant gradation of `keskkond` /
`keskkonna`, and that is the right direction to miss in: a signal not counted
is a suggestion not made, and a suggestion not made costs nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.text import normalize_for_matching

#: Letters an Estonian word is made of. Digits are deliberately absent: a year
#: or a reference number is an identifier, and identifiers are matched exactly
#: elsewhere or not at all.
_WORD = re.compile(r"[A-Za-zÕÄÖÜŠŽõäöüšž][A-Za-zÕÄÖÜŠŽõäöüšž\-]*")

#: The shortest token that can be a subject word. `ELi`, `ja`, `nr` and the
#: like are below it; so is `Koda`, which is on every second title.
MIN_TERM_LENGTH = 5

#: The shortest stem two words may agree on. Below this, `ette` matches
#: `ettevõtja` and every title in the register is similar to every other.
MIN_STEM_MATCH = 6

#: Words that describe the *kind* of document or the *stage* of a procedure and
#: say nothing about its subject. Folded at import through the same function
#: the tokens go through, so the list and the comparison cannot drift apart —
#: the lesson docs/adr/0055 records about a stopword written with a diacritic
#: the fold then removed.
GENERIC_WORDS: frozenset[str] = frozenset(
    normalize_for_matching(word)
    for word in (
        # the head noun of every act, in every case
        "seadus",
        "seaduse",
        "seadust",
        "seaduses",
        "seadusest",
        "seadusele",
        "seadusega",
        "seaduseks",
        "seadused",
        "seaduste",
        "seadusi",
        "seadustik",
        "seadustiku",
        "seadustikku",
        "seadusandlus",
        "seadusandluse",
        "seaduseelnõu",
        # the shape of the procedure
        "eelnõu",
        "eelnõud",
        "eelnõude",
        "eelnõule",
        "eelnõus",
        "eelnõuga",
        "muutmine",
        "muutmise",
        "muutmiseks",
        "muutmist",
        "muudatus",
        "muudatused",
        "muudatuste",
        "muudatusi",
        "muudatusettepanek",
        "muudatusettepanekud",
        "muudatusettepanekute",
        "seletuskiri",
        "seletuskirja",
        "seletuskirjas",
        "kooskõlastamine",
        "kooskõlastamiseks",
        "kooskõlastamisele",
        "kooskõlastamist",
        "kooskõlastusring",
        "kooskõlastusringile",
        "kooskõlastus",
        "arvamus",
        "arvamuse",
        "arvamust",
        "arvamused",
        "arvamuste",
        "küsimine",
        "küsimiseks",
        "avaldamine",
        "avaldamiseks",
        "esitamine",
        "esitamiseks",
        "väljatöötamiskavatsus",
        "väljatöötamiskavatsuse",
        "väljatöötamine",
        "väljatöötamise",
        "kavatsus",
        "kavatsuse",
        "ettepanek",
        "ettepanekud",
        "ettepanekute",
        "ettepanekuid",
        "rakendamine",
        "rakendamise",
        "rakendamiseks",
        "kehtestamine",
        "kehtestamise",
        "kinnitamine",
        "kinnitamise",
        "tunnistamine",
        "tunnistamise",
        "kehtetuks",
        "jõustumine",
        "jõustumise",
        "ülevõtmine",
        "ülevõtmise",
        "täiendamine",
        "täiendamise",
        "menetlus",
        "menetluse",
        "menetlemine",
        "menetlemise",
        "määrus",
        "määruse",
        "määrust",
        "määruste",
        "määrused",
        "otsus",
        "otsuse",
        "eelnõuga",
        "teema",
        "teemad",
        # connective and generic
        "teiste",
        "teise",
        "sellega",
        "seonduvalt",
        "seonduvate",
        "seotud",
        "kohta",
        "ning",
        "samuti",
        "muude",
        "üldine",
        "üldise",
        "riiklik",
        "riikliku",
        "eesti",
        "vabariigi",
        "valitsuse",
        "riigikogu",
        "ministeerium",
        "ministeeriumi",
        "ministeeriumile",
        "ministeeriumis",
        "kaubandus-tööstuskoda",
        "kaubandus",
        "tööstuskoda",
        "tööstuskoja",
        "tavaline",
        "avatud",
        "nähtav",
        "kõigile",
    )
)

#: A word that cannot be the attribute of a two-word act. `muutmise seadus` is
#: the tail of «X seaduse muutmise seadus», not an act called *muutmine*.
_NOT_AN_ACT_ATTRIBUTE: frozenset[str] = frozenset(
    normalize_for_matching(word)
    for word in (
        "muutmise",
        "muutmiseks",
        "muutmine",
        "täiendamise",
        "kehtetuks",
        "tunnistamise",
        "rakendamise",
        "kohaldamise",
        "jõustumise",
        "selle",
        "sellise",
        "selliste",
        "nende",
        "uue",
        "uus",
        "uute",
        "kehtiva",
        "kehtiv",
        "vastava",
        "vastavate",
        "nimetatud",
        "antud",
        "kõnealuse",
        "käesoleva",
        "eelnõu",
        "eelnõude",
        "seaduse",
        "seadus",
        "seaduste",
        "teiste",
        "mitme",
        "ühe",
        "iga",
        "kogu",
        "samuti",
        "ehk",
        "kui",
        "poolt",
        "järgi",
        "alusel",
        "ette",
        "nähtud",
        "kohta",
        "ning",
        "või",
        "ja",
        "eesti",
        "vabariigi",
        "riigikogu",
        "koja",
        "koda",
    )
)

#: Case endings, longest first. Stripped once, and only when enough of the word
#: is left to still be the word (`MIN_TERM_LENGTH`).
_SUFFIXES: tuple[str, ...] = tuple(
    sorted(
        {
            "tesse",
            "desse",
            "test",
            "dest",
            "tele",
            "dele",
            "tega",
            "dega",
            "teks",
            "deks",
            "teni",
            "deni",
            "tena",
            "dena",
            "teta",
            "deta",
            "sse",
            "ste",
            "est",
            "elt",
            "ele",
            "ega",
            "eks",
            "eni",
            "ena",
            "eta",
            "ist",
            "ilt",
            "ile",
            "iga",
            "iks",
            "ini",
            "ina",
            "ita",
            "ust",
            "ult",
            "ule",
            "uga",
            "uks",
            "uni",
            "una",
            "uta",
            "ast",
            "alt",
            "ale",
            "aga",
            "aks",
            "ani",
            "ana",
            "ata",
            "st",
            "lt",
            "le",
            "ga",
            "ks",
            "ni",
            "na",
            "ta",
            "te",
            "de",
            "se",
            "id",
            "ud",
            "ad",
            "ed",
            "e",
            "i",
            "u",
            "a",
            "s",
            "t",
            "d",
            "l",
        },
        key=lambda suffix: (-len(suffix), suffix),
    )
)

_ACT_HEADS: tuple[str, ...] = ("seadustik", "seadus")
_COMPOUND_ACT = re.compile(
    r"^(?P<stem>[a-zõäöüšž][a-zõäöüšž\-]{2,})(?P<head>seadustik|seadus)(?P<suffix>[a-zõäöü]{0,5})$"
)
_BARE_ACT = re.compile(r"^(?P<head>seadustik|seadus)(?P<suffix>[a-zõäöü]{0,5})$")
_VOWELS = frozenset("aeiouõäöü")
#: How many attributes a multi-word act may carry in front of its head noun:
#: «kohaliku omavalitsuse korralduse seadus» keeps two of its three.
_MAX_ACT_ATTRIBUTES = 2


def tokens(text: str) -> list[str]:
    """The words of ``text``, lower-cased, in order, hyphens kept."""
    if not text:
        return []
    return [match.group(0).lower() for match in _WORD.finditer(text)]


def strip_suffix(word: str) -> str:
    """One case ending off a lower-cased word, when enough word remains."""
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= MIN_TERM_LENGTH:
            return word[: -len(suffix)]
    return word


def stem(word: str) -> str:
    """The folded, suffix-stripped comparison key of one word."""
    return normalize_for_matching(strip_suffix(word.lower()))


def same_word(a: str, b: str) -> bool:
    """Whether two stems are inflections of one word.

    Prefix agreement rather than equality, because a crude stemmer leaves
    `pakendijäätm` and `pakendijäätme` for two forms of the same noun. Both
    sides have to be long enough that the agreement means something.
    """
    if not a or not b:
        return False
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return len(shorter) >= MIN_STEM_MATCH and longer.startswith(shorter)


def is_generic(word: str) -> bool:
    """Whether a word is document or procedure boilerplate."""
    return normalize_for_matching(word) in GENERIC_WORDS


@dataclass(frozen=True)
class Term:
    """One subject word of a title, in the three forms the engine needs.

    ``display`` is what the reason line prints, ``prefix`` is what the simple
    text-search vector is asked for (lower-cased, diacritics kept, one case
    ending removed, so it matches the other inflections as a prefix), and
    ``key`` is what two terms are compared by.
    """

    display: str
    prefix: str
    key: str


def subject_terms(*texts: str, limit: int = 8) -> list[Term]:
    """The words of ``texts`` that say what the thing is about.

    Boilerplate, short words and repeated inflections of one word are dropped;
    the longest words come first because in Estonian legal titles the longest
    word is usually the compound that names the subject. Bounded, because these
    become one query annotation each.
    """
    found: list[Term] = []
    for text in texts:
        for word in tokens(text):
            plain = word.strip("-")
            if len(plain) < MIN_TERM_LENGTH or is_generic(plain):
                continue
            key = stem(plain)
            if any(same_word(key, existing.key) for existing in found):
                continue
            found.append(Term(display=plain, prefix=strip_suffix(plain), key=key))
    found.sort(key=lambda term: (-len(term.display), term.display))
    return found[:limit]


@dataclass(frozen=True)
class LegalInstrument:
    """A named act, as it was written and as it is compared."""

    display: str
    key: str


def legal_instruments(text: str) -> list[LegalInstrument]:
    """The acts a title names, in order of appearance, without repeats.

    A compound (`pakendiseaduse`) yields its nominative (`pakendiseadus`). A
    head noun on its own (`seaduse`) is an act only when the word in front of
    it is a genitive attribute that is not boilerplate — so «töölepingu seaduse
    eelnõu» yields `töölepingu seadus` and «X seaduse muutmise seadus» yields
    nothing for its second half.
    """
    words = tokens(text)
    found: list[LegalInstrument] = []
    seen: set[str] = set()

    def keep(display: str) -> None:
        key = normalize_for_matching(display)
        if key not in seen:
            seen.add(key)
            found.append(LegalInstrument(display=display, key=key))

    for index, word in enumerate(words):
        compound = _COMPOUND_ACT.match(word)
        if compound:
            keep(f"{compound.group('stem')}{compound.group('head')}")
            continue
        bare = _BARE_ACT.match(word)
        if not bare:
            continue
        attributes: list[str] = []
        for back in range(1, _MAX_ACT_ATTRIBUTES + 1):
            position = index - back
            if position < 0:
                break
            attribute = words[position]
            if not _is_act_attribute(attribute):
                break
            attributes.insert(0, attribute)
        if attributes:
            keep(" ".join([*attributes, bare.group("head")]))
    return found


def _is_act_attribute(word: str) -> bool:
    """Whether a word can be the genitive attribute of a two-word act."""
    plain = word.strip("-")
    if len(plain) < 4 or not plain.isalpha():
        return False
    if normalize_for_matching(plain) in _NOT_AN_ACT_ATTRIBUTE:
        return False
    if _COMPOUND_ACT.match(plain) or _BARE_ACT.match(plain):
        return False
    # An Estonian genitive ends in a vowel. `arvamus`, `eelnõud` and `Koda`
    # (excluded above) do not attribute the noun after them.
    return plain[-1] in _VOWELS


def shared_instruments(
    ours: list[LegalInstrument], theirs: list[LegalInstrument]
) -> list[LegalInstrument]:
    """The acts both sides name, in our spelling, in our order."""
    their_keys = {instrument.key for instrument in theirs}
    return [instrument for instrument in ours if instrument.key in their_keys]


def matching_terms(terms: list[Term], text: str) -> list[Term]:
    """Which of ``terms`` occur, in any inflection, in ``text``."""
    stems = [stem(word) for word in tokens(text) if len(word.strip("-")) >= MIN_TERM_LENGTH]
    return [term for term in terms if any(same_word(term.key, other) for other in stems)]


def format_list(items: list[str], limit: int = 3) -> str:
    """«a, b, c» — the first few, for a reason line that has to stay short."""
    return ", ".join(items[:limit])
