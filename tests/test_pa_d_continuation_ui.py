"""Post-audit round, agent D: the rail «Seotud» card and the list continuation line.

QA-16 — a closed Teema whose closed banner already names the successor does not
name it a third time in the rail's «Seotud» card. Everything else stays: the
predecessor rows, the «cannot be opened» rows, the collaborators, and the
successor row on an open Teema or wherever the banner does not name it.

QA-17 — the rail comment says what the inline «Saatja» save really redraws.

QA-18 — a list line names a stage by reference *and* a short title, without a
hover tooltip being the only place the title lives.

QA-19 — the endpoint after the arrow says visibly that it is «viimane etapp», and
a screen reader hears how many readable stages the «…» stands for.

The rail's rows for a stage this reader cannot open use the agreed words
(COMMON.md, QA-05) and never print a number.
"""

from __future__ import annotations

import re
from itertools import pairwise
from pathlib import Path

import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from app.matters.continuation_lines import ContinuationLine, continuation_lines
from app.matters.continuations import Continuations
from app.matters.models import Matter
from app.matters.services import close_matter
from app.workflow.enums import Disposition
from tests import factories

pytestmark = pytest.mark.django_db

FORWARD_UNOPENABLE = "Jätkub teemas, mida siit avada ei saa"
BACKWARD_UNOPENABLE = "Varasem etapp, mida siit avada ei saa"
RAIL = Path(__file__).resolve().parents[1] / "templates" / "matters" / "partials" / "rail.html"


def _squash(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _visible(html: str) -> str:
    """What a sighted reader sees: the text without the visually hidden spans."""
    return _squash(re.sub(r'<span class="visually-hidden">[^<]*</span>', " ", html))


def _page(client, matter: Matter) -> str:
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


def _nav(body: str) -> str:
    match = re.search(r'<nav class="matterhead__continuation".*?</nav>', body, re.DOTALL)
    return match.group(0) if match else ""


def _banner(body: str) -> str:
    match = re.search(r'<div class="banner banner--closed">.*?</p>', body, re.DOTALL)
    return match.group(0) if match else ""


def _seotud(body: str) -> str:
    """The whole «Seotud» card: up to its own closing tag, not its first row's."""
    start = body.find('<span class="railcard__label">Seotud</span>')
    return body[start : body.find("\n    </div>", start)] if start >= 0 else ""


def _chain(owner, n: int) -> list[Matter]:
    """n open Matters, each continuing as the next (`superseded_by` only)."""
    matters = [factories.MatterFactory(owner=owner, title=f"Etapp {i}") for i in range(1, n + 1)]
    for earlier, later in pairwise(matters):
        Matter.all_objects.filter(pk=earlier.pk).update(superseded_by=later)
    return [Matter.all_objects.get(pk=m.pk) for m in matters]


def _closed_chain(owner, n: int) -> list[Matter]:
    """`_chain`, with every stage but the last closed as continuing in the next."""
    matters = _chain(owner, n)
    Matter.all_objects.filter(pk__in=[m.pk for m in matters[:-1]]).update(
        is_open=False, closed_at=timezone.now(), disposition=Disposition.SUPERSEDED
    )
    return [Matter.all_objects.get(pk=m.pk) for m in matters]


# ---------------------------------------------------------------------------
# QA-16 — the rail does not repeat the successor the closed banner names
# ---------------------------------------------------------------------------


def test_qa16_a_closed_stage_says_jatkub_teemas_in_the_header_and_banner_only(
    signed_in, specialist
):
    old = factories.MatterFactory(owner=specialist, title="Vana pakendiseadus")
    new = factories.MatterFactory(owner=specialist, title="Uus pakendiseaduse eelnõu")
    close_matter(matter=old, disposition=Disposition.SUPERSEDED, actor=specialist, successor=new)

    body = _page(signed_in, old)

    assert f"Jätkub teemas {new.display_reference}" in _squash(_nav(body))
    assert f"Jätkub teemas {new.display_reference}" in _squash(_banner(body))
    assert "Jätkub teemas" not in _squash(_seotud(body))
    # Exactly the header path and the banner, nowhere else on the page.
    assert _squash(body).count("Jätkub teemas") == 2


def test_qa16_a_closed_middle_stage_keeps_its_other_rail_rows(signed_in, specialist):
    first, middle, last = _chain(specialist, 3)
    close_matter(
        matter=middle, disposition=Disposition.SUPERSEDED, actor=specialist, successor=last
    )
    middle.collaborators.add(factories.UserFactory(display_name="Kaia Kaastöötaja"))

    body = _page(signed_in, middle)
    card = _squash(_seotud(body))

    assert "Jätkub teemas" not in card
    assert "Kaastöötaja" in card  # the card itself stays, with everything else on it
    # The predecessor is where it always is on this rail: «Varasemad etapid».
    assert "Varasemad etapid" in body and first.display_reference in body


def test_qa16_a_closed_stage_whose_successor_cannot_be_opened_keeps_the_rail_row(
    signed_in, specialist
):
    old = factories.MatterFactory(owner=specialist)
    gone = factories.MatterFactory(owner=specialist, title="Kustutatud jätkuteema")
    close_matter(matter=old, disposition=Disposition.SUPERSEDED, actor=specialist, successor=gone)
    Matter.all_objects.filter(pk=gone.pk).update(deleted_at=timezone.now())

    body = _page(signed_in, old)

    # The banner does not name it, so the rail is not repeating anything.
    assert gone.display_reference not in _banner(body)
    assert FORWARD_UNOPENABLE in _squash(_seotud(body))
    assert "Kustutatud jätkuteema" not in body


def test_qa16_an_open_teema_keeps_its_successor_and_predecessors_on_the_rail(signed_in, specialist):
    first, middle, last = _chain(specialist, 3)  # every stage still open

    body = _page(signed_in, middle)
    card = _squash(_seotud(body))

    assert f"Jätkub teemas {last.display_reference} · Etapp 3" in card
    assert "Varasemad etapid" in body and first.display_reference in body


def _rail_for(user, matter: Matter, continuations: Continuations) -> str:
    """The rail drawn for `user`, with the chain answer this request would hold."""
    request = RequestFactory().get(f"/teemad/{matter.pk}/")
    request.user = user
    request._readable_continuations = {
        (matter.pk, matter.superseded_by_id, matter.is_open): continuations
    }
    context = {"matter": matter, "request": request, "owners": [], "colleagues": []}
    return render_to_string("matters/partials/rail.html", context, request=request)


def test_the_rail_says_a_stage_cannot_be_opened_in_the_agreed_words_and_no_number(specialist):
    matter = factories.MatterFactory(owner=specialist)
    for hidden in (True, 2):  # agent A's bool, and the int it replaces
        rail = _rail_for(
            specialist, matter, Continuations(hidden_predecessors=hidden, hidden_after=True)
        )
        card = _squash(_seotud(rail))
        assert BACKWARD_UNOPENABLE in card
        assert FORWARD_UNOPENABLE in card
        assert "lubatud" not in card
        assert not re.search(r"\d", card), card


# ---------------------------------------------------------------------------
# QA-17 — the rail comment matches what the Saatja save redraws
# ---------------------------------------------------------------------------


def test_qa17_the_rail_comment_no_longer_claims_a_saatja_save_drops_the_section():
    source = RAIL.read_text(encoding="utf-8")
    assert "redraws this rail without the section" not in source
    assert "_field_surface_context" in source


# ---------------------------------------------------------------------------
# QA-18 / QA-19 — the list line, readable without hover
# ---------------------------------------------------------------------------


def _line_html(line: ContinuationLine) -> str:
    return render_to_string("matters/partials/continuation_line.html", {"line": line})


def test_qa18_a_successor_is_named_by_reference_and_a_short_visible_title(specialist):
    old = factories.MatterFactory(owner=specialist)
    new = factories.MatterFactory(owner=specialist, title="Uus pakendiseaduse eelnõu " * 6)
    close_matter(matter=old, disposition=Disposition.SUPERSEDED, actor=specialist, successor=new)
    old.refresh_from_db()

    html = _line_html(continuation_lines([old], specialist)[old.pk])
    visible = _visible(html)

    assert visible.startswith(f"Jätkub teemas {new.display_reference} · Uus pakendiseaduse eelnõu")
    assert visible.endswith("…")
    assert len(visible) < len(f"Jätkub teemas {new.display_reference} · ") + 42
    # The full title stays the link's tooltip.
    assert f'title="{new.title}"' in html


def test_qa18_a_stage_without_a_reference_is_not_titled_twice(specialist):
    old = factories.MatterFactory(owner=specialist)
    new = factories.MatterFactory(
        owner=specialist, title="Viiteta jätk", reference_year=None, reference_number=None
    )
    close_matter(matter=old, disposition=Disposition.SUPERSEDED, actor=specialist, successor=new)
    old.refresh_from_db()

    visible = _visible(_line_html(continuation_lines([old], specialist)[old.pk]))

    assert visible == "Jätkub teemas Viiteta jätk"


def test_qa18_qa19_the_endpoint_says_viimane_etapp_visibly_and_carries_its_title(specialist):
    first, middle, last = _closed_chain(specialist, 3)

    html = _line_html(continuation_lines([first], specialist)[first.pk])
    visible = _visible(html)

    assert visible == (
        f"Jätkub teemas {middle.display_reference} → viimane etapp "
        f"{last.display_reference} (avatud) · Etapp 3"
    )
    # The successor's title is still there for a screen reader.
    assert "Etapp 2" in _squash(html)
    # Direction: the immediate successor first, the endpoint after the arrow.
    links = re.findall(r'href="(/teemad/[^"]+/)"', html)
    assert links == [f"/teemad/{middle.pk}/", f"/teemad/{last.pk}/"]
    assert "vahel veel" not in html  # nothing skipped, nothing announced


@pytest.mark.parametrize(
    ("stages", "spoken"), [(4, "vahel veel 1 etapp"), (5, "vahel veel 2 etappi")]
)
def test_qa19_a_screen_reader_hears_how_many_stages_the_ellipsis_skips(specialist, stages, spoken):
    chain = _closed_chain(specialist, stages)
    first, second, last = chain[0], chain[1], chain[-1]

    html = _line_html(continuation_lines([first], specialist)[first.pk])

    assert re.search(rf'<span class="visually-hidden">[^<]*{spoken}[^<]*</span>', html), html
    assert _visible(html).startswith(
        f"Jätkub teemas {second.display_reference} → … viimane etapp {last.display_reference}"
    )
    # The visible ellipsis is decoration; the count is what is spoken.
    assert re.search(r'<span aria-hidden="true"[^>]*>[^<]*…', html)
