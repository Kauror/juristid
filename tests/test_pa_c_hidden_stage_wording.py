"""A stage the reader cannot open: yes or no, never how many, never «not allowed».

QA-04 and QA-05 of the independent continuation audit (2026-11-05), with the
owner's binding rule of the same day:

* **QA-04.** A deleted stage still ends the history (ADR 0209 §4), but it is not
  a permissions matter, so no surface says «… mida sul ei ole lubatud näha».
* **QA-05.** No surface shows a count, reference or title of a stage the reader
  cannot open. Navigation surfaces (header, closed banner, rail, MCP) say only
  *that* the history continues past one, in the agreed words:

  - forward  «Jätkub teemas, mida siit avada ei saa»
  - backward «Varasem etapp, mida siit avada ei saa»

  «Seotud materjalid» stays silent about it.

Under docs/adr/0181 the only stage a live reader cannot open is a deleted one;
a narrower reader is reached here, as in the audit, through a hand-built
participation scope (`narrow`). Tests that depend on agent A's change
(`hidden_predecessors` becoming a bool, the rail and MCP wording) are
``xfail(strict=True)`` and say so. Synthetic data only.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.matters.continuations import Continuations
from app.matters.models import Matter
from app.mcp_server import readers
from app.workflow.enums import Disposition
from tests import factories

pytestmark = pytest.mark.django_db

FORWARD = "Jätkub teemas, mida siit avada ei saa"
BACKWARD = "Varasem etapp, mida siit avada ei saa"
FORBIDDEN = "lubatud"

A_PENDING = "QA-05: agent A makes hidden_predecessors a bool and words the rail/MCP"


@pytest.fixture(autouse=True)
def public_origin(settings):
    settings.JURISTID_PUBLIC_ORIGIN = "https://juristid.example.test"


@pytest.fixture
def narrow(monkeypatch, specialist):
    """A reader who may not read RESTRICTED work of others (unreachable under 0181)."""
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
    return specialist


def _teema(owner: Any, title: str, **fields: Any) -> Matter:
    return factories.MatterFactory(owner=owner, title=title, **fields)


def _secret(title: str) -> Matter:
    return factories.MatterFactory(
        owner=factories.UserFactory(), visibility=Visibility.RESTRICTED, title=title
    )


def _continues(earlier: Matter, later: Matter) -> None:
    Matter.all_objects.filter(pk=earlier.pk).update(
        superseded_by=later,
        is_open=False,
        closed_at=timezone.now(),
        disposition=Disposition.SUPERSEDED,
    )


def _fresh(matter: Matter) -> Matter:
    return Matter.all_objects.get(pk=matter.pk)


def _page(client: Any, matter: Matter) -> str:
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
    start = body.find('<span class="railcard__label">Seotud</span>')
    return body[start : body.find("</div>\n  ", start)] if start >= 0 else ""


def _section(body: str) -> str:
    start = body.find('id="seotud-materjalid"')
    return body[start : body.find("</section>", start)] if start >= 0 else ""


def _squash(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _no_count(text: str) -> None:
    """No number stands beside the unopenable-stage wording."""
    assert not re.search(r"\(\s*\d+\s*\)", text), text
    assert not re.search(r"\bja \d+\b", text), text
    assert not re.search(r"\b\d+ (teemat|etappi|matter)", text), text


# -- QA-04: a deleted stage is unopenable, not forbidden ------------------------------


def test_qa04_a_deleted_successor_is_said_to_be_unopenable_in_header_and_banner(
    signed_in, specialist
):
    old = _teema(specialist, "PA-C vana")
    gone = _teema(specialist, "PA-C KUSTUTATUD-41")
    _continues(old, gone)
    Matter.all_objects.filter(pk=gone.pk).update(deleted_at=timezone.now())

    body = _page(signed_in, _fresh(old))
    nav, banner = _squash(_nav(body)), _squash(_banner(body))

    assert FORWARD in nav
    assert f"Teema on suletud. {FORWARD}" in banner
    assert FORBIDDEN not in nav and FORBIDDEN not in banner
    for leak in ("KUSTUTATUD-41", gone.display_reference, str(gone.pk)):
        assert leak not in body


def test_qa04_a_deleted_stage_inside_a_path_ends_it_with_the_forward_sentence(
    signed_in, specialist
):
    first, middle, last = (_teema(specialist, f"PA-C etapp {n}") for n in (1, 2, 3))
    gone = _teema(specialist, "PA-C KUSTUTATUD-42")
    _continues(first, middle)
    _continues(middle, last)
    _continues(last, gone)
    Matter.all_objects.filter(pk=gone.pk).update(deleted_at=timezone.now())

    nav = _nav(_page(signed_in, _fresh(middle)))

    assert "Teema etapid" in _squash(nav)
    assert FORWARD in _squash(nav)
    assert FORBIDDEN not in nav
    assert gone.display_reference not in nav and "KUSTUTATUD-42" not in nav


def test_qa04_the_old_permission_wording_is_gone_from_the_two_templates():
    """A source check, so a later edit cannot quietly bring the sentence back."""
    from pathlib import Path

    from django.conf import settings

    for name in ("header.html", "matter_banner.html"):
        path = Path(settings.BASE_DIR) / "templates" / "matters" / "partials" / name
        assert "lubatud näha" not in path.read_text(encoding="utf-8"), name


# -- QA-05: yes or no, never how many (header and banner) -----------------------------


def test_qa05_hidden_predecessors_beside_a_readable_one_are_one_sentence_no_count(client, narrow):
    shown = _teema(narrow, "PA-C NÄHTAV-51")
    secrets = [_secret("PA-C SALA-52"), _secret("PA-C SALA-53")]
    viewed = _teema(narrow, "PA-C VAADATUD-54")
    for earlier in (shown, *secrets):
        _continues(earlier, viewed)
    client.force_login(narrow)

    body = _page(client, _fresh(viewed))
    nav = _squash(_nav(body))

    assert shown.display_reference in nav
    assert nav.count(BACKWARD) == 1
    assert FORBIDDEN not in nav
    _no_count(nav)
    for secret in secrets:
        assert secret.display_reference not in body and secret.title not in body
        assert str(secret.pk) not in body


@pytest.mark.parametrize("how_many", [1, 3])
def test_qa05_only_hidden_predecessors_give_the_same_sentence_whatever_their_number(
    client, narrow, how_many
):
    viewed = _teema(narrow, "PA-C VAADATUD-55")
    for n in range(how_many):
        _continues(_secret(f"PA-C SALA-56-{n}"), viewed)
    client.force_login(narrow)

    nav = _squash(_nav(_page(client, _fresh(viewed))))

    assert nav == BACKWARD
    _no_count(nav)


def test_qa05_a_hidden_stage_before_a_readable_path_is_the_backward_sentence(client, narrow):
    secret = _secret("PA-C SALA-57")
    first, second, viewed = (_teema(narrow, f"PA-C rada {n}") for n in (1, 2, 3))
    _continues(secret, first)
    _continues(first, second)
    _continues(second, viewed)
    client.force_login(narrow)

    body = _page(client, _fresh(viewed))
    nav = _squash(_nav(body))

    assert "Teema etapid" in nav
    assert nav.startswith(f"Teema etapid {BACKWARD} {first.display_reference}")
    assert FORBIDDEN not in nav
    assert secret.display_reference not in body and "SALA-57" not in body


def test_qa05_a_hidden_successor_on_the_banner_is_the_forward_sentence(client, narrow):
    old = _teema(narrow, "PA-C vana-58")
    secret = _secret("PA-C SALA-59")
    _continues(old, secret)
    client.force_login(narrow)

    body = _page(client, _fresh(old))

    assert f"Teema on suletud. {FORWARD}" in _squash(_banner(body))
    assert FORWARD in _squash(_nav(body))
    assert secret.display_reference not in body and "SALA-59" not in body


@pytest.mark.parametrize("hidden", [1, 3, True])
def test_qa05_the_header_reads_the_same_for_a_count_or_a_bool(specialist, monkeypatch, hidden):
    """`hidden_predecessors` is a count on 9eff0e9a and a bool after agent A's change;
    the header must print neither, and read identically for both."""
    shown = _teema(specialist, "PA-C NÄHTAV-60")
    viewed = _teema(specialist, "PA-C VAADATUD-61")
    crafted = Continuations(predecessors=(shown,), hidden_predecessors=hidden, hidden_before=True)
    monkeypatch.setattr(
        "app.matters.templatetags.continuations.continuations_for", lambda *_: crafted
    )
    request = RequestFactory().get(f"/teemad/{viewed.pk}/")
    request.user = specialist
    html = render_to_string(
        "matters/partials/header.html",
        {"matter": viewed, "request": request, "owners": [], "colleagues": []},
        request=request,
    )
    nav = _squash(_nav(html))

    assert nav == f"Eelnevad teemad: {shown.display_reference} {BACKWARD}"
    _no_count(nav)


def test_qa05_seotud_materjalid_is_silent_about_a_hidden_stage(client, narrow):
    shown = _teema(narrow, "PA-C NÄHTAV-62")
    secret = _secret("PA-C SALA-63")
    viewed = _teema(narrow, "PA-C VAADATUD-64")
    _continues(shown, viewed)
    _continues(secret, viewed)
    client.force_login(narrow)

    body = _page(client, _fresh(viewed))
    fragment = client.get(
        reverse("related_materials:section", kwargs={"pk": viewed.pk}), HTTP_HX_REQUEST="true"
    ).content.decode()

    for html in (_section(body), fragment):
        text = _squash(html)
        assert shown.display_reference in text
        assert "avada ei saa" not in text and FORBIDDEN not in text
        assert secret.display_reference not in html and "SALA-63" not in html


# -- QA-05 on surfaces agent A owns: xfail(strict) until A's change lands --------------


@pytest.mark.xfail(strict=True, reason=A_PENDING)
def test_qa05_continuations_report_a_hidden_predecessor_as_yes_or_no(narrow):
    from app.matters.continuations import continuations_for

    viewed = _teema(narrow, "PA-C VAADATUD-65")
    for n in range(2):
        _continues(_secret(f"PA-C SALA-66-{n}"), viewed)

    chain = continuations_for(_fresh(viewed), narrow)

    assert chain.hidden_predecessors is True


@pytest.mark.xfail(strict=True, reason=A_PENDING)
def test_qa05_get_matter_flags_hidden_predecessors_without_a_count(narrow):
    viewed = _teema(narrow, "PA-C VAADATUD-67")
    for n in range(2):
        _continues(_secret(f"PA-C SALA-68-{n}"), viewed)
    viewed = _fresh(viewed)

    payload = readers.matter_snapshot(narrow, viewed.display_reference)["continuations"]
    text = readers.fetch_source(narrow, f"matter:{viewed.pk}")["text"]

    assert payload["hidden_predecessors"] is True
    _no_count(text)


def test_qa05_the_rail_says_yes_or_no_in_the_agreed_words(client, narrow):
    viewed = _teema(narrow, "PA-C VAADATUD-69")
    for n in range(2):
        _continues(_secret(f"PA-C SALA-70-{n}"), viewed)
    client.force_login(narrow)

    rail = _squash(_seotud(_page(client, _fresh(viewed))))

    assert BACKWARD in rail
    assert FORBIDDEN not in rail
    _no_count(rail)


def test_qa04_the_rail_calls_a_deleted_successor_unopenable(signed_in, specialist):
    old = _teema(specialist, "PA-C vana-71")
    gone = _teema(specialist, "PA-C KUSTUTATUD-72")
    _continues(old, gone)
    Matter.all_objects.filter(pk=gone.pk).update(deleted_at=timezone.now())

    rail = _squash(_seotud(_page(signed_in, _fresh(old))))

    assert FORWARD in rail
    assert FORBIDDEN not in rail
