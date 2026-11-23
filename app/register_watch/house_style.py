"""How the app's AI writes about the Chamber (owner's house style, 2026-11-23).

Every model that writes Estonian for the register watch — a finding's line, a
new draft's reason, an agenda item's reason — gets `KODA_STYLE` in its system
prompt, and every line it returns passes `fix` before it is saved, so a slip the
prompt did not prevent still does not reach a lawyer. `manage.py
fix_koda_style` runs the same `fix` over what was saved before.

The rules are the owner's guide (docs/koda-house-style.md):

* the full name is «Eesti Kaubandus-Tööstuskoda» (one hyphen, no «ja»); the
  short forms are «kaubanduskoda» and «koda», lowercase mid-sentence and
  capitalised only at the start of a sentence or heading;
* the cases: koda, koja, kojale, kojas, kotta, kojast, kojaga.

`fix` is deliberately narrow. It never touches another organisation's name
(«Põllumajandus-Kaubanduskoda», «Looduskoostöö Koda»): a capital «Koda» is
kept after a capitalised word, and «Kaja» (a person's name) is left alone.
"""

from __future__ import annotations

import re

KODA_STYLE = """House style for writing about the Chamber in Estonian (follow exactly):
- The official name is «Eesti Kaubandus-Tööstuskoda» (one hyphen, no spaces, never \
«Kaubandus- ja Tööstuskoda»). Decline only the last part: «Eesti Kaubandus-Tööstuskoja», \
«Eesti Kaubandus-Tööstuskojale».
- Short forms: «kaubanduskoda» or «koda». Mid-sentence they are lowercase: «koja \
ettepanekud», «kaubanduskoja arvamus», «ministeerium küsis koja arvamust». Capitalise \
only at the start of a sentence: «Koja hinnangul …», «Kaubanduskoda toetab …».
- Cases: koda, koja (of), kojale (to), kojas (at), kotta (into), kojast (from), kojaga \
(with); the same endings on «kaubanduskoda». The genitive is «koja», never «kaja».
- Prefer «koda» or «kaubanduskoda» to the abbreviation EKTK.
- Other organisations with «koda» in their name (Põllumajandus-Kaubanduskoda, \
Looduskoostöö Koda) keep their own spelling."""

_ENDS = r"(?:da|ja|jale|jas|jast|jaga|jani|jana|jata|tta)\b"

#: «kaja» where the model meant «koja»; lowercase only — «Kaja» is a name.
_KAJA = re.compile(r"\bkaja\b")
#: «Kaubandus- ja Tööstuskoda», «Kaubandus Tööstuskoda» → the hyphenated name.
_WRONG_NAME = re.compile(r"\bKaubandus(?:-\s*ja\s+|\s+)Tööstusko(?=" + _ENDS + ")")
#: «kaubandus koda» → «kaubanduskoda».
_SPLIT = re.compile(r"\b([Kk])aubandus\s+ko(?=" + _ENDS + ")")
#: «Kaubandus-Tööstuskoja» without «Eesti» before it → the short form.
_BARE_NAME = re.compile(r"(?<!Eesti )(?<![\w-])Kaubandus-Tööstusko(?=" + _ENDS + ")")
#: A capital short form standing on its own («Koja», «Kaubanduskojale»).
_CAPITAL = re.compile(r"(?<![\w-])(?:Kaubandusko|Ko)(?=" + _ENDS + ")")
#: The last word before a match.
_WORD_BEFORE = re.compile(r"([\w-]+)[^\w]*$")

_SENTENCE_ENDS = '.!?\n«"“'
_IN_SENTENCE_MARKS = ",;:–—("


def _starts_sentence(text: str, at: int) -> bool:
    before = text[:at].rstrip()
    return not before or before[-1] in _SENTENCE_ENDS


def _lower_mid_sentence(text: str, match: re.Match[str]) -> str:
    """Lowercase a short form mid-sentence; keep it right after a capitalised word (a name)."""
    if _starts_sentence(text, match.start()):
        return match.group(0)
    before = text[: match.start()].rstrip()
    previous = _WORD_BEFORE.search(before)
    if before[-1:] not in _IN_SENTENCE_MARKS and previous and previous.group(1)[:1].isupper():
        return match.group(0)
    return match.group(0).lower()


def fix(text: str) -> str:
    """The house style applied to one AI-written line; unchanged if it was right."""
    if not text:
        return text
    text = _KAJA.sub("koja", text)
    text = _WRONG_NAME.sub("Kaubandus-Tööstusko", text)
    text = _SPLIT.sub(lambda m: f"{m.group(1)}aubandusko", text)
    current = text
    text = _BARE_NAME.sub(
        lambda m: "Kaubandusko" if _starts_sentence(current, m.start()) else "kaubandusko",
        current,
    )
    current = text
    return _CAPITAL.sub(lambda m: _lower_mid_sentence(current, m), current)
