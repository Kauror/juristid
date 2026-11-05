"""Agent E (independent verifier): QA-07 byte routes, QA-14/15/16/18 surfaces.

QA-07: every byte route answers HEAD with GET's status and *every* header GET
sends (not a chosen list), reads and audits nothing, and refuses exactly where
GET refuses — including where GET redirects, where the stored object has gone
(the page XML, not covered by the authors), and for a persona-less gate session.

QA-14: a refused «Seo teemaga» for a continuation stage writes nothing at all —
no relation, no change event, no audit row — by plain POST and by HTMX.

QA-15/18: accessible names and short titles carry hostile titles escaped.

QA-16: a closed Teema's rail does not repeat the successor its banner names.

Synthetic data only.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import SecurityEventType
from app.audit.models import ChangeEvent, SecurityAuditEvent
from app.matters.continuation_lines import continuation_lines
from app.matters.models import Matter
from app.related_materials.models import MatterRelation
from app.workflow.enums import Disposition
from tests import factories
from tests.test_download_methods import _source_page_link, _thumbnail

pytestmark = pytest.mark.django_db

HX = {"HTTP_HX_REQUEST": "true"}
PDF = b"%PDF-1.4\npa-e-head-8801\n"
#: Headers that legitimately differ between two requests.
VOLATILE = {"Date", "Set-Cookie", "X-Request-Id", "X-Correlation-Id", "Vary"}


@pytest.fixture(autouse=True)
def public_origin(settings):
    settings.JURISTID_PUBLIC_ORIGIN = "https://juristid.example.test"


def _downloads() -> int:
    return SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.DOCUMENT_DOWNLOADED
    ).count()


def _drain(response: Any) -> bytes:
    if getattr(response, "streaming", False):
        return b"".join(response.streaming_content)
    return bytes(response.content)


def _headers(response: Any) -> dict[str, str]:
    return {k: v for k, v in response.headers.items() if k not in VOLATILE}


@pytest.fixture
def evidence(capture_evidence, specialist):
    matter = factories.MatterFactory(owner=specialist)
    version = capture_evidence(matter, PDF, 'kiri "õigus";.pdf', "application/pdf")
    html = capture_evidence(matter, b"<html><script>x</script></html>", "leht.html", "text/html")
    return {
        "matter": matter,
        "download": reverse("documents:download", kwargs={"pk": version.pk}),
        "open": reverse("documents:open", kwargs={"pk": version.pk}),
        "thumbnail": reverse("documents:thumbnail", kwargs={"pk": _thumbnail(version).pk}),
        "source_xml": reverse(
            "legacy_import:source_xml", kwargs={"pk": _source_page_link(matter).pk}
        ),
        "open_unsafe": reverse("documents:open", kwargs={"pk": html.pk}),
    }


@pytest.mark.parametrize("route", ["download", "open", "thumbnail", "source_xml"])
def test_e_head_sends_every_header_get_sends_and_no_more(client, specialist, evidence, route):
    client.force_login(specialist)
    head = client.head(evidence[route])
    get = client.get(evidence[route])
    _drain(get)
    assert head.status_code == get.status_code == 200
    assert _headers(head) == _headers(get), route
    assert _drain(head) == b""


def test_e_head_on_an_unsafe_inline_open_redirects_like_get(client, specialist, evidence):
    client.force_login(specialist)
    before = _downloads()
    head = client.head(evidence["open_unsafe"])
    get = client.get(evidence["open_unsafe"])
    assert head.status_code == get.status_code == 302
    assert head["Location"] == get["Location"]
    assert _downloads() == before


def test_e_head_on_page_xml_whose_object_is_gone_is_404_and_audits_nothing(
    client, specialist, settings
):
    from app.legacy_import.historical_apply import legacy_source_storage

    link = _source_page_link(factories.MatterFactory(owner=specialist))
    legacy_source_storage().delete(link.source_page.source_xml_storage_key)
    url = reverse("legacy_import:source_xml", kwargs={"pk": link.pk})
    client.force_login(specialist)
    before = SecurityAuditEvent.objects.count()
    head = client.head(url)
    assert head.status_code == 404 and _drain(head) == b""
    assert SecurityAuditEvent.objects.count() == before
    get = client.get(url)
    assert get.status_code == 404
    assert SecurityAuditEvent.objects.count() == before + 1  # GET's failed row, as before


def test_e_head_and_get_refuse_a_personaless_gate_session_alike(settings, evidence):
    from tests.gate import PASSWORD, apply_shared_gate

    apply_shared_gate(settings)
    gate = Client()
    assert gate.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302
    for route in ("download", "open", "thumbnail", "source_xml"):
        before = _downloads()
        get, head = gate.get(evidence[route]), gate.head(evidence[route])
        assert head.status_code == get.status_code, route
        if get.status_code in (301, 302):
            assert head["Location"] == get["Location"], route
        assert _drain(head) == b""
        assert PDF not in _drain(get)
        assert _downloads() == before, route


def test_e_head_never_creates_a_storage_object(client, specialist, evidence, settings):
    """`exists()`/`size()` only: a HEAD on a missing thumbnail must not create it."""
    from pathlib import Path

    client.force_login(specialist)
    for path in Path(settings.DERIVATIVE_ROOT).rglob("*.png"):
        path.unlink()
    head = client.head(evidence["thumbnail"])
    assert head.status_code == 404
    assert not list(Path(settings.DERIVATIVE_ROOT).rglob("*.png"))


# -- QA-14 -----------------------------------------------------------------------------


def _continues(earlier: Matter, later: Matter) -> None:
    Matter.all_objects.filter(pk=earlier.pk).update(
        superseded_by=later,
        is_open=False,
        closed_at=timezone.now(),
        disposition=Disposition.SUPERSEDED,
    )


@pytest.mark.parametrize("hx", [True, False], ids=["htmx", "plain"])
@pytest.mark.parametrize("direction", ["earlier", "later"])
def test_e_a_refused_stage_link_writes_no_row_of_any_kind(client, specialist, hx, direction):
    a = factories.MatterFactory(owner=specialist, title="PAE etapp A")
    b = factories.MatterFactory(owner=specialist, title="PAE etapp B")
    c = factories.MatterFactory(owner=specialist, title="PAE etapp C")
    _continues(a, b)
    _continues(b, c)
    viewed, target = (c, a) if direction == "earlier" else (a, c)
    # `a` is closed: only the earlier-direction case is posted on an open Teema.
    Matter.all_objects.filter(pk=viewed.pk).update(is_open=True, closed_at=None, disposition="")
    client.force_login(specialist)
    counts = (
        MatterRelation.objects.count(),
        ChangeEvent.objects.count(),
        SecurityAuditEvent.objects.count(),
    )
    url = reverse("related_materials:link", kwargs={"pk": viewed.pk})
    response = client.post(url, {"teema": str(target.pk)}, **(HX if hx else {}))
    assert response.status_code == (400 if hx else 302)
    if not hx:
        follow = client.get(response["Location"])
        assert "eraldi seost ei lisatud" in follow.content.decode()
    assert (
        MatterRelation.objects.count(),
        ChangeEvent.objects.count(),
        SecurityAuditEvent.objects.count(),
    ) == counts
    assert dict(Matter.all_objects.values_list("pk", "superseded_by"))[a.pk] == b.pk


# -- QA-15 / QA-18: hostile titles stay text -------------------------------------------

HOSTILE = '"><img src=x onerror=alert(9)> PAE vaenulik'


def test_e_accessible_names_and_short_titles_escape_a_hostile_title(client, specialist):
    viewed = factories.MatterFactory(owner=specialist, title="PAE vaadatud")
    other = factories.MatterFactory(owner=specialist, title=HOSTILE)
    client.force_login(specialist)
    picker = client.get(
        reverse("related_materials:picker", kwargs={"pk": viewed.pk}), {"q": "vaenulik"}, **HX
    ).content.decode()
    assert "<img src=x" not in picker
    labels = re.findall(r'aria-label="([^"]*)"', picker)
    assert any("Seo teemaga" in label and "&lt;img" in label for label in labels), labels
    _continues(viewed, other)
    lines = continuation_lines([Matter.all_objects.get(pk=viewed.pk)], specialist)
    assert lines[viewed.pk].successor.pk == other.pk
    register = client.get(reverse("matters:matter_list") + "?olek=koik").content.decode()
    assert "<img src=x" not in register


# -- QA-16 -----------------------------------------------------------------------------


def _rail_seotud(body: str) -> str:
    start = body.find('<span class="railcard__label">Seotud</span>')
    return body[start : body.find("</div>\n  ", start)] if start >= 0 else ""


def test_e_closed_stage_says_its_successor_in_banner_and_header_not_rail(client, specialist):
    viewed = factories.MatterFactory(owner=specialist, title="PAE suletud etapp")
    successor = factories.MatterFactory(
        owner=specialist, title="PAE järgmine etapp", reference_year=2034, reference_number=7
    )
    _continues(viewed, successor)
    client.force_login(specialist)
    body = client.get(reverse("matters:matter_detail", kwargs={"pk": viewed.pk})).content.decode()
    banner = re.search(r'<div class="banner banner--closed">.*?</p>', body, re.DOTALL)
    assert banner is not None and "2034_7" in banner.group(0)
    assert "Jätkub teemas" not in _rail_seotud(body)
    # Reopened (no successor any more): nothing to say anywhere.
    Matter.all_objects.filter(pk=viewed.pk).update(
        is_open=True, closed_at=None, disposition="", superseded_by=None
    )
    body = client.get(reverse("matters:matter_detail", kwargs={"pk": viewed.pk})).content.decode()
    assert "2034_7" not in body
