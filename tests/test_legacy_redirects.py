"""The intentional bookmark redirects, in one inventory (audit 2026-10-28, §8.4, §9.4).

Each of these routes exists only so that an old bookmark, a pasted link or a
saved query still lands on the page it meant. None is dead code, and none is
reversed anywhere in the application — which is exactly what made them look
removable to a dead-code search. Several are also asserted beside the feature
that retired them (tests/test_department_page.py, test_teemad_consolidation.py,
test_arvamused_workspace_retirement.py); this file names all of them by route
name in one place, so removing one fails here, on purpose, with the reason.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

#: (route name, destination route name, permanent?) for the redirects that
#: carry the query string through unchanged.
QUERY_PRESERVING = [
    ("matters:overview_legacy", "matters:department", True),
    ("matters:department_work", "matters:department", True),
    ("matters:my_work_legacy", "matters:my_work", True),
]

#: The retired `/jalgimine/` pages' old addresses: one hop, never a chain.
INTELLIGENCE_LEGACY = [
    "intelligence:important_dates_legacy",
    "intelligence:effective_dates_legacy",
    "intelligence:work_victories_legacy",
]


@pytest.mark.parametrize(("name", "destination", "permanent"), QUERY_PRESERVING)
def test_a_bookmarked_address_still_lands_with_its_query(
    client, specialist, name, destination, permanent
) -> None:
    client.force_login(specialist)
    response = client.get(f"{reverse(name)}?periood=kuu&vaade=valdkonniti")

    assert response.status_code == (301 if permanent else 302)
    assert response["Location"] == f"{reverse(destination)}?periood=kuu&vaade=valdkonniti"
    assert client.get(response["Location"]).status_code == 200


@pytest.mark.parametrize("name", INTELLIGENCE_LEGACY)
def test_a_retired_tracking_address_redirects_once_to_a_live_page(client, specialist, name):
    client.force_login(specialist)
    response = client.get(reverse(name))

    assert response.status_code == 302
    landed = client.get(response["Location"])
    assert landed.status_code == 200, "one hop, to a page that renders — not a chain"


def test_the_retired_position_page_redirects_after_authorization(
    client, specialist, normal_matter
) -> None:
    """`matter_position`: temporary, and only once the Matter is visible to the caller."""
    client.force_login(specialist)
    response = client.get(reverse("matters:matter_position", kwargs={"pk": normal_matter.pk}))

    assert response.status_code == 302
    assert response["Location"].startswith(
        reverse("matters:matter_documents", kwargs={"pk": normal_matter.pk})
    )
    assert client.get(response["Location"]).status_code == 200
