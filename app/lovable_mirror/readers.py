"""Where a Lovable snapshot comes from: the refresh-adapter seam.

The importer (:mod:`app.lovable_mirror.importer`) does not know or care how the
rows were obtained. It takes an :class:`ExportBundle` — verified table rows plus
the metadata a snapshot records — and writes only to the mirror tables. A
*reader* produces that bundle.

Two readers are defined:

* :class:`FileExportReader` — **the working v1.** Reads an operator-provided
  export bundle (``manifest.json`` + one JSON file per table) from a directory
  outside the checkout, verifies every checksum, and refuses anything malformed.
* :class:`LiveReadOnlyReader` — **disabled, unimplemented.** The shape a future
  read-only database role or API will plug into once approved access exists
  (owner decision of 2026-10-13: access will be provided later). It makes no
  connection, holds no credential, and refuses unless the deployment both
  enables it by setting and supplies an implementation. See
  docs/lovable-mirror.md §"Live read-only refresh" for the exact access shape.

Nothing here ever accepts or looks for a Supabase service-role key. A reader
that needed one would be a writer.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from django.conf import settings
from django.utils.dateparse import parse_datetime

from app.lovable_mirror import catalogue
from app.lovable_mirror.models import DataClass

BUNDLE_FORMAT = "juristid.lovable-mirror-export"
SUPPORTED_FORMAT_VERSIONS: frozenset[int] = frozenset({1})
MANIFEST_NAME = "manifest.json"

#: One table file larger than this is refused rather than read into memory.
#: The whole alpha is a few thousand rows; a file this size is not an export
#: of it.
MAX_TABLE_BYTES = 256 * 1024 * 1024

#: The setting that would enable a live reader. **Off unless set**, and not
#: declared in config/settings.py: the coordinator owns that file and nothing
#: is to be enabled yet. Read with ``getattr(..., False)``.
LIVE_READER_SETTING = "LOVABLE_MIRROR_LIVE_READER_ENABLED"

READER_FILE_EXPORT = "FILE_EXPORT"
READER_LIVE_READ_ONLY = "LIVE_READ_ONLY"


class BundleRefused(Exception):
    """The bundle cannot be imported as it stands. Nothing has been written."""


class ReaderUnavailable(Exception):
    """No configured, authorised reader exists for this request."""


@dataclass(frozen=True)
class ExportBundle:
    """Verified rows plus the metadata one `SourceSnapshot` records."""

    checksum: str
    reader: str
    format_version: int
    data_class: str
    source_schema_version: str
    exported_at: datetime
    exported_by: str
    export_method: str
    label: str
    notes: str
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


class SourceReader(Protocol):
    """Anything that can produce one verified :class:`ExportBundle`."""

    name: str

    def read(self) -> ExportBundle: ...


# ---------------------------------------------------------------------------
# Path guard
# ---------------------------------------------------------------------------


def refuse_inside_checkout(path: Path) -> Path:
    """An export carries real register content; it may not sit where Git can see it.

    The same rule `reference_data --unresolved-out` and `backfill_legacy_owners`
    apply to what they *write*, applied here to what is *read*: a bundle under
    the checkout is one ``git add`` from a pull request, so the command refuses
    rather than relying on .gitignore.
    """
    resolved = path.expanduser().resolve()
    checkout = Path(settings.BASE_DIR).resolve()
    if resolved == checkout or checkout in resolved.parents:
        raise BundleRefused(
            f"{resolved} on repositooriumi sees. Lovable'i eksport peab asuma väljaspool "
            "repositooriumi, heakskiidetud päris andmete keskkonnas."
        )
    return resolved


# ---------------------------------------------------------------------------
# The file export reader (v1)
# ---------------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _text(manifest: dict[str, Any], key: str, *, required: bool = True) -> str:
    value = manifest.get(key, "")
    if not isinstance(value, str) or (required and not value.strip()):
        if required:
            raise BundleRefused(f"Manifestis puudub kohustuslik tekstiväli «{key}».")
        raise BundleRefused(f"Manifesti väli «{key}» peab olema tekst.")
    return value.strip()


class FileExportReader:
    """Reads ``<bundle>/manifest.json`` and the table files it names."""

    name = READER_FILE_EXPORT

    def __init__(self, path: str | Path, *, expected_sha256: str = "") -> None:
        self.path = Path(path)
        self.expected_sha256 = expected_sha256.strip().lower()

    def read(self) -> ExportBundle:
        directory = refuse_inside_checkout(self.path)
        if not directory.is_dir():
            raise BundleRefused(f"{directory} ei ole kaust.")
        manifest_path = directory / MANIFEST_NAME
        if not manifest_path.is_file():
            raise BundleRefused(f"Kaustas {directory.name} puudub {MANIFEST_NAME}.")

        raw_manifest = manifest_path.read_bytes()
        checksum = sha256_bytes(raw_manifest)
        if self.expected_sha256 and checksum != self.expected_sha256:
            raise BundleRefused(
                f"Manifesti SHA-256 on {checksum}, oodati {self.expected_sha256}. "
                "Importida võib ainult seda pakki, mille operaator nimetas."
            )

        try:
            manifest = json.loads(raw_manifest.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BundleRefused(f"{MANIFEST_NAME} ei ole korrektne JSON: {error}") from error
        if not isinstance(manifest, dict):
            raise BundleRefused(f"{MANIFEST_NAME} peab olema JSON-objekt.")

        if manifest.get("format") != BUNDLE_FORMAT:
            raise BundleRefused(f"Manifesti «format» peab olema «{BUNDLE_FORMAT}».")
        version = manifest.get("format_version")
        if version not in SUPPORTED_FORMAT_VERSIONS or isinstance(version, bool):
            raise BundleRefused(f"Vormingu versiooni {version!r} ei toetata.")

        data_class = _text(manifest, "data_class")
        if data_class not in DataClass.values:
            raise BundleRefused(f"«data_class» peab olema üks: {', '.join(DataClass.values)}.")

        exported_at = parse_datetime(_text(manifest, "exported_at"))
        if exported_at is None or exported_at.tzinfo is None:
            raise BundleRefused("«exported_at» peab olema ISO 8601 aeg koos ajavööndiga.")

        tables_spec = manifest.get("tables")
        if not isinstance(tables_spec, dict) or not tables_spec:
            raise BundleRefused("Manifesti «tables» peab nimetama vähemalt ühe tabeli.")

        tables: dict[str, list[dict[str, Any]]] = {}
        for table_name, spec in sorted(tables_spec.items()):
            tables[table_name] = self._read_table(directory, table_name, spec)

        return ExportBundle(
            checksum=checksum,
            reader=self.name,
            format_version=int(version),
            data_class=data_class,
            source_schema_version=_text(manifest, "source_schema_version"),
            exported_at=exported_at,
            exported_by=_text(manifest, "exported_by", required=False),
            export_method=_text(manifest, "export_method", required=False),
            label=directory.name[:200],
            notes=_text(manifest, "notes", required=False),
            tables=tables,
        )

    def _read_table(self, directory: Path, name: str, spec: Any) -> list[dict[str, Any]]:
        if catalogue.table(name) is None:
            raise BundleRefused(catalogue.catalogue_refusal(name))
        if not isinstance(spec, dict):
            raise BundleRefused(f"Tabeli «{name}» kirjeldus peab olema objekt.")

        file_name = spec.get("file")
        if (
            not isinstance(file_name, str)
            or not file_name
            or Path(file_name).name != file_name
            or file_name in {".", "..", MANIFEST_NAME}
        ):
            raise BundleRefused(f"Tabeli «{name}» fail peab olema pelgalt failinimi paki kaustas.")
        expected = spec.get("sha256")
        if not isinstance(expected, str) or len(expected) != 64:
            raise BundleRefused(f"Tabeli «{name}» SHA-256 puudub või on vigane.")
        row_count = spec.get("row_count")
        if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 0:
            raise BundleRefused(f"Tabeli «{name}» row_count peab olema mittenegatiivne täisarv.")

        path = directory / file_name
        if not path.is_file():
            raise BundleRefused(f"Tabeli «{name}» fail {file_name} puudub.")
        if path.stat().st_size > MAX_TABLE_BYTES:
            raise BundleRefused(f"Tabeli «{name}» fail on suurem kui {MAX_TABLE_BYTES} baiti.")
        data = path.read_bytes()
        actual = sha256_bytes(data)
        if actual != expected.lower():
            raise BundleRefused(
                f"Tabeli «{name}» faili SHA-256 on {actual}, manifest ütleb {expected}. "
                "Pakki on pärast eksporti muudetud või see on poolik."
            )
        try:
            rows = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BundleRefused(f"Tabeli «{name}» fail ei ole korrektne JSON: {error}") from error
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise BundleRefused(f"Tabeli «{name}» fail peab olema JSON-objektide massiiv.")
        if len(rows) != row_count:
            raise BundleRefused(
                f"Tabelis «{name}» on {len(rows)} rida, manifest ütleb {row_count}."
            )
        return rows


# ---------------------------------------------------------------------------
# The live read-only reader (disabled stub)
# ---------------------------------------------------------------------------


def live_reader_enabled() -> bool:
    """Whether this deployment says a live reader is configured. Off by default."""
    return bool(getattr(settings, LIVE_READER_SETTING, False))


class LiveReadOnlyReader:
    """The future live refresh. Deliberately not implemented.

    When approved access exists, this reader will connect as a **read-only
    database role** (or a read-only API) scoped to exactly the catalogued
    tables and columns, read them in one repeatable-read transaction, compute
    the bundle checksum over the canonical JSON of what it read, and return an
    :class:`ExportBundle` — so the importer, the immutability rules and the
    mirror tables are exactly the ones the file path uses.

    Until then it refuses in every configuration. It does not open a socket,
    resolve a host or read an environment variable holding a credential.
    """

    name = READER_LIVE_READ_ONLY

    def read(self) -> ExportBundle:
        if not live_reader_enabled():
            raise ReaderUnavailable(
                "Otselugemine Lovable'ist on välja lülitatud. Kasuta operaatori eksporti "
                "(--export) või seadista heakskiidetud ainult-lugemisõigusega lugeja."
            )
        raise ReaderUnavailable(
            f"{LIVE_READER_SETTING} on sisse lülitatud, kuid otselugejat ei ole veel "
            "ehitatud. Vaata docs/lovable-mirror.md «Live read-only refresh»."
        )
