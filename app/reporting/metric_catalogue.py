"""The catalogue: every published metric, defined once, in code.

This file is the reviewable artefact behind the Statistika workspace. A number
appears on a page only if it has an entry here, and changing what a number
means is a change to this file — reviewed, diffed and versioned — rather than a
setting somebody adjusted (docs/adr/0007, docs/adr/0017,
master specification 18.5).

There is deliberately no admin screen for writing formulas. A metric whose
definition can be edited through the product is a metric with no reviewed
definition, and "who changed the population" becomes unanswerable exactly when
somebody disputes a figure in a board paper.

Three conventions run through the entries below.

**The definition is enforced, not described.** ``eligible_record_modes`` and
``eligible_origins`` are read by ``selectors.base.eligible_matters``; the
thresholds are read by ``metric_types.grade``. Nothing here is prose that the
code might disagree with.

**Era boundaries are stated, not smoothed.** The register begins in 2011,
`KELLELT` means the sender until 2019 and `KELLELE` the addressee from 2020,
structured `Submission` records begin only with this system, and the OneNote
archive's exact identity match is reliable mainly from 2020. Any metric whose
trend crosses one of those says so.

**A missing measurement is not a zero.** ``earliest_reliable_period`` stops a
chart drawing 2011–2025 flatlines for something that was never recorded, which
is the single most convincing way to publish a false fact (brief 24).
"""

from __future__ import annotations

from app.documents.enums import ExtractionState
from app.matters.enums import MatterOrigin, RecordMode
from app.reporting.metric_types import MetricDefinition, TimeBasis, Unit

#: The first year the Excel register covers. Nothing derived from it may be
#: published for an earlier period, and no trend line may start before it.
REGISTER_FIRST_YEAR = 2011

_ERA_ORGANISATION = (
    "Registris tähendas veerg KELLELT aastatel 2011–2019 saatjat ja KELLELE "
    "aastatel 2020–2026 adressaati. Need on eri faktid ja neid ei liideta."
)
_ERA_SUBMISSION = (
    "Ajaloolised arvamused on taastatud arvamuste arhiivist ja need on olemas "
    "alates 2020. aastast. Varasemate aastate kohta arhiivis materjali ei ole, "
    "seega mõõtmist ei ole — see ei tähenda, et arvamusi ei saadetud. Ka "
    "2020+ katvus ei ole täielik: osa arhiivi failidest ei ole veel üheselt "
    "teemaga seotud ja ootab ülevaatust."
)

#: The first year the opinions archive holds anything at all. Measured, not
#: assumed: the register begins in 2011, the archive does not, and reporting a
#: 2014 trend from a corpus that starts in 2020 would present absence as zero
#: (Stage-2H brief 52, 54).
OPINION_ARCHIVE_FIRST_YEAR = 2020
_ERA_ONENOTE_YEAR = (
    "OneNote'i-põhistel teemadel puudub registri aruandlusaasta; nad on rühmas „Teadmata aasta“."
)
_ARCHIVE_SPARSITY = (
    "Arhiivikirjetel puuduvad tänapäevased väljad sageli õigustatult — see on "
    "katvuse piirang, mitte viga."
)

# ---------------------------------------------------------------------------
# Metric keys
#
# Constants rather than string literals at the call sites, so a typo is an
# ImportError at startup instead of a card that silently never renders.
# ---------------------------------------------------------------------------

MATTERS_TOTAL = "MATTERS_TOTAL"
MATTERS_BY_REPORTING_YEAR = "MATTERS_BY_REPORTING_YEAR"
ACTIVE_FULL_MATTERS = "ACTIVE_FULL_MATTERS"
MATTERS_BY_RECORD_MODE = "MATTERS_BY_RECORD_MODE"
MATTERS_BY_ORIGIN = "MATTERS_BY_ORIGIN"
MATTERS_BY_STAGE = "MATTERS_BY_STAGE"
MATTERS_BY_OWNER = "MATTERS_BY_OWNER"
MATTERS_BY_RESPONSIBILITY = "MATTERS_BY_RESPONSIBILITY"
MATTERS_BY_YEAR_AND_RESPONSIBILITY = "MATTERS_BY_YEAR_AND_RESPONSIBILITY"
ACTIVE_FULL_MATTERS_BY_STAGE = "ACTIVE_FULL_MATTERS_BY_STAGE"
ACTIVE_FULL_MATTERS_BY_RESPONSIBILITY = "ACTIVE_FULL_MATTERS_BY_RESPONSIBILITY"
MATTERS_BY_POLICY_AREA = "MATTERS_BY_POLICY_AREA"
MATTERS_UNCLASSIFIED_POLICY_AREA = "MATTERS_UNCLASSIFIED_POLICY_AREA"
MATTERS_BY_TRACK = "MATTERS_BY_TRACK"
MATTERS_BY_TAG = "MATTERS_BY_TAG"
MATTERS_WITH_HISTORICAL_SOURCE = "MATTERS_WITH_HISTORICAL_SOURCE"
MATTERS_WITHOUT_HISTORICAL_SOURCE = "MATTERS_WITHOUT_HISTORICAL_SOURCE"
ONENOTE_ONLY_MATTERS = "ONENOTE_ONLY_MATTERS"
MATTERS_WITH_MULTIPLE_SOURCE_PAGES = "MATTERS_WITH_MULTIPLE_SOURCE_PAGES"
HISTORICAL_SOURCE_COVERAGE_CLASSES = "HISTORICAL_SOURCE_COVERAGE_CLASSES"

SUBMISSIONS_SENT = "SUBMISSIONS_SENT"
SUBMISSIONS_SENT_BY_PERIOD = "SUBMISSIONS_SENT_BY_PERIOD"
SUBMISSIONS_BY_RECIPIENT = "SUBMISSIONS_BY_RECIPIENT"
SUBMISSIONS_BY_KIND = "SUBMISSIONS_BY_KIND"
MATTERS_BY_SUBMISSION_COUNT = "MATTERS_BY_SUBMISSION_COUNT"
MATTERS_WITH_MULTIPLE_SUBMISSIONS = "MATTERS_WITH_MULTIPLE_SUBMISSIONS"

NEW_NATIVE_FULL_MATTERS = "NEW_NATIVE_FULL_MATTERS"
NEW_NATIVE_FULL_MATTERS_BY_MONTH = "NEW_NATIVE_FULL_MATTERS_BY_MONTH"
NEW_NATIVE_MATTERS_BY_RESPONSIBILITY_MONTH = "NEW_NATIVE_MATTERS_BY_RESPONSIBILITY_MONTH"
NEW_NATIVE_MATTERS_YOY_CHANGE = "NEW_NATIVE_MATTERS_YOY_CHANGE"
ACTIVE_WITHOUT_NEXT_ACTION = "ACTIVE_WITHOUT_NEXT_ACTION"
ACTIVE_WITHOUT_OWNER = "ACTIVE_WITHOUT_OWNER"
OVERDUE_DO_DEADLINE = "OVERDUE_DO_DEADLINE"
REVIEW_DUE = "REVIEW_DUE"
RESPONSE_DEADLINES_OPEN = "RESPONSE_DEADLINES_OPEN"
ENTRY_COUNT = "ENTRY_COUNT"
ENTRY_COUNT_BY_KIND = "ENTRY_COUNT_BY_KIND"

MATTERS_BY_SOURCE_ORGANISATION = "MATTERS_BY_SOURCE_ORGANISATION"
MATTERS_BY_ADDRESSEE_ORGANISATION = "MATTERS_BY_ADDRESSEE_ORGANISATION"

LEGACY_SOURCE_PAGES = "LEGACY_SOURCE_PAGES"
LEGACY_SOURCE_PAGES_BY_SECTION = "LEGACY_SOURCE_PAGES_BY_SECTION"
LEGACY_SOURCE_PAGES_BY_YEAR = "LEGACY_SOURCE_PAGES_BY_YEAR"
LEGACY_SOURCE_PAGES_BY_ROLE = "LEGACY_SOURCE_PAGES_BY_ROLE"
HISTORICAL_RESOURCE_OCCURRENCES = "HISTORICAL_RESOURCE_OCCURRENCES"
HISTORICAL_RESOURCE_BYTES = "HISTORICAL_RESOURCE_BYTES"
HISTORICAL_RESOURCES_BY_TYPE = "HISTORICAL_RESOURCES_BY_TYPE"
HISTORICAL_UNIQUE_BINARY_CONTENTS = "HISTORICAL_UNIQUE_BINARY_CONTENTS"
HISTORICAL_EMAIL_RESOURCES = "HISTORICAL_EMAIL_RESOURCES"
HISTORICAL_SIGNED_CONTAINERS = "HISTORICAL_SIGNED_CONTAINERS"
RESOURCES_PER_PAGE = "RESOURCES_PER_PAGE"
RESOURCES_PER_MATTER = "RESOURCES_PER_MATTER"
SOURCE_PAGE_TEXT_LENGTH = "SOURCE_PAGE_TEXT_LENGTH"
MATERIALISATION_STATUS = "MATERIALISATION_STATUS"

EXTRACTION_ELIGIBLE = "EXTRACTION_ELIGIBLE"
EXTRACTION_SUCCESS = "EXTRACTION_SUCCESS"
EXTRACTION_PENDING = "EXTRACTION_PENDING"
EXTRACTION_FAILED = "EXTRACTION_FAILED"
EXTRACTION_NOT_APPLICABLE = "EXTRACTION_NOT_APPLICABLE"
EXTRACTION_AWAITING_SCANNER = "EXTRACTION_AWAITING_SCANNER"
SEARCHABLE_DOCUMENT_COVERAGE = "SEARCHABLE_DOCUMENT_COVERAGE"

OPINION_ARCHIVE_OCCURRENCES = "OPINION_ARCHIVE_OCCURRENCES"
OPINION_ARCHIVE_DISTINCT_BINARIES = "OPINION_ARCHIVE_DISTINCT_BINARIES"
OPINION_ARCHIVE_BY_YEAR = "OPINION_ARCHIVE_BY_YEAR"
OPINION_ARCHIVE_BY_MONTH = "OPINION_ARCHIVE_BY_MONTH"
OPINION_ARCHIVE_YOY_CHANGE = "OPINION_ARCHIVE_YOY_CHANGE"
OPINION_ARCHIVE_LINK_COVERAGE = "OPINION_ARCHIVE_LINK_COVERAGE"
OPINION_ARCHIVE_LINKED_BY_RESPONSIBILITY = "OPINION_ARCHIVE_LINKED_BY_RESPONSIBILITY"
OPINION_ARCHIVE_LINKED_BY_MONTH_RESPONSIBILITY = "OPINION_ARCHIVE_LINKED_BY_MONTH_RESPONSIBILITY"

OPINION_ARCHIVE_MATTER_COVERAGE = "OPINION_ARCHIVE_MATTER_COVERAGE"
OPINION_ARCHIVE_UNRESOLVED = "OPINION_ARCHIVE_UNRESOLVED"
HISTORICAL_SUBMISSION_COVERAGE = "HISTORICAL_SUBMISSION_COVERAGE"
SUBMISSION_RECIPIENT_COVERAGE = "SUBMISSION_RECIPIENT_COVERAGE"

RECONCILIATION_PENDING = "RECONCILIATION_PENDING"
RECONCILIATION_CONFLICT = "RECONCILIATION_CONFLICT"
RECONCILIATION_BY_CLASS = "RECONCILIATION_BY_CLASS"
READING_ORDER_AMBIGUOUS = "READING_ORDER_AMBIGUOUS"
UNLINKED_SUBSTANTIVE_PAGES = "UNLINKED_SUBSTANTIVE_PAGES"
MATERIALISATION_FAILED = "MATERIALISATION_FAILED"
ACTIVE_WITHOUT_STAGE = "ACTIVE_WITHOUT_STAGE"
DATA_QUALITY_ATTENTION = "DATA_QUALITY_ATTENTION"


def _matter(
    key: str,
    label: str,
    description: str,
    *,
    population: str,
    time_basis: TimeBasis = TimeBasis.REPORTING_YEAR,
    version: int = 1,
    **kwargs: object,
) -> MetricDefinition:
    """A Matter-population definition with this file's shared defaults."""
    return MetricDefinition(
        key=key,
        version=version,
        label_et=label,
        description_et=description,
        source_population_et=population,
        time_basis=time_basis,
        unit=Unit.MATTERS,
        **{"drillthrough_et": "Teemade register, samade filtritega", **kwargs},  # type: ignore[arg-type]
    )


_DEFINITIONS: tuple[MetricDefinition, ...] = (
    # -- Teemad ------------------------------------------------------------
    _matter(
        MATTERS_TOTAL,
        "Teemasid perioodil",
        "Kõik nähtavad teemad, mille aruandlusaasta langeb valitud perioodi.",
        population="Nähtavad teemad, mille aruandlusaasta on teada",
        earliest_reliable_period=REGISTER_FIRST_YEAR,
        source_era_limitations_et=_ERA_ONENOTE_YEAR,
        coverage_description_et="Teemad, millel on registri aruandlusaasta",
        minimum_coverage=0.0,
    ),
    _matter(
        MATTERS_BY_REPORTING_YEAR,
        "Teemad aastate kaupa",
        "Teemade arv aruandlusaasta järgi, ilma andmeteta aastaid välja jätmata.",
        population="Nähtavad teemad",
        earliest_reliable_period=REGISTER_FIRST_YEAR,
        source_era_limitations_et=_ERA_ONENOTE_YEAR,
        coverage_description_et="Teemad, millel on registri aruandlusaasta",
        minimum_coverage=0.0,
        notes_et="Aastad, mille kohta ühtki kirjet ei ole, jäetakse teljelt välja.",
    ),
    _matter(
        ACTIVE_FULL_MATTERS,
        "Aktiivseid teemasid",
        "Avatud täielikud teemad praegu. Arhiivikirjed ei ole aktiivne töö.",
        population="Avatud teemad, kirje liik FULL",
        time_basis=TimeBasis.POINT_IN_TIME,
        eligible_record_modes=(RecordMode.FULL.value,),
        required_fields=("is_open", "record_mode"),
        exclusions_et="Arhiivikirjed; suletud teemad",
        respects_period=False,
    ),
    _matter(
        MATTERS_BY_RECORD_MODE,
        "Teemad kirje liigi järgi",
        "Täielik operatiivne kirje või ajalooline arhiivirida.",
        population="Nähtavad teemad",
    ),
    _matter(
        MATTERS_BY_ORIGIN,
        "Teemad päritolu järgi",
        "Kust kirje pärineb: süsteemist, Exceli registrist või OneNote'ist.",
        population="Nähtavad teemad",
        source_era_limitations_et=(
            "OneNote'i-põhised teemad tekkisid ainult ajaloolise korpuse impordist."
        ),
    ),
    _matter(
        MATTERS_BY_STAGE,
        "Teemad hetkeseisu järgi",
        "Kus väline menetlus seisab. Ei ole lõpetamise põhjus.",
        population="Nähtavad teemad, millel on hetkeseis",
        coverage_description_et="Teemad, millel on hetkeseis määratud",
        minimum_coverage=0.0,
        source_era_limitations_et=(
            "Hetkeseisu veerg ilmus registrisse alles 2023. aastal. " + _ARCHIVE_SPARSITY
        ),
    ),
    _matter(
        MATTERS_BY_OWNER,
        "Teemad vastutaja järgi",
        (
            "Portfelli jaotus. See on inventuur, mitte töökoormus ega "
            "tulemuslikkus: avatud teemade arv ei mõõda tehtud tööd."
        ),
        population="Nähtavad teemad",
        coverage_description_et="Teemad, millel on vastutaja määratud",
        minimum_coverage=0.0,
        notes_et="Juriste ei järjestata ega võrrelda.",
        source_era_limitations_et=_ARCHIVE_SPARSITY,
    ),
    _matter(
        MATTERS_BY_RESPONSIBILITY,
        "Teemad vastutuse järgi",
        (
            "Portfelli jaotus vastutaja järgi, nii nagu allikas teda nimetab: "
            "registri VASTUTAJA tekst, selle puudumisel kanooniline vastutaja. "
            "See on inventuur, mitte töökoormus, tulemuslikkus ega juristide "
            "järjestus."
        ),
        population="Nähtavad teemad",
        coverage_description_et="Teemad, millel on vastutaja nimi teada",
        minimum_coverage=0.0,
        source_era_limitations_et=(
            "Registris nimetatud kolleegil ei pruugi olla siinset kasutajakontot. "
            "Sel juhul säilib allika nimi ja teda ei liideta rühma „Määramata“."
        ),
        notes_et=(
            "Juriste ei järjestata ega võrrelda. Erineb näitajast „Teemad "
            "vastutaja järgi“, mis rühmitab ainult kanoonilise kasutajakonto "
            "järgi ja avab registri loendi."
        ),
        drillthrough_et=(
            "Loendit ei avata: register filtreerib lahendatud vastutaja järgi, "
            "siin on rühmitatud allika nime järgi, ja link avaks teistsuguse hulga."
        ),
    ),
    _matter(
        MATTERS_BY_YEAR_AND_RESPONSIBILITY,
        "Teemad aastate ja vastutuse kaupa",
        (
            "Kui palju teemasid on iga aasta kohta millise vastutaja all. "
            "Tabel, mitte edetabel: read on aastad, veerud vastutajad tähestiku "
            "järjekorras, ja arv on inventuur, mitte tehtud töö maht."
        ),
        population="Nähtavad teemad, millel on registri aruandlusaasta",
        earliest_reliable_period=REGISTER_FIRST_YEAR,
        source_era_limitations_et=_ERA_ONENOTE_YEAR,
        coverage_description_et="Teemad, mis mahuvad aastatelgele",
        minimum_coverage=0.0,
        notes_et=(
            "Aastad, mille kohta kirjeid ei ole, jäetakse tabelist välja. Kui "
            "vastutajate nimesid on rohkem, kui tabel loetavalt mahutab, "
            "koondatakse ülejäänud eraldi märgistatud veergu ja tabel ütleb, "
            "mitu nime seal on."
        ),
        drillthrough_et="Loendit ei avata: registril ei ole aasta × vastutaja filtrit.",
    ),
    _matter(
        ACTIVE_FULL_MATTERS_BY_STAGE,
        "Aktiivsed teemad hetkeseisu järgi",
        (
            "Kus praegu avatud täielike teemade väline menetlus seisab. "
            "Arhiivikirjed ei ole aktiivne töö ega kuulu siia."
        ),
        population="Avatud teemad, kirje liik FULL",
        time_basis=TimeBasis.POINT_IN_TIME,
        eligible_record_modes=(RecordMode.FULL.value,),
        required_fields=("is_open", "record_mode"),
        exclusions_et="Arhiivikirjed; suletud teemad",
        respects_period=False,
        coverage_description_et="Aktiivsed teemad, millel on hetkeseis määratud",
        minimum_coverage=0.0,
        notes_et=(
            "Erineb näitajast „Teemad hetkeseisu järgi“, mis järgib valitud "
            "perioodi ja sisaldab ka arhiivikirjeid, millel hetkeseisu "
            "õigustatult ei ole."
        ),
        drillthrough_et="Teemade register: avatud, kirje liik FULL, valitud hetkeseis",
    ),
    _matter(
        ACTIVE_FULL_MATTERS_BY_RESPONSIBILITY,
        "Aktiivsed teemad vastutuse järgi",
        (
            "Praegune portfell vastutajate kaupa, allika nimetuse järgi. "
            "Hetkeseisu inventuur, mitte töökoormus ega tulemuslikkus."
        ),
        population="Avatud teemad, kirje liik FULL",
        time_basis=TimeBasis.POINT_IN_TIME,
        eligible_record_modes=(RecordMode.FULL.value,),
        respects_period=False,
        coverage_description_et="Aktiivsed teemad, millel on vastutaja nimi teada",
        minimum_coverage=0.0,
        source_era_limitations_et=(
            "Kui register nimetab vastutajat, kellel siinset kontot ei ole, "
            "säilib allika nimi. Teda ei loeta määramata vastutajaks."
        ),
        notes_et="Juriste ei järjestata ega võrrelda.",
        drillthrough_et=(
            "Loendit ei avata: register filtreerib lahendatud vastutaja järgi, "
            "siin on rühmitatud allika nime järgi."
        ),
    ),
    _matter(
        MATTERS_BY_POLICY_AREA,
        "Teemad valdkondade järgi",
        "Kanooniline valdkonnaklassifikatsioon koos katvusega.",
        population="Nähtavad teemad",
        coverage_description_et="Teemad, millele on määratud vähemalt üks valdkond",
        minimum_coverage=0.0,
        notes_et=(
            "OneNote'i sektsioon ei ole valdkond. Ajaloolist jaotust vaata "
            "eraldi ajaloolise materjali vaatest."
        ),
        source_era_limitations_et=_ARCHIVE_SPARSITY,
    ),
    _matter(
        MATTERS_UNCLASSIFIED_POLICY_AREA,
        "Valdkonnata teemasid",
        "Teemad, millel ei ole ühtki valdkonda. Ei jäeta nimetajast välja.",
        population="Nähtavad teemad",
        source_era_limitations_et=_ARCHIVE_SPARSITY,
    ),
    _matter(
        MATTERS_BY_TRACK,
        "Teemad menetlusliigi järgi",
        "Riigisisene, ELi algatus, ülevõtmine, strateegia või Koja algatus.",
        population="Nähtavad teemad, millel on menetlusliik",
        coverage_description_et="Teemad, millel on menetlusliik määratud",
        minimum_coverage=0.0,
        source_era_limitations_et=(
            "Register ei sisaldanud menetlusliiki; see täidetakse selles süsteemis."
        ),
    ),
    _matter(
        MATTERS_BY_TAG,
        "Teemad kinnitatud siltide järgi",
        "Ainult inimese kinnitatud sildid. Masina ettepanek ei ole seos.",
        population="Nähtavad teemad, millel on kinnitatud silt",
        coverage_description_et="Teemad, millele on kinnitatud vähemalt üks silt",
        minimum_coverage=0.0,
    ),
    _matter(
        MATTERS_WITH_HISTORICAL_SOURCE,
        "Ajaloolise materjaliga teemasid",
        "Teemad, mille küljes on vähemalt üks OneNote'i lähteleht.",
        population="Nähtavad teemad",
        time_basis=TimeBasis.WHOLE_CORPUS,
        respects_period=False,
        source_era_limitations_et=(
            "Täpne Exceli ja OneNote'i identiteedivaste on usaldusväärne peamiselt "
            "alates 2020. aastast."
        ),
        drillthrough_et="Teemade register, filtreeritud ajaloolise allika olemasolule",
    ),
    _matter(
        MATTERS_WITHOUT_HISTORICAL_SOURCE,
        "Ajaloolise materjalita teemasid",
        (
            "Teemad ilma OneNote'i lähteleheta. Vanal registrireal ei pruukinud "
            "kunagi OneNote'i lehte olla — see ei ole viga."
        ),
        population="Nähtavad teemad",
        time_basis=TimeBasis.WHOLE_CORPUS,
        respects_period=False,
        notes_et="Arhiivi hõredus ei ole andmekvaliteedi puudus.",
    ),
    _matter(
        ONENOTE_ONLY_MATTERS,
        "OneNote'i-põhiseid teemasid",
        (
            "Teemad, mis eksisteerivad ainult OneNote'i lehe tõttu. Neil ei ole "
            "registri viidet ega aruandlusaastat, ja see on õige."
        ),
        population="Nähtavad teemad, päritolu LEGACY_ONENOTE",
        # No period. These Matters have no register reporting year by
        # definition, so a year filter could only ever return none of them —
        # which would read as "there are none" rather than "the question does
        # not apply" (Stage-2E brief 15).
        time_basis=TimeBasis.WHOLE_CORPUS,
        respects_period=False,
        eligible_origins=(MatterOrigin.LEGACY_ONENOTE.value,),
        source_era_limitations_et=_ERA_ONENOTE_YEAR,
    ),
    _matter(
        MATTERS_WITH_MULTIPLE_SOURCE_PAGES,
        "Mitme lähtelehega teemasid",
        "Teemad, mille külge on seotud rohkem kui üks OneNote'i leht.",
        population="Nähtavad teemad, millel on vähemalt kaks lähtelehte",
        time_basis=TimeBasis.WHOLE_CORPUS,
        respects_period=False,
    ),
    _matter(
        HISTORICAL_SOURCE_COVERAGE_CLASSES,
        "Ajaloolise allika katvus",
        (
            "Registri rida koos OneNote'i allikaga, registri rida ilma selleta, "
            "ainult OneNote'i-põhine teema ja süsteemis loodud teema."
        ),
        population="Nähtavad teemad",
        time_basis=TimeBasis.WHOLE_CORPUS,
        respects_period=False,
    ),
    # -- Koja tegevus ------------------------------------------------------
    MetricDefinition(
        key=SUBMISSIONS_SENT,
        version=2,
        label_et="Saadetud arvamusi",
        description_et=(
            "Kanoonilised saadetud Submission-kirjed. Arvamust ei tuletata "
            "failinimest, PDF-ist ega registri VÄLJA-kuupäevast."
        ),
        source_population_et="Nähtavad arvamused olekus SAADETUD",
        time_basis=TimeBasis.SUBMISSION_SENT_AT,
        unit=Unit.SUBMISSIONS,
        required_fields=("sent_at", "final_version"),
        exclusions_et="Mustandid, tagasi võetud ja asendatud arvamused",
        source_era_limitations_et=_ERA_SUBMISSION,
        minimum_population=1,
        drillthrough_et="Arvamuste loend, samade filtritega",
    ),
    MetricDefinition(
        key=SUBMISSIONS_SENT_BY_PERIOD,
        version=2,
        earliest_reliable_period=OPINION_ARCHIVE_FIRST_YEAR,
        label_et="Saadetud arvamused ajas",
        description_et=(
            "Saadetud arvamused aastate kaupa. Enne süsteemi kasutuselevõttu "
            "mõõtmist ei ole ja tühje aastaid ei joonistata."
        ),
        source_population_et="Nähtavad arvamused olekus SAADETUD",
        time_basis=TimeBasis.SUBMISSION_SENT_AT,
        unit=Unit.SUBMISSIONS,
        source_era_limitations_et=_ERA_SUBMISSION,
        minimum_population=1,
        notes_et="Puuduv mõõtmine ei ole null.",
        drillthrough_et="Arvamuste loend, filtreeritud aasta järgi",
    ),
    MetricDefinition(
        key=SUBMISSIONS_BY_RECIPIENT,
        version=2,
        label_et="Arvamuste adressaadid",
        description_et=(
            "Kellele Koda ametlikult kirjutas. Ainult adressaadid — "
            "teadmiseks saajad on eraldi fakt ega kuulu siia."
        ),
        source_population_et="Saadetud arvamuste adressaatorganisatsioonid",
        time_basis=TimeBasis.SUBMISSION_SENT_AT,
        unit=Unit.SUBMISSIONS,
        exclusions_et="Rollis „teadmiseks“ olevad saajad",
        source_era_limitations_et=_ERA_SUBMISSION,
        minimum_population=1,
        drillthrough_et="Arvamuste loend, filtreeritud saaja järgi",
    ),
    MetricDefinition(
        key=SUBMISSIONS_BY_KIND,
        version=2,
        label_et="Arvamused liigi järgi",
        description_et="Ametlik arvamus, täiendav arvamus, pöördumine, ühispöördumine.",
        source_population_et="Nähtavad arvamused olekus SAADETUD",
        time_basis=TimeBasis.SUBMISSION_SENT_AT,
        unit=Unit.SUBMISSIONS,
        source_era_limitations_et=_ERA_SUBMISSION,
        notes_et=(
            "Milliseid liike aastaaruande „kirjalike arvamuste“ arv sisaldab, "
            "on lahtine ärialane otsus."
        ),
    ),
    _matter(
        MATTERS_BY_SUBMISSION_COUNT,
        "Teemad arvamuste arvu järgi",
        "Mitu saadetud arvamust ühel teemal on: 0, 1 või 2 ja rohkem.",
        population="Nähtavad täielikud teemad",
        version=2,
        eligible_record_modes=(RecordMode.FULL.value,),
        source_era_limitations_et=_ERA_SUBMISSION,
        notes_et=(
            "„0 arvamust“ tähendab nüüd sageli, et arhiivist ei õnnestunud ühtki "
            "faili selle teemaga üheselt siduda, mitte et arvamust ei saadetud."
        ),
    ),
    _matter(
        MATTERS_WITH_MULTIPLE_SUBMISSIONS,
        "Mitme arvamusega teemasid",
        "Teemad, millelt on saadetud rohkem kui üks arvamus.",
        population="Nähtavad täielikud teemad",
        version=2,
        eligible_record_modes=(RecordMode.FULL.value,),
        source_era_limitations_et=_ERA_SUBMISSION,
    ),
    _matter(
        NEW_NATIVE_FULL_MATTERS,
        "Uusi teemasid perioodil",
        "Süsteemis loodud täielikud teemad, mille saabumise kuupäev on perioodis.",
        population="Nähtavad teemad, kirje liik FULL, päritolu NATIVE",
        time_basis=TimeBasis.RECEIVED_DATE,
        eligible_record_modes=(RecordMode.FULL.value,),
        eligible_origins=(MatterOrigin.NATIVE.value,),
        required_fields=("received_date",),
        coverage_description_et="Teemad, millel on saabumise kuupäev",
        minimum_coverage=0.0,
    ),
    _matter(
        NEW_NATIVE_FULL_MATTERS_BY_MONTH,
        "Uued teemad kuude kaupa",
        (
            "Süsteemis loodud täielikud teemad saabumise kuu järgi. Sama "
            "populatsioon ja sama kell nagu näitajal „Uusi teemasid perioodil“."
        ),
        population="Nähtavad teemad, kirje liik FULL, päritolu NATIVE, saabumise kuupäev teada",
        time_basis=TimeBasis.RECEIVED_DATE,
        eligible_record_modes=(RecordMode.FULL.value,),
        eligible_origins=(MatterOrigin.NATIVE.value,),
        required_fields=("received_date",),
        exclusions_et=(
            "Imporditud registriread: allikas annab ainult aruandlusaasta, mitte "
            "kuu, ja kuu täpsust ei tuletata."
        ),
        notes_et=(
            "Telg algab esimesel ja lõpeb viimasel mõõdetud kuupäeval. Vahepealne "
            "tühi kuu on mõõdetud null; akna taha tulpa ei joonistata."
        ),
        drillthrough_et=(
            "Loendit ei avata. Registril on aasta-, mitte kuufilter, ja "
            "„saabumise kuupäev on teada“ ei ole filter — avanev loend oleks "
            "pikem kui number selle kohal."
        ),
    ),
    _matter(
        NEW_NATIVE_MATTERS_BY_RESPONSIBILITY_MONTH,
        "Uued teemad kuude ja vastutuse kaupa",
        (
            "Kes sai millisel kuul uusi teemasid. Saabunud töö jaotus, mitte "
            "tehtud töö maht ega juristide võrdlus."
        ),
        population="Nähtavad teemad, kirje liik FULL, päritolu NATIVE, saabumise kuupäev teada",
        time_basis=TimeBasis.RECEIVED_DATE,
        eligible_record_modes=(RecordMode.FULL.value,),
        eligible_origins=(MatterOrigin.NATIVE.value,),
        required_fields=("received_date",),
        exclusions_et="Imporditud registriread, millel kuud ei ole",
        notes_et=(
            "Veerud on tähestiku järjekorras, määramata vastutaja viimasena. "
            "Suuruse järgi ei järjestata."
        ),
        drillthrough_et="Loendit ei avata: registril ei ole kuu × vastutaja filtrit.",
    ),
    _matter(
        NEW_NATIVE_MATTERS_YOY_CHANGE,
        "Uute teemade muutus eelmise aastaga",
        (
            "Käesolev aasta tänase kuupäevani, võrrelduna eelmise aasta sama "
            "ajavahemikuga. Mahu muutus, mitte tulemuslikkus."
        ),
        population="Nähtavad teemad, kirje liik FULL, päritolu NATIVE, saabumise kuupäev teada",
        time_basis=TimeBasis.RECEIVED_DATE,
        eligible_record_modes=(RecordMode.FULL.value,),
        eligible_origins=(MatterOrigin.NATIVE.value,),
        required_fields=("received_date",),
        respects_period=False,
        minimum_population=1,
        notes_et=(
            "Mõlemad pooled on lõigatud samal kuupäeval: osalist aastat ei "
            "võrrelda terve aastaga. Kui eelmises võrreldavas perioodis ei olnud "
            "ühtki teemat, näidatakse ainult absoluutset vahet — protsenti "
            "nullist ei arvutata."
        ),
        drillthrough_et=(
            "Loendit ei avata: registril ei ole filtrit „saabus 1. jaanuarist "
            "tänaseni“, ja lähem filter avaks numbrist erineva hulga."
        ),
    ),
    _matter(
        ACTIVE_WITHOUT_NEXT_ACTION,
        "Aktiivseid ilma järgmise tegevuseta",
        "Avatud täielikud teemad, millel ei ole ühtki kehtivat järgmist tegevust.",
        population="Avatud teemad, kirje liik FULL",
        time_basis=TimeBasis.POINT_IN_TIME,
        eligible_record_modes=(RecordMode.FULL.value,),
        exclusions_et="Arhiivikirjed — neil ei peagi järgmist tegevust olema",
        respects_period=False,
    ),
    _matter(
        ACTIVE_WITHOUT_OWNER,
        "Aktiivseid ilma vastutajata",
        "Avatud täielikud teemad, millel ei ole vastutajat.",
        population="Avatud teemad, kirje liik FULL",
        time_basis=TimeBasis.POINT_IN_TIME,
        eligible_record_modes=(RecordMode.FULL.value,),
        respects_period=False,
    ),
    _matter(
        ACTIVE_WITHOUT_STAGE,
        "Aktiivseid ilma hetkeseisuta",
        "Avatud täielikud teemad, millel ei ole menetlusetappi määratud.",
        population="Avatud teemad, kirje liik FULL",
        time_basis=TimeBasis.POINT_IN_TIME,
        eligible_record_modes=(RecordMode.FULL.value,),
        respects_period=False,
    ),
    MetricDefinition(
        key=OVERDUE_DO_DEADLINE,
        version=1,
        label_et="Tähtaeg möödas",
        description_et=(
            "Ainult tähtajaks tehtav töö saab hilineda. Kuupäev, mis tähendab "
            "ülevaatust või oodatavat aega, ei ole tähtaeg ja seda ei nimetata "
            "kunagi hilinenuks."
        ),
        # The population statement names the stored values, not a label: `DO`
        # and `DEADLINE` are the columns a reviewer would query, and they are
        # exactly what did not change when the classification stopped being
        # shown to readers (ADR 0054).
        source_population_et="Kehtivad järgmised tegevused, liik DO, kuupäeva tähendus DEADLINE",
        time_basis=TimeBasis.POINT_IN_TIME,
        required_fields=("target_date",),
        respects_period=False,
        drillthrough_et="Minu töö või teemade register",
    ),
    #: One number, not two. `WAIT_REVIEW_DUE` and `MONITOR_REVIEW_DUE` counted
    #: the same event — a review date that has arrived — and differed only by
    #: the stored kind, which is not a distinction a reader is asked to make.
    #: Their sum is this (ADR 0054).
    MetricDefinition(
        key=REVIEW_DUE,
        version=1,
        label_et="Ülevaatus käes",
        description_et=(
            "Kehtivad tegevused, mille ülevaatuse kuupäev on saabunud. Ei ole hilinemine."
        ),
        source_population_et="Kehtivad järgmised tegevused, liik WAIT või MONITOR, kuupäevaga",
        time_basis=TimeBasis.POINT_IN_TIME,
        respects_period=False,
        drillthrough_et="Teemade register",
    ),
    _matter(
        RESPONSE_DEADLINES_OPEN,
        "Arvamuse tähtaegu ees",
        "Avatud täielikud teemad, mille arvamuse tähtaeg on veel tulemas.",
        population="Avatud teemad, kirje liik FULL, arvamuse tähtajaga",
        time_basis=TimeBasis.POINT_IN_TIME,
        eligible_record_modes=(RecordMode.FULL.value,),
        respects_period=False,
    ),
    MetricDefinition(
        key=ENTRY_COUNT,
        version=1,
        label_et="Sissekandeid",
        description_et="Juristide kirjutatud kronoloogia kanded, toimumise aja järgi.",
        source_population_et="Nähtavad sissekanded",
        time_basis=TimeBasis.ENTRY_OCCURRED_AT,
        source_era_limitations_et=(
            "Sissekandeid on ainult selle süsteemi kasutusajast. OneNote'i lehed "
            "ei ole sissekanded."
        ),
    ),
    MetricDefinition(
        key=ENTRY_COUNT_BY_KIND,
        version=1,
        label_et="Sissekanded liigi järgi",
        description_et="Märkus, kohtumine, kõne, istung, töörühm, avalik esinemine.",
        source_population_et="Nähtavad sissekanded",
        time_basis=TimeBasis.ENTRY_OCCURRED_AT,
        source_era_limitations_et=("Sissekandeid on ainult selle süsteemi kasutusajast."),
    ),
    # -- Organisatsioonid --------------------------------------------------
    _matter(
        MATTERS_BY_SOURCE_ORGANISATION,
        "Teemad algataja või saatja järgi",
        "Kellelt teema tuli. Ei ole sama, mis adressaat.",
        population="Nähtavad teemad, millel on algataja või saatja",
        coverage_description_et="Teemad, millel on algataja või saatja määratud",
        minimum_coverage=0.0,
        source_era_limitations_et=_ERA_ORGANISATION,
    ),
    _matter(
        MATTERS_BY_ADDRESSEE_ORGANISATION,
        "Teemad adressaadi järgi",
        "Kellele teema oli adresseeritud. Ei ole sama, mis saatja.",
        population="Nähtavad teemad, millel on adressaat",
        coverage_description_et="Teemad, millel on adressaat määratud",
        minimum_coverage=0.0,
        source_era_limitations_et=_ERA_ORGANISATION,
    ),
    # -- Ajalooline materjal ----------------------------------------------
    MetricDefinition(
        key=LEGACY_SOURCE_PAGES,
        version=1,
        label_et="Ajaloolisi lähtelehti",
        description_et=(
            "Arhiveeritud OneNote'i lehed, mis on seotud vähemalt ühe nähtava "
            "teemaga. Leht imporditakse üks kord, olenemata sellest, mitu teemat "
            "sellele viitab."
        ),
        source_population_et="Lähtelehed, mis on seotud nähtava teemaga",
        time_basis=TimeBasis.WHOLE_CORPUS,
        unit=Unit.PAGES,
        respects_period=False,
        drillthrough_et="Teemade register, filtreeritud ajaloolise allika olemasolule",
    ),
    MetricDefinition(
        key=LEGACY_SOURCE_PAGES_BY_SECTION,
        version=1,
        label_et="Lähtelehed OneNote'i sektsioonide kaupa",
        description_et=(
            "Ajalooline lähteklassifikatsioon: kuhu jurist 2019. aastal midagi "
            "arhiveeris. See EI OLE tänapäevane valdkond."
        ),
        source_population_et="Lähtelehed, mis on seotud nähtava teemaga",
        time_basis=TimeBasis.WHOLE_CORPUS,
        unit=Unit.PAGES,
        respects_period=False,
        notes_et="Ajaloolist sektsiooni ei kaardistata vaikimisi valdkonnale.",
    ),
    MetricDefinition(
        key=LEGACY_SOURCE_PAGES_BY_YEAR,
        version=1,
        label_et="Lähtelehed lähtekuupäeva järgi",
        description_et=(
            "OneNote'i lehe enda loomise ajatempel. See on allika ajalugu, "
            "mitte teema aruandlusaasta."
        ),
        source_population_et="Lähtelehed, mis on seotud nähtava teemaga",
        time_basis=TimeBasis.SOURCE_TIMESTAMP,
        unit=Unit.PAGES,
        coverage_description_et="Lehed, millel on lähtekuupäev teada",
        minimum_coverage=0.0,
        respects_period=False,
        notes_et="Ärge segage seda teemade aastajaotusega.",
    ),
    MetricDefinition(
        key=LEGACY_SOURCE_PAGES_BY_ROLE,
        version=1,
        label_et="Lähtelehed rolli järgi",
        description_et="Mida audit lehest arvas: teemalaadne, kategooria, taustainfo, tühi.",
        source_population_et="Lähtelehed, mis on seotud nähtava teemaga",
        time_basis=TimeBasis.WHOLE_CORPUS,
        unit=Unit.PAGES,
        respects_period=False,
    ),
    MetricDefinition(
        key=HISTORICAL_RESOURCE_OCCURRENCES,
        version=1,
        label_et="Ajaloolisi materjale",
        description_et=(
            "Failiesinemisi lehtedel. Sama fail kahel lehel on kaks esinemist — "
            "see ei ole sama, mis unikaalsete failisisude arv."
        ),
        source_population_et="Lähtefailid nähtava teemaga seotud lehtedel",
        time_basis=TimeBasis.WHOLE_CORPUS,
        unit=Unit.FILES,
        respects_period=False,
        notes_et="Esinemine, mitte unikaalne fail.",
        drillthrough_et="Ajaloolise materjali loend",
    ),
    MetricDefinition(
        key=HISTORICAL_UNIQUE_BINARY_CONTENTS,
        version=1,
        label_et="Unikaalseid failisisusid",
        description_et="Erinevate SHA-256 väärtuste arv. Ei ole sama, mis esinemiste arv.",
        source_population_et="Lähtefailid nähtava teemaga seotud lehtedel",
        time_basis=TimeBasis.WHOLE_CORPUS,
        unit=Unit.FILES,
        respects_period=False,
    ),
    MetricDefinition(
        key=HISTORICAL_RESOURCE_BYTES,
        version=1,
        label_et="Ajaloolise materjali maht",
        description_et="Failiesinemiste kogumaht baitides.",
        source_population_et="Lähtefailid nähtava teemaga seotud lehtedel",
        time_basis=TimeBasis.WHOLE_CORPUS,
        unit=Unit.BYTES,
        respects_period=False,
    ),
    MetricDefinition(
        key=HISTORICAL_RESOURCES_BY_TYPE,
        version=1,
        label_et="Materjalid failitüübi järgi",
        description_et="Esinemiste arv ja maht laiendi järgi.",
        source_population_et="Lähtefailid nähtava teemaga seotud lehtedel",
        time_basis=TimeBasis.WHOLE_CORPUS,
        unit=Unit.FILES,
        respects_period=False,
        drillthrough_et="Ajaloolise materjali loend, filtreeritud tüübi järgi",
    ),
    MetricDefinition(
        key=HISTORICAL_EMAIL_RESOURCES,
        version=1,
        label_et="E-kirju materjalides",
        description_et="MSG- ja EML-failid, mis on lehtedele manustatud.",
        source_population_et="Lähtefailid nähtava teemaga seotud lehtedel",
        time_basis=TimeBasis.WHOLE_CORPUS,
        unit=Unit.FILES,
        respects_period=False,
    ),
    MetricDefinition(
        key=HISTORICAL_SIGNED_CONTAINERS,
        version=1,
        label_et="Digiallkirjastatud materjale",
        description_et=(
            "ASiC-E ja BDoc ümbrikud. Neid ei avata teadlikult, seega on "
            "„ei kohaldu“ nende puhul õige ja oodatud seisund."
        ),
        source_population_et="Lähtefailid nähtava teemaga seotud lehtedel",
        time_basis=TimeBasis.WHOLE_CORPUS,
        unit=Unit.FILES,
        respects_period=False,
        notes_et="Allkirjaümbrik ei ole kunagi eraldamise ebaõnnestumine.",
    ),
    MetricDefinition(
        key=RESOURCES_PER_PAGE,
        version=1,
        label_et="Materjale lehe kohta",
        description_et="Mediaan ja protsentiilid. Aritmeetiline keskmine oleks eksitav.",
        source_population_et="Lähtelehed, mis on seotud nähtava teemaga",
        time_basis=TimeBasis.WHOLE_CORPUS,
        unit=Unit.FILES,
        respects_period=False,
    ),
    MetricDefinition(
        key=RESOURCES_PER_MATTER,
        version=1,
        label_et="Materjale teema kohta",
        description_et="Mediaan ja protsentiilid ajaloolise materjaliga teemade kohta.",
        source_population_et="Nähtavad teemad, millel on ajalooline lähteleht",
        time_basis=TimeBasis.WHOLE_CORPUS,
        unit=Unit.FILES,
        respects_period=False,
    ),
    MetricDefinition(
        key=SOURCE_PAGE_TEXT_LENGTH,
        version=1,
        label_et="Lähtelehe teksti pikkus",
        description_et="Märkide arv lehel, mediaani ja protsentiilidena.",
        source_population_et="Lähtelehed, mis on seotud nähtava teemaga",
        time_basis=TimeBasis.WHOLE_CORPUS,
        unit=Unit.COUNT,
        respects_period=False,
    ),
    MetricDefinition(
        key=MATERIALISATION_STATUS,
        version=1,
        label_et="Materjalide ülekandmise seis",
        description_et=(
            "Neli eri fakti allika kohta: imporditud, kopeerimist ootab, "
            "allikas tühi, kopeerimine ebaõnnestus. Tühi originaal ei ole viga "
            "ega igavene ootamine."
        ),
        source_population_et="Lähtefailid nähtava teemaga seotud lehtedel",
        time_basis=TimeBasis.WHOLE_CORPUS,
        unit=Unit.FILES,
        respects_period=False,
    ),
    # -- Dokumendid ja otsitavus -------------------------------------------
    MetricDefinition(
        key=EXTRACTION_ELIGIBLE,
        version=1,
        label_et="Eraldamiseks kõlblikke",
        description_et=(
            "Tõendiversioonid, mida töötaja tohib avada. Tekstitöötlust ootavat "
            "faili ei pakuta järjekorda ega loeta ebaõnnestunuks."
        ),
        source_population_et="Nähtavate teemade tõendiversioonid",
        drillthrough_et="Tõendiversioonide eraldi loendit ei ole; failid avanevad teema juurest",
        time_basis=TimeBasis.POINT_IN_TIME,
        respects_period=False,
    ),
    MetricDefinition(
        key=EXTRACTION_SUCCESS,
        version=1,
        label_et="Eraldatud",
        description_et="Versioonid, mille kõik nõutud tuletised said valmis.",
        source_population_et="Nähtavate teemade tõendiversioonid",
        drillthrough_et="Tõendiversioonide eraldi loendit ei ole; failid avanevad teema juurest",
        time_basis=TimeBasis.POINT_IN_TIME,
        respects_period=False,
    ),
    MetricDefinition(
        key=EXTRACTION_PENDING,
        version=1,
        label_et="Eraldamise järjekorras",
        description_et="Versioonid, mis ootavad töötlemist või on töös.",
        source_population_et="Nähtavate teemade tõendiversioonid",
        drillthrough_et="Tõendiversioonide eraldi loendit ei ole; failid avanevad teema juurest",
        time_basis=TimeBasis.POINT_IN_TIME,
        respects_period=False,
    ),
    MetricDefinition(
        key=EXTRACTION_AWAITING_SCANNER,
        # Version 2: the wording, not the measurement. The population is
        # unchanged — the same versions the extraction queue will not yet offer
        # a worker — but "Ootab pahavarakontrolli" described the *mechanism* and
        # readers understood it as "these files may be infected". They are not:
        # the Juristid corpus is known to be malware-free. What is missing is a
        # step in this system's own text-extraction pipeline (Statistika QA §4).
        version=2,
        label_et="Ootab tekstitöötlust",
        description_et=(
            "Versioonid, mille tekst on veel eraldamata, sest eraldamise "
            "tehniline eeltingimus ei ole täidetud. Failid ise on teadaolevalt "
            "pahavaravabad — see seisund ei tähenda pahavarakahtlust ega "
            "lahendamata turvaküsimust."
        ),
        source_population_et="Nähtavate teemade tõendiversioonid",
        drillthrough_et="Tõendiversioonide eraldi loendit ei ole; failid avanevad teema juurest",
        time_basis=TimeBasis.POINT_IN_TIME,
        respects_period=False,
        notes_et=(
            "Ei ole viga, järjekord ega turvaintsident. Tehniline eeltingimus "
            "on tekstituvastuse konveieri väravakontroll, mis Turvalise piloodi "
            "väravani veel ei tööta; selles keskkonnas on nullist erinev arv "
            "ootuspärane."
        ),
    ),
    MetricDefinition(
        key=EXTRACTION_FAILED,
        version=1,
        label_et="Eraldamine ebaõnnestus",
        description_et=(
            "Tõeline parseri viga. Allkirjaümbrikud ja tekstitöötlust ootavad failid ei kuulu siia."
        ),
        source_population_et="Nähtavate teemade tõendiversioonid",
        drillthrough_et="Tõendiversioonide eraldi loendit ei ole; failid avanevad teema juurest",
        time_basis=TimeBasis.POINT_IN_TIME,
        respects_period=False,
    ),
    MetricDefinition(
        key=EXTRACTION_NOT_APPLICABLE,
        version=1,
        label_et="Ei kohaldu",
        description_et="Vormingud, mida ükski parser ei ava — näiteks allkirjaümbrikud.",
        source_population_et="Nähtavate teemade tõendiversioonid",
        drillthrough_et="Tõendiversioonide eraldi loendit ei ole; failid avanevad teema juurest",
        time_basis=TimeBasis.POINT_IN_TIME,
        respects_period=False,
    ),
    MetricDefinition(
        key=SEARCHABLE_DOCUMENT_COVERAGE,
        version=1,
        label_et="Otsitava sisu katvus",
        description_et=(
            "Kui suur osa avatavast sisust on tegelikult eraldatud ja otsitav. "
            "Nimetajast on välja jäetud need, mida ükski parser ei ava."
        ),
        source_population_et="Nähtavate teemade tõendiversioonid",
        drillthrough_et="Tõendiversioonide eraldi loendit ei ole; failid avanevad teema juurest",
        time_basis=TimeBasis.POINT_IN_TIME,
        unit=Unit.PERCENT,
        exclusions_et=f"Versioonid olekus {ExtractionState.NOT_APPLICABLE.label}",
        coverage_description_et="Eraldatud versioonid avatavatest versioonidest",
        minimum_coverage=0.0,
        respects_period=False,
        notes_et=(
            "Kui eraldamine on turvakontrolli taga, ei väida see näitaja otsitavuse täielikkust."
        ),
    ),
    # -- Arvamuste arhiiv --------------------------------------------------
    MetricDefinition(
        key=OPINION_ARCHIVE_OCCURRENCES,
        version=1,
        label_et="Arvamuste arhiivi esinemisi",
        description_et=(
            "Failide esinemisi arhiivis. Sama fail kahes kohas on kaks esinemist "
            "ja üks baidijada — mõlemad on tõsi ja mõlemad loetakse eraldi."
        ),
        source_population_et="Kataloogitud arvamuste arhiivi kirjed",
        time_basis=TimeBasis.WHOLE_CORPUS,
        unit=Unit.FILES,
        respects_period=False,
        drillthrough_et="Halduse arvamuste ülevaatuse järjekord",
    ),
    MetricDefinition(
        key=OPINION_ARCHIVE_DISTINCT_BINARIES,
        version=1,
        label_et="Erinevaid arvamuse faile",
        description_et="Erinevate SHA-256 väärtuste arv arvamuste arhiivis.",
        source_population_et="Kataloogitud arvamuste arhiivi kirjed",
        time_basis=TimeBasis.WHOLE_CORPUS,
        unit=Unit.FILES,
        respects_period=False,
    ),
    MetricDefinition(
        key=OPINION_ARCHIVE_BY_YEAR,
        version=1,
        label_et="Arvamuste arhiiv aastate kaupa",
        description_et=(
            "Erinevaid arhiivi faile aasta kohta, arhiivi failinime kuupäeva "
            "järgi. See ei ole väljasaatmise aeg ja mitte kanooniline saadetud "
            "arvamus: allika metaandmed ütlevad, millise kuupäeva kiri kannab."
        ),
        source_population_et="Kataloogitud arvamuste arhiivi kirjed, millel on failinime kuupäev",
        time_basis=TimeBasis.SOURCE_TIMESTAMP,
        unit=Unit.FILES,
        earliest_reliable_period=OPINION_ARCHIVE_FIRST_YEAR,
        required_fields=("filename_date",),
        coverage_description_et="Erinevad failid, millel on failinime kuupäev",
        minimum_coverage=0.0,
        source_era_limitations_et=(
            "Arhiiv algab 2020. aastast. Varasemate aastate kohta arhiivis "
            "mõõtmist ei ole ja neid aastaid ei joonistata — puuduv mõõtmine "
            "ei ole null."
        ),
        notes_et=(
            "Loetakse erinevaid faile (SHA-256), mitte esinemisi: sama kiri "
            "kahes kohas on üks kiri. Sama fail kahe kuupäevaga arvestatakse "
            "varaseima kuupäeva järgi. Teemafiltrid ei kitsenda seda näitajat."
        ),
        drillthrough_et=(
            "Loendit ei avata: arhiivil ei ole jagatud värava taga lugejale "
            "avatavat vaadet, ja katkine link oleks halvem kui link puudub."
        ),
    ),
    MetricDefinition(
        key=OPINION_ARCHIVE_BY_MONTH,
        version=1,
        label_et="Arvamuste arhiiv kuude kaupa",
        description_et=("Erinevaid arhiivi faile kuu kohta, arhiivi failinime kuupäeva järgi."),
        source_population_et="Kataloogitud arvamuste arhiivi kirjed, millel on failinime kuupäev",
        time_basis=TimeBasis.SOURCE_TIMESTAMP,
        unit=Unit.FILES,
        earliest_reliable_period=OPINION_ARCHIVE_FIRST_YEAR,
        required_fields=("filename_date",),
        coverage_description_et="Erinevad failid, millel on failinime kuupäev",
        minimum_coverage=0.0,
        source_era_limitations_et=(
            "Telg lõpeb arhiivi viimasel mõõdetud kuupäeval. Hilisemaid kuid ei "
            "joonistata nullidena: nende kohta ei ole tõendust, mitte tõendus "
            "tühjusest."
        ),
        notes_et="Loetakse erinevaid faile, mitte esinemisi.",
        drillthrough_et="Loendit ei avata; vt aastate kaupa näitaja selgitust.",
    ),
    MetricDefinition(
        key=OPINION_ARCHIVE_YOY_CHANGE,
        version=1,
        label_et="Arhiivi arvamuste muutus eelmise aastaga",
        description_et=(
            "Arhiivi viimane aasta kuni viimase mõõdetud kuupäevani, võrrelduna "
            "eelmise aasta täpselt sama ajavahemikuga."
        ),
        source_population_et="Kataloogitud arvamuste arhiivi kirjed, millel on failinime kuupäev",
        time_basis=TimeBasis.SOURCE_TIMESTAMP,
        unit=Unit.FILES,
        earliest_reliable_period=OPINION_ARCHIVE_FIRST_YEAR,
        required_fields=("filename_date",),
        respects_period=False,
        source_era_limitations_et=(
            "Arhiivi katvus lõpeb viimase failinime kuupäevaga. Kui see on näiteks "
            "31. juuli, siis on ka eelmise aasta pool lõigatud 31. juulil."
        ),
        notes_et=(
            "Mahu muutus, mitte tulemuslikkus: rohkem arvamusi ei ole iseenesest "
            "parem ega halvem. Kui eelmises võrreldavas perioodis ei olnud ühtki "
            "faili, näidatakse ainult absoluutset vahet — protsenti nullist ei "
            "arvutata."
        ),
        drillthrough_et="Loendit ei avata; vt aastate kaupa näitaja selgitust.",
    ),
    MetricDefinition(
        key=OPINION_ARCHIVE_LINK_COVERAGE,
        version=1,
        label_et="Arhiivi failid teemaga seotud (seoste järgi)",
        description_et=(
            "Kui suur osa erinevatest arhiivi failidest on seotud vähemalt ühe "
            "teemaga. Loetakse kinnitatud ja tuletatud teemaseoseid, mitte "
            "ülevaatust ootavaid ettepanekuid."
        ),
        source_population_et="Kataloogitud arvamuste arhiivi kirjed",
        time_basis=TimeBasis.POINT_IN_TIME,
        unit=Unit.PERCENT,
        coverage_description_et="Erinevad failid, millel on seos nähtava teemaga",
        minimum_coverage=0.0,
        respects_period=False,
        notes_et=(
            "Sidumata fail ei ole puuduv arvamus, vaid arhiivitõendus, mis ei ole "
            "veel teemaga seotud. Mitme teemaga seotud fail loetakse kaetuks üks "
            "kord. Loetakse ainult seoseid teemadega, mida lugeja näeb, seega "
            "piiratud nähtavusega teema ei muuda kellegi teise numbrit."
        ),
        drillthrough_et="Loendit ei avata; vt aastate kaupa näitaja selgitust.",
    ),
    MetricDefinition(
        key=OPINION_ARCHIVE_LINKED_BY_RESPONSIBILITY,
        version=1,
        label_et="Arhiivi failid vastutuse järgi",
        description_et=(
            "Erinevad arhiivi failid, millel on seos selle vastutaja teemaga. "
            "Üks fail võib puudutada mitut teemat ja on siis arvestatud iga "
            "vastutaja juures, seega rühmade summa võib ületada failide koguarvu."
        ),
        source_population_et="Arhiivi failid, mis on seotud lugejale nähtava teemaga",
        time_basis=TimeBasis.POINT_IN_TIME,
        unit=Unit.FILES,
        coverage_description_et="Erinevad failid, millel on seos nähtava teemaga",
        minimum_coverage=0.0,
        respects_period=False,
        source_era_limitations_et=(
            "Vastutaja on registri VASTUTAJA tekst, selle puudumisel kanooniline "
            "vastutaja. Ajaloolist nime ei asendata praeguse töötajaga."
        ),
        notes_et=(
            "Arhiivi inventuur, mitte juristide võrdlus ega tulemuslikkus. "
            "Esmast teemat ei valita: mudelis seda ei ole ja väljamõeldud "
            "esmasus paneks kirja vale inimese nimele."
        ),
        drillthrough_et="Loendit ei avata; vt aastate kaupa näitaja selgitust.",
    ),
    MetricDefinition(
        key=OPINION_ARCHIVE_LINKED_BY_MONTH_RESPONSIBILITY,
        version=1,
        label_et="Arhiivi failid kuude ja vastutuse kaupa",
        description_et=(
            "Teemaga seotud arhiivi failid kuu ja vastutaja kaupa, arhiivi "
            "failinime kuupäeva järgi."
        ),
        source_population_et="Arhiivi failid, mis on seotud lugejale nähtava teemaga",
        time_basis=TimeBasis.SOURCE_TIMESTAMP,
        unit=Unit.FILES,
        earliest_reliable_period=OPINION_ARCHIVE_FIRST_YEAR,
        required_fields=("filename_date",),
        source_era_limitations_et=(
            "Kuupäev on failinime kuupäev, mitte väljasaatmise aeg. Arhiiv algab 2020. aastast."
        ),
        notes_et=(
            "Mitut teemat puudutav fail on arvestatud iga vastutaja juures, "
            "seega ridade summa võib ületada erinevate failide arvu. Esmast "
            "teemat ei valita."
        ),
        drillthrough_et="Loendit ei avata; vt aastate kaupa näitaja selgitust.",
    ),
    # -- Andmekvaliteet ----------------------------------------------------
    MetricDefinition(
        key=OPINION_ARCHIVE_MATTER_COVERAGE,
        version=1,
        label_et="Arhiivi failid teemaga seotud",
        description_et=(
            "Kui suur osa arhiivi failidest on jõudnud kindla teemani. "
            "Sidumata fail ei ole puuduv arvamus, vaid lahendamata tõendus."
        ),
        source_population_et="Kataloogitud arvamuste arhiivi kirjed",
        time_basis=TimeBasis.POINT_IN_TIME,
        unit=Unit.PERCENT,
        coverage_description_et="Failid, millel on kinnitatud teema",
        minimum_coverage=0.0,
        respects_period=False,
        drillthrough_et="Halduse arvamuste ülevaatuse järjekord",
    ),
    MetricDefinition(
        key=OPINION_ARCHIVE_UNRESOLVED,
        version=1,
        label_et="Ülevaatust ootavaid arvamusi",
        description_et="Arhiivi failid, mille teemat ei õnnestunud tõenduse põhjal otsustada.",
        source_population_et="Arvamuste sidumiskandidaadid olekus OOTEL",
        time_basis=TimeBasis.POINT_IN_TIME,
        respects_period=False,
        drillthrough_et="Halduse arvamuste ülevaatuse järjekord",
    ),
    MetricDefinition(
        key=HISTORICAL_SUBMISSION_COVERAGE,
        version=1,
        label_et="Taastatud arvamusi arhiivist",
        description_et=(
            "Arhiivi failid, millest sai kanooniline saadetud arvamus. "
            "Lävi on range: üks teema, kaitstav kuupäev ja täpne lõplik tõend."
        ),
        source_population_et="Kataloogitud arvamuste arhiivi kirjed",
        time_basis=TimeBasis.POINT_IN_TIME,
        unit=Unit.PERCENT,
        coverage_description_et="Failid, millest sai saadetud arvamus",
        minimum_coverage=0.0,
        respects_period=False,
        notes_et=("Ülejäänu ei ole kadunud: tõendus on alles ja ootab ülevaatust."),
    ),
    MetricDefinition(
        key=SUBMISSION_RECIPIENT_COVERAGE,
        version=1,
        label_et="Arvamusi adressaadiga",
        description_et=(
            "Saadetud arvamused, millel on vähemalt üks lahendatud adressaat. "
            "Lahendamata saaja tähendab, et nime ei õnnestunud üheselt "
            "organisatsiooniga siduda — mitte et adressaati ei olnud."
        ),
        source_population_et="Nähtavad arvamused olekus SAADETUD",
        time_basis=TimeBasis.POINT_IN_TIME,
        unit=Unit.PERCENT,
        coverage_description_et="Arvamused, millel on adressaat",
        minimum_coverage=0.0,
        respects_period=False,
        exclusions_et="Rollis „teadmiseks“ olevad saajad",
    ),
    MetricDefinition(
        key=RECONCILIATION_PENDING,
        version=1,
        label_et="Ülevaatust ootavaid sidumisi",
        description_et="Ajaloo sidumiskandidaadid, mille kohta ei ole otsust tehtud.",
        source_population_et="Ajaloo sidumiskandidaadid olekus OOTEL",
        time_basis=TimeBasis.POINT_IN_TIME,
        respects_period=False,
        drillthrough_et="Halduse ajaloo ülevaatuse järjekord",
    ),
    MetricDefinition(
        key=RECONCILIATION_CONFLICT,
        version=1,
        label_et="Vastuolusid",
        description_et="Kandidaadid, mille tõendus on omavahel vastuolus.",
        source_population_et="Ajaloo sidumiskandidaadid klassis CONFLICT",
        time_basis=TimeBasis.POINT_IN_TIME,
        respects_period=False,
        drillthrough_et="Halduse ajaloo ülevaatuse järjekord, klass CONFLICT",
    ),
    MetricDefinition(
        key=RECONCILIATION_BY_CLASS,
        version=1,
        label_et="Ootel sidumised klassi järgi",
        description_et="Tugev, vajab ülevaatust, vastuolu, sidumata leht, katkine link.",
        source_population_et="Ajaloo sidumiskandidaadid olekus OOTEL",
        time_basis=TimeBasis.POINT_IN_TIME,
        respects_period=False,
        drillthrough_et="Halduse ajaloo ülevaatuse järjekord, klassi kaupa",
    ),
    MetricDefinition(
        key=UNLINKED_SUBSTANTIVE_PAGES,
        version=1,
        label_et="Sidumata sisukaid lehti",
        description_et="Teemalaadsed OneNote'i lehed, mis ei ole ühegi teemaga seotud.",
        source_population_et="Ajaloo sidumiskandidaadid klassis UNLINKED_PAGE, olekus OOTEL",
        time_basis=TimeBasis.POINT_IN_TIME,
        respects_period=False,
    ),
    MetricDefinition(
        key=READING_ORDER_AMBIGUOUS,
        version=1,
        label_et="Ebaselge lugemisjärjekorraga lehti",
        description_et=(
            "OneNote'i vaba paigutus: XML-i ja visuaalse järjekorra vahel oli "
            "lahkarvamus, seega võib narratiivi järjestus olla ebatäpne."
        ),
        source_population_et="Lähtelehed, mis on seotud nähtava teemaga",
        time_basis=TimeBasis.WHOLE_CORPUS,
        unit=Unit.PAGES,
        respects_period=False,
    ),
    MetricDefinition(
        key=MATERIALISATION_FAILED,
        version=1,
        label_et="Ülekandmine ebaõnnestus",
        description_et=(
            "Failid, mille kopeerimine päriselt ebaõnnestus. Allikas tühjad failid ei kuulu siia."
        ),
        source_population_et="Lähtefailid nähtava teemaga seotud lehtedel",
        time_basis=TimeBasis.WHOLE_CORPUS,
        unit=Unit.FILES,
        respects_period=False,
    ),
    MetricDefinition(
        key=DATA_QUALITY_ATTENTION,
        version=1,
        label_et="Andmekvaliteedi tähelepanu",
        description_et=(
            "Kokku ridu andmekvaliteedi järjekordades. Arhiivi õigustatud hõredus ei ole siin sees."
        ),
        source_population_et="Andmekvaliteedi järjekordade read",
        time_basis=TimeBasis.POINT_IN_TIME,
        respects_period=False,
        drillthrough_et="Andmekvaliteedi vaade",
    ),
)


CATALOGUE: dict[str, MetricDefinition] = {definition.key: definition for definition in _DEFINITIONS}

if len(CATALOGUE) != len(_DEFINITIONS):  # pragma: no cover - import-time guard
    raise RuntimeError("Two metric definitions share a key.")


def definition(key: str) -> MetricDefinition:
    """Look one up, loudly.

    A ``KeyError`` at import or render time is a great deal better than a card
    that quietly renders nothing because a key was misspelt.
    """
    try:
        return CATALOGUE[key]
    except KeyError as exc:  # pragma: no cover - programming error
        raise KeyError(f"No metric definition for {key!r}.") from exc


#: Metrics the brief made conditional on the canonical data supporting them
#: cleanly, and which it does not. Kept as data rather than as a paragraph in a
#: document, so the Statistika page can say *why* a number is absent instead of
#: leaving a reader to wonder whether it is zero.
#:
#: The register's member-feedback counts and its `VÄLJA` outbound dates exist in
#: the source, and the importer reads them — but nothing persists them as
#: queryable columns. They survive only inside
#: ``MatterSourceReference.source_row_raw``, which is ``{column letter: raw
#: text}``: recovering them would mean re-resolving each row's era contract,
#: finding that era's letter for the field, and re-parsing free text that
#: includes sequence numbers and unreadable values. That is exactly the brittle
#: parsing the brief says to defer rather than ship (Stage-2E brief 25, 26, 58).
DEFERRED_METRICS: dict[str, str] = {
    "LEGACY_MEMBER_ASKED_MATTERS": (
        "Registri liikmete tagasiside arvud ei ole päritavad veergudena — need "
        "on ainult lähterea toorandmetes."
    ),
    "LEGACY_MEMBER_ASKED_COUNT": (
        "Vt eespool. Lisaks: küsitute ja vastanute arvud on sõltumatud "
        "vaatlused, millest ei tohi kunagi arvutada vastamismäära."
    ),
    "LEGACY_MEMBER_RESPONSE_COUNT": (
        "Vt eespool. Registris on read, kus vastanuid on rohkem kui otse "
        "küsituid, seega ühist nimetajat ei ole."
    ),
    "LEGACY_SENT_DATE_OBSERVATIONS": (
        "Registri VÄLJA-kuupäevad ei ole päritavad veeruna. Neid ei tohiks "
        "niikuinii nimetada arvamusteks ega liita Submission.sent_at väärtustega."
    ),
}
