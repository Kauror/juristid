"""The two register columns, and the sentence that moves work elsewhere.

Pure rules, no database. Every assertion here is one the final cutover rests on,
and most of them are refusals: the failures this module exists to prevent are
all cases of reading one column as if it were the other.
"""

from __future__ import annotations

import pytest

from app.legacy_import.register_semantics import (
    TERMINAL_STATUS_LABELS,
    ContinuationVerdict,
    detect_continuation,
    has_send_date,
    is_real_row,
    is_terminal_status,
)

# =========================================================================
# HETKESEIS — whether the proceeding is still running
# =========================================================================


@pytest.mark.parametrize("label", ["jõustunud", "rohkem pole tegevusi plaanis"])
def test_the_two_terminal_labels_end_current_work(label: str) -> None:
    assert is_terminal_status(label)


@pytest.mark.parametrize(
    "label",
    [
        "idee",
        "kooskõlastusringil",
        "valitsuses",
        "Riigikogus",
        "ootan jõustumist",
        "Eesti seisukoht",
        "ELi menetluses",
        "ootan ELi õiguse ülevõtmist",
        "muu",
    ],
)
def test_every_other_controlled_status_stays_current(label: str) -> None:
    """The whole live vocabulary, named one by one.

    Parametrised over the real list rather than spot-checked, because the way
    this rule breaks is somebody adding a label to the terminal set that merely
    sounds final.
    """
    assert not is_terminal_status(label)


def test_muu_is_a_status_and_not_a_gap() -> None:
    """Called out separately because it reads like an absence and is not.

    Five current Matters in the approved snapshot carry it. Treating it as
    "no status recorded, therefore finished" would retire live work.
    """
    assert not is_terminal_status("muu")


def test_ootan_joustumist_is_not_joustunud() -> None:
    """Waiting for an act to commence is live work; the act commencing is not.

    The two labels differ by an inflection, and confusing them retires the
    Matters that are specifically still being watched.
    """
    assert not is_terminal_status("ootan jõustumist")
    assert is_terminal_status("jõustunud")


@pytest.mark.parametrize("label", ["", "   ", None])
def test_a_missing_status_leaves_the_matter_current(label: str | None) -> None:
    assert not is_terminal_status(label)


def test_an_unknown_label_leaves_the_matter_current() -> None:
    """Failing open, deliberately, and the opposite of what authorization does.

    Here the harm is dropping live work off somebody's list; an extra row on a
    dashboard is not a harm. Authorization whitelists because its harm runs the
    other way.
    """
    assert not is_terminal_status("mingi uus seis mida keegi lisas")


def test_the_comparison_survives_casing_and_padding() -> None:
    assert is_terminal_status("  Jõustunud  ")
    assert is_terminal_status("ROHKEM POLE TEGEVUSI PLAANIS")


def test_the_terminal_set_is_exactly_two_labels() -> None:
    """A guard on the vocabulary itself, not on any one call.

    Widening this set retires work in bulk and silently, so the size is asserted
    where a reviewer of the diff will see it.
    """
    assert TERMINAL_STATUS_LABELS == {"jõustunud", "rohkem pole tegevusi plaanis"}


# =========================================================================
# VÄLJA — whether the opinion has been sent
# =========================================================================


@pytest.mark.parametrize("raw", ["14.05.2026", "2026-05-14", "45789"])
def test_any_recorded_send_value_counts_as_sent(raw: str) -> None:
    """Presence, not parseability.

    An unreadable date still says the department recorded a send. Treating it
    as blank would put a finished opinion back into the drafting queue on the
    strength of a formatting problem.
    """
    assert has_send_date(raw)


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_a_blank_send_column_means_still_drafting(raw: str | None) -> None:
    assert not has_send_date(raw)


# =========================================================================
# A reference is not a row
# =========================================================================


def test_a_reference_with_no_title_is_not_a_row() -> None:
    """The current sheet is pre-numbered past the work that exists.

    105 rows in the approved snapshot carry a number and nothing else.
    """
    assert not is_real_row("2026_240", "")
    assert not is_real_row("2026_240", "   ")


def test_a_title_makes_it_a_row() -> None:
    assert is_real_row("2026_1", "Sünteetiline eelnõu")


def test_a_row_can_be_real_without_a_reference() -> None:
    """The title decides, not the number. An unreferenced row is a data-quality
    finding for the importer, not a reason for this rule to look away."""
    assert is_real_row("", "Sünteetiline eelnõu")


# =========================================================================
# Continuation — both halves, or neither
# =========================================================================


def test_wording_with_one_reference_supersedes() -> None:
    verdict = detect_continuation("Jätkub teema 2026_55 all.")
    assert verdict.verdict == ContinuationVerdict.SUPERSEDED
    assert verdict.reference == "2026_55"


@pytest.mark.parametrize(
    "text",
    [
        "Jätkub 2026_79.",
        "jätkub teema 2026_79 all",
        "Töö jätkub teema 2026_79 all, vastutaja sama.",
    ],
)
def test_the_wording_is_matched_through_its_inflections(text: str) -> None:
    assert detect_continuation(text).reference == "2026_79"


def test_a_reference_without_continuation_wording_is_only_a_cross_reference() -> None:
    """The register cross-references related files constantly.

    Reading one as "this Matter has moved" would retire live work on the
    strength of a footnote — and in the approved snapshot every one of the 24
    genuine continuations carries the wording, so nothing is lost by requiring
    it.
    """
    assert detect_continuation("Seotud teemaga 2026_55, ootan tagasisidet.").verdict == (
        ContinuationVerdict.NONE
    )
    assert detect_continuation("Vt ka 2025_10 ja 2026_55.").verdict == ContinuationVerdict.NONE


def test_continuation_wording_without_a_reference_is_ambiguous() -> None:
    """It says the work continues and not where, which is not an answer."""
    verdict = detect_continuation("Jätkub uue teema all.")
    assert verdict.verdict == ContinuationVerdict.AMBIGUOUS
    assert verdict.reference == ""


def test_continuation_wording_with_several_references_is_ambiguous() -> None:
    """Picking the first would file the work under whichever was typed first."""
    verdict = detect_continuation("Jätkub kas teema 2026_55 või 2026_79 all.")
    assert verdict.verdict == ContinuationVerdict.AMBIGUOUS
    assert verdict.needs_review


def test_the_same_reference_twice_is_still_one_answer() -> None:
    """Repetition is not ambiguity.

    "Jätkub 2026_55 all (vt 2026_55)" names one Matter, and sending it to
    review would make a reviewer settle a question the sentence answers.
    """
    verdict = detect_continuation("Jätkub teema 2026_55 all, vt 2026_55.")
    assert verdict.verdict == ContinuationVerdict.SUPERSEDED
    assert verdict.reference == "2026_55"


@pytest.mark.parametrize("text", ["", "   ", None, "Ootan ministeeriumi vastust."])
def test_an_ordinary_instruction_supersedes_nothing(text: str | None) -> None:
    assert detect_continuation(text).verdict == ContinuationVerdict.NONE


def test_a_date_is_not_read_as_a_reference() -> None:
    """``2026_55`` is anchored on word boundaries so a serial or a date cannot
    masquerade as one."""
    assert detect_continuation("Jätkub pärast 12.03.2026 toimuvat istungit.").verdict == (
        ContinuationVerdict.AMBIGUOUS
    )


def test_a_reference_shaped_like_a_year_range_is_not_matched() -> None:
    assert detect_continuation("Jätkub perioodil 2025-2026.").verdict == (
        ContinuationVerdict.AMBIGUOUS
    )
