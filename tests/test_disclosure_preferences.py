"""Remembered blocks: the save and reset endpoints and the template tag (docs/adr/0194).

The endpoints write presentation state for the person the request is signed in
as and nobody else; the registry bounds what may be written; the tag reads a
person's choices once per request and falls back to each block's default.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template import Context, Template
from django.test import Client, RequestFactory
from django.urls import reverse

from app.accounts import disclosures
from app.accounts.models import DisclosurePreference
from tests import factories

pytestmark = pytest.mark.django_db

SAVE = "accounts:save_disclosure"
RESET = "accounts:reset_disclosures"
OPEN_BY_DEFAULT = "my_work.statistics"
CLOSED_BY_DEFAULT = "document.data"


def _rows(person) -> dict[str, bool]:
    return dict(DisclosurePreference.objects.filter(user=person).values_list("key", "is_open"))


def _signed_in(client: Client, person=None):
    person = person or factories.UserFactory()
    client.force_login(person)
    return person


# -- saving ------------------------------------------------------------------------


def test_a_choice_is_saved_for_the_sender_and_the_last_write_wins(client):
    person = _signed_in(client)

    assert client.post(reverse(SAVE), {"key": OPEN_BY_DEFAULT, "open": "0"}).status_code == 204
    assert client.post(reverse(SAVE), {"key": OPEN_BY_DEFAULT, "open": "1"}).status_code == 204

    assert _rows(person) == {OPEN_BY_DEFAULT: True}


@pytest.mark.parametrize("key", ["", "accordion-3", "matter.1234", "my_work.statistics "])
def test_an_unregistered_key_is_a_400_and_writes_nothing(client, key):
    person = _signed_in(client)
    assert client.post(reverse(SAVE), {"key": key, "open": "1"}).status_code == 400
    assert _rows(person) == {}


@pytest.mark.parametrize("value", ["", "true", "2", "open", "on"])
def test_any_other_value_is_a_400_and_writes_nothing(client, value):
    person = _signed_in(client)
    assert client.post(reverse(SAVE), {"key": OPEN_BY_DEFAULT, "open": value}).status_code == 400
    assert _rows(person) == {}


def test_an_anonymous_caller_writes_nothing(client):
    response = client.post(reverse(SAVE), {"key": OPEN_BY_DEFAULT, "open": "0"})
    assert response.status_code == 302
    assert not DisclosurePreference.objects.exists()


def test_a_get_is_refused(client):
    _signed_in(client)
    assert client.get(reverse(SAVE)).status_code == 405
    assert client.get(reverse(RESET)).status_code == 405


def test_both_writes_need_the_csrf_token():
    client = Client(enforce_csrf_checks=True)
    person = _signed_in(client)
    disclosures.remember(person, CLOSED_BY_DEFAULT, opened=True)

    assert client.post(reverse(SAVE), {"key": OPEN_BY_DEFAULT, "open": "0"}).status_code == 403
    assert client.post(reverse(RESET)).status_code == 403
    assert _rows(person) == {CLOSED_BY_DEFAULT: True}


def test_one_persons_write_does_not_touch_anothers(client):
    marko, sandra = factories.UserFactory(), factories.UserFactory()
    disclosures.remember(sandra, OPEN_BY_DEFAULT, opened=True)
    _signed_in(client, marko)

    client.post(reverse(SAVE), {"key": OPEN_BY_DEFAULT, "open": "0"})

    assert _rows(marko) == {OPEN_BY_DEFAULT: False}
    assert _rows(sandra) == {OPEN_BY_DEFAULT: True}


def test_there_is_no_parameter_naming_somebody_else(client):
    marko, sandra = factories.UserFactory(), factories.UserFactory()
    _signed_in(client, marko)

    client.post(
        reverse(SAVE),
        {"key": OPEN_BY_DEFAULT, "open": "0", "user": str(sandra.pk), "user_id": str(sandra.pk)},
    )

    assert _rows(sandra) == {}
    assert _rows(marko) == {OPEN_BY_DEFAULT: False}


# -- resetting ---------------------------------------------------------------------


def test_reset_deletes_only_the_senders_rows_and_returns_to_next(client):
    marko, sandra = factories.UserFactory(), factories.UserFactory()
    for person in (marko, sandra):
        disclosures.remember(person, OPEN_BY_DEFAULT, opened=False)
        disclosures.remember(person, CLOSED_BY_DEFAULT, opened=True)
    _signed_in(client, marko)

    response = client.post(reverse(RESET), {"next": reverse("haldus:index")})

    assert response.status_code == 302 and response["Location"] == reverse("haldus:index")
    assert _rows(marko) == {}
    assert _rows(sandra) == {OPEN_BY_DEFAULT: False, CLOSED_BY_DEFAULT: True}


def test_reset_will_not_redirect_off_site(client):
    _signed_in(client)
    response = client.post(reverse(RESET), {"next": "https://example.org/"})
    assert response.status_code == 302 and response["Location"] == reverse("haldus:index")


def test_an_anonymous_reset_deletes_nothing(client):
    disclosures.remember(factories.UserFactory(), OPEN_BY_DEFAULT, opened=False)
    assert client.post(reverse(RESET)).status_code == 302
    assert DisclosurePreference.objects.count() == 1


# -- the template tag --------------------------------------------------------------


def _render(person, *keys: str) -> str:
    request = RequestFactory().get("/")
    request.user = person
    source = "{% load disclosures %}" + "".join(
        f'<details {{% disclosure "{key}" %}}></details>' for key in keys
    )
    return Template(source).render(Context({"request": request}))


def test_a_block_nobody_touched_renders_its_default():
    html = _render(factories.UserFactory(), OPEN_BY_DEFAULT, CLOSED_BY_DEFAULT)
    assert f'data-disclosure="{OPEN_BY_DEFAULT}" data-disclosure-saved="1" open>' in html
    assert f'data-disclosure="{CLOSED_BY_DEFAULT}" data-disclosure-saved="0">' in html


def test_a_stored_choice_wins_over_the_default():
    person = factories.UserFactory()
    disclosures.remember(person, OPEN_BY_DEFAULT, opened=False)
    disclosures.remember(person, CLOSED_BY_DEFAULT, opened=True)

    html = _render(person, OPEN_BY_DEFAULT, CLOSED_BY_DEFAULT)

    assert f'data-disclosure="{OPEN_BY_DEFAULT}" data-disclosure-saved="0">' in html
    assert f'data-disclosure="{CLOSED_BY_DEFAULT}" data-disclosure-saved="1" open>' in html


def test_another_persons_choice_is_not_read():
    sandra = factories.UserFactory()
    disclosures.remember(sandra, OPEN_BY_DEFAULT, opened=False)
    html = _render(factories.UserFactory(), OPEN_BY_DEFAULT)
    assert 'data-disclosure-saved="1" open' in html


def test_an_anonymous_reader_gets_the_defaults_without_a_query(django_assert_num_queries):
    with django_assert_num_queries(0):
        html = _render(AnonymousUser(), OPEN_BY_DEFAULT, CLOSED_BY_DEFAULT)
    assert html.count(" open>") == 1


def test_several_blocks_on_a_page_cost_one_query(django_assert_num_queries):
    person = factories.UserFactory()
    disclosures.remember(person, CLOSED_BY_DEFAULT, opened=True)
    keys = list(disclosures.DISCLOSURES)[:6]

    with django_assert_num_queries(1):
        _render(person, *keys)
