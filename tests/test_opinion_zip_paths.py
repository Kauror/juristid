r"""How a ZIP member name becomes a path this application will use.

The approved opinions archive stores every member as ``Opinions\<name>.pdf``.
That is a Windows separator in a field the ZIP specification says holds forward
slashes, and ``zipfile`` papers over it: while parsing a name it rewrites the
host's ``os.sep`` to ``/``. The identical container therefore reads as
``Opinions/x.pdf`` on Windows and keeps the backslash on Linux — same bytes,
same SHA-256, two different paths. The reader refused the Linux reading, so an
archive that planned cleanly on a laptop could not be planned in production.

Two things follow, and both are tested here rather than described.

The canonical path has to be the POSIX one on every host, because
``archive_relative_path`` is part of an occurrence's identity: the catalogue
writes it and materialisation later asks `ArchiveReader` for that exact string.

And translating the separator must not buy a traversal its way in. The
translation happens before validation, never after — ``Opinions\..\x.pdf`` has
to become ``Opinions/../x.pdf`` in time for the guard to see what it is.

Every archive here is written by `synthetic_opinions.write_raw_archive`, which
stores a member name exactly as given, and read under `posix_zip_names`, which
makes a Windows host parse those names the way the acceptance environment does.
Nothing in this module touches a database, and no real filename appears.
"""

from __future__ import annotations

import datetime
import zipfile
from pathlib import Path

import pytest

from app.legacy_import.opinion_sources import (
    ArchiveReader,
    OpinionSourceError,
    read_opinion_archive,
)
from tests import synthetic_opinions as syn

# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------


def test_the_stored_name_really_does_keep_its_backslash(tmp_path, posix_zip_names):
    r"""Asserted, not assumed. Everything below rests on this being reproducible.

    ``ZipInfo.__init__`` rewrites the separator on the way in as well as on the
    way out, so a fixture that built its entries the ordinary way would quietly
    be testing nothing.
    """
    path = syn.write_raw_archive(tmp_path / "a.zip", [("Opinions\\x.pdf", syn.pdf_bytes("x"))])

    assert b"Opinions\\x.pdf" in path.read_bytes()
    with zipfile.ZipFile(path) as archive:
        assert archive.infolist()[0].filename == "Opinions\\x.pdf"


def test_a_windows_separator_in_a_member_name_reads_as_one_posix_path(tmp_path, posix_zip_names):
    """The production blocker, as an archive rather than as a description of one."""
    data = syn.pdf_bytes("backslash")
    path = syn.write_raw_archive(
        tmp_path / "a.zip",
        [("Opinions\\2026-01-01 - Näidisamet - Näidispealkiri.pdf", data)],
    )

    _, occurrences = read_opinion_archive(path)

    assert len(occurrences) == 1
    occurrence = occurrences[0]
    assert occurrence.relative_path == "Opinions/2026-01-01 - Näidisamet - Näidispealkiri.pdf"
    assert occurrence.original_filename == "2026-01-01 - Näidisamet - Näidispealkiri.pdf"
    assert occurrence.sha256 == syn.sha256(data)
    assert occurrence.filename_date == datetime.date(2026, 1, 1)
    assert occurrence.filename_recipient == "Näidisamet"


def test_the_bytes_open_by_the_path_the_inventory_recorded(tmp_path, posix_zip_names):
    """The half of the fix a plan cannot prove.

    Cataloguing stores `relative_path` as ``archive_relative_path``;
    materialisation later hands that exact string back to `ArchiveReader`. Two
    normalisations would report every catalogued file as missing from the
    archive it was catalogued out of.
    """
    data = syn.pdf_bytes("reader")
    path = syn.write_raw_archive(
        tmp_path / "a.zip", [("Opinions\\2026-01-01 - Näidisamet - Teine.pdf", data)]
    )

    _, occurrences = read_opinion_archive(path)

    assert ArchiveReader(path).read(occurrences[0].relative_path) == data


def test_a_specification_compliant_member_name_is_left_alone(tmp_path, posix_zip_names):
    """The ordinary case has to survive the compatibility rule unchanged."""
    data = syn.pdf_bytes("fs")
    path = syn.write_raw_archive(
        tmp_path / "a.zip", [("Opinions/2026-01-01 - Amet - Kolmas.pdf", data)]
    )

    _, occurrences = read_opinion_archive(path)

    assert occurrences[0].relative_path == "Opinions/2026-01-01 - Amet - Kolmas.pdf"
    assert ArchiveReader(path).read("Opinions/2026-01-01 - Amet - Kolmas.pdf") == data


def test_the_name_is_decoded_before_the_separator_is_translated(tmp_path, posix_zip_names):
    """Order: recover the name, canonicalise the separator, then read it.

    91 of the real entries carry UTF-8 bytes without the UTF-8 flag and are
    recovered from ``zipfile``'s cp437 reading. Doing anything to a name before
    that recovery risks operating on half a character, so the recovery is
    asserted here on an entry that also carries the backslash.
    """
    data = syn.pdf_bytes("unflagged")
    path = syn.write_raw_archive(
        tmp_path / "a.zip",
        [("Opinions\\2026-02-02 - Näidisamet - Tõõväline mõõdik.pdf", data)],
    )
    _clear_the_only_utf8_flag(path)

    _, occurrences = read_opinion_archive(path)

    assert occurrences[0].filename_encoding == "cp437-bytes-were-utf8"
    assert occurrences[0].original_filename == "2026-02-02 - Näidisamet - Tõõväline mõõdik.pdf"
    assert occurrences[0].relative_path == "Opinions/" + occurrences[0].original_filename


def _clear_the_only_utf8_flag(path: Path) -> None:
    """Reproduce the 91 entries whose UTF-8 names carry no UTF-8 flag.

    `synthetic_opinions._clear_utf8_flag` finds its entries by name, which
    cannot address a member whose stored name ``zipfile`` declines to hand back
    unchanged on every host. This archive holds exactly one entry, so both flag
    words are found from the header offsets instead.
    """
    with zipfile.ZipFile(path) as archive:
        local_offset = archive.infolist()[0].header_offset
        directory_start = archive.start_dir
    raw = bytearray(path.read_bytes())
    raw[local_offset + 7] &= ~0x08
    raw[directory_start + 9] &= ~0x08
    path.write_bytes(bytes(raw))


# ---------------------------------------------------------------------------
# What must stay refused
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("case", "name"),
    [
        ("backslash traversal", "Opinions\\..\\evil.pdf"),
        ("posix traversal", "Opinions/../evil.pdf"),
        ("leading backslash", "\\evil.pdf"),
        ("posix absolute", "/evil.pdf"),
        ("empty segment, backslash", "Opinions\\\\evil.pdf"),
        ("empty segment, posix", "Opinions//evil.pdf"),
        ("drive, backslash", "C:\\evil.pdf"),
        ("drive, posix", "C:/evil.pdf"),
        ("drive relative", "C:evil.pdf"),
        ("dot segment", "Opinions/./evil.pdf"),
        ("dot segment, backslash", "Opinions\\.\\evil.pdf"),
    ],
)
def test_a_member_name_that_is_not_a_relative_path_is_refused(
    tmp_path, posix_zip_names, case, name
):
    r"""The translation must not weaken the guard it now runs before.

    The Windows-shaped names are the whole reason the order matters: validated
    first and translated afterwards, ``Opinions\..\evil.pdf`` would have been
    accepted and only then turned into a traversal.

    The drive cases are refused on every host, including Linux where they are
    technically relative names. An archive read on one machine and unpacked on
    another should not depend on which of them had a C: drive. A "." segment is
    refused rather than collapsed, so two member names cannot quietly become
    one identity.
    """
    path = syn.write_raw_archive(tmp_path / "evil.zip", [(name, syn.pdf_bytes("x"))])

    with pytest.raises(OpinionSourceError):
        read_opinion_archive(path)


def test_a_directory_record_is_skipped_whichever_separator_ended_it(tmp_path, posix_zip_names):
    r"""A trailing separator means a directory on both hosts, or neither.

    `zipfile.ZipInfo.is_dir` decides this with ``os.path.altsep``, so
    ``Opinions\`` is a directory record on Windows and a file with an empty
    last segment on Linux — one of them skipped and the other refused, from one
    container. The decision is made here instead, after the translation, so the
    archive holds the same set of files on either machine.
    """
    data = syn.pdf_bytes("kept")
    path = syn.write_raw_archive(
        tmp_path / "a.zip",
        [
            ("Opinions\\", b""),
            ("Opinions/", b""),
            ("Opinions\\2026-06-06 - Amet - Kuues.pdf", data),
        ],
    )

    _, occurrences = read_opinion_archive(path)

    assert [o.relative_path for o in occurrences] == ["Opinions/2026-06-06 - Amet - Kuues.pdf"]


def test_two_member_names_that_mean_one_path_are_refused(tmp_path, posix_zip_names):
    """Neither answer is acceptable, so the archive gets no answer.

    Counting them as two occurrences duplicates the identity the catalogue's
    uniqueness constraint rests on; keeping one silently drops a byte sequence
    that is in the archive. The archive is malformed, and the operator is the
    one who has to know.
    """
    path = syn.write_raw_archive(
        tmp_path / "collide.zip",
        [
            ("Opinions/2026-03-03 - Amet - Üks.pdf", syn.pdf_bytes("first")),
            ("Opinions\\2026-03-03 - Amet - Üks.pdf", syn.pdf_bytes("second")),
        ],
    )

    with pytest.raises(OpinionSourceError, match="one path"):
        read_opinion_archive(path)
    with pytest.raises(OpinionSourceError, match="one path"):
        ArchiveReader(path)


def test_two_genuinely_different_paths_are_still_two_occurrences(tmp_path, posix_zip_names):
    """Refusing a collision must not refuse an archive that merely repeats bytes."""
    data = syn.pdf_bytes("same")
    path = syn.write_raw_archive(
        tmp_path / "dupe.zip",
        [
            ("Opinions\\2026-04-04 - Amet - Neljas.pdf", data),
            ("Opinions\\koopia\\2026-04-04 - Amet - Neljas.pdf", data),
        ],
    )

    _, occurrences = read_opinion_archive(path)

    assert {o.relative_path for o in occurrences} == {
        "Opinions/2026-04-04 - Amet - Neljas.pdf",
        "Opinions/koopia/2026-04-04 - Amet - Neljas.pdf",
    }
    assert len({o.sha256 for o in occurrences}) == 1
    reader = ArchiveReader(path)
    assert all(reader.read(o.relative_path) == data for o in occurrences)


# ---------------------------------------------------------------------------
# A directory is not a ZIP
# ---------------------------------------------------------------------------


def test_a_directory_source_does_not_gain_the_separator_rule(tmp_path):
    """The compatibility is a ZIP-entry rule and stays one.

    A backslash is a legal character in a POSIX filename, so translating one
    for a file on disk would rename somebody's file. A directory source keeps
    giving the answer this reader has always given.
    """
    root = tmp_path / "arhiiv"
    (root / "koopia").mkdir(parents=True)
    (root / "2026-05-05 - Amet - Viies.pdf").write_bytes(syn.pdf_bytes("dir"))
    (root / "koopia" / "2026-05-05 - Amet - Viies.pdf").write_bytes(syn.pdf_bytes("dir"))

    _, occurrences = read_opinion_archive(root)

    assert {o.relative_path for o in occurrences} == {
        "2026-05-05 - Amet - Viies.pdf",
        "koopia/2026-05-05 - Amet - Viies.pdf",
    }
