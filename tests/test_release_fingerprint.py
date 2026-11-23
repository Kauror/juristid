"""`tools.release_fingerprint`: a release kit's expected fingerprint lines (r89 finding).

No database. The helper may only ever produce the two migration-leaf lines and «None -> 0»
for models the migration creates; it never changes how a kit compares them (the kit's
verdict stays an exact match).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.release_fingerprint import (
    check_lines,
    created_models,
    expected_lines,
    leaf_lines,
    main,
    new_model_lines,
)

ROOT = Path(__file__).resolve().parent.parent
#: A real reviewed migration that creates one model (r89, `legacy_import 0033`).
R89_FILE = ROOT / "app/legacy_import/migrations/0033_lovable_owner_alias.py"

#: r84's shape (register_watch 0005): two CreateModel and one AddField.
R84 = """
        migrations.CreateModel(
            name="GovernmentAgenda",
            fields=[],
        ),
        migrations.AddField(
            model_name="candidatenotice",
            name="agendas",
            field=models.IntegerField(db_default=0),
        ),
        migrations.CreateModel(
            name="AgendaItem",
            fields=[],
        ),
"""
ADD_FIELD_ONLY = """
        migrations.AddField(
            model_name="watchfinding",
            name="files",
            field=models.JSONField(null=True),
        ),
"""


def test_r89_derives_the_line_its_kit_was_missing():
    lines = expected_lines(
        app="legacy_import",
        migration="0033_lovable_owner_alias",
        previous_leaf="0032_postipoiss_letters",
        text=R89_FILE.read_text(encoding="utf-8"),
    )
    assert lines == [
        "canonical_counts.legacy_import.LovableOwnerAlias: None -> 0",
        "migration_leaves: newly present: legacy_import.0033_lovable_owner_alias",
        "migration_leaves: no longer present: legacy_import.0032_postipoiss_letters",
    ]


def test_r84_gives_exactly_its_two_new_models_and_nothing_for_the_added_field():
    assert new_model_lines("register_watch", R84) == [
        "canonical_counts.register_watch.GovernmentAgenda: None -> 0",
        "canonical_counts.register_watch.AgendaItem: None -> 0",
    ]


def test_a_migration_without_a_new_model_gives_only_the_leaves():
    lines = expected_lines(
        app="register_watch",
        migration="0007_finding_files",
        previous_leaf="0006_agenda_item_decision",
        text=ADD_FIELD_ONLY,
    )
    assert lines == leaf_lines("register_watch", "0007_finding_files", "0006_agenda_item_decision")


@pytest.mark.parametrize(
    "wider",
    [
        "canonical_counts.legacy_import.LovableOwnerAlias: None -> 1",
        "canonical_counts.matters.Matter: 2900 -> 2899",
        "canonical_counts.legacy_import.LovableOwnerAlias: 0 -> 0",
        "migration_leaves: newly present: legacy_import.anything",
        "accounts.User: None -> 0",
        "",
    ],
)
def test_no_line_wider_than_the_two_exact_shapes_is_accepted(wider):
    """The helper cannot widen what a kit accepts: never a count other than None -> 0, never
    a moved count of an existing model, never a leaf that is not a migration name."""
    with pytest.raises(ValueError):
        check_lines([wider])


def test_every_derived_line_has_one_of_the_two_shapes_and_none_repeats():
    lines = expected_lines(
        app="register_watch",
        migration="0005_government_agenda",
        previous_leaf="0004_topic_candidates",
        text=R84,
    )
    assert check_lines(lines) == lines
    duplicate = ["migration_leaves: newly present: a.0001_x"] * 2
    with pytest.raises(ValueError):
        check_lines(duplicate)


@pytest.mark.parametrize(
    "text",
    [
        "migrations.CreateModel(\n    fields=[],\n    name=NAME,\n)",
        "migrations.CreateModel(name='A', fields=[])\nmigrations.CreateModel(name='A')",
    ],
    ids=["unreadable-name", "created-twice"],
)
def test_what_it_cannot_read_is_refused_not_skipped(text):
    with pytest.raises(ValueError):
        created_models(text)


@pytest.mark.parametrize(
    ("app", "migration", "previous"),
    [
        ("legacy-import", "0033_x", "0032_y"),
        ("legacy_import", "33_x", "0032_y"),
        ("legacy_import", "0033_x", "0033_x"),
    ],
    ids=["bad-app", "bad-migration", "own-leaf"],
)
def test_malformed_labels_are_refused(app, migration, previous):
    with pytest.raises(ValueError):
        expected_lines(app=app, migration=migration, previous_leaf=previous, text="")


def test_the_command_prints_the_lines_and_checks_the_file_name(capsys):
    arguments = ["--app", "legacy_import", "--previous-leaf", "0032_postipoiss_letters"]
    file = ["--migration-file", str(R89_FILE)]
    assert main([*arguments, "--migration", "0033_lovable_owner_alias", *file]) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed[0] == "canonical_counts.legacy_import.LovableOwnerAlias: None -> 0"
    with pytest.raises(SystemExit):
        main([*arguments, "--migration", "0034_other", *file])
