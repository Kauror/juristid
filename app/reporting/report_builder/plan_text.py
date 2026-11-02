"""The fixed opening of «Majanduspoliitika ülevaade»: the department's action plan.

Every report the board receives opens with the same section of the
Poliitikakujundamise ja õigusosakond's yearly action plan, then the
«Täitmine» table for the period. The text is the department's own, copied
from its reports as written; it changes once a year, by a change to this file
(docs/adr/0199 §4). Nothing here is read from a record.
"""

from __future__ import annotations

#: The bold lines above the plan table.
HEADINGS = (
    "Poliitikakujundamise – ja õigusosakonna tegevuskava 2026",
    "1. valdkond – osalemine poliitikakujundamises",
)
#: The plain line introducing the plan table.
INTRODUCTION = "Tegevused ja eesmärgid"
#: The plan table's first row, spanning both columns.
TABLE_TITLE = "Tegevuse pealkiri"
#: ``(label, text)`` rows of the plan table; ``\n`` separates paragraphs.
ROWS: tuple[tuple[str, str], ...] = (
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
