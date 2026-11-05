"""The Teema page names where its work came from and where it continues (docs/adr/0207).

The header line under the title, the closed banner and the rail's `Seotud` card
all read one answer — `app.matters.continuations.continuations_for` through the
request's scope — so a stage this reader may not read (a deleted Teema, or any
Teema at all for an administrator without a grant) is said to exist and never
named. `get_matter` carries the same answer.
"""

from __future__ import annotations

import re
from itertools import pairwise

import pytest
from django.db import connection
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.matters.continuations import MAX_HOPS
from app.matters.models import Matter
from app.matters.services import close_matter, create_matter
from app.mcp_server import readers
from app.mcp_server.readers import NotFound
from app.workflow.enums import Disposition
from app.workflow.models import StageVocabulary
from tests import factories

pytestmark = pytest.mark.django_db

HIDDEN_SUCCESSOR = "Jätkub teemas, mida siit avada ei saa"
HIDDEN_PREDECESSOR = "Varasem etapp, mida siit avada ei saa"


def _chain(n: int, **fields) -> list[Matter]:
    """n Matters, each continuing as the next, with distinct references."""
    matters = [
        factories.MatterFactory(title=f"Etapp {index}", **fields) for index in range(1, n + 1)
    ]
    for earlier, later in pairwise(matters):
        Matter.all_objects.filter(pk=earlier.pk).update(superseded_by=later)
    return [Matter.all_objects.get(pk=m.pk) for m in matters]


def _page(client, matter) -> str:
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


def _link(matter) -> str:
    return f'href="/teemad/{matter.pk}/"'


def _nav(body: str) -> str:
    """The header's continuation line only."""
    match = re.search(r'<nav class="matterhead__continuation".*?</nav>', body, re.DOTALL)
    return match.group(0) if match else ""


def _banner(body: str) -> str:
    match = re.search(r'<div class="banner banner--closed">.*?</p>', body, re.DOTALL)
    return match.group(0) if match else ""


def _seotud(body: str) -> str:
    start = body.find('<span class="railcard__label">Seotud</span>')
    return body[start : body.find("</div>\n  ", start)] if start >= 0 else ""


def _squash(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


# ---------------------------------------------------------------------------
# One step each way
# ---------------------------------------------------------------------------


def test_a_closed_teema_names_its_successor_in_the_header_banner_and_rail(signed_in, specialist):
    old = factories.MatterFactory(owner=specialist, title="Vana pakendiseadus")
    new = factories.MatterFactory(owner=specialist, title="Uus pakendiseaduse eelnõu")
    close_matter(matter=old, disposition=Disposition.SUPERSEDED, actor=specialist, successor=new)

    body = _page(signed_in, old)
    reference = new.display_reference

    nav = _nav(body)
    assert f"Jätkub teemas {reference}" in _squash(nav)
    assert _link(new) in nav
    assert "Jätkub mujal" in body  # the badge stays; the line says where

    banner = _banner(body)
    assert f"Teema on suletud. Jätkub teemas {reference} (Uus pakendiseaduse eelnõu)" in (
        _squash(banner)
    )
    assert _link(new) in banner
    assert "Asendatud järgnenud teemaga" not in banner

    # The rail does not say it a third time (owner, QA-16).
    assert "Jätkub teemas" not in _squash(_seotud(body))


def test_the_successor_names_its_one_predecessor(signed_in, specialist):
    old, new = _chain(2, owner=specialist)
    body = _page(signed_in, new)
    nav = _nav(body)
    assert f"Eelnev teema: {old.display_reference}" in _squash(nav)
    assert _link(old) in nav
    assert "Jätkub teemas" not in _squash(nav)
    # The rail names the predecessor once, under `Seotud materjalid` →
    # `Varasemad etapid`, and not again in the `Seotud` card (docs/adr/0209).
    assert f"{old.display_reference} — Etapp 1" in _squash(body)
    assert "Eelnev teema" not in _squash(_seotud(body))


def test_several_predecessors_are_an_accessible_list(signed_in, specialist):
    a, b, target = (factories.MatterFactory(owner=specialist) for _ in range(3))
    Matter.all_objects.filter(pk__in=[a.pk, b.pk]).update(superseded_by=target)

    nav = _nav(_page(signed_in, target))

    assert "Eelnevad teemad:" in nav
    items = re.findall(r"<li><a [^>]*>([^<]+)</a></li>", nav)
    assert sorted(items) == sorted([a.display_reference, b.display_reference])
    assert _link(a) in nav and _link(b) in nav


def test_a_teema_with_no_continuation_draws_no_line(signed_in, specialist):
    body = _page(signed_in, factories.MatterFactory(owner=specialist))
    assert "matterhead__continuation" not in body
    assert '<span class="railcard__label">Seotud</span>' not in body


# ---------------------------------------------------------------------------
# Chains
# ---------------------------------------------------------------------------


def test_a_three_stage_chain_prints_every_stage_in_order_from_any_stage(signed_in, specialist):
    first, middle, last = _chain(3, owner=specialist)
    references = [m.display_reference for m in (first, middle, last)]

    for current in (first, middle, last):
        nav = _nav(_page(signed_in, current))
        stages = re.findall(r'<li class="chainpath__stage[^"]*"[^>]*>(.*?)</li>', nav, re.DOTALL)
        assert [_squash(stage).replace(" (see teema)", "") for stage in stages] == references
        # The current stage is marked and is not a link; every other is.
        current_item = re.search(r'<li [^>]*aria-current="page"[^>]*>(.*?)</li>', nav).group(1)
        assert current.display_reference in current_item and "<a " not in current_item
        assert _link(current) not in nav
        for other in {first, middle, last} - {current}:
            assert _link(other) in nav
        assert "Teema etapid" in nav
        # The path replaces the two neighbour sentences; nothing is said twice.
        assert "Jätkub teemas" not in _squash(nav) and "Eelnev teema:" not in nav


def test_the_endpoint_is_named_by_direction_and_says_whether_it_is_open(signed_in, specialist):
    first, middle, last = _chain(3, owner=specialist)
    nav = _squash(_nav(_page(signed_in, first)))
    assert f"viimane etapp {last.display_reference} on avatud" in nav

    close_matter(matter=last, disposition=Disposition.MONITORING_STOPPED, actor=specialist)
    nav = _squash(_nav(_page(signed_in, middle)))
    assert f"viimane etapp {last.display_reference} on suletud" in nav
    # At the endpoint itself there is nothing further to name.
    assert "viimane etapp" not in _squash(_nav(_page(signed_in, last)))


def test_the_historical_stage_is_not_redirected(signed_in, specialist):
    first, _middle, _last = _chain(3, owner=specialist)
    response = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": first.pk}))
    assert response.status_code == 200 and response.wsgi_request.path == f"/teemad/{first.pk}/"


def test_a_cycle_is_noted_and_never_walked_forever(signed_in, specialist):
    a, _b, c = _chain(3, owner=specialist)
    Matter.all_objects.filter(pk=c.pk).update(superseded_by=a)
    nav = _squash(_nav(_page(signed_in, a)))
    assert "Ahel jõuab tagasi juba näidatud teemani" in nav
    assert "viimane etapp" not in nav  # a cycle has no endpoint to name


def test_a_chain_longer_than_the_bound_is_cut_and_says_so(signed_in, specialist):
    chain = _chain(MAX_HOPS + 2, owner=specialist)
    nav = _squash(_nav(_page(signed_in, chain[0])))
    assert f"Ahel on pikem kui {MAX_HOPS} etappi, näidatud on lähimad." in nav
    assert chain[-1].display_reference not in nav.split()
    assert "viimane etapp" not in nav


# ---------------------------------------------------------------------------
# What a reader may not read
# ---------------------------------------------------------------------------


def test_a_deleted_successor_is_said_to_exist_and_never_named(signed_in, specialist):
    old = factories.MatterFactory(owner=specialist)
    new = factories.MatterFactory(owner=specialist, title="Kustutatud jätkuteema")
    close_matter(matter=old, disposition=Disposition.SUPERSEDED, actor=specialist, successor=new)
    Matter.all_objects.filter(pk=new.pk).update(deleted_at=timezone.now())

    body = _page(signed_in, old)

    assert HIDDEN_SUCCESSOR in _squash(_nav(body))
    assert f"Teema on suletud. {HIDDEN_SUCCESSOR}" in _squash(_banner(body))
    assert "Jätkub teemas, mida siit avada ei saa" in _squash(_seotud(body))
    assert "Kustutatud jätkuteema" not in body
    assert new.display_reference not in body
    assert f"/teemad/{new.pk}/" not in body


def test_a_hidden_middle_stage_ends_the_path_unnamed(signed_in, specialist):
    first, middle, last = _chain(3, owner=specialist)
    Matter.all_objects.filter(pk=middle.pk).update(deleted_at=timezone.now())

    forward = _page(signed_in, first)
    assert HIDDEN_SUCCESSOR in _squash(_nav(forward))
    back = _page(signed_in, last)
    # A deleted predecessor is not history this page offers (continuations.py).
    assert "matterhead__continuation" not in back
    for body in (forward, back):
        assert middle.display_reference not in body and "Etapp 2" not in body


def _render_for(user, template: str, matter) -> str:
    request = RequestFactory().get(f"/teemad/{matter.pk}/")
    request.user = user
    context = {"matter": matter, "request": request, "owners": [], "colleagues": []}
    return render_to_string(template, context, request=request)


def test_an_outsider_administrator_reads_no_reference_at_all(client, specialist, administrator):
    """The page itself is a 404 for an administrator without a grant (docs/adr/0181);
    and were a partial drawn for one, neither end of the chain would be named — nor
    even flagged: a reader who may not read the Matter learns nothing about its
    chain (QA-06; this test asserted the «may not see» flags before)."""
    old = factories.MatterFactory(owner=specialist, title="Varasem teema 81")
    new = factories.MatterFactory(owner=specialist, title="Hilisem teema 82")
    close_matter(matter=old, disposition=Disposition.SUPERSEDED, actor=specialist, successor=new)
    old.refresh_from_db()

    client.force_login(administrator)
    for matter in (old, new):
        response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
        assert response.status_code == 404
        assert old.display_reference not in response.content.decode()
        assert new.display_reference not in response.content.decode()

    banner = _render_for(administrator, "matters/partials/matter_banner.html", old)
    assert HIDDEN_SUCCESSOR not in _squash(banner)
    assert new.display_reference not in banner and "Hilisem teema 82" not in banner
    new_header = _render_for(administrator, "matters/partials/header.html", new)
    assert HIDDEN_PREDECESSOR not in _squash(new_header)
    assert old.display_reference not in new_header.replace(new.display_reference, "")
    assert "Varasem teema 81" not in new_header


# ---------------------------------------------------------------------------
# HTMX re-renders that carry the rail and the header
# ---------------------------------------------------------------------------


def test_the_rail_re_rendered_by_an_inline_save_keeps_seotud(signed_in, specialist):
    from app.matters.services import matter_field_revision

    old, new = _chain(2, owner=specialist)
    organisation = factories.OrganisationFactory(name="Kliimaministeerium")
    response = signed_in.post(
        reverse("matters:update_field", kwargs={"pk": new.pk, "field": "source_organisations"}),
        {
            "source_organisations": [str(organisation.pk)],
            "revision": matter_field_revision(new, "source_organisations"),
        },
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    body = response.content.decode()
    # The rail redrawn by the save carries «Seotud materjalid» too (ADR 0209), so the
    # predecessor is listed there under «Varasemad etapid», as on a full load.
    assert "Varasemad etapid" in body and old.display_reference in body


def test_a_closure_re_renders_the_header_banner_and_rail_with_the_chain(signed_in, specialist):
    owner = specialist
    stage = StageVocabulary.objects.get(key="consultation")
    earlier = factories.MatterFactory(owner=owner)
    matter = create_matter(title="Pakendiseaduse muudatus", actor=owner, owner=owner, stage=stage)
    Matter.all_objects.filter(pk=earlier.pk).update(superseded_by=matter)

    response = signed_in.post(
        reverse("matters:close_from_workspace", kwargs={"pk": matter.pk}),
        {"disposition": Disposition.MONITORING_STOPPED.value},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert 'id="teema-pais"' in body  # the header, out of band
    assert f"Eelnev teema: {earlier.display_reference}" in _squash(_nav(body))
    assert "Teema on suletud." in _squash(_banner(body))
    # Under `Varasemad etapid` in the re-rendered section, not twice (docs/adr/0209).
    assert f"{earlier.display_reference} — " in _squash(body)
    assert "Eelnev teema" not in _squash(_seotud(body))


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


def test_the_teema_page_walks_the_chain_once_per_request(signed_in, specialist):
    lone = factories.MatterFactory(owner=specialist)
    with CaptureQueriesContext(connection) as plain:
        _page(signed_in, lone)

    _first, middle, _last = _chain(3, owner=specialist)
    Matter.all_objects.filter(pk=middle.pk).update(
        is_open=False, closed_at=timezone.now(), disposition=Disposition.SUPERSEDED
    )
    with CaptureQueriesContext(connection) as chained:
        _page(signed_in, middle)

    # Header, banner and rail share one walk: a three-stage chain costs a
    # handful of queries more than no chain, not three walks' worth.
    assert len(chained.captured_queries) - len(plain.captured_queries) <= 8


def test_the_teema_page_query_count_is_bounded(
    signed_in, specialist, django_assert_max_num_queries
):
    chain = _chain(6, owner=specialist)
    lone = factories.MatterFactory(owner=specialist)
    with CaptureQueriesContext(connection) as plain:
        _page(signed_in, lone)
    with django_assert_max_num_queries(len(plain.captured_queries) + 2 * (MAX_HOPS + 2)):
        _page(signed_in, chain[2])


# ---------------------------------------------------------------------------
# get_matter
# ---------------------------------------------------------------------------


@pytest.fixture
def public_origin(settings):
    settings.JURISTID_PUBLIC_ORIGIN = "https://juristid.example.test"


def test_get_matter_names_the_readable_successor_predecessors_and_endpoint(
    public_origin, specialist
):
    first, middle, last = _chain(3, owner=specialist)
    snapshot = readers.matter_snapshot(specialist, middle.display_reference)
    c = snapshot["continuations"]

    assert c["successor"] == {
        "id": f"matter:{last.pk}",
        "reference": last.display_reference,
        "url": f"https://juristid.example.test/teemad/{last.pk}/",
    }
    assert [p["reference"] for p in c["predecessors"]] == [first.display_reference]
    assert [s["reference"] for s in c["chain"]] == [
        first.display_reference,
        middle.display_reference,
        last.display_reference,
    ]
    assert c["endpoint"]["reference"] == last.display_reference and c["endpoint"]["is_open"]
    assert c["successor_hidden"] is False and c["hidden_predecessors"] == 0
    assert c["cyclic"] is False and c["truncated"] is False

    text = readers.fetch_source(specialist, f"matter:{middle.pk}")["text"]
    assert f"Continues as: {last.display_reference}" in text
    assert f"Continued from: {first.display_reference}" in text
    assert "Chain: " in text and "endpoint" in text


def test_get_matter_never_names_a_stage_the_reader_may_not_read(public_origin, specialist):
    first, middle, last = _chain(3, owner=specialist)
    Matter.all_objects.filter(pk=middle.pk).update(deleted_at=timezone.now())

    forward = readers.matter_snapshot(specialist, first.display_reference)["continuations"]
    assert forward["successor"] is None and forward["successor_hidden"] is True
    assert forward["endpoint"] is None and forward["chain_continues_hidden"] is True
    assert [s["reference"] for s in forward["chain"]] == [first.display_reference]
    assert middle.display_reference not in repr(forward)

    back = readers.matter_snapshot(specialist, last.display_reference)["continuations"]
    assert back["predecessors"] == [] and middle.display_reference not in repr(back)


def test_get_matter_of_an_outsider_is_not_found(public_origin, specialist, administrator):
    old, _new = _chain(2, owner=specialist)
    with pytest.raises(NotFound):
        readers.matter_snapshot(administrator, old.display_reference)


def test_get_matter_reports_a_cycle_without_looping(public_origin, specialist):
    a, b = _chain(2, owner=specialist)
    Matter.all_objects.filter(pk=b.pk).update(superseded_by=a)
    c = readers.matter_snapshot(specialist, a.display_reference)["continuations"]
    assert c["cyclic"] is True and c["endpoint"] is None


# ---------------------------------------------------------------------------
# The helpers the page reads (app/matters/continuations.py)
# ---------------------------------------------------------------------------


def test_the_middle_of_three_stages_shows_a_path_and_two_stages_do_not(specialist):
    from app.matters.continuations import continuations_for

    first, middle, last = _chain(3, owner=specialist)
    mid = continuations_for(middle, specialist)
    assert not mid.has_chain and mid.shows_path and mid.stage_count == 3
    assert not continuations_for(_chain(2, owner=specialist)[0], specialist).shows_path
    assert continuations_for(first, specialist).endpoint_is_known
    assert not continuations_for(last, specialist).endpoint_is_known  # nothing after it


def test_an_endpoint_behind_a_hidden_step_or_a_cycle_is_not_known(specialist):
    from app.matters.continuations import continuations_for

    first, middle, third, _last = _chain(4, owner=specialist)
    Matter.all_objects.filter(pk=third.pk).update(deleted_at=timezone.now())
    hidden = continuations_for(first, specialist)
    assert hidden.endpoint == middle and not hidden.endpoint_is_known

    a, b = _chain(2, owner=specialist)
    Matter.all_objects.filter(pk=b.pk).update(superseded_by=a)
    looped = continuations_for(Matter.all_objects.get(pk=a.pk), specialist)
    assert looped.cyclic and not looped.endpoint_is_known
