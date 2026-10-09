"""Three `Hetkeseis` explanations corrected by `workflow/0016` (F2 and F3, 9 October 2026).

What is pinned, and why each one:

* **«Muu» says when «Muu» is the answer**, and «ELi menetluses» keeps the
  department's own text — the duplication `workflow/0006` shipped is resolved in
  the one row that was wrong.
* **The EU wording matches the approved matrix.** The guidance matrix dims
  «ELi õiguse ülevõtmise ootel» for an EU regulation because a regulation is
  directly applicable (docs/adr/0130 §5). The explanations used to say the
  opposite for every EU act; they now draw the four distinctions the brief named
  — entry into force, direct applicability, a directive's transposition, and a
  regulation's national implementing measures — without moving the matrix.
* **The migration fails closed** and **reverses exactly**: a text somebody has
  since edited is nobody's to overwrite, in either direction.
"""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps

from app.workflow.models import StageVocabulary
from app.workflow.reference_stages import REWORDED_STAGE_LABELS_V2
from app.workflow.stage_guidance import TYPICAL_STAGES_BY_INSTRUMENT

CORRECTION = importlib.import_module("app.workflow.migrations.0016_stage_help_corrections")
DEPARTMENT_TEXTS = importlib.import_module(
    "app.workflow.migrations.0006_stage_help_from_the_department"
)

pytestmark = pytest.mark.django_db


def _help(key: str) -> str:
    return StageVocabulary.objects.get(key=key).help_text


def test_the_guard_compares_against_what_workflow_0006_actually_wrote():
    """A frozen copy nobody checks is a copy that silently stops matching —
    and here a mismatch would make the correction a silent no-op everywhere."""
    for key, previous in CORRECTION.PREVIOUS.items():
        assert previous == DEPARTMENT_TEXTS.SUPPLIED[key], key


def test_muu_is_corrected_and_eli_menetluses_is_left_as_the_department_wrote_it():
    assert _help("other") == (
        "Vali „Muu“, kui ükski loetletud hetkeseis ei kirjelda teema tegelikku olukorda."
    )
    assert _help("eu_procedure") == DEPARTMENT_TEXTS.SUPPLIED["eu_procedure"]


def test_no_other_explanation_moved():
    untouched = set(DEPARTMENT_TEXTS.SUPPLIED) - set(CORRECTION.CORRECTED)
    assert untouched == {
        "idea",
        "consultation",
        "government",
        "parliament",
        "awaiting_entry",
        "estonian_eu_position",
        "eu_procedure",
    }
    for key in untouched:
        assert _help(key) == DEPARTMENT_TEXTS.SUPPLIED[key], key


def test_jõustunud_keeps_the_transposition_rule_for_a_directive_only():
    text = _help("in_force")

    # A directive: not «jõustunud» while Estonia still has to transpose it, and
    # the explanation names the stage it is in meanwhile, by its current label.
    assert "Kui jõustub ELi direktiiv" in text
    assert f"„{REWORDED_STAGE_LABELS_V2['awaiting_transposition']}“" in text

    # A regulation: directly applicable, not transposed, in force from entry
    # into force — including when it applies later and when Estonia has to
    # supplement its own law to implement it.
    assert "ELi määrus kohaldub vahetult ja seda üle ei võeta" in text
    assert "hakatakse kohaldama hiljem" in text
    assert "rakendamiseks" in text

    # The sentence that sent a regulation towards transposition is gone.
    assert "kui jõustub ELi akt" not in text


def test_ülevõtmise_ootel_is_about_a_directive_and_says_a_regulation_is_not_transposed():
    text = _help("awaiting_transposition")
    assert text.startswith("ELi direktiivi (või muu ülevõtmist vajava ELi akti) jõustumisest")
    assert "ELi määrust üle ei võeta" in text
    assert not text.startswith("ELi õigusakti jõustumisest")


def test_the_wording_and_the_approved_matrix_now_say_the_same_thing():
    """F3 is resolved by the words, not by the matrix: the matrix is unchanged."""
    assert "awaiting_transposition" not in TYPICAL_STAGES_BY_INSTRUMENT["el-maarus"]
    assert "in_force" in TYPICAL_STAGES_BY_INSTRUMENT["el-maarus"]
    assert "awaiting_transposition" in TYPICAL_STAGES_BY_INSTRUMENT["direktiiv"]
    assert "awaiting_transposition" in TYPICAL_STAGES_BY_INSTRUMENT["muu-eli-dokument"]


def test_an_edited_explanation_is_left_alone_in_both_directions():
    StageVocabulary.objects.filter(key="other").update(help_text="Osakonna enda uus tekst.")
    StageVocabulary.objects.filter(key="in_force").update(
        help_text=CORRECTION.PREVIOUS["in_force"] + " Lisatud lause."
    )

    CORRECTION.restore(apps, None)
    CORRECTION.correct(apps, None)

    assert _help("other") == "Osakonna enda uus tekst."
    assert _help("in_force") == CORRECTION.PREVIOUS["in_force"] + " Lisatud lause."
    # The untouched third row still round-trips.
    assert _help("awaiting_transposition") == CORRECTION.CORRECTED["awaiting_transposition"]


def test_the_reverse_restores_exactly_what_was_there_and_the_forward_is_idempotent():
    CORRECTION.restore(apps, None)
    for key, previous in CORRECTION.PREVIOUS.items():
        assert _help(key) == previous, key

    CORRECTION.correct(apps, None)
    CORRECTION.correct(apps, None)
    for key, corrected in CORRECTION.CORRECTED.items():
        assert _help(key) == corrected, key


def test_the_migration_touches_workflow_and_nothing_else():
    assert CORRECTION.Migration.dependencies == [
        ("workflow", "0015_current_register_status_labels")
    ]
