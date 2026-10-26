"""A `Teema käik` headline names its kind once (owner's UX corrections, 2026-10-26).

Imported koda.ee pages carry their own kind in their title — «Koduka ülevaade:
Mida arvad…», «Uudis: Saatsime 15 ettepanekut…» — and the chronology already
leads the line with the kind, so the row read «Ülevaade – Koduka ülevaade: …»
and «Uudis – Uudis: …». This joins the kind and the title **for display**: the
stored title is never touched, and only a prefix that *is* the kind — the kind
word alone, or with one of a few known source words before it — is dropped.

An allow-list rather than «any prefix ending in the word»: «Majanduse ülevaade:
2026» is a real title whose first words are content, and a rule that read every
`…ülevaade:` as a kind label would print «Ülevaade: 2026».
"""

from __future__ import annotations

#: Words a source puts before the kind in its own title: «Koduka ülevaade»,
#: «Koja uudis», «Kodulehe ülevaade». Compared case-insensitively.
SOURCE_WORDS = ("koduka", "koja", "kodulehe")


def _kind_prefix_end(kind: str, title: str, source_words: tuple[str, ...]) -> int:
    """Where the title's own kind prefix ends (after its colon), or 0 when it has none."""
    head, colon, _rest = title.partition(":")
    if not colon:
        return 0
    words = head.split()
    wanted = kind.casefold()
    if not words or words[-1].casefold() != wanted:
        return 0
    if len(words) == 1 or (len(words) == 2 and words[0].casefold() in source_words):
        return len(head) + 1
    return 0


def kind_and_title(
    kind: str, title: str, *, separator: str = " – ", source_words: tuple[str, ...] = ()
) -> str:
    """``Kind – title``, or ``Kind: rest`` when the title already begins with the kind.

    >>> kind_and_title("Ülevaade", "Koduka ülevaade: Mida arvad?", source_words=SOURCE_WORDS)
    'Ülevaade: Mida arvad?'
    >>> kind_and_title("Uudis", "Uudis: Saatsime 15 ettepanekut")
    'Uudis: Saatsime 15 ettepanekut'
    >>> kind_and_title("Ülevaade", "Majanduse ülevaade: 2026")
    'Ülevaade – Majanduse ülevaade: 2026'
    """
    title = title.strip()
    if not title:
        return kind
    # `source_words` only for imported koda.ee titles: in a title a lawyer
    # typed — «Koja märge: …» — «Koja» is content (review of #555).
    end = _kind_prefix_end(kind, title, source_words)
    if end:
        rest = title[end:].strip()
        return f"{kind}: {rest}" if rest else kind
    return f"{kind}{separator}{title}"
