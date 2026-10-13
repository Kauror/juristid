"""The Lovable mirror: import, idempotency, refusal, matching, diffing, access, isolation.

Every value is invented, and every bundle is written by the test itself into
pytest's temporary directory — outside the checkout, as the importer requires.
No Lovable export, schema dump or record is checked in (docs/adr/0157).
"""

from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.core.management import CommandError, call_command
from django.db import DatabaseError, transaction
from django.urls import reverse

from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.core.errors import ImmutableRecordError
from app.documents.models import Document, DocumentVersion
from app.legacy_import.models import ImportBatch, MatterSourceReference
from app.lovable_mirror import catalogue
from app.lovable_mirror.catalogue import MappingClass
from app.lovable_mirror.importer import import_bundle, import_from
from app.lovable_mirror.models import SourceFieldValue, SourceRecord, SourceSnapshot
from app.lovable_mirror.readers import (
    LIVE_READER_SETTING,
    BundleRefused,
    FileExportReader,
    LiveReadOnlyReader,
    ReaderUnavailable,
)
from app.lovable_mirror.reconcile import MatchClass, MatchReason, Outcome, build_report
from app.matters.models import Matter
from app.submissions.models import Submission
from app.workflow.models import NextAction
from tests import factories

pytestmark = pytest.mark.django_db

YEAR = 2031
APP = Path(settings.BASE_DIR) / "app" / "lovable_mirror"


# ---------------------------------------------------------------------------
# Building invented bundles
# ---------------------------------------------------------------------------


def topic(nr: str | None, teema: str, dataset: str = "ds-1", **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": f"t-{dataset}-{nr}-{teema}"[:120],
        "dataset_id": dataset,
        "nr": nr,
        "aasta": YEAR,
        "teema": teema,
        "oigusakt": None,
        "sisse": None,
        "arvamuse_tahtaeg": None,
        "valja": None,
        "valja_kuupaev": None,
        "vastutaja": None,
        "hetkeseis": None,
        "jargmiseks": None,
        "onenote_link": None,
        "raw_hyperlink": None,
        "sheet_name": str(YEAR),
        "row_index": 2,
    }
    row.update(extra)
    return row


def write_bundle(
    directory: Path,
    tables: dict[str, list[dict[str, Any]]],
    *,
    exported_at: str = "2031-03-01T09:00:00+02:00",
    data_class: str = "SYNTHETIC",
    **manifest_extra: Any,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    spec = {}
    for name, rows in tables.items():
        data = json.dumps(rows, ensure_ascii=False).encode("utf-8")
        (directory / f"{name}.json").write_bytes(data)
        spec[name] = {
            "file": f"{name}.json",
            "sha256": hashlib.sha256(data).hexdigest(),
            "row_count": len(rows),
        }
    manifest = {
        "format": "juristid.lovable-mirror-export",
        "format_version": 1,
        "data_class": data_class,
        "source_schema_version": "invented-schema@test",
        "exported_at": exported_at,
        "exported_by": "Testoperaator",
        "export_method": "invented for a test",
        "tables": spec,
        "notes": "",
    }
    manifest.update(manifest_extra)
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory


def datasets(*current: str, stale: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    return [{"id": d, "is_current": True, "file_name": "x.xlsx"} for d in current] + [
        {"id": d, "is_current": False, "file_name": "x.xlsx"} for d in stale
    ]


def simple_bundle(directory: Path, **kwargs: Any) -> Path:
    return write_bundle(
        directory,
        {"datasets": datasets("ds-1"), "topics": [topic(f"{YEAR}_1", "Näidisteema üks")]},
        **kwargs,
    )


def manifest_sha(directory: Path) -> str:
    return hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest()


def run_import(directory: Path, expected: str = "") -> Any:
    return import_from(FileExportReader(directory, expected_sha256=expected))


# ---------------------------------------------------------------------------
# Import and idempotency
# ---------------------------------------------------------------------------


def test_an_import_stores_one_immutable_snapshot_with_its_records(tmp_path):
    bundle = write_bundle(
        tmp_path / "b1",
        {
            "datasets": datasets("ds-1"),
            "topics": [
                topic(f"{YEAR}_1", "Näidisteema üks", jargmiseks="Helistada"),
                topic(f"{YEAR}_2", "Näidisteema kaks", leiutatud_veerg="x"),
            ],
        },
    )
    result = run_import(bundle)

    assert result.created
    snapshot = result.snapshot
    assert snapshot.manifest_sha256 == manifest_sha(bundle)
    assert snapshot.row_counts == {"datasets": 1, "topics": 2}
    assert snapshot.source_exported_at == dt.datetime(2031, 3, 1, 7, 0, tzinfo=dt.UTC)
    assert snapshot.source_schema_version == "invented-schema@test"
    assert snapshot.catalogue_version == catalogue.CATALOGUE_VERSION
    assert snapshot.bundle_label == "b1"
    assert snapshot.uncatalogued_fields == {"topics": ["leiutatud_veerg"]}

    record = SourceRecord.objects.get(snapshot=snapshot, table="topics", stable_key=f"{YEAR}_1")
    classes = {v.field: v.mapping_class for v in record.values.all()}
    assert classes["nr"] == MappingClass.IDENTITY
    assert classes["teema"] == MappingClass.COMPARABLE
    assert classes["jargmiseks"] == MappingClass.NEVER_AUTO
    assert classes["row_index"] == MappingClass.PROVENANCE
    assert record.values.get(field="jargmiseks").value == "Helistada"
    unknown = SourceFieldValue.objects.get(record__stable_key=f"{YEAR}_2", field="leiutatud_veerg")
    assert unknown.mapping_class == MappingClass.UNCATALOGUED


def test_importing_the_same_bundle_twice_writes_nothing_the_second_time(tmp_path):
    bundle = simple_bundle(tmp_path / "b")
    first = run_import(bundle)
    counts = (SourceRecord.objects.count(), SourceFieldValue.objects.count())

    second = run_import(bundle)

    assert first.created and not second.created
    assert second.snapshot.pk == first.snapshot.pk
    assert SourceSnapshot.objects.count() == 1
    assert (SourceRecord.objects.count(), SourceFieldValue.objects.count()) == counts


def test_the_import_command_is_idempotent_and_says_so(tmp_path, capsys):
    bundle = simple_bundle(tmp_path / "b")
    call_command("lovable_mirror_import", "--export", str(bundle))
    call_command("lovable_mirror_import", "--export", str(bundle))
    out = capsys.readouterr().out
    assert "Hetktõmmis imporditud." in out
    assert "Sama kontrollsummaga hetktõmmis on juba olemas." in out
    assert SourceSnapshot.objects.count() == 1


def test_stored_rows_cannot_be_updated(tmp_path):
    snapshot = run_import(simple_bundle(tmp_path / "b")).snapshot
    snapshot.notes = "muudetud"
    with pytest.raises(ImmutableRecordError, match="immutable"):
        snapshot.save()
    with pytest.raises(DatabaseError, match="does not permit UPDATE"), transaction.atomic():
        SourceRecord.objects.update(stable_key="x")
    with pytest.raises(DatabaseError, match="does not permit UPDATE"), transaction.atomic():
        SourceFieldValue.objects.update(value="x")


def test_a_whole_snapshot_can_still_be_deleted_for_retention(tmp_path):
    snapshot = run_import(simple_bundle(tmp_path / "b")).snapshot
    snapshot.delete()
    assert not SourceRecord.objects.exists()
    assert not SourceFieldValue.objects.exists()


# ---------------------------------------------------------------------------
# Refusals: checksum, malformed input, location, real data
# ---------------------------------------------------------------------------


def _nothing_written() -> bool:
    return not (SourceSnapshot.objects.exists() or SourceRecord.objects.exists())


def test_a_table_file_changed_after_export_is_refused(tmp_path):
    bundle = simple_bundle(tmp_path / "b")
    (bundle / "topics.json").write_text(
        json.dumps([topic(f"{YEAR}_1", "Muudetud pealkiri")]), encoding="utf-8"
    )
    with pytest.raises(BundleRefused, match="manifest ütleb"):
        run_import(bundle)
    assert _nothing_written()


def test_an_unexpected_manifest_digest_is_refused(tmp_path):
    bundle = simple_bundle(tmp_path / "b")
    with pytest.raises(BundleRefused, match="Importida võib ainult seda pakki"):
        run_import(bundle, expected="0" * 64)
    assert _nothing_written()
    assert run_import(bundle, expected=manifest_sha(bundle)).created


MALFORMED = {
    "unknown_table": (
        lambda d: write_bundle(d, {"leiutatud_tabel": [{"id": "1"}]}),
        "Tundmatu tabel",
    ),
    "unmapped_live_table": (
        lambda d: write_bundle(d, {"topic_change_event": [{"id": "1"}]}),
        "elusskeemis teada, kuid ei ole kataloogis .NOT MAPPED.",
    ),
    "unmapped_live_table_topic": (
        lambda d: write_bundle(d, {"topic": [{"id": "1"}]}),
        "Schema-only export",
    ),
    "missing_required_column": (
        lambda d: write_bundle(d, {"topics": [{"id": "1", "nr": "2031_1", "dataset_id": "x"}]}),
        "puuduvad väljad: teema",
    ),
    "duplicate_id": (
        lambda d: write_bundle(d, {"topics": [topic("2031_1", "a"), topic("2031_1", "a")]}),
        "mitu korda",
    ),
    "wrong_format": (
        lambda d: write_bundle(d, {"topics": []}, format="midagi-muud"),
        "«format»",
    ),
    "unsupported_version": (
        lambda d: write_bundle(d, {"topics": []}, format_version=2),
        "ei toetata",
    ),
    "naive_timestamp": (
        lambda d: write_bundle(d, {"topics": []}, exported_at="2031-03-01T09:00:00"),
        "ajavööndiga",
    ),
    "unknown_data_class": (
        lambda d: write_bundle(d, {"topics": []}, data_class="MAYBE"),
        "data_class",
    ),
}


@pytest.mark.parametrize("case", sorted(MALFORMED))
def test_malformed_bundles_are_refused_and_write_nothing(tmp_path, case):
    build, message = MALFORMED[case]
    bundle = build(tmp_path / "b")
    with pytest.raises(BundleRefused, match=message):
        run_import(bundle)
    assert _nothing_written()


def test_a_manifest_that_is_not_json_is_refused(tmp_path):
    bundle = simple_bundle(tmp_path / "b")
    (bundle / "manifest.json").write_text("{ei ole json", encoding="utf-8")
    with pytest.raises(BundleRefused, match="ei ole korrektne JSON"):
        run_import(bundle)


def test_a_row_count_that_disagrees_with_the_manifest_is_refused(tmp_path):
    bundle = simple_bundle(tmp_path / "b")
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    manifest["tables"]["topics"]["row_count"] = 5
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BundleRefused, match="manifest ütleb 5"):
        run_import(bundle)


def test_a_table_file_outside_the_bundle_is_refused(tmp_path):
    bundle = simple_bundle(tmp_path / "b")
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    manifest["tables"]["topics"]["file"] = "../topics.json"
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BundleRefused, match="pelgalt failinimi"):
        run_import(bundle)


def test_a_bundle_inside_the_checkout_is_refused_before_it_is_read():
    inside = Path(settings.BASE_DIR) / "private-data" / "lovable-export"
    with pytest.raises(BundleRefused, match="on repositooriumi sees"):
        run_import(inside)
    with pytest.raises(CommandError, match="on repositooriumi sees"):
        call_command("lovable_mirror_import", "--export", str(inside))


def test_a_real_bundle_is_refused_where_real_data_is_not_allowed(tmp_path, settings):
    settings.REAL_DATA_ALLOWED = False
    bundle = simple_bundle(tmp_path / "b", data_class="REAL")
    with pytest.raises(BundleRefused, match="REAL_DATA_ALLOWED on väljas"):
        run_import(bundle)
    assert _nothing_written()


# ---------------------------------------------------------------------------
# The live reader seam: disabled, and makes no connection
# ---------------------------------------------------------------------------


def test_the_live_reader_refuses_while_disabled(settings):
    assert not getattr(settings, LIVE_READER_SETTING, False)
    with pytest.raises(ReaderUnavailable, match="välja lülitatud"):
        LiveReadOnlyReader().read()
    with pytest.raises(CommandError, match="välja lülitatud"):
        call_command("lovable_mirror_import", "--live")


def test_the_live_reader_refuses_even_when_enabled_because_none_is_built(settings):
    setattr(settings, LIVE_READER_SETTING, True)
    with pytest.raises(ReaderUnavailable, match="ei ole veel ehitatud"):
        LiveReadOnlyReader().read()
    assert _nothing_written()


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _app_modules() -> list[Path]:
    return sorted(p for p in APP.rglob("*.py") if "migrations" not in p.parts)


def test_the_mirror_imports_no_network_client_and_no_supabase():
    forbidden = ("socket", "requests", "httpx", "urllib", "http.client", "supabase", "psycopg")
    for path in _app_modules():
        for name in _imports(path):
            assert not name.startswith(forbidden), f"{path.name} imports {name}"


#: The only canonical-side modules the mirror may import. Every one is a reader:
#: models to query, the pure owner resolver, the visibility gate. No service
#: that creates a Submission, NextAction, stage change, document or ownership
#: is reachable from here — so Järgmiseks, Välja, Hetkeseis and notes cannot be
#: converted by any code path in this app.
ALLOWED_PROJECT_IMPORTS = {
    "app.accounts.models",
    "app.core.authorization",
    "app.core.errors",
    "app.core.ids",
    "app.core.text",
    "app.legacy_import.resolution",
    "app.matters.models",
}


def test_the_mirror_reaches_no_canonical_write_service():
    for path in _app_modules():
        for name in _imports(path):
            if name.startswith("app.") and not name.startswith("app.lovable_mirror"):
                assert name in ALLOWED_PROJECT_IMPORTS, f"{path.name} imports {name}"


# ---------------------------------------------------------------------------
# Matching and canonical comparison
# ---------------------------------------------------------------------------


@pytest.fixture
def owner(db):
    return factories.UserFactory(display_name="Kaarel Näidismees")


@pytest.fixture
def other_owner(db):
    return factories.UserFactory(display_name="Leelo Näidisnaine")


def _matter(number: int, **kwargs: Any) -> Matter:
    return factories.MatterFactory(reference_year=YEAR, reference_number=number, **kwargs)


def _by_nr(report: Any) -> dict[str, Any]:
    return {item.row.key: item for item in report.rows}


def test_rows_are_classified_matched_unmatched_and_ambiguous(tmp_path, department_head, specialist):
    _matter(1, title="Näidisteema üks", owner=specialist)
    _matter(2, title="Kahe reaga teema", owner=specialist)
    _matter(3, title="Piiratud näidisteema", owner=specialist, visibility=Visibility.RESTRICTED)
    run_import(
        write_bundle(
            tmp_path / "b",
            {
                "datasets": datasets("ds-1"),
                "topics": [
                    topic(f"{YEAR}_1", "Näidisteema üks"),
                    topic(f"{YEAR}_2", "Esimene koopia"),
                    topic(f"{YEAR}_2", "Teine koopia", id="t-dup"),
                    topic(f"{YEAR}_3", "Piiratud näidisteema"),
                    topic(f"{YEAR}_99", "Teemata rida"),
                    topic("mitte-viide", "Vigase NR-iga rida"),
                ],
            },
        )
    )

    # The department head reads every Matter (docs/adr/0042), so nothing is
    # withheld from them and every class appears.
    report = build_report(department_head)
    rows = _by_nr(report)
    assert report.hidden_rows == 0
    assert rows[f"{YEAR}_1"].match_class == MatchClass.MATCHED
    assert rows[f"{YEAR}_3"].match_class == MatchClass.MATCHED
    assert rows[f"{YEAR}_2"].match_class == MatchClass.AMBIGUOUS
    assert rows[f"{YEAR}_2"].reason == MatchReason.DUPLICATE_SOURCE_KEY
    assert rows[f"{YEAR}_99"].reason == MatchReason.NO_MATTER
    assert rows["mitte-viide"].reason == MatchReason.NO_KEY
    assert len(report.ambiguous) == 2
    assert len(report.matched) == 2


def test_only_comparable_fields_can_conflict_and_unknown_is_not_a_conflict(
    tmp_path, administrator, owner, other_owner
):
    _matter(
        1,
        title="Kanooniline pealkiri",
        owner=owner,
        received_date=dt.date(2031, 1, 10),
        response_deadline=dt.date(2031, 2, 1),
    )
    _matter(2, title="Teine teema", owner=owner, alternate_titles=["Vana nimi"])
    _matter(3, title="Kolmas teema", owner=None)
    run_import(
        write_bundle(
            tmp_path / "b",
            {
                "datasets": datasets("ds-1"),
                "topics": [
                    topic(
                        f"{YEAR}_1",
                        "Lovable'i pealkiri",
                        sisse="2031-01-10",
                        arvamuse_tahtaeg="2031-02-05",
                        vastutaja="Leelo",
                        hetkeseis="Riigikogus",
                        jargmiseks="Saata arvamus 15.03",
                        valja="12.02.2031",
                    ),
                    topic(f"{YEAR}_2", "vana  NIMI", vastutaja="Kaarel", sisse="pole teada"),
                    topic(f"{YEAR}_3", "Kolmas teema", vastutaja="Tundmatu Isik"),
                ],
            },
        )
    )
    rows = _by_nr(build_report(administrator))

    first = {c.field: c for c in rows[f"{YEAR}_1"].comparisons}
    assert first["teema"].outcome == Outcome.DIFFERENT
    assert first["sisse"].outcome == Outcome.SAME
    assert first["arvamuse_tahtaeg"].outcome == Outcome.DIFFERENT
    assert first["vastutaja"].outcome == Outcome.DIFFERENT
    assert first["vastutaja"].canonical == "Kaarel Näidismees"
    # Hetkeseis is set beside the stage for reading, never compared.
    assert first["hetkeseis"].outcome == Outcome.SHOWN
    assert {c.field for c in rows[f"{YEAR}_1"].conflicts} == {
        "teema",
        "arvamuse_tahtaeg",
        "vastutaja",
    }

    second = {c.field: c for c in rows[f"{YEAR}_2"].comparisons}
    assert second["teema"].outcome == Outcome.SAME  # an alternate title, normalised
    assert second["vastutaja"].outcome == Outcome.SAME
    assert second["sisse"].outcome == Outcome.NOT_COMPARABLE
    assert second["arvamuse_tahtaeg"].outcome == Outcome.SOURCE_EMPTY

    third = {c.field: c for c in rows[f"{YEAR}_3"].comparisons}
    assert third["vastutaja"].outcome == Outcome.NOT_COMPARABLE
    assert not rows[f"{YEAR}_3"].conflicts

    report = build_report(administrator)
    assert [r.row.key for r in report.conflicts] == [f"{YEAR}_1"]


def test_only_the_current_lovable_dataset_is_compared(tmp_path, administrator):
    _matter(1, title="Uus")
    run_import(
        write_bundle(
            tmp_path / "b",
            {
                "datasets": datasets("ds-new", stale=("ds-old",)),
                "topics": [
                    topic(f"{YEAR}_1", "Uus", dataset="ds-new"),
                    topic(f"{YEAR}_1", "Vana", dataset="ds-old"),
                ],
            },
        )
    )
    report = build_report(administrator)
    assert [r.row.title for r in report.rows] == ["Uus"]
    assert report.coverage.dataset_boundary == ""


def test_an_undeterminable_current_dataset_compares_nothing_and_says_why(tmp_path, administrator):
    run_import(
        write_bundle(
            tmp_path / "b",
            {
                "datasets": datasets("ds-a", "ds-b"),
                "topics": [topic(f"{YEAR}_1", "A", dataset="ds-a")],
            },
        )
    )
    report = build_report(administrator)
    assert report.rows == []
    assert "Kehtivat Lovable'i andmestikku ei saa üheselt määrata" in (
        report.coverage.dataset_boundary
    )


# ---------------------------------------------------------------------------
# Change between consecutive snapshots
# ---------------------------------------------------------------------------


def test_consecutive_snapshots_are_diffed_by_nr_not_by_lovable_row_id(tmp_path, administrator):
    for number in (1, 2, 3, 4):
        _matter(number, title=f"Teema {number}")
    run_import(
        write_bundle(
            tmp_path / "earlier",
            {
                "datasets": datasets("ds-1"),
                "topics": [
                    topic(f"{YEAR}_1", "Püsiv", dataset="ds-1", hetkeseis="Idee"),
                    topic(f"{YEAR}_2", "Kaob", dataset="ds-1"),
                    topic(f"{YEAR}_4", "Ainult reanumber muutub", dataset="ds-1", row_index=4),
                ],
            },
            exported_at="2031-03-01T09:00:00+02:00",
        )
    )
    run_import(
        write_bundle(
            tmp_path / "later",
            {
                # A fresh upload: new dataset id, new row ids, as Lovable does.
                "datasets": datasets("ds-2", stale=("ds-1",)),
                "topics": [
                    topic(f"{YEAR}_1", "Püsiv", dataset="ds-2", hetkeseis="Riigikogus"),
                    topic(f"{YEAR}_3", "Lisandub", dataset="ds-2"),
                    topic(f"{YEAR}_4", "Ainult reanumber muutub", dataset="ds-2", row_index=9),
                ],
            },
            exported_at="2031-03-08T09:00:00+02:00",
        )
    )
    report = build_report(administrator)

    assert report.previous is not None
    assert [c.key for c in report.diff.added] == [f"{YEAR}_3"]
    assert [c.key for c in report.diff.removed] == [f"{YEAR}_2"]
    assert [c.key for c in report.diff.changed] == [f"{YEAR}_1"]
    (change,) = report.diff.changed[0].fields
    assert (change.field, change.before, change.after) == ("hetkeseis", "Idee", "Riigikogus")
    assert change.mapping_class == MappingClass.NEVER_AUTO


def test_the_first_snapshot_says_there_is_nothing_to_compare_with(tmp_path, administrator):
    run_import(simple_bundle(tmp_path / "b"))
    report = build_report(administrator)
    assert report.previous is None
    assert "Esimene hetktõmmis" in report.coverage.previous_boundary


# ---------------------------------------------------------------------------
# Who may see it
# ---------------------------------------------------------------------------


URL = "/haldus/lovable/"


def test_the_route_is_where_the_contract_puts_it():
    assert reverse("lovable_mirror:preview") == URL


@pytest.mark.parametrize("persona", ["specialist", "department_head", "reader"])
def test_the_preview_is_404_for_everybody_but_the_administrator(client, request, persona):
    client.force_login(request.getfixturevalue(persona))
    assert client.get(URL).status_code == 404


def test_the_preview_needs_a_sign_in(client):
    response = client.get(URL)
    assert response.status_code == 302


def test_the_administrator_sees_the_preview_and_it_offers_no_write(client, administrator, tmp_path):
    _matter(1, title="Näidisteema üks")
    run_import(simple_bundle(tmp_path / "b"))
    client.force_login(administrator)

    response = client.get(URL)

    assert response.status_code == 200
    body = response.content.decode()
    for heading in (
        "Lisandunud pärast eelmist hetktõmmist",
        "Muutunud pärast eelmist hetktõmmist",
        "Seotud teemad",
        "Sidumata",
        "Mitmeti mõistetavad",
        "Vastuolud kanooniliste andmetega",
        "Ulatus ja piirid",
        "Väljade kaardistus",
    ):
        assert heading in body
    assert "<form" not in body.split("<main", 1)[-1].split("</main>", 1)[0]
    assert client.post(URL).status_code == 405


def test_the_preview_renders_with_nothing_imported(client, administrator):
    client.force_login(administrator)
    response = client.get(URL)
    assert "Ühtki Lovable'i hetktõmmist ei ole imporditud." in response.content.decode()


def test_the_report_command_refuses_a_reader_who_may_not_see_the_page(tmp_path, specialist):
    run_import(simple_bundle(tmp_path / "b"))
    with pytest.raises(CommandError, match="ei tohi Lovable'i peeglit vaadata"):
        call_command("lovable_mirror_report", "--as", specialist.upn)


def test_the_report_command_prints_the_lists_for_the_administrator(tmp_path, administrator, capsys):
    _matter(1, title="Näidisteema üks")
    run_import(simple_bundle(tmp_path / "b"))
    call_command("lovable_mirror_report", "--as", administrator.upn, "--json")
    data = json.loads(capsys.readouterr().out)
    assert [r["nr"] for r in data["matched"]] == [f"{YEAR}_1"]
    assert data["unmatched"] == [] and data["ambiguous"] == []


# ---------------------------------------------------------------------------
# Canonical data is untouched
# ---------------------------------------------------------------------------


def _canonical_state() -> dict[str, Any]:
    return {
        "matters": sorted(
            Matter.all_objects.values_list(
                "id",
                "title",
                "owner_id",
                "stage_id",
                "received_date",
                "response_deadline",
                "updated_at",
            )
        ),
        "collaborators": sorted(
            Matter.collaborators.through.objects.values_list("matter_id", "user_id")
        ),
        "submissions": Submission.objects.count(),
        "next_actions": NextAction.objects.count(),
        "documents": Document.objects.count(),
        "document_versions": DocumentVersion.objects.count(),
        "change_events": ChangeEvent.objects.count(),
        "source_references": MatterSourceReference.objects.count(),
        "import_batches": ImportBatch.objects.count(),
    }


def test_an_import_and_a_report_leave_every_canonical_table_untouched(
    tmp_path, client, administrator, owner, other_owner
):
    matter = _matter(1, title="Kanooniline", owner=owner, response_deadline=dt.date(2031, 2, 1))
    matter.collaborators.add(other_owner)
    before = _canonical_state()

    run_import(
        write_bundle(
            tmp_path / "b",
            {
                "datasets": datasets("ds-1"),
                "topics": [
                    topic(
                        f"{YEAR}_1",
                        "Teine pealkiri",
                        vastutaja="Leelo",
                        arvamuse_tahtaeg="2031-03-01",
                        valja="01.03.2031",
                        valja_kuupaev="2031-03-01",
                        jargmiseks="Järgmine samm 10.03",
                        hetkeseis="Valitsuses",
                    ),
                    topic(f"{YEAR}_5", "Uus teema Lovable'is"),
                ],
            },
        )
    )
    build_report(administrator)
    client.force_login(administrator)
    assert client.get(URL).status_code == 200
    call_command("lovable_mirror_report", "--as", administrator.upn)

    assert _canonical_state() == before
    assert SourceSnapshot.objects.count() == 1


def test_the_catalogue_never_lets_a_never_auto_field_be_compared():
    for table_name, mapping in catalogue.all_field_mappings():
        if mapping.name in {"jargmiseks", "valja", "valja_kuupaev", "hetkeseis", "source_data"}:
            assert mapping.mapping_class == MappingClass.NEVER_AUTO, (table_name, mapping.name)


def test_a_direct_bundle_import_with_an_unknown_table_is_refused():
    """Any future reader goes through the same table check, not only the file reader."""
    from app.lovable_mirror.readers import ExportBundle

    bundle = ExportBundle(
        checksum="a" * 64,
        reader="TEST",
        format_version=1,
        data_class="SYNTHETIC",
        source_schema_version="x",
        exported_at=dt.datetime(2031, 1, 1, tzinfo=dt.UTC),
        exported_by="",
        export_method="",
        label="",
        notes="",
        tables={"leiutatud": [{"id": "1"}]},
    )
    with pytest.raises(BundleRefused, match="Tundmatu tabel"):
        import_bundle(bundle)
    assert _nothing_written()


# ---------------------------------------------------------------------------
# Fail-closed visibility of Lovable source text (docs/adr/0157 §5a)
# ---------------------------------------------------------------------------

#: Seeded into every Lovable row the administrator may not see. It must never
#: reach any page, report or JSON for an unauthorised reader.
SENTINEL = "LOVABLE-SENTINEL-q7Zx"

#: References that must never be printed for the administrator either: the
#: RESTRICTED Matter's, and the ones no Matter has.
HIDDEN_KEYS = (f"{YEAR}_7", f"{YEAR}_88", f"{YEAR}_9", f"{YEAR}_66", "mitte-viide")


def _secret(nr: str | None, dataset: str, **extra: Any) -> dict[str, Any]:
    return topic(
        nr,
        f"{SENTINEL} pealkiri",
        dataset=dataset,
        hetkeseis=f"{SENTINEL} hetkeseis",
        jargmiseks=f"{SENTINEL} järgmiseks",
        vastutaja=f"{SENTINEL}",
        **extra,
    )


@pytest.fixture
def visibility_world(tmp_path, specialist):
    """A NORMAL Matter, a RESTRICTED one, and Lovable rows with and without a Matter.

    Two snapshots, so the sentinel also travels through every change list:
    a row about the RESTRICTED Matter changes, a Matter-less row is added and
    another is removed.
    """
    _matter(1, title="Avalik näidisteema", owner=specialist)
    _matter(7, title="Piiratud näidisteema", owner=specialist, visibility=Visibility.RESTRICTED)
    run_import(
        write_bundle(
            tmp_path / "earlier",
            {
                "datasets": datasets("ds-1"),
                "topics": [
                    topic(f"{YEAR}_1", "Avalik näidisteema", dataset="ds-1", hetkeseis="Idee"),
                    _secret(f"{YEAR}_7", "ds-1", sisse="2031-01-01"),
                    _secret(f"{YEAR}_88", "ds-1"),
                    _secret(f"{YEAR}_66", "ds-1"),
                    _secret("mitte-viide", "ds-1"),
                ],
            },
            exported_at="2031-03-01T09:00:00+02:00",
        )
    )
    run_import(
        write_bundle(
            tmp_path / "later",
            {
                "datasets": datasets("ds-2", stale=("ds-1",)),
                "topics": [
                    topic(
                        f"{YEAR}_1", "Avalik näidisteema", dataset="ds-2", hetkeseis="Riigikogus"
                    ),
                    _secret(f"{YEAR}_7", "ds-2", sisse="2031-01-02"),
                    _secret(f"{YEAR}_88", "ds-2"),
                    _secret(f"{YEAR}_9", "ds-2"),
                    _secret("mitte-viide", "ds-2"),
                ],
            },
            exported_at="2031-03-08T09:00:00+02:00",
        )
    )


def _grant_break_glass(user: Any, granted_by: Any) -> None:
    from datetime import timedelta

    from django.utils import timezone

    from app.accounts.models import BreakGlassGrant

    BreakGlassGrant.objects.create(
        user=user,
        granted_by=granted_by,
        reason="Sünteetiline Lovable'i peegli kontroll",
        starts_at=timezone.now() - timedelta(minutes=1),
        expires_at=timezone.now() + timedelta(hours=1),
    )


def _report_json(upn: str, capsys: Any) -> str:
    capsys.readouterr()
    call_command("lovable_mirror_report", "--as", upn, "--json")
    return str(capsys.readouterr().out)


def _report_text(upn: str, capsys: Any) -> str:
    capsys.readouterr()
    call_command("lovable_mirror_report", "--as", upn)
    return str(capsys.readouterr().out)


def _assert_nothing_leaks(text: str) -> None:
    assert SENTINEL not in text
    for key in HIDDEN_KEYS:
        assert key not in text, key


def test_the_administrator_sees_only_rows_of_matters_they_may_read(visibility_world, administrator):
    """Technical administration is not business access (AGENTS.md)."""
    report = build_report(administrator)

    assert [r.row.key for r in report.rows] == [f"{YEAR}_1"]
    assert report.rows[0].matter is not None
    # 7 (RESTRICTED), 88 and 9 (no Matter) and the row without a valid NR.
    assert report.hidden_rows == 4
    assert report.unmatched == [] and report.ambiguous == []
    assert report.coverage.unmatched_withheld is True
    # The NORMAL row's change is listed; the RESTRICTED row's change, the
    # added Matter-less row and the removed one are only counted.
    assert [c.key for c in report.diff.changed] == [f"{YEAR}_1"]
    assert report.diff.added == [] and report.diff.removed == []
    assert report.hidden_changes == 3  # 7 changed, 9 added, 66 removed
    assert report.hidden_rows_message == (
        "4 rida varjatud: puudub õigus vastava teema sisu nägemiseks"
    )


def test_a_restricted_matter_row_is_withheld_not_shown_as_unmatched(
    visibility_world, administrator
):
    report = build_report(administrator)
    assert f"{YEAR}_7" not in {r.row.key for r in report.rows}
    assert f"{YEAR}_7" not in {c.key for c in report.diff.changed}
    assert not any(r.reason == MatchReason.NO_MATTER for r in report.rows)


def test_a_restricted_matter_is_indistinguishable_from_no_matter(visibility_world, administrator):
    """No «restricted match» marker: the report reads the same as if 2031_7 had no Matter."""
    from app.lovable_mirror.management.commands.lovable_mirror_report import as_dict

    with_restricted = as_dict(build_report(administrator))
    Matter.objects.filter(reference_year=YEAR, reference_number=7).update(reference_number=700)
    without_matter = as_dict(build_report(administrator))

    assert with_restricted == without_matter


def test_an_unrestricted_matter_row_is_shown_with_its_comparison(visibility_world, administrator):
    (row,) = build_report(administrator).matched
    assert row.row.key == f"{YEAR}_1"
    assert row.row.title == "Avalik näidisteema"
    assert row.matter.title == "Avalik näidisteema"


def test_a_participant_administrator_sees_the_restricted_row_but_not_unmatched_ones(
    visibility_world, administrator
):
    """Visibility is the Matter's, so participation unlocks exactly that row."""
    Matter.objects.get(reference_year=YEAR, reference_number=7).collaborators.add(administrator)
    report = build_report(administrator)
    assert {r.row.key for r in report.rows} == {f"{YEAR}_1", f"{YEAR}_7"}
    assert report.hidden_rows == 3
    assert report.coverage.unmatched_withheld is True


def test_an_unmatched_row_is_withheld_from_the_administrator(visibility_world, administrator):
    report = build_report(administrator)
    keys = {r.row.key for r in report.rows}
    assert not keys & {f"{YEAR}_88", f"{YEAR}_9", "mitte-viide"}


def test_an_administrator_under_break_glass_sees_every_row(
    visibility_world, administrator, department_head
):
    """The positive control: the sentinel is there to be found when access exists."""
    _grant_break_glass(administrator, department_head)
    report = build_report(administrator)
    assert report.hidden_rows == 0 and report.hidden_changes == 0
    assert report.coverage.unmatched_withheld is False
    assert {r.row.key for r in report.unmatched} == {f"{YEAR}_88", f"{YEAR}_9", "mitte-viide"}
    assert any(SENTINEL in r.row.title for r in report.rows)


@pytest.mark.parametrize(
    ("persona", "hidden"),
    [("reader", 4), ("specialist", 0), ("department_head", 0)],
)
def test_the_selector_follows_matter_visibility_not_the_page_gate(
    visibility_world, request, persona, hidden
):
    """Below the page gate the rule is still visibility: a READER reads NORMAL only."""
    report = build_report(request.getfixturevalue(persona))
    assert report.hidden_rows == hidden
    if hidden:
        _assert_nothing_leaks(repr([r.row for r in report.rows]))


@pytest.mark.parametrize("persona", ["reader", "specialist", "department_head"])
def test_the_page_is_404_for_non_administrators_and_shows_no_source_text(
    visibility_world, client, request, persona
):
    client.force_login(request.getfixturevalue(persona))
    response = client.get(URL)
    assert response.status_code == 404
    _assert_nothing_leaks(response.content.decode())


@pytest.mark.parametrize("persona", ["reader", "specialist", "department_head"])
def test_the_report_command_refuses_every_non_administrator(
    visibility_world, request, persona, capsys
):
    with pytest.raises(CommandError, match="ei tohi Lovable'i peeglit vaadata"):
        call_command("lovable_mirror_report", "--as", request.getfixturevalue(persona).upn)
    _assert_nothing_leaks(capsys.readouterr().out)


def test_no_lovable_source_text_reaches_the_administrator_without_business_access(
    visibility_world, client, administrator, capsys
):
    client.force_login(administrator)
    body = client.get(URL).content.decode()
    _assert_nothing_leaks(body)
    assert "4 rida varjatud: puudub õigus vastava teema sisu nägemiseks" in body
    assert "Sidumata ridu ei kuvata." in body
    assert "Avalik näidisteema" in body  # the readable row is still there

    as_json = _report_json(administrator.upn, capsys)
    _assert_nothing_leaks(as_json)
    data = json.loads(as_json)
    assert data["hidden_rows"] == 4 and data["hidden_changes"] == 3
    assert data["coverage"]["unmatched_withheld"] is True
    assert [r["nr"] for r in data["matched"]] == [f"{YEAR}_1"]

    _assert_nothing_leaks(_report_text(administrator.upn, capsys))


def test_the_sentinel_does_reach_an_administrator_under_break_glass(
    visibility_world, client, administrator, department_head, capsys
):
    """Proves the leak test above would fail if redaction were removed."""
    _grant_break_glass(administrator, department_head)
    client.force_login(administrator)
    assert SENTINEL in client.get(URL).content.decode()
    assert f"{YEAR}_88" in _report_json(administrator.upn, capsys)


# ---------------------------------------------------------------------------
# Reconciliation cost is bounded
# ---------------------------------------------------------------------------


def _bounded_world(tmp_path: Path, size: int, offset: int) -> SourceSnapshot:
    owner = factories.UserFactory(display_name=f"Omanik {offset}")
    for number in range(offset, offset + size):
        _matter(number, title=f"Teema {number}", owner=owner)
    for label, exported in (("a", "2031-03-01"), ("b", "2031-03-08")):
        run_import(
            write_bundle(
                tmp_path / f"{offset}-{label}",
                {
                    "datasets": datasets(f"ds-{offset}-{label}"),
                    "topics": [
                        topic(
                            f"{YEAR}_{n}",
                            f"Teema {n} {label}",
                            dataset=f"ds-{offset}-{label}",
                            vastutaja="Omanik",
                            hetkeseis=label,
                        )
                        for n in range(offset, offset + size)
                    ]
                    + [topic(f"{YEAR}_{offset + 5000}", "Teemata", dataset=f"ds-{offset}-{label}")],
                },
                exported_at=f"{exported}T09:00:00+02:00",
            )
        )
    latest = SourceSnapshot.objects.order_by("-source_exported_at").first()
    assert latest is not None
    return latest


def test_the_report_issues_the_same_number_of_queries_for_5_and_60_rows(tmp_path, administrator):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    small = _bounded_world(tmp_path, 5, 100)
    with CaptureQueriesContext(connection) as few:
        report = build_report(administrator, small)
    assert len(report.matched) == 5

    SourceSnapshot.objects.all().delete()
    large = _bounded_world(tmp_path, 60, 1000)
    with CaptureQueriesContext(connection) as many:
        report = build_report(administrator, large)
    assert len(report.matched) == 60

    assert len(many) == len(few), [q["sql"][:120] for q in many.captured_queries]
