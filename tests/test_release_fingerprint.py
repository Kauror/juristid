"""`tools.release_fingerprint`: a release kit's expected fingerprint lines (r89 finding).

No database. The helper may only produce the two migration-leaf lines and «None -> 0» for
models a release's single migration creates; it never changes how a kit compares them (the
kit's verdict stays an exact match). Anything it cannot vouch for is refused when the kit is
generated, before deployment — including every bypass the review of #680 tried.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tools import release_fingerprint
from tools.release_fingerprint import (
    EXIT_REFUSED,
    Refused,
    analyse_migration,
    check_lines,
    expected_lines,
    leaf_lines,
    main,
    release_lines,
)

ROOT = Path(__file__).resolve().parent.parent
#: A real reviewed migration that creates one model (r89, `legacy_import 0033`).
R89_FILE = ROOT / "app/legacy_import/migrations/0033_lovable_owner_alias.py"
HELPER = Path(release_fingerprint.__file__)
HEAD = "from django.db import migrations, models\n\n\n"


def _migration(
    operations: str, *, extra: str = "", dependency: str = "('x', '0001_initial')"
) -> str:
    """A migration file shaped as `makemigrations` writes it."""
    return (
        f"{HEAD}class Migration(migrations.Migration):\n{extra}"
        f"    dependencies = [{dependency}]\n\n"
        f"    operations = [\n{operations}\n    ]\n"
    )


CREATE_A = "        migrations.CreateModel(name='A', fields=[]),"
#: r84's shape (register_watch 0005): two CreateModel and one AddField.
R84 = _migration(
    """
        migrations.CreateModel(name="GovernmentAgenda", fields=[]),
        migrations.AddField(
            model_name="candidatenotice", name="agendas", field=models.IntegerField(db_default=0)
        ),
        migrations.CreateModel(name="AgendaItem", fields=[]),""",
    dependency="('register_watch', '0004_topic_candidates')",
)


def _lines(text: str, *, app: str = "x", previous: str = "0001_initial") -> list[str]:
    return expected_lines(app=app, migration="0002_y", previous_leaf=previous, text=text)


def test_r89_derives_the_line_its_kit_was_missing():
    assert expected_lines(
        app="legacy_import",
        migration="0033_lovable_owner_alias",
        previous_leaf="0032_postipoiss_letters",
        text=R89_FILE.read_text(encoding="utf-8"),
    ) == [
        "canonical_counts.legacy_import.LovableOwnerAlias: None -> 0",
        "migration_leaves: newly present: legacy_import.0033_lovable_owner_alias",
        "migration_leaves: no longer present: legacy_import.0032_postipoiss_letters",
    ]


def test_r84_gives_its_two_new_models_and_nothing_for_the_added_field():
    assert analyse_migration(R84, app="register_watch", previous_leaf="0004_topic_candidates") == [
        "GovernmentAgenda",
        "AgendaItem",
    ]


def test_a_migration_without_a_new_model_gives_only_the_leaves():
    text = _migration(
        "        migrations.AddField(model_name='a', name='b', field=models.JSONField(null=True)),"
    )
    assert _lines(text) == leaf_lines("x", "0002_y", "0001_initial")


def test_a_comment_naming_a_data_operation_is_not_code():
    assert _lines(_migration(CREATE_A + "  # not a RunPython")) == sorted(
        ["canonical_counts.x.A: None -> 0", *leaf_lines("x", "0002_y", "0001_initial")]
    )


@pytest.mark.parametrize(
    "wider",
    [
        "canonical_counts.legacy_import.LovableOwnerAlias: None -> 1",
        "canonical_counts.matters.Matter: 2900 -> 2899",
        "canonical_counts.legacy_import.LovableOwnerAlias: 0 -> 0",
        "migration_leaves: newly present: legacy_import.anything",
        "migration_leaves: newly present: a.0005_x, b.0003_y",
        "accounts.User: None -> 0",
        "",
    ],
)
def test_no_line_wider_than_the_two_exact_shapes_is_accepted(wider):
    with pytest.raises(Refused):
        check_lines([wider])


#: Every way the review of #680 got an unsupported migration past the first version.
BYPASSES = {
    "bare-delete-model": (
        "from django.db.migrations import DeleteModel\n" + HEAD,
        "        DeleteModel(name='Old'),",
        "",
    ),
    "aliased-operations": (
        "from django.db.migrations import operations as ops\n" + HEAD,
        "        ops.DeleteModel(name='Old'),",
        "",
    ),
    "migrations-operations": (HEAD, "        migrations.operations.RenameModel('Old', 'New'),", ""),
    "getattr": (HEAD, "        getattr(migrations, 'Run' + 'Python')(print),", ""),
    "starred-list": (HEAD + "EXTRA = []\n", "        *EXTRA,", ""),
    "replaces": (HEAD, CREATE_A, "    replaces = [('x', '0002_old')]\n"),
    "run-before": (HEAD, CREATE_A, "    run_before = [('y', '0001_initial')]\n"),
    "atomic": (HEAD, CREATE_A, "    atomic = False\n"),
    "initial": (HEAD, CREATE_A, "    initial = True\n"),
    "proxy": (
        HEAD,
        "        migrations.CreateModel(name='P', fields=[], options={'proxy': True}),",
        "",
    ),
    "unmanaged": (
        HEAD,
        "        migrations.CreateModel(name='U', fields=[], options={'managed': False}),",
        "",
    ),
    "run-python": (HEAD, "        migrations.RunPython(print),", ""),
    "run-sql": (HEAD, "        migrations.RunSQL('UPDATE matters_matter SET title = title'),", ""),
    "remove-field": (HEAD, "        migrations.RemoveField(model_name='a', name='b'),", ""),
    "rename-field": (HEAD, "        migrations.RenameField('a', 'b', 'c'),", ""),
    "delete-model": (HEAD, "        migrations.DeleteModel(name='Old'),", ""),
    "alter-table": (HEAD, "        migrations.AlterModelTable(name='a', table='b'),", ""),
    "separate-state": (
        HEAD,
        "        migrations.SeparateDatabaseAndState(state_operations=[]),",
        "",
    ),
    "unknown": (HEAD, "        migrations.SomethingNew(),", ""),
    "unnamed-create": (HEAD, "        migrations.CreateModel(fields=[], name=NAME),", ""),
    "created-twice": (HEAD, CREATE_A + "\n" + CREATE_A, ""),
    "module-code": (HEAD + "def forwards(apps, schema_editor):\n    pass\n\n\n", CREATE_A, ""),
    # The second review of #680: code carried by an otherwise plain file.
    "star-import": ("from app.evil import *\n" + HEAD, CREATE_A, ""),
    "other-import": ("import os\n" + HEAD, CREATE_A, ""),
    "aliased-models": ("from app.evil import thing as models\n" + HEAD, CREATE_A, ""),
    "relative-import": ("from . import helpers\n" + HEAD, CREATE_A, ""),
    "dunder-import-in-default": (
        HEAD,
        "        migrations.AddField(model_name='a', name='b', field=models.IntegerField("
        "default=__import__('os').getpid())),",
        "",
    ),
    "lambda-default": (
        HEAD,
        "        migrations.AddField(model_name='a', name='b', field=models.IntegerField("
        "default=lambda: 1)),",
        "",
    ),
    "dict-lookup": (
        HEAD,
        "        migrations.AddIndex(model_name='a', index=migrations.__dict__['x']),",
        "",
    ),
    "subscript": (
        HEAD,
        "        migrations.AddIndex(model_name='a', index=models.Index(fields=['a'][0:1])),",
        "",
    ),
    "comprehension": (
        HEAD,
        "        migrations.AddIndex(model_name='a', "
        "index=models.Index(fields=[f for f in 'ab'])),",
        "",
    ),
    "dunder-attribute": (
        HEAD,
        "        migrations.AddField(model_name='a', name='b', field=models.__class__),",
        "",
    ),
    "unknown-name-in-argument": (
        HEAD,
        "        migrations.AddField(model_name='a', name='b', field=evil.Field()),",
        "",
    ),
    # The third review of #680: code or SQL reached through an allowed first name.
    "uuid-os-system": (
        "import uuid\n" + HEAD,
        "        migrations.AlterModelOptions(name='a', options=dict(x=uuid.os.system('echo'))),",
        "",
    ),
    "ids-os-system": (
        "import app.core.ids\n" + HEAD,
        "        migrations.AddField(model_name='a', name='b', field=models.IntegerField("
        "default=app.core.ids.os.system('echo'))),",
        "",
    ),
    "call-command-flush": (
        "import django.core.validators\n" + HEAD,
        "        migrations.AlterModelOptions(name='a', options={'x': "
        "django.core.management.call_command('flush', interactive=False)}),",
        "",
    ),
    "raw-sql-index": (
        HEAD,
        "        migrations.AddIndex(model_name='a', index=models.Index("
        "models.expressions.RawSQL('1); DELETE FROM t; --', []), name='i')),",
        "",
    ),
    "func-template": (
        HEAD,
        "        migrations.AddConstraint(model_name='a', constraint=models.CheckConstraint("
        "condition=models.Q(models.Func(template='1=1); DELETE FROM t; --')), name='c')),",
        "",
    ),
    "template-keyword": (
        HEAD,
        "        migrations.AddConstraint(model_name='a', constraint=models.CheckConstraint("
        "condition=models.Q(x=1), name='c', template='x')),",
        "",
    ),
    # The fourth review of #680: SQL text carried inside otherwise allowed calls.
    "opclasses-sql": (
        HEAD,
        "        migrations.AddIndex(model_name='a', index=models.Index(fields=['title'], "
        "name='i', opclasses=['text_pattern_ops); DELETE FROM t; --'])),",
        "",
    ),
    "max-length-sql": (
        HEAD,
        "        migrations.AddField(model_name='a', name='b', field=models.CharField("
        "max_length='10); DELETE FROM t; --')),",
        "",
    ),
    "quoted-index-name": (
        HEAD,
        "        migrations.AddIndex(model_name='a', index=models.Index(fields=['a'], "
        "name='\"i\" ON t; DELETE FROM t; --')),",
        "",
    ),
    "quoted-db-column": (
        HEAD,
        "        migrations.AddField(model_name='a', name='b', field=models.IntegerField("
        "db_column='\"x\" int); DELETE FROM t; --')),",
        "",
    ),
    "db-table-option": (
        HEAD,
        "        migrations.CreateModel(name='T', fields=[], options={'db_table': 'other'}),",
        "",
    ),
    "field-list-sql": (
        HEAD,
        "        migrations.AddIndex(model_name='a', index=models.Index("
        "fields=['a); DELETE FROM t; --'], name='i')),",
        "",
    ),
    "alter-options-unmanaged": (
        HEAD,
        "        migrations.AlterModelOptions(name='a', options={'managed': False}),",
        "",
    ),
    "semicolon-elsewhere": (
        HEAD,
        "        migrations.AddField(model_name='a', name='b', field=models.ForeignKey("
        "on_delete=django.db.models.deletion.CASCADE, to='x.a; DROP TABLE t')),",
        "",
    ),
    "double-star-options": (
        HEAD,
        "        migrations.CreateModel(name='P', fields=[], **{'options': {'proxy': True}}),",
        "",
    ),
}


def _decorated(text: str) -> str:
    return text.replace("class Migration(", "@(lambda c: c)\nclass Migration(", 1)


def test_code_in_a_swappable_dependency_is_refused():
    text = _migration(
        CREATE_A,
        dependency="('x', '0001_initial'), "
        "migrations.swappable_dependency(__import__('os').system('echo'))",
    )
    with pytest.raises(Refused):
        _lines(text)
    other = _migration(
        CREATE_A, dependency="('x', '0001_initial'), migrations.swappable_dependency('x.A')"
    )
    with pytest.raises(Refused, match="neither"):
        _lines(other)
    fine = _migration(
        CREATE_A,
        dependency="('x', '0001_initial'), "
        "migrations.swappable_dependency(settings.AUTH_USER_MODEL)",
    ).replace(HEAD, "from django.conf import settings\n" + HEAD, 1)
    assert "canonical_counts.x.A: None -> 0" in _lines(fine)


@pytest.mark.parametrize("name", ["dependencies", "operations"])
def test_a_second_assignment_cannot_hide_the_first(name):
    """The first value still runs when the class body executes."""
    hidden = f"    {name} = [print('x')]\n"
    with pytest.raises(Refused):
        _lines(_migration(CREATE_A, extra=hidden))


def test_the_settings_text_and_quoted_help_still_pass():
    """Free text (help_text with quotes) and a real index stay accepted."""
    operations = (
        "        migrations.AddField(model_name='a', name='b', field=models.CharField("
        "max_length=20, help_text='Say \"yes\"; or no')),\n"
        "        migrations.AddIndex(model_name='a', index=models.Index(fields=['-b'], "
        "name='a_b_idx', opclasses=['text_pattern_ops'])),"
    )
    assert _lines(_migration(operations)) == leaf_lines("x", "0002_y", "0001_initial")


def test_a_decorator_or_a_metaclass_on_the_migration_class_is_refused():
    with pytest.raises(Refused, match="decorator or a class keyword"):
        _lines(_decorated(_migration(CREATE_A)))
    metaclass = _migration(CREATE_A).replace(
        "(migrations.Migration)", "(migrations.Migration, metaclass=type)", 1
    )
    with pytest.raises(Refused, match="decorator or a class keyword"):
        _lines(metaclass)


def test_every_real_schema_only_argument_shape_is_still_accepted():
    """The argument allowlist was surveyed from this repository's migrations: r89's and a
    constraint with `models.Q(...) | ~models.Q(...)` and a JSON `default=dict` pass."""
    operations = (
        "        migrations.AddField(model_name='a', name='j', "
        "field=models.JSONField(default=dict)),\n"
        "        migrations.AddConstraint(model_name='a', constraint=models.CheckConstraint("
        "condition=models.Q(x__gte=0) | ~models.Q(y=-1), name='c')),\n"
        "        migrations.AlterField(model_name='a', name='o', field=models.ForeignKey("
        "on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),"
    )
    head = "import django.db.models.deletion\nfrom django.conf import settings\n" + HEAD
    text = _migration(operations).replace(HEAD, head, 1)
    assert _lines(text) == leaf_lines("x", "0002_y", "0001_initial")


@pytest.mark.parametrize(
    ("head", "operations", "extra"), list(BYPASSES.values()), ids=list(BYPASSES)
)
def test_every_known_bypass_is_refused_before_deployment(head, operations, extra):
    text = _migration(operations, extra=extra).replace(HEAD, head, 1)
    with pytest.raises(Refused):
        _lines(text)


def test_a_dependency_that_is_not_the_previous_leaf_is_refused():
    with pytest.raises(Refused, match="do not name"):
        _lines(_migration(CREATE_A, dependency="('x', '0000_other')"))


def test_text_that_is_not_a_migration_is_refused():
    with pytest.raises(Refused):
        _lines("migrations.CreateModel(name='A', fields=[])")
    with pytest.raises(Refused):
        _lines("class Migration(:")


def test_a_model_the_fingerprint_does_not_count_is_refused():
    with pytest.raises(Refused, match=r"does not count x\.A"):
        expected_lines(
            app="x",
            migration="0002_y",
            previous_leaf="0001_initial",
            text=_migration(CREATE_A),
            uncounted=frozenset({"x.A"}),
        )


def test_every_app_label_is_its_directory_name():
    """The helper writes the directory as the app label (`recovery_fingerprint` counts by
    `model._meta.app_label`): a custom label would make its lines wrong."""
    for apps in sorted(ROOT.glob("app/*/apps.py")):
        assert f'label = "{apps.parent.name}"' in apps.read_text(encoding="utf-8"), apps


def test_the_repository_history_is_either_accepted_or_refused_never_crashes():
    accepted = refused = 0
    for path in sorted(ROOT.glob("app/*/migrations/0*.py")):
        try:
            analyse_migration(
                path.read_text(encoding="utf-8"),
                app=path.parent.parent.name,
                previous_leaf="0000_none",
            )
        except Refused:
            refused += 1
        else:  # pragma: no cover - every real migration depends on a real predecessor
            accepted += 1
    assert refused and not accepted  # "0000_none" is nobody's dependency


# ---------------------------------------------------------------------------------------
# The whole release, read from two commits
# ---------------------------------------------------------------------------------------

DEPLOYMENT = (
    'REBUILDABLE_MODELS = frozenset({"search.SearchDocument"})\n'
    'OPERATIONAL_MODELS = frozenset({"x.Throttle"})\n'
)


class Repo:
    """A throwaway git repository holding a release: helper, app x, its migrations."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.git_exe = shutil.which("git")
        if self.git_exe is None:
            pytest.skip("git is not available")
        self.write("tools/release_fingerprint.py", HELPER.read_text(encoding="utf-8"))
        self.write("app/core/deployment.py", DEPLOYMENT)
        self.write("config/settings.py", "INSTALLED_APPS = []\n")
        self.write("app/x/apps.py", 'class XConfig:\n    label = "x"\n')
        self.write("app/x/migrations/__init__.py", "")
        self.write("app/x/migrations/0001_initial.py", _migration(CREATE_A, dependency=""))
        self.git("init", "-q")
        self.previous = self.commit()

    def write(self, path: str, text: str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")

    def git(self, *args: str) -> str:
        return subprocess.run(  # noqa: S603 - git, a temporary repository, fixed arguments
            [str(self.git_exe), "-C", str(self.root), "-c", "core.autocrlf=false", *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def commit(self) -> str:
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "c")
        return self.git("rev-parse", "HEAD")

    def lines(self, ref: str) -> list[str]:
        return release_lines(repo=self.root, ref=ref, previous_release=self.previous, helper=HELPER)


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path / "repo")


def test_a_release_with_one_migration_gives_its_lines(repo):
    repo.write(
        "app/x/migrations/0002_b.py",
        _migration("        migrations.CreateModel(name='B', fields=[]),"),
    )
    ref = repo.commit()
    assert repo.lines(ref) == [
        "canonical_counts.x.B: None -> 0",
        "migration_leaves: newly present: x.0002_b",
        "migration_leaves: no longer present: x.0001_initial",
    ]


def test_a_release_without_a_migration_expects_nothing(repo):
    repo.write("app/x/views.py", "# code only\n")
    assert repo.lines(repo.commit()) == []


def test_two_migrations_in_one_app_are_refused(repo):
    repo.write("app/x/migrations/0002_b.py", _migration("        migrations.RunPython(print),"))
    repo.write(
        "app/x/migrations/0003_c.py",
        _migration(CREATE_A.replace("'A'", "'C'"), dependency="('x', '0002_b')"),
    )
    with pytest.raises(Refused, match="more than one migration"):
        repo.lines(repo.commit())


def test_migrations_in_two_apps_are_refused(repo):
    repo.write("app/y/apps.py", 'class YConfig:\n    label = "y"\n')
    repo.write("app/y/migrations/0001_initial.py", _migration(CREATE_A, dependency=""))
    repo.write(
        "app/x/migrations/0002_b.py",
        _migration("        migrations.CreateModel(name='B', fields=[]),"),
    )
    with pytest.raises(Refused, match="more than one migration"):
        repo.lines(repo.commit())


def test_a_changed_migration_is_refused(repo):
    repo.write(
        "app/x/migrations/0001_initial.py", _migration(CREATE_A, dependency="") + "# edited\n"
    )
    with pytest.raises(Refused, match="changed, not added"):
        repo.lines(repo.commit())


def test_a_model_production_does_not_count_is_refused(repo):
    repo.write(
        "app/x/migrations/0002_b.py",
        _migration("        migrations.CreateModel(name='Throttle', fields=[]),"),
    )
    with pytest.raises(Refused, match=r"does not count x\.Throttle"):
        repo.lines(repo.commit())


def test_a_custom_app_label_is_refused(repo):
    repo.write("app/x/apps.py", 'class XConfig:\n    label = "other"\n')
    repo.write(
        "app/x/migrations/0002_b.py",
        _migration("        migrations.CreateModel(name='B', fields=[]),"),
    )
    with pytest.raises(Refused, match="does not label"):
        repo.lines(repo.commit())


def test_a_helper_that_is_not_the_commits_copy_is_refused(repo, tmp_path):
    repo.write(
        "app/x/migrations/0002_b.py",
        _migration("        migrations.CreateModel(name='B', fields=[]),"),
    )
    ref = repo.commit()
    other = tmp_path / "release_fingerprint.py"
    other.write_text(HELPER.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    with pytest.raises(Refused, match=r"is not tools/release_fingerprint\.py at"):
        release_lines(repo=repo.root, ref=ref, previous_release=repo.previous, helper=other)


def test_a_release_that_moves_the_fingerprints_exclusions_is_refused(repo):
    """Moving a model into REBUILDABLE_MODELS / OPERATIONAL_MODELS would drop it from both
    sides of the comparison unseen, even in a release with no migration."""
    repo.write("app/core/deployment.py", DEPLOYMENT.replace('"x.Throttle"', '"x.Throttle", "x.A"'))
    with pytest.raises(Refused, match="does not count"):
        repo.lines(repo.commit())


def test_migration_modules_moved_outside_app_is_refused(repo):
    repo.write("config/settings.py", "MIGRATION_MODULES = {'x': 'elsewhere.migrations'}\n")
    with pytest.raises(Refused, match="MIGRATION_MODULES"):
        repo.lines(repo.commit())


def test_a_changed_migrations_init_is_refused(repo):
    repo.write("app/x/migrations/__init__.py", "import os\nos.getpid()\n")
    with pytest.raises(Refused, match=r"__init__\.py changes"):
        repo.lines(repo.commit())


def test_a_new_app_says_so(repo):
    repo.write("app/y/apps.py", 'class YConfig:\n    label = "y"\n')
    repo.write("app/y/migrations/__init__.py", "")
    repo.write("app/y/migrations/0001_initial.py", _migration(CREATE_A, dependency=""))
    with pytest.raises(Refused, match="new app"):
        repo.lines(repo.commit())


def test_a_label_in_a_comment_or_another_attribute_is_not_the_label(repo):
    repo.write("app/x/apps.py", 'class XConfig:\n    verbose_label = "x"\n    # label = "x"\n')
    repo.write(
        "app/x/migrations/0002_b.py",
        _migration("        migrations.CreateModel(name='B', fields=[]),"),
    )
    with pytest.raises(Refused, match="does not label"):
        repo.lines(repo.commit())


def test_the_repo_must_be_the_top_level(repo):
    repo.write(
        "app/x/migrations/0002_b.py",
        _migration("        migrations.CreateModel(name='B', fields=[]),"),
    )
    ref = repo.commit()
    with pytest.raises(Refused, match="top level"):
        release_lines(
            repo=repo.root / "app", ref=ref, previous_release=repo.previous, helper=HELPER
        )


def test_the_command_line_says_refused_with_its_own_exit_code(repo, capsys):
    repo.write("app/x/migrations/0002_b.py", _migration("        migrations.RunSQL('x'),"))
    ref = repo.commit()
    argv = ["--repo", str(repo.root), "--ref", ref, "--previous-release", repo.previous]
    assert main(argv) == EXIT_REFUSED
    assert "REFUSED: unsupported: RunSQL" in capsys.readouterr().err
    assert main([*argv[:4], "--previous-release", repo.previous[:12]]) == EXIT_REFUSED
    assert main(
        ["--migration-file", "nope.py", "--app", "x", "--previous-leaf", "0001_initial"]
    ) == (EXIT_REFUSED)


def test_the_review_mode_prints_r89s_lines(capsys):
    argv = ["--migration-file", str(R89_FILE), "--app", "legacy_import"]
    assert main([*argv, "--previous-leaf", "0032_postipoiss_letters"]) == 0
    assert capsys.readouterr().out.splitlines()[0] == (
        "canonical_counts.legacy_import.LovableOwnerAlias: None -> 0"
    )
