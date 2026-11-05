"""Independent audit of the continuation / related-materials round (PR #599, 2026-11-05).

Adversarial tests written against 96bb8542 by a reviewer who did not write the
change. Synthetic data only. They attack:

* `historical_predecessors` on branching and converging histories, behind a
  deleted stage, under the all-or-nothing read policy (docs/adr/0181: a grant
  reads everything, RESTRICTED included; no grant reads nothing), and at its
  two bounds;
* `continuation_component_ids` (the suggestion exclusion) through a deleted stage;
* «Seotud materjalid»: dedup, count, every control, and the closed-Matter rule
  on every surface that draws the section;
* `continuation_lines` against the Teema page's own `continuations_for` at and
  around `MAX_HOPS`, and past a deleted step;
* the MCP payload behind a deleted stage;
* HEAD on the byte-serving routes.
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

from app.core.enums import Visibility
from app.matters.continuation_lines import UNNAMED, continuation_lines
from app.matters.continuations import (
    MAX_HISTORY,
    MAX_HOPS,
    continuation_component_ids,
    continuations_for,
    historical_predecessors,
)
from app.matters.models import Matter
from app.mcp_server import readers
from app.related_materials import engine, services
from app.related_materials.models import MatterRelation
from app.related_materials.selectors import related_materials_for
from app.workflow.enums import Disposition
from tests import factories
from tests.break_glass import grant_to

pytestmark = pytest.mark.django_db

HX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture(autouse=True)
def public_origin(settings):
    settings.JURISTID_PUBLIC_ORIGIN = "https://juristid.example.test"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _teema(ref: str, owner: Any, **fields: Any) -> Matter:
    year, number = (int(part) for part in ref.split("_"))
    fields.setdefault("title", f"Auditi teema {ref}")
    return factories.MatterFactory(
        owner=owner, reference_year=year, reference_number=number, **fields
    )


def _continues(earlier: Matter, later: Matter, *, close: bool = True) -> None:
    values: dict[str, Any] = {"superseded_by": later}
    if close:
        values.update(is_open=False, closed_at=timezone.now(), disposition=Disposition.SUPERSEDED)
    Matter.all_objects.filter(pk=earlier.pk).update(**values)


def _fresh(matter: Matter) -> Matter:
    return Matter.all_objects.get(pk=matter.pk)


def _refs(stages: Any) -> list[tuple[str, int]]:
    return [(s.matter.display_reference, s.depth) for s in stages]


def _section(body: str) -> str:
    start = body.index('id="seotud-materjalid"')
    return body[start : body.index("</section>", start)]


# ---------------------------------------------------------------------------
# 1. historical_predecessors
# ---------------------------------------------------------------------------


def test_branching_and_converging_history_is_complete_and_ordered(specialist):
    """2020_1 ─┐
    2020_2 ─┴→ 2021_3 ─┐
            2021_4 ←── 2020_5   (2020_5 → 2021_4)
                  └──────┴→ 2022_6 (viewed)
    """
    a1, a2, b, d, e, c = (
        _teema(ref, specialist)
        for ref in ("2020_1", "2020_2", "2021_3", "2021_4", "2020_5", "2022_6")
    )
    for earlier, later in ((a1, b), (a2, b), (b, c), (d, c), (e, d)):
        _continues(earlier, later)

    history = historical_predecessors(_fresh(c), specialist)

    assert _refs(history.stages) == [
        ("2020_1", 2),
        ("2020_2", 2),
        ("2020_5", 2),
        ("2021_3", 1),
        ("2021_4", 1),
    ]
    assert not history.cyclic and not history.truncated
    # A branch's own history holds only its own branch, never its sibling's.
    assert _refs(historical_predecessors(_fresh(b), specialist).stages) == [
        ("2020_1", 1),
        ("2020_2", 1),
    ]
    assert historical_predecessors(_fresh(e), specialist).stages == ()


def test_the_read_policy_is_all_or_nothing_for_the_history(specialist, administrator):
    """docs/adr/0181: a business reader or a grant holder reads every live stage,
    RESTRICTED included; an administrator without a grant reads none, and is told
    no count, in the section, the MCP payload and the list line alike."""
    first = _teema("2020_11", specialist)
    restricted = _teema("2021_12", specialist, visibility=Visibility.RESTRICTED)
    side = _teema("2021_13", specialist)
    viewed = _teema("2022_14", specialist)
    _continues(first, restricted)
    _continues(restricted, viewed)
    _continues(side, viewed)

    assert historical_predecessors(_fresh(viewed), administrator).stages == ()
    materials = related_materials_for(_fresh(viewed), administrator)
    assert materials.is_empty and materials.count == 0
    assert continuation_lines([_fresh(side)], administrator)[side.pk] is UNNAMED

    granted = grant_to(administrator)
    assert _refs(historical_predecessors(_fresh(viewed), granted).stages) == [
        ("2020_11", 2),
        ("2021_12", 1),
        ("2021_13", 1),
    ]
    payload = readers.matter_snapshot(granted, viewed.display_reference)["continuations"]
    assert [item["depth"] for item in payload["historical_predecessors"]] == [2, 1, 1]
    assert len(historical_predecessors(_fresh(viewed), specialist)) == 3


def test_a_deleted_stage_hides_everything_behind_it_in_every_reader(specialist):
    """The one stage a live reader may not read is a deleted one (docs/adr/0102)."""
    first = _teema("2020_15", specialist)
    deleted = _teema("2021_16", specialist)
    side = _teema("2021_17", specialist)
    viewed = _teema("2022_18", specialist)
    _continues(first, deleted)
    _continues(deleted, viewed)
    _continues(side, viewed)
    Matter.all_objects.filter(pk=deleted.pk).update(deleted_at=timezone.now())

    history = historical_predecessors(_fresh(viewed), specialist)
    assert _refs(history.stages) == [("2021_17", 1)]
    assert history.truncated is False and history.cyclic is False

    payload = readers.matter_snapshot(specialist, viewed.display_reference)["continuations"]
    listed = repr(payload["historical_predecessors"])
    for gone in (deleted, first):
        assert str(gone.pk) not in listed
        assert gone.display_reference not in listed
    text = readers.fetch_source(specialist, f"matter:{viewed.pk}")["text"]
    assert "2020_15" not in text and "2021_16" not in text
    assert related_materials_for(_fresh(viewed), specialist).count == 1


def test_the_width_bound_cuts_without_reading_further_levels(specialist):
    viewed = _teema("2026_900", specialist)
    direct = [_teema(f"2025_{n}", specialist) for n in range(1, MAX_HISTORY + 2)]
    for matter in direct:
        _continues(matter, viewed)
    for n, matter in enumerate(direct[:3]):
        _continues(_teema(f"2024_{n + 1}", specialist), matter)

    with CaptureQueriesContext(connection) as captured:
        history = historical_predecessors(_fresh(viewed), specialist)

    assert len(history) == MAX_HISTORY
    assert {s.depth for s in history.stages} == {1}
    assert history.truncated is True
    # The nearest stages are shown: the 61st reference (by order) is the cut one.
    assert direct[-1].pk not in {s.matter.pk for s in history.stages}
    assert len(captured.captured_queries) <= 3


def test_a_history_exactly_at_the_depth_bound_lists_every_stage(specialist):
    chain = [_teema(f"{2000 + n}_{n + 1}", specialist) for n in range(MAX_HOPS + 1)]
    for earlier, later in pairwise(chain):
        _continues(earlier, later)

    history = historical_predecessors(_fresh(chain[-1]), specialist)

    assert len(history) == MAX_HOPS
    assert history.truncated is False


def test_a_cycle_behind_the_successor_cannot_trap_any_walk(specialist):
    """M → X ⇄ Y. The cycle does not run through M; every walk must still end."""
    m, x, y = (_teema(ref, specialist) for ref in ("2025_31", "2025_32", "2025_33"))
    _continues(m, x)
    _continues(x, y)
    _continues(y, x)

    assert historical_predecessors(_fresh(m), specialist).stages == ()
    assert continuation_component_ids(m.pk) == {m.pk, x.pk, y.pk}
    chain = continuations_for(_fresh(m), specialist)
    assert chain.cyclic is True and not chain.endpoint_is_known
    line = continuation_lines([_fresh(m)], specialist)[m.pk]
    assert line.successor == x and line.endpoint is None


# ---------------------------------------------------------------------------
# 3. Suggestions
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-D-02: continuation_component_ids follows predecessors of a deleted successor",
)
def test_the_suggestion_exclusion_does_not_walk_through_a_deleted_stage(specialist):
    """`continuation_component_ids` says «every live Matter». A deleted stage
    (docs/adr/0102) is not live, and the history behind it is not reached by
    `historical_predecessors`; the exclusion walk should stop there as well.

    a → d (deleted) ← b: b is on no history this page shows for a.
    """
    a = _teema("2025_41", specialist, title="Jäätmeseaduse muutmine A")
    d = _teema("2025_42", specialist, title="Jäätmeseaduse muutmine D")
    b = _teema("2025_43", specialist, title="Jäätmeseaduse muutmine B")
    _continues(a, d)
    _continues(b, d)
    Matter.all_objects.filter(pk=d.pk).update(deleted_at=timezone.now())

    component = continuation_component_ids(a.pk)
    assert b.pk not in component


def test_a_genuine_lookalike_outside_the_history_is_still_suggested(specialist):
    first = _teema("2024_51", specialist, title="Jäätmeseaduse muutmine, etapp 1")
    second = _teema("2025_52", specialist, title="Jäätmeseaduse muutmine, etapp 2")
    outsider = _teema("2025_53", specialist, title="Jäätmeseaduse muutmine, eraldi eelnõu")
    _continues(first, second)

    suggested = {i.matter.pk for i in engine.suggestions_for(_fresh(second), specialist).matters}

    assert first.pk not in suggested
    assert outsider.pk in suggested


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-D-03: hidden_candidates/hidden_count do not exclude the continuation history",
)
def test_a_dismissed_stage_is_not_offered_back_under_hidden_suggestions(signed_in, specialist):
    """A candidate dismissed before it became a stage of the history.

    «Näita peidetud» offers «Taasta soovitus» for it, but a restored stage can
    never be suggested again (it is excluded as history), so the restore button
    does something nobody can see.
    """
    viewed = _teema("2025_62", specialist, title="Pakendiseaduse muutmine, etapp 2")
    earlier = _teema("2024_61", specialist, title="Pakendiseaduse muutmine, etapp 1")
    response = signed_in.post(
        reverse("related_materials:dismiss", kwargs={"pk": viewed.pk}),
        {"liik": "teema", "kandidaat": str(earlier.pk)},
        **HX,
    )
    assert response.status_code == 200
    _continues(earlier, viewed, close=False)

    found = engine.suggestions_for(_fresh(viewed), specialist, include_hidden=True)
    assert earlier.pk not in {item.matter.pk for item in found.hidden_matters}
    assert found.hidden_count == 0


# ---------------------------------------------------------------------------
# 2. «Seotud materjalid» controls
# ---------------------------------------------------------------------------


def test_no_route_in_the_section_can_change_the_continuation(signed_in, specialist):
    old = _teema("2025_71", specialist)
    new = _teema("2025_72", specialist)
    _continues(old, new, close=False)
    kwargs = {"pk": new.pk}
    posts = [
        ("related_materials:unlink", {"teema": str(old.pk)}),
        ("related_materials:link", {"teema": str(old.pk)}),
        ("related_materials:unlink", {"teema": str(old.pk)}),
        ("related_materials:dismiss", {"liik": "teema", "kandidaat": str(old.pk)}),
        ("related_materials:restore", {"liik": "teema", "kandidaat": str(old.pk)}),
    ]
    for name, data in posts:
        response = signed_in.post(reverse(name, kwargs=kwargs), data, **HX)
        assert response.status_code in (200, 400), (name, response.status_code)
        assert _fresh(old).superseded_by_id == new.pk, name
        assert [i.matter for i in related_materials_for(_fresh(new), specialist).stages] == [old]
    # And from the earlier stage's side, too.
    for name, data in posts:
        swapped = {k: (str(new.pk) if v == str(old.pk) else v) for k, v in data.items()}
        signed_in.post(reverse(name, kwargs={"pk": old.pk}), swapped, **HX)
        assert _fresh(old).superseded_by_id == new.pk, name


def test_a_reader_cannot_use_the_stage_row_remove_control(client, specialist, reader):
    old = _teema("2025_81", specialist)
    new = _teema("2025_82", specialist)
    _continues(old, new, close=False)
    services.link_related_matters(matter=new, other=old, actor=specialist)
    client.force_login(reader)

    response = client.post(
        reverse("related_materials:unlink", kwargs={"pk": new.pk}), {"teema": str(old.pk)}, **HX
    )

    assert response.status_code == 404
    assert MatterRelation.objects.count() == 1
    assert _fresh(old).superseded_by_id == new.pk


def test_the_stage_row_remove_control_is_csrf_guarded(specialist):
    from django.test import Client

    old = _teema("2025_91", specialist)
    new = _teema("2025_92", specialist)
    _continues(old, new, close=False)
    services.link_related_matters(matter=new, other=old, actor=specialist)
    client = Client(enforce_csrf_checks=True)
    client.force_login(specialist)

    response = client.post(
        reverse("related_materials:unlink", kwargs={"pk": new.pk}), {"teema": str(old.pk)}, **HX
    )

    assert response.status_code == 403
    assert MatterRelation.objects.count() == 1


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-D-01: the Matter page and the rail re-render pass can_write without is_open",
)
def test_a_closed_middle_stage_offers_no_remove_control_on_any_surface(signed_in, specialist):
    """The typical stage page: a continued Teema is closed by definition.

    `section_context` hides every write control on a closed Matter (R2-02 §9);
    the Matter page and the rail redrawn by an inline save must not offer the
    stage row's «Eemalda käsitsi lisatud seos» either, or the surfaces disagree.
    """
    first = _teema("2025_101", specialist)
    middle = _teema("2025_102", specialist)
    last = _teema("2025_103", specialist)
    _continues(first, middle, close=False)
    services.link_related_matters(matter=middle, other=first, actor=specialist)
    unrelated = _teema("2025_104", specialist)
    services.link_related_matters(matter=middle, other=unrelated, actor=specialist)
    _continues(middle, last)  # middle is now closed: its work continues as `last`
    middle = _fresh(middle)
    assert middle.is_open is False

    fragment = signed_in.get(
        reverse("related_materials:section", kwargs={"pk": middle.pk}), **HX
    ).content.decode()
    assert "Eemalda käsitsi lisatud seos" not in fragment
    assert "seotud ka käsitsi" in fragment

    page = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": middle.pk}))
    assert page.status_code == 200
    section = _section(page.content.decode())
    assert "seotud ka käsitsi" in section
    assert "Eemalda käsitsi lisatud seos" not in section
    # The same rule for an ordinary manual row and the «+ Lisa» disclosure.
    assert "Eemalda seos" not in section
    assert 'id="lisa-seotud-teema"' not in section

    # The rail redrawn by an inline save on the closed Teema says the same.
    from app.matters.services import matter_field_revision

    organisation = factories.OrganisationFactory(name="Auditiamet")
    response = signed_in.post(
        reverse("matters:update_field", kwargs={"pk": middle.pk, "field": "source_organisations"}),
        {
            "source_organisations": [str(organisation.pk)],
            "revision": matter_field_revision(middle, "source_organisations"),
        },
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    rail = _section(response.content.decode())
    assert "seotud ka käsitsi" in rail
    assert "Eemalda käsitsi lisatud seos" not in rail
    assert "Eemalda seos" not in rail


def test_the_rail_refusal_re_render_keeps_the_section(signed_in, specialist):
    """update_field's refusal branches use `_field_surface_context` as well."""
    old = _teema("2025_111", specialist)
    new = _teema("2025_112", specialist)
    _continues(old, new, close=False)

    response = signed_in.post(
        reverse("matters:update_field", kwargs={"pk": new.pk, "field": "source_organisations"}),
        {"source_organisations": ["not-a-uuid"], "revision": "stale"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code in (400, 409)
    body = response.content.decode()
    assert 'id="seotud-materjalid"' in body
    assert "Varasemad etapid" in body and old.display_reference in body
    # And the predecessor is said once on this rail, not twice.
    assert "Eelnev teema" not in body


def test_a_predecessor_two_levels_back_and_related_by_hand_is_one_row(signed_in, specialist):
    first = _teema("2024_121", specialist)
    middle = _teema("2025_122", specialist)
    last = _teema("2026_123", specialist)
    _continues(first, middle)
    _continues(middle, last)
    services.link_related_matters(matter=last, other=first, actor=specialist)
    # A manual relation to the successor side is not a stage and stays a manual row.
    later = _teema("2026_124", specialist)
    services.link_related_matters(matter=last, other=later, actor=specialist)

    materials = related_materials_for(_fresh(last), specialist)
    assert [i.other for i in materials.manual_rows] == [later]
    assert [(i.matter, i.is_also_manual) for i in materials.stages] == [
        (first, True),
        (middle, False),
    ]
    assert materials.count == 3

    section = _section(
        signed_in.get(reverse("matters:matter_detail", kwargs={"pk": last.pk})).content.decode()
    )
    assert section.count(f'href="/teemad/{first.pk}/"') == 1
    assert re.search(r'sectionlabel__count">3<', section)


# ---------------------------------------------------------------------------
# 4. List lines
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hops", [1, 2, MAX_HOPS - 1, MAX_HOPS, MAX_HOPS + 1, MAX_HOPS + 2])
def test_the_list_line_agrees_with_the_teema_page_about_the_endpoint(specialist, hops):
    chain = [_teema(f"{1990 + n}_{n + 1}", specialist) for n in range(hops + 1)]
    for earlier, later in pairwise(chain):
        _continues(earlier, later)
    row = _fresh(chain[0])

    line = continuation_lines([row], specialist)[row.pk]
    page = continuations_for(row, specialist)

    assert line.successor == page.successor
    if page.endpoint_is_known and page.endpoint != page.successor:
        assert line.endpoint == page.endpoint
        assert line.skipped == len(page.after) - 2
    else:
        assert line.endpoint is None


def test_the_list_line_never_names_past_a_deleted_step(specialist):
    gone_next = _teema("2025_131", specialist)
    row_a = _teema("2025_130", specialist)
    _continues(row_a, gone_next)

    shown_next = _teema("2025_133", specialist)
    gone_end = _teema("2025_134", specialist)
    row_b = _teema("2025_132", specialist)
    _continues(row_b, shown_next)
    _continues(shown_next, gone_end)
    Matter.all_objects.filter(pk__in=[gone_next.pk, gone_end.pk]).update(deleted_at=timezone.now())

    lines = continuation_lines([_fresh(row_a), _fresh(row_b)], specialist)

    assert lines[row_a.pk] is UNNAMED
    assert lines[row_b.pk].successor == shown_next
    assert lines[row_b.pk].endpoint is None


# ---------------------------------------------------------------------------
# 6. Byte-serving routes
# ---------------------------------------------------------------------------


def test_head_on_the_evidence_download_writes_an_audit_row_like_get(client, specialist):
    """HEAD stays allowed (require_safe). Recorded here, not changed: a HEAD
    reads the route and is audited as a download, as before this round."""
    from app.audit.enums import SecurityEventType
    from app.audit.models import SecurityAuditEvent
    from app.documents.services import add_evidence_version

    matter = factories.MatterFactory(owner=specialist)
    document = factories.DocumentFactory(matter=matter)
    version = add_evidence_version(
        document=document,
        content=b"%PDF-1.4\naudit-head",
        original_filename="audit.pdf",
        mime_type="application/pdf",
    )
    client.force_login(specialist)

    response = client.head(reverse("documents:download", kwargs={"pk": version.pk}))

    assert response.status_code == 200
    downloads = SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.DOCUMENT_DOWNLOADED
    ).count()
    assert downloads == 1
