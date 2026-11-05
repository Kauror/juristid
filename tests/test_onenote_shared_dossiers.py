"""A shared OneNote page read as one dossier (docs/adr/0208), against invented captures only.

A page linked to several Teemad has its files held for a person's decision
(docs/adr/0205 §4). These tests fix what a reader is told about such a page —
that it is whole and readable, that the files' Teema is not decided, and how the
Teemad relate by `superseded_by` — the narrow read-only download of a held
original from its sealed capture, and the earlier stages' pages on the
Dokumendid tab. Every page, file and Teema here is invented.
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.core.enums import Visibility
from app.documents.models import Document
from app.legacy_import import onenote_capture as oc
from app.legacy_import import source_originals
from app.legacy_import.historical_views import historical_summary
from app.legacy_import.models import ImportBatch
from app.legacy_import.shared_dossiers import (
    COMPLEX,
    NONE,
    ONE_CHAIN,
    PARTIAL_CHAIN,
    shared_dossier,
)
from app.legacy_import.source_pages import (
    LegacySourcePage,
    LegacySourceResource,
    LegacySourceResourceImport,
    MatterSourcePage,
    ResourceImportState,
)
from app.matters.models import Matter
from tests import factories
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
    Matter.objects.filter(pk=earlier.pk).update(superseded_by=later)


@pytest.fixture
def world(tmp_path, evidence_root, enabled, settings):
    """Pages 001 (2025_140 + 2025_196) and 002 (2025_200 + 2025_201) are shared.

    Page 004 is 2025_100's alone, so its file is imported. A Teema's stored
    Lovable link names one page, so a second page of a Teema is linked by hand
    where a test needs one (`_also_link`).
    """
    a, b = _teema(2025, 140), _teema(2025, 196)
    c, d = _teema(2025, 200), _teema(2025, 201, is_open=False)
    z = _teema(2025, 100)
    pages = [
        {
            "tag": "001",
            "guid": _guid(1),
            "matters": [a, b],
            "title": "Ühine toimik",
            "files": [
                _file(1, "kiri.pdf", T1, data=b"%PDF-1.4 kiri"),
                _file(2, "lõuendil.pdf", None, floating=True, modified=T2),
                _file(3, "tühi.txt", T1, data=b""),
            ],
        },
        {
            "tag": "002",
            "guid": _guid(2),
            "matters": [c, d],
            "title": "Kahe teema leht",
            "files": [_file(1, "jagatud.pdf", T2)],
        },
        {
            "tag": "004",
            "guid": _guid(4),
            "matters": [z],
            "title": "Eelmise etapi leht",
            "files": [_file(1, "vana.pdf", T1)],
        },
    ]
    corpus = tmp_path / "corpus"
    root = build_capture(corpus / "onenote-desktop-capture-x" / "pkg-v2", pages)
    settings.HISTORICAL_SOURCE_ROOT = str(corpus)
    plan = oc.build_plan(oc.load_capture(root))
    _apply(plan, _all(plan))
    _apply(plan, _all(plan, include_shared=True))
    return {"root": root, "corpus": corpus, "a": a, "b": b, "c": c, "d": d, "z": z}


def _page(guid_n: int) -> LegacySourcePage:
    return LegacySourcePage.objects.get(page_key=f"p-{_guid(guid_n).lower()}")


def _link(matter: Matter, guid_n: int) -> MatterSourcePage:
    return MatterSourcePage.objects.get(matter=matter, source_page=_page(guid_n))


def _also_link(matter: Matter, guid_n: int) -> MatterSourcePage:
    template = MatterSourcePage.objects.filter(source_page=_page(guid_n)).first()
    return MatterSourcePage.objects.create(
        matter=matter,
        source_page=template.source_page,
        relationship_kind=template.relationship_kind,
        match_method=template.match_method,
        match_class=template.match_class,
    )


def _resource(guid_n: int, name: str) -> LegacySourceResource:
    return LegacySourceResource.objects.get(source_page=_page(guid_n), original_filename=name)


@pytest.fixture
def operator(monkeypatch, specialist):
    """A reader who may *not* read every Teema.

    Since docs/adr/0181 every reader reads all or nothing, so no account today
    has a narrower view. The visibility builders still honour a hand-built
    participation scope (NORMAL, plus RESTRICTED work one owns or collaborates
    on), and that is the reader these tests need: whatever narrower reader the
    future brings, a Teema it may not read must be counted, never named.
    """
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


def _restricted() -> Matter:
    matter = factories.MatterFactory(
        owner=factories.UserFactory(), visibility=Visibility.RESTRICTED, title="SALAJANE-TEEMA-77"
    )
    return matter


def _refs(dossier) -> list[list[str]]:
    return [[item.matter.display_reference for item in chain] for chain in dossier.chains]


# -- 1. how the Teemad of one page relate ----------------------------------------------


def test_a_shared_page_whose_teemad_are_one_continuation_is_one_chain(world, specialist):
    _continues(world["a"], world["b"])
    dossier = shared_dossier(_page(1), specialist)
    assert dossier.kind == ONE_CHAIN and dossier.shared and not dossier.ambiguous
    assert _refs(dossier) == [["2025_140", "2025_196"]]
    assert [dossier.relation_of(t) for t in dossier.teemad] == ["Etapp 1/2", "Etapp 2/2"]


def test_a_stage_between_them_that_is_not_on_the_page_still_makes_one_chain(world, specialist):
    middle = _teema(2025, 150)
    _continues(world["a"], middle)
    _continues(middle, world["b"])
    assert shared_dossier(_page(1), specialist).kind == ONE_CHAIN


def test_teemad_without_any_continuation_edge_relate_by_none(world, specialist):
    dossier = shared_dossier(_page(2), specialist)
    assert dossier.kind == NONE and dossier.ambiguous and not dossier.chains
    assert {dossier.relation_of(t) for t in dossier.teemad} == {
        "Jätku seos teiste selle lehe teemadega puudub"
    }


def test_a_chain_covering_some_of_the_teemad_is_partial(world, specialist):
    e = _teema(2025, 300)
    _also_link(e, 2)
    _continues(world["c"], world["d"])
    dossier = shared_dossier(_page(2), specialist)
    assert dossier.kind == PARTIAL_CHAIN
    assert _refs(dossier) == [["2025_200", "2025_201"]]
    assert [t.matter.pk for t in dossier.unchained] == [e.pk]


def test_converging_teemad_are_complex_not_a_chain(world, specialist):
    later = _teema(2025, 400)  # both continue as one Teema that is not on the page
    _continues(world["c"], later)
    _continues(world["d"], later)
    dossier = shared_dossier(_page(2), specialist)
    assert dossier.kind == COMPLEX and not dossier.chains
    assert {dossier.relation_of(t) for t in dossier.teemad} == {"Jätku seos ei ole lineaarne"}


def test_a_cycle_ends_the_walk_and_is_complex(world, specialist):
    _continues(world["c"], world["d"])
    _continues(world["d"], world["c"])
    dossier = shared_dossier(_page(2), specialist)
    assert dossier.kind == COMPLEX and dossier.cyclic


def test_a_hidden_teema_is_counted_never_named_and_a_chain_no_longer_covers_all(world, operator):
    hidden = _restricted()
    assert not Matter.objects.visible_to(operator).filter(pk=hidden.pk).exists()
    _also_link(hidden, 1)
    _continues(world["a"], world["b"])
    dossier = shared_dossier(_page(1), operator)
    assert dossier.hidden == 1 and dossier.total == 3
    assert hidden.pk not in {t.matter.pk for t in dossier.teemad}
    # The readable two are one chain, but «every linked Teema» cannot be shown.
    assert dossier.kind == PARTIAL_CHAIN and _refs(dossier) == [["2025_140", "2025_196"]]


def test_a_continuation_into_a_hidden_teema_grants_nothing(world, operator):
    hidden = _restricted()
    _also_link(hidden, 2)
    _continues(world["c"], hidden)
    _continues(hidden, world["d"])
    dossier = shared_dossier(_page(2), operator)
    # The walk stops at the step this reader may not read: no chain through it.
    assert dossier.kind == NONE and dossier.hidden == 1


def test_a_deleted_teemas_link_is_not_part_of_the_dossier(world, specialist):
    Matter.objects.filter(pk=world["d"].pk).update(deleted_at=timezone.now())
    dossier = shared_dossier(_page(2), specialist)
    assert dossier.total == 1 and dossier.hidden == 0 and not dossier.shared


# -- 2 and 5. what the page says ---------------------------------------------------------


def _view(client, link):
    return client.get(reverse("legacy_import:source_page", kwargs={"pk": link.pk}))


def test_the_page_of_one_chain_names_its_stages_and_both_facts(world, client, specialist):
    _continues(world["a"], world["b"])
    client.force_login(specialist)
    html = _view(client, _link(world["b"], 1)).content.decode()
    assert "Ühine toimik · 2 teemat" in html
    assert "Leht on tervikuna hõivatud ja siin täielikult loetav." in html
    flat = " ".join(html.split())
    assert "Milline fail millisele teemale kuulub, ei ole otsustatud" in flat
    other = reverse("legacy_import:source_page", kwargs={"pk": _link(world["a"], 1).pk})
    assert "Sama menetluse etapid:" in html
    assert f'<a href="{other}">2025_140</a> → ' in html
    assert '<strong aria-current="page">2025_196</strong>' in html
    # One chain names every Teema: neither the old list nor the overview repeats them.
    assert "Sama leht on seotud ka" not in html
    assert "Lehe teemad ja failid" not in html


def test_an_ambiguous_page_gets_a_read_only_overview_with_onenote_days(world, client, specialist):
    client.force_login(specialist)
    html = _view(client, _link(world["a"], 1)).content.decode()
    assert "Teemade vahel ei ole registris jätku seost." in html
    overview = html.split('id="ulevaade-heading"', 1)[1].split("</section>", 1)[0]
    assert "2025_140" in overview and "2025_196" in overview
    assert "Avatud" in overview
    assert "Jätku seos teiste selle lehe teemadega puudub" in overview
    assert "kiri.pdf" in overview and "30.8.2021" in overview
    assert "lõuendil.pdf" in overview and "Lisamise kuupäev teadmata" in overview
    # Read-only: nothing to press, nothing to post.
    assert "<form" not in overview and "<button" not in overview
    closed = _view(client, _link(world["c"], 2)).content.decode()
    assert "Suletud" in closed.split('id="ulevaade-heading"', 1)[1]


def test_a_hidden_teema_on_the_page_is_counted_and_not_named(world, client, operator):
    hidden = _restricted()
    _also_link(hidden, 2)
    client.force_login(operator)
    html = _view(client, _link(world["c"], 2)).content.decode()
    assert "SALAJANE-TEEMA-77" not in html
    assert hidden.display_reference not in html
    assert "Lisaks on leht seotud 1 teemaga, mida selles kasutajavaates ei kuvata." in html
    assert "Ühine toimik · 3 teemat" in html


def test_held_files_offer_their_original_and_imported_ones_keep_their_document(
    world, client, specialist
):
    client.force_login(specialist)
    link = _link(world["a"], 1)
    html = _view(client, link).content.decode()
    kiri = _resource(1, "kiri.pdf")
    url = reverse("legacy_import:source_original", kwargs={"pk": link.pk, "resource_pk": kiri.pk})
    assert f'<a href="{url}" download>Laadi alla (allikas)</a>' in html
    # The empty file has nothing to download.
    assert html.count("Laadi alla (allikas)") == 2
    single = _view(client, _link(world["z"], 4)).content.decode()
    assert "Laadi alla (allikas)" not in single
    assert "Päritolu" in single  # the imported file still links its Document


# -- 3. the held original ----------------------------------------------------------------


def _original_url(link, resource):
    return reverse(
        "legacy_import:source_original", kwargs={"pk": link.pk, "resource_pk": resource.pk}
    )


def _audits():
    return list(
        SecurityAuditEvent.objects.filter(
            event_type=SecurityEventType.DOCUMENT_DOWNLOADED,
            detail__source="onenote_source_original",
        )
    )


def _body(response) -> bytes:
    return b"".join(response.streaming_content) if response.streaming else response.content


def test_a_reader_downloads_the_proved_original_as_an_attachment(world, client, specialist):
    client.force_login(specialist)
    link, kiri = _link(world["a"], 1), _resource(1, "kiri.pdf")
    response = client.get(_original_url(link, kiri))
    assert response.status_code == 200
    assert _body(response) == b"%PDF-1.4 kiri"
    assert response["Content-Type"] == "application/octet-stream"
    assert response["Content-Disposition"].startswith('attachment; filename="kiri.pdf"')
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Cache-Control"] == "private, no-store"
    (row,) = _audits()
    assert row.succeeded and row.actor_id == specialist.pk
    assert row.detail["matter"] == str(world["a"].pk)
    assert row.detail["sha256"] == kiri.sha256 == hashlib.sha256(b"%PDF-1.4 kiri").hexdigest()
    assert row.detail["outcome"] == "served" and row.detail["disposition"] == "attachment"
    # Nothing but the audit row was written.
    assert not Document.objects.filter(matter__in=[world["a"], world["b"]]).exists()
    assert LegacySourceResourceImport.objects.get(matter_source_page=link, resource=kiri).state == (
        ResourceImportState.SKIPPED
    )


def test_a_floating_file_has_its_original_too(world, client, specialist):
    client.force_login(specialist)
    response = client.get(_original_url(_link(world["b"], 1), _resource(1, "lõuendil.pdf")))
    assert response.status_code == 200
    assert "filename*=UTF-8''l%C3%B5uendil.pdf" in response["Content-Disposition"]


def test_an_outsider_or_anonymous_visitor_gets_nothing(world, client, administrator):
    url = _original_url(_link(world["a"], 1), _resource(1, "kiri.pdf"))
    assert client.get(url).status_code == 302  # to sign-in
    client.force_login(administrator)  # an administrator without a grant reads none (0181)
    assert client.get(url).status_code == 404
    assert not _audits()


def test_a_restricted_teemas_link_is_a_404_for_a_reader_outside_it(world, client, operator):
    hidden = _restricted()
    hidden_link = _also_link(hidden, 1)
    LegacySourceResourceImport.objects.create(
        matter_source_page=hidden_link,
        resource=_resource(1, "kiri.pdf"),
        import_batch=ImportBatch.objects.order_by("-started_at").first(),
        state=ResourceImportState.SKIPPED,
        error_code=oc.HOLD_SHARED_PAGE,
    )
    client.force_login(operator)
    assert client.get(_original_url(hidden_link, _resource(1, "kiri.pdf"))).status_code == 404
    assert not _audits()


def test_a_link_and_a_resource_of_different_pages_are_a_404(world, client, specialist):
    client.force_login(specialist)
    # A shared page's held file, asked for through another page's link.
    assert (
        client.get(_original_url(_link(world["z"], 4), _resource(1, "kiri.pdf"))).status_code == 404
    )
    assert (
        client.get(_original_url(_link(world["a"], 1), _resource(2, "jagatud.pdf"))).status_code
        == 404
    )
    assert not _audits()


def test_an_imported_an_empty_or_a_not_held_resource_is_a_404(world, client, specialist):
    client.force_login(specialist)
    imported = _resource(4, "vana.pdf")
    assert client.get(_original_url(_link(world["z"], 4), imported)).status_code == 404
    assert (
        client.get(_original_url(_link(world["a"], 1), _resource(1, "tühi.txt"))).status_code == 404
    )
    # A file over the evidence limit is held too, but serving it is not decided.
    LegacySourceResourceImport.objects.filter(resource=_resource(1, "kiri.pdf")).update(
        error_code=oc.HOLD_TOO_LARGE
    )
    assert (
        client.get(_original_url(_link(world["a"], 1), _resource(1, "kiri.pdf"))).status_code == 404
    )
    assert not _audits()


def test_a_resource_another_teema_holds_as_a_stricter_document_is_withheld(
    world, client, operator, capture_evidence
):
    kiri = _resource(1, "kiri.pdf")
    owner = factories.UserFactory()
    version = capture_evidence(
        world["b"],
        b"%PDF-1.4 kiri",
        "kiri.pdf",
        "application/pdf",
        visibility_override=Visibility.RESTRICTED,
        created_by=owner,
    )
    assert not Document.objects.visible_to(operator).filter(pk=version.document_id).exists()
    LegacySourceResourceImport.objects.filter(
        matter_source_page=_link(world["b"], 1), resource=kiri
    ).update(document=version.document, document_version=version, state="IMPORTED")
    client.force_login(operator)
    link = _link(world["a"], 1)
    assert client.get(_original_url(link, kiri)).status_code == 404
    html = _view(client, link).content.decode()
    assert _original_url(link, kiri) not in html


@pytest.mark.parametrize(
    ("damage", "outcome"),
    [
        ("no_root", "package_missing"),
        ("empty_root", "package_missing"),
        ("other_manifest", "package_missing"),
        ("other_capture", "package_missing"),
        ("missing_blob", "blob_missing"),
        ("longer_blob", "size_mismatch"),
        ("changed_blob", "hash_mismatch"),
        ("unsealed_blob", "blob_not_sealed"),
        ("traversal_digest", "resource_digest_invalid"),
    ],
)
def test_anything_unproved_is_a_404_and_a_failed_audit_row(
    world, client, specialist, settings, tmp_path, damage, outcome
):
    link, kiri = _link(world["a"], 1), _resource(1, "kiri.pdf")
    blob = world["root"] / "blobs" / kiri.sha256
    if damage == "no_root":
        settings.HISTORICAL_SOURCE_ROOT = ""
    elif damage == "empty_root":
        (tmp_path / "elsewhere").mkdir()
        settings.HISTORICAL_SOURCE_ROOT = str(tmp_path / "elsewhere")
    elif damage == "other_manifest":
        ImportBatch.objects.update(source_snapshot_sha256="0" * 64)
    elif damage == "other_capture":
        seal_path = world["root"] / "SEAL.json"
        seal = json.loads(seal_path.read_text(encoding="utf-8"))
        seal_path.write_text(json.dumps({**seal, "capture_id": "another-capture"}))
    elif damage == "missing_blob":
        blob.unlink()
    elif damage == "longer_blob":
        blob.write_bytes(b"%PDF-1.4 kiri, aga pikem")
    elif damage == "changed_blob":
        blob.write_bytes(b"%PDF-1.4 KIRI")
    elif damage == "unsealed_blob":
        sums = world["root"] / "SHA256SUMS"
        sums.write_text(
            "".join(
                line + "\n"
                for line in sums.read_text(encoding="utf-8").splitlines()
                if kiri.sha256 not in line
            )
        )
        seal_path = world["root"] / "SEAL.json"
        seal = json.loads(seal_path.read_text(encoding="utf-8"))
        seal["sha256sums_sha256"] = hashlib.sha256(sums.read_bytes()).hexdigest()
        seal_path.write_text(json.dumps(seal))
    elif damage == "traversal_digest":
        LegacySourceResource.objects.filter(pk=kiri.pk).update(sha256="../../manifest.json")
    client.force_login(specialist)
    response = client.get(_original_url(link, kiri))
    assert response.status_code == 404
    body = _body(response).decode("utf-8", errors="replace")
    assert str(tmp_path) not in body and "blobs" not in body and kiri.sha256 not in body
    (row,) = _audits()
    assert not row.succeeded and row.detail["outcome"] == outcome
    assert row.detail["matter"] == str(world["a"].pk)


def test_a_seal_that_names_its_capture_is_matched_by_it(world, client, specialist):
    seal_path = world["root"] / "SEAL.json"
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    seal_path.write_text(json.dumps({**seal, "capture_id": "synthetic-capture"}))
    client.force_login(specialist)
    response = client.get(_original_url(_link(world["a"], 1), _resource(1, "kiri.pdf")))
    assert response.status_code == 200


def test_a_symbolic_link_is_never_followed(world, client, specialist, tmp_path):
    kiri = _resource(1, "kiri.pdf")
    blob = world["root"] / "blobs" / kiri.sha256
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4 kiri")
    blob.unlink()
    try:
        os.symlink(outside, blob)
    except (OSError, NotImplementedError):
        pytest.skip("this filesystem cannot make a symbolic link")
    client.force_login(specialist)
    assert client.get(_original_url(_link(world["a"], 1), kiri)).status_code == 404
    (row,) = _audits()
    assert row.detail["outcome"] == "blob_missing"


def test_the_route_takes_ids_only():
    from django.urls import NoReverseMatch

    with pytest.raises(NoReverseMatch):
        reverse("legacy_import:source_original", kwargs={"pk": "x", "resource_pk": "../blobs"})


def test_the_scan_is_bounded_in_depth(world, client, specialist, settings, tmp_path):
    deep = tmp_path / "deep" / "a" / "b" / "c"
    deep.mkdir(parents=True)
    os.replace(world["root"], deep / "pkg")
    settings.HISTORICAL_SOURCE_ROOT = str(tmp_path / "deep")
    client.force_login(specialist)
    # SEAL.json four levels down is past SCAN_DEPTH: not found, not served.
    assert (
        client.get(_original_url(_link(world["a"], 1), _resource(1, "kiri.pdf"))).status_code == 404
    )
    (row,) = _audits()
    assert row.detail["outcome"] == "package_missing"


# -- 4. the earlier stages' pages on Dokumendid -------------------------------------------


def _documents(client, matter):
    return client.get(
        reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    ).content.decode()


def test_a_continuation_lists_its_predecessors_own_pages_once_and_counts_none(
    world, client, specialist
):
    earlier_page = _also_link(world["a"], 4)
    _continues(world["a"], world["b"])
    client.force_login(specialist)
    html = _documents(client, world["b"])
    section = html.split('id="eelnevad-heading"', 1)[1].split("</section>", 1)[0]
    # 2025_140's own page, through 2025_140's link (its scope, not this Teema's).
    earlier = reverse("legacy_import:source_page", kwargs={"pk": earlier_page.pk})
    assert f'href="{earlier}"' in section and "Eelmise etapi leht" in section
    # The chip is the predecessor's link's own record: nothing imported for 2025_140.
    assert "2025_140" in section and "1 fail on veel importimata" in section
    # The page both link is not repeated; this Teema's own row says it is shared.
    assert "Ühine toimik" not in section
    assert "ka eelneva teema 2025_140 toimikus" in html
    # This Teema's own figures are unchanged: page 001, three files.
    assert "1 lehte · 3 faili" in html
    summary = historical_summary(world["b"], specialist)
    assert summary["page_count"] == 1 and summary["file_count"] == 3
    assert [row["page"].title for row in summary["predecessor_pages"]] == ["Eelmise etapi leht"]


def test_without_a_continuation_there_is_no_earlier_section(world, client, specialist):
    client.force_login(specialist)
    html = _documents(client, world["b"])
    assert "Eelnevate etappide ajalooline materjal" not in html
    assert "ka eelneva teema" not in html


def test_a_hidden_predecessor_lends_nothing(world, client, operator):
    hidden = _restricted()
    _also_link(hidden, 4)
    _continues(hidden, world["b"])
    client.force_login(operator)
    html = _documents(client, world["b"])
    assert "Eelnevate etappide ajalooline materjal" not in html
    assert "SALAJANE-TEEMA-77" not in html


def test_a_teema_with_only_earlier_pages_shows_only_that_section(world, client, specialist):
    later = _teema(2026, 12)
    _also_link(world["a"], 4)
    _continues(world["a"], later)
    client.force_login(specialist)
    html = _documents(client, later)
    assert "Eelnevate etappide ajalooline materjal" in html
    assert 'id="ajalooline-heading"' not in html
    summary = historical_summary(later, specialist)
    assert summary["links"] == [] and len(summary["predecessor_pages"]) == 2
