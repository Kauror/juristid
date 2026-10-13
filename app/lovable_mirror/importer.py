"""Write one verified Lovable bundle into the mirror tables. Nothing else.

The write boundary of this app. It creates `SourceSnapshot`, `SourceRecord` and
`SourceFieldValue` rows inside one transaction and touches no other table: no
Matter, Submission, NextAction, document, owner or audit row is read for
writing or written (tests/test_lovable_mirror.py proves the canonical tables are
unchanged by an import).

**Idempotent by checksum.** The snapshot's identity is the SHA-256 of the
bundle's manifest, which pins every table file's own SHA-256. Importing the same
bundle again finds the existing snapshot and writes nothing. Two different
bundles exported at the same moment are two snapshots; the checksum, not the
timestamp, says whether bytes are the same.

**All or nothing.** Every row is validated before the first insert; a refusal
leaves the database exactly as it was.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction

from app.lovable_mirror import catalogue
from app.lovable_mirror.models import (
    DataClass,
    SourceFieldValue,
    SourceRecord,
    SourceSnapshot,
)
from app.lovable_mirror.readers import BundleRefused, ExportBundle, SourceReader

BATCH_SIZE = 1000


@dataclass(frozen=True)
class ImportResult:
    snapshot: SourceSnapshot
    created: bool


def canonical_json(value: Any) -> str:
    """Sorted keys, no whitespace: the same row always hashes the same."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _key_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool | dict | list):
        raise BundleRefused(f"Võtmeväärtus {value!r} peab olema tekst või arv.")
    return str(value).strip()


@dataclass(frozen=True)
class _PreparedRow:
    table: str
    source_id: str
    stable_key: str
    row_sha256: str
    values: dict[str, Any]


def _prepare(bundle: ExportBundle) -> tuple[list[_PreparedRow], dict[str, list[str]]]:
    prepared: list[_PreparedRow] = []
    uncatalogued: dict[str, list[str]] = {}
    for table_name, rows in bundle.tables.items():
        spec = catalogue.table(table_name)
        if spec is None:  # the reader refuses this first; kept for any other reader
            raise BundleRefused(f"Tundmatu tabel «{table_name}».")
        seen: set[str] = set()
        unknown: set[str] = set()
        for index, row in enumerate(rows, start=1):
            missing = sorted(spec.required - row.keys())
            if missing:
                raise BundleRefused(
                    f"Tabeli «{table_name}» real {index} puuduvad väljad: {', '.join(missing)}."
                )
            source_id = _key_text(row.get(spec.id_field))
            if not source_id:
                raise BundleRefused(f"Tabeli «{table_name}» real {index} puudub ID.")
            if len(source_id) > 200:
                raise BundleRefused(f"Tabeli «{table_name}» real {index} on ID liiga pikk.")
            if source_id in seen:
                raise BundleRefused(f"Tabelis «{table_name}» esineb ID {source_id} mitu korda.")
            seen.add(source_id)
            for name in row:
                if not isinstance(name, str) or not name or len(name) > 128:
                    raise BundleRefused(f"Tabeli «{table_name}» real {index} on vigane veerunimi.")
                if spec.field(name) is None:
                    unknown.add(name)
            prepared.append(
                _PreparedRow(
                    table=table_name,
                    source_id=source_id,
                    stable_key=_key_text(row.get(spec.stable_key_field))[:200],
                    row_sha256=hashlib.sha256(canonical_json(row).encode("utf-8")).hexdigest(),
                    values=row,
                )
            )
        if unknown:
            uncatalogued[table_name] = sorted(unknown)
    return prepared, uncatalogued


def import_bundle(bundle: ExportBundle) -> ImportResult:
    """Store one bundle as an immutable snapshot, or find the one already stored."""
    existing = SourceSnapshot.objects.filter(manifest_sha256=bundle.checksum).first()
    if existing is not None:
        return ImportResult(snapshot=existing, created=False)

    if bundle.data_class == DataClass.REAL and not settings.REAL_DATA_ALLOWED:
        raise BundleRefused(
            "Pakk on märgitud päris andmeteks, kuid see keskkond ei luba päris andmeid "
            "(REAL_DATA_ALLOWED on väljas). Lovable'i päris eksporti tohib importida ainult "
            "turvalise piloodi nõuetele vastavasse keskkonda (docs/secure-pilot-gate.md)."
        )

    prepared, uncatalogued = _prepare(bundle)

    try:
        with transaction.atomic():
            snapshot = SourceSnapshot.objects.create(
                manifest_sha256=bundle.checksum,
                format_version=bundle.format_version,
                data_class=bundle.data_class,
                source_schema_version=bundle.source_schema_version[:200],
                catalogue_version=catalogue.CATALOGUE_VERSION,
                reader=bundle.reader,
                source_exported_at=bundle.exported_at,
                exported_by=bundle.exported_by[:200],
                export_method=bundle.export_method[:400],
                bundle_label=bundle.label[:200],
                row_counts={name: len(rows) for name, rows in sorted(bundle.tables.items())},
                uncatalogued_fields=uncatalogued,
                notes=bundle.notes,
            )
            for start in range(0, len(prepared), BATCH_SIZE):
                chunk = prepared[start : start + BATCH_SIZE]
                records = SourceRecord.objects.bulk_create(
                    [
                        SourceRecord(
                            snapshot=snapshot,
                            table=row.table,
                            source_id=row.source_id,
                            stable_key=row.stable_key,
                            row_sha256=row.row_sha256,
                        )
                        for row in chunk
                    ]
                )
                values: list[SourceFieldValue] = []
                for record, row in zip(records, chunk, strict=True):
                    spec = catalogue.TABLES[row.table]
                    values.extend(
                        SourceFieldValue(
                            record=record,
                            field=name,
                            value=value,
                            mapping_class=spec.class_of(name),
                        )
                        for name, value in sorted(row.values.items())
                    )
                SourceFieldValue.objects.bulk_create(values, batch_size=BATCH_SIZE)
    except IntegrityError:
        # A concurrent import of the same bytes won the unique checksum. Its
        # snapshot is the answer; ours rolled back whole.
        winner = SourceSnapshot.objects.filter(manifest_sha256=bundle.checksum).first()
        if winner is None:
            raise
        return ImportResult(snapshot=winner, created=False)

    return ImportResult(snapshot=snapshot, created=True)


def import_from(reader: SourceReader) -> ImportResult:
    """Read with any reader and store the result. The one entry point commands use."""
    return import_bundle(reader.read())
