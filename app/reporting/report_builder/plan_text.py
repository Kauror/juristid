"""The fixed opening of «Majanduspoliitika ülevaade»: the department's action plan.

Every report the board receives opens with the section of the
Poliitikakujundamise ja õigusosakond's action plan **for the report's year**,
then the «Täitmine» table for the period. The text is the department's own,
copied from its reports as written; nothing here is read from a record and
nothing is generated (docs/adr/0199 §4).

A year's plan is registered in `PLANS` once it is approved — a new `Plan`
for 2027, nothing else changes. A period in a year with no registered plan,
or one across New Year (whose plan would be a guess), is refused for export
with `problem_for`'s sentence; the draft is kept.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Plan:
    year: int
    #: The bold lines above the plan table.
    headings: tuple[str, ...]
    #: The plain line introducing the plan table.
    introduction: str
    #: The plan table's first row, spanning both columns.
    table_title: str
    #: ``(label, text)`` rows of the plan table; ``\n`` separates paragraphs.
    rows: tuple[tuple[str, str], ...]


# -- 2026, as in the department's reports ---------------------------------------
_HEADINGS_2026 = (
    "Poliitikakujundamise – ja õigusosakonna tegevuskava 2026",
    "1. valdkond – osalemine poliitikakujundamises",
)
_INTRODUCTION_2026 = "Tegevused ja eesmärgid"
_TABLE_TITLE_2026 = "Tegevuse pealkiri"
_ROWS_2026: tuple[tuple[str, str], ...] = (
    (
        "Tegevuse kirjeldus:",
        "A. Osalemine õigusloomes\n"
        "Eesti ja EL ettevõtlust mõjutavate õigusaktide eelnõude analüüs, arvamuste "
        "koostamine, esitamine asjaomastele institutsioonidele, avalikkusele\n"
        "B. Osalemine strateegiate, poliitikadokumentide, arengukavade väljatöötamisel\n"
        "C. Ettevõtlust mõjutava seadusloome (protsessi) parendamine\n"
        "(riigi survestamine halduskoormuse hindamiseks oluliste teemade puhul; "
        "eelnõude väljatöötamisel mõjude analüüsi läbiviimiseks)\n"
        "D. Koja juhatuse poolt seatud tegevuseesmärkide täitmine.",
    ),
    (
        "Mõju liikmetele /koja tegevusele/ majandusele:",
        "Bürokraatia kasvu vähendamine; bürokraatlike nõuete optimeerimine; liikmete "
        "kulude vähendamine; tingimuste säilitamine või loomine liikmete tulude kasvuks. "
        "/Koja ühiskondliku positsiooni ja mõjujõu kasv / majanduse konkurentsivõime kasv",
    ),
    (
        "Tegevuse olulisus/lisaväärtus:",
        "Kaasaaitamine ettevõtjate konkurentsivõime tugevdamisele / säilitamisele",
    ),
    (
        "Lõpptulemus:",
        "Väärtustub Eesti ühiskonnas ettevõtlus ja majandustegevus, Strateegia valdkond "
        "„Mõju liikmetele“ realiseerub; Koja strateegilised eesmärgid täidetakse",
    ),
    ("Toimumisaeg:", "Pidev tegevus aasta jooksul"),
    (
        "Tegevused:",
        "Eelnõude läbitöötamine, dokumentide, ideekavandite läbitöötamine, analüüs, "
        "liikmetele tutvustamine läbi Koja infokanalite s.h koduleht\n"
        "Liikmete arvamuste küsimine, läbiarutamine\n"
        "Seisukohtade, ettepanekute ja arvamuste edastamine, suhtlus asjaomaste "
        "institutsioonidega",
    ),
    (
        "Partnerid:",
        "Asjaomased ministeeriumid, Riigikogu komisjonid; Valitsus; asjakohased Koja, "
        "liikmed, teised ettevõtlusorganisatsioonid, meediakanalid; EL institutsioonid; "
        "Eurochambres; Euroopa Majandus- ja Sotsiaalkomitee",
    ),
    ("Projektijuht/vastutaja:", "Mait Palts"),
)


#: The approved plans by year. Add a year's `Plan` here when it is approved.
PLANS: dict[int, Plan] = {
    2026: Plan(
        year=2026,
        headings=_HEADINGS_2026,
        introduction=_INTRODUCTION_2026,
        table_title=_TABLE_TITLE_2026,
        rows=_ROWS_2026,
    ),
}


def plan_for(start: date, end: date) -> Plan | None:
    """The plan of the period's year; ``None`` when that is not one known plan."""
    if start.year != end.year:
        return None
    return PLANS.get(start.year)


def problem_for(start: date, end: date) -> str:
    """Why no plan opens this period's report, or ``""`` when one does."""
    if start.year != end.year:
        return (
            "Periood ulatub üle aastavahetuse, aga ülevaade algab ühe aasta tegevuskavaga. "
            "Vali periood ühe aasta piires."
        )
    if start.year not in PLANS:
        return (
            f"{start.year}. aasta tegevuskava pole Juristidis seadistatud, seega selle "
            "perioodi Wordi ülevaadet ei koostata. Valitud teemad ja tekstid jäävad alles."
        )
    return ""
