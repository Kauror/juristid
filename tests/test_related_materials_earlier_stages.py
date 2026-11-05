"""«Seotud materjalid» lists every readable earlier stage of a Teema (docs/adr/0209).

`Varasemad etapid` is the continuation history under the chosen materials:
every Matter whose `superseded_by` chain reaches this one, through every branch,
in this reader's scope (`historical_predecessors`). It is shown, never edited.
A predecessor that somebody also related by hand is one row carrying both
bases, and its `×` withdraws the manual relation only — `superseded_by` is not
something this section can change.

The section is drawn on the Matter page, by the HTMX fragment and the write
routes that re-render it, by the overview re-renders, and by the no-script
page; every one of them says the same thing.
"""

from __future__ import annotations

import re
from itertools import pairwise
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.documents.services import add_evidence_version
from app.matters.continuations import MAX_HOPS
from app.matters.models import Matter
from app.matters.services import create_matter
from app.related_materials import services
from app.related_materials.models import MatterBackgroundMaterial, MatterRelation
from app.related_materials.selectors import related_materials_for
from app.submissions.enums import SubmissionStatus
from app.workflow.enums import Disposition
from app.workflow.models import StageVocabulary
from tests import factories

pytestmark = pytest.mark.django_db

SECTION = "related_materials:section"
HX = {"HTTP_HX_REQUEST": "true"}


# ---------------------------------------------------------------------------
# Builders and readers
# ---------------------------------------------------------------------------


def _teema(ref: str, owner: Any, title: str | None = None) -> Matter:
    year, number = (int(part) for part in ref.split("_"))
    return factories.MatterFactory(
        owner=owner,
        title=title or f"Teema {ref}",
        reference_year=year,
        reference_number=number,
    )


def _continues(earlier: Matter, later: Matter) -> None:
    Matter.all_objects.filter(pk=earlier.pk).update(superseded_by=later)


def _chain(owner: Any, *refs: str) -> list[Matter]:
    matters = [_teema(ref, owner) for ref in refs]
    for earlier, later in pairwise(matters):
        _continues(earlier, later)
    return [Matter.all_objects.get(pk=m.pk) for m in matters]


def _close(matter: Matter) -> None:
    Matter.all_objects.filter(pk=matter.pk).update(
        is_open=False, closed_at=timezone.now(), disposition=Disposition.SUPERSEDED
    )


def _fresh(matter: Matter) -> Matter:
    return Matter.all_objects.get(pk=matter.pk)


def _page(client: Any, matter: Matter) -> str:
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


def _fragment(client: Any, matter: Matter) -> str:
    response = client.get(reverse(SECTION, kwargs={"pk": matter.pk}), **HX)
    assert response.status_code == 200
    return response.content.decode()


def _noscript(client: Any, matter: Matter) -> str:
    response = client.get(reverse(SECTION, kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    body = response.content.decode()
    assert "<title>" in body  # the page of its own, not the fragment
    return body


def _section(body: str) -> str:
    start = body.index('id="seotud-materjalid"')
    return body[start : body.index("</section>", start)]


def _stages(body: str) -> str:
    """The `Varasemad etapid` list, or "" when the section has none."""
    section = _section(body)
    if "data-related-stages>" not in section:
        return ""
    start = section.index("data-related-stages>")
    # The CSRF token is masked afresh on every render; nothing else may differ.
    return re.sub(
        r'name="csrfmiddlewaretoken" value="[^"]+"',
        'name="csrfmiddlewaretoken"',
        section[start : section.index("</ul>", start)],
    )


def _confirmed(body: str) -> str:
    section = _section(body)
    if "data-related-confirmed" not in section:
        return ""
    start = section.index("data-related-confirmed")
    return section[start : section.index("</ul>", start)]


def _count(body: str) -> int | None:
    match = re.search(
        r'Seotud materjalid <span class="sectionlabel__count">(\d+)</span>', _section(body)
    )
    return int(match.group(1)) if match else None


def _stage_refs(body: str) -> list[str]:
    """The references of the stage rows, in the order they render."""
    return re.findall(
        r'class="factrow__title factrow__matter" href="[^"]+">([0-9]+_[0-9]+) — ', _stages(body)
    )


def _seotud_card(body: str) -> str:
    start = body.find('<span class="railcard__label">Seotud</span>')
    return body[start : body.find("</div>\n  ", start)] if start >= 0 else ""


def _squash(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _link(matter: Matter) -> str:
    return f'href="/teemad/{matter.pk}/"'


def _sent_opinion(matter: Matter, title: str) -> Any:
    document = factories.DocumentFactory(matter=matter)
    version = add_evidence_version(
        document=document,
        content=b"%PDF-1.4\n" + title.encode("utf-8"),
        original_filename="arvamus.pdf",
        mime_type="application/pdf",
    )
    return factories.SubmissionFactory(
        matter=matter,
        title=title,
        status=SubmissionStatus.SENT,
        sent_at=timezone.now(),
        final_version=version,
    )


# ---------------------------------------------------------------------------
# The owner's examples
# ---------------------------------------------------------------------------


def test_one_predecessor_is_listed_with_its_reference_title_link_and_state(signed_in, specialist):
    old = _teema("2025_140", specialist, "Pakendiseaduse muutmine")
    new = _teema("2025_196", specialist, "Pakendiseaduse muutmine II")
    _continues(old, new)
    _close(old)

    body = _page(signed_in, _fresh(new))
    stages = _stages(body)

    assert "Varasemad etapid" in _section(body)
    assert _stage_refs(body) == ["2025_140"]
    assert "2025_140 — Pakendiseaduse muutmine</a>" in stages
    assert _link(old) in stages
    assert "suletud" in _squash(stages)
    # Not editable: no control at all on a stage row nobody related by hand.
    assert "<form" not in stages and "×" not in stages
    assert _count(body) == 1


def test_the_endpoint_of_a_three_stage_chain_lists_both_earlier_stages(signed_in, specialist):
    first, middle, last = _chain(specialist, "2025_10", "2026_55", "2026_190")
    body = _page(signed_in, last)
    assert _stage_refs(body) == ["2025_10", "2026_55"]  # earliest first
    for stage in (first, middle):
        assert _link(stage) in _stages(body)
    assert _count(body) == 2
    # The middle stage lists only its own history, not what came after it.
    assert _stage_refs(_page(signed_in, middle)) == ["2025_10"]


def test_converging_predecessors_both_appear(signed_in, specialist):
    target = _teema("2026_149", specialist)
    a = _teema("2022_74", specialist)
    b = _teema("2026_88", specialist)
    _continues(a, target)
    _continues(b, target)

    body = _page(signed_in, target)

    assert _stage_refs(body) == ["2022_74", "2026_88"]
    assert _count(body) == 2


def test_a_four_stage_chain_lists_every_earlier_stage(signed_in, specialist):
    chain = _chain(specialist, "2023_1", "2024_2", "2025_3", "2026_4")
    body = _page(signed_in, chain[-1])
    assert _stage_refs(body) == ["2023_1", "2024_2", "2025_3"]
    assert _count(body) == 3


def test_a_teema_without_history_has_no_stages_group(signed_in, specialist):
    body = _page(signed_in, _teema("2026_400", specialist))
    assert "Varasemad etapid" not in _section(body)
    assert _count(body) is None
    assert "relatedmaterials--compact" in body


# ---------------------------------------------------------------------------
# Manual relations beside and on top of the history
# ---------------------------------------------------------------------------


def test_a_manual_relation_and_a_predecessor_are_two_rows(signed_in, specialist):
    _old, new = _chain(specialist, "2025_140", "2025_196")
    other = _teema("2024_9", specialist, "Jäätmeseaduse muutmine")
    services.link_related_matters(matter=new, other=other, actor=specialist)

    body = _page(signed_in, _fresh(new))

    assert ">Jäätmeseaduse muutmine</a>" in _confirmed(body)
    assert 'aria-label="Eemalda seos: Jäätmeseaduse muutmine"' in _confirmed(body)
    assert _stage_refs(body) == ["2025_140"]
    assert "seotud ka käsitsi" not in _stages(body)
    assert _count(body) == 2


def test_a_predecessor_also_related_by_hand_is_one_row_with_both_bases(signed_in, specialist):
    old, new = _chain(specialist, "2025_140", "2025_196")
    services.link_related_matters(matter=new, other=old, actor=specialist)

    body = _page(signed_in, _fresh(new))
    stages = _stages(body)

    # One row, under the history, and not a second one in the chosen list.
    assert _stage_refs(body) == ["2025_140"]
    assert _confirmed(body) == ""
    assert _section(body).count(_link(old)) == 1
    assert "seotud ka käsitsi" in _squash(stages)
    assert _count(body) == 1
    # Both relationships are still in the database.
    assert MatterRelation.objects.count() == 1
    assert _fresh(old).superseded_by_id == new.pk
    # The one control there is withdraws the manual relation, named as such.
    assert stages.count("<form") == 1
    assert reverse("related_materials:unlink", kwargs={"pk": new.pk}) in stages
    assert f'name="teema" value="{old.pk}"' in stages
    assert 'aria-label="Eemalda käsitsi lisatud seos: Teema 2025_140"' in stages
    # Nothing on the section posts anywhere that could touch the continuation.
    for route in re.findall(r'action="([^"]+)"', _section(body)):
        assert "/seotud/" in route


def test_removing_the_manual_relation_through_the_route_keeps_the_continuation(
    signed_in, specialist
):
    old, new = _chain(specialist, "2025_140", "2025_196")
    services.link_related_matters(matter=new, other=old, actor=specialist)
    unlink = reverse("related_materials:unlink", kwargs={"pk": new.pk})

    response = signed_in.post(unlink, {"teema": str(old.pk), "peidetud": "0", "piir": "5"}, **HX)

    assert response.status_code == 200
    body = response.content.decode()
    assert "Seos on eemaldatud." in body
    assert MatterRelation.objects.count() == 0
    assert _fresh(old).superseded_by_id == new.pk
    # The stage is still listed, now without the manual basis or its control.
    assert _stage_refs(body) == ["2025_140"]
    assert "seotud ka käsitsi" not in _stages(body)
    assert "<form" not in _stages(body)
    assert _count(body) == 1

    # And once more, with nothing manual left to remove: the continuation stays.
    response = signed_in.post(unlink, {"teema": str(old.pk)})
    assert response.status_code == 302
    assert _fresh(old).superseded_by_id == new.pk
    assert _stage_refs(_page(signed_in, _fresh(new))) == ["2025_140"]


def test_a_predecessor_without_any_manual_material_makes_the_section_not_empty(specialist):
    old, new = _chain(specialist, "2025_140", "2025_196")
    materials = related_materials_for(new, specialist)
    assert not materials.is_empty
    assert materials.count == 1
    assert materials.relations == () and materials.background == ()
    assert [item.matter for item in materials.stages] == [old]


def test_the_selector_counts_a_doubly_related_predecessor_once(specialist):
    first, middle, last = _chain(specialist, "2025_10", "2026_55", "2026_190")
    other = _teema("2024_9", specialist)
    services.link_related_matters(matter=last, other=first, actor=specialist)
    services.link_related_matters(matter=last, other=other, actor=specialist)

    materials = related_materials_for(last, specialist)

    assert {item.other for item in materials.relations} == {first, other}
    assert [item.other for item in materials.manual_rows] == [other]
    assert [(item.matter, item.is_also_manual) for item in materials.stages] == [
        (first, True),
        (middle, False),
    ]
    assert materials.count == 3


def test_a_reader_sees_the_stages_and_the_manual_basis_but_no_control(client, specialist, reader):
    old, new = _chain(specialist, "2025_140", "2025_196")
    services.link_related_matters(matter=new, other=old, actor=specialist)
    client.force_login(reader)

    body = _page(client, _fresh(new))

    assert _stage_refs(body) == ["2025_140"]
    assert "seotud ka käsitsi" in _squash(_stages(body))
    assert "<form" not in _stages(body)


# ---------------------------------------------------------------------------
# What this reader may not read
# ---------------------------------------------------------------------------


def test_an_outsider_reads_no_stage_and_no_count(client, specialist, administrator):
    _old, new = _chain(specialist, "2025_140", "2025_196")
    materials = related_materials_for(new, administrator)
    assert materials.stages == () and materials.is_empty and materials.count == 0

    client.force_login(administrator)
    for url in (
        reverse("matters:matter_detail", kwargs={"pk": new.pk}),
        reverse(SECTION, kwargs={"pk": new.pk}),
    ):
        response = client.get(url)
        assert response.status_code == 404
        assert "2025_140" not in response.content.decode()


def test_a_deleted_middle_stage_is_no_row_no_link_and_no_error(signed_in, specialist):
    first, middle, last = _chain(specialist, "2024_1", "2025_2", "2026_3")
    Matter.all_objects.filter(pk=middle.pk).update(deleted_at=timezone.now())

    body = _page(signed_in, last)

    assert "Varasemad etapid" not in _section(body)
    for gone in (first, middle):
        assert _link(gone) not in _section(body)
    assert _count(body) is None
    assert _fragment(signed_in, last).count("Varasemad etapid") == 0


def test_a_cycle_ends_the_list_with_one_honest_note(signed_in, specialist):
    a, b = _chain(specialist, "2025_1", "2026_2")
    _continues(b, a)

    body = _page(signed_in, _fresh(b))

    assert _stage_refs(body) == ["2025_1"]
    assert 'data-related-stages-note="cyclic"' in body
    assert "Ajalugu jõuab tagasi juba näidatud teemani, kaugemale seda ei näidata." in body
    assert _count(body) == 1


def test_a_history_beyond_the_bound_is_cut_and_says_so_without_a_count(signed_in, specialist):
    refs = [f"{2000 + n}_{n + 1}" for n in range(MAX_HOPS + 3)]
    chain = _chain(specialist, *refs)

    body = _page(signed_in, chain[-1])

    assert len(_stage_refs(body)) == MAX_HOPS
    assert _stage_refs(body)[-1] == refs[-2]  # the nearest stages are the ones shown
    assert refs[0] not in _stages(body)
    note = "Ajalugu on pikem, kui siin näidatakse; näidatud on lähimad etapid."
    assert note in body
    assert _count(body) == MAX_HOPS


# ---------------------------------------------------------------------------
# Every surface that draws the section says the same thing
# ---------------------------------------------------------------------------


def test_the_page_the_fragment_and_the_noscript_page_agree(signed_in, specialist):
    target = _teema("2026_149", specialist)
    a = _teema("2022_74", specialist)
    b = _teema("2026_88", specialist)
    _continues(a, target)
    _continues(b, target)
    services.link_related_matters(matter=target, other=b, actor=specialist)
    target = _fresh(target)

    bodies = {
        "page": _page(signed_in, target),
        "fragment": _fragment(signed_in, target),
        "noscript": _noscript(signed_in, target),
        "suggestions open": signed_in.get(
            reverse(SECTION, kwargs={"pk": target.pk}) + "?avatud=1", **HX
        ).content.decode(),
    }
    expected = _stages(bodies["page"])
    assert expected
    for name, body in bodies.items():
        assert _stages(body) == expected, name
        assert _stage_refs(body) == ["2022_74", "2026_88"], name
        assert _count(body) == 2, name
    assert "<title>" not in bodies["fragment"]


def test_a_write_route_re_renders_the_stages(signed_in, specialist):
    _old, new = _chain(specialist, "2025_140", "2025_196")
    other = _teema("2024_9", specialist)

    response = signed_in.post(
        reverse("related_materials:link", kwargs={"pk": new.pk}), {"teema": str(other.pk)}, **HX
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert _stage_refs(body) == ["2025_140"]
    assert _count(body) == 2

    # The no-script form lands back on the Matter page, which lists them too.
    response = signed_in.post(
        reverse("related_materials:unlink", kwargs={"pk": new.pk}), {"teema": str(other.pk)}
    )
    assert response.status_code == 302
    assert response["Location"].endswith("#seotud-materjalid")
    assert _stage_refs(_page(signed_in, _fresh(new))) == ["2025_140"]


def test_an_overview_re_render_carries_the_stages(signed_in, specialist):
    stage = StageVocabulary.objects.get(key="consultation")
    matter = create_matter(
        title="Pakendiseaduse muudatus", actor=specialist, owner=specialist, stage=stage
    )
    earlier = _teema("2024_77", specialist)
    _continues(earlier, matter)

    response = signed_in.post(
        reverse("matters:close_from_workspace", kwargs={"pk": matter.pk}),
        {"disposition": Disposition.MONITORING_STOPPED.value},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert _stage_refs(body) == ["2024_77"]
    assert _link(earlier) in _stages(body)


# ---------------------------------------------------------------------------
# The rail's `Seotud` card does not say the predecessor a second time
# ---------------------------------------------------------------------------


def test_the_seotud_card_keeps_the_successor_and_drops_the_predecessor_rows(signed_in, specialist):
    _first, middle, last = _chain(specialist, "2025_10", "2026_55", "2026_190")

    body = _page(signed_in, middle)
    card = _squash(_seotud_card(body))

    assert "Jätkub teemas 2026_190" in card
    assert "Eelnev teema" not in card
    assert _stage_refs(body) == ["2025_10"]
    # The header line is untouched by this round.
    assert "Teema etapid" in body

    # A Teema with only history and no successor or collaborator has no card.
    assert _seotud_card(_page(signed_in, last)) == ""


# ---------------------------------------------------------------------------
# Background materials and the query budget
# ---------------------------------------------------------------------------


def test_background_material_is_unaffected_by_the_history(signed_in, specialist):
    _old, new = _chain(specialist, "2025_140", "2025_196")
    source = _teema("2024_955", specialist)
    opinion = _sent_opinion(source, "Koja arvamus pakendiseaduse kohta")
    services.add_background_submission(matter=new, submission=opinion, actor=specialist)

    body = _page(signed_in, _fresh(new))

    assert "Koja arvamus pakendiseaduse kohta" in _confirmed(body)
    assert "data-related-background" in _confirmed(body)
    assert _stage_refs(body) == ["2025_140"]
    assert _count(body) == 2
    assert MatterBackgroundMaterial.objects.filter(matter=new).count() == 1


def _section_queries(client: Any, matter: Matter) -> int:
    with CaptureQueriesContext(connection) as captured:
        _fragment(client, matter)
    return len(captured.captured_queries)


def test_six_converging_stages_cost_no_more_queries_than_one(signed_in, specialist):
    one = _teema("2026_501", specialist)
    _continues(_teema("2025_1", specialist), one)
    six = _teema("2026_502", specialist)
    for number in range(10, 16):
        _continues(_teema(f"2025_{number}", specialist), six)

    assert len(related_materials_for(six, specialist).stages) == 6
    _section_queries(signed_in, one)  # warm whatever is cached per process
    assert _section_queries(signed_in, six) == _section_queries(signed_in, one)


def test_a_six_deep_history_costs_one_query_per_level_and_none_per_row(signed_in, specialist):
    one = _chain(specialist, "2025_1", "2026_1")[-1]
    six = _chain(specialist, "2020_7", "2021_7", "2022_7", "2023_7", "2024_7", "2025_7", "2026_7")[
        -1
    ]
    _section_queries(signed_in, one)
    # The walk asks once per level (app/matters/continuations.py); rendering a
    # row asks nothing. Six levels against one is five more queries at most.
    assert _section_queries(signed_in, six) - _section_queries(signed_in, one) <= 5
