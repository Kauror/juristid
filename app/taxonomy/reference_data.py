"""The reviewed production vocabulary of Koda's policy areas.

Reference data, not a fixture. The distinction matters more here than anywhere
else in the repository, because both kinds of row end up in the same table.

``seed_dev_data`` invents ``Näidisministeerium`` and five provisional areas so a
developer has something to click. Those are *props*: wrong on purpose, so that
nobody mistakes a rehearsal for the real register. What is below is the
opposite — the actual classification the Chamber uses in public, which belongs
in a real deployment and would be useless as an invention.

**Where these nine come from.** Eesti Kaubandus-Tööstuskoda publishes what it
works on under "Meie mõju ja eesmärk" → "Millega tegeleme?"
(https://www.koda.ee/et/meie-moju, read 2026-08-23). It lists nine focus areas
and this manifest is those nine, in the order the page gives them. Three public
headings are sentences rather than labels, so the database name is the concept
and the heading is recorded beside it:

============================  ==========================================
manifest name                 public heading
============================  ==========================================
Halduskoormus                 Võitlus halduskoormusega
Aus konkurents                Aus konkurentsikeskkond
Haridus ja ettevõtlikkus      Hariduse ja ettevõtlikkuse edendamine
============================  ==========================================

The other six are already single words on the page and are taken verbatim.

**Why the website is evidence and not a dependency.** Nothing at runtime reads
koda.ee. A page that Koda's communications team rewords must not silently
reclassify a decade of filing; changing this vocabulary is a code change with a
migration behind it, reviewed like any other. The URL and date above exist so a
reviewer can check the claim, not so a cron job can act on it.

**Why exactly these and nothing else.** Pension, käibemaks, AI,
kestlikkusaruandlus, välistööjõud and a dozen other genuinely important topics
are deliberately absent. ``PolicyArea`` is the small stable reporting
classification — the axis a yearly report is cut along — and it stops being that
the moment it grows a row per subject. Narrower concepts are ``Tag``, or they
are Matters inside one of these nine. Adding a tenth area is a business
decision, not a convenience.

**Why they overlap.** ``Matter.policy_areas`` is many-to-many because a file
about an energy tax genuinely is both Maksud and Energeetika, and a reporting
burden in an environmental permit is both Keskkond and Halduskoormus. These nine
are not a partition and must not be forced into one.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Bumped when the *set* of reviewed areas changes, not when wording is fixed.
#: Recorded in the reference-data plan digest so a plan built under one
#: vocabulary can never be applied under another.
REFERENCE_POLICY_AREA_VERSION = "1.0"

#: Where the business list was read, and when. Quoted in the ADR and asserted by
#: the source-contract test, so that changing the vocabulary without changing
#: this provenance fails review.
POLICY_AREA_SOURCE_TITLE = "Meie mõju ja eesmärk — Millega tegeleme?"
POLICY_AREA_SOURCE_PUBLISHER = "Eesti Kaubandus-Tööstuskoda"
POLICY_AREA_SOURCE_URL = "https://www.koda.ee/et/meie-moju"
POLICY_AREA_SOURCE_VERIFIED_ON = "2026-08-23"


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


REFERENCE_POLICY_AREAS_V1: tuple[ReferencePolicyArea, ...] = (
    ReferencePolicyArea(
        key="maksud",
        name_et="Maksud",
        description=(
            "Maksud, maksuhaldus ja maksutaolised tasud. Siia kuulub ka "
            "maksumenetlus ja maksuaruandlus ise; puhtalt aruandluskoormuse "
            "küsimus ilma maksusisuta on Halduskoormus."
        ),
        sort_order=10,
    ),
    ReferencePolicyArea(
        key="toojoud",
        name_et="Tööjõud",
        description=(
            "Tööõigus, töösuhted, kutse- ja kvalifikatsiooninõuded ning "
            "välistööjõud. Oskuste ja õppe pool kuulub Hariduse ja "
            "ettevõtlikkuse alla."
        ),
        sort_order=20,
    ),
    ReferencePolicyArea(
        key="keskkond",
        name_et="Keskkond",
        description=(
            "Keskkonna-, kliima- ja ressursiregulatsioon: jäätmed ja pakend, "
            "vesi, keskkonnatasud, keskkonnamõju hindamine. Energiaturu ja "
            "varustuskindluse küsimused on Energeetika."
        ),
        sort_order=30,
    ),
    ReferencePolicyArea(
        key="energeetika",
        name_et="Energeetika",
        description=(
            "Energiaturg, varustuskindlus, võrgud ja taristu ning ettevõtja "
            "energiakulu. Kliimaeesmärk ise on Keskkond; sama eelnõu võib "
            "kuuluda mõlemasse."
        ),
        sort_order=40,
    ),
    ReferencePolicyArea(
        key="halduskoormus",
        name_et="Halduskoormus",
        description=(
            "Läbiv aruandlus-, menetlus- ja bürokraatiakoormus: uued kohustused, "
            "nende kaotamine ja piirmäärad. Valdkonnaülene — enamasti koos selle "
            "valdkonnaga, mille koormusest jutt käib."
        ),
        sort_order=50,
        public_heading="Võitlus halduskoormusega",
    ),
    ReferencePolicyArea(
        key="aus-konkurents",
        name_et="Aus konkurents",
        description=(
            "Aus konkurentsiolukord: varimajandus, ebavõrdsed tingimused, "
            "turu läbipaistvus ja järelevalve. Käsitleb turgu tervikuna, mitte "
            "üksikut vaidlust."
        ),
        sort_order=60,
        public_heading="Aus konkurentsikeskkond",
    ),
    ReferencePolicyArea(
        key="arioigus",
        name_et="Äriõigus",
        description=(
            "Äri- ja tsiviilõiguslik raamistik: äriühinguõigus, registrid, "
            "lepingu- ja võlaõigus, maksejõuetus. Maksuõigus on Maksud."
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
        key="haridus-ettevotlikkus",
        name_et="Haridus ja ettevõtlikkus",
        description=(
            "Haridussüsteem, oskused, ettevõtlikkus ja hariduse vastavus "
            "tööturu vajadustele. Tööõiguse ja töösuhte küsimused on Tööjõud."
        ),
        sort_order=90,
        public_heading="Hariduse ja ettevõtlikkuse edendamine",
    ),
)

#: The stable keys, in reviewed order. Read by `seed_dev_data` so development
#: fixtures classify synthetic Matters with the *real* vocabulary instead of
#: growing a second one beside it.
REFERENCE_POLICY_AREA_KEYS: tuple[str, ...] = tuple(area.key for area in REFERENCE_POLICY_AREAS_V1)
