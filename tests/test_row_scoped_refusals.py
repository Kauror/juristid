"""A refusal answers on the row it came from, and the header follows the save.

ENG-036: a refused `+ Lisa fail` on one `Märge` or `Väline seisukoht` put the
refused form into the page-wide context, so every row of that kind drew the
picker, with the refused record's id on each of them, and the sentence also
landed under PRAEGUNE TEGEVUS (and in a hidden add panel).

ENG-093: a refused `Salvesta avaldatuna` rebuilt the form with Django's default
ids, beside the other planned rows' own, so `id_url` and `id_published_on`
appeared twice.

ENG-091: a `Märge` that moved `Hetkeseis` answered with the column only, and
the header went on stating the old stage until a reload.

The browser half is `e2e/test_row_scoped_refusals.py`.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.matters.services import plan_website_overview, record_external_position
from app.matters.workspace import add_procedural_development
from tests import factories

pytestmark = pytest.mark.django_db

HX = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "teema-vaade"}
REFUSAL = "Vali vähemalt üks fail."


def _duplicate_ids(html: str) -> list[str]:
    counts = Counter(re.findall(r'\sid="([^"]+)"', html))
    return sorted(name for name, seen in counts.items() if seen > 1)


def _region(html: str, element_id: str) -> str:
    """The element with this id, up to the next element of the same family."""
    start = html.index(f'id="{element_id}"')
    following = re.search(
        r'id="(menetluse-areng|valine-seisukoht)-[0-9a-f-]{36}-sisu"', html[start + 10 :]
    )
    end = start + 10 + following.start() if following else len(html)
    return html[start:end]


def _pdf(name: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"%PDF-1.4 synthetic evidence", content_type="application/pdf")


@pytest.fixture
def developments(normal_matter, specialist):
    """Three steps, and an open task, so PRAEGUNE TEGEVUS has a form to wrongly print under."""
    return [
        add_procedural_development(
            matter=normal_matter,
            author=specialist,
            title=f"Samm {index}",
            occurred_on=timezone.localdate() - dt.timedelta(days=index),
            note="",
            next_text="Loe läbi" if index == 0 else "",
        ).record
        for index in range(3)
    ]


@pytest.fixture
def positions(normal_matter, specialist):
    organisation = factories.OrganisationFactory()
    return [
        record_external_position(
            matter=normal_matter,
            organisation=organisation,
            summary=f"Seisukoht {index}",
            stated_on=timezone.localdate() - dt.timedelta(days=index),
            actor=specialist,
        )
        for index in range(3)
    ]


def _development_evidence(matter, development) -> str:
    return reverse(
        "matters:add_development_evidence",
        kwargs={"pk": matter.pk, "development_id": development.pk},
    )


def _position_evidence(matter, position) -> str:
    return reverse(
        "matters:add_external_position_evidence",
        kwargs={"pk": matter.pk, "position_id": position.pk},
    )


# -- ENG-036 -----------------------------------------------------------------


def test_a_refused_development_file_reopens_only_its_own_row(
    signed_in, normal_matter, developments
):
    refused = developments[1]

    response = signed_in.post(_development_evidence(normal_matter, refused), {}, **HX)

    html = response.content.decode()
    assert response.status_code == 400
    assert html.count('aria-label="Faili lisamine märkele"') == 1
    row = _region(html, f"menetluse-areng-{refused.pk}-sisu")
    assert _development_evidence(normal_matter, refused) in row
    assert 'aria-label="Faili lisamine märkele"' in row
    assert html.count(REFUSAL) == 1
    assert REFUSAL in row
    assert _duplicate_ids(html) == []


def test_the_refusal_is_said_nowhere_but_its_row(signed_in, normal_matter, developments):
    response = signed_in.post(_development_evidence(normal_matter, developments[1]), {}, **HX)

    html = response.content.decode()
    task = html[html.index('id="praegune-tegevus"') : html.index('id="lisa-teemale"')]
    assert REFUSAL not in task
    assert 'class="formerror' not in html


def test_the_refused_picker_is_described_by_its_sentence(signed_in, normal_matter, developments):
    refused = developments[1]

    html = signed_in.post(_development_evidence(normal_matter, refused), {}, **HX).content.decode()

    control = re.search(r'<input[^>]*type="file"[^>]*toend_failid[^>]*>', html)
    assert control is not None
    tag = control.group(0)
    assert 'aria-invalid="true"' in tag
    described = re.search(r'aria-describedby="([^"]+)"', tag)
    assert described is not None
    for reference in described.group(1).split():
        paragraph = re.search(rf'id="{re.escape(reference)}"[^>]*>([^<]*)<', html)
        if paragraph and REFUSAL in paragraph.group(1):
            break
    else:
        pytest.fail(f"no described element says why: {described.group(1)}")


def test_a_service_refusal_lands_on_the_row_too(
    signed_in, normal_matter, developments, specialist, evidence_root
):
    """A closed Matter refuses in the service; the sentence still has one home."""
    from app.matters.services import close_matter
    from app.workflow.enums import Disposition

    refused = developments[2]
    close_matter(matter=normal_matter, disposition=Disposition.COMPLETED, actor=specialist)

    response = signed_in.post(
        _development_evidence(normal_matter, refused), {"attachments": [_pdf("a.pdf")]}, **HX
    )

    html = response.content.decode()
    assert response.status_code == 400
    assert html.count('aria-label="Faili lisamine märkele"') == 1
    assert 'aria-label="Faili lisamine märkele"' in _region(
        html, f"menetluse-areng-{refused.pk}-sisu"
    )
    assert _duplicate_ids(html) == []


def test_a_retry_on_the_refused_row_lands_on_that_row(signed_in, normal_matter, developments):
    from app.documents.models import DocumentLink

    refused = developments[1]
    signed_in.post(_development_evidence(normal_matter, refused), {}, **HX)

    response = signed_in.post(
        _development_evidence(normal_matter, refused), {"attachments": [_pdf("b.pdf")]}, **HX
    )

    assert response.status_code == 200
    assert DocumentLink.objects.filter(procedural_development=refused).count() == 1
    for other in (developments[0], developments[2]):
        assert not DocumentLink.objects.filter(procedural_development=other).exists()


def test_a_refused_position_file_reopens_only_its_own_row(signed_in, normal_matter, positions):
    refused = positions[1]

    response = signed_in.post(_position_evidence(normal_matter, refused), {}, **HX)

    html = response.content.decode()
    assert response.status_code == 400
    posts = re.findall(r'hx-post="([^"]*/valine-seisukoht/[^"]*/lisa-fail/)"', html)
    assert posts == [_position_evidence(normal_matter, refused)]
    assert REFUSAL in _region(html, f"valine-seisukoht-{refused.pk}-sisu")
    assert html.count(REFUSAL) == 1
    assert _duplicate_ids(html) == []


def test_opening_a_picker_still_draws_it_on_its_row(signed_in, normal_matter, developments):
    """The GET that opens the picker is the row alone, and it still has one."""
    response = signed_in.get(_development_evidence(normal_matter, developments[0]), **HX)

    html = response.content.decode()
    assert html.count('aria-label="Faili lisamine märkele"') == 1
    assert REFUSAL not in html


# -- ENG-093 -----------------------------------------------------------------


def test_a_refused_publish_keeps_every_id_on_its_own_row(signed_in, normal_matter):
    plans = [
        plan_website_overview(matter=normal_matter, actor=normal_matter.owner) for _ in range(2)
    ]
    refused = plans[1]

    response = signed_in.post(
        reverse(
            "matters:publish_website_overview",
            kwargs={"pk": normal_matter.pk, "overview_id": refused.pk},
        ),
        {"url": "pole aadress", "published_on": "14.3.2026"},
        **HX,
    )

    html = response.content.decode()
    assert response.status_code == 400
    assert _duplicate_ids(html) == []
    own = f"id_kodulehe_ulevaade_{refused.pk}_url"
    assert re.search(rf'<input[^>]*name="url"[^>]*id="{own}"', html) or re.search(
        rf'<input[^>]*id="{own}"[^>]*name="url"', html
    )
    assert 'value="pole aadress"' in html
    # The other plan keeps its own, unbound form.
    assert f"id_kodulehe_ulevaade_{plans[0].pk}_url" in html
    assert 'id="id_url"' not in html.split('id="kodulehe-ulevaated"')[1].split("</section>")[0]


# -- ENG-091 -----------------------------------------------------------------


def _marge(matter) -> str:
    return reverse("matters:add_note", kwargs={"pk": matter.pk})


def test_a_marge_that_moves_the_stage_answers_with_the_header_stage(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist, stage=None)
    stage = factories.StageFactory()

    response = signed_in.post(_marge(matter), {"stage": str(stage.pk)}, **HX)

    html = response.content.decode()
    matter.refresh_from_db()
    assert matter.stage_id == stage.pk
    fragment = html[html.index('id="teema-hetkeseis"') - 40 :]
    assert 'hx-swap-oob="true"' in fragment[:200]
    assert stage.label_et in fragment.split("</summary>")[0]
    assert re.search(rf'<option value="{stage.pk}"\s*selected', fragment)
    # One fragment, not the whole header: the other inline editors are left be.
    assert 'id="teema-pais"' not in html
    assert _duplicate_ids(html) == []


def test_the_header_after_a_reload_says_the_same(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist, stage=None)
    stage = factories.StageFactory()
    signed_in.post(_marge(matter), {"stage": str(stage.pk)}, **HX)

    page = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()

    header = page[page.index('id="teema-hetkeseis"') :].split("</summary>")[0]
    assert stage.label_et in header


def test_a_marge_with_a_file_answers_with_the_document_count(signed_in, specialist, evidence_root):
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        _marge(matter), {"title": "Eelnõu saabus", "attachments": [_pdf("e.pdf")]}, **HX
    )

    html = response.content.decode()
    tabs = html[html.index('id="teema-vaated"') - 40 :]
    assert 'hx-swap-oob="true"' in tabs[:200]
    assert "· 1" in tabs.split("</nav>")[0]


def test_a_closure_still_answers_with_the_whole_header_and_no_fragments(signed_in, specialist):
    """The closure keeps its full header; the fragments are not sent twice beside it."""
    from app.matters.views import _render_overview

    matter = factories.MatterFactory(owner=specialist)
    request = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).wsgi_request

    html = _render_overview(request, matter, header_out_of_band=True).content.decode()

    assert html.count('id="teema-pais"') == 1
    assert html.count('id="teema-hetkeseis"') == 1
    assert html.count('id="teema-vaated"') == 1
