"""Agent E (independent verifier): QA-04, QA-05, QA-06 on every channel.

Written against the owner's rule of 2026-11-05, not against the authors' tests:

* no surface shows a count, a reference or a title of a stage the reader cannot
  open (QA-05); navigation surfaces say yes/no in the two agreed sentences, and
  «Seotud materjalid» says nothing;
* a deleted stage is never described as a permissions matter (QA-04);
* a reader who may not read the Matter itself gets nothing at all (QA-06).

A narrower-than-0181 reader is built the way the audit built it (a hand-made
participation scope), because under ADR 0181 no live reader is narrow. Every
channel is asked: the Teema page, the «Seotud materjalid» fragment (HTMX and
no-JS), the suggestions panel (`?avatud=1`), the Teemad register, the header
search, the Dokumendid tab, and MCP `get_matter` (JSON and text lines).
Synthetic data only.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.matters.continuation_lines import continuation_lines
from app.matters.continuations import (
    Continuations,
    EarlierStages,
    confirmed_continuation_ids,
    continuations_for,
    historical_predecessors,
)
from app.matters.models import Matter
from app.mcp_server import readers
from app.related_materials import engine
from app.related_materials.selectors import continuation_stage_labels, related_materials_for
from app.workflow.enums import Disposition
from tests import factories

pytestmark = pytest.mark.django_db

HX = {"HTTP_HX_REQUEST": "true"}
FORWARD = "Jätkub teemas, mida siit avada ei saa"
BACKWARD = "Varasem etapp, mida siit avada ei saa"


@pytest.fixture(autouse=True)
def public_origin(settings):
    settings.JURISTID_PUBLIC_ORIGIN = "https://juristid.example.test"


@pytest.fixture
def narrow(monkeypatch, specialist):
    from app.core.authorization import Scope
    from app.core.authorization import scope_for_user as real

    def scope(user: Any) -> Any:
        if getattr(user, "pk", None) == specialist.pk:
            return Scope(
                user=user,
                is_authenticated=True,
                sees_restricted_by_role=False,
                break_glass_grant_id=None,
            )
        return real(user)

    monkeypatch.setattr("app.matters.models.scope_for_user", scope)
    # The header search and the picker scope through their own import.
    monkeypatch.setattr("app.search.services.scope_for_user", scope)
    return specialist


def _teema(ref: str, owner: Any, **fields: Any) -> Matter:
    year, number = (int(part) for part in ref.split("_"))
    fields.setdefault("title", f"PAE teema {ref}")
    return factories.MatterFactory(
        owner=owner, reference_year=year, reference_number=number, **fields
    )


def _secret(ref: str, title: str) -> Matter:
    return _teema(ref, factories.UserFactory(), visibility=Visibility.RESTRICTED, title=title)


def _continues(earlier: Matter, later: Matter) -> None:
    Matter.all_objects.filter(pk=earlier.pk).update(
        superseded_by=later,
        is_open=False,
        closed_at=timezone.now(),
        disposition=Disposition.SUPERSEDED,
    )


def _soft_delete(matter: Matter) -> None:
    """A tombstone in the middle of a history (only reachable through raw data:
    `delete_matter` refuses while a live predecessor names it, QA-20). The search
    projection is forgotten as `delete_matter` would forget it."""
    from app.search.indexing import forget_matter

    Matter.all_objects.filter(pk=matter.pk).update(deleted_at=timezone.now())
    forget_matter(matter.pk)


def _fresh(matter: Matter) -> Matter:
    return Matter.all_objects.get(pk=matter.pk)


def _body(response: Any) -> str:
    if getattr(response, "streaming", False):
        return b"".join(response.streaming_content).decode(errors="replace")
    return response.content.decode(errors="replace")


def _channels(client: Client, viewed: Matter, query: str) -> dict[str, str]:
    section = reverse("related_materials:section", kwargs={"pk": viewed.pk})
    picker = reverse("related_materials:picker", kwargs={"pk": viewed.pk})
    detail = reverse("matters:matter_detail", kwargs={"pk": viewed.pk})
    out = {
        "detail": client.get(detail),
        "dokumendid": client.get(reverse("matters:matter_documents", kwargs={"pk": viewed.pk})),
        "fragment": client.get(section, **HX),
        "nojs": client.get(section),
        "suggestions": client.get(section + "?avatud=1&peidetud=1&piir=50", **HX),
        "picker": client.get(picker, {"q": query}, **HX),
        "teemad": client.get(reverse("matters:matter_list") + "?olek=koik"),
        "search": client.get(reverse("search:search"), {"q": query}),
    }
    for name, response in out.items():
        assert response.status_code == 200, (name, response.status_code)
    return {name: _body(response) for name, response in out.items()}


def _mcp(user: Any, matter: Matter) -> dict[str, Any]:
    return readers.matter_snapshot(user, str(matter.pk))


def _assert_unnamed(text: str, *stages: Matter, where: str) -> None:
    for stage in stages:
        assert stage.title not in text, (where, stage.title)
        assert str(stage.pk) not in text, (where, stage.pk)
        if stage.display_reference:
            assert stage.display_reference not in text, (where, stage.display_reference)
    assert "lubatud näha" not in text, where
    assert "may not read" not in text, where


# -- QA-05: a hidden branch with readable stages behind it -----------------------------


@pytest.fixture
def behind_a_door(narrow):
    """2011_1 -> 2012_2 -> 2013_3 (hidden) -> 2014_4 (viewed) <- 2013_5 (hidden).

    2011_1 and 2012_2 are readable but lie behind the hidden stage; 2014_4 also
    continues as 2015_6 (hidden) -> 2016_7 (readable endpoint).
    """
    a = _teema("2011_1", narrow, title="PAE ESIMENE taga")
    b = _teema("2012_2", narrow, title="PAE TEINE taga")
    door = _secret("2013_3", "PAE SALAUKS")
    other_door = _secret("2013_5", "PAE TEINE SALAUKS")
    viewed = _teema("2014_4", narrow, title="PAE VAADATUD")
    hidden_next = _secret("2015_6", "PAE SALAJÄRGLANE")
    endpoint = _teema("2016_7", narrow, title="PAE LÕPP pärast salajärglast")
    _continues(a, b)
    _continues(b, door)
    _continues(door, viewed)
    _continues(other_door, viewed)
    _continues(viewed, hidden_next)
    _continues(hidden_next, endpoint)
    return {
        "a": a,
        "b": b,
        "door": door,
        "other_door": other_door,
        "viewed": _fresh(viewed),
        "hidden_next": hidden_next,
        "endpoint": endpoint,
    }


def test_e_no_channel_names_a_hidden_stage_or_what_lies_behind_it(client, behind_a_door):
    w = behind_a_door
    client.force_login(w["viewed"].owner)
    pages = _channels(client, w["viewed"], "PAE")
    secret = (w["door"], w["other_door"], w["hidden_next"])
    behind = (w["a"], w["b"], w["endpoint"])
    for name, text in pages.items():
        _assert_unnamed(text, *secret, where=name)
    # What lies behind a door is not read through it on the Teema's own surfaces.
    for name in ("detail", "dokumendid", "fragment", "nojs", "suggestions"):
        _assert_unnamed(pages[name], *behind, where=name)
    # The register and the search name the viewed row's successor nowhere: the
    # line is the plain «Jätkub mujal» and the endpoint behind the door is absent.
    for name in ("teemad", "search"):
        row = pages[name]
        start = row.find(w["viewed"].title)
        assert start >= 0, name
        assert w["endpoint"].display_reference not in row[start : start + 1500], name


def test_e_navigation_says_yes_no_once_and_the_section_says_nothing(client, behind_a_door):
    w = behind_a_door
    client.force_login(w["viewed"].owner)
    pages = _channels(client, w["viewed"], "PAE")
    detail = pages["detail"]
    assert BACKWARD in detail and FORWARD in detail
    # Two hidden direct predecessors are still one sentence, with no number.
    nav = re.search(r'<nav class="matterhead__continuation".*?</nav>', detail, re.DOTALL)
    assert nav is not None
    assert nav.group(0).count(BACKWARD) == 1
    assert not re.search(r"\(\s*\d+\s*\)", nav.group(0))
    for name in ("fragment", "nojs", "suggestions"):
        assert BACKWARD not in pages[name] and FORWARD not in pages[name], name
        assert "avada ei saa" not in pages[name], name


def test_e_mcp_is_yes_no_and_counts_nothing(behind_a_door):
    w = behind_a_door
    snapshot = _mcp(w["viewed"].owner, w["viewed"])
    dumped = json.dumps(snapshot, ensure_ascii=False, default=str)
    _assert_unnamed(
        dumped,
        w["door"],
        w["other_door"],
        w["hidden_next"],
        w["a"],
        w["b"],
        w["endpoint"],
        where="mcp",
    )
    block = readers._continuations(w["viewed"].owner, _fresh(w["viewed"]))
    assert block["hidden_predecessors"] is True
    assert block["successor"] is None and block["successor_hidden"] is True
    assert block["endpoint"] is None
    assert block["historical_predecessors"] == []
    assert not re.search(r"\b2 (matter|stage)", dumped)


def test_e_helpers_never_return_a_number_for_hidden_stages(behind_a_door):
    w = behind_a_door
    chain = continuations_for(_fresh(w["viewed"]), w["viewed"].owner)
    assert chain.hidden_predecessors is True  # a bool, not 2
    assert type(chain.hidden_predecessors) is bool
    assert chain.predecessors == () and chain.before == ()
    assert chain.successor is None and chain.hidden_after
    assert historical_predecessors(_fresh(w["viewed"]), w["viewed"].owner).stages == ()


def test_e_list_lines_never_name_past_a_hidden_step(behind_a_door):
    """b -> door (hidden): b's register line is the unnamed «Jätkub mujal»."""
    w = behind_a_door
    lines = continuation_lines([_fresh(w["b"]), _fresh(w["a"])], w["viewed"].owner)
    assert lines[w["b"].pk].successor is None and lines[w["b"].pk].endpoint is None
    # a -> b -> door: the successor b is readable, but the endpoint is unknown.
    assert lines[w["a"].pk].successor.pk == w["b"].pk
    assert lines[w["a"].pk].endpoint is None and lines[w["a"].pk].skipped_text == ""


def test_e_picker_does_not_label_a_stage_behind_a_door(behind_a_door):
    """No oracle: a readable stage behind a hidden one is *not* labelled «Juba
    varasem etapp», or the label would tell the reader the hidden stage exists."""
    w = behind_a_door
    labels = continuation_stage_labels(_fresh(w["viewed"]), w["viewed"].owner)
    assert w["a"].pk not in labels and w["b"].pk not in labels
    assert w["endpoint"].pk not in labels


# -- QA-04: a deleted stage is not a permissions matter ---------------------------------


def test_e_a_deleted_middle_stage_never_reads_as_forbidden_anywhere(client, specialist):
    first = _teema("2011_11", specialist, title="PAE ENNE KUSTUTATUT")
    gone = _teema("2012_12", specialist, title="PAE KUSTUTATUD")
    viewed = _teema("2013_13", specialist, title="PAE PÄRAST KUSTUTATUT")
    _continues(first, gone)
    _continues(gone, viewed)
    _soft_delete(gone)
    client.force_login(specialist)
    for matter in (first, viewed):
        pages = _channels(client, _fresh(matter), "PAE")
        for name, text in pages.items():
            _assert_unnamed(text, gone, where=(matter.title, name))
    first_page = client.get(reverse("matters:matter_detail", kwargs={"pk": first.pk}))
    assert FORWARD in _body(first_page)
    # The cut is kept (owner): the deleted stage ends the later stage's history,
    # and the later stage says nothing about it (no backward sentence either).
    viewed_page = _body(client.get(reverse("matters:matter_detail", kwargs={"pk": viewed.pk})))
    assert BACKWARD not in viewed_page
    assert (
        first.title
        not in viewed_page.split('id="seotud-materjalid"', 1)[-1].split("</section>", 1)[0]
    )
    mcp = json.dumps(_mcp(specialist, _fresh(first)), ensure_ascii=False, default=str)
    _assert_unnamed(mcp, gone, where="mcp")
    block = readers._continuations(specialist, _fresh(first))
    assert block["successor"] is None and block["successor_hidden"] is True
    assert readers._continuation_line(block) == "a matter that cannot be opened here"


# -- QA-06: nothing for a reader who may not read the Matter -----------------------------


@pytest.fixture
def secret_with_history(specialist):
    owner = factories.UserFactory()
    earlier = _teema("2011_21", specialist, title="PAE AVALIK ENNE")
    root = _secret("2012_22", "PAE SALAJUUR")
    later = _teema("2013_23", specialist, title="PAE AVALIK PÄRAST")
    hidden_peer = _teema("2011_24", owner, visibility=Visibility.RESTRICTED, title="PAE PEER")
    _continues(earlier, root)
    _continues(hidden_peer, root)
    _continues(root, later)
    return {"root": _fresh(root), "earlier": earlier, "later": later}


def test_e_every_helper_answers_empty_for_an_unreadable_root(narrow, secret_with_history):
    root = secret_with_history["root"]
    assert continuations_for(root, narrow) == Continuations()
    assert historical_predecessors(root, narrow) == EarlierStages()
    assert continuation_stage_labels(root, narrow) == {}
    # The exclusion ids stay unscoped by design (ids only, never shown); they
    # must not *add* anything readable-only either.
    assert root.pk in confirmed_continuation_ids(root, narrow)


@pytest.mark.parametrize("who", ["none", "anonymous", "admin"])
def test_e_unprivileged_viewers_get_empty_answers_even_for_an_ordinary_matter(
    specialist, who, administrator
):
    from django.contrib.auth.models import AnonymousUser

    viewer = {"none": None, "anonymous": AnonymousUser(), "admin": administrator}[who]
    a = _teema("2011_31", specialist)
    b = _teema("2012_32", specialist)
    _continues(a, b)
    assert continuations_for(_fresh(b), viewer) == Continuations()
    assert continuations_for(_fresh(a), viewer) == Continuations()
    assert historical_predecessors(_fresh(b), viewer).stages == ()
    assert related_materials_for(_fresh(b), viewer).stages == ()


def test_e_memo_does_not_leak_between_two_viewers_on_one_instance(
    specialist, administrator, narrow
):
    """The memo is keyed on the reader object: an outsider asking second on the
    same instance gets nothing, and a reader asking after an outsider gets all."""
    a = _teema("2011_41", specialist)
    v = _teema("2012_42", specialist)
    _continues(a, v)
    instance = _fresh(v)
    assert len(historical_predecessors(instance, specialist)) == 1
    assert historical_predecessors(instance, administrator).stages == ()
    assert continuations_for(instance, administrator) == Continuations()
    other = _fresh(v)
    assert continuations_for(other, administrator) == Continuations()
    assert len(historical_predecessors(other, specialist)) == 1
    # Same primary key, another object: asked again, never answered from the memo.
    clone = type(specialist).objects.get(pk=specialist.pk)
    assert len(historical_predecessors(instance, clone)) == 1


def test_e_suggestions_never_offer_an_unreadable_stage_and_profile_is_quiet(narrow, behind_a_door):
    w = behind_a_door
    result = engine.suggestions_for(_fresh(w["viewed"]), narrow, limit=50, include_hidden=True)
    offered = {item.matter.pk for item in result.matters}
    assert not offered & {w["door"].pk, w["other_door"].pk, w["hidden_next"].pk}
    # The readable stages behind the door are part of the component and stay
    # excluded too (ids only, never shown).
    assert not offered & {w["a"].pk, w["b"].pk, w["endpoint"].pk}
