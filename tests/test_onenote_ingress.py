"""OneNote ingress Phase 1 prototype, against invented notebooks only.

Every host, GUID, title and filename here is made up at test time. These tests
prove how *our* code handles each case; they prove nothing about how Microsoft
assigns or preserves IDs. Where a test models provider behaviour (an ID that
survives a move, a copy that gets a new ID), it is labelled "modelled".
"""

from __future__ import annotations

import ast
import hashlib
import io
import pathlib
import urllib.error
import urllib.parse
import urllib.request
import urllib.response
import uuid
from datetime import UTC, datetime, timedelta
from email.message import Message

import pytest

from tools.onenote_ingress.capture import (
    CaptureLedger,
    CaptureResponse,
    CaptureVerdict,
    PageMetadata,
    verify_capture,
)
from tools.onenote_ingress.identity import (
    AccessState,
    AccessTracker,
    Continuity,
    ContinuityEvidence,
    IdentityRegistry,
    LocatorHistory,
    LocatorObservation,
    ObservedPage,
    OccurrenceRecord,
    ProviderId,
    ResourceChange,
    classify_resources,
)
from tools.onenote_ingress.inventory import (
    ListingChain,
    Reader,
    from_desktop_hierarchy,
    reconcile_scans,
)
from tools.onenote_ingress.locators import LinkFamily, TargetKind, canonical_guid, parse_locator
from tools.onenote_ingress.resolution import (
    ApprovedScope,
    CohortMember,
    LinkState,
    resolve_cohort,
    totals,
)
from tools.onenote_ingress.resources import (
    MAX_RESOURCE_BYTES,
    DownloadVerdict,
    OccurrenceKind,
    UrlVerdict,
    check_resource_url,
    occurrences,
    verify_download,
)
from tools.onenote_ingress.scan import (
    OVERLAP,
    Job,
    JobKind,
    RunOutcome,
    ScopeCheckpoint,
    ScopeScanResult,
    advance,
    listing_window_start,
    plan,
    retry_after_seconds,
    run_outcome,
)
from tools.onenote_ingress.transport import ScopedGraphTransport, ScopeViolation, TooLarge

HOST = "naidiskoda-invalid.sharepoint.com"
SITE = "/sites/Naidisosakond"
NOTEBOOK = str(uuid.UUID(int=0xA11CE)).upper()
OTHER_NOTEBOOK = str(uuid.UUID(int=0xB0B)).upper()
NB_PATH = f"{SITE}/SiteAssets/Näidisosakond Notebook"
SCOPE = ApprovedScope(
    host=HOST, site_path=SITE, notebook_guid=NOTEBOOK, notebook_server_path=NB_PATH
)


DOC = f"https://{HOST}{SITE}/_layouts/Doc.aspx?sourcedoc={{{NOTEBOOK}}}&wd="


def wd(text: str) -> str:
    return urllib.parse.quote(text, safe="")


def g(n: int) -> str:
    return str(uuid.UUID(int=n)).upper()


def doc_link(
    section: str, sguid: str, title: str, pguid: str, *, notebook: str = NOTEBOOK, history: str = ""
) -> str:
    target = urllib.parse.quote(f"target({section}|{sguid}/{title}|{pguid}/)", safe="")
    prefix = f"/_vti_history/{history}" if history else ""
    return (
        f"https://{HOST}{SITE}{prefix}/_layouts/Doc.aspx?sourcedoc={{{notebook}}}&wd={target}"
        f"&wdpartid={{{g(9)}}}{{1}}&wdsectionfileid={{{g(8)}}}&end"
    )


def client_link(section: str, sguid: str, pguid: str, title: str = "x") -> str:
    path = urllib.parse.quote(f"{NB_PATH}/{section}")
    return f"onenote:https://{HOST}{path}#{title}&section-id={{{sguid}}}&page-id={{{pguid}}}&end"


# -- locators --------------------------------------------------------------------


def test_doc_link_yields_the_page_guid_not_the_notebook_guid():
    raw = doc_link("Maksud.one", g(1), "Käibemaks (eelnõu)", g(2))
    loc = parse_locator(raw)
    assert loc.family is LinkFamily.WEB_DOC_TARGET
    assert loc.target_kind is TargetKind.PAGE
    assert loc.page_guid == g(2)
    assert loc.section_guid == g(1)
    assert loc.notebook_guid == NOTEBOOK
    assert loc.page_guid != loc.notebook_guid != loc.section_file_guid
    assert loc.title_hint == "Käibemaks (eelnõu)"
    assert loc.raw == raw


def test_many_doc_links_never_collapse_onto_one_identity():
    """The ADR 0015 shape: every link shares sourcedoc, so sourcedoc is never a page id."""
    links = [doc_link("A.one", g(1), f"Leht {n}", g(100 + n)) for n in range(25)]
    assert len({parse_locator(link).page_guid for link in links}) == 25


def test_lowercase_braced_and_percent_encoded_guids_compare_equal_but_raw_is_kept():
    upper = doc_link("A.one", g(1), "T", g(2))
    lower = upper.replace(g(2), g(2).lower()).replace("{", "%7b").replace("}", "%7d")
    assert parse_locator(lower).page_guid == parse_locator(upper).page_guid == g(2)
    assert parse_locator(lower).raw == lower
    assert (
        canonical_guid("%7Babc00000-0000-0000-0000-000000000001%7D")
        == "ABC00000-0000-0000-0000-000000000001"
    )
    assert canonical_guid("not-a-guid") == ""


def test_nested_section_group_target_and_escaped_title_characters():
    target = f"target(Rühm/Alamrühm/Jaotis.one|{g(3)}/Pealkiri \\) | kaldkriips / lõpp|{g(4)}/)"
    raw = DOC + wd(target)
    loc = parse_locator(raw)
    assert loc.target_section_path == "Rühm/Alamrühm/Jaotis.one"
    assert loc.page_guid == g(4)
    assert loc.title_hint == "Pealkiri ) | kaldkriips / lõpp"


def test_double_encoded_wd_is_decoded_and_flagged():
    once = urllib.parse.quote(f"target(A.one|{g(1)}/T|{g(2)}/)", safe="")
    raw = DOC + wd(once)
    loc = parse_locator(raw)
    assert loc.page_guid == g(2)
    assert "WD_MULTIPLY_ENCODED" in loc.problems


def test_web_plus_client_link_preserves_fragment_and_checks_agreement():
    raw = doc_link("A.one", g(1), "T", g(2)) + " " + client_link("A.one", g(1), g(2))
    loc = parse_locator(raw)
    assert loc.family is LinkFamily.WEB_DOC_TARGET_WITH_CLIENT
    assert loc.client_page_guid == loc.page_guid == g(2)
    assert loc.page_guids == {g(2)}
    assert loc.raw == raw  # nothing stripped, fragment included

    conflicting = doc_link("A.one", g(1), "T", g(2)) + " " + client_link("A.one", g(1), g(5))
    loc = parse_locator(conflicting)
    assert "CLIENT_PAGE_GUID_DIFFERS" in loc.problems
    assert loc.page_guids == {g(2), g(5)}


def test_onenote_aspx_id_is_a_notebook_path_not_a_page_id():
    target = wd(f"target(A.one|{g(1)}/T|{g(2)}/)")
    raw = f"https://{HOST}{SITE}/_vti_history/77/_layouts/OneNote.aspx?id={wd(NB_PATH)}&wd={target}"
    loc = parse_locator(raw)
    assert loc.family is LinkFamily.WEB_ONENOTE_ASPX_TARGET
    assert loc.notebook_path == NB_PATH
    assert loc.page_guid == g(2)
    assert loc.history_version == "77"
    assert loc.site_path == SITE


def test_section_link_document_path_and_garbage():
    section = DOC + wd(f"target(A.one|{g(1)}/)")
    assert parse_locator(section).target_kind is TargetKind.SECTION
    assert (
        parse_locator(f"https://{HOST}{SITE}/Shared%20Documents/General/Kaust").family
        is LinkFamily.SHAREPOINT_PATH
    )
    assert parse_locator("   ").family is LinkFamily.EMPTY
    assert parse_locator("http://x.invalid/a").family is LinkFamily.UNPARSEABLE
    assert parse_locator("lihtsalt tekst").family is LinkFamily.UNPARSEABLE


# -- desktop inventory --------------------------------------------------------------


def hierarchy(
    pages_by_section: dict[str, list[tuple[str, str, int]]], *, binned: dict | None = None
) -> tuple[str, dict[str, str]]:
    ns = "http://schemas.microsoft.com/office/onenote/2013/onenote"
    links: dict[str, str] = {}
    out = [
        f'<one:Notebook xmlns:one="{ns}" name="Näidis" ID="{{NB}}" path="https://{HOST}{NB_PATH}/">'
    ]

    def section(name: str, sguid: str, pages: list[tuple[str, str, int]]) -> None:
        sid = f"{{S-{sguid}}}"
        links[sid] = f"onenote:https://{HOST}{NB_PATH}/{name}#section-id={{{sguid}}}&end"
        out.append(
            f'<one:Section name="{name.removesuffix(".one")}" ID="{sid}" path="https://{HOST}{NB_PATH}/{name}">'
        )
        for pguid, title, level in pages:
            pid = f"{{P-{pguid}}}"
            links[pid] = client_link(name, sguid, pguid, title)
            out.append(
                f'<one:Page ID="{pid}" name="{title}" pageLevel="{level}" '
                'dateTime="2026-01-01T00:00:00.000Z" lastModifiedTime="2026-02-01T00:00:00.000Z"/>'
            )
        out.append("</one:Section>")

    for i, (name, pages) in enumerate(pages_by_section.items()):
        section(name, g(1000 + i), pages)
    if binned:
        out.append('<one:SectionGroup name="OneNote_RecycleBin" ID="{RB}" isRecycleBin="true">')
        for i, (name, pages) in enumerate(binned.items()):
            section(name, g(2000 + i), pages)
        out.append("</one:SectionGroup>")
    out.append("</one:Notebook>")
    return "".join(out), links


def test_desktop_inventory_derives_subpage_parents_from_section_order_and_marks_the_bin():
    xml, links = hierarchy(
        {
            "Aktiivne.one": [
                (g(1), "Ülem", 1),
                (g(2), "Alam", 2),
                (g(3), "Alam-alam", 3),
                (g(4), "Teine", 1),
            ],
            "ARHIIV.one": [(g(5), "Vana", 2)],
        },
        binned={"Kustutatud.one": [(g(6), "Prügi", 1)]},
    )
    inv = from_desktop_hierarchy(xml, links, read_started="t0", read_finished="t1")
    pages = {p.page_guid: p for p in inv.pages}
    assert inv.reader is Reader.ONENOTE_DESKTOP
    assert pages[g(2)].parent_object_id == pages[g(1)].object_id
    assert pages[g(3)].parent_object_id == pages[g(2)].object_id
    assert pages[g(4)].parent_object_id == ""
    assert pages[g(5)].archived and not pages[g(5)].in_recycle_bin
    assert pages[g(6)].in_recycle_bin
    assert inv.gaps == [
        "SUBPAGE_WITHOUT_PARENT:{P-" + g(5) + "}"
    ]  # orphan level-2 page is a gap, not hidden


def test_inventory_records_a_page_without_a_link_as_a_gap():
    xml, links = hierarchy({"A.one": [(g(1), "T", 1)]})
    del links["{P-" + g(1) + "}"]
    inv = from_desktop_hierarchy(xml, links, read_started="", read_finished="")
    assert not inv.complete
    assert any(gap.startswith("PAGE_WITHOUT_LINK") for gap in inv.gaps)


# -- resolution (Gate B states, many-to-many, COMPARE) --------------------------------


def _inventory():
    xml, links = hierarchy(
        {
            "A.one": [(g(1), "Ühine leht", 1), (g(2), "Teine", 1), (g(3), "Sama pealkiri", 1)],
            "ARHIIV.one": [(g(4), "Sama pealkiri", 2)],
        },
        binned={"B.one": [(g(5), "Kustutatud", 1)]},
    )
    return from_desktop_hierarchy(xml, links, read_started="", read_finished="")


def member(
    n: int, link: str, *, canonical: str | None = None, is_open: bool = True
) -> CohortMember:
    return CohortMember(
        matter_id=str(n),
        source_id=str(uuid.UUID(int=n)),
        reference=f"2026_{n:03d}",
        is_open=is_open,
        observed_link=link,
        applied_link=link,
        canonical_link=link if canonical is None else canonical,
    )


def test_every_topic_gets_exactly_one_explicit_state():
    sec = 1000
    a = g(sec)
    members = [
        member(1, doc_link("A.one", a, "Ühine leht", g(1))),
        member(
            2, doc_link("A.one", a, "Ühine", g(1)) + " " + client_link("A.one", a, g(1))
        ),  # shared page
        member(3, ""),  # missing link stays a visible gap
        member(
            4,
            DOC + wd(f"target(A.one|{a}/)"),
        ),
        member(5, doc_link("A.one", a, "Kadunud", g(77))),  # GUID not in inventory
        member(6, doc_link("B.one", g(2000), "Kustutatud", g(5))),
        member(7, doc_link("A.one", a, "Teine", g(2), notebook=OTHER_NOTEBOOK)),
        member(8, f"https://{HOST}{SITE}/Shared%20Documents/General/Kaust"),
        member(
            9, doc_link("A.one", a, "T", g(2)) + " " + client_link("A.one", a, g(3))
        ),  # two pages named
        member(
            10, doc_link("Vana.one", g(999), "Sama pealkiri", g(4)), is_open=False
        ),  # moved to ARHIIV (modelled)
    ]
    res = resolve_cohort(members, _inventory(), SCOPE)
    states = [r.state for r in res]
    assert states == [
        LinkState.EXACT_PAGE,
        LinkState.EXACT_PAGE,
        LinkState.NO_LINK,
        LinkState.CONTAINER_ONLY,
        LinkState.PAGE_NOT_FOUND,
        LinkState.IN_RECYCLE_BIN,
        LinkState.OUT_OF_SCOPE,
        LinkState.OUT_OF_SCOPE,
        LinkState.MULTIPLE_PAGES,
        LinkState.EXACT_PAGE,
    ]
    assert (
        "PAGE_NOW_IN_ANOTHER_SECTION" in res[9].notes and "PAGE_IN_ARCHIVE_SECTION" in res[9].notes
    )
    t = totals(res, expected_source_ids=[m.source_id for m in members])
    assert t.accounted and t.topics == 10 and sum(t.by_state.values()) == 10
    assert t.shared_pages == 1 and t.topics_on_shared_pages == 2
    assert t.by_state["NO_LINK"] == 1


def test_title_similarity_only_generates_candidates_never_a_resolution():
    res = resolve_cohort(
        [member(1, doc_link("A.one", g(1000), "Sama pealkiri", g(404)))], _inventory(), SCOPE
    )[0]
    assert res.state is LinkState.PAGE_NOT_FOUND
    assert len(res.title_candidates) == 2  # two pages share the title
    assert not res.resolved and res.pages == []


def test_compare_uses_the_observed_locator_and_reports_drift_from_canonical():
    observed = doc_link("A.one", g(1000), "Teine", g(2))
    canonical = doc_link("A.one", g(1000), "Ühine leht", g(1))
    m = member(1, observed, canonical=canonical)
    res = resolve_cohort([m], _inventory(), SCOPE)[0]
    assert res.pages[0].page_guid == g(2)
    assert "OBSERVED_DIFFERS_FROM_CANONICAL" in res.notes
    assert m.canonical_link == canonical  # nothing was "applied"


# -- capture binding: the ADR 0015 regression --------------------------------------------


def page_html(title: str, created: str, text: str) -> bytes:
    return (
        f'<html><head><title>{title}</title><meta name="created" content="{created}" /></head>'
        f"<body><div><p>{text}</p></div></body></html>"
    ).encode()


def response_for(
    url: str, body: bytes, *, echo: bool = True, status: int = 200, ctype: str = "text/html"
) -> CaptureResponse:
    rid = str(uuid.uuid4())
    headers = {
        "Content-Type": ctype,
        "Content-Length": str(len(body)),
        "request-id": str(uuid.uuid4()),
    }
    if echo:
        headers["client-request-id"] = rid
    return CaptureResponse(url, url, status, headers, body, rid)


def _meta(n: int, title: str | None = None) -> PageMetadata:
    return PageMetadata(
        page_id=f"1-{n:04x}!1-{g(n)}",
        title=title or f"Leht {n}",
        created=f"2026-01-{n:02d}T10:00:00Z",
        last_modified="",
    )


def test_a_stale_previous_body_is_never_filed_under_the_next_page():
    """Reproduces the 342-page failure: each response carries the previous page's body."""
    ledger = CaptureLedger()
    metas = [_meta(n) for n in range(1, 8)]
    for m in metas:
        ledger.register_metadata(m)
    bodies = [
        page_html(m.title, m.created, f"Sisu, mis kuulub ainult lehele {m.title}, piisavalt pikk.")
        for m in metas
    ]
    accepted = 0
    for index, m in enumerate(metas):
        stale = bodies[index - 1] if index else b"<html></html>"
        result = verify_capture(
            m, response_for(f"https://graph.microsoft.com/v1.0/x/{index}", stale), ledger
        )
        accepted += result.usable
        assert result.verdict is CaptureVerdict.REJECTED
    assert accepted == 0


def test_a_correct_capture_is_accepted_once_and_a_repeat_read_is_not_a_duplicate():
    ledger = CaptureLedger()
    m = _meta(3)
    ledger.register_metadata(m)
    body = page_html(
        m.title,
        "2026-01-03T10:00:00.0000000",
        "Päris sisu, mis on piisavalt pikk, et olla tõendmaterjal.",
    )
    first = verify_capture(m, response_for("https://graph.microsoft.com/v1.0/p", body), ledger)
    again = verify_capture(m, response_for("https://graph.microsoft.com/v1.0/p", body), ledger)
    assert first.verdict is again.verdict is CaptureVerdict.ACCEPTED
    assert first.body_sha256 == again.body_sha256


def test_cache_misrouted_or_unechoed_responses_are_rejected():
    ledger = CaptureLedger()
    m = _meta(4)
    body = page_html(m.title, m.created, "x" * 80)
    assert (
        verify_capture(m, response_for("u", body, echo=False), ledger).verdict
        is CaptureVerdict.REJECTED
    )
    login = (
        b"<html><head><title>Sign in to your account</title></head>"
        b"<body>login.microsoftonline.com</body></html>"
    )
    assert "SIGN_IN_OR_ERROR_PAGE" in verify_capture(m, response_for("u", login), ledger).reasons
    truncated = body[:-20]
    assert (
        verify_capture(m, response_for("u", truncated), ledger).verdict is CaptureVerdict.REJECTED
    )
    wrong_type = verify_capture(m, response_for("u", body, ctype="application/json"), ledger)
    assert "NOT_TEXT_HTML" in wrong_type.reasons


def test_identical_bodies_under_two_identities_are_classified_not_kept_silently():
    a, b = _meta(5, "Mall"), _meta(6, "Mall")
    b = PageMetadata(b.page_id, b.title, a.created, "")  # same title, same created second
    ledger = CaptureLedger.for_run([a, b])
    template = page_html("Mall", a.created, "")
    first = verify_capture(a, response_for("u", template), ledger)
    second = verify_capture(b, response_for("u", template), ledger)
    # Markers cannot tell the twins apart, so neither may count as a proven binding.
    assert first.verdict is second.verdict is CaptureVerdict.WEAK_BINDING
    assert not first.usable and not second.usable
    assert ledger.duplicate_holders == {a.page_id, b.page_id}

    c, d = _meta(7, "Koopia"), _meta(8, "Koopia")  # copied page: same title, different created
    ledger = CaptureLedger.for_run([c, d])
    body = page_html(
        "Koopia", "2026-01-07T10:00:00Z", "Sisuline tekst, mis on mõlemal lehel sama ja pikk."
    )
    body_d = body.replace(b"2026-01-07T10:00:00Z", b"2026-01-08T10:00:00Z")
    assert verify_capture(c, response_for("u", body), ledger).verdict is CaptureVerdict.ACCEPTED
    assert verify_capture(d, response_for("u", body_d), ledger).verdict is CaptureVerdict.ACCEPTED
    same = verify_capture(d, response_for("u", body), ledger)  # c's body under d's identity
    assert same.verdict is CaptureVerdict.REJECTED  # its created marker belongs to c

    e, f = _meta(9, "Mall 2"), _meta(10, "Mall 3")
    ledger = CaptureLedger.for_run([e, f])
    text = "Täpselt sama pikk sisu kahel eri lehel, mis tuleb üle vaadata."
    body_e = page_html("Mall 2", e.created, text)
    body_f = page_html("Mall 3", f.created, text)
    assert verify_capture(e, response_for("u", body_e), ledger).verdict is CaptureVerdict.ACCEPTED
    assert verify_capture(f, response_for("u", body_f), ledger).verdict is CaptureVerdict.ACCEPTED
    assert ledger.duplicate_holders == set()  # different bodies (markers differ)


def test_a_capture_outside_the_run_ledger_is_refused():
    m = _meta(11)
    body = page_html(m.title, m.created, "Sisu, mis on piisavalt pikk, et olla tõend.")
    result = verify_capture(m, response_for("u", body), CaptureLedger())
    assert result.verdict is CaptureVerdict.REJECTED
    assert "METADATA_NOT_IN_RUN_LEDGER" in result.reasons


# -- resources -----------------------------------------------------------------------------

SITE_ID = "naidis.sharepoint.com,1111,2222"
SCOPE_PATH = f"/v1.0/sites/{SITE_ID}"
OTHER_SCOPE = "/v1.0/sites/teine-sait"


def res_url(rid: str, *, scope: str = SCOPE_PATH, host: str = "graph.microsoft.com") -> str:
    return f"https://{host}{scope}/onenote/resources/{rid}/$value"


def test_embedded_original_image_and_links_are_distinguished_and_urls_scoped():
    html = (
        f'<html><body><object data="{res_url("r-1")}" data-attachment="leping.pdf" '
        'type="application/pdf"/>'
        f'<img src="{res_url("r-2s")}" data-fullres-src="{res_url("r-2")}" '
        'data-fullres-src-type="image/png"/>'
        f'<img src="https://evil.invalid/x.png"/>'
        f'<object data="{res_url("r-3", scope=OTHER_SCOPE)}" data-attachment="voor.docx"/>'
        f'<a href="https://{HOST}/sites/x/Shared%20Documents/a.docx">dok</a><a href="https://www.riigiteataja.ee/">RT</a>'
        '<a href="onenote:#Teine&amp;section-id={1}">sisemine</a></body></html>'
    )
    occ = occurrences("p1", html, site_scope_path=SCOPE_PATH)
    kinds = [o.kind for o in occ]
    assert kinds == [
        OccurrenceKind.EMBEDDED_FILE,
        OccurrenceKind.IMAGE,
        OccurrenceKind.IMAGE_RENDITION_ONLY,  # only an optimised src: never filed as an original
        OccurrenceKind.EMBEDDED_FILE,
        OccurrenceKind.LINKED_SHAREPOINT,
        OccurrenceKind.EXTERNAL_LINK,
        OccurrenceKind.INTERNAL_ONENOTE_LINK,
    ]
    assert occ[0].resource_id == "r-1" and occ[0].filename == "leping.pdf"
    assert occ[1].resource_id == "r-2"  # the original, not the optimised src
    assert occ[2].url_verdict is UrlVerdict.HOST_NOT_ALLOWED
    assert occ[3].url_verdict is UrlVerdict.OUTSIDE_SITE_SCOPE
    assert (
        check_resource_url(res_url("../../me"), site_scope_path=SCOPE_PATH)[0] is not UrlVerdict.OK
    )


def _occ(name: str):
    return occurrences(
        "p", f'<object data="{res_url("r")}" data-attachment="{name}"/>', site_scope_path=SCOPE_PATH
    )[0]


def test_download_verification_detects_truncation_impostors_and_size_budget():
    pdf = b"%PDF-1.7\n" + b"0" * 500
    o = _occ("a.pdf")
    url = o.url
    ok = verify_download(
        o,
        status=200,
        headers={"Content-Length": str(len(pdf))},
        body=pdf,
        requested_url=url,
        final_url=url,
    )
    assert ok.verdict is DownloadVerdict.VERIFIED and ok.sha256 == hashlib.sha256(pdf).hexdigest()
    cut = verify_download(
        o,
        status=200,
        headers={"Content-Length": str(len(pdf) + 10)},
        body=pdf,
        requested_url=url,
        final_url=url,
    )
    assert cut.verdict is DownloadVerdict.TRUNCATED
    login = b"<!DOCTYPE html><html>Sign in login.microsoftonline.com</html>"
    assert (
        verify_download(
            o, status=200, headers={}, body=login, requested_url=url, final_url=url
        ).verdict
        is DownloadVerdict.NOT_AN_ORIGINAL
    )
    png_as_pdf = verify_download(
        o, status=200, headers={}, body=b"\x89PNG\r\n\x1a\n...", requested_url=url, final_url=url
    )
    assert (
        png_as_pdf.verdict is DownloadVerdict.FORMAT_IMPLAUSIBLE
    )  # a preview is not the attachment
    big = verify_download(
        o,
        status=200,
        headers={"Content-Length": str(MAX_RESOURCE_BYTES + 1)},
        body=b"",
        requested_url=url,
        final_url=url,
    )
    assert big.verdict is DownloadVerdict.TOO_LARGE_NOT_TESTED
    assert (
        verify_download(
            o, status=302, headers={}, body=b"", requested_url=url, final_url="https://x.invalid"
        ).verdict
        is DownloadVerdict.REDIRECTED
    )


def test_signed_container_is_preserved_and_never_claimed_verified():
    o = _occ("allkiri.asice")
    body = b"PK\x03\x04mimetypeapplication/vnd.etsi.asic-e+zip"
    r = verify_download(o, status=200, headers={}, body=body, requested_url=o.url, final_url=o.url)
    assert r.verdict is DownloadVerdict.VERIFIED
    assert r.signed_container and r.signature_verified is False


def test_replacement_rename_and_separate_same_named_file():
    before = [
        OccurrenceRecord("r1", "otsus.pdf", "aa", 0),
        OccurrenceRecord("r2", "lisa.docx", "bb", 1),
    ]
    after = [
        OccurrenceRecord(
            "r1", "otsus (parandatud).pdf", "aa", 0
        ),  # rename only: same id, same bytes
        OccurrenceRecord(
            "r3", "lisa.docx", "cc", 1
        ),  # same name, new id, new bytes: maybe replacement
        OccurrenceRecord("r4", "lisa.docx", "bb", 2),  # same bytes as r2 under a new occurrence
        OccurrenceRecord("r5", "uus.xlsx", "dd", 3),
    ]
    out = classify_resources(before, after)
    assert out == {
        "r1": ResourceChange.UNCHANGED,
        "r3": ResourceChange.POSSIBLE_REPLACEMENT_REVIEW,
        "r4": ResourceChange.SAME_BYTES_NEW_OCCURRENCE,
        "r5": ResourceChange.NEW_OCCURRENCE,
        "absent:r2": ResourceChange.OCCURRENCE_ABSENT,
    }
    assert classify_resources(before, before) == {
        "r1": ResourceChange.UNCHANGED,
        "r2": ResourceChange.UNCHANGED,
    }


def test_a_section_move_reissues_block_ids_but_the_occurrence_carries_over():
    """D2 (disposable notebook, 2026-11-04): moving a page to another section kept the
    page GUID and every block creation time, and re-issued every block object id."""
    t1, t2 = "2026-01-05T09:00:00.000Z", "2026-01-06T10:00:00.000Z"
    before = [
        OccurrenceRecord("{AAAA}{39}{B0}", "otsus.pdf", "aa", 0, block_created=t1),
        OccurrenceRecord("{AAAA}{48}{B0}", "lisa.docx", "bb", 1, block_created=t2),
    ]
    after = [  # same page after the move: new ids, same block times
        OccurrenceRecord("{BBBB}{21}{B0}", "otsus.pdf", "aa", 0, block_created=t1),
        OccurrenceRecord("{BBBB}{25}{B0}", "lisa.docx", "b2", 1, block_created=t2),  # edited too
    ]
    assert classify_resources(before, after) == {
        "{BBBB}{21}{B0}": ResourceChange.SAME_BLOCK_NEW_ID,
        "{BBBB}{25}{B0}": ResourceChange.SAME_BLOCK_NEW_ID_CONTENT_CHANGED,
    }  # and nothing is reported absent


def test_block_continuity_never_matches_an_ambiguous_or_unknown_block():
    t = "2026-01-05T09:00:00.000Z"
    twins = [
        OccurrenceRecord("o1", "lisa.pdf", "aa", 0, block_created=t),
        OccurrenceRecord("o2", "lisa.pdf", "bb", 1, block_created=t),
    ]
    moved = [
        OccurrenceRecord("n1", "lisa.pdf", "aa", 0, block_created=t),
        OccurrenceRecord("n2", "lisa.pdf", "bb", 1, block_created=t),
    ]
    out = classify_resources(twins, moved)
    assert ResourceChange.SAME_BLOCK_NEW_ID not in out.values()
    assert out["absent:o1"] is ResourceChange.OCCURRENCE_ABSENT
    unknown = classify_resources(
        [OccurrenceRecord("o1", "x.pdf", "aa", 0)], [OccurrenceRecord("n1", "x.pdf", "zz", 0)]
    )
    assert unknown == {
        "n1": ResourceChange.POSSIBLE_REPLACEMENT_REVIEW,
        "absent:o1": ResourceChange.OCCURRENCE_ABSENT,
    }
    renamed = classify_resources(
        [OccurrenceRecord("o1", "x.pdf", "aa", 0, block_created=t)],
        [OccurrenceRecord("n1", "y.pdf", "aa", 0, block_created=t)],
    )
    assert renamed["n1"] is ResourceChange.SAME_BYTES_NEW_OCCURRENCE  # name differs: no carry


# -- identity: move versus copy (modelled provider behaviour) ---------------------------------


def test_move_with_same_id_is_one_identity_and_copy_with_same_bytes_is_not():
    reg = IdentityRegistry()
    pid = ProviderId("GRAPH", "nb", "1-abc!1")
    _, page = reg.classify(ObservedPage(pid, "https://…/A.one…", "Teema", "sha-x"))
    moved, again = reg.classify(
        ObservedPage(pid, "https://…/ARHIIV.one…", "Teema (arhiveeritud)", "sha-y")
    )
    assert moved is Continuity.SAME_IDENTITY and again is page
    copy_kind, copy = reg.classify(
        ObservedPage(
            ProviderId("GRAPH", "nb", "1-def!1"), "https://…/ARHIIV.one…", "Teema", "sha-x"
        )
    )
    assert copy_kind is Continuity.NEW_OR_COPIED and copy is not page


def test_changed_provider_id_needs_accepted_evidence():
    old, new = ProviderId("GRAPH", "nb", "1-old"), ProviderId("GRAPH", "nb", "1-new")
    weak = ContinuityEvidence(old, new, "TITLE_MATCH", "skript", "t")
    reg = IdentityRegistry([weak])
    reg.classify(ObservedPage(old, "u", "T"))
    assert reg.classify(ObservedPage(new, "u2", "T"))[0] is Continuity.NEW_OR_COPIED
    reg = IdentityRegistry([ContinuityEvidence(old, new, "REVIEWER_DECISION", "jurist", "t")])
    _, first = reg.classify(ObservedPage(old, "u", "T"))
    kind, second = reg.classify(ObservedPage(new, "u2", "T"))
    assert kind is Continuity.CHANGED_ID_WITH_EVIDENCE and second is first


def test_locator_history_is_append_only_and_ignores_identical_repeats():
    hist = LocatorHistory()
    obs = LocatorObservation(None, "https://a", "2026-11-04T00:00:00Z", "lovable-snapshot:x")
    hist.append(obs)
    hist.append(obs)
    hist.append(LocatorObservation(None, "https://b", "2026-11-05T00:00:00Z", "lovable-snapshot:y"))
    assert len(hist) == 2
    assert not hasattr(hist, "remove") and not hasattr(hist, "update")


def test_access_loss_is_held_never_deleted_and_blocks_promotion():
    t = AccessTracker()
    assert t.observe(status=200, listed=True, capture_id="c1") is AccessState.AVAILABLE
    assert t.observe(status=403, listed=False) is AccessState.RETRY_PENDING
    assert not t.may_promote
    t.observe(status=403, listed=False)
    assert t.observe(status=403, listed=False) is AccessState.ACCESS_LOST_REVIEW
    assert t.last_good_capture == "c1"
    t2 = AccessTracker()
    for _ in range(3):
        state = t2.observe(status=404, listed=False)
    assert state is AccessState.NOT_FOUND_REVIEW
    assert t2.observe(status=200, listed=True) is AccessState.AVAILABLE


# -- pagination, scans and checkpoints ------------------------------------------------------------

PAGES = "https://graph.microsoft.com/v1.0/sites/s/onenote/sections/x/pages"


def test_listing_beyond_one_response_completes_only_at_the_end_of_the_chain():
    chain = ListingChain(PAGES)
    nxt = chain.accept(
        {
            "value": [{"id": f"p{n}"} for n in range(100)],
            "@odata.nextLink": PAGES + "?$skiptoken=AAA",
        }
    )
    assert nxt == PAGES + "?$skiptoken=AAA" and not chain.complete
    assert chain.accept({"value": [{"id": "p100"}]}) is None
    assert chain.complete and len(chain.ids) == 101


def test_lost_continuation_off_scope_and_looping_links_leave_the_chain_incomplete():
    lost = ListingChain(PAGES)
    lost.accept({"value": [{"id": "a"}], "@odata.nextLink": PAGES + "?$skiptoken=1"})
    lost.abandon()
    assert not lost.complete

    off = ListingChain(PAGES)
    assert off.accept({"value": [], "@odata.nextLink": "https://evil.invalid/pages?x"}) is None
    assert not off.complete

    loop = ListingChain(PAGES)
    loop.accept({"value": [{"id": "a"}], "@odata.nextLink": PAGES + "?t=1"})
    loop.accept({"value": [{"id": "b"}], "@odata.nextLink": PAGES + "?t=1"})
    assert not loop.complete

    failed = ListingChain(PAGES)
    failed.accept(None, status=503)
    assert not failed.complete


def test_mid_enumeration_edits_show_up_as_scan_differences():
    """A page edited during the listing can move across response boundaries (date sort)."""
    first = ListingChain(PAGES)
    first.accept({"value": [{"id": "a"}, {"id": "b"}], "@odata.nextLink": PAGES + "?t=1"})
    first.accept({"value": [{"id": "b"}, {"id": "d"}]})  # 'b' edited and re-sorted; 'c' skipped
    assert not first.complete  # duplicate id across responses
    diff = reconcile_scans(first.ids, ["a", "b", "c", "d"])
    assert diff["only_second"] == {"c"}


def test_watermark_never_passes_an_unresolved_capture_or_incomplete_listing():
    now = datetime(2026, 11, 4, tzinfo=UTC)
    cp = ScopeCheckpoint("sec-1", complete_through=now - timedelta(days=1))
    held = advance(cp, ScopeScanResult(listing_complete=False, max_modified_seen=now), now=now)
    assert held.complete_through == cp.complete_through and held.gaps
    failed_at = now - timedelta(hours=5)
    partial = advance(
        cp,
        ScopeScanResult(True, now, failed={"p9": failed_at}),
        now=now,
    )
    assert partial.complete_through < failed_at
    assert any("p9" in gap for gap in partial.gaps)
    resumed = listing_window_start(partial)
    assert resumed is not None and resumed == partial.complete_through - OVERLAP
    assert listing_window_start(ScopeCheckpoint("new")) is None  # never read: read everything


def test_priority_signals_cannot_starve_full_reconciliation_and_periodic_runs_without_lovable():
    priority = [Job(JobKind.PRIORITY, f"p{n}", "new locator") for n in range(50)]
    periodic = [Job(JobKind.PERIODIC, f"s{n}") for n in range(5)]
    full = [Job(JobKind.FULL, f"f{n}") for n in range(10)]
    chosen, _ = plan(priority, periodic, full, budget=20)
    assert sum(j.kind is JobKind.FULL for j in chosen) >= 5
    quiet, _ = plan([], periodic, full, budget=20)  # no Lovable change at all
    assert sum(j.kind is JobKind.PERIODIC for j in quiet) == 5


def test_retry_after_is_bounded_and_zero_change_is_not_completeness():
    assert retry_after_seconds("7", attempt=0) == 7
    assert retry_after_seconds("99999", attempt=0) == 300
    assert retry_after_seconds(None, attempt=3) == 8
    assert (
        run_outcome(auth_ok=True, scopes_planned=10, scopes_complete=9, gaps=0)
        is RunOutcome.PARTIAL
    )
    assert (
        run_outcome(auth_ok=False, scopes_planned=10, scopes_complete=10, gaps=0)
        is RunOutcome.AUTH_FAILED
    )
    assert (
        run_outcome(auth_ok=True, scopes_planned=0, scopes_complete=0, gaps=0) is RunOutcome.FAILED
    )
    assert (
        run_outcome(auth_ok=True, scopes_planned=3, scopes_complete=3, gaps=0)
        is RunOutcome.RUN_COMPLETE
    )


# -- transport: GET only, scoped, no redirects, bounded --------------------------


class FakeHandle(io.BytesIO):
    def __init__(self, url: str, status: int, body: bytes, headers: dict[str, str]):
        super().__init__(body)
        self.status = status
        self._url = url
        msg = Message()
        for key, value in headers.items():
            msg[key] = value
        self.headers = msg

    def geturl(self) -> str:
        return self._url


class RecordingOpener:
    def __init__(self, script):
        self.script = list(script)
        self.requests: list[urllib.request.Request] = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        status, body, headers = self.script.pop(0)
        if status >= 300:
            msg = Message()
            for key, value in headers.items():
                msg[key] = value
            raise urllib.error.HTTPError(request.full_url, status, "x", msg, io.BytesIO(body))
        return FakeHandle(request.full_url, status, body, headers)


def transport(opener, **kw) -> ScopedGraphTransport:
    return ScopedGraphTransport(
        site_id="s",
        token_source=lambda: "synthetic-token",
        opener=opener,
        sleep=lambda _s: None,
        **kw,
    )


def test_transport_is_get_only_and_refuses_urls_outside_the_site_scope():
    opener = RecordingOpener([(200, b"{}", {})])
    t = transport(opener)
    t.get("https://graph.microsoft.com/v1.0/sites/s/onenote/notebooks")
    assert [r.get_method() for r in opener.requests] == ["GET"]
    for bad in (
        "https://graph.microsoft.com/v1.0/sites/other/onenote/pages",
        "https://graph.microsoft.com/v1.0/me/onenote/pages",
        "https://graph.microsoft.com/v1.0/sites/s/drive/root",
        "https://evil.invalid/v1.0/sites/s/onenote/pages",
        "http://graph.microsoft.com/v1.0/sites/s/onenote/pages",
    ):
        with pytest.raises(ScopeViolation):
            t.get(bad)
    assert len(opener.requests) == 1  # nothing off-scope was ever sent, token included
    public = {name for name in dir(ScopedGraphTransport) if not name.startswith("_")}
    assert not public & {"post", "patch", "put", "delete", "request"}


def test_transport_honours_retry_after_does_not_follow_redirects_and_caps_size():
    opener = RecordingOpener([(429, b"", {"Retry-After": "2"}), (200, b'{"value": []}', {})])
    slept: list[float] = []
    t = ScopedGraphTransport(
        site_id="s", token_source=lambda: "t", opener=opener, sleep=slept.append
    )
    assert t.get("https://graph.microsoft.com/v1.0/sites/s/onenote/pages").status == 200
    assert slept == [2.0]

    redirect = RecordingOpener([(302, b"", {"Location": "https://evil.invalid/steal"})])
    r = transport(redirect).get("https://graph.microsoft.com/v1.0/sites/s/onenote/pages")
    assert r.status == 302 and len(redirect.requests) == 1

    big = RecordingOpener([(200, b"x" * 11, {})])
    with pytest.raises(TooLarge):
        transport(big, max_bytes=10).get("https://graph.microsoft.com/v1.0/sites/s/onenote/pages")

    unauthorised = RecordingOpener([(401, b'{"error": {}}', {})])
    assert (
        transport(unauthorised).get("https://graph.microsoft.com/v1.0/sites/s/onenote/pages").status
        == 401
    )
    assert len(unauthorised.requests) == 1  # auth failure is not retried into a lockout

    for req in opener.requests:
        assert uuid.UUID(req.get_header("Client-request-id"))  # fresh per request


def test_the_package_cannot_write_juristid_data():
    """No Django, no app models, no ORM: the prototype has no path to a production write."""
    root = pathlib.Path(__file__).resolve().parents[1] / "tools" / "onenote_ingress"
    imported: set[str] = set()
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
    assert not imported & {"django", "app", "psycopg", "psycopg2", "msal", "requests"}


# -- regressions from the independent review ------------------------------------------


def test_dot_segments_and_the_legacy_onenote_host_never_get_a_token():
    walk = "https://graph.microsoft.com/v1.0/sites/s/onenote/resources/a/../../../../x/$value"
    assert check_resource_url(walk, site_scope_path="/v1.0/sites/s")[0] is not UrlVerdict.OK
    legacy = "https://www.onenote.com/v1.0/sites/s/onenote/resources/r/$value"
    assert check_resource_url(legacy, site_scope_path="/v1.0/sites/s")[0] is not UrlVerdict.OK
    opener = RecordingOpener([])
    t = transport(opener)
    for bad in (walk, legacy, "https://graph.microsoft.com/v1.0/sites/s/onenote/./pages"):
        with pytest.raises(ScopeViolation):
            t.get(bad)
    assert opener.requests == []


class _RedirectingHTTPS(urllib.request.BaseHandler):
    handler_order = 100  # before the stock HTTPSHandler: nothing leaves the process

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def https_open(self, req):
        self.calls.append((req.full_url, req.get_header("Authorization")))
        headers = Message()
        headers["Location"] = "https://evil.invalid/steal"
        response = urllib.response.addinfourl(io.BytesIO(b""), headers, req.full_url, 302)
        response.msg = "Found"  # the reason phrase HTTPErrorProcessor reads
        return response


def test_the_default_opener_does_not_follow_a_redirect_with_the_token():
    from tools.onenote_ingress.transport import default_opener

    fake = _RedirectingHTTPS()
    opener = default_opener()
    opener.add_handler(fake)
    url = "https://graph.microsoft.com/v1.0/sites/s/onenote/pages"
    response = transport(opener).get(url)
    assert response.status == 302
    assert [call[0] for call in fake.calls] == [url]  # evil.invalid was never contacted


def test_a_response_from_another_url_is_refused_even_if_an_opener_followed_it():
    class Following(RecordingOpener):
        def open(self, request, timeout=None):
            self.requests.append(request)
            return FakeHandle("https://evil.invalid/x", 200, b"{}", {})

    with pytest.raises(ScopeViolation):
        transport(Following([])).get("https://graph.microsoft.com/v1.0/sites/s/onenote/pages")


def test_aliases_are_checked_like_any_other_resolution():
    from tools.onenote_ingress.resolution import ReviewedAlias

    inv = _inventory()
    by_guid = inv.pages_by_guid()
    page_two = by_guid[g(2)][0].object_id
    section_link = DOC + wd(f"target(A.one|{g(1000)}/)")
    other_nb = doc_link("A.one", g(1000), "Teine", g(2), notebook=OTHER_NOTEBOOK)
    conflicting = doc_link("A.one", g(1000), "Ühine leht", g(1))
    aliases = {
        section_link: ReviewedAlias(section_link, page_two, "jurist", "t", "üle vaadatud"),
        other_nb: ReviewedAlias(other_nb, page_two, "jurist", "t", "x"),
        conflicting: ReviewedAlias(conflicting, page_two, "jurist", "t", "x"),
        "gone": ReviewedAlias("gone", "{P-missing}", "jurist", "t", "x"),
    }
    members = [member(1, section_link), member(2, other_nb), member(3, conflicting)]
    res = resolve_cohort(members, inv, SCOPE, aliases=aliases)
    assert [r.state for r in res] == [
        LinkState.VERIFIED_ALIAS,  # a container link a reviewer pinned to one page
        LinkState.OUT_OF_SCOPE,  # an alias cannot pull another notebook into scope
        LinkState.AMBIGUOUS,  # an alias may not silently override an exact GUID
    ]
    assert "ALIAS_CONFLICTS_GUID" in res[2].notes
    missing = resolve_cohort(
        [member(4, doc_link("A.one", g(1000), "x", g(1)))],
        inv,
        SCOPE,
        aliases={doc_link("A.one", g(1000), "x", g(1)): aliases["gone"]},
    )[0]
    assert missing.state is LinkState.EXACT_PAGE and "ALIAS_TARGET_MISSING" in missing.notes


def test_absence_from_an_inventory_with_identity_gaps_is_not_page_not_found():
    xml, links = hierarchy({"A.one": [(g(1), "T", 1), (g(2), "U", 1)]})
    del links["{P-" + g(2) + "}"]
    inv = from_desktop_hierarchy(xml, links, read_started="", read_finished="")
    res = resolve_cohort([member(1, doc_link("A.one", g(1000), "U", g(2)))], inv, SCOPE)[0]
    assert res.state is LinkState.INVENTORY_INCOMPLETE


def test_a_client_only_link_into_another_notebook_is_out_of_scope():
    inside = client_link("A.one", g(1000), g(1))
    outside = inside.replace(
        urllib.parse.quote(NB_PATH), urllib.parse.quote(f"{SITE}/SiteAssets/Muu")
    )
    res = resolve_cohort([member(1, inside), member(2, outside)], _inventory(), SCOPE)
    assert [r.state for r in res] == [LinkState.EXACT_PAGE, LinkState.OUT_OF_SCOPE]


def test_accounting_fails_on_missing_extra_or_duplicate_topics():
    members = [member(1, ""), member(2, "")]
    res = resolve_cohort(members, _inventory(), SCOPE)
    assert totals(res, expected_source_ids=[m.source_id for m in members]).accounted
    assert not totals([], expected_source_ids=[]).accounted
    assert not totals(res[:1], expected_source_ids=[m.source_id for m in members]).accounted
    assert not totals(res + res[:1], expected_source_ids=[m.source_id for m in members]).accounted


def test_unrequested_pages_keep_their_state_and_unlisted_readable_pages_are_their_own_state():
    t = AccessTracker()
    t.observe(status=200, listed=True, capture_id="c1")
    for _ in range(5):
        assert t.observe(status=None, listed=True) is AccessState.AVAILABLE
    assert t.observe(status=200, listed=False) is AccessState.UNLISTED_BUT_READABLE
    assert not t.may_promote


def test_duplicate_resource_ids_and_unread_bytes_are_never_collapsed_or_matched():
    previous = [OccurrenceRecord("r1", "a.pdf", "", 0)]
    current = [
        OccurrenceRecord("r2", "b.pdf", "", 0),
        OccurrenceRecord("r3", "c.pdf", "x", 1),
        OccurrenceRecord("r3", "c.pdf", "x", 2),
    ]
    out = classify_resources(previous, current)
    assert out["r2"] is ResourceChange.NEW_OCCURRENCE  # "" is not known bytes
    assert out["duplicate-current:r3"] is ResourceChange.DUPLICATE_RESOURCE_ID


def test_the_full_cursor_reaches_every_slot_under_constant_priority_load():
    full = [Job(JobKind.FULL, f"f{n}") for n in range(17)]
    priority = [Job(JobKind.PRIORITY, f"p{n}") for n in range(100)]
    seen: set[str] = set()
    cursor = 0
    for _ in range(4):  # ceil(17 / 5) runs
        chosen, cursor = plan(priority, [], full, budget=20, full_cursor=cursor)
        seen.update(job.scope for job in chosen if job.kind is JobKind.FULL)
    assert seen == {job.scope for job in full}


def test_a_failure_older_than_the_watermark_stays_in_the_retry_window():
    now = datetime(2026, 11, 4, tzinfo=UTC)
    cp = ScopeCheckpoint("s", complete_through=now - timedelta(hours=1))
    old_failure = now - timedelta(days=10)
    after = advance(cp, ScopeScanResult(True, now, failed={"p1": old_failure}), now=now)
    assert after.retry == {"p1": old_failure}
    assert listing_window_start(after) == old_failure - OVERLAP
    fixed = advance(after, ScopeScanResult(True, now, resolved=["p1"]), now=now)
    assert fixed.retry == {} and not fixed.has_open_work
