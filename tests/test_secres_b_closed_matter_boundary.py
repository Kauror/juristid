"""Assessor B (WS1, 2026-10-29): every business write, fired at a CLOSED Matter.

`tests/test_closed_matter_stage_boundary.py` pins the stage door and
`tests/test_business_write_boundary.py` pins *who* may write; neither fires the
whole write matrix at a file that is shut. This does, as a SPECIALIST, reusing
that matrix and its world with every Matter closed first.

The rule is docs/adr/0075 §12 / 0076 §2: *closed → no new or advanced
canonical business content*, not *closed → immutable*. So the assertion has
two halves:

* every route **not** named in `CORRECTIONS_ALLOWED_WHEN_CLOSED` must leave its
  probe and the audit trail exactly as they were;
* the routes that are named there are the ones the product documents as
  corrections, personal data or work that is not bound to the Matter's state.
  Each entry carries the place that says so. A new route that writes on a
  closed Matter fails here until somebody writes its reason down.

Observed on main 359e34bf (assessor B's probe): 11 of 78 matrix rows change
state on a closed Matter; all 11 are documented below. Status: boundary
verified sound; three entries are raised as owner questions in
WS1B-FINDINGS (response deadline, published-overview link, suggestion
dismissals) because their closed-Matter behaviour is stated only in code.
"""

from __future__ import annotations

import pytest

from app.audit.models import ChangeEvent
from app.matters.models import Matter
from app.matters.services import close_matter
from tests import factories
from tests.test_business_write_boundary import WRITE_ROUTES, _build_world, _post

pytestmark = pytest.mark.django_db

#: (route name, label) -> where the closed-Matter behaviour is decided.
CORRECTIONS_ALLOWED_WHEN_CLOSED: dict[tuple[str, str], str] = {
    # Not bound to any Matter's state.
    ("matters:matter_create", "Uus teema"): "creates a new Matter",
    ("matters:intake_stage", "Uus teema: faili ettevalmistamine"): "staging session, no Matter",
    ("organisations:quick_create", "Asutuse kiirlisamine"): "reference data (docs/adr/0063)",
    # Personal data, outside the business-write boundary.
    ("matters:save_note", "Isikliku märkme salvestamine"): "docs/adr/0076 §6 «Personal notes»",
    # Documented corrections on a closed file.
    ("matters:response_deadline", "Arvamuse tähtaja määramine"): (
        "app/matters/views.py `_header_context`: «other corrections on a closed file "
        "stay allowed» (RULE-03); app/matters/response_deadlines.py"
    ),
    ("matters:correct_website_overview", "Ülevaate / uudise lingi parandamine"): (
        "app/matters/services.py `correct_website_overview_link`: «Allowed on a closed "
        "Matter, and that is the point»"
    ),
    ("submissions:metadata", "Arvamuse märksõnad ja seosed"): (
        "app/submissions/views.py `metadata`; docs/adr/0075 §12, 0093 §3"
    ),
    ("submissions:withdraw", "Arvamuse tagasivõtmine"): (
        "app/submissions/services.py `withdraw_submission`: «A closed Matter may be "
        "corrected this way»"
    ),
    ("matters:add_working_document", "Töödokumendi lisamine"): (
        "docs/adr/0076 §6 «Working documents»: a SharePoint reference stays available"
    ),
    ("related_materials:dismiss", "Soovituse peitmine"): (
        "app/related_materials/services.py `_lock_pair(require_open=False)`"
    ),
    ("related_materials:restore", "Soovituse taastamine"): (
        "app/related_materials/services.py `_lock_pair(require_open=False)`"
    ),
    # «Kontrolli jõustumist» answers a commencement, which is normally on a
    # closed (in-force) file (docs/adr/0188).
    # «Kontrollitud» used to close the prompt before the other two answers ran;
    # since its retirement (2026-10-30) they are what answers it here.
    ("notifications:defer", "Jõustumise kontrolli edasilükkamine"): "docs/adr/0188",
    ("notifications:dismiss", "Jõustumise meeldetuletuse sulgemine"): "docs/adr/0188",
}


@pytest.fixture
def closed_world():
    world = _build_world()
    author = world["author"]
    for matter in Matter.objects.all():
        if matter.is_open:
            close_matter(
                matter=matter, actor=author, disposition="COMPLETED", follow_ups_confirmed=True
            )
    assert not Matter.objects.filter(is_open=True).exists()
    return world


def test_the_allowed_list_names_only_matrix_rows():
    rows = {(route.name, route.label) for route in WRITE_ROUTES}
    unknown = set(CORRECTIONS_ALLOWED_WHEN_CLOSED) - rows
    assert not unknown, unknown


def test_no_new_business_content_lands_on_a_closed_matter(client, closed_world):
    client.force_login(factories.UserFactory())
    moved = []
    for route in WRITE_ROUTES:
        key = (route.name, route.label)
        before = route.probe(closed_world)
        events = ChangeEvent.objects.count()
        response = _post(client, route, closed_world)
        assert response.status_code < 500, key
        changed = route.probe(closed_world) != before or ChangeEvent.objects.count() != events
        if changed and key not in CORRECTIONS_ALLOWED_WHEN_CLOSED:
            moved.append((*key, response.status_code))
    assert moved == [], "\n".join(map(str, moved))


def test_the_documented_exceptions_still_write_on_a_closed_matter(client, closed_world):
    """The other half: an entry above that no longer writes is a stale reason."""
    client.force_login(factories.UserFactory())
    stale = []
    for route in WRITE_ROUTES:
        key = (route.name, route.label)
        if key not in CORRECTIONS_ALLOWED_WHEN_CLOSED:
            continue
        before = route.probe(closed_world)
        events = ChangeEvent.objects.count()
        _post(client, route, closed_world)
        if route.probe(closed_world) == before and ChangeEvent.objects.count() == events:
            stale.append(key)
    assert stale == [], stale
