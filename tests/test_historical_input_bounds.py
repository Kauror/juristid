"""Historical readers stop at a limit while they read, not after (docs/adr/0184).

Finding csf_c79fda7c380313bb74cf517b of the 2026-10-22 round-2 review: round 1
bounded ZIPs and workbooks (docs/adr/0178), but the OneNote archive's JSON and
text, the migration audit's CSV and JSON, the evidence package's
``package.json`` and every directory listing were still read whole, and the
opinions-directory reader sorted its whole walk before counting it.

Every limit is lowered through settings so the sources stay tiny. Where the
point is *when* a reader stops, the test counts what it consumed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.legacy_import import evidence_pilot, limits, safe_paths
from app.legacy_import.historical_audit import MigrationAudit
from app.legacy_import.integrity import build_manifest
from app.legacy_import.limits import (
    SourceLimitExceeded,
    WorkBudget,
    current_limits,
    load_json_document,
    read_csv_document,
)
from app.legacy_import.onenote_archive import ArchiveError, OneNoteArchive
from app.legacy_import.opinion_sources import OpinionSourceError, read_opinion_archive
from app.legacy_import.resolution import MappingFileError, MappingTables
from tests import synthetic_historical
from tests import synthetic_opinions as syn


@pytest.fixture
def scandir_counter(monkeypatch):
    """How many directory entries readers actually pulled from `os.scandir`."""
    consumed = {"entries": 0}
    real = os.scandir

    class Counting:
        def __init__(self, path):
            self._inner = real(path)

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *exc):
            return self._inner.__exit__(*exc)

        def __iter__(self):
            for entry in self._inner:
                consumed["entries"] += 1
                yield entry

    monkeypatch.setattr(safe_paths.os, "scandir", Counting)
    return consumed


def _many_files(root: Path, count: int) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for number in range(count):
        (root / f"{number:04d}.txt").write_bytes(b"x")
    return root


# ---------------------------------------------------------------------------
# Listings stop at one past the limit
# ---------------------------------------------------------------------------


def test_a_directory_listing_stops_at_one_past_its_limit(tmp_path, scandir_counter):
    root = _many_files(tmp_path / "many", 200)
    with pytest.raises(SourceLimitExceeded, match="the limit is 5"):
        safe_paths.scan_directory(root, root, max_members=5)
    assert scandir_counter["entries"] == 6


def test_a_tree_walk_stops_at_one_past_its_file_limit(tmp_path, scandir_counter):
    root = tmp_path / "tree"
    for directory in range(10):
        _many_files(root / f"d{directory}", 20)
    with pytest.raises(SourceLimitExceeded, match="the limit is 7"):
        list(safe_paths.iter_files(root, max_files=7, max_directory_members=1000))
    assert scandir_counter["entries"] < 40  # at most two directories looked into


def test_the_opinions_directory_is_counted_while_walked_never_sorted_whole(
    tmp_path, settings, scandir_counter, monkeypatch
):
    root = tmp_path / "opinions"
    root.mkdir()
    for number in range(50):
        (root / f"2023-05-{number:02d} - Näidisamet - kiri {number}.pdf").write_bytes(
            syn.pdf_bytes(str(number))
        )
    settings.LEGACY_IMPORT_MAX_ARCHIVE_MEMBERS = 3
    with pytest.raises(OpinionSourceError, match="the limit is 3"):
        read_opinion_archive(root)
    assert scandir_counter["entries"] == 4


def test_an_archive_with_too_many_pages_is_refused_while_listing(tmp_path, settings):
    root = synthetic_historical.build_archive(tmp_path / "archive")
    settings.LEGACY_IMPORT_MAX_PAGES = 3
    with pytest.raises(SourceLimitExceeded, match="the limit is 3"):
        OneNoteArchive(root).page_keys()


def test_a_page_with_too_many_resources_is_refused_while_listing(tmp_path, settings):
    root = synthetic_historical.build_archive(tmp_path / "archive")
    holder = root / "pages/p-exact/resources"
    for number in range(4):
        (holder / f"r-extra-{number}").mkdir()
    settings.LEGACY_IMPORT_MAX_PAGE_RESOURCES = 2
    with pytest.raises(SourceLimitExceeded, match="the limit is 2"):
        OneNoteArchive(root).page("p-exact")


# ---------------------------------------------------------------------------
# Documents are bounded before they are parsed
# ---------------------------------------------------------------------------


def test_a_json_document_past_its_limit_is_never_parsed(monkeypatch, settings):
    settings.LEGACY_IMPORT_MAX_JSON_BYTES = 10

    def refuse(*args, **kwargs):
        raise AssertionError("parsed before the size was checked")

    monkeypatch.setattr(limits.json, "loads", refuse)
    with pytest.raises(SourceLimitExceeded, match="the limit is 10"):
        load_json_document(b'{"a": "0123456789"}', what="page.json")


def test_a_json_document_nested_past_the_interpreter_is_refused():
    deep = b"[" * 100_000 + b"]" * 100_000
    with pytest.raises(SourceLimitExceeded, match="nested too deeply"):
        load_json_document(deep, what="blocks.json")


def test_an_archive_page_json_past_its_limit_is_refused(tmp_path, settings):
    root = synthetic_historical.build_archive(tmp_path / "archive")
    settings.LEGACY_IMPORT_MAX_JSON_BYTES = 64
    with pytest.raises(ArchiveError, match="larger than the 64-byte limit"):
        OneNoteArchive(root).page("p-exact")


def test_csv_rows_and_fields_are_bounded(settings):
    settings.LEGACY_IMPORT_MAX_CSV_ROWS = 2
    rows = "a,b\n" + "1,2\n" * 3
    with pytest.raises(SourceLimitExceeded, match="the limit is 2"):
        read_csv_document(rows.encode(), what="exact-matches.csv")

    settings.LEGACY_IMPORT_MAX_CSV_ROWS = 100
    settings.LEGACY_IMPORT_MAX_CSV_FIELD_CHARS = 5
    with pytest.raises(SourceLimitExceeded, match="the limit is 5"):
        read_csv_document(b"a,b\n123456,1\n", what="exact-matches.csv")

    settings.LEGACY_IMPORT_MAX_CSV_BYTES = 4
    with pytest.raises(SourceLimitExceeded, match="the limit is 4"):
        read_csv_document(b"a,b\n1,2\n", what="exact-matches.csv")


def test_a_field_past_the_csv_readers_own_limit_is_a_refusal_not_a_crash():
    huge = b"a\n" + b"x" * 200_000 + b"\n"
    with pytest.raises(SourceLimitExceeded, match=r"cannot be read as CSV|field longer"):
        read_csv_document(huge, what="review-required.csv")


def test_the_audit_reads_its_reports_within_limits(tmp_path, settings):
    corpus = synthetic_historical.build_corpus(tmp_path / "source")
    settings.LEGACY_IMPORT_MAX_CSV_ROWS = 1
    with pytest.raises(SourceLimitExceeded, match="the limit is 1"):
        MigrationAudit(corpus["audit_root"]).page_profiles()


def test_the_audit_reports_exactly_what_it_parsed(tmp_path):
    corpus = synthetic_historical.build_corpus(tmp_path / "source")
    audit = MigrationAudit(corpus["audit_root"])
    audit.page_profiles()
    consumed = audit.consumed()
    profile = "reports/onenote/onenote-summary.csv"
    data = (corpus["audit_root"] / profile).read_bytes()
    assert consumed.entries[profile].size == len(data)


def test_a_package_json_past_its_limit_is_refused(tmp_path, settings):
    root = tmp_path / "package"
    root.mkdir()
    (root / "package.json").write_text(json.dumps({"topics": ["x" * 200]}), encoding="utf-8")
    settings.LEGACY_IMPORT_MAX_JSON_BYTES = 100
    with pytest.raises(evidence_pilot.EvidencePilotError, match="100-byte limit"):
        evidence_pilot.load_package(root)


def test_a_mapping_file_past_its_limit_is_refused(tmp_path, settings):
    mapping = tmp_path / "mapping.toml"
    mapping.write_text("[owners]\n" + '"a" = "b"\n' * 50, encoding="utf-8")
    settings.LEGACY_IMPORT_MAX_JSON_BYTES = 100
    with pytest.raises(MappingFileError, match="larger than a mapping file can be"):
        MappingTables.load(mapping)


# ---------------------------------------------------------------------------
# A whole source, and a whole run
# ---------------------------------------------------------------------------


def test_a_source_tree_past_its_total_is_refused(tmp_path, settings):
    root = _many_files(tmp_path / "tree", 10)
    settings.LEGACY_IMPORT_MAX_TOTAL_SOURCE_BYTES = 5
    with pytest.raises(SourceLimitExceeded, match="the limit is 5"):
        build_manifest(root)


def test_a_run_stops_at_its_work_budget(tmp_path, settings):
    root = synthetic_historical.build_archive(tmp_path / "archive")
    settings.LEGACY_IMPORT_MAX_WORK_BYTES = 2_000
    archive = OneNoteArchive(root, budget=WorkBudget(current_limits()))
    with pytest.raises(SourceLimitExceeded, match="bytes of source; the limit is 2000"):
        for _ in range(100):
            archive.page("p-exact")


def test_the_defaults_are_far_above_the_measured_corpus():
    """Measured 2026-10-22 on the reviewed v2.3 package and round 1's corpus (ADR 0184)."""
    defaults = current_limits()
    measured = {
        "max_json_bytes": 359_541,
        "max_text_bytes": 8_756,
        "max_member_bytes": 7_133_408,
        "max_pages": 755,
        "max_page_resources": 44,
        "max_directory_members": 767,
        "max_source_files": 767,
        "max_total_source_bytes": int(4.14 * 1024**3),
    }
    for name, value in measured.items():
        assert getattr(defaults, name) >= 7 * value, name
