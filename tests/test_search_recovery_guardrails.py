"""The search projection's checks check what they claim, and its repair repays.

Three findings, each proven on the code before it was fixed:

* **ENG-080** — `check_search_integrity` recomputed text for 250 MATTER rows
  only, chosen by UUIDv7 order (the rows written longest ago), compared
  crossings for five of eight kinds, and named `refresh_matter_search` as the
  repair for a stale child row it cannot repair.
* **ENG-085** — `rebuild_search_index` rebuilt the index and left every
  `SearchRebuildDebt` row in place, so freshness stayed red after the repair and
  the worker rebuilt the whole corpus again.
* **ENG-142** — a release that moved `INDEX_VERSION` left `deployment_readiness`
  green while search answered nothing, and nothing in the release artifact said
  a rebuild was owed.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import threading
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from app.accounts.models import User
from app.core import deployment
from app.legacy_import.source_pages import (
    MatterSourcePage,
    SourceMatchClass,
    SourceMatchMethod,
    SourceRelationshipKind,
)
from app.matters.enums import ExternalPositionProvenance
from app.matters.models import (
    Entry,
    MatterEngagement,
    MatterExternalPosition,
    MatterProceduralDevelopment,
)
from app.search import freshness
from app.search.indexing import rebuild_all
from app.search.management.commands.check_search_integrity import (
    build_report,
    kind_contracts,
)
from app.search.models import (
    INDEX_VERSION,
    SearchDocument,
    SearchRebuildDebt,
    SearchRebuildReason,
    SearchSourceKind,
)
from tests import factories
from tests import synthetic_corpus as corpus
from tests.test_search_reliability import _source_page

PDF = "application/pdf"
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci" / "index_version_change.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("index_version_change", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # the dataclass resolves annotations through it
    spec.loader.exec_module(module)
    return module


index_version_change = _load_script()

pytestmark = pytest.mark.django_db


@pytest.fixture
def every_kind(specialist, capture_evidence, extract):
    """At least one row of every source kind, freshly rebuilt."""
    matter = factories.MatterFactory(owner=specialist, title="Kõigi liikidega teema")
    factories.EntryFactory(matter=matter, author=specialist, body="<p>Kõne nõunikuga.</p>")
    factories.SubmissionFactory(matter=matter, title="Koja arvamus", summary="Toetame.")
    version = capture_evidence(
        matter, corpus.text_pdf(["Sisu failis."]), "lisa.pdf", PDF, title="Lisa"
    )
    extract(version)
    MatterSourcePage.objects.create(
        matter=matter,
        source_page=_source_page(),
        relationship_kind=SourceRelationshipKind.PRIMARY,
        match_method=SourceMatchMethod.MANUAL,
        match_class=SourceMatchClass.REVIEWED,
    )
    MatterEngagement.objects.create(matter=matter, kind="SURVEY", title="Küsitlus")
    MatterProceduralDevelopment.objects.create(
        matter=matter, title="Ministeerium saatis eelnõu", note="Märkus", created_by=specialist
    )
    MatterExternalPosition.objects.create(
        matter=matter,
        summary="Liit toetab",
        source_label="Liit",
        provenance=ExternalPositionProvenance.RECEIVED,
        created_by=specialist,
    )
    rebuild_all()
    kinds = set(SearchDocument.objects.values_list("source_kind", flat=True))
    assert kinds == set(SearchSourceKind.values), kinds
    return matter


def _labels(report) -> list[str]:
    return [finding.label for finding in report.findings]


# ---------------------------------------------------------------------------
# ENG-080 — every kind is checked, and the report says how
# ---------------------------------------------------------------------------


def test_every_source_kind_has_a_complete_integrity_contract():
    """The next source kind cannot be added with the check aware of only the old ones."""
    contracts = kind_contracts()

    assert set(contracts) == set(SearchSourceKind.values)
    for kind, contract in contracts.items():
        assert callable(contract.rebuild), kind
        assert contract.repair, kind
        assert (contract.source_matter is None) == (kind == SearchSourceKind.MATTER), kind


def test_a_healthy_corpus_is_clean_in_both_modes(every_kind):
    sampled = build_report()
    full = build_report(full=True)

    assert sampled.ok and full.ok, (_labels(sampled), _labels(full))
    assert set(full.drift_checked) == set(SearchSourceKind.values)
    assert sum(full.drift_checked.values()) == SearchDocument.objects.count()


@pytest.mark.parametrize("kind", SearchSourceKind.values)
def test_stale_text_of_every_kind_is_reported(every_kind, kind):
    SearchDocument.objects.filter(source_kind=kind).update(title="vananenud pealkiri")

    for report in (build_report(), build_report(full=True)):
        assert not report.ok
        label = kind_contracts()[kind].label
        assert f"{label}: vananenud tekst" in _labels(report), _labels(report)


def test_a_marge_edited_behind_the_signals_is_reported(every_kind):
    MatterProceduralDevelopment.objects.update(title="Muudetud otse andmebaasis")

    report = build_report()

    assert "Märked: vananenud tekst" in _labels(report)


def test_an_author_renamed_behind_the_signals_is_reported(every_kind, specialist):
    """Identity text the projection carries — an Entry's author name."""
    User.objects.filter(pk=specialist.pk).update(display_name="Ümbernimetatud Jurist")

    report = build_report()

    assert "Sissekanded: vananenud tekst" in _labels(report)


def test_a_removed_record_whose_row_survived_is_reported(every_kind):
    from django.utils import timezone

    Entry.objects.update(removed_at=timezone.now())

    report = build_report()

    assert "Sissekanded: vananenud tekst" in _labels(report)


@pytest.mark.parametrize(
    "kind",
    [
        SearchSourceKind.PROCEDURAL_DEVELOPMENT,
        SearchSourceKind.EXTERNAL_POSITION,
        SearchSourceKind.DOCUMENT,
        SearchSourceKind.ENGAGEMENT,
    ],
)
def test_a_row_pointing_at_another_matter_is_reported_for_every_kind(every_kind, specialist, kind):
    elsewhere = factories.MatterFactory(owner=specialist, title="Hoopis teine teema")
    SearchDocument.objects.filter(source_kind=kind).update(matter=elsewhere)

    report = build_report()

    assert f"{kind_contracts()[kind].label}: teemaviide" in _labels(report)


def test_the_sample_spans_the_corpus_and_full_reads_everything(specialist):
    for number in range(12):
        factories.MatterFactory(owner=specialist, title=f"Teema {number}")
    rebuild_all()

    first = build_report(sample=3)
    second = build_report(sample=3)
    full = build_report(full=True)

    assert first.drift_checked[SearchSourceKind.MATTER] == 3
    assert first.drift_checked == second.drift_checked
    assert full.drift_checked[SearchSourceKind.MATTER] == 12
    # Drift in one row: the full pass always finds it.
    newest = SearchDocument.objects.filter(source_kind=SearchSourceKind.MATTER).latest("pk")
    SearchDocument.objects.filter(pk=newest.pk).update(title="vana")
    assert "Teemad: vananenud tekst" in _labels(build_report(full=True))


def test_a_child_finding_names_the_repair_that_repairs_it(every_kind):
    SearchDocument.objects.filter(source_kind=SearchSourceKind.ENTRY).update(body_text="vana")

    report = build_report()

    finding = next(f for f in report.findings if f.label == "Sissekanded: vananenud tekst")
    assert "rebuild_search_index" in finding.detail
    assert "refresh_matter_search" not in finding.detail


def test_the_command_says_which_mode_it_ran(every_kind):
    sampled, full = StringIO(), StringIO()
    call_command("check_search_integrity", stdout=sampled)
    call_command("check_search_integrity", "--full", stdout=full)

    assert "valim" in sampled.getvalue() and "--full" in sampled.getvalue()
    assert "kõik read kontrollitud" in full.getvalue()


# ---------------------------------------------------------------------------
# ENG-085 — the manual repair pays the debt it repaired, and only that
# ---------------------------------------------------------------------------


def _debt(reason: str = SearchRebuildReason.ORGANISATION_RENAMED) -> SearchRebuildDebt:
    return freshness.mark_rebuild_owed(reason)


def test_a_manual_rebuild_clears_the_debt_it_covered(every_kind):
    owed = _debt()
    assert not freshness.status().is_clear

    out = StringIO()
    call_command("rebuild_search_index", stdout=out)

    assert not SearchRebuildDebt.objects.filter(pk=owed.pk).exists()
    assert freshness.status().is_clear
    assert "Cleared 1" in out.getvalue()
    call_command("check_search_freshness", "--quiet")  # exits 0


def test_the_keep_existing_rebuild_clears_it_too(every_kind):
    _debt()
    call_command("rebuild_search_index", "--keep-existing", stdout=StringIO())
    assert freshness.status().is_clear


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_debt_written_during_the_rebuild_survives_it(specialist, monkeypatch):
    """A: claimed at start and cleared. B: marked mid-rebuild, committed from
    another connection, and still owed afterwards."""
    factories.MatterFactory(owner=specialist, title="Teema")
    first = _debt()
    marked_during: dict[str, object] = {}
    real_rebuild = freshness.rebuild_all

    def rebuild_with_a_rename_in_the_middle(**kwargs: object) -> object:
        def rename_elsewhere() -> None:
            try:
                marked_during["debt"] = _debt(SearchRebuildReason.TAG_RENAMED)
            finally:
                connection.close()

        thread = threading.Thread(target=rename_elsewhere)
        thread.start()
        thread.join()
        return real_rebuild(**kwargs)

    monkeypatch.setattr(freshness, "rebuild_all", rebuild_with_a_rename_in_the_middle)

    call_command("rebuild_search_index", stdout=StringIO())

    remaining = set(SearchRebuildDebt.objects.values_list("pk", flat=True))
    assert first.pk not in remaining
    assert marked_during["debt"].pk in remaining  # type: ignore[attr-defined]
    assert not freshness.status().is_clear


def test_a_failed_rebuild_keeps_the_debt_and_says_why(every_kind, monkeypatch):
    owed = _debt()

    def explode(**kwargs: object) -> None:
        raise RuntimeError("katkes")

    monkeypatch.setattr(freshness, "rebuild_all", explode)

    with pytest.raises(RuntimeError):
        call_command("rebuild_search_index", stdout=StringIO())

    owed.refresh_from_db()
    assert owed.attempts == 1
    assert "RuntimeError" in owed.last_error
    assert not freshness.status().is_clear


def test_a_rebuild_with_nothing_owed_still_rebuilds(every_kind):
    SearchDocument.objects.all().delete()
    call_command("rebuild_search_index", stdout=StringIO())
    assert SearchDocument.objects.exists()


# ---------------------------------------------------------------------------
# ENG-142 — a moved index version cannot be missed
# ---------------------------------------------------------------------------


def test_a_current_index_is_ready(every_kind):
    """A. Unchanged version, current rows: the ordinary release, unchanged."""
    report = deployment.readiness_report()

    assert report.search_index is not None
    assert report.search_index.stale_rows == 0
    assert not report.search_index.problem
    assert not [p for p in report.problems if "search" in p.lower()]


def test_before_the_rebuild_the_stale_index_is_expected(every_kind):
    """B. New code, old rows, named as the pre-rebuild phase: said, not failed."""
    SearchDocument.objects.update(index_version="TEEMA.1")

    report = deployment.readiness_report(search_phase=deployment.SEARCH_PHASE_PRE_REBUILD)

    assert report.search_index.stale_rows == SearchDocument.objects.count()
    assert not [p for p in report.problems if "search" in p.lower()]
    assert any("rebuild_search_index" in warning for warning in report.warnings)


def test_after_the_rebuild_the_final_check_is_green(every_kind):
    """C. The rebuild ran: the final check passes."""
    SearchDocument.objects.update(index_version="TEEMA.1")
    call_command("rebuild_search_index", stdout=StringIO())

    report = deployment.readiness_report()

    assert report.search_index.stale_rows == 0
    assert not report.search_index.problem


def test_a_skipped_rebuild_fails_the_final_check(every_kind):
    """D. The operator skipped the rebuild: the mandatory final check fails."""
    SearchDocument.objects.update(index_version="TEEMA.1")

    report = deployment.readiness_report()

    assert any("rebuild_search_index" in problem for problem in report.problems)
    with pytest.raises(CommandError, match="older index version"):
        call_command("deployment_readiness", "--quiet", stdout=StringIO())


def test_matters_with_no_current_search_rows_fail_the_final_check(every_kind):
    SearchDocument.objects.all().delete()

    report = deployment.readiness_report()

    assert any("search answers nothing" in problem for problem in report.problems)


def test_an_empty_installation_is_ready_without_an_index(db):
    report = deployment.readiness_report()
    assert not report.search_index.problem


# -- the release manifest -----------------------------------------------------


def _repo(tmp_path: Path, versions: list[str | None]) -> tuple[Path, list[str]]:
    """A throwaway repository with one commit per models.py version."""
    root = tmp_path / "repo"
    (root / "app" / "search").mkdir(parents=True)
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*git, "init", "-q"], check=True)  # noqa: S603
    commits = []
    for number, version in enumerate(versions):
        models = root / index_version_change.MODELS_PATH
        if version is None:
            models.write_text("# no constant here\n")
        else:
            models.write_text(f'"""x"""\n\nINDEX_VERSION = "{version}"\n\nOTHER = "{number}"\n')
        subprocess.run([*git, "add", "-A"], check=True)  # noqa: S603
        subprocess.run([*git, "commit", "-q", "-m", f"c{number}"], check=True)  # noqa: S603
        commits.append(
            subprocess.run(  # noqa: S603
                [*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
            ).stdout.strip()
        )
    return root, commits


def test_the_manifest_says_no_when_the_version_stays(tmp_path, capsys):
    root, (previous, target) = _repo(tmp_path, ["DOKUMENT.1", "DOKUMENT.1"])
    env = tmp_path / "env"

    assert (
        index_version_change.main(
            [
                "--previous",
                previous,
                "--target",
                target,
                "--git-dir",
                str(root),
                "--env-file",
                str(env),
            ]
        )
        == 0
    )

    printed = capsys.readouterr().out
    assert "search_rebuild_required:  NO" in printed
    assert "INDEX_VERSION_CHANGED=NO" in env.read_text()


def test_the_manifest_says_yes_when_the_version_moves(tmp_path, capsys):
    root, (previous, target) = _repo(tmp_path, ["TEEMA.1", "DOKUMENT.1"])
    env = tmp_path / "env"

    index_version_change.main(
        ["--previous", previous, "--target", target, "--git-dir", str(root), "--env-file", str(env)]
    )

    printed = capsys.readouterr().out
    assert "index_version:            TEEMA.1 -> DOKUMENT.1" in printed
    assert "search_rebuild_required:  YES" in printed
    assert "INDEX_VERSION_CHANGED=YES" in env.read_text()


def test_an_unreadable_previous_version_requires_the_rebuild(tmp_path, capsys):
    root, (previous, target) = _repo(tmp_path, [None, "DOKUMENT.1"])

    index_version_change.main(["--previous", previous, "--target", target, "--git-dir", str(root)])

    assert "search_rebuild_required:  YES" in capsys.readouterr().out


def test_an_unreadable_target_refuses_the_release(tmp_path):
    root, (previous, target) = _repo(tmp_path, ["DOKUMENT.1", None])

    assert (
        index_version_change.main(
            ["--previous", previous, "--target", target, "--git-dir", str(root)]
        )
        == 2
    )


def test_the_script_reads_the_constant_the_application_uses():
    source = (Path(__file__).resolve().parents[1] / index_version_change.MODELS_PATH).read_text()
    assert index_version_change.declared_version(source) == INDEX_VERSION
