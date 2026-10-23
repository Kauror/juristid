"""Under `local_password` the personal sign-in stays as it was (docs/adr/0145, 0186).

One fix this round: a fragment a page fetches after drawing (the top bar's
reminder count) during mandatory enrolment was redirected to the enrolment
page, which htmx swapped into the fragment's slot and fetched again — a loop
that never settled and a second code field on the page, seen in the browser
suite on the combined tree.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from tests.local_auth import apply_local_password, person, sign_in

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _local(settings):
    return apply_local_password(settings)


def test_an_htmx_fragment_during_enrolment_gets_nothing_to_swap(client):
    admin, _ = person(manage=True, delegate=True)
    sign_in(client, admin)  # lands on mandatory enrolment

    fragment = client.get(reverse("matters:my_work"), HTTP_HX_REQUEST="true")
    navigation = client.get(reverse("matters:my_work"))

    assert fragment.status_code == 403
    assert fragment.content == b""
    assert navigation.status_code == 302
    assert navigation["Location"] == reverse("accounts:security_enrol")
    assert client.get(reverse("accounts:security_enrol")).status_code == 200
