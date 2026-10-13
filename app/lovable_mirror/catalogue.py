"""The field-level mapping catalogue: every Lovable column, and what Juristid may do with it.

This module is policy written down. It holds no query. The importer reads it to
decide which tables and columns a bundle may carry, and the reconciliation reads
it to decide which columns may be compared with a canonical value at all.

**What the catalogue was built from.** The Lovable alpha's source repository,
`Kauror/cheerful-control`, at commit `f248d20b` (pushed 2026-08-17):
`src/integrations/supabase/types.ts` for the tables and columns, and
`src/lib/dashboard/{parse.server,upload.functions,snapshot.server,changes}.ts`
for what each column means. **Live schema NOT verified.** The owner has since
observed, through a read-only connector, that the live project holds tables the
repository does not (`LIVE_TABLES_REPORTED`, `UNMAPPED_LIVE_TABLES`); their
columns are unknown and are not invented here. Nothing in this module was read
from the live project. That is why an unknown *table* is refused rather
than skipped — a table the catalogue has not described is one nobody has decided
anything about — while an unknown *column* on a known table is kept as
``UNCATALOGUED`` and shown in the coverage report, never compared.

**The classes.**

* ``IDENTITY`` — the deterministic key a row is matched by. For ``topics`` that
  is ``nr``, the Excel register number Lovable carries verbatim, which is the
  same ``YYYY_N`` as a Matter's Teemaviide. Lovable's own row ``id`` is *not*
  stable: every upload inserts a fresh ``datasets`` row and fresh ``topics``
  ids, and Lovable itself compares snapshots by ``nr`` only (``changes.ts``).
* ``COMPARABLE`` — compared with one canonical Juristid value by an exact rule
  (normalised text, an ISO date, an owner resolved by
  :func:`app.legacy_import.resolution.resolve_owner`). A difference is reported
  as a canonical conflict. Nothing is ever written to resolve it.
* ``REVIEW_ONLY`` — shown beside the nearest canonical value for a person to
  read. No equivalence rule exists, so no difference is ever counted.
* ``NEVER_AUTO`` — free text or a date whose canonical counterpart is an act
  somebody performs. Shown as the source said it, and **never** converted:
  ``jargmiseks`` is not a ``NextAction``, ``valja`` is not a SENT
  ``Submission``, ``hetkeseis`` is not a stage transition, a note is not
  evidence or an opinion. ``tests/test_lovable_mirror.py`` pins that no module
  in this app imports the canonical write services.
* ``PROVENANCE`` — where the row sat in Lovable (dataset, sheet, row index,
  links). Kept, never compared, excluded from the changed-field diff because it
  churns on every upload.
* ``LOVABLE_DERIVED`` — Lovable's own computation (active flag, next deadline,
  attention reasons). Lovable's interpretation, not a fact about the Matter.
* ``UNCATALOGUED`` — a column the catalogue does not know. Stored, reported.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import models

#: The schema the catalogue describes. Recorded on every snapshot beside the
#: one the bundle declares, so a later reader can tell which reading of Lovable
#: interpreted it.
CATALOGUE_VERSION = "1"
CATALOGUE_SOURCE = "Kauror/cheerful-control@f248d20b src/integrations/supabase/types.ts"


class MappingClass(models.TextChoices):
    IDENTITY = "IDENTITY", "Identiteet"
    COMPARABLE = "COMPARABLE", "Võrreldav"
    REVIEW_ONLY = "REVIEW_ONLY", "Ainult ülevaatuseks"
    NEVER_AUTO = "NEVER_AUTO", "Mitte kunagi automaatselt"
    PROVENANCE = "PROVENANCE", "Päritolu"
    LOVABLE_DERIVED = "LOVABLE_DERIVED", "Lovable'i arvutus"
    UNCATALOGUED = "UNCATALOGUED", "Kataloogimata"


#: Classes whose change between two snapshots is a change worth listing.
DIFFED_CLASSES: frozenset[str] = frozenset(
    {
        MappingClass.IDENTITY,
        MappingClass.COMPARABLE,
        MappingClass.REVIEW_ONLY,
        MappingClass.NEVER_AUTO,
        MappingClass.UNCATALOGUED,
    }
)


class Canonical(models.TextChoices):
    """The canonical value a COMPARABLE or REVIEW_ONLY field is set beside."""

    NONE = "", "—"
    REFERENCE = "REFERENCE", "Teemaviide"
    TITLE = "TITLE", "Pealkiri"
    RECEIVED_DATE = "RECEIVED_DATE", "Saabus"
    RESPONSE_DEADLINE = "RESPONSE_DEADLINE", "Arvamuse tähtaeg"
    OWNER = "OWNER", "Vastutaja"
    LEGAL_INSTRUMENTS = "LEGAL_INSTRUMENTS", "Õigusakt"
    STAGE = "STAGE", "Hetkeseis"


@dataclass(frozen=True)
class FieldMapping:
    name: str
    mapping_class: str
    concept: str
    canonical: str = Canonical.NONE
    note: str = ""


@dataclass(frozen=True)
class TableMapping:
    name: str
    role: str
    description: str
    fields: tuple[FieldMapping, ...]
    required: frozenset[str] = field(default_factory=frozenset)
    id_field: str = "id"
    stable_key_field: str = "id"

    def field(self, name: str) -> FieldMapping | None:
        for mapping in self.fields:
            if mapping.name == name:
                return mapping
        return None

    def class_of(self, name: str) -> str:
        mapping = self.field(name)
        return mapping.mapping_class if mapping else MappingClass.UNCATALOGUED


#: Table roles.
ROLE_OPERATIVE = "operative"  # matched and compared
ROLE_METADATA = "metadata"  # tells which operative rows are current
ROLE_HISTORY = "history"  # stored for coverage, never matched in v1

_P = MappingClass.PROVENANCE
_D = MappingClass.LOVABLE_DERIVED

#: The operative register columns, shared by `topics` and `longterm_topics`.
_REGISTER_FIELDS: tuple[FieldMapping, ...] = (
    FieldMapping("id", _P, "Lovable'i rea UUID — uus igal üleslaadimisel, ei ole püsiv"),
    FieldMapping("dataset_id", _P, "Lovable'i andmestik, kuhu rida kuulub"),
    FieldMapping("sheet_name", _P, "Exceli aastaleht"),
    FieldMapping("row_index", _P, "Exceli rea number"),
    FieldMapping(
        "nr",
        MappingClass.IDENTITY,
        "Exceli NR = Teemaviide (YYYY_N)",
        Canonical.REFERENCE,
        "Ainus sidumisvõti. Ei tuletata pealkirjast ega vastutajast.",
    ),
    FieldMapping("aasta", _D, "NR-ist tuletatud aasta"),
    FieldMapping(
        "teema",
        MappingClass.COMPARABLE,
        "Teema pealkiri",
        Canonical.TITLE,
        "Võrreldakse normaliseeritult pealkirja ja muude pealkirjadega.",
    ),
    FieldMapping(
        "oigusakt",
        MappingClass.REVIEW_ONLY,
        "Õigusakti tüüp vabatekstina",
        Canonical.LEGAL_INSTRUMENTS,
        "Taksonoomiaga võrdusreeglit ei ole.",
    ),
    FieldMapping("sisse", MappingClass.COMPARABLE, "Saabumise kuupäev", Canonical.RECEIVED_DATE),
    FieldMapping(
        "arvamuse_tahtaeg",
        MappingClass.COMPARABLE,
        "Arvamuse tähtaeg",
        Canonical.RESPONSE_DEADLINE,
    ),
    FieldMapping(
        "valja",
        MappingClass.NEVER_AUTO,
        "«Välja» tekst (kuupäev või «ei saatnud»)",
        note="Ei ole kunagi saadetud Submission.",
    ),
    FieldMapping(
        "valja_kuupaev",
        MappingClass.NEVER_AUTO,
        "«Välja» kuupäevana",
        note="Ei ole kunagi saadetud Submission ega selle kuupäev.",
    ),
    FieldMapping(
        "vastutaja",
        MappingClass.COMPARABLE,
        "Vastutaja eesnimi",
        Canonical.OWNER,
        "Lahendatakse resolve_owner reeglitega; lahendamata nimi ei ole vastuolu.",
    ),
    FieldMapping(
        "hetkeseis",
        MappingClass.NEVER_AUTO,
        "Hetkeseisu vabatekst",
        Canonical.STAGE,
        "Kuvatakse etapi kõrval ülevaatuseks; ei ole kunagi etapimuutus.",
    ),
    FieldMapping(
        "jargmiseks",
        MappingClass.NEVER_AUTO,
        "«Järgmiseks» vabatekst",
        note="Ei ole kunagi NextAction ega tähtaeg.",
    ),
)

TABLES: dict[str, TableMapping] = {
    "datasets": TableMapping(
        name="datasets",
        role=ROLE_METADATA,
        description="Lovable'i üleslaadimised; is_current näitab kehtivat andmestikku.",
        required=frozenset({"id", "is_current"}),
        fields=(
            FieldMapping("id", _P, "Andmestiku UUID"),
            FieldMapping("file_name", _P, "Üles laaditud faili nimi"),
            FieldMapping("is_current", _P, "Kas see andmestik on Lovable'is kehtiv"),
            FieldMapping("latest_year", _D, "Suurim aastaleht"),
            FieldMapping("row_count", _P, "Ridu andmestikus"),
            FieldMapping("uploaded_at", _P, "Üleslaadimise aeg"),
            FieldMapping("years", _D, "Andmestiku aastad"),
        ),
    ),
    "topics": TableMapping(
        name="topics",
        role=ROLE_OPERATIVE,
        description="Lovable'i operatiivsed teemaread (Exceli aastalehed 2025+).",
        required=frozenset({"id", "nr", "teema", "dataset_id"}),
        stable_key_field="nr",
        fields=(
            *_REGISTER_FIELDS,
            FieldMapping("onenote_link", _P, "TEEMA lahtri hüperlingist tuletatud OneNote'i link"),
            FieldMapping("raw_hyperlink", _P, "TEEMA lahtri hüperlingi algne siht"),
        ),
    ),
    "longterm_topics": TableMapping(
        name="longterm_topics",
        role=ROLE_HISTORY,
        description="Pikaajalise statistika read (aastalehed 2011+). Ainult hoiul.",
        required=frozenset({"id", "teema", "dataset_id"}),
        stable_key_field="nr",
        fields=_REGISTER_FIELDS,
    ),
    "snapshots": TableMapping(
        name="snapshots",
        role=ROLE_HISTORY,
        description="Lovable'i enda päevased hetkeseisud.",
        required=frozenset({"id", "snapshot_date"}),
        fields=(
            FieldMapping("id", _P, "Lovable'i hetkeseisu UUID"),
            FieldMapping("created_at", _P, "Loodud"),
            FieldMapping("dataset_id", _P, "Andmestik"),
            FieldMapping("file_name", _P, "Faili nimi"),
            FieldMapping("fingerprint", _P, "Lovable'i sisu sõrmejälg"),
            FieldMapping("latest_year", _D, "Suurim aasta"),
            FieldMapping("row_count", _P, "Ridu"),
            FieldMapping("snapshot_date", _P, "Hetkeseisu kuupäev (Europe/Tallinn)"),
            FieldMapping("source", _P, "Allikas (nt excel_upload)"),
            FieldMapping("summary", _D, "Lovable'i kokkuvõte"),
            FieldMapping("years", _D, "Aastad"),
        ),
    ),
    "snapshot_themes": TableMapping(
        name="snapshot_themes",
        role=ROLE_HISTORY,
        description="Lovable'i hetkeseisu teemaread. Ainult hoiul.",
        required=frozenset({"id", "snapshot_id"}),
        stable_key_field="nr",
        fields=(
            FieldMapping("id", _P, "Rea UUID"),
            FieldMapping("snapshot_id", _P, "Hetkeseis"),
            FieldMapping("nr", MappingClass.IDENTITY, "Exceli NR", Canonical.REFERENCE),
            FieldMapping("source_sheet", _P, "Aastaleht"),
            FieldMapping("source_year", _D, "Aasta"),
            FieldMapping("vastutaja", MappingClass.REVIEW_ONLY, "Vastutaja eesnimi"),
            FieldMapping(
                "source_data",
                MappingClass.NEVER_AUTO,
                "Kogu Exceli rida, sh märkuste veerud",
                note="Märkus ei ole kunagi tõend ega arvamus.",
            ),
            FieldMapping("derived_data", _D, "Lovable'i arvutatud seis"),
            FieldMapping("is_active", _D, "Lovable'i aktiivsuse hinnang"),
            FieldMapping("next_deadline", _D, "Lovable'i tuletatud järgmine tähtaeg"),
            FieldMapping("deadline_status", _D, "Lovable'i tähtaja olek"),
            FieldMapping("days_to_deadline", _D, "Päevi tähtajani"),
            FieldMapping("is_overdue", _D, "Lovable'i hinnang: möödas"),
            FieldMapping("requires_attention", _D, "Lovable'i hinnang: vajab tähelepanu"),
            FieldMapping("attention_reasons", _D, "Lovable'i tähelepanu põhjused"),
        ),
    ),
}

#: Where the catalogue stands. Shown on the page and in the report.
CATALOGUE_STATUS = (
    "Derived from the August 2026 repository (Kauror/cheerful-control@f248d20b); "
    "live schema NOT verified."
)
CATALOGUE_STATUS_ET = (
    "Kataloog on tuletatud 2026. aasta augusti repositooriumist "
    "(Kauror/cheerful-control@f248d20b); Lovable'i elusskeemi EI OLE kontrollitud."
)

#: The tables the owner observed in the LIVE Lovable project on 2026-10-13
#: through a read-only connector — names only, no columns and no rows. The
#: August repository is therefore not the complete live schema.
#: `longterm_topics` is catalogued from the repository but was not in that
#: listing; whether it still exists live is unknown.
LIVE_TABLES_REPORTED: tuple[str, ...] = (
    "topic",
    "topics",
    "topic_change_event",
    "topic_change_field",
    "topic_commencement",
    "topic_responsible_change",
    "topic_link",
    "snapshots",
    "snapshot_themes",
    "datasets",
)


@dataclass(frozen=True)
class UnmappedTable:
    """A table known to exist live whose columns nobody has seen.

    The probable role is read from the table's *name* only. No column is
    invented: the column definitions are UNKNOWN until an operator-provided
    schema-only export (docs/lovable-mirror.md, «Schema-only export») has been
    reconciled with this catalogue. Until then a bundle carrying the table is
    refused.
    """

    name: str
    probable_role: str
    status: str = "NOT MAPPED"


#: Known live tables with no catalogue entry. Placeholders, NOT MAPPED.
UNMAPPED_LIVE_TABLES: dict[str, UnmappedTable] = {
    spec.name: spec
    for spec in (
        UnmappedTable(
            "topic",
            "Probably a single, longer-lived topic entity beside the per-upload `topics` "
            "rows; its relation to `topics` and to `nr` is unknown.",
        ),
        UnmappedTable(
            "topic_change_event",
            "Probably one row per recorded change to a topic (when, by whom, of what kind).",
        ),
        UnmappedTable(
            "topic_change_field",
            "Probably the field-level detail of a change event (field, old and new value).",
        ),
        UnmappedTable(
            "topic_commencement",
            "Probably entry-into-force (jõustumine) dates recorded per topic.",
        ),
        UnmappedTable(
            "topic_responsible_change",
            "Probably the history of the responsible person (vastutaja) of a topic.",
        ),
        UnmappedTable(
            "topic_link",
            "Probably links from a topic to another topic or to an external address.",
        ),
    )
}

#: Where the refusal of an uncatalogued table points the operator.
SCHEMA_EXPORT_PROCEDURE = "docs/lovable-mirror.md §«Schema-only export»"


def catalogue_refusal(name: str) -> str:
    """Why a table without a catalogue entry is refused, and what unblocks it."""
    if name in UNMAPPED_LIVE_TABLES:
        reason = (
            f"Tabel «{name}» on Lovable'i elusskeemis teada, kuid ei ole kataloogis "
            "(NOT MAPPED): selle veerud on teadmata."
        )
    else:
        reason = f"Tundmatu tabel «{name}»: see ei ole kataloogis."
    return (
        f"{reason} Kataloogimata tabelit ei impordita. Kõigepealt on vaja operaatori "
        f"skeemiekspordi (ainult skeem, ühtki rida) ülevaatust: {SCHEMA_EXPORT_PROCEDURE}."
    )


def table(name: str) -> TableMapping | None:
    return TABLES.get(name)


def all_field_mappings() -> list[tuple[str, FieldMapping]]:
    return [(name, mapping) for name, spec in TABLES.items() for mapping in spec.fields]
