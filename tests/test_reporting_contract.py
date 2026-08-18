"""Guards on the reporting contract and the metrics it may never express.

The consultation `contacted` and `response` counts in the historical register
are independent observations with no guaranteed common denominator. Turning
them into a response rate would manufacture a statistic the source cannot
support (master specification 2.1, 6.3, 18.8). These tests make that rule
executable rather than merely written down.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.apps import apps

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
EXPORT_CONTRACT = REPOSITORY_ROOT / "docs" / "data-contracts" / "dashkoda-export-v1.md"
METRIC_CATALOGUE = REPOSITORY_ROOT / "docs" / "metric-catalog" / "README.md"

FORBIDDEN_FIELD_PATTERNS = (
    "response_rate",
    "win_rate",
    "productivity",
    "influence_score",
)


def test_the_export_contract_exists_and_is_versioned():
    text = EXPORT_CONTRACT.read_text(encoding="utf-8")
    assert re.search(r"^version:\s*1\b", text, flags=re.MULTILINE)


def test_the_export_contract_keeps_consultation_counts_independent():
    text = EXPORT_CONTRACT.read_text(encoding="utf-8").lower()
    assert "independent" in text
    assert "does not export a response rate" in text
    assert "legacy consultation response rate" in text

    # No table row may define a response-rate field.
    table_rows = [line for line in text.splitlines() if line.startswith("|")]
    assert not any("response_rate" in row for row in table_rows)


def test_the_export_contract_derives_the_sent_date_from_submissions():
    text = EXPORT_CONTRACT.read_text(encoding="utf-8").lower()
    assert "submission" in text
    assert "derived" in text


def test_the_metric_catalogue_requires_coverage():
    text = METRIC_CATALOGUE.read_text(encoding="utf-8").lower()
    for required in ("coverage", "source population", "earliest reliable period"):
        assert required in text


@pytest.mark.parametrize("pattern", FORBIDDEN_FIELD_PATTERNS)
def test_no_model_stores_a_prohibited_derived_metric(pattern):
    offending = [
        f"{model._meta.label}.{field.name}"
        for model in apps.get_models()
        for field in model._meta.get_fields()
        if pattern in getattr(field, "name", "")
    ]
    assert offending == [], (
        f"{offending} looks like a prohibited derived metric stored as canonical data."
    )
