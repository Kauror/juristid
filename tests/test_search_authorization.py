"""Search must never leak the existence of a Matter somebody cannot see.

Not its title, not a snippet, not its tags, not its source organisation, not
whether it has a OneNote link — and not the fact that a result *count* went up.
A count is a disclosure: "your search for the merger matched 4 things, and you
may see 3" tells the reader there is a fourth.

So every assertion here is about the boundary rather than the rendering, and the
counts are checked as carefully as the rows.

Since docs/adr/0181 the boundary is the application's perimeter, not RESTRICTED:
a READER finds every Matter a lawyer does, and the somebody who cannot see is an
outsider (ADMINISTRATOR or superuser without a grant, anonymous).
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.core.authorization import Scope
from app.core.enums import Visibility
from app.matters.services import create_matter, set_matter_visibility
from app.organisations.models import Organisation, OrganisationType
from app.search.indexing import rebuild_all
from app.search.services import result_count, search_matters, visible_documents
from tests import factories

pytestmark = pytest.mark.django_db

RESTRICTED_TITLE = "Salajane ühinemise eelnõu konfidentsiaalne"
OPEN_TITLE = "Avalik ühinemise eelnõu"


@pytest.fixture
def world(db, specialist, other_specialist):
    ministry = Organisation.objects.create(
        name="Salaministeerium", organisation_type=OrganisationType.MINISTRY
    )
    tag = factories.TagFactory(name_et="Ühinemised")

    restricted = create_matter(
        title=RESTRICTED_TITLE,
        owner=specialist,
        reference_year=2026,
        visibility=Visibility.RESTRICTED,
        addressee_organisation=ministry,
    )
    restricted.tags.add(tag, through_defaults={})

    visible = create_matter(title=OPEN_TITLE, owner=other_specialist, reference_year=2026)
    rebuild_all()
    return {"restricted": restricted, "visible": visible, "ministry": ministry, "tag": tag}


def _titles(results) -> list[str]:
    return [result.matter.title for result in results]


def test_the_owner_of_a_restricted_matter_finds_it(world, specialist) -> None:
    assert RESTRICTED_TITLE in _titles(search_matters(query="ühinemise", user=specialist))


def test_a_collaborator_finds_it(world, other_specialist) -> None:
    world["restricted"].collaborators.add(other_specialist)
    assert RESTRICTED_TITLE in _titles(search_matters(query="ühinemise", user=other_specialist))


def test_a_reader_finds_it_like_any_internal_user(world, reader, administrator) -> None:
    """docs/adr/0181: participation no longer decides reading."""
    results = search_matters(query="ühinemise", user=reader)
    assert sorted(_titles(results)) == sorted([OPEN_TITLE, RESTRICTED_TITLE])
    assert search_matters(query="ühinemise", user=administrator) == []


def test_a_department_head_finds_it(world, department_head) -> None:
    assert RESTRICTED_TITLE in _titles(search_matters(query="ühinemise", user=department_head))


def test_a_technical_administrator_alone_does_not(world, administrator) -> None:
    """Technical administration is not business access (specification 5.2)."""
    assert RESTRICTED_TITLE not in _titles(search_matters(query="ühinemise", user=administrator))


def test_a_superuser_alone_does_not(world, superuser) -> None:
    assert RESTRICTED_TITLE not in _titles(search_matters(query="ühinemise", user=superuser))


def test_a_break_glass_grant_does(world, other_specialist, department_head) -> None:
    from datetime import timedelta

    from app.accounts.services import grant_break_glass

    grant_break_glass(
        user=other_specialist,
        granted_by=department_head,
        reason="Sünteetiline juhtum",
        duration=timedelta(hours=1),
    )
    assert RESTRICTED_TITLE in _titles(search_matters(query="ühinemise", user=other_specialist))


def test_an_anonymous_caller_sees_nothing(world) -> None:
    from django.contrib.auth.models import AnonymousUser

    assert search_matters(query="ühinemise", user=AnonymousUser()) == []
    assert result_count(query="ühinemise", user=AnonymousUser()) == 0
    assert visible_documents(AnonymousUser()).count() == 0


def test_a_hidden_matter_does_not_change_the_visible_count(
    world, specialist, reader, administrator
) -> None:
    """The disclosure that looks like it is not one.

    Since docs/adr/0181 the READER counts what the owner counts; the outsider
    (ADMINISTRATOR without a grant) counts nothing at all."""
    assert result_count(query="ühinemise", user=specialist) == 2
    assert result_count(query="ühinemise", user=reader) == 2
    assert result_count(query="ühinemise", user=administrator) == 0


def test_searching_a_restricted_matters_exact_reference_finds_nothing(world, administrator) -> None:
    """Even the strongest possible query must not confirm the record exists —
    to an outsider (docs/adr/0181: was a READER)."""
    reference = world["restricted"].display_reference
    assert search_matters(query=reference, user=administrator) == []
    assert result_count(query=reference, user=administrator) == 0


def test_searching_a_restricted_matters_organisation_does_not_surface_it(
    world, administrator
) -> None:
    assert RESTRICTED_TITLE not in _titles(
        search_matters(query="Salaministeerium", user=administrator)
    )


def test_searching_a_restricted_matters_tag_does_not_surface_it(world, administrator) -> None:
    assert RESTRICTED_TITLE not in _titles(search_matters(query="Ühinemised", user=administrator))


def test_restricting_a_matter_takes_effect_without_reindexing(world, reader, monkeypatch) -> None:
    """The whole reason the projection stores no visibility.

    The search document is untouched here. The next query still gets it right,
    because authorization is evaluated against the live Matter every time — and
    that is why docs/adr/0042 needed no `INDEX_VERSION` bump and no rebuild: the
    indexed text never carried the answer.

    Asked as a reader, because the point is that restricting *changes* what the
    viewer may see. Since docs/adr/0042 a lawyer reads the department either
    way, so a lawyer cannot show this property at all.

    Since docs/adr/0181 no request has a scope that restricting changes — every
    internal user reads RESTRICTED work — so the property is shown through a
    hand-built narrowed scope (authenticated, no restricted role, no grant, not a
    participant), which is what the projection's live join still answers.
    """
    import app.search.services as search_services

    narrowed = Scope(
        user=reader, is_authenticated=True, sees_restricted_by_role=False, break_glass_grant_id=None
    )
    original = search_services.scope_for_user
    monkeypatch.setattr(
        search_services,
        "scope_for_user",
        lambda user: narrowed if user == reader else original(user),
    )
    open_matter = world["visible"]
    assert OPEN_TITLE in _titles(search_matters(query="ühinemise", user=reader))

    set_matter_visibility(matter=open_matter, visibility=Visibility.RESTRICTED)
    # Deliberately no rebuild_all() here.

    assert OPEN_TITLE not in _titles(search_matters(query="ühinemise", user=reader))
    assert result_count(query="ühinemise", user=reader) == 0


def test_relaxing_a_matter_takes_effect_without_reindexing(world, other_specialist) -> None:
    set_matter_visibility(matter=world["restricted"], visibility=Visibility.NORMAL)
    assert RESTRICTED_TITLE in _titles(search_matters(query="ühinemise", user=other_specialist))


def test_a_document_whose_matter_is_gone_cannot_return_a_result(world, specialist) -> None:
    """Results come through the Matter join, so a matterless row is unreachable.

    A Matter cannot actually be deleted — its audit trail protects it — and the
    projection cascades from it anyway. This asserts the shape the guarantee
    rests on rather than simulating an impossible state.
    """
    from app.search.models import SearchDocument

    field = SearchDocument._meta.get_field("matter")
    assert not field.null, "a document without a Matter must not be representable"
    assert field.remote_field.on_delete.__name__ == "CASCADE"


# -- the view --------------------------------------------------------------


def test_the_results_page_shows_only_what_the_reader_may_see(world, client, reader, administrator):
    """docs/adr/0181: the READER sees both; an outsider neither."""
    client.force_login(reader)
    response = client.get(reverse("search:search"), {"q": "ühinemise"})
    body = response.content.decode()
    assert OPEN_TITLE in body
    assert RESTRICTED_TITLE in body

    client.force_login(administrator)
    body = client.get(reverse("search:search"), {"q": "ühinemise"}).content.decode()
    assert OPEN_TITLE not in body
    assert RESTRICTED_TITLE not in body
    assert "Salaministeerium" not in body


def test_the_page_count_matches_what_it_shows(world, client, reader, administrator):
    client.force_login(reader)
    response = client.get(reverse("search:search"), {"q": "ühinemise"})
    assert response.context["result_count"] == 2
    assert len(response.context["rows"]) == 2

    client.force_login(administrator)
    response = client.get(reverse("search:search"), {"q": "ühinemise"})
    assert response.context["result_count"] == 0
    assert len(response.context["rows"]) == 0


def test_an_exact_reference_still_navigates_straight_to_the_matter(
    world, client, specialist
) -> None:
    client.force_login(specialist)
    reference = world["restricted"].display_reference
    response = client.get(reverse("search:search"), {"q": reference})
    assert response.status_code == 302
    assert str(world["restricted"].pk) in response["Location"]


def test_a_reference_the_reader_cannot_see_does_not_redirect(
    world, client, reader, administrator
) -> None:
    """A redirect would confirm the record exists just as loudly as a title.

    Since docs/adr/0181 the READER can see it and is redirected like the owner;
    the reader who cannot is an outsider (ADMINISTRATOR without a grant)."""
    reference = world["restricted"].display_reference
    client.force_login(reader)
    response = client.get(reverse("search:search"), {"q": reference})
    assert response.status_code == 302
    assert str(world["restricted"].pk) in response["Location"]

    client.force_login(administrator)
    response = client.get(reverse("search:search"), {"q": reference})
    assert response.status_code == 200
    assert response.context["result_count"] == 0


def test_search_requires_a_signed_in_user(world, client) -> None:
    response = client.get(reverse("search:search"), {"q": "ühinemise"})
    assert response.status_code == 302
