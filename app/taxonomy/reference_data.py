"""The reviewed production vocabulary of Koda's policy areas.

Reference data, not a fixture. The distinction matters more here than anywhere
else in the repository, because both kinds of row end up in the same table.

``seed_dev_data`` invents ``Näidisministeerium`` and five provisional areas so a
developer has something to click. Those are *props*: wrong on purpose, so that
nobody mistakes a rehearsal for the real register. What is below is the
opposite — the classification the department actually files under, which
belongs in a real deployment and would be useless as an invention.

**Where these twenty-three come from.** The department's own reviewed working
list of Valdkonnad, supplied with the approved Teema redesign on 2026-08-24 and
transcribed here label for label, in the order it was given. It is not derived
from anything: no page was scraped, no old list was mapped forward, and no
label was reworded to look tidier beside its neighbours. "Alkohol, tubakas" and
"Finantsõigus, rahapesu" carry their commas because that is what the department
calls them.

**Why nothing at runtime reads a source.** Changing this vocabulary is a code
change with a migration behind it, reviewed like any other. The provenance
below exists so a reviewer can check the claim, not so a job can act on it.

**Why they overlap.** ``Matter.policy_areas`` is many-to-many because a file
about an energy tax genuinely is both Maksud ja toll and Energeetika, and a
consultation on a construction permit is both Ehitus and Keskkond. The
vocabulary is not a partition and must not be forced into one.

The working vocabulary, and why it replaced the nine
----------------------------------------------------

Version 1.0 was those nine public focus areas, on the argument that
``PolicyArea`` is the small stable axis a yearly report is cut along and that
anything narrower belongs to ``Tag``. A year of real filing said otherwise: the
nine are what Koda *campaigns on*, and they are not what a lawyer reaches for
when asked which area a file belongs to. "Maksejõuetus", "Riigihanked",
"Alkohol, tubakas" and "ELi õiguse ülevõtmine" are the words the department
actually uses, and with only nine broad headings on offer, most files were
either filed under nothing or filed under "Muu".

Version 2.0 is therefore the reviewed *working* vocabulary — the twenty-three
labels the department named, in the order it named them. ``Tag`` is unaffected
and stays what it was: free subject vocabulary with aliases and merging.
Valdkond answers *which area of law or policy*, Silt answers *what specifically
about it*, and the two are never merged (Teema redesign §7, §22.2).

**Nothing was renamed, remapped or deleted.** Four of the nine — Energeetika,
Riigihanked, Äriõigus, Keskkond — are in the new list under the same name and
therefore keep their key, their row and every relation pointing at them. The
other five carry names the new list does not contain, so they are *deactivated*
and nothing else: the Matters filed under Maksud, Tööjõud, Halduskoormus, Aus
konkurents and Haridus ja ettevõtlikkus still show them, statistics still count
them, and no code guesses which of the new labels somebody meant. "Maksud" is
not "Maksud ja toll", and inventing that equivalence would rewrite a decade of
somebody else's filing on a coincidence of spelling (`taxonomy/0003`).

**``Olulised tähtajad`` here is a label, not a deadline.** It is the subject
area a file belongs to. ``MatterImportantDate`` is an operational date on one
Matter. They share four words and nothing else, and no code may treat one as
the other.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Bumped when the *set* of reviewed areas changes, not when wording is fixed.
#: Recorded in the reference-data plan digest so a plan built under one
#: vocabulary can never be applied under another.
REFERENCE_POLICY_AREA_VERSION = "2.0"

#: Where the business list came from, and when. Quoted in the ADR and asserted
#: by the source-contract test, so that changing the vocabulary without changing
#: this provenance fails review.
POLICY_AREA_SOURCE_TITLE = "Koda Õigusloome — Teema redesign, Valdkonnad"
POLICY_AREA_SOURCE_PUBLISHER = "Eesti Kaubandus-Tööstuskoda, õigusosakond"
POLICY_AREA_SOURCE_URL = ""
POLICY_AREA_SOURCE_VERIFIED_ON = "2026-08-24"

#: Version 1.0's provenance, kept rather than overwritten. The nine areas it
#: describes are still in the database — four of them active — and a reader
#: asking where "Halduskoormus" came from must still be able to find out.
POLICY_AREA_SOURCE_V1_TITLE = "Meie mõju ja eesmärk — Millega tegeleme?"
POLICY_AREA_SOURCE_V1_URL = "https://www.koda.ee/et/meie-moju"
POLICY_AREA_SOURCE_V1_VERIFIED_ON = "2026-08-23"


@dataclass(frozen=True)
class ReferencePolicyArea:
    key: str
    name_et: str
    #: What falls inside and — more usefully — what falls outside. Written to
    #: make two people classify the same Matter the same way, which marketing
    #: prose cannot do.
    description: str
    sort_order: int
    #: The heading the public page carries, when it differs from the name. Kept
    #: so the trail from business source to database row survives a reword.
    public_heading: str = ""


REFERENCE_POLICY_AREAS_V2: tuple[ReferencePolicyArea, ...] = (
    ReferencePolicyArea(
        key="maksejouetus",
        name_et="Maksejõuetus",
        description=(
            "Maksejõuetusmenetlus, pankrot, saneerimine ja võlgade "
            "ümberkujundamine. Äriühingu enda õiguslik raamistik on Äriõigus."
        ),
        sort_order=10,
    ),
    ReferencePolicyArea(
        key="raamatupidamine",
        name_et="Raamatupidamine",
        description=(
            "Raamatupidamine, majandusaasta aruandlus ja auditeerimine. "
            "Maksuaruandlus on Maksud ja toll."
        ),
        sort_order=20,
    ),
    ReferencePolicyArea(
        key="intellektuaalomand",
        name_et="Intellektuaalomand",
        description=("Autoriõigus, patendid, kaubamärgid, ärisaladus ja litsentsimine."),
        sort_order=30,
    ),
    ReferencePolicyArea(
        key="toetusmeetmed",
        name_et="Toetusmeetmed",
        description=(
            "Riigiabi, ettevõtlustoetused ja Euroopa Liidu rahastusmeetmed ning nende tingimused."
        ),
        sort_order=40,
    ),
    ReferencePolicyArea(
        key="koalitsioonilepped",
        name_et="Koalitsioonilepped",
        description=(
            "Valitsuse koalitsioonilepped ja tegevusprogrammid ettevõtluskeskkonda puudutavas osas."
        ),
        sort_order=50,
    ),
    ReferencePolicyArea(
        key="oigusloome",
        name_et="Õigusloome",
        description=(
            "Õigusloome kvaliteet ja menetlus ise: kaasamise hea tava, "
            "mõjuanalüüs, jõustumisreeglid. Eelnõu sisu kuulub oma valdkonda."
        ),
        sort_order=60,
    ),
    ReferencePolicyArea(
        key="energeetika",
        name_et="Energeetika",
        description=(
            "Energiaturg, varustuskindlus, võrgud ja taristu ning ettevõtja "
            "energiakulu. Kliimaeesmärk ise on Keskkond; sama eelnõu võib "
            "kuuluda mõlemasse."
        ),
        sort_order=70,
    ),
    ReferencePolicyArea(
        key="riigihanked",
        name_et="Riigihanked",
        description=(
            "Riigihangete regulatsioon ja praktika: hankekord, vaidlustus, "
            "hankija ja pakkuja kohustused."
        ),
        sort_order=80,
    ),
    ReferencePolicyArea(
        key="haridus",
        name_et="Haridus",
        description=(
            "Haridussüsteem, kutseharidus, oskused ja hariduse vastavus tööturu "
            "vajadustele. Töösuhte küsimused on Töösuhted, töökeskkond."
        ),
        sort_order=90,
    ),
    ReferencePolicyArea(
        key="tarbijakaitse",
        name_et="Tarbijakaitse",
        description=("Tarbija õigused, müügitingimused, garantii ja kaubandustavad."),
        sort_order=100,
    ),
    ReferencePolicyArea(
        key="alkohol-tubakas",
        name_et="Alkohol, tubakas",
        description=(
            "Alkoholi, tubaka ja nendega sarnaste toodete käitlemine, "
            "müügipiirangud ja reklaam. Aktsiis ise on Maksud ja toll."
        ),
        sort_order=110,
    ),
    ReferencePolicyArea(
        key="digiteemad",
        name_et="Digiteemad",
        description=(
            "Digilahendused, andmed, küberturvalisus, e-teenused ja "
            "tehisintellekt ettevõtja vaates."
        ),
        sort_order=120,
    ),
    ReferencePolicyArea(
        key="finantsoigus-rahapesu",
        name_et="Finantsõigus, rahapesu",
        description=(
            "Finantsteenused, makseteenused, krediit ning rahapesu ja "
            "terrorismi rahastamise tõkestamine."
        ),
        sort_order=130,
    ),
    ReferencePolicyArea(
        key="ehitus",
        name_et="Ehitus",
        description=(
            "Ehitusõigus, planeerimine, ehitusload ja kinnisvara. Keskkonnamõju "
            "hindamine on Keskkond."
        ),
        sort_order=140,
    ),
    ReferencePolicyArea(
        key="arioigus",
        name_et="Äriõigus",
        description=(
            "Äri- ja tsiviilõiguslik raamistik: äriühinguõigus, registrid, "
            "lepingu- ja võlaõigus. Maksejõuetus on oma valdkond."
        ),
        sort_order=150,
    ),
    ReferencePolicyArea(
        key="valistoojoud",
        name_et="Välistööjõud",
        description=(
            "Välismaalase töötamine ja elamine Eestis: kvoot, load, lühiajaline "
            "töötamine, rändepoliitika."
        ),
        sort_order=160,
    ),
    ReferencePolicyArea(
        key="maksud-ja-toll",
        name_et="Maksud ja toll",
        description=("Maksud, aktsiisid, tollireeglid, maksuhaldus ja maksuaruandlus."),
        sort_order=170,
    ),
    ReferencePolicyArea(
        key="toosuhted-tookeskkond",
        name_et="Töösuhted, töökeskkond",
        description=(
            "Tööõigus, töölepingud, töötasu, töö- ja puhkeaeg ning töötervishoid ja tööohutus."
        ),
        sort_order=180,
    ),
    ReferencePolicyArea(
        key="keskkond",
        name_et="Keskkond",
        description=(
            "Keskkonna-, kliima- ja ressursiregulatsioon: jäätmed ja pakend, "
            "vesi, keskkonnatasud, keskkonnamõju hindamine. Energiaturg on "
            "Energeetika."
        ),
        sort_order=190,
    ),
    ReferencePolicyArea(
        key="muud-teemad",
        name_et="Muud teemad",
        description=(
            "Teemad, mis ei kuulu ühessegi loetletud valdkonda. Kasuta viimase "
            "võimalusena; täpsustus käib sildiga."
        ),
        sort_order=200,
    ),
    ReferencePolicyArea(
        key="eli-oiguse-ulevotmine",
        name_et="ELi õiguse ülevõtmine",
        description=(
            "Direktiivide ja määruste ülevõtmine ning rakendamine Eesti "
            "õiguses, ülereguleerimise vältimine."
        ),
        sort_order=210,
    ),
    ReferencePolicyArea(
        key="olulised-tahtajad",
        name_et="Olulised tähtajad",
        description=(
            "Valdkondadeülene jälgimisnimekiri: teemad, mille ajakava on Koja "
            "jaoks kriitiline. Taksonoomia silt, mitte ühe teema operatiivne "
            "tähtaeg."
        ),
        sort_order=220,
    ),
    ReferencePolicyArea(
        key="arengukavad-strateegiad",
        name_et="Arengukavad, strateegiad",
        description=(
            "Riiklikud arengukavad, strateegiad ja pikaajalised kavad ning nende ettevõtlusmõju."
        ),
        sort_order=230,
    ),
)

#: The name the rest of the codebase imports. Version 1.0's nine reviewed areas
#: are not deleted from the database by the change of manifest — four of them
#: are in the list above under the same key, and the other five are deactivated
#: by `taxonomy/0003` and keep every relation they had. What the manifest
#: governs is which areas are *offered*, and that is now these twenty-three.
REFERENCE_POLICY_AREAS_V1: tuple[ReferencePolicyArea, ...] = REFERENCE_POLICY_AREAS_V2

#: The five version-1.0 keys whose names the working vocabulary does not
#: contain. Kept here as data because two places need to agree about them: the
#: migration that deactivates them, and the test that proves nothing remapped a
#: Matter filed under one of them. Never a mapping table — there is no
#: reviewed equivalence between any of these and any new label, and writing one
#: down is how a guess becomes a fact (Teema redesign §7.2).
RETIRED_POLICY_AREA_KEYS_V1: tuple[str, ...] = (
    "maksud",
    "toojoud",
    "halduskoormus",
    "aus-konkurents",
    "haridus-ettevotlikkus",
)

#: The stable keys, in reviewed order. Read by `seed_dev_data` so development
#: fixtures classify synthetic Matters with the *real* vocabulary instead of
#: growing a second one beside it.
REFERENCE_POLICY_AREA_KEYS: tuple[str, ...] = tuple(area.key for area in REFERENCE_POLICY_AREAS_V1)
