"""«Eelvaade» — the register's quick topic preview (UX-006, app/matters/preview.py).

What the drawer's fragment must hold: one Teema's step, dates, procedure and
last acts, read-only, for exactly the readers who may open that Teema, at a
bounded cost — and the row's icon that asks for it.
"""

from __future__ import annotations

import sys
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core import authorization
from app.core.authorization import Scope
from app.core.enums import Visibility
from app.matters import legal_process, preview
from app.matters.services import add_entry, change_stage
from app.submissions.enums import SubmissionStatus
from app.workflow.enums import Track
from app.workflow.models import StageVocabulary
from app.workflow.services import set_next_action_for_new_work
from app.workflow.stage_flow import MONITORING_STOPPED
from tests import factories

pytestmark = pytest.mark.django_db


def _url(matter) -> str:
    return reverse("matters:matter_preview", kwargs={"pk": matter.pk})


def _filed(owner, **kwargs):
    """A Teema with a summary, an open step, a deadline ahead and three notes."""
    matter = factories.MatterFactory(
        owner=owner,
        title=kwargs.pop("title", "Näidisseadus eelvaate jaoks"),
        brief_summary="Ettevõtjatele tähendab see uut aruandluskohustust.",
        **kwargs,
    )
    for number in range(4):
        add_entry(matter=matter, body=f"<p>Näidismärge {number}</p>", author=owner)
    set_next_action_for_new_work(
        matter=matter,
        text="Koosta arvamuse kavand",
        target_date=timezone.localdate() + timedelta(days=5),
        responsible=owner,
        actor=owner,
    )
    factories.ImportantDateFactory(
        matter=matter,
        title="Kooskõlastusringi lõpp",
        date_value=timezone.localdate() + timedelta(days=20),
        period_end=timezone.localdate() + timedelta(days=20),
    )
    return matter


def test_the_preview_holds_what_the_row_cannot(client, specialist):
    matter = _filed(specialist)
    client.force_login(specialist)

    response = client.get(_url(matter))

    assert response.status_code == 200
    body = response.content.decode()
    assert "data-mpreview-body" in body
    assert "Näidisseadus eelvaate jaoks" in body
    assert "uut aruandluskohustust" in body, "the summary, read-only"
    assert "Koosta arvamuse kavand" in body, "the current step"
    assert "Kooskõlastusringi lõpp" in body, "the next milestone beside the step"
    assert "Viimased tegevused" in body
    # The newest three rows of Teema käik, not the fourth.
    assert "Näidismärge 3" in body
    assert "Näidismärge 0" not in body
    assert reverse("matters:matter_detail", kwargs={"pk": matter.pk}) in body
    assert response["Cache-Control"] == "private, no-store"


def test_the_preview_is_read_only(client, specialist):
    """No form, no POST target and no editable summary in the drawer."""
    matter = _filed(specialist)
    client.force_login(specialist)

    body = client.get(_url(matter)).content.decode()

    assert "<form" not in body
    assert "csrfmiddlewaretoken" not in body
    assert "update_summary" not in body and "Muuda" not in body


def test_the_preview_refuses_a_post(client, specialist):
    matter = _filed(specialist)
    client.force_login(specialist)

    assert client.post(_url(matter)).status_code == 405


def test_the_preview_of_a_teema_nobody_may_see_is_not_found(client, administrator, specialist):
    """404, the answer `get_visible_matter` gives: a refusal would confirm it exists."""
    matter = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    client.force_login(administrator)

    response = client.get(_url(matter))

    assert response.status_code == 404
    assert matter.title not in response.content.decode()


def test_an_unknown_teema_is_not_found(client, specialist):
    client.force_login(specialist)
    missing = reverse(
        "matters:matter_preview", kwargs={"pk": "00000000-0000-0000-0000-000000000000"}
    )

    assert client.get(missing).status_code == 404


def test_signed_out_the_preview_asks_for_sign_in(client, specialist):
    matter = _filed(specialist)

    response = client.get(_url(matter))

    assert response.status_code == 302
    assert "Näidisseadus" not in response.content.decode()


@pytest.fixture
def narrowed(monkeypatch):
    """Make a user read through a participation-narrowed scope.

    No request builds that scope since docs/adr/0181 — every authorized reader
    reads everything — but every child read still derives its visibility from
    it, and a hand-built scope is how that derivation is proved
    (tests/test_child_projection_visibility.py does the same). Patched wherever
    a module holds its own reference to `scope_for_user`.
    """
    real = authorization.scope_for_user
    users: set = set()

    def scope(user):
        if getattr(user, "pk", None) in users:
            return Scope(
                user=user,
                is_authenticated=True,
                sees_restricted_by_role=False,
                break_glass_grant_id=None,
            )
        return real(user)

    for module in list(sys.modules.values()):
        if getattr(module, "__name__", "").startswith("app.") and (
            getattr(module, "scope_for_user", None) is real
        ):
            monkeypatch.setattr(module, "scope_for_user", scope)

    def narrow(user):
        users.add(user.pk)
        return user

    return narrow


def test_restricted_children_of_a_visible_teema_stay_out_of_the_preview(
    client, specialist, other_specialist, narrowed, capture_evidence
):
    """A step, a milestone and a note restricted below their Matter are not named."""
    matter = factories.MatterFactory(owner=specialist, title="Avalik näidisteema")
    add_entry(matter=matter, body="<p>Avalik märge</p>", author=specialist)
    add_entry(
        matter=matter,
        body="<p>Piiratud märge</p>",
        author=specialist,
        visibility_override=Visibility.RESTRICTED,
    )
    action = set_next_action_for_new_work(
        matter=matter, text="Piiratud samm", responsible=specialist, actor=specialist
    )
    type(action).objects.filter(pk=action.pk).update(visibility_override=Visibility.RESTRICTED)
    factories.ImportantDateFactory(
        matter=matter,
        title="Piiratud tähtaeg",
        date_value=timezone.localdate() + timedelta(days=3),
        period_end=timezone.localdate() + timedelta(days=3),
        visibility_override=Visibility.RESTRICTED,
    )

    # A sent opinion is a «Viimased tegevused» source and prints its title; a
    # document is not a source at all — neither may surface below the line.
    restricted_file = capture_evidence(
        matter,
        b"%PDF-1.4 synthetic",
        "piiratud.pdf",
        "application/pdf",
        title="Piiratud dokument",
        visibility_override=Visibility.RESTRICTED,
    )
    public_file = capture_evidence(matter, b"%PDF-1.4 avalik", "avalik.pdf", "application/pdf")
    factories.SubmissionFactory(
        matter=matter,
        title="Piiratud arvamus",
        status=SubmissionStatus.SENT,
        sent_at=timezone.now(),
        final_version=restricted_file,
        visibility_override=Visibility.RESTRICTED,
    )
    factories.SubmissionFactory(
        matter=matter,
        title="Avalik arvamus",
        status=SubmissionStatus.SENT,
        sent_at=timezone.now() - timedelta(hours=1),
        final_version=public_file,
    )

    client.force_login(specialist)
    owner_view = client.get(_url(matter)).content.decode()
    assert "Piiratud samm" in owner_view and "Piiratud arvamus" in owner_view

    client.force_login(narrowed(other_specialist))
    response = client.get(_url(matter))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Avalik märge" in body
    assert "Avalik arvamus" in body
    assert "Piiratud märge" not in body
    assert "Piiratud arvamus" not in body
    assert "Piiratud dokument" not in body
    assert "Piiratud samm" not in body
    assert "Piiratud tähtaeg" not in body

    secret = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    assert client.get(_url(secret)).status_code == 404


def test_the_preview_costs_at_most_fifteen_queries(
    client, specialist, django_assert_max_num_queries
):
    """The drawer is opened while scanning a list; it must stay cheap (UX-006).

    Measured on a Teema with a step, a milestone, a summary and more history
    than the drawer lists, so every read it makes is exercised.
    """
    matter = _filed(specialist)
    client.force_login(specialist)
    client.get(_url(matter))  # session, persona and the like settle first

    with django_assert_max_num_queries(15):
        response = client.get(_url(matter))
    assert response.status_code == 200


def _stopped(owner):
    """A Teema whose `Hetkeseis` became «Rohkem ei tegele» while the bill was in
    the Riigikogu — the stage, not only the disposition."""
    matter = _filed(owner, track=Track.DOMESTIC.value)
    change_stage(matter=matter, stage=StageVocabulary.objects.get(key="parliament"), actor=owner)
    change_stage(
        matter=matter,
        stage=StageVocabulary.objects.get(key=MONITORING_STOPPED),
        actor=owner,
        follow_ups_confirmed=True,
    )
    matter.refresh_from_db()
    assert matter.stage.key == MONITORING_STOPPED
    return matter


def test_a_teema_koda_stopped_following_costs_no_more_than_any_other(
    client, specialist, django_assert_max_num_queries
):
    """«Rohkem ei tegele» used to cost a 16th query: `phase_context` read the
    period before it on its own. The preview reads it in the Matter's statement."""
    matter = _stopped(specialist)
    client.force_login(specialist)
    client.get(_url(matter))

    with django_assert_max_num_queries(15):
        response = client.get(_url(matter))
    assert response.status_code == 200


def test_a_stopped_teema_stands_where_the_teema_page_rail_puts_it(specialist):
    """The folded read gives the answer `phase_context`'s own read gives."""
    matter = _stopped(specialist)
    read = preview.preview_matter(specialist, matter.pk)
    assert read is not None

    label, phases = preview.procedure_strip(read)

    reference = legal_process.phase_context(matter=matter)
    assert reference.stage_key == "parliament"
    assert label == reference.pattern.label
    current = [phase.label for phase in phases if phase.state == "current"]
    assert current == [
        reference.pattern.nodes[reference.pattern.index_of_phase(reference.current_phase)].label
    ]


def test_every_register_row_offers_the_preview_without_prefetching_it(client, specialist):
    """One quiet icon per row naming its Teema; no fragment is loaded with the list."""
    first = factories.MatterFactory(owner=specialist, title="Esimene näidisteema")
    second = factories.MatterFactory(owner=specialist, title="Teine näidisteema")
    client.force_login(specialist)

    body = client.get(reverse("matters:matter_list")).content.decode()

    for matter in (first, second):
        assert f'data-mpreview-open="{_url(matter)}"' in body
        assert f'aria-label="Eelvaade: {matter.title}"' in body
        assert f'data-matter-row="{matter.pk}"' in body
    assert "data-mpreview-body" not in body, "nothing is prefetched"
    assert "data-mpreview " in body or "data-mpreview hidden" in body, "the drawer shell"


def test_saabunud_rows_offer_the_preview_too(client, specialist):
    """Saabunud renders the same table partial (decision N-C1: Teemad + Saabunud)."""
    matter = factories.MatterFactory(owner=None, title="Saabunud näidisteema")
    client.force_login(specialist)

    body = client.get(reverse("matters:inbox")).content.decode()

    assert f'data-mpreview-open="{_url(matter)}"' in body
