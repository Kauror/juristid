"""QA-14, QA-15 and QA-12: «Seotud materjalid» and its continuation history.

* QA-14 — the «+ Lisa» picker showed a stage of the Teema's own continuation
  history as an ordinary candidate with «Seo teemaga». It now shows the row
  labelled «Juba varasem etapp» / «Juba hilisem etapp», without an add control,
  and the `link` route refuses a crafted POST for one.
* QA-15 — «Seo teemaga», «Lisa», «Ei ole seotud» (and «Taasta soovitus») had the
  same accessible name on every row. Each now names its target.
* QA-12 — «Varasemad etapid» stays flat and compact (owner, 2026-11-05); a
  direct predecessor says «vahetu eelmine etapp», a deeper stage «jätkus teemana
  <reference>».

The history, invented throughout (arrows are `superseded_by`)::

    a 2019_1 → b 2020_2 → c 2021_3 → T 2025_10
               d 2018_4 → e 2022_5 → T
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse

from app.matters.models import Matter
from app.related_materials import services
from app.related_materials.models import MatterRelation
from tests import factories

pytestmark = pytest.mark.django_db

HX = {"HTTP_HX_REQUEST": "true"}

#: One distinct searchable word per Teema, so each picker query finds one stage.
WORDS = {
    "a": "Aprikoosiseadus",
    "b": "Banaaniseadus",
    "c": "Kirsiseadus",
    "d": "Datliseadus",
    "e": "Ebaküdooniaseadus",
    "t": "Tomatiseadus",
    "x": "Ploomiseadus",
}
REFS = {"a": (2019, 1), "b": (2020, 2), "c": (2021, 3), "d": (2018, 4), "e": (2022, 5)}
REFS |= {"t": (2025, 10), "x": (2024, 7)}


def _continues(earlier: Matter, later: Matter | None) -> None:
    Matter.all_objects.filter(pk=earlier.pk).update(superseded_by=later)


@pytest.fixture
def history(specialist) -> dict[str, Matter]:
    made = {
        key: factories.MatterFactory(
            owner=specialist,
            title=f"{WORDS[key]} muutmise eelnõu",
            reference_year=REFS[key][0],
            reference_number=REFS[key][1],
        )
        for key in WORDS
    }
    for earlier, later in (("a", "b"), ("b", "c"), ("c", "t"), ("d", "e"), ("e", "t")):
        _continues(made[earlier], made[later])
    return {key: Matter.all_objects.get(pk=m.pk) for key, m in made.items()}


def _picker(client: Any, matter: Matter, word: str) -> str:
    response = client.get(
        reverse("related_materials:picker", kwargs={"pk": matter.pk}), {"q": word}, **HX
    )
    assert response.status_code == 200
    return response.content.decode()


def _row(body: str, matter: Matter) -> str:
    """The picker row naming `matter`, or ""."""
    for row in body.split('<li class="factrow"')[1:]:
        if f'href="{reverse("matters:matter_detail", kwargs={"pk": matter.pk})}"' in row:
            return row.split("</li>", 1)[0]
    return ""


def _offers_link(row: str, matter: Matter) -> bool:
    return f'name="teema" value="{matter.pk}"' in row


def _edges() -> dict[Any, Any]:
    return dict(Matter.all_objects.values_list("pk", "superseded_by"))


# -- QA-14: the picker and a crafted POST ---------------------------------------------


@pytest.mark.parametrize(
    "stage",
    [
        pytest.param("c", id="direct"),
        pytest.param("a", id="indirect-three-back"),
        pytest.param("d", id="converging-branch"),
    ],
)
def test_the_picker_labels_an_earlier_stage_and_offers_no_add(history, signed_in, stage):
    body = _picker(signed_in, history["t"], WORDS[stage].lower())
    row = _row(body, history[stage])
    assert row, f"the earlier stage {stage} is shown"
    assert "Juba varasem etapp" in row
    assert not _offers_link(row, history[stage])
    assert "Seo teemaga" not in row


@pytest.mark.parametrize(
    ("viewed", "later"),
    [pytest.param("c", "t", id="direct"), pytest.param("a", "t", id="indirect")],
)
def test_the_picker_labels_a_later_stage(history, signed_in, viewed, later):
    body = _picker(signed_in, history[viewed], WORDS[later].lower())
    row = _row(body, history[later])
    assert "Juba hilisem etapp" in row and not _offers_link(row, history[later])


def test_an_unrelated_teema_is_still_offered(history, signed_in):
    body = _picker(signed_in, history["t"], WORDS["x"].lower())
    row = _row(body, history["x"])
    assert _offers_link(row, history["x"]) and "Juba " not in row


def test_a_converging_sibling_is_not_a_stage_of_this_teema(history, signed_in):
    """c and e both continue as T; neither continues the other, so c may relate e by hand."""
    body = _picker(signed_in, history["c"], WORDS["e"].lower())
    assert _offers_link(_row(body, history["e"]), history["e"])


@pytest.mark.parametrize(
    ("viewed", "target"),
    [
        pytest.param("t", "c", id="direct-earlier"),
        pytest.param("t", "a", id="indirect-earlier"),
        pytest.param("t", "d", id="converging-earlier"),
        pytest.param("c", "t", id="later"),
        pytest.param("a", "t", id="indirect-later"),
    ],
)
def test_a_crafted_link_post_for_a_stage_writes_nothing(history, signed_in, viewed, target):
    edges = _edges()
    url = reverse("related_materials:link", kwargs={"pk": history[viewed].pk})
    response = signed_in.post(url, {"teema": str(history[target].pk)}, **HX)
    assert response.status_code == 400
    assert "eraldi seost ei lisatud" in response.content.decode()
    plain = signed_in.post(url, {"teema": str(history[target].pk)})
    assert plain.status_code == 302
    assert MatterRelation.objects.count() == 0
    assert _edges() == edges


def test_a_manual_relation_to_a_stage_keeps_showing_and_cannot_be_doubled(
    history, signed_in, specialist
):
    # Made before this round's guard, or through the service directly.
    services.link_related_matters(matter=history["t"], other=history["b"], actor=specialist)
    edges = _edges()
    section = signed_in.get(
        reverse("related_materials:section", kwargs={"pk": history["t"].pk}), **HX
    ).content.decode()
    stage_row = section.split(f'data-related-stage="{history["b"].pk}"', 1)[1].split("</li>")[0]
    assert "seotud ka käsitsi" in stage_row and "Eemalda käsitsi lisatud seos" in stage_row
    # The picker still finds b, as the stage it is, with no second «Seo teemaga».
    row = _row(_picker(signed_in, history["t"], WORDS["b"].lower()), history["b"])
    assert "Juba varasem etapp" in row and not _offers_link(row, history["b"])
    response = signed_in.post(
        reverse("related_materials:link", kwargs={"pk": history["t"].pk}),
        {"teema": str(history["b"].pk)},
        **HX,
    )
    assert response.status_code == 400
    assert MatterRelation.objects.count() == 1
    assert _edges() == edges


# -- QA-15: accessible names ----------------------------------------------------------


def test_the_picker_button_names_its_teema(history, signed_in):
    row = _row(_picker(signed_in, history["t"], WORDS["x"].lower()), history["x"])
    assert 'aria-label="Seo teemaga 2024_7 — Ploomiseadus muutmise eelnõu"' in row
    assert ">Seo teemaga</button>" in row  # the visible word is unchanged


def _card(template: str, item: Any, matter: Matter, specialist: Any) -> str:
    request = RequestFactory().get("/")
    request.user = specialist
    return render_to_string(
        f"related_materials/partials/{template}",
        {"item": item, "matter": matter, "can_write": True},
        request=request,
    )


def _names(html: str) -> list[str]:
    return re.findall(r'<button[^>]*aria-label="([^"]+)"[^>]*>', html)


def test_suggestion_buttons_name_their_teema(history, specialist):
    def item(key: str, *, dismissed: bool = False) -> Any:
        return SimpleNamespace(
            matter=history[key],
            is_dismissed=dismissed,
            state_label="avatud",
            reasons=[],
            outcome=SimpleNamespace(is_empty=True),
        )

    html = _card("matter_card.html", item("x"), history["t"], specialist) + _card(
        "matter_card.html", item("t"), history["x"], specialist
    )
    assert _names(html) == [
        "Lisa 2024_7 — Ploomiseadus muutmise eelnõu",
        "Ei ole seotud 2024_7 — Ploomiseadus muutmise eelnõu",
        "Lisa 2025_10 — Tomatiseadus muutmise eelnõu",
        "Ei ole seotud 2025_10 — Tomatiseadus muutmise eelnõu",
    ]
    assert ">Lisa</button>" in html and ">Ei ole seotud</button>" in html
    hidden = _card("matter_card.html", item("x", dismissed=True), history["t"], specialist)
    assert _names(hidden) == ["Taasta soovitus 2024_7 — Ploomiseadus muutmise eelnõu"]


def test_material_buttons_name_their_material(history, specialist):
    item = SimpleNamespace(
        is_dismissed=False,
        form_kind="arvamus",
        key="00000000-0000-0000-0000-000000000001",
        label="Varasem arvamus",
        open_url="/x/",
        title="Arvamus pakendiseaduse kohta",
        date=None,
        source_reference="",
        source_title="",
        recipient="",
        reasons=[],
    )
    html = _card("material_card.html", item, history["t"], specialist)
    assert _names(html) == [
        "Lisa varasem arvamus — Arvamus pakendiseaduse kohta",
        "Ei ole seotud varasem arvamus — Arvamus pakendiseaduse kohta",
    ]


# -- QA-12: which stage each row continued as -----------------------------------------


def test_varasemad_etapid_marks_direct_and_deeper_stages(history, signed_in):
    section = signed_in.get(
        reverse("related_materials:section", kwargs={"pk": history["t"].pk}), **HX
    ).content.decode()
    stages = section.split("data-related-stages>", 1)[1].split("</ul>", 1)[0]
    meta = {}
    for key in "abcde":
        row = stages.split(f'data-related-stage="{history[key].pk}"', 1)[1].split("</li>")[0]
        text = re.search(r'class="muted relatedmaterials__meta"[^>]*>([^<]*)<', row)
        assert text, key
        meta[key] = " ".join(text.group(1).split())
    assert meta == {
        "a": "avatud · jätkus teemana 2020_2",
        "b": "avatud · jätkus teemana 2021_3",
        "c": "avatud · vahetu eelmine etapp",
        "d": "avatud · jätkus teemana 2022_5",
        "e": "avatud · vahetu eelmine etapp",
    }
    # Still flat and earliest first (by depth, then reference).
    order = [m.group(1) for m in re.finditer(r'data-related-stage="([0-9a-f-]+)"', stages)]
    assert order == [str(history[k].pk) for k in ("a", "d", "b", "c", "e")]
