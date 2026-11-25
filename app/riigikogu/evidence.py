"""Official evidence before inference: the lead committee's list of amendments (ADR 0227 §5).

For the second reading the lead committee publishes «Muudatusettepanekute
loetelu» (``…_MEde_loetelu.docx`` in the bill's texts, read 24.11.2026 on 892 SE).
Each numbered proposal ends with who made it and the committee's decision, e.g.::

    1.2. Jätta välja eelnõu § 3 …
    Selgitus: …
    Majanduskomisjon
    Juhtivkomisjon: ARVESTADA TÄIELIKULT

That is the Riigikogu's own record of which proposal was taken into account, by
whom it was made and why — direct evidence a model could only approximate. This
module reads it deterministically. The decision vocabulary is kept as written
(«ARVESTADA TÄIELIKULT», «ARVESTADA OSALISELT», «ARVESTADA SISULISELT»,
«JÄTTA ARVESTAMATA»); the exact provisions of the Riigikogu Rules of Procedure
behind it could not be read programmatically on 24.11.2026 and are cited in the
ADR as to be verified.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.riigikogu.organisation import names_koda

_DECISION = re.compile(r"juhtivkomisjon\s*:\s*([A-ZÕÄÖÜ ]{6,40})", re.IGNORECASE)
_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)*)\.\s", re.MULTILINE)

DECISIONS = {
    "ARVESTADA TÄIELIKULT": "FULL",
    "ARVESTADA OSALISELT": "PARTIAL",
    "ARVESTADA SISULISELT": "SUBSTANCE",
    "JÄTTA ARVESTAMATA": "REJECTED",
}


@dataclass(frozen=True)
class Entry:
    number: str
    proposer: str
    decision: str
    decision_text: str
    text: str

    @property
    def from_koda(self) -> bool:
        return names_koda(self.proposer)


def parse(text: str) -> list[Entry]:
    """Every proposal of a list of amendments, with its proposer and decision."""
    entries: list[Entry] = []
    start = 0
    for match in _DECISION.finditer(text or ""):
        block = text[start : match.start()]
        start = match.end()
        lines = [line.strip() for line in block.strip().splitlines() if line.strip()]
        if not lines:
            continue
        proposer = lines[-1]
        numbers = _NUMBER.findall(block)
        raw = " ".join(match.group(1).split()).upper()
        decision = next(
            (code for words, code in DECISIONS.items() if raw.startswith(words)), "OTHER"
        )
        entries.append(
            Entry(
                number=numbers[0] if numbers else "",
                proposer=proposer[:300],
                decision=decision,
                decision_text=raw,
                text="\n".join(lines[:-1])[:4000],
            )
        )
    return entries


def koda_entries(text: str) -> list[Entry]:
    """The proposals the list attributes to koda (alone or among others)."""
    return [entry for entry in parse(text) if entry.from_koda]
