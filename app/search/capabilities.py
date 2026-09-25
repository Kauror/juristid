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

#: The fuzzy tier's own threshold (`app.search.services.TRIGRAM_THRESHOLD`),
#: repeated here because this module must not import the search service.
#: `test_search_capabilities` holds the two equal.
FUZZY_THRESHOLD = 0.6


@dataclass(frozen=True)
class CapabilityReport:
    postgresql_version: tuple[int, int]
    version_ok: bool
    missing_extensions: tuple[str, ...]
    has_estonian_configuration: bool
    estonian_lexemes: tuple[str, ...]
    #: `pg_trgm.word_similarity_threshold`, or ``None`` without pg_trgm.
    word_similarity_threshold: float | None = None

    @property
    def fuzzy_index_ok(self) -> bool:
        """Whether the trigram index can serve the whole fuzzy tier.

        The tier's candidates come from the ``%>`` operator, which compares
        against this server setting, and the tier's own rule then requires a
        similarity of :data:`FUZZY_THRESHOLD`. A setting at or below the rule
        makes the operator's answer a superset of the rule's; one above it
        would drop real matches before the rule ever saw them (ENG-010).
        """
        return (
            self.word_similarity_threshold is not None
            and self.word_similarity_threshold <= FUZZY_THRESHOLD
        )

    @property
    def ok(self) -> bool:
        return (
            self.version_ok
            and not self.missing_extensions
            and self.has_estonian_configuration
            and self.fuzzy_index_ok
        )


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


def word_similarity_threshold() -> float:
    """The server's `pg_trgm.word_similarity_threshold`.

    The setting exists only once the pg_trgm library is loaded into the
    session, so a pg_trgm function is called first.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT word_similarity('a', 'a'), "
            "current_setting('pg_trgm.word_similarity_threshold')::float"
        )
        return float(cursor.fetchone()[1])


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
        word_similarity_threshold=(word_similarity_threshold() if "pg_trgm" in installed else None),
    )
