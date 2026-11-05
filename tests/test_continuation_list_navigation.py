"""List and search rows say where a continued Teema's work went (app/matters/continuation_lines.py).

A row whose work status is «Jätkub mujal» gains one quiet line under its title:
«Jätkub teemas 2025_196», the reference linked to that Teema, and — when the
chain's endpoint is known and is not the direct successor — «→ … 2026_190
(avatud)». A successor the reader may not read is never named: the line is the
plain «Jätkub mujal». The badge beside the title is untouched, and the whole page
costs one scoped batch, never a query per row.
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

from app.core.enums import Visibility
from app.matters.continuation_lines import UNNAMED, continuation_lines
from app.matters.models import Matter
from app.matters.services import close_matter
from app.matters.work_status import CONTINUES
from app.workflow.enums import Disposition
from tests import factories

pytestmark = pytest.mark.django_db


def _continues(earlier: Matter, later: Matter) -> None:
    """`earlier` closed because its work continues as `later` (no service side effects)."""
    Matter.all_objects.filter(pk=earlier.pk).update(
        is_open=False,
        closed_at=timezone.now(),
        disposition=Disposition.SUPERSEDED,
        superseded_by=later,
    )


def _chain(n: int, **fields) -> list[Matter]:
    """n Matters, each but the last closed as continuing in the next."""
    matters = [
        factories.MatterFactory(title=f"Ahela etapp {index}", **fields) for index in range(1, n + 1)
    ]
    for earlier, later in pairwise(matters):
        _continues(earlier, later)
    return [Matter.all_objects.get(pk=m.pk) for m in matters]


def _register(client, **params) -> str:
    response = client.get(reverse("matters:matter_list"), {"olek": "koik", "kaupa": "30", **params})
    assert response.status_code == 200
    return response.content.decode()


def _row_for(body: str, matter: Matter) -> str:
    for row in re.findall(r"<tr>.*?</tr>", body, re.DOTALL):
        link = re.search(r'class="table__titlelink"\s+href="([^"]+)"', row)
        if link and link.group(1) == f"/teemad/{matter.pk}/":
            return row
    raise AssertionError(f"no register row for {matter.title}")


def _line(fragment: str) -> str:
    match = re.search(
        r'<span class="continuationline[^"]*">.*?</span>\s*</td>', fragment, re.DOTALL
    )
    if match is None:
        match = re.search(r'<span class="continuationline[^"]*">.*?</span>', fragment, re.DOTALL)
    return match.group(0) if match else ""


def _squash(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _link(matter: Matter) -> str:
    return f'href="/teemad/{matter.pk}/"'


def _badges(row: str) -> list[str]:
    return re.findall(r'<span class="badge[^"]*">([^<]+)</span>', row)


# ---------------------------------------------------------------------------
# Teemad
# ---------------------------------------------------------------------------


def test_the_register_row_links_the_readable_successor(signed_in, specialist):
    old = factories.MatterFactory(owner=specialist, title="Vana pakendiseadus")
    new = factories.MatterFactory(owner=specialist, title="Uus pakendiseaduse eelnõu " * 6)
    close_matter(matter=old, disposition=Disposition.SUPERSEDED, actor=specialist, successor=new)

    row = _row_for(_register(signed_in), old)
    line = _line(row)

    assert _squash(line) == f"Jätkub teemas {new.display_reference}"
    assert _link(new) in line
    # The long title is the link's tooltip; the visible text is the reference.
    assert f'title="{new.title}"' in line
    assert '<a class="continuationline__link"' in line


def test_the_status_badge_is_unchanged(signed_in, specialist):
    plain = factories.MatterFactory(owner=specialist, title="Lihtsalt suletud")
    close_matter(matter=plain, disposition=Disposition.MONITORING_STOPPED, actor=specialist)
    old, new = _chain(2, owner=specialist)

    body = _register(signed_in)
    continued = _row_for(body, old)

    # The same badges as any other closed row: the line navigates, it is no status.
    assert _badges(continued) == _badges(_row_for(body, plain)) == ["Suletud"]
    assert Matter.objects.get(pk=old.pk).work_status is CONTINUES
    # Only the continued row gains a line; an open or plainly closed one does not.
    assert "continuationline" not in _row_for(body, plain)
    assert "continuationline" not in _row_for(body, new)


def test_a_deleted_successor_is_never_named(signed_in, specialist):
    old = factories.MatterFactory(owner=specialist)
    gone = factories.MatterFactory(owner=specialist, title="Kustutatud jätkuteema")
    _continues(old, gone)
    Matter.all_objects.filter(pk=gone.pk).update(deleted_at=timezone.now())

    body = _register(signed_in)
    line = _line(_row_for(body, old))

    assert _squash(line) == "Jätkub mujal"
    assert "<a " not in line
    assert gone.display_reference not in body
    assert "Kustutatud jätkuteema" not in body
    assert f"/teemad/{gone.pk}/" not in body


def test_a_restricted_successor_is_linked_for_an_internal_reader(client, specialist, reader):
    # docs/adr/0181: every business reader reads restricted content too.
    old = factories.MatterFactory(owner=specialist)
    hidden = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    _continues(old, hidden)
    client.force_login(reader)

    line = _line(_row_for(_register(client), old))

    assert _squash(line) == f"Jätkub teemas {hidden.display_reference}"


def _table_for(user, matters: list[Matter], *, fetched_by=None) -> str:
    """The shared register table drawn for `user`, its rows read the way lists read them."""
    from app.matters.selectors import matter_list_queryset

    rows = list(matter_list_queryset(fetched_by or user).filter(pk__in=[m.pk for m in matters]))
    request = RequestFactory().get("/teemad/")
    request.user = user
    context = {"matters": rows, "request": request, "colleagues": [], "user": user}
    return render_to_string("matters/partials/matter_table.html", context, request=request)


def test_an_administrator_without_a_grant_reads_no_reference(client, specialist, administrator):
    """The register is empty for an administrator without a grant (docs/adr/0181);
    and were the table drawn for one, the successor would not be named."""
    old = factories.MatterFactory(owner=specialist)
    new = factories.MatterFactory(owner=specialist, title="Hilisem etapp")
    _continues(old, new)
    old = Matter.all_objects.get(pk=old.pk)

    client.force_login(administrator)
    body = _register(client)
    assert new.display_reference not in body and f"/teemad/{new.pk}/" not in body

    table = _table_for(administrator, [old], fetched_by=specialist)
    assert _squash(_line(table)) == "Jätkub mujal"
    assert new.display_reference not in table
    assert "Hilisem etapp" not in table
    assert f"/teemad/{new.pk}/" not in table
    assert continuation_lines([old], administrator) == {old.pk: UNNAMED}


def test_minu_asjad_and_saabunud_share_the_row_and_its_line(specialist):
    """The table partial batches for itself, so every surface drawing it agrees."""
    old, new = _chain(2, owner=specialist)
    table = _table_for(specialist, [old, new])
    assert _squash(_line(_row_for(table, old))) == f"Jätkub teemas {new.display_reference}"


# ---------------------------------------------------------------------------
# Chains
# ---------------------------------------------------------------------------


def test_a_closed_successor_that_continues_names_it_then_the_endpoint(signed_in, specialist):
    first, middle, last = _chain(3, owner=specialist)

    body = _register(signed_in)
    line = _line(_row_for(body, first))

    # The immediate successor first, always; the endpoint after it, distinct.
    assert _squash(line) == (
        f"Jätkub teemas {middle.display_reference} → , viimane etapp "
        f"{last.display_reference} (avatud)"
    )
    links = re.findall(r'href="(/teemad/[^"]+/)"', line)
    assert links == [f"/teemad/{middle.pk}/", f"/teemad/{last.pk}/"]
    assert '<span class="visually-hidden">, viimane etapp</span>' in line
    # The stage that continues straight into the endpoint names only that one.
    assert _squash(_line(_row_for(body, middle))) == f"Jätkub teemas {last.display_reference}"


def test_stages_between_the_successor_and_the_endpoint_are_an_ellipsis(signed_in, specialist):
    first, second, _third, last = _chain(4, owner=specialist)
    close_matter(matter=last, disposition=Disposition.MONITORING_STOPPED, actor=specialist)

    line = _squash(_line(_row_for(_register(signed_in), first)))

    assert line == (
        f"Jätkub teemas {second.display_reference} → … , viimane etapp "
        f"{last.display_reference} (suletud)"
    )


def test_an_endpoint_behind_a_step_the_reader_may_not_read_is_not_named(signed_in, specialist):
    first, middle, last = _chain(3, owner=specialist)
    Matter.all_objects.filter(pk=last.pk).update(deleted_at=timezone.now())

    body = _register(signed_in)
    line = _line(_row_for(body, first))

    assert _squash(line) == f"Jätkub teemas {middle.display_reference}"
    assert last.display_reference not in body
    # The middle stage's own successor is the deleted one: nothing named there.
    assert _squash(_line(_row_for(body, middle))) == "Jätkub mujal"


def test_a_cycle_names_the_successor_and_no_endpoint(signed_in, specialist):
    a, b, c = _chain(3, owner=specialist)
    _continues(c, a)

    body = _register(signed_in)
    for row, successor in ((a, b), (b, c), (c, a)):
        assert _squash(_line(_row_for(body, row))) == f"Jätkub teemas {successor.display_reference}"


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def _search(client, query: str) -> str:
    from app.search.indexing import rebuild_all

    rebuild_all()
    response = client.get(reverse("search:search"), {"q": query})
    assert response.status_code == 200
    return response.content.decode()


def _search_row(body: str, matter: Matter) -> str:
    # Split on the row wrapper only, not on `resultrow__continuation` inside it.
    for row in re.split(r'(?=<div class="resultrow[" ])', body)[1:]:
        if f'class="result__title" title="{matter.title}"' in row:
            return row
    raise AssertionError(f"no search row for {matter.title}")


def test_a_search_result_links_the_readable_successor(signed_in, specialist):
    old = factories.MatterFactory(owner=specialist, title="Kvaasiseaduse esimene etapp")
    new = factories.MatterFactory(owner=specialist, title="Kvaasiseaduse teine etapp")
    close_matter(matter=old, disposition=Disposition.SUPERSEDED, actor=specialist, successor=new)

    body = _search(signed_in, "Kvaasiseaduse")
    row = _search_row(body, old)
    line = re.search(r'<div class="resultrow__continuation">.*?</div>', row, re.S).group(0)

    assert _squash(line) == f"Jätkub teemas {new.display_reference}"
    assert _link(new) in line
    assert 'class="resultrow resultrow--continued"' in row
    # Beside the result, never inside its link.
    result = re.search(r'<a class="result".*?</a>\s*', row, re.S).group(0)
    assert "continuationline" not in result
    assert "Suletud" in result
    assert "continuationline" not in _search_row(body, new)


def test_a_search_result_never_names_a_deleted_successor(signed_in, specialist):
    old = factories.MatterFactory(owner=specialist, title="Kvaasiseaduse vana etapp")
    gone = factories.MatterFactory(owner=specialist, title="Kustutatud kvaasietapp")
    _continues(old, gone)
    Matter.all_objects.filter(pk=gone.pk).update(deleted_at=timezone.now())

    body = _search(signed_in, "Kvaasiseaduse")
    line = re.search(r'<div class="resultrow__continuation">.*?</div>', body, re.S).group(0)

    assert _squash(line) == "Jätkub mujal"
    assert gone.display_reference not in body
    assert f"/teemad/{gone.pk}/" not in body


# ---------------------------------------------------------------------------
# Cost: one batch per page, not one query per row
# ---------------------------------------------------------------------------


def _closed_rows(owner, n: int) -> tuple[list[Matter], list[Matter]]:
    rows = [factories.MatterFactory(owner=owner, title=f"Suletud rida {i}") for i in range(n)]
    successors = [factories.MatterFactory(owner=owner, title=f"Jätkurida {i}") for i in range(n)]
    for row in rows:
        close_matter(matter=row, disposition=Disposition.MONITORING_STOPPED, actor=owner)
    return rows, successors


def _register_queries(client, **params) -> int:
    with CaptureQueriesContext(connection) as captured:
        _register(client, olek="suletud", **params)
    return len(captured.captured_queries)


def test_the_register_costs_the_same_with_one_or_ten_continued_rows(signed_in, specialist):
    rows, successors = _closed_rows(specialist, 10)
    _register_queries(signed_in)  # warm caches that are not about this page
    none = _register_queries(signed_in)

    _continues(rows[0], successors[0])
    one = _register_queries(signed_in)
    for row, successor in zip(rows[1:], successors[1:], strict=True):
        _continues(row, successor)
    ten = _register_queries(signed_in)

    assert one == ten
    assert one - none <= 1  # the one scoped batch of successors
    assert _register(signed_in, olek="suletud").count('class="continuationline"') == 10


def test_endpoints_cost_one_query_per_level_not_per_row(signed_in, specialist):
    rows, successors = _closed_rows(specialist, 10)
    ends = [factories.MatterFactory(owner=specialist) for _ in range(10)]
    _register_queries(signed_in)
    _continues(rows[0], successors[0])
    _continues(successors[0], ends[0])
    one = _register_queries(signed_in)
    for row, successor, end in zip(rows[1:], successors[1:], ends[1:], strict=True):
        _continues(row, successor)
        _continues(successor, end)
    ten = _register_queries(signed_in)

    assert one == ten
    body = _register(signed_in, olek="suletud")
    for row, successor, end in zip(rows, successors, ends, strict=True):
        assert _squash(_line(_row_for(body, row))) == (
            f"Jätkub teemas {successor.display_reference} → , viimane etapp "
            f"{end.display_reference} (avatud)"
        )
    assert body.count("viimane etapp") == 10


def test_search_costs_the_same_with_one_or_ten_continued_results(signed_in, specialist):
    from app.search.indexing import rebuild_all

    rows = [factories.MatterFactory(owner=specialist, title=f"Kvaasirida {i}") for i in range(10)]
    successors = [factories.MatterFactory(owner=specialist, title=f"Jätk {i}") for i in range(10)]

    def queries() -> int:
        rebuild_all()
        with CaptureQueriesContext(connection) as captured:
            signed_in.get(reverse("search:search"), {"q": "Kvaasirida"})
        return len(captured.captured_queries)

    _continues(rows[0], successors[0])
    one = queries()
    for row, successor in zip(rows[1:], successors[1:], strict=True):
        _continues(row, successor)
    ten = queries()

    assert one == ten
