"""The database must actually be able to do Estonian search.

PostgreSQL 18 is locked as the launch baseline specifically for this. Proving
it in CI now avoids discovering the opposite in Stage 2.
"""

from __future__ import annotations

import pytest

from app.search.capabilities import (
    ESTONIAN_TEXT_SEARCH_CONFIG,
    REQUIRED_EXTENSIONS,
    build_report,
    installed_extensions,
    lexemes,
    postgresql_version,
    text_search_configurations,
)

pytestmark = pytest.mark.django_db


def test_postgresql_is_at_least_18():
    major, _minor = postgresql_version()
    assert major >= 18


@pytest.mark.parametrize("extension", REQUIRED_EXTENSIONS)
def test_required_extensions_are_installed(extension):
    assert extension in installed_extensions()


def test_estonian_text_search_configuration_exists():
    assert ESTONIAN_TEXT_SEARCH_CONFIG in text_search_configurations()


def test_estonian_configuration_stems_inflected_forms():
    singular = lexemes("eelnõu")
    inflected = lexemes("eelnõule")
    assert singular
    assert set(singular) & set(inflected), (
        f"Expected a shared stem between {singular} and {inflected}"
    )


def test_capability_report_is_satisfied():
    report = build_report()
    assert report.ok, report
