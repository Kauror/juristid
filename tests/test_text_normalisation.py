"""Diacritic-free and case-insensitive matching for Estonian input."""

from __future__ import annotations

import pytest

from app.core.text import normalize_for_matching


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Õigusloome", "oigusloome"),
        ("ÕIGUSLOOME", "oigusloome"),
        ("oigusloome", "oigusloome"),
        ("  Käibemaksu   seadus ", "kaibemaksu seadus"),
        ("Tööstuskoda", "toostuskoda"),
        ("", ""),
    ],
)
def test_normalisation_is_stable_across_spelling(value, expected):
    assert normalize_for_matching(value) == expected


def test_diacritic_and_diacritic_free_input_match():
    assert normalize_for_matching("Rahandusministeerium") == normalize_for_matching(
        "rahandusministeerium"
    )
    assert normalize_for_matching("Sotsiaalministeeriumi Ülevaade") == normalize_for_matching(
        "sotsiaalministeeriumi ulevaade"
    )
