"""One OneNote section at a time (owner brief §10; live-sync round 2026-11, agent D).

A section inventory matches pages only by exact identity, lists containers and
shared dossiers as what they are, creates no Teema and invents no register
number. Invented sections, pages and Teemad only.
"""

from __future__ import annotations

import json

import pytest
from django.core.management import CommandError, call_command

from app.legacy_import import onenote_capture as oc
from app.legacy_import.onenote_coverage_models import CoverageScope, OneNoteCoverageMark
from app.legacy_import.source_pages import LegacySourcePage
from app.matters.models import Matter
from tests.test_onenote_capture_changes import FIRST, T1, build, f, run
from tests.test_onenote_capture_import import _rewrite_manifest, register_teema, stored_link

pytestmark = pytest.mark.django_db

SECTION = "Näidissektsioon"
G_EXACT = "0000CC01-0000-4000-8000-0000000000C1"
G_PARENT = "0000CC02-0000-4000-8000-0000000000C2"
G_CHILD = "0000CC03-0000-4000-8000-0000000000C3"
G_SHARED = "0000CC04-0000-4000-8000-0000000000C4"
G_ORPHAN = "0000CC05-0000-4000-8000-0000000000C5"
G_FORGOT = "0000CC06-0000-4000-8000-0000000000C6"
G_ELSEWHERE = "0000CC07-0000-4000-8000-0000000000C7"


@pytest.fixture
def enabled(settings):
    settings.ONENOTE_CAPTURE_IMPORT_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    return settings


@pytest.fixture
def section(tmp_path):
    exact, forgot = register_teema("2026_71"), register_teema("2026_72")
    s1, s2 = register_teema("2026_73"), register_teema("2026_74")
    for teema, guid in ((forgot, G_FORGOT),):
        state = teema.lovable_sync_state
        state.applied = {"onenote_link": stored_link(guid)}
        state.save(update_fields=["applied"])
    page = {"section": SECTION}
    root = build(
        tmp_path / "s",
        "cap-s",
        [
            {**page, "tag": "001", "guid": G_EXACT, "matters": [exact], "files": FIRST[:1]},
            {**page, "tag": "002", "guid": G_PARENT, "matters": [], "files": [], "text": "Kaust"},
            {
                **page,
                "tag": "003",
                "guid": G_CHILD,
                "matters": [],
                "files": [f("{K1}", "k.pdf", T1)],
            },
            {**page, "tag": "004", "guid": G_SHARED, "matters": [s1, s2], "files": FIRST[1:2]},
            {
                **page,
                "tag": "005",
                "guid": G_ORPHAN,
                "matters": [],
                "files": [f("{O1}", "o.pdf", T1)],
            },
            # The register names this page, but the capture's manifest does not say so.
            {**page, "tag": "006", "guid": G_FORGOT, "matters": [], "files": []},
            {"tag": "007", "guid": G_ELSEWHERE, "matters": [], "files": [], "section": "Muu"},
        ],
    )

    def parent(manifest):
        by_tag = {p["tag"]: p for p in manifest["pages"]}
        by_tag["003"]["parent_object_id"] = by_tag["002"]["page_object_id"]
        by_tag["003"]["level"] = 2

    _rewrite_manifest(root, parent)
    return {"root": root, "exact": exact, "forgot": forgot, "shared": (s1, s2)}


def _rows(result):
    return {row["page_guid"]: row for row in result["rows"]}


def test_a_section_plan_matches_by_exact_identity_and_lists_the_rest(section):
    before = Matter.objects.count()
    result = oc.section_plan(oc.load_capture(section["root"]), SECTION)
    rows = _rows(result)
    assert result["pages"] == 6 and G_ELSEWHERE not in rows  # one section only
    assert rows[G_EXACT]["match"] == oc.SECTION_EXACT and rows[G_EXACT]["role"] == "PAGE"
    assert rows[G_PARENT]["role"] == "CONTAINER_CANDIDATE"
    assert rows[G_SHARED]["role"] == "SHARED_DOSSIER"
    assert rows[G_ORPHAN]["match"] == oc.SECTION_NO_MATCH
    assert rows[G_FORGOT]["match"] == oc.SECTION_REGISTER_NOT_IN_CAPTURE
    assert rows[G_FORGOT]["teemad_register"] == [str(section["forgot"].pk)]
    exceptions = {e["page_guid"]: e for e in result["exceptions"]}
    assert set(exceptions) == {G_PARENT, G_CHILD, G_SHARED, G_ORPHAN, G_FORGOT}
    assert exceptions[G_PARENT]["action"] == "NOT_A_MATTER"
    assert exceptions[G_ORPHAN]["action"] == "AWAITING_ASSOCIATION"
    assert result["teemad_created"] == 0 and Matter.objects.count() == before


def test_applying_a_section_capture_writes_only_the_exactly_linked_pages(section, enabled):
    before = Matter.objects.count()
    run(section["root"])
    written = set(LegacySourcePage.objects.values_list("page_key", flat=True))
    assert written == {f"p-{G_EXACT.lower()}", f"p-{G_SHARED.lower()}"}
    assert Matter.objects.count() == before


def test_a_section_the_capture_does_not_hold_is_refused(section):
    with pytest.raises(oc.CaptureError, match="the capture holds no page of section"):
        oc.section_plan(oc.load_capture(section["root"]), "Puuduv")


def test_the_section_plan_command_prints_counts_only_and_can_record_the_section(section, tmp_path):
    out = tmp_path / "plan.json"

    class Sink:
        text = ""

        def write(self, value):
            Sink.text += value

        def flush(self):
            pass

    call_command(
        "onenote_capture_import",
        "section-plan",
        "--capture",
        str(section["root"]),
        "--section",
        SECTION,
        "--record",
        "--out",
        str(out),
        stdout=Sink(),
    )
    printed = json.loads(Sink.text.splitlines()[0])
    assert "rows" not in printed and "exceptions" not in printed and "section" not in printed
    assert printed["pages"] == 6
    assert "title" not in Sink.text
    assert json.loads(out.read_text(encoding="utf-8"))["rows"]
    mark = OneNoteCoverageMark.objects.get(scope=CoverageScope.SECTION)
    assert mark.section == SECTION and mark.state == "VERIFIED"
    with pytest.raises(CommandError, match="name the section"):
        call_command("onenote_capture_import", "section-plan", "--capture", str(section["root"]))
