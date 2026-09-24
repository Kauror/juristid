"""Malformed and hostile input is refused, never a 500, never a write (ENG-046).

Everything here is something a person can put in an address bar or a form by
hand. Each was a 500 on main before this change, reproduced by the sweep at the
bottom of this module and by the named cases above it:

* a NUL byte in any query parameter that reached a text comparison — the
  register, every Statistika tab, a Matter's documents, the opinion block;
* an out-of-range integer: ``?nihe=999999999999`` on the chronology,
  ``?aasta=999999999999`` on a Matter's documents (``bigint out of range``);
* an ISO date that is not a day: ``?joustub_alates=2026-02-30``
  (``parse_date`` raises where ``parse_estonian_date`` answers None);
* an unknown ``failityyp`` or ``seisund`` on the materials export.

And three that were not 500s but were wrong: an unreadable review date
*cleared* the stored one, a non-UUID evidence choice reached a primary-key
lookup, and a ``next`` that was a URL name or another host's address was
followed.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Any

import pytest
from django.db import connection, transaction
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from app.audit.models import ChangeEvent
from app.core.dates import parse_flexible_date, read_flexible_date
from app.core.request_params import (
    bounded_int,
    choice_or_default,
    has_nul,
    safe_local_path,
    text_or_empty,
    uuid_or_none,
)
from app.matters.models import Matter
from app.submissions.services import create_submission
from app.workflow.enums import ActionKind, ActionStatus, DateSemantics
from app.workflow.models import NextAction
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

HOST = "testserver"


# ---------------------------------------------------------------------------
# The shared readers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, 7),
        ("", 7),
        ("12", 12),
        (" 12 ", 12),
        ("-1", 7),
        ("abc", 7),
        ("1e3", 7),
        ("999999999999", 7),
        ("9" * 3000, 7),
        ("1\x002", 7),
    ],
)
def test_bounded_int_answers_the_default_for_anything_unusable(raw, expected):
    assert bounded_int(raw, default=7, minimum=0, maximum=1000) == expected


def test_bounded_int_includes_both_ends():
    assert bounded_int("0", default=5, minimum=0, maximum=10) == 0
    assert bounded_int("10", default=5, minimum=0, maximum=10) == 10
    assert bounded_int("11", default=5, minimum=0, maximum=10) == 5


def test_uuid_or_none():
    value = uuid.uuid4()
    assert uuid_or_none(str(value)) == value
    assert uuid_or_none(f" {value} ") == value
    for raw in (None, "", "abc", "not-a-uuid", f"{value}\x00", "1" * 3000):
        assert uuid_or_none(raw) is None


def test_choice_or_default():
    assert choice_or_default("PDF", {"PDF", "DOCX"}, "") == "PDF"
    assert choice_or_default("pdf", {"PDF"}, "") == ""
    assert choice_or_default("PDF\x00", {"PDF"}, None) is None


def test_text_or_empty_refuses_rather_than_cleans_a_nul():
    assert text_or_empty("  otsing ") == "otsing"
    assert text_or_empty("a\x00b") == ""
    assert text_or_empty(None) == ""
    assert has_nul("a\x00") and not has_nul("a") and not has_nul(None)


@pytest.mark.parametrize(
    "candidate",
    ["/teemad/", "/teemad/?leht=2", "/teemad/#ankur", "/minu-too/"],
)
def test_a_local_path_is_a_redirect_target(candidate):
    assert safe_local_path(candidate, host=HOST) == candidate


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        None,
        "https://evil.example/",
        "//evil.example/",
        "/\\evil.example/",
        "\\\\evil.example",
        "http://testserver/teemad/",
        "teemad/",
        "matters:overview",
        "javascript:alert(1)",
        "/teemad/\x00",
        "/teemad/\r\nLocation: https://evil.example/",
        " //evil.example/",
    ],
)
def test_anything_but_a_local_path_is_not(candidate):
    assert safe_local_path(candidate, host=HOST) == ""


@pytest.mark.parametrize(
    ("raw", "value", "invalid"),
    [
        (None, None, False),
        ("", None, False),
        ("   ", None, False),
        ("7.9.2026", dt.date(2026, 9, 7), False),
        ("2026-09-07", dt.date(2026, 9, 7), False),
        ("31.02.2026", None, True),
        ("2026-02-30", None, True),
        ("abc", None, True),
        ("7.9.2026\x00", None, True),
    ],
)
def test_a_typed_date_is_empty_a_day_or_not_a_day(raw, value, invalid):
    reading = read_flexible_date(raw)
    assert reading.value == value
    assert reading.invalid is invalid
    assert reading.empty is (value is None and not invalid)
    # The filter-side reader keeps its old contract and never raises.
    assert parse_flexible_date(raw) == value


# ---------------------------------------------------------------------------
# The reproduced 500s, by name
# ---------------------------------------------------------------------------


@pytest.fixture
def owned(specialist):
    return factories.MatterFactory(owner=specialist, reference_year=2026, reference_number=5)


def _reproduced(matter: Matter) -> list[tuple[str, dict[str, str]]]:
    documents = reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    timeline = reverse("matters:timeline_page", kwargs={"pk": matter.pk})
    export = reverse("reporting:export", kwargs={"slug": "materjalid"})
    return [
        (reverse("matters:matter_list"), {"arvamus_q": "\x00"}),
        (reverse("matters:matter_list"), {"valdkond": "a\x00b"}),
        (reverse("matters:matter_list"), {"joustub_alates": "2026-02-30"}),
        (reverse("matters:matter_list"), {"joustub_kuni": "2026-02-30"}),
        (reverse("reporting:overview"), {"hetkeseis": "\x00"}),
        (reverse("reporting:matters"), {"silt": "\x00"}),
        (reverse("reporting:historical"), {"paritolu": "\x00"}),
        (reverse("submissions:embedded_block"), {"arvamus_q": "a\x00b"}),
        (documents, {"otsi": "\x00"}),
        (documents, {"aasta": "999999999999"}),
        (timeline, {"nihe": "999999999999"}),
        (export, {"failityyp": "abc"}),
        (export, {"seisund": "abc"}),
        (reverse("submissions:sent"), {"saaja": "abc"}),
        (reverse("submissions:sent"), {"vastutaja": "abc"}),
    ]


def test_every_reproduced_address_is_answered_without_a_server_error(client, specialist, owned):
    client.force_login(specialist)
    client.raise_request_exception = False
    for path, params in _reproduced(owned):
        response = client.get(path, params)
        assert response.status_code < 500, (path, params, response.status_code)


def test_a_nul_byte_is_a_400_in_estonian_before_any_view_runs(client, specialist):
    client.force_login(specialist)
    response = client.get(reverse("matters:matter_list"), {"otsi": "a\x00b"})
    assert response.status_code == 400
    assert "Päringut ei saa lugeda" in response.content.decode()
    assert "\x00" not in response.content.decode()


def test_a_nul_byte_in_a_form_writes_nothing(client, specialist, owned):
    client.force_login(specialist)
    before = ChangeEvent.objects.count()
    response = client.post(
        reverse("matters:assign_owner", kwargs={"pk": owned.pk}),
        {"owner": str(specialist.pk), "next": "/teemad/\x00"},
    )
    assert response.status_code == 400
    assert ChangeEvent.objects.count() == before


def test_an_unknown_file_type_or_state_is_not_found_on_the_list_and_the_export(client, specialist):
    client.force_login(specialist)
    for name in ("reporting:materials", "reporting:export"):
        kwargs = {"slug": "materjalid"} if name == "reporting:export" else {}
        for params in ({"failityyp": "abc"}, {"seisund": "abc"}):
            response = client.get(reverse(name, kwargs=kwargs), params)
            assert response.status_code == 404, (name, params)
    ok = client.get(
        reverse("reporting:export", kwargs={"slug": "materjalid"}), {"failityyp": "PDF"}
    )
    assert ok.status_code == 200


def test_an_unreadable_recipient_or_owner_is_refused_with_a_sentence(client, specialist):
    client.force_login(specialist)
    for params, sentence in (
        ({"saaja": "abc"}, "Saajat ei leitud."),
        ({"vastutaja": "abc"}, "Vastutajat ei leitud."),
    ):
        response = client.get(reverse("submissions:sent"), params)
        assert response.status_code == 200
        assert sentence in response.content.decode()


def test_an_out_of_range_offset_is_the_first_slice(client, specialist, owned):
    client.force_login(specialist)
    url = reverse("matters:timeline_page", kwargs={"pk": owned.pk})
    assert client.get(url, {"nihe": "999999999999"}).content == client.get(url).content


def test_an_unusable_year_is_not_applied_and_not_named(client, specialist, owned):
    client.force_login(specialist)
    url = reverse("matters:matter_documents", kwargs={"pk": owned.pk})
    response = client.get(url, {"aasta": "999999999999"})
    assert response.status_code == 200
    assert response.context["document_filters"]["aasta"] == ""


def test_an_evidence_choice_that_is_not_an_identifier_writes_nothing(client, specialist, owned):
    client.force_login(specialist)
    submission = create_submission(matter=owned, title="Arvamus", actor=specialist)
    before = ChangeEvent.objects.count()
    response = client.post(
        reverse("submissions:attach_evidence", kwargs={"pk": submission.pk}),
        {"existing_version": "abc"},
    )
    assert response.status_code == 302
    submission.refresh_from_db()
    assert submission.final_version_id is None
    assert ChangeEvent.objects.count() == before


# ---------------------------------------------------------------------------
# A date that is not a day is refused, not read as "no date"
# ---------------------------------------------------------------------------


@pytest.fixture
def monitored(specialist, owned):
    return set_next_action(
        matter=owned,
        text="Jälgin menetlust",
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=dt.date(2026, 10, 1),
        actor=specialist,
    )


@pytest.mark.parametrize("typed", ["31.02.2026", "2026-02-30", "abc"])
def test_an_unreadable_review_date_keeps_the_stored_one(
    client, specialist, owned, monitored, typed
):
    client.force_login(specialist)
    before = ChangeEvent.objects.count()
    response = client.post(
        reverse("matters:review_action", kwargs={"pk": owned.pk, "action_id": monitored.pk}),
        {"next_review_date": typed},
    )
    assert response.status_code == 400
    assert "Kirjuta kuupäev kujul 7.9.2026." in response.content.decode()
    action = NextAction.objects.get(pk=monitored.pk)
    assert action.target_date == dt.date(2026, 10, 1)
    assert action.status == ActionStatus.OPEN
    assert ChangeEvent.objects.count() == before


def test_an_empty_review_date_still_means_no_next_date(client, specialist, owned, monitored):
    client.force_login(specialist)
    response = client.post(
        reverse("matters:review_action", kwargs={"pk": owned.pk, "action_id": monitored.pk}),
        {"next_review_date": ""},
    )
    assert response.status_code == 200
    assert NextAction.objects.get(pk=monitored.pk).target_date is None


def test_a_readable_review_date_is_stored(client, specialist, owned, monitored):
    client.force_login(specialist)
    client.post(
        reverse("matters:review_action", kwargs={"pk": owned.pk, "action_id": monitored.pk}),
        {"next_review_date": "15.10.2026"},
    )
    assert NextAction.objects.get(pk=monitored.pk).target_date == dt.date(2026, 10, 15)


# ---------------------------------------------------------------------------
# `next` is a path on this site, or it is ignored
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "next_value",
    ["https://evil.example/", "//evil.example/", "/\\evil.example/", "matters:overview", "teemad/"],
)
def test_a_foreign_or_named_next_lands_on_the_default(client, specialist, next_value):
    client.force_login(specialist)
    matter = factories.MatterFactory(owner=None)
    response = client.post(
        reverse("matters:assign_owner", kwargs={"pk": matter.pk}),
        {"owner": str(specialist.pk), "next": next_value},
    )
    assert response.status_code == 302
    assert response["Location"] == reverse("matters:matter_list")


def test_a_local_next_is_followed_exactly(client, specialist):
    client.force_login(specialist)
    matter = factories.MatterFactory(owner=None)
    response = client.post(
        reverse("matters:assign_owner", kwargs={"pk": matter.pk}),
        {"owner": str(specialist.pk), "next": "/teemad/?vastutaja=puudub"},
    )
    assert response["Location"] == "/teemad/?vastutaja=puudub"


# ---------------------------------------------------------------------------
# The bounded sweep
# ---------------------------------------------------------------------------

#: Every query parameter name this application reads, measured from the source
#: when this test was written. A sweep with a parameter nobody reads costs a
#: request and proves nothing, so this is the list rather than a generator.
PARAMETER_NAMES = (
    "_otsing aasta action adressaat ajajoon alates allikas arvamus arvamus_q arvamus_vaade "
    "asutus avatud channel data_class decision disposition engagement existing_version fail "
    "failityyp hetkeseis intake jarjesta jarjestus joustub_alates joustub_kuni joustumine "
    "kandidaat kaupa kind klass koik kuni kuu kuupaev laadi label leht liik materjalid matter "
    "menetlusliik multiple next nihe olek organisation organisation_type otsi owner paevad "
    "paritolu peidetud periood piir pin policy_areas precision process_phase publication q "
    "reference revision roll saaja saatja seisund seotud silt stage suggestion_state suletud "
    "tagasi tahtajad target teema tegevus too too_alates too_kuni too_vastutaja toovoit tuhjad "
    "ulatus vaade valdkond vali vastutaja viide voog vorm"
).split()

HOSTILE_VALUES = ("\x00", "abc", "-1", "999999999999", "not-a-uuid", "2026-02-30", "%", "1" * 600)

#: Routes a sweep must not touch: the admin has its own tests, and signing out
#: would end the session the sweep is using.
_SKIPPED = ("admin", "__", "valju", "logout")


def _routes(patterns: Any, prefix: str = "") -> Any:
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _routes(pattern.url_patterns, prefix + str(pattern.pattern))
        elif isinstance(pattern, URLPattern):
            yield prefix + str(pattern.pattern)


def _sweep_paths(matter: Matter) -> list[str]:
    paths = []
    for route in sorted(set(_routes(get_resolver().url_patterns))):
        if any(part in route for part in _SKIPPED):
            continue
        names = [name for _, name in re.findall(r"<(?:(\w+):)?(\w+)>", route)]
        if any(name not in ("pk", "slug") for name in names):
            continue
        path = "/" + re.sub(r"<(?:\w+:)?pk>", str(matter.pk), route)
        if "<slug:slug>" in path:
            paths += [
                path.replace("<slug:slug>", slug)
                for slug in ("teemad", "arvamused", "materjalid", "andmekvaliteet")
            ]
        else:
            paths.append(path)
    return paths


def _answer(client: Any, method: str, path: str, data: dict[str, str]) -> int:
    """One request, in its own savepoint, so a failure cannot poison the next."""
    savepoint = transaction.savepoint()
    status = getattr(client, method)(path, data).status_code
    if status >= 500:
        transaction.savepoint_rollback(savepoint)
        connection.needs_rollback = False
    else:
        transaction.savepoint_commit(savepoint)
    return status


@pytest.mark.parametrize("value", HOSTILE_VALUES, ids=repr)
def test_no_address_answers_a_hostile_value_with_a_server_error(client, specialist, owned, value):
    """Every parameterless route and every Matter route, every parameter name at once."""
    client.force_login(specialist)
    client.raise_request_exception = False
    failures = [
        path
        for path in _sweep_paths(owned)
        if _answer(client, "get", path, dict.fromkeys(PARAMETER_NAMES, value)) >= 500
    ]
    assert failures == []


def test_a_nul_in_every_field_of_every_matter_form_writes_nothing(client, specialist, owned):
    client.force_login(specialist)
    client.raise_request_exception = False
    before = (ChangeEvent.objects.count(), Matter.all_objects.count())
    statuses = {
        _answer(client, "post", path, dict.fromkeys(PARAMETER_NAMES, "\x00"))
        for path in _sweep_paths(owned)
    }
    assert statuses == {400}
    assert (ChangeEvent.objects.count(), Matter.all_objects.count()) == before
