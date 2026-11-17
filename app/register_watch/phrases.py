"""The words a Teema is searched by in its ministry's register (ADR 0217 §7).

A Teema at the ministry stage has no bill number, only its title: «Pankrotiseaduse
ja kohtutäituri seaduse muutmise seaduse eelnõu väljatöötamiskavatsus». The
register titles name the same acts in the same inflected words, so the search
is the title's first two distinctive words («Pankrotiseaduse kohtutäituri»):
the words that say which act, without the ones every draft carries. A lawyer
can replace them on the review page; the title is never read again then.

Which register is the Teema's ministry: a confirmed sender on the Teema first,
else the proposed one (ADR 0215).
"""

from __future__ import annotations

import re
from typing import Any

from app.register_watch.models import WatchSource

#: Words that say "a draft" rather than "which act" (lower case).
GENERIC = frozenset(
    """
    seadus seaduse seaduste seadustiku seadusele seadusesse muutmise muutmine muudatuste
    muudatused täiendamise eelnõu eelõu eelnõust eelnõule eelnõud väljatöötamiskavatsus
    väljatöötamiskavatsu väljatöötamiskavatsuse vtk ja ning ning teiste sellega seonduvalt
    määruse määruste määrus põhimäärus põhimääruse nr projekt esitamine kooskõlastamiseks
    ülevõtmise seoses muutmisega rakendamise rakendamine kohta osas eesti ettepanek ministri
    kooskõlastamine kooskõlastamiseks koostamine loomine tööversioon tegevuskava kava
    vabariigi valitsuse valitsus direktiivi direktiiv seotud täiendavad kehtestada
    vähendada lahendused standardid aasta jaanuari veebruari märtsi aprilli mai juuni
    juuli augusti septembri oktoobri novembri detsembri määrused euroopa liidu
    """.split()
)
#: A minister's title («Rahandusministri», «digiministri») says who signs, not what.
MINISTER = re.compile(r"ministri$", re.IGNORECASE)
WORD = re.compile(r"[0-9A-Za-zÕÄÖÜŠŽõäöüšž-]+")
MAX_WORDS = 2

#: The ministry (as an `Organisation` name) -> the register that holds its papers.
MINISTRY_SOURCE = {
    "Justiits- ja Digiministeerium": WatchSource.ADR_JDM,
    "Justiitsministeerium": WatchSource.ADR_JDM,
    "Majandus- ja Kommunikatsiooniministeerium": WatchSource.ADR_MKM,
    "Rahandusministeerium": WatchSource.ADR_RAM,
    "Sotsiaalministeerium": WatchSource.ADR_SOM,
    "Haridus- ja Teadusministeerium": WatchSource.HTM_REGISTER,
    "Kliimaministeerium": WatchSource.KLIM_REGISTER,
}


def words(title: str) -> str:
    """The first distinctive words of ``title``, or ``""`` when there are none."""
    text = re.sub(r"\([^)]*\)", " ", title or "")
    text = re.sub(r"[„“”\"«»]", " ", text)
    chosen: list[str] = []
    for word in WORD.findall(text):
        if word.endswith("-"):
            # «Justiits- ja digiministri»: the first half of a joined name.
            continue
        clean = word.strip("-")
        if len(clean) < 3 or clean.isdigit() or clean.lower() in GENERIC or MINISTER.search(clean):
            continue
        if clean.casefold() in (word.casefold() for word in chosen):
            continue
        chosen.append(clean)
        if len(chosen) == MAX_WORDS:
            break
    return " ".join(chosen)


def ministry_source(matter: Any) -> WatchSource | None:
    """The register of the Teema's ministry: confirmed sender first, else the proposed one."""
    for organisation in matter.source_organisations.all():
        if organisation.name in MINISTRY_SOURCE:
            return MINISTRY_SOURCE[organisation.name]
    # The reverse one-to-one raises a subclass of AttributeError when absent.
    suggestion = getattr(matter, "classification_suggestion", None)
    sender = suggestion.sender if suggestion is not None else None
    if sender is not None and sender.name in MINISTRY_SOURCE:
        return MINISTRY_SOURCE[sender.name]
    return None
