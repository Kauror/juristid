"""QA-11: Dokumendid lists the OneNote pages of *every* readable earlier stage.

«Eelnevate etappide ajalooline materjal» (docs/adr/0208) used to walk only the
linear part of the history plus the direct predecessors, so in a converging
history a grand-predecessor's pages were missing from Dokumendid while
«Seotud materjalid» listed the stage itself. It now reads the same viewer-scoped
graph as «Varasemad etapid» (`historical_predecessors`, docs/adr/0209).

The world, invented throughout (arrows are `superseded_by`)::

    a 2019_1 → b 2020_2 → c 2021_3 → T 2025_10
               d 2018_4 → e 2022_5 → T

Pages: 012 is b's, 013 c's, 015 e's, 016 T's own, 017 is shared by a and d (its
file held for a person's decision), and T links b's page 012 too.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.documents.models import Document
from app.legacy_import import onenote_capture as oc
from app.legacy_import import source_originals
from app.legacy_import.historical_views import historical_summary, predecessor_pages
from app.legacy_import.source_pages import (
    LegacySourcePage,
    LegacySourceResource,
    LegacySourceResourceImport,
    MatterSourcePage,
)
from app.matters.models import Matter
from app.related_materials.selectors import related_materials_for
from tests.test_onenote_capture_import import (
    T1,
    T2,
    _all,
    _apply,
    _file,
    _guid,
    build_capture,
    register_teema,
)

pytestmark = pytest.mark.django_db

KIRI = b"%PDF-1.4 jagatud kiri PA-B"


@pytest.fixture
def enabled(settings):
    settings.ONENOTE_CAPTURE_IMPORT_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    return settings


@pytest.fixture(autouse=True)
def _fresh_package_cache():
    source_originals.clear_cache()
    yield
    source_originals.clear_cache()


def _teema(year: int, number: int, *, is_open: bool = True) -> Matter:
    matter = register_teema(f"{year}_{number}", is_open=is_open)
    Matter.objects.filter(pk=matter.pk).update(reference_year=year, reference_number=number)
    matter.refresh_from_db()
    return matter


def _continues(earlier: Matter, later: Matter | None) -> None:
    Matter.all_objects.filter(pk=earlier.pk).update(superseded_by=later)


def _page(guid_n: int) -> LegacySourcePage:
    return LegacySourcePage.objects.get(page_key=f"p-{_guid(guid_n).lower()}")


def _link(matter: Matter, guid_n: int) -> MatterSourcePage:
    return MatterSourcePage.objects.get(matter=matter, source_page=_page(guid_n))


def _also_link(matter: Matter, guid_n: int) -> MatterSourcePage:
    template = MatterSourcePage.objects.filter(source_page=_page(guid_n)).first()
    assert template is not None
    return MatterSourcePage.objects.create(
        matter=matter,
        source_page=template.source_page,
        relationship_kind=template.relationship_kind,
        match_method=template.match_method,
        match_class=template.match_class,
    )


@pytest.fixture
def world(tmp_path, evidence_root, enabled, settings):
    a, b, c = (
        _teema(2019, 1, is_open=False),
        _teema(2020, 2, is_open=False),
        _teema(2021, 3, is_open=False),
    )
    d, e = _teema(2018, 4, is_open=False), _teema(2022, 5, is_open=False)
    target = _teema(2025, 10)
    pages = [
        {
            "tag": "012",
            "guid": _guid(12),
            "matters": [b],
            "title": "B toimik",
            "files": [_file(1, "b-kiri.pdf", T1, comment="b ajalooline märkus")],
        },
        {
            "tag": "013",
            "guid": _guid(13),
            "matters": [c],
            "title": "C toimik",
            "files": [_file(1, "c-kiri.pdf", T2)],
        },
        {
            "tag": "015",
            "guid": _guid(15),
            "matters": [e],
            "title": "E toimik",
            "files": [_file(1, "e-kiri.pdf", T1)],
        },
        {
            "tag": "016",
            "guid": _guid(16),
            "matters": [target],
            "title": "Praeguse etapi toimik",
            "files": [_file(1, "t-kiri.pdf", T2)],
        },
        {
            "tag": "017",
            "guid": _guid(17),
            "matters": [a, d],
            "title": "A ja D ühine toimik",
            "files": [_file(1, "jagatud.pdf", T1, data=KIRI)],
        },
    ]
    corpus = tmp_path / "corpus"
    root = build_capture(corpus / "onenote-desktop-capture-pab" / "pkg-v2", pages)
    settings.HISTORICAL_SOURCE_ROOT = str(corpus)
    plan = oc.build_plan(oc.load_capture(root))
    assert plan.problems == []
    _apply(plan, _all(plan))
    _apply(plan, _all(plan, include_shared=True))
    _also_link(target, 12)
    return {"a": a, "b": b, "c": c, "d": d, "e": e, "t": target}


def _converge(w: dict[str, Matter]) -> None:
    _continues(w["a"], w["b"])
    _continues(w["b"], w["c"])
    _continues(w["c"], w["t"])
    _continues(w["d"], w["e"])
    _continues(w["e"], w["t"])


def _rows(matter: Matter, user: Any) -> tuple[list[dict[str, Any]], list[MatterSourcePage]]:
    from app.legacy_import.historical_views import visible_links

    own = list(visible_links(user).filter(matter=matter))
    return predecessor_pages(matter, user, own=own), own


def _shape(rows: list[dict[str, Any]]) -> list[tuple[str, list[str]]]:
    return [
        (row["page"].title, [stage.display_reference for stage in row["stages"]]) for row in rows
    ]


def _documents(client: Any, matter: Matter) -> str:
    response = client.get(reverse("matters:matter_documents", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


def _earlier_section(html: str) -> str:
    if 'id="eelnevad-heading"' not in html:
        return ""
    return html.split('id="eelnevad-heading"', 1)[1].split("</section>", 1)[0]


def _evidence() -> dict[str, list[str]]:
    return {
        "Document": sorted(map(repr, Document.objects.values())),
        "LegacySourcePage": sorted(map(repr, LegacySourcePage.objects.values())),
        "LegacySourceResource": sorted(map(repr, LegacySourceResource.objects.values())),
        "LegacySourceResourceImport": sorted(
            map(repr, LegacySourceResourceImport.objects.values())
        ),
        "MatterSourcePage": sorted(map(repr, MatterSourcePage.objects.values())),
    }


@pytest.fixture
def narrow_reader(monkeypatch, specialist):
    """A reader who may *not* read every Teema (as in test_onenote_shared_dossiers)."""
    from app.core.authorization import Scope
    from app.core.authorization import scope_for_user as real

    def scope(user):
        if getattr(user, "pk", None) == specialist.pk:
            return Scope(
                user=user,
                is_authenticated=True,
                sees_restricted_by_role=False,
                break_glass_grant_id=None,
            )
        return real(user)

    for module in (
        "app.matters.models",
        "app.documents.models",
        "app.legacy_import.historical_views",
    ):
        monkeypatch.setattr(f"{module}.scope_for_user", scope)
    return specialist


# -- histories of every shape ---------------------------------------------------------


def test_direct_history_lists_the_predecessors_page(world, specialist):
    _continues(world["c"], world["t"])
    rows, own = _rows(world["t"], specialist)
    assert _shape(rows) == [("C toimik", ["2021_3"])]
    assert [link.shared_with_earlier for link in own if link.source_page == _page(12)] == [[]]


def test_linear_history_lists_every_stage(world, specialist):
    _continues(world["b"], world["c"])
    _continues(world["c"], world["t"])
    rows, own = _rows(world["t"], specialist)
    assert _shape(rows) == [("C toimik", ["2021_3"])]
    # b's page is T's own too: said on T's row, not repeated below.
    (shared,) = [link for link in own if link.source_page == _page(12)]
    assert [m.display_reference for m in shared.shared_with_earlier] == ["2020_2"]


def test_multi_stage_linear_history_reaches_the_first_stage(world, specialist):
    _continues(world["a"], world["b"])
    _continues(world["b"], world["c"])
    _continues(world["c"], world["t"])
    rows, _own = _rows(world["t"], specialist)
    assert _shape(rows) == [("A ja D ühine toimik", ["2019_1"]), ("C toimik", ["2021_3"])]


def test_converging_direct_predecessors_both_lend_their_pages(world, specialist):
    _continues(world["c"], world["t"])
    _continues(world["e"], world["t"])
    rows, _own = _rows(world["t"], specialist)
    assert _shape(rows) == [("C toimik", ["2021_3"]), ("E toimik", ["2022_5"])]


def test_multi_level_convergence_lists_every_branch_once(world, specialist):
    """QA-11 itself: a ← b ← c and d ← e both continuing into T.

    Before the fix only c's and e's pages were listed (the direct predecessors),
    and T's own row for b's page did not say b's dossier holds it too.
    """
    _converge(world)
    rows, own = _rows(world["t"], specialist)
    # Every branch, each page once, attributed to every original Teema holding it.
    assert _shape(rows) == [
        ("A ja D ühine toimik", ["2019_1", "2018_4"]),
        ("C toimik", ["2021_3"]),
        ("E toimik", ["2022_5"]),
    ]
    # The page identity is the source page, read through the earliest stage's link.
    shared_row = rows[0]
    assert shared_row["link"] == _link(world["a"], 17)
    assert shared_row["page"] == _page(17)
    (shared,) = [link for link in own if link.source_page == _page(12)]
    assert [m.display_reference for m in shared.shared_with_earlier] == ["2020_2"]
    # Every stage «Seotud materjalid» lists that has a page is attributed here.
    stages = {item.matter.pk for item in related_materials_for(world["t"], specialist).stages}
    attributed = {m.pk for row in rows for m in row["stages"]} | {
        m.pk for link in own for m in link.shared_with_earlier
    }
    assert attributed == stages  # a, b, c, d, e all have a page
    assert stages == {world[k].pk for k in "abcde"}


def test_the_dokumendid_tab_shows_every_branch_and_counts_none(world, client, specialist):
    _converge(world)
    client.force_login(specialist)
    before = _evidence()
    documents = Document.objects.count()
    held = LegacySourceResourceImport.objects.filter(document__isnull=True).count()
    html = _documents(client, world["t"])
    section = _earlier_section(html)
    assert "A ja D ühine toimik" in section and "2019_1, 2018_4" in section
    assert "C toimik" in section and "E toimik" in section
    assert "B toimik" not in section  # T's own page: one row, above
    assert "ka eelneva teema 2020_2 toimikus" in html
    assert '<span class="docsection__count">3 lehte</span>' in section
    # T's own figures: two pages, two files; nothing of the earlier stages counted.
    summary = historical_summary(world["t"], specialist)
    assert summary["page_count"] == 2 and summary["file_count"] == 2
    assert "jagatud.pdf" not in html.split('class="table doctable"', 1)[-1].split("</table>")[0]
    # Nothing written, nothing promoted: the held shared file stays unassigned.
    assert _evidence() == before
    assert Document.objects.count() == documents
    assert LegacySourceResourceImport.objects.filter(document__isnull=True).count() == held
    assert not Document.objects.filter(matter=world["t"], title__icontains="jagatud").exists()
    for link in MatterSourcePage.objects.filter(source_page=_page(17)):
        assert all(r.document_id is None for r in link.resource_imports.all())


def test_each_listed_page_opens_and_its_held_original_downloads_as_before(
    world, client, specialist
):
    _converge(world)
    client.force_login(specialist)
    rows, _own = _rows(world["t"], specialist)
    for row in rows:
        page = client.get(reverse("legacy_import:source_page", kwargs={"pk": row["link"].pk}))
        assert page.status_code == 200
    shared = rows[0]["link"]
    resource = LegacySourceResource.objects.get(
        source_page=_page(17), original_filename="jagatud.pdf"
    )
    response = client.get(
        reverse(
            "legacy_import:source_original", kwargs={"pk": shared.pk, "resource_pk": resource.pk}
        )
    )
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == KIRI
    # The historical date and the attached comment are the earlier stage's, unchanged.
    b_file = LegacySourceResource.objects.get(source_page=_page(12), original_filename="b-kiri.pdf")
    assert b_file.source_added_at == datetime(2021, 8, 30, 8, 48, 10, tzinfo=UTC)
    b_document = Document.objects.get(title="b-kiri.pdf")
    assert b_document.matter_id == world["b"].pk
    assert "b ajalooline märkus" in b_document.provenance_note
    b_view = client.get(
        reverse("legacy_import:source_page", kwargs={"pk": _link(world["b"], 12).pk})
    )
    assert b_view.status_code == 200 and "b-kiri.pdf" in b_view.content.decode()


# -- what the walk must not do --------------------------------------------------------


def test_a_restricted_middle_stage_lends_nothing_and_hides_what_is_behind_it(
    world, client, narrow_reader
):
    _converge(world)
    Matter.objects.filter(pk=world["b"].pk).update(visibility=Visibility.RESTRICTED)
    rows, own = _rows(world["t"], narrow_reader)
    # b is unreadable, so a (behind it) is not reached: page 017 comes through d alone.
    assert _shape(rows) == [
        ("A ja D ühine toimik", ["2018_4"]),
        ("C toimik", ["2021_3"]),
        ("E toimik", ["2022_5"]),
    ]
    assert rows[0]["link"] == _link(world["d"], 17)
    (shared,) = [link for link in own if link.source_page == _page(12)]
    assert shared.shared_with_earlier == []
    client.force_login(narrow_reader)
    html = _documents(client, world["t"])
    assert "2020_2" not in html and "2019_1" not in html
    # The restricted stage's own links stay closed, exactly as before.
    url = reverse("legacy_import:source_page", kwargs={"pk": _link(world["b"], 12).pk})
    assert client.get(url).status_code == 404
    url = reverse("legacy_import:source_page", kwargs={"pk": _link(world["d"], 17).pk})
    assert client.get(url).status_code == 200


def test_a_deleted_stage_ends_its_branch(world, specialist):
    _converge(world)
    Matter.all_objects.filter(pk=world["c"].pk).update(deleted_at=timezone.now())
    rows, own = _rows(world["t"], specialist)
    assert _shape(rows) == [("A ja D ühine toimik", ["2018_4"]), ("E toimik", ["2022_5"])]
    (shared,) = [link for link in own if link.source_page == _page(12)]
    assert shared.shared_with_earlier == []


def test_a_cycle_through_the_viewed_teema_ends_safely(world, specialist):
    _converge(world)
    _continues(world["t"], world["a"])  # T → a → b → c → T
    rows, _own = _rows(world["t"], specialist)
    assert _shape(rows) == [
        ("A ja D ühine toimik", ["2019_1", "2018_4"]),
        ("C toimik", ["2021_3"]),
        ("E toimik", ["2022_5"]),
    ]
    assert "Praeguse etapi toimik" not in [row["page"].title for row in rows]


def test_a_stage_without_pages_lends_nothing(world, specialist):
    blank = _teema(2017, 9, is_open=False)
    _continues(blank, world["t"])
    rows, _own = _rows(world["t"], specialist)
    assert rows == []
