"""What a permanently deleted Teema leaves behind is unreachable (owner review B).

docs/adr/0165 §5 keeps an audit residue under a deleted Teema: the placeholder
`Matter` row («Kustutatud teema», `deleted_at` set) its append-only
`ChangeEvent`s point at, those events themselves, `MatterStageEpisode` rows and
any `ImportRowLedger`. That residue is preserved, untouched — and this module
proves that **no ordinary application surface reaches it**:

* the Teemad register under every status, record kind and data class, and its
  search;
* `/otsing/` and its suggestions, by the old title, a word of it, the
  reference number and the Matter id;
* the Teema page and every sub-URL; its documents, versions, thumbnails and
  opinions by id;
* Minu asjad, Osakond, Osakond Muudatused and Tehtud, Saabunud, a colleague's
  page, the tracking lists (Jõustumised among them);
* Statistika: every tab, every drill-through list and every CSV export reached
  from them — **and their figures are exactly what they were before the Teema
  existed**;
* a related Teema's page, history, change log and related-materials pickers.

Three deleted Teemad are used — an ordinary one with a sent opinion, a TEST-data
one and a register archive row — so every `andmed`/`liik`/`olek` branch has
something it could leak. Every record here is synthetic.
"""

from __future__ import annotations

import re
from collections import deque
from datetime import timedelta
from urllib.parse import urlsplit

import pytest
from django.urls import reverse
from django.utils import timezone

from app.documents.models import Document, DocumentVersion
from app.intelligence.services import add_effective_date, add_important_date
from app.matters.deletion import TOMBSTONE_TITLE, permanently_delete_matter, plan_matter_deletion
from app.matters.enums import MatterDataClass, MatterOrigin, RecordMode
from app.matters.models import Matter
from app.matters.services import add_entry
from app.related_materials.services import link_related_matters
from app.submissions.models import Submission
from tests import factories
from tests.follow_ups import day, send_koja_arvamus

pytestmark = pytest.mark.django_db

#: Words that occur nowhere else in any world this suite builds.
TOKENS = {
    "real": "Zorblaksiline",
    "test": "Kwivertrumm",
    "archive": "Pluzzandrell",
}

#: Statistika pages and CSV exports whose *figures* are compared before the
#: deleted Teemad existed and after they were deleted.
STATISTICS_PAGES = (
    "/statistika/",
    "/statistika/teemad/",
    "/statistika/tegevus/",
    "/statistika/ajalooline/",
    "/statistika/andmekvaliteet/",
    "/statistika/arvamused/",
    "/statistika/materjalid/",
    "/statistika/eksport/teemad.csv",
    "/statistika/eksport/arvamused.csv",
    "/statistika/eksport/andmekvaliteet.csv",
    "/statistika/eksport/materjalid.csv",
)
DATA_CLASSES = ("", "?andmed=koik", "?andmed=test", "?andmed=paris")

#: Where the crawl may go from the seeds: list and report surfaces only, never
#: a write route, never a Teema page (those are checked by id below).
CRAWL_PREFIXES = (
    "/statistika/",
    "/teemad/?",
    "/arvamused/",
    "/osakond/",
    "/minu-asjad/",
    "/jalgimine/",
    "/otsing/",
    "/saabunud/",
)
CRAWL_LIMIT = 500

HREF = re.compile(r'href="([^"#]+)"')
VOLATILE = (
    re.compile(r'name="csrfmiddlewaretoken" value="[^"]*"'),
    re.compile(r'name="csrf-token" content="[^"]*"'),
    re.compile(r'data-csrf="[^"]*"'),  # docs/adr/0194: the remembered-blocks endpoint meta
    re.compile(r'nonce="[^"]*"'),
)


def _normalise(body: str) -> str:
    for pattern in VOLATILE:
        body = pattern.sub("", body)
    return body


def _get(client, url: str):
    response = client.get(url)
    body = b"".join(response.streaming_content) if response.streaming else response.content
    return response.status_code, body.decode("utf-8", errors="replace")


@pytest.fixture
def head(db):
    return factories.DepartmentHeadFactory(display_name="Testjuht Residuu")


@pytest.fixture
def world(specialist, head, capture_evidence, pdf_bytes):
    """A live department, and its Statistika as it reads *before* the deleted ones."""
    organisation = factories.OrganisationFactory(name="Püsiv ministeerium")
    bystander = factories.MatterFactory(owner=specialist, title="Püsiv kõrvalteema")
    add_entry(matter=bystander, body="Püsiv märge", author=specialist)
    capture_evidence(bystander, pdf_bytes, "pusiv.pdf", "application/pdf")
    send_koja_arvamus(bystander, specialist, [organisation], day(0))
    return {
        "organisation": organisation,
        "bystander": Matter.objects.get(pk=bystander.pk),
    }


def _snapshot(client, urls) -> dict[str, tuple[int, str]]:
    shot = {}
    for url in urls:
        status, body = _get(client, url)
        shot[url] = (status, _normalise(body))
    return shot


def _make_doomed(world, specialist, capture_evidence, pdf_bytes) -> dict:
    """Three Teemad with something on every surface, then deleted for good."""
    today = timezone.localdate()
    real = factories.MatterFactory(
        owner=specialist,
        title=f"{TOKENS['real']} eelnõu teema",
        brief_summary=f"{TOKENS['real']} kokkuvõte",
    )
    test = factories.MatterFactory(
        owner=specialist,
        title=f"{TOKENS['test']} katsetus",
        data_class=MatterDataClass.TEST,
    )
    # Every person exists before the "before" snapshot: a new colleague would
    # change the Vastutaja filter, which is a fact about people, not residue.
    archive = factories.MatterFactory(
        owner=specialist,
        title=f"{TOKENS['archive']} ajalooline kirje",
        record_mode=RecordMode.ARCHIVE,
        origin=MatterOrigin.LEGACY_IMPORT,
        is_open=False,
    )
    for matter, token in ((real, TOKENS["real"]), (test, TOKENS["test"])):
        add_entry(matter=matter, body=f"{token} märge", author=specialist)
        capture_evidence(matter, pdf_bytes + token.encode(), f"{token}.pdf", "application/pdf")
        add_effective_date(
            matter=matter,
            date_value=today + timedelta(days=10),
            period_end=today + timedelta(days=10),
            description=f"{token} jõustumine",
            actor=specialist,
        )
        add_important_date(
            matter=matter,
            title=f"{token} tähtaeg",
            date_value=today + timedelta(days=5),
            period_end=today + timedelta(days=5),
            actor=specialist,
        )
        send_koja_arvamus(matter, specialist, [world["organisation"]], day(0))
    capture_evidence(archive, pdf_bytes + b"archive", f"{TOKENS['archive']}.pdf", "application/pdf")
    link_related_matters(matter=world["bystander"], other=real, actor=specialist)

    doomed = [Matter.objects.get(pk=m.pk) for m in (real, test, archive)]
    ids = {
        "matters": [m.pk for m in doomed],
        "references": [m.display_reference for m in doomed],
        "documents": list(Document.objects.filter(matter__in=doomed).values_list("pk", flat=True)),
        "versions": list(
            DocumentVersion.objects.filter(document__matter__in=doomed).values_list("pk", flat=True)
        ),
        "submissions": list(
            Submission.objects.filter(matter__in=doomed).values_list("pk", flat=True)
        ),
        "owner": specialist.pk,
    }
    return {"doomed": doomed, "ids": ids}


def _delete_all(doomed, head, django_capture_on_commit_callbacks) -> None:
    for matter in doomed:
        with django_capture_on_commit_callbacks(execute=True):
            permanently_delete_matter(
                matter=matter, actor=head, confirmation=plan_matter_deletion(matter).digest
            )
    for matter in doomed:
        assert Matter.all_objects.get(pk=matter.pk).deleted_at is not None


def _leaks(body: str, ids: dict) -> list[str]:
    """Every trace of the deleted Teemad this body carries."""
    found = [token for token in TOKENS.values() if token in body]
    for reference in ids["references"]:
        if reference and re.search(rf"(?<![\d_]){re.escape(reference)}(?!\d)", body):
            found.append(reference)
    for key in ("matters", "documents", "versions", "submissions"):
        found.extend(str(pk) for pk in ids[key] if str(pk) in body)
    return found


@pytest.fixture
def deleted(
    client,
    world,
    specialist,
    head,
    capture_evidence,
    pdf_bytes,
    django_capture_on_commit_callbacks,
):
    """The three Teemad, created and permanently deleted; Statistika before and after."""
    client.force_login(head)
    figure_urls = [page + variant for page in STATISTICS_PAGES for variant in DATA_CLASSES]
    figure_urls += ["/osakond/", "/minu-asjad/", f"/inimesed/{specialist.pk}/asjad/"]
    before = _snapshot(client, figure_urls)

    made = _make_doomed(world, specialist, capture_evidence, pdf_bytes)
    # Every surface really did carry them before the deletion — otherwise the
    # absence below would prove nothing.
    _status, register = _get(client, "/teemad/?olek=koik&andmed=koik")
    assert TOKENS["real"] in register and TOKENS["test"] in register
    _status, archive = _get(client, "/teemad/?olek=arhiiv&andmed=koik")
    assert TOKENS["archive"] in archive
    # And the figures did move while they existed, so «unchanged afterwards»
    # is a statement about the deletion rather than about insensitive pages.
    during = _snapshot(client, figure_urls)
    assert sum(during[url] != before[url] for url in figure_urls) >= 10

    _delete_all(made["doomed"], head, django_capture_on_commit_callbacks)
    return {**made, "before": before, "figure_urls": figure_urls}


# ---------------------------------------------------------------------------
# The residue exists — it is preserved, not cleaned up
# ---------------------------------------------------------------------------


def test_the_audit_residue_is_preserved(deleted):
    from app.audit.models import ChangeEvent

    for matter in deleted["doomed"]:
        tombstone = Matter.all_objects.get(pk=matter.pk)
        assert tombstone.title == TOMBSTONE_TITLE
        assert ChangeEvent.objects.filter(matter_id=matter.pk).exists()
        assert not Matter.objects.filter(pk=matter.pk).exists()


# ---------------------------------------------------------------------------
# Every route keyed by a deleted id answers 404
# ---------------------------------------------------------------------------

MATTER_ROUTES = (
    "matters:matter_detail",
    "matters:matter_documents",
    "matters:matter_changes",
    "matters:timeline_page",
    "matters:matter_delete",
    "matters:matter_edit",
    "matters:matter_edit_assisted",
    "matters:matter_position",
    "related_materials:section",
    "related_materials:picker",
)


def test_every_teema_url_of_a_deleted_matter_is_a_404(client, head, deleted):
    client.force_login(head)
    for pk in deleted["ids"]["matters"]:
        for name in MATTER_ROUTES:
            url = reverse(name, kwargs={"pk": pk})
            status, body = _get(client, url)
            assert status == 404, (url, status)
            assert not _leaks(body, deleted["ids"]), url


def test_documents_versions_and_opinions_of_a_deleted_matter_are_404(client, head, deleted):
    client.force_login(head)
    ids = deleted["ids"]
    urls = [reverse("documents:document_detail", kwargs={"pk": pk}) for pk in ids["documents"]]
    for pk in ids["versions"]:
        urls += [
            reverse("documents:download", kwargs={"pk": pk}),
            reverse("documents:open", kwargs={"pk": pk}),
            reverse("documents:thumbnail", kwargs={"pk": pk}),
        ]
    urls += [reverse("submissions:metadata", kwargs={"pk": pk}) for pk in ids["submissions"]]
    urls += [
        reverse("submissions:register_sent", kwargs={"matter_id": pk}) for pk in ids["matters"]
    ]
    assert ids["versions"] and ids["submissions"]
    for url in urls:
        status, body = _get(client, url)
        assert status in (404, 405), (url, status)
        assert not _leaks(body, ids), url


# ---------------------------------------------------------------------------
# Register, search and autocomplete
# ---------------------------------------------------------------------------


def _register_urls(ids) -> list[str]:
    urls = []
    for state in ("avatud", "suletud", "arhiiv", "koik"):
        for data in ("koik", "test", "paris"):
            for mode in ("", "&liik=FULL", "&liik=ARCHIVE"):
                urls.append(f"/teemad/?olek={state}&andmed={data}{mode}")
    for term in [*TOKENS.values(), TOMBSTONE_TITLE, "Kustutatud", *ids["references"]]:
        urls += [
            f"/teemad/?olek=koik&andmed=koik&q={term}",
            f"/otsing/?q={term}",
            f"/otsing/?q={term}&andmed=koik",
            f"/otsing/soovitused/?q={term}",
        ]
    urls += [f"/otsing/?q={pk}" for pk in ids["matters"]]
    urls += [
        "/teemad/?olek=koik&andmed=koik&joustumine=on",
        "/teemad/?joustumine=on&vaade=kalender",
    ]
    return urls


def _without_echo(url: str, body: str) -> str:
    """The body minus the search term itself, which the search box echoes back."""
    from urllib.parse import parse_qs

    for term in parse_qs(urlsplit(url).query).get("q", []):
        body = body.replace(term, "")
    return body


def test_the_register_and_search_never_return_a_deleted_teema(client, head, deleted):
    client.force_login(head)
    ids = deleted["ids"]
    for url in _register_urls(ids):
        status, body = _get(client, url)
        assert status == 200, (url, status)
        body = _without_echo(url, body)
        assert not _leaks(body, ids), (url, _leaks(body, ids))


def test_search_for_the_placeholder_title_finds_no_placeholder(client, head, deleted):
    client.force_login(head)
    for url in (f"/otsing/?q={TOMBSTONE_TITLE}", f"/otsing/soovitused/?q={TOMBSTONE_TITLE}"):
        _status, body = _get(client, url)
        for pk in deleted["ids"]["matters"]:
            assert str(pk) not in body, url


def test_the_related_pickers_never_offer_a_deleted_teema(client, head, world, deleted):
    client.force_login(head)
    bystander = world["bystander"]
    for term in TOKENS.values():
        for url in (
            reverse("related_materials:picker", kwargs={"pk": bystander.pk}) + f"?q={term}",
            reverse("related_materials:draft_picker") + f"?q={term}",
        ):
            status, body = _get(client, url)
            assert status == 200, (url, status)
            assert not _leaks(_without_echo(url, body), deleted["ids"]), url
        response = client.post(reverse("related_materials:draft_suggestions"), {"title": term})
        assert not _leaks(response.content.decode(), deleted["ids"]), term


def test_a_related_teema_keeps_no_trace_of_its_deleted_partner(client, head, world, deleted):
    """`Seotud` and the history of the Teema that was linked to the deleted one."""
    client.force_login(head)
    bystander = world["bystander"]
    for name in (
        "matters:matter_detail",
        "matters:matter_changes",
        "matters:timeline_page",
        "matters:matter_documents",
        "related_materials:section",
    ):
        url = reverse(name, kwargs={"pk": bystander.pk})
        status, body = _get(client, url)
        assert status == 200, (url, status)
        assert not _leaks(body, deleted["ids"]), (url, _leaks(body, deleted["ids"]))


# ---------------------------------------------------------------------------
# Work surfaces, department pages, tracking lists and Statistika — a crawl
# ---------------------------------------------------------------------------


def _seeds(specialist_pk) -> list[str]:
    seeds = [
        "/minu-asjad/",
        "/osakond/",
        "/osakond/muudatused/",
        "/osakonna-too/",
        "/saabunud/",
        f"/inimesed/{specialist_pk}/asjad/",
        "/arvamused/",
        "/arvamused/arhiiv/",
        "/jalgimine/joustumised/",
        "/jalgimine/tahtajad/",
        "/jalgimine/toovoidud/",
    ]
    for period in ("7", "14", "30", "kvartal", "aasta"):
        seeds.append(f"/osakond/muudatused/?periood={period}")
        seeds.append(f"/osakond/?periood={period}")
    seeds += [page + variant for page in STATISTICS_PAGES for variant in DATA_CLASSES]
    return seeds


def _crawlable(url: str) -> bool:
    if not url.startswith("/"):
        return False
    path = urlsplit(url).path
    if path.startswith("/teemad/") and path != "/teemad/":
        return False
    return url.startswith(CRAWL_PREFIXES) or path == "/teemad/"


def test_no_list_report_drill_through_or_export_reaches_the_residue(
    client, head, specialist, deleted
):
    client.force_login(head)
    ids = deleted["ids"]
    queue = deque(_seeds(specialist.pk))
    seen: set[str] = set()
    drill_throughs = 0
    while queue and len(seen) < CRAWL_LIMIT:
        url = queue.popleft().replace("&amp;", "&")
        if url in seen:
            continue
        seen.add(url)
        status, body = _get(client, url)
        assert status in (200, 301, 302, 404), (url, status)
        body = _without_echo(url, body)
        assert not _leaks(body, ids), (url, _leaks(body, ids))
        if "/statistika/loend/" in url or url.endswith(".csv") or "eksport" in url:
            drill_throughs += 1
        for href in HREF.findall(body):
            href = href.replace("&amp;", "&")
            if _crawlable(href) and href not in seen:
                queue.append(href)
    # The crawl really went through the drill-throughs and exports.
    assert drill_throughs >= 5, drill_throughs
    assert len(seen) > 60, len(seen)


def test_statistics_and_department_figures_are_as_if_the_teemad_never_existed(
    client, head, deleted
):
    """Before they were created, and after they were deleted: the same page."""
    client.force_login(head)
    after = _snapshot(client, deleted["figure_urls"])
    changed = [url for url, shot in deleted["before"].items() if after[url] != shot]
    if changed:
        import difflib

        url = changed[0]
        diff = "\n".join(
            difflib.unified_diff(
                deleted["before"][url][1].splitlines(),
                after[url][1].splitlines(),
                lineterm="",
                n=1,
            )
        )
        pytest.fail(f"{len(changed)} changed, first {url}:\n{diff[:3000]}")
