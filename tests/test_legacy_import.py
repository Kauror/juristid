"""Historical provenance is preserved verbatim, including its anomalies."""

from __future__ import annotations

import pytest

from app.core.errors import ImmutableRecordError
from app.legacy_import.models import MatterSourceReference
from tests import factories

pytestmark = pytest.mark.django_db

# Real workbook anomalies the specification insists must survive import
# (master specification 19.3, 22.7).
ANOMALOUS_ROW = {
    "NR": "2019_14",
    "PEALKIRI": "Näidiseelnõu",
    "KELLELT": "Endine Näidisministeerium",
    "SAABUS": "43831",  # Excel serial left as a string
    "TÄHTAEG": "",  # blank, which is not the same as zero
    "KÜSITUD": "0",
    "VASTAS": "3",  # answered more than asked
    "MENETLUSAEG": "-4",  # negative interval
}


def test_raw_source_values_are_stored_exactly_as_supplied():
    reference = factories.MatterSourceReferenceFactory(
        source_row_raw=ANOMALOUS_ROW,
        source_date_raw="43831",
        source_title="Näidiseelnõu",
    )
    reference.refresh_from_db()

    assert reference.source_row_raw == ANOMALOUS_ROW
    assert reference.source_row_raw["VASTAS"] == "3"
    assert reference.source_row_raw["KÜSITUD"] == "0"
    assert reference.source_row_raw["MENETLUSAEG"] == "-4"
    assert reference.source_row_raw["TÄHTAEG"] == ""
    assert reference.source_date_raw == "43831"


def test_blank_and_zero_source_values_stay_different():
    reference = factories.MatterSourceReferenceFactory(source_row_raw=ANOMALOUS_ROW)
    assert reference.source_row_raw["TÄHTAEG"] != reference.source_row_raw["KÜSITUD"]


def test_imported_raw_values_cannot_be_edited_afterwards():
    reference = factories.MatterSourceReferenceFactory(source_row_raw=ANOMALOUS_ROW)
    reference.source_row_raw = {"NR": "korrastatud"}
    with pytest.raises(ImmutableRecordError):
        reference.save()

    reference.refresh_from_db()
    reference.source_date_raw = "2019-12-31"
    with pytest.raises(ImmutableRecordError):
        reference.save()


def test_interpretation_fields_remain_reviewable():
    reference = factories.MatterSourceReferenceFactory(source_row_raw=ANOMALOUS_ROW)
    reference.reviewed_by = "Osakonnajuht"
    reference.review_note = "Kinnitatud käsitsi."
    reference.save()
    reference.refresh_from_db()
    assert reference.reviewed_by == "Osakonnajuht"


def test_the_direction_column_name_is_preserved_per_era():
    """`KELLELT` (2011–2019) and `KELLELE` (2020+) must never be unified."""
    older = factories.MatterSourceReferenceFactory(
        source_sheet="2019", source_row_raw={"KELLELT": "Näidisministeerium"}
    )
    newer = factories.MatterSourceReferenceFactory(
        source_sheet="2021", source_row_raw={"KELLELE": "Näidisministeerium"}
    )
    assert "KELLELT" in older.source_row_raw
    assert "KELLELE" in newer.source_row_raw
    assert "KELLELT" not in newer.source_row_raw


def test_a_matter_keeps_every_source_reference_it_was_built_from():
    matter = factories.MatterFactory()
    factories.MatterSourceReferenceFactory(matter=matter, source_sheet="2019")
    factories.MatterSourceReferenceFactory(matter=matter, source_sheet="2020")
    assert MatterSourceReference.objects.filter(matter=matter).count() == 2
