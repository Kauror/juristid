"""Restricted content stays hidden on every Stage-1 surface.

A UI that merely omits a row is not access control. These tests go at the
routes: list, detail, search, timeline, download and every child URL, for a user
who is not involved and for an administrator whose role is technical only.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.urls import reverse

from app.core.enums import Visibility
from app.documents.services import add_evidence_version, create_document
from app.matters.services import add_entry, create_matter
from app.submissions.services import create_submission
from app.workflow.services import set_next_action

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4 synthetic restricted evidence"


@pytest.fixture
def restricted_world(specialist):
    """A restricted Matter with one of every child record."""
    matter = create_matter(
        title="Piiratud teema tundlike tõenditega",
        actor=specialist,
        owner=specialist,
        visibility=Visibility.RESTRICTED,
    )
    entry = add_entry(matter=matter, body="<p>Tundlik sissekanne</p>", author=specialist)
    action = set_next_action(
        matter=matter,
        text="Tundlik tegevus",
        actor=specialist,
        target_date=date.today() + timedelta(days=5),
    )
    submission = create_submission(matter=matter, title="Tundlik arvamus", actor=specialist)
    document = create_document(matter=matter, title="Tundlik tõend", created_by=specialist)
    version = add_evidence_version(
        document=document,
        content=PDF,
        original_filename="tundlik.pdf",
        mime_type="application/pdf",
        uploaded_by=specialist,
    )
    return {
        "matter": matter,
        "entry": entry,
        "action": action,
        "submission": submission,
        "document": document,
        "version": version,
    }


def _urls(world):
    matter = world["matter"]
    return [
        reverse("matters:matter_detail", kwargs={"pk": matter.pk}),
        reverse("matters:matter_position", kwargs={"pk": matter.pk}),
        reverse("matters:matter_documents", kwargs={"pk": matter.pk}),
        reverse("matters:timeline_page", kwargs={"pk": matter.pk}),
        reverse("documents:download", kwargs={"pk": world["version"].pk}),
    ]


def test_the_owner_reaches_everything(client, specialist, restricted_world):
    client.force_login(specialist)
    for url in _urls(restricted_world):
        assert client.get(url, follow=True).status_code == 200, url


def test_an_uninvolved_specialist_reaches_nothing(client, reader, restricted_world):
    """404 rather than 403: a 403 would confirm the record exists.

    Followed, because one of these addresses is now a compatibility redirect
    (`matters:matter_position`, docs/adr/0060) and the whole point of this
    assertion is the *final* answer. It is 404 at the first hop — the retired
    route resolves the Matter before it reverses anything — and following proves
    the redirect did not hand an unauthorized caller a working URL.
    """
    client.force_login(reader)
    for url in _urls(restricted_world):
        assert client.get(url, follow=True).status_code == 404, url


def test_the_department_head_reaches_it(client, department_head, restricted_world):
    client.force_login(department_head)
    for url in _urls(restricted_world):
        assert client.get(url, follow=True).status_code == 200, url


def test_a_technical_administrator_does_not(client, administrator, restricted_world):
    """Administering the system is not permission to read the business content."""
    client.force_login(administrator)
    for url in _urls(restricted_world):
        assert client.get(url).status_code == 404, url


def test_a_superuser_alone_does_not_either(client, superuser, restricted_world):
    client.force_login(superuser)
    for url in _urls(restricted_world):
        assert client.get(url).status_code == 404, url


def test_it_is_absent_from_the_register_and_its_count(client, reader, restricted_world):
    client.force_login(reader)
    response = client.get(reverse("matters:matter_list"), {"olek": "koik"})
    assert response.context["page"].paginator.count == 0
    assert "Piiratud teema" not in response.content.decode()


def test_it_is_absent_from_my_work(client, other_specialist, restricted_world):
    client.force_login(other_specialist)
    response = client.get(reverse("matters:my_work"))
    body = response.content.decode()
    assert "Tundlik tegevus" not in body
    assert response.context["work"].open_matters == 0


def test_it_is_absent_from_search_results_and_snippets(client, reader, restricted_world):
    client.force_login(reader)
    response = client.get(reverse("search:search"), {"q": "piiratud"})
    assert response.context["result_count"] == 0
    assert "Piiratud teema" not in response.content.decode()


def test_the_owner_does_find_it(client, specialist, restricted_world):
    client.force_login(specialist)
    response = client.get(reverse("search:search"), {"q": "piiratud"})
    assert response.context["result_count"] == 1


def test_a_restricted_child_inside_a_normal_matter_stays_hidden(
    client, specialist, reader, normal_matter
):
    """Visibility is per record, not only per Matter."""
    add_entry(
        matter=normal_matter,
        body="<p>Ainult vastutajale nähtav</p>",
        author=specialist,
        visibility_override=Visibility.RESTRICTED,
    )
    add_entry(matter=normal_matter, body="<p>Kõigile nähtav</p>", author=specialist)

    client.force_login(reader)
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}))
    body = response.content.decode()
    assert response.status_code == 200
    assert "Kõigile nähtav" in body
    assert "Ainult vastutajale nähtav" not in body


def test_an_anonymous_visitor_is_sent_to_sign_in(client, restricted_world):
    for url in _urls(restricted_world):
        response = client.get(url)
        assert response.status_code in (302, 404), url


def test_posting_to_a_restricted_matter_is_refused(client, reader, restricted_world):
    """Write routes are protected, and now by the write gate rather than the
    visibility lookup.

    The 404 now comes from the write gate rather than from
    `get_visible_matter`: `@business_write_required` runs first, and this
    actor may not write. That is not a weaker guarantee — it is an earlier
    one — but it does mean this route can no longer *demonstrate* the
    visibility rule, because every actor who fails visibility also fails the
    write gate (`ROLES_WITH_BUSINESS_WRITE` is a subset of
    `ROLES_WITH_RESTRICTED_ACCESS`). The visibility rule itself is asserted
    where it is still observable, in `tests/test_authorization.py`, and
    `tests/test_one_write_gate.py` guards the subset relation that makes this
    reasoning true.
    """
    client.force_login(reader)
    matter = restricted_world["matter"]

    compose = client.post(
        reverse("matters:compose", kwargs={"pk": matter.pk}), {"body": "<p>Sekkumine</p>"}
    )
    assert compose.status_code == 404

    field = client.post(
        reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "owner"}),
        {"owner": str(reader.pk)},
    )
    assert field.status_code == 404
