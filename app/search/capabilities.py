"""Probes for the database capabilities Estonian search depends on.

The product locks PostgreSQL 18+ specifically because the Estonian full-text
search configuration must be present. These probes turn that assumption into
something CI can fail on, rather than something Stage 2 discovers.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.db import connection

REQUIRED_EXTENSIONS = ("pg_trgm", "unaccent")
ESTONIAN_TEXT_SEARCH_CONFIG = "estonian"


@dataclass(frozen=True)
class CapabilityReport:
    postgresql_version: tuple[int, int]
    version_ok: bool
    missing_extensions: tuple[str, ...]
    has_estonian_configuration: bool
    estonian_lexemes: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.version_ok and not self.missing_extensions and self.has_estonian_configuration


def postgresql_version() -> tuple[int, int]:
    number: int = connection.pg_version  # type: ignore[attr-defined]  # e.g. 180002
    return number // 10000, (number % 10000) // 100


def installed_extensions() -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT extname FROM pg_extension")
        return {row[0] for row in cursor.fetchall()}


def text_search_configurations() -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT cfgname FROM pg_ts_config")
        return {row[0] for row in cursor.fetchall()}


def lexemes(text: str, configuration: str = ESTONIAN_TEXT_SEARCH_CONFIG) -> list[str]:
    """Return the stemmed lexemes PostgreSQL produces for ``text``."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT lexeme FROM unnest(to_tsvector(%s::regconfig, %s)) ORDER BY lexeme",
            [configuration, text],
        )
        return [row[0] for row in cursor.fetchall()]


def build_report(sample: str = "õigusloome eelnõude kooskõlastamine") -> CapabilityReport:
    version = postgresql_version()
    installed = installed_extensions()
    configurations = text_search_configurations()
    has_estonian = ESTONIAN_TEXT_SEARCH_CONFIG in configurations

    return CapabilityReport(
        postgresql_version=version,
        version_ok=version >= settings.MINIMUM_POSTGRESQL_VERSION,
        missing_extensions=tuple(e for e in REQUIRED_EXTENSIONS if e not in installed),
        has_estonian_configuration=has_estonian,
        estonian_lexemes=tuple(lexemes(sample)) if has_estonian else (),
    )
