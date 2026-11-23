"""`tools.release_fingerprint`: a release kit's expected fingerprint lines (r89 finding).

No database. The helper may only ever produce the two migration-leaf lines and «None -> 0»
for models the migration creates; it never changes how a kit compares them (the kit's
verdict stays an exact match). A migration it cannot vouch for is refused when the kit is
generated, and with ``--ref`` it runs only as the reviewed commit's own copy, reading the
migration from that same commit.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tools import release_fingerprint
from tools.release_fingerprint import (
    SUPPORTED_OPERATIONS,
    check_lines,
    check_supported,
    created_models,
    expected_lines,
    leaf_lines,
    main,
    new_model_lines,
    pinned_migration,
)

ROOT = Path(__file__).resolve().parent.parent
#: A real reviewed migration that creates one model (r89, `legacy_import 0033`).
R89_FILE = ROOT / "app/legacy_import/migrations/0033_lovable_owner_alias.py"
HELPER = Path(release_fingerprint.__file__)


def _migration(operations: str) -> str:
    """A migration file around ``operations``, shaped as `makemigrations` writes them."""
    return (
        "from django.db import migrations, models\n\n\n"
        "class Migration(migrations.Migration):\n"
        "    dependencies = [('x', '0001_initial')]\n\n"
        f"    operations = [\n{operations}\n    ]\n"
    )


#: r84's shape (register_watch 0005): two CreateModel and one AddField.
R84 = _migration(
    """
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
        ),"""
)
ADD_FIELD_ONLY = _migration(
    """
        migrations.AddField(
            model_name="watchfinding",
            name="files",
            field=models.JSONField(null=True),
        ),"""
)


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


# ---------------------------------------------------------------------------------------
# Unsupported migrations fail when the kit is generated, before deployment
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "operations",
    [
        "        migrations.RunPython(forwards, migrations.RunPython.noop),",
        "        migrations.RunSQL('UPDATE matters_matter SET title = title'),",
        "        RunPython(forwards),",  # imported bare: still refused
        "        migrations.DeleteModel(name='Old'),",
        "        migrations.RenameModel(old_name='Old', new_name='New'),",
        "        migrations.AlterModelTable(name='a', table='b'),",
        "        migrations.RemoveField(model_name='a', name='b'),",
        "        migrations.RenameField(model_name='a', old_name='b', new_name='c'),",
        "        migrations.SeparateDatabaseAndState(state_operations=[]),",
        "        migrations.SomethingNew(),",
    ],
    ids=[
        "run-python",
        "run-sql",
        "bare-run-python",
        "delete-model",
        "rename-model",
        "alter-model-table",
        "remove-field",
        "rename-field",
        "separate-state",
        "unknown",
    ],
)
def test_an_unsupported_operation_is_refused_before_deployment(operations):
    text = _migration("        migrations.CreateModel(name='A', fields=[]),\n" + operations)
    with pytest.raises(ValueError):
        expected_lines(app="x", migration="0002_y", previous_leaf="0001_initial", text=text)


def test_text_that_is_not_a_migration_is_refused():
    with pytest.raises(ValueError):
        check_supported("migrations.CreateModel(name='A', fields=[])")


@pytest.mark.parametrize(
    "operations",
    [
        "        migrations.CreateModel(\n"
        "            fields=[],\n"
        "            name=NAME,\n"
        "        ),",
        "        migrations.CreateModel(name='A', fields=[]),\n"
        "        migrations.CreateModel(name='A', fields=[]),",
    ],
    ids=["unreadable-name", "created-twice"],
)
def test_what_it_cannot_read_is_refused_not_skipped(operations):
    with pytest.raises(ValueError):
        created_models(_migration(operations))


def test_every_supported_operation_the_repository_uses_is_schema_only():
    """Each real migration either yields lines or is refused; none crashes. The supported
    ones never yield a line for an operation that is not a CreateModel."""
    accepted = refused = 0
    for path in sorted(ROOT.glob("app/*/migrations/0*.py")):
        text = path.read_text(encoding="utf-8")
        try:
            operations = check_supported(text)
        except ValueError:
            refused += 1
            continue
        accepted += 1
        assert set(operations) <= SUPPORTED_OPERATIONS
        assert len(new_model_lines(path.parent.parent.name, text)) == operations.count(
            "CreateModel"
        )
    assert accepted and refused  # both kinds exist in the history (data migrations refused)


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
        expected_lines(app=app, migration=migration, previous_leaf=previous, text=ADD_FIELD_ONLY)


# ---------------------------------------------------------------------------------------
# The helper and the migration from one pinned commit
# ---------------------------------------------------------------------------------------


@pytest.fixture
def release_repo(tmp_path):
    """A throwaway git repository whose one commit holds this helper and r89's migration."""
    git_exe = shutil.which("git")
    if git_exe is None:
        pytest.skip("git is not available")
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "app/legacy_import/migrations").mkdir(parents=True)
    shutil.copyfile(HELPER, repo / "tools/release_fingerprint.py")
    shutil.copyfile(R89_FILE, repo / "app/legacy_import/migrations/0033_lovable_owner_alias.py")

    def git(*a: str) -> str:
        return subprocess.run(  # noqa: S603 - git, a temporary repository, fixed arguments
            [git_exe, "-C", str(repo), "-c", "core.autocrlf=false", *a],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "r")
    return repo, git("rev-parse", "HEAD"), git


def test_the_pinned_mode_reads_the_migration_from_the_reviewed_commit(release_repo, capsys):
    repo, sha, _git = release_repo
    text = pinned_migration(
        repo=repo, ref=sha, app="legacy_import", migration="0033_lovable_owner_alias", helper=HELPER
    )
    assert "LovableOwnerAlias" in text
    argv = ["--repo", str(repo), "--ref", sha, "--app", "legacy_import"]
    argv += [
        "--migration",
        "0033_lovable_owner_alias",
        "--previous-leaf",
        "0032_postipoiss_letters",
    ]
    assert main(argv) == 0
    assert capsys.readouterr().out.splitlines()[0] == (
        "canonical_counts.legacy_import.LovableOwnerAlias: None -> 0"
    )


def test_a_helper_that_is_not_the_commits_copy_is_refused(release_repo, tmp_path):
    repo, sha, _git = release_repo
    other = tmp_path / "release_fingerprint.py"
    other.write_text(HELPER.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"is not tools/release_fingerprint\.py at"):
        pinned_migration(
            repo=repo,
            ref=sha,
            app="legacy_import",
            migration="0033_lovable_owner_alias",
            helper=other,
        )


def test_a_migration_not_in_the_commit_or_a_short_ref_is_refused(release_repo, capsys):
    repo, sha, _git = release_repo
    with pytest.raises(ValueError, match="is not in commit"):
        pinned_migration(
            repo=repo, ref=sha, app="legacy_import", migration="0034_other", helper=HELPER
        )
    with pytest.raises(ValueError, match="full 40-character"):
        pinned_migration(
            repo=repo, ref=sha[:12], app="legacy_import", migration="0033_x", helper=HELPER
        )
    argv = ["--repo", str(repo), "--ref", sha, "--app", "legacy_import"]
    argv += ["--migration", "0034_other", "--previous-leaf", "0033_lovable_owner_alias"]
    assert main(argv) == 2
    assert "REFUSED" in capsys.readouterr().err


def test_the_local_file_mode_checks_the_file_name(capsys):
    arguments = ["--app", "legacy_import", "--previous-leaf", "0032_postipoiss_letters"]
    file = ["--migration-file", str(R89_FILE)]
    assert main([*arguments, "--migration", "0033_lovable_owner_alias", *file]) == 0
    assert capsys.readouterr().out.startswith("canonical_counts.legacy_import.LovableOwnerAlias")
    assert main([*arguments, "--migration", "0034_other", *file]) == 2
