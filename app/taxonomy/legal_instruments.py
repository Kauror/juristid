"""The reviewed vocabulary of Õigusakt, and the one seam that reads the old one.

`Õigusakt` answers *what kind of legal or source instrument does this Matter
concern*. It is not `Menetlusliik` and the two are never merged: `Track` says
what kind of **procedure** a Matter belongs to, this says what kind of
**instrument** it is about. A file can be `ELi õiguse ülevõtmine` (procedure)
about a `Seadus` (instrument), or an `ELi algatus` about an `EL määrus`, and
neither answer can be derived from the other (docs/adr/0070).

Where the vocabulary comes from
-------------------------------

The historical register — `Tööd eelnõudega.xlsx`, column `ÕIGUSAKT` on every
year sheet from 2011 to 2026 — is the whole source. It was read read-only in
2026-09 across all sixteen year sheets: **2418 non-empty cells in 57 distinct
spellings**, plus one further spelling (`EK konsultatsioon`) that appears only
in the department's newer working copies, for **58 in total**. Nothing was
invented and nothing was taken from a list somebody wrote for this task; the
seventeen labels below are what those 58 spellings say once case, whitespace,
diacritic and abbreviation variants are collapsed.

**Sixteen concepts and a `Muu`, not fifty-eight rows.** `S`, `seadus` and
`Seadus` are one concept written three ways, and offering all three would let
spelling create categories. Collapsing them is the whole job of
`canonical_legal_instrument_keys` below.

**Nothing here rewrites history.** The raw cell survives untouched in
`CurrentRegisterState.legal_instrument_raw` and in every source observation.
This module says what that raw value *means*; it never replaces it. Both facts
exist for one record and neither is derivable from the other after the fact
(docs/data-contracts/excel-era-*.toml, task §21).

Where EU-ness lives, and where it does not
------------------------------------------

The register writes `EL määrus`, `EL strateegia`, `EL konsultatsioon` and
`EL direktiiv` — but those four prefixes are not one rule.

A **Regulation** and a **Directive** are distinct legal acts with no domestic
equivalent, so `EL määrus` and `Direktiiv` are their own concepts and an EU
Regulation is never filed as the Estonian ministerial `Määrus` beside it. A
**strategy** and a **consultation** are the same kind of document whoever runs
them, so `EL strateegia` is `Strateegia` and `EL konsultatsioon` is
`Konsultatsioon`. Whether the *procedure* is a European one is already an
answer this product holds, and it is Menetlusliik's (`Track.EU_INITIATIVE`).

`EK` — Euroopa Komisjon — is read as `EL` throughout: a Commission regulation
is an EU regulation and a Commission communication is an EU communication. That
is a reading of what the words say, not a merge of two departmental categories.

Ordering
--------

`sort_order` is a reviewed sequence, not a ranking. Estonian legislation first
because it is what most files are, then the European acts, then the strategic
documents, then the executive acts, then the remaining kinds, then `Muu` last.
It is deliberately **not** derived from how often each value occurs: an order
computed from records is an order that rearranges itself under the reader, and
it is a channel through which the contents of the register can be inferred from
a checkbox list (`app/taxonomy/vocabulary.py`, Uus teema redesign §7).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.text import normalize_for_matching

#: Bumped when the *set* of reviewed instrument types changes, not when a raw
#: alias is added to one of them. Pinned by the reference-data test so that
#: growing the vocabulary is a decision somebody made rather than a diff that
#: slipped through.
REFERENCE_LEGAL_INSTRUMENT_VERSION = "1.0"

#: Where the business list came from, and when. Quoted in the ADR and asserted
#: by the source-contract test, so changing the vocabulary without changing this
#: provenance fails review.
LEGAL_INSTRUMENT_SOURCE_TITLE = "Tööd eelnõudega.xlsx — veerg ÕIGUSAKT, lehed 2011–2026"
LEGAL_INSTRUMENT_SOURCE_PUBLISHER = "Eesti Kaubandus-Tööstuskoda, õigusosakond"
LEGAL_INSTRUMENT_SOURCE_VERIFIED_ON = "2026-09-10"

#: What the survey found, recorded so a later reader can tell whether the
#: vocabulary still covers the source it was derived from. Read-only survey; no
#: row-level content from the workbook is stored in this repository.
LEGAL_INSTRUMENT_SOURCE_SHEETS: tuple[str, ...] = tuple(str(year) for year in range(2011, 2027))
LEGAL_INSTRUMENT_SOURCE_NON_EMPTY = 2418
LEGAL_INSTRUMENT_SOURCE_DISTINCT_SPELLINGS = 58

#: The `Muu` row's key. It is a real vocabulary row here, unlike Valdkonnad's
#: `Muu` — and that difference is deliberate rather than an inconsistency.
#:
#: `Matter.policy_area_other` is revealed by a checkbox that is *not* a
#: `PolicyArea`, because no historical record ever needed the answer "some other
#: area" to be storable. This column does: 1130 of the 2418 historical cells say
#: exactly `muu`, and a vocabulary with nowhere to put them would either lose
#: the department's own answer or invent a category it never chose.
OTHER_LEGAL_INSTRUMENT_KEY = "muu"


@dataclass(frozen=True)
class ReferenceLegalInstrumentType:
    key: str
    label_et: str
    sort_order: int
    #: Every spelling the historical register uses for this concept, exactly as
    #: written there. Matching is diacritic- and case-insensitive, so a variant
    #: that differs only in those does not need its own entry — `Seadus`, `S `
    #: and `seadus` are all reached by `seadus` below.
    #:
    #: Combinations are **not** listed here. `S, M` is not an alias of anything;
    #: it is two answers, and `canonical_legal_instrument_keys` splits it before
    #: it ever reaches this table.
    aliases: tuple[str, ...] = field(default_factory=tuple)
    #: Why this concept exists and what falls outside it. Written to make two
    #: people classify the same file the same way.
    description: str = ""


REFERENCE_LEGAL_INSTRUMENT_TYPES: tuple[ReferenceLegalInstrumentType, ...] = (
    ReferenceLegalInstrumentType(
        key="seadus",
        label_et="Seadus",
        sort_order=10,
        aliases=("seadus", "S"),
        description="Riigikogu vastu võetav seadus või selle muutmine.",
    ),
    ReferenceLegalInstrumentType(
        key="maarus",
        label_et="Määrus",
        sort_order=20,
        aliases=("määrus", "määrused", "M"),
        description=(
            "Vabariigi Valitsuse või ministri määrus. Euroopa Liidu määrus on eraldi "
            "liik EL määrus."
        ),
    ),
    ReferenceLegalInstrumentType(
        key="vtk",
        label_et="VTK",
        sort_order=30,
        aliases=("VTK",),
        description="Väljatöötamiskavatsus — eelnõule eelnev kavatsus ja selle materjalid.",
    ),
    ReferenceLegalInstrumentType(
        key="eelnou",
        label_et="Eelnõu",
        sort_order=40,
        aliases=("eelnõu",),
        description=(
            "Eelnõu, mille liiki allikas täpsemalt ei nimetanud. Kui liik on teada, "
            "vali ka see — üks teema võib kanda mõlemat."
        ),
    ),
    ReferenceLegalInstrumentType(
        key="direktiiv",
        label_et="Direktiiv",
        sort_order=50,
        aliases=(
            "direktiiv",
            "direktiivid",
            "D",
            "EL direktiiv",
            "ELi direktiiv",
            "EL direktiivid",
        ),
        description=(
            "Euroopa Liidu direktiiv. Direktiiv on alati ELi akt, seega eraldi "
            "«EL direktiiv» liiki ei ole."
        ),
    ),
    ReferenceLegalInstrumentType(
        key="el-maarus",
        label_et="EL määrus",
        sort_order=60,
        aliases=("EL määrus", "ELi määrus", "EK määrus", "EL määrused", "EL M"),
        description=(
            "Euroopa Liidu määrus, sealhulgas Euroopa Komisjoni oma. Eraldi liik "
            "Määrusest: ELi määrus kehtib vahetult ja seda ei võeta üle."
        ),
    ),
    ReferenceLegalInstrumentType(
        key="el-teatis",
        label_et="EL teatis",
        sort_order=70,
        aliases=("EL teatis", "EK teatis"),
        description="Euroopa Komisjoni teatis või muu ELi institutsiooni teatis.",
    ),
    ReferenceLegalInstrumentType(
        key="konsultatsioon",
        label_et="Konsultatsioon",
        sort_order=80,
        aliases=(
            "EL konsultatsioon",
            "ELi konsultatsioon",
            "EK konsultatsioon",
            "EL avalik konsultatsioon",
            "avalik konsultatsioon",
        ),
        description=(
            "Avalik konsultatsioon või arvamuse küsimine. Kas seda korraldab ELi "
            "institutsioon, ütleb menetlusliik, mitte akti liik."
        ),
    ),
    ReferenceLegalInstrumentType(
        key="strateegia",
        label_et="Strateegia",
        sort_order=90,
        aliases=("strateegia", "EL strateegia", "EK strateegia"),
        description="Strateegiadokument. Arengukava ja tegevuskava on eraldi liigid.",
    ),
    ReferenceLegalInstrumentType(
        key="arengukava",
        label_et="Arengukava",
        sort_order=100,
        aliases=("arengukava",),
        description="Riiklik või valdkondlik arengukava.",
    ),
    ReferenceLegalInstrumentType(
        key="tegevuskava",
        label_et="Tegevuskava",
        sort_order=110,
        aliases=("tegevuskava",),
        description="Tegevuskava — kokkulepitud sammud, mitte õigusakt ega arengukava.",
    ),
    ReferenceLegalInstrumentType(
        key="visioon",
        label_et="Visioon",
        sort_order=120,
        aliases=("visioon",),
        description="Visioonidokument.",
    ),
    ReferenceLegalInstrumentType(
        key="korraldus",
        label_et="Korraldus",
        sort_order=130,
        aliases=("korraldus", "VV korraldus"),
        description="Vabariigi Valitsuse või muu haldusorgani korraldus.",
    ),
    ReferenceLegalInstrumentType(
        key="kaskkiri",
        label_et="Käskkiri",
        sort_order=140,
        aliases=("käskkiri",),
        description="Ministri või muu haldusorgani käskkiri.",
    ),
    ReferenceLegalInstrumentType(
        key="ettepanek",
        label_et="Ettepanek",
        sort_order=150,
        aliases=("ettepanek",),
        description="Ettepanek, sealhulgas Euroopa Komisjoni ettepanek õigusakti kohta.",
    ),
    ReferenceLegalInstrumentType(
        key="kusitlus",
        label_et="Küsitlus",
        sort_order=160,
        aliases=("küsitlus",),
        description="Küsitlus või uuring, millele Koda vastab.",
    ),
    #: Last, with a gap before it, so a later addition to the vocabulary does
    #: not have to renumber anything to keep `Muu` at the end of the row.
    ReferenceLegalInstrumentType(
        key=OTHER_LEGAL_INSTRUMENT_KEY,
        label_et="Muu",
        sort_order=200,
        aliases=("muu",),
        description=(
            "Mõni muu akt või dokument. Vali ka «Õigusakti liik» ja kirjuta, "
            "millega on tegemist — sellest ei teki uut liiki."
        ),
    ),
)

#: The stable keys, in reviewed order. Read by the seed migration, the
#: reference-data test and `seed_dev_data`, so none of the three grows its own
#: copy of the list.
REFERENCE_LEGAL_INSTRUMENT_KEYS: tuple[str, ...] = tuple(
    item.key for item in REFERENCE_LEGAL_INSTRUMENT_TYPES
)


# ---------------------------------------------------------------------------
# Raw ÕIGUSAKT -> canonical keys. The one seam.
# ---------------------------------------------------------------------------

#: What separates two answers written in one cell. The register combines
#: instruments five ways — `S, M`, `D, M`, `direktiiv ja määrus`,
#: `direktiiv+määrus`, `direktiiv/määrus` — and every one of them is two
#: answers, never one concatenated pseudo-value.
#:
#: `ja` is matched as a whole word only. No alias above contains it, and a
#: substring match would cut `käskkiri` in half.
_SEPARATORS = re.compile(r"\s*(?:,|\+|/|;|\bja\b)\s*")

#: Built once from the aliases above, so there is exactly one list of spellings
#: in this module and the table a reader checks is the table the code uses.
_ALIAS_TO_KEY: dict[str, str] = {}
for _item in REFERENCE_LEGAL_INSTRUMENT_TYPES:
    for _alias in _item.aliases:
        _normalized = normalize_for_matching(_alias)
        if _normalized in _ALIAS_TO_KEY and _ALIAS_TO_KEY[_normalized] != _item.key:
            raise AssertionError(
                f"{_alias!r} is claimed by both {_ALIAS_TO_KEY[_normalized]!r} and {_item.key!r}"
            )
        _ALIAS_TO_KEY[_normalized] = _item.key
del _item, _alias, _normalized

#: Raw values that name **several** concepts in one phrase, with no separator to
#: split on. Two of them, and both are reviewed judgements rather than mechanics,
#: which is why they are here in one visible place rather than folded into the
#: alias tuples above:
#:
#: * ``VTK eelnõu`` is a draft of a väljatöötamiskavatsus. Both words name a
#:   concept in this vocabulary and both are true of the file.
#: * ``Seaduse muutmise seaduse eelnõu`` is the register answering with more
#:   detail than usual: an Act, in draft. Reading it as `Seadus` alone would
#:   drop half of what the department wrote down, and reading it as `Muu` would
#:   drop all of it.
#:
#: Nothing is inferred by looking for canonical words *inside* an arbitrary
#: phrase. These two are matched whole, as reviewed spellings, exactly as every
#: other alias is — otherwise a Matter title pasted into the wrong cell would
#: start classifying itself.
COMPOUND_ALIASES: dict[str, tuple[str, ...]] = {
    "VTK eelnõu": ("vtk", "eelnou"),
    "Seaduse muutmise seaduse eelnõu": ("seadus", "eelnou"),
}

_COMPOUND_BY_NORMALIZED: dict[str, tuple[str, ...]] = {
    normalize_for_matching(raw): keys for raw, keys in COMPOUND_ALIASES.items()
}

#: Raw values the survey found that name **no instrument at all**, and the
#: reason each one is left unmapped rather than filed under `Muu`.
#:
#: `Muu` is an answer: it says *the kind is some other kind*. Neither of these
#: says that. `EL` names a scope and `sisendi küsimine VTK ettevalmistamiseks`
#: names an activity, and recording either as `Muu` would put a decision in the
#: department's mouth that the department did not make. They keep their raw
#: value and get no canonical classification, which is the honest answer
#: (task §11).
UNMAPPABLE_RAW_VALUES: dict[str, str] = {
    "EL": ("Nimetab ulatuse, mitte akti liiki. Milline ELi akt see oli, ei ole reast tuletatav."),
    "sisendi küsimine VTK ettevalmistamiseks": (
        "Nimetab tegevust, mitte akti: sisendi küsimine ei ole VTK, vaid sellele eelnev samm."
    ),
}


def canonical_legal_instrument_keys(raw: str) -> tuple[str, ...]:
    """Read one historical `ÕIGUSAKT` cell as zero, one or several canonical keys.

    The only place a raw spelling is turned into canonical classification.
    Scattering this across the importer, the forms and the services is how the
    same cell starts meaning two different things depending on which door it
    came through, so every caller — import, a future reviewed backfill, the
    tests that prove the coverage — comes here.

    Four steps, in order:

    0. **Try the whole value.** Two reviewed spellings name several concepts in
       one phrase with nothing to split on (`COMPOUND_ALIASES`), so the whole
       value is looked up before it is taken apart.
    1. **Split.** `S, M` is two answers. Separators are `,` `+` `/` `;` and the
       word `ja`; nothing in the alias table contains any of them.
    2. **Normalise.** Case, surrounding and repeated whitespace and diacritics
       are collapsed by `normalize_for_matching`, so `Määrus `, `määrus` and
       `MAARUS` are one lookup. No two concepts in this vocabulary differ only
       by a diacritic, which is what makes that safe.
    3. **Look up.** A part that is not a reviewed alias resolves to nothing, and
       an unrecognised part makes the **whole value** unmapped rather than
       partially mapped — half of a combined answer is a worse record than none,
       because nothing downstream could tell it was half.

    Returns the keys in reviewed vocabulary order, de-duplicated. An empty
    tuple means *this value has no canonical reading*, which is a real answer
    and never an error: the raw string is preserved regardless, and
    `UNMAPPABLE_RAW_VALUES` documents the two spellings the survey found.

    Nothing here touches the database and nothing here creates a vocabulary
    row. A spelling that is not in the table does not become a new instrument
    type; it becomes a review finding.
    """
    if not raw or not raw.strip():
        return ()

    compound = _COMPOUND_BY_NORMALIZED.get(normalize_for_matching(raw))
    if compound is not None:
        return tuple(key for key in REFERENCE_LEGAL_INSTRUMENT_KEYS if key in compound)

    parts = [part for part in _SEPARATORS.split(raw.strip()) if part.strip()]
    if not parts:
        return ()

    keys: set[str] = set()
    for part in parts:
        key = _ALIAS_TO_KEY.get(normalize_for_matching(part))
        if key is None:
            return ()
        keys.add(key)
    return tuple(key for key in REFERENCE_LEGAL_INSTRUMENT_KEYS if key in keys)
