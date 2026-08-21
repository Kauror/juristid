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
    "Struktuurne arvamuse kirje tekib alles selles süsteemis. Varasemate "
    "aastate kohta ei ole mõõtmist — see ei tähenda, et arvamusi ei saadetud."
)
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
ACTIVE_WITHOUT_NEXT_ACTION = "ACTIVE_WITHOUT_NEXT_ACTION"
ACTIVE_WITHOUT_OWNER = "ACTIVE_WITHOUT_OWNER"
OVERDUE_DO_DEADLINE = "OVERDUE_DO_DEADLINE"
WAIT_REVIEW_DUE = "WAIT_REVIEW_DUE"
MONITOR_REVIEW_DUE = "MONITOR_REVIEW_DUE"
RESPONSE_DEADLINES_OPEN = "RESPONSE_DEADLINES_OPEN"
NEXT_ACTION_BY_KIND = "NEXT_ACTION_BY_KIND"
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
    **kwargs: object,
) -> MetricDefinition:
    """A Matter-population definition with this file's shared defaults."""
    return MetricDefinition(
        key=key,
        version=1,
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
        version=1,
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
        version=1,
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
        version=1,
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
        version=1,
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
        eligible_record_modes=(RecordMode.FULL.value,),
        source_era_limitations_et=_ERA_SUBMISSION,
    ),
    _matter(
        MATTERS_WITH_MULTIPLE_SUBMISSIONS,
        "Mitme arvamusega teemasid",
        "Teemad, millelt on saadetud rohkem kui üks arvamus.",
        population="Nähtavad täielikud teemad",
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
            "Ainult TEEN + TÄHTAEG saab olla hilinenud. Ootamist ega jälgimist "
            "ei nimetata kunagi hilinenuks."
        ),
        source_population_et="Kehtivad järgmised tegevused, liik DO, kuupäeva tähendus DEADLINE",
        time_basis=TimeBasis.POINT_IN_TIME,
        required_fields=("target_date",),
        respects_period=False,
        drillthrough_et="Minu töö või teemade register",
    ),
    MetricDefinition(
        key=WAIT_REVIEW_DUE,
        version=1,
        label_et="Ootan — ülevaatus käes",
        description_et="Ootamised, mille ülevaatuse kuupäev on saabunud. Ei ole hilinemine.",
        source_population_et="Kehtivad järgmised tegevused, liik WAIT, kuupäevaga",
        time_basis=TimeBasis.POINT_IN_TIME,
        respects_period=False,
    ),
    MetricDefinition(
        key=MONITOR_REVIEW_DUE,
        version=1,
        label_et="Jälgin — ülevaatus käes",
        description_et="Jälgimised, mille ülevaatuse kuupäev on saabunud. Ei ole hilinemine.",
        source_population_et="Kehtivad järgmised tegevused, liik MONITOR, kuupäevaga",
        time_basis=TimeBasis.POINT_IN_TIME,
        respects_period=False,
    ),
    MetricDefinition(
        key=NEXT_ACTION_BY_KIND,
        version=1,
        label_et="Järgmised tegevused liigi järgi",
        description_et="Teen, ootan, jälgin — kolm eri asja, mida ei liideta.",
        source_population_et="Kehtivad järgmised tegevused",
        time_basis=TimeBasis.POINT_IN_TIME,
        respects_period=False,
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
            "Tõendiversioonid, mida töötaja tohib avada. Pahavarakontrolli "
            "ootavat faili ei pakuta järjekorda ega loeta ebaõnnestunuks."
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
        version=1,
        label_et="Ootab pahavarakontrolli",
        description_et=(
            "Versioonid, mida ei töödelda enne, kui skanner ütleb „puhas“. "
            "See ei ole ebaõnnestumine ega järjekord."
        ),
        source_population_et="Nähtavate teemade tõendiversioonid",
        drillthrough_et="Tõendiversioonide eraldi loendit ei ole; failid avanevad teema juurest",
        time_basis=TimeBasis.POINT_IN_TIME,
        respects_period=False,
        notes_et="Selles keskkonnas võib see olla nullist erinev ja see on korras.",
    ),
    MetricDefinition(
        key=EXTRACTION_FAILED,
        version=1,
        label_et="Eraldamine ebaõnnestus",
        description_et=(
            "Tõeline parseri viga. Allkirjaümbrikud ja skannerit ootavad failid ei kuulu siia."
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
    # -- Andmekvaliteet ----------------------------------------------------
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
