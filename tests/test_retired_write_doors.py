"""Superseded write routes are gone, not hidden (ENG-050A).

Each of these still accepted a POST after the page that posted to it went
away — a second workflow nobody saw or tested as part of ordinary use, drifting
behind the one that replaced it (`matters:complete_action` completed a step
without the `Mida tegid?` the current ✓ Tehtud requires). Their window has
ended, and the current workflow is the only write path:

* `matters:complete_action`, `matters:complete_work_item` → `PRAEGUNE TEGEVUS`
  (`matters:complete_current_action`);
* `matters:defer_action` → `Muuda` (`matters:set_action`) or `Vaatasin üle`
  (`matters:review_action`);
* `matters:add_engagement` → `+ Kaasamine` (`matters:add_engagement_compact`);
* `submissions:create` → `Lisa teemale → Koja arvamus`;
* `matters:compose`, the superseded composer (ENG-050A2) → one explicit act per
  save: `PRAEGUNE TEGEVUS`, a `LISA TEEMALE` panel, or a terminal `Hetkeseis`.
  One composer payload could carry several of those at once, so there is no
  honest single operation to send it to.

A stale tab's POST is a 404 and writes nothing. Not a redirect: a POST sent on
to another operation would carry a payload that operation never asked for.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import NoReverseMatch, URLResolver, get_resolver, reverse
from django.utils import timezone

from app.audit.models import ChangeEvent
from app.documents.models import Document
from app.intelligence.models import MatterImportantDate
from app.matters.models import Entry, Matter, MatterEngagement
from app.submissions.models import Submission
from app.workflow.enums import ActionKind, ActionStatus, DateSemantics
from app.workflow.models import NextAction
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

RETIRED_NAMES = [
    "matters:complete_action",
    "matters:complete_work_item",
    "matters:defer_action",
    "matters:add_engagement",
    "submissions:create",
    "matters:compose",
]


@pytest.fixture
def step(specialist):
    matter = factories.MatterFactory(owner=specialist)
    action = set_next_action(
        matter=matter,
        text="Saata arvamus ministeeriumile",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() + timedelta(days=3),
        actor=specialist,
    )
    return matter, action


def _stale_posts(matter, action):
    """What an old tab would still send, by address — the names no longer exist."""
    return [
        (f"/teemad/{matter.pk}/jargmiseks/{action.pk}/valmis/", {}),
        (f"/minu-too/valmis/{action.pk}/", {"next": "/minu-asjad/"}),
        (f"/teemad/{matter.pk}/jargmiseks/{action.pk}/lukka/", {"paevad": "7"}),
        (f"/teemad/{matter.pk}/kaasamine/", {"title": "Hiline kaasamine", "kind": "SURVEY"}),
        (
            f"/arvamused/teema/{matter.pk}/uus/",
            {"arvamus-title": "Hiline arvamus", "arvamus-kind": "FORMAL_OPINION"},
        ),
        # Everything the composer once took in one save: an entry, a next step,
        # an engagement, a deadline and a closure.
        (
            f"/teemad/{matter.pk}/sissekanne/",
            {
                "body": "<p>Hiline sissekanne.</p>",
                "next_text": "Hiline samm",
                "engagement_kind": "SURVEY",
                "engagement_audience": "Liikmed",
                "deadline_title": "Hiline tähtaeg",
                "closure_outcome": "COMPLETED",
            },
        ),
    ]


def _state(matter, action):
    action.refresh_from_db()
    return {
        "action": (action.status, action.target_date, action.ended_at),
        "actions": NextAction.objects.filter(matter=matter).count(),
        "entries": Entry.objects.filter(matter=matter).count(),
        "engagements": MatterEngagement.objects.filter(matter=matter).count(),
        "submissions": Submission.objects.filter(matter=matter).count(),
        "documents": Document.objects.filter(matter=matter).count(),
        "important_dates": MatterImportantDate.objects.filter(matter=matter).count(),
        "open": Matter.objects.values_list("is_open", flat=True).get(pk=matter.pk),
        "events": ChangeEvent.objects.filter(matter=matter).count(),
    }


def _route_names() -> set[str]:
    """Every namespaced route name the resolver knows, whatever its arguments."""
    names: set[str] = set()

    def walk(patterns, namespace=None):
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                walk(pattern.url_patterns, pattern.namespace or namespace)
            elif pattern.name:
                names.add(f"{namespace}:{pattern.name}" if namespace else pattern.name)

    walk(get_resolver().url_patterns)
    return names


def test_no_retired_route_name_resolves():
    names = _route_names()
    # The walk sees the live neighbours, so an empty answer cannot pass this.
    assert {"matters:complete_current_action", "matters:add_engagement_compact"} <= names
    assert names.isdisjoint(RETIRED_NAMES)
    with pytest.raises(NoReverseMatch):
        reverse("submissions:create", kwargs={"matter_id": "00000000-0000-0000-0000-000000000000"})


@pytest.mark.parametrize(
    "index", range(len(RETIRED_NAMES)), ids=[n.split(":")[1] for n in RETIRED_NAMES]
)
def test_a_stale_post_to_a_retired_door_is_404_and_writes_nothing(signed_in, step, index):
    matter, action = step
    before = _state(matter, action)
    url, payload = _stale_posts(matter, action)[index]

    response = signed_in.post(url, payload, headers={"HX-Request": "true"})

    assert response.status_code == 404, url
    assert _state(matter, action) == before
    assert action.status == ActionStatus.OPEN


def test_the_current_doors_still_answer(signed_in, step):
    """The replacements resolve and still write — nothing above took them along."""
    matter, action = step

    completed = signed_in.post(
        reverse("matters:complete_current_action", kwargs={"pk": matter.pk}),
        {"action_id": str(action.pk), "body": "Arvamus saadeti."},
        headers={"HX-Request": "true"},
    )
    added = signed_in.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}),
        {"audience": "Liikmed"},
        headers={"HX-Request": "true"},
    )

    assert completed.status_code == 200 and added.status_code == 200
    action.refresh_from_db()
    assert action.status == ActionStatus.COMPLETED
    assert MatterEngagement.objects.filter(matter=matter).count() == 1


def test_a_reader_meets_the_same_404_and_nothing_else(client, reader, step):
    """No alternative door: the retired addresses answer every actor the same way."""
    matter, action = step
    before = _state(matter, action)
    client.force_login(reader)

    for url, payload in _stale_posts(matter, action):
        assert client.post(url, payload).status_code == 404, url

    assert _state(matter, action) == before
