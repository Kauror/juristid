"""Is koda an addressee, the author, or only named? (ADR 0223 §1)

Read from the document's **metadata**: its direction and its ``author`` field,
which for a letter the Riigikogu sends lists the addressees and for a letter it
receives names the sender. Searching the register for «Tööstuskoda» finds the
candidates; only this decides. A document that merely mentions koda in its title
or text is not an invitation to koda.

The names koda appears under in the Riigikogu's own data (checked 24.11.2026 on
every document since 2009 the search finds): «Eesti Kaubandus-Tööstuskoda»,
«Eesti Kaubandus- ja Tööstuskoda», «Eesti Kaubandus- Tööstuskoda» (a stray
space), the bare «Kaubandus-Tööstuskoda», and the genitive «…Tööstuskoja». The
Eesti Põllumajandus-Kaubanduskoda is a different organisation and never matches.
"""

from __future__ import annotations

import re

from app.riigikogu.enums import DocumentKind, KodaRole

#: «Kaubandus-Tööstuskoda», «Kaubandus- ja Tööstuskoda», «Kaubandus- Tööstuskoda»,
#: «Kaubandus–Tööstuskoda» (en dash), any case, nominative or genitive and its
#: other case forms («-kojale», «-kojaga»).
_KODA = re.compile(
    r"(?<![\w-])(?:eesti\s+)?kaubandus\s*[-‐‑–]\s*(?:ja\s+)?t[öo]{1,2}stusko(?:da|ja)\w*",
    re.IGNORECASE,
)
#: The organisation that is not koda, though its name shares a word.
_NOT_KODA = re.compile(r"p[õo]llumajandus\s*[-‐‑–]\s*kaubandusko", re.IGNORECASE)
#: An addressee list is a comma- or semicolon-separated list of organisations.
_SPLIT = re.compile(r"[;,\n]")


def names_koda(text: str) -> bool:
    """Whether ``text`` names koda itself (never the Põllumajandus-Kaubanduskoda)."""
    return bool(_KODA.search(text or ""))


def koda_parties(parties: str) -> list[str]:
    """The entries of an author/addressee list that are koda."""
    found = []
    for part in _SPLIT.split(parties or ""):
        part = part.strip()
        if part and names_koda(part) and not _NOT_KODA.search(part.split(" - ")[0]):
            found.append(part)
    return found


def koda_role(*, direction: str, parties: str, title: str) -> str:
    """How koda appears in one document's metadata."""
    listed = bool(koda_parties(parties))
    if listed and direction == "VALJA":
        return KodaRole.ADDRESSEE
    if listed and direction == "SISSE":
        return KodaRole.AUTHOR
    if listed:
        # Named among the parties, but the direction is missing or unknown.
        return KodaRole.UNCERTAIN
    if names_koda(title):
        return KodaRole.MENTIONED
    return KodaRole.NONE


#: Words a committee's request for an opinion is titled with.
_INVITATION_TITLE = re.compile(
    r"^\s*(kaasamiskiri|kaasamine\b|ettepanekute\s+esitamine|seisukoha\s+küsimine)|"
    r"arvamuse\s+(küsimine|avaldamine|andmine)|^\s*kutse\b",
    re.IGNORECASE,
)
#: Sentences of the letter itself that ask for an opinion or for participation.
_INVITATION_TEXT = re.compile(
    r"(võimalus\s+avaldada|palume\s+(teie\s+)?(arvamus|seisukoh|ettepanek|osale)|"
    r"ootame\s+(teie\s+|neid\s+)?(arvamus|seisukoh|ettepanek|hiljemalt)|"
    r"palun\s+esitage|palume\s+(need\s+|neid\s+)?esitada|palume\s+esitada\s+need|"
    r"kui\s+teil\s+on\s+(soovi|ettepanekuid|arvamusi|täiendavaid|märkusi)|"
    r"soovi\s+esitada\s+eelnõu\s+kohta|võimalus\s+esitada|palume\s+teilt|"
    r"kutse\s+osaleda|soovite\s+arutelul\s+osaleda|"
    r"kutsume\s+(teid\s+)?(osalema|arutelule|istungile))",
    re.IGNORECASE,
)
#: A committee's notice that a bill is in proceedings, sent to the groups it informs.
_NOTIFICATION_TEXT = re.compile(
    r"teavitatud\s+huvirühmad|menetlemine\s+riigikogus|eelnõu\s+kaardilt", re.IGNORECASE
)


def classify(*, role: str, title: str, document_type: str, text: str = "") -> str:
    """The document's kind, from its metadata and, for a letter to koda, its own words."""
    kind = (document_type or "").casefold()
    if role == KodaRole.ADDRESSEE:
        if _INVITATION_TITLE.search(title or "") or _INVITATION_TEXT.search(text or ""):
            return DocumentKind.INVITATION
        if _NOTIFICATION_TEXT.search(title or "") or _NOTIFICATION_TEXT.search(text or ""):
            return DocumentKind.NOTIFICATION
        return DocumentKind.KODA_LETTER
    if role == KodaRole.AUTHOR:
        return DocumentKind.KODA_OPINION
    if role == KodaRole.UNCERTAIN:
        return DocumentKind.UNCERTAIN
    if "muudatusettepanek" in kind:
        return DocumentKind.AMENDMENT
    if "protokoll" in kind or (title or "").casefold().startswith("protokoll"):
        return DocumentKind.PROTOCOL
    if "arvamus" in (title or "").casefold():
        return DocumentKind.OTHER_OPINION
    return DocumentKind.OTHER
