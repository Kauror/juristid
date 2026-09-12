"""Two boundaries the engagement link does not keep, found by red team.

`MatterEngagement.url` is the one field on this model that is a **persistent,
user-supplied string copied into the search projection and rendered back into
HTML**. That makes it the field worth attacking, and the post-release UX round
is about to add two more of exactly its shape — a Smaily campaign link and an
Alchemer questionnaire link — on the same model, through the same validation,
into the same projection.

So both properties below are stated about *a provider link* rather than about
one column: whatever holds for `url` must hold for every link field this model
grows.

Reproducible on `origin/main` at 5c0b2a2143ecf2b63e1fd5198f7f7b0930622102.
Neither is an authorization bypass and neither crosses a visibility boundary —
a two-world differential over ninety reader-reachable surfaces, with a
RESTRICTED engagement carrying a distinctive host and a distinctive token,
showed zero byte differences for both READER and ADMINISTRATOR, and a crafted
POST at the hidden engagement's id answered byte-for-byte like a random UUID.
What these are is a validation layer that stops one field short of the column,
and a label that yields the URL's *authority* where its own docstring promises
the *host*.

**Why this has gone unnoticed, and why it matters now.** No page currently
offers the link. `+ Kaasamine` posts `CompactEngagementForm`, which has no
`url` field, and `workspace.add_matter_engagement` takes no `url` argument;
`EngagementForm`, which does have one, is posted to only from
`templates/matters/partials/engagement.html`, which no live template includes.
The live writer of a link today is `app.legacy_import.register_outreach`,
which takes it unbounded from the Smaily *Template preview* column and from
the approved mapping — so the length failure is the importer's, and the
provider-link round is what will put both failures on a page.

No fix is included. These tests state the boundary; the implementation belongs
to the lane that adds the fields.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.core.errors import DomainError
from app.matters.enums import EngagementKind
from app.matters.models import MatterEngagement
from app.matters.services import add_engagement
from tests import factories

pytestmark = pytest.mark.django_db


def _post_add(client, matter, **fields) -> object:
    payload = {
        "kind": EngagementKind.SURVEY.value,
        "title": "Küsitlus liikmetele",
        "url": "",
        "note": "",
        "occurred_on": "",
    }
    payload.update(fields)
    return client.post(reverse("matters:add_engagement", kwargs={"pk": matter.pk}), payload)


# -- a link longer than the column ------------------------------------------
#
# `MatterEngagement.url` is `URLField(max_length=1000)`. `EngagementForm.url`
# is a `CharField` with no `max_length` at all, `normalize_engagement_url`
# checks only the scheme and the authority, and `add_engagement` truncates
# `title` to 500 while leaving the link alone. So the only thing between a
# pasted address and the column is PostgreSQL, which answers
# `StringDataRightTruncation` — a `django.db.utils.DataError` that no view and
# no importer catches.
#
# Not a hypothetical length. The field's own docstring says campaign and
# survey links "routinely run to hundreds of characters of query string",
# which is the whole reason the host is printed instead of the address; a
# provider preview URL carrying UTM parameters and a signed token is the
# ordinary way something reaches four figures.

#: One character past the column. 1000 saves; 1001 is the first value that
#: cannot, so it is the boundary worth naming.
TOO_LONG = "https://survey.example.com/f?token=" + "a" * 966

SHORT = "https://survey.example.com/f?token=lyhike"


def test_the_service_refuses_a_link_longer_than_the_column(normal_matter, specialist):
    """The importer's door, which is the live one.

    `register_outreach` hands `add_engagement` whatever the Smaily export and
    the approved mapping say, with no length of its own. A `DomainError` here
    names the row and leaves the rest of the import to be decided; a
    `DataError` aborts the transaction with a PostgreSQL sentence and no
    reference, which is the failure mode the refusals in this service exist to
    avoid.
    """
    assert len(TOO_LONG) == 1001

    with pytest.raises(DomainError):
        add_engagement(
            matter=normal_matter,
            kind=EngagementKind.EMAIL_CAMPAIGN,
            title="Uudiskirja saatmine liikmetele",
            url=TOO_LONG,
            actor=specialist,
        )

    assert not MatterEngagement.objects.filter(matter=normal_matter).exists()


def test_the_service_still_keeps_a_link_the_length_of_the_column(normal_matter, specialist):
    """The other half, so a fix cannot be a blanket shortening of the field."""
    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title="Uudiskirja saatmine liikmetele",
        url=TOO_LONG[:1000],
        actor=specialist,
    )

    assert engagement.url == TOO_LONG[:1000]


def test_the_route_refuses_a_link_longer_than_the_column(signed_in, specialist):
    """And the form says so beside the field, like every other bad link.

    Refused rather than silently trimmed, deliberately: a link cut off at a
    thousand characters is a link that no longer resolves, and storing one
    would leave a reader following a dead address in the belief that it is
    what was recorded. The scheme rule on this same field already answers this
    way — 400, with a sentence — so this is the field's own established answer
    to a value it cannot keep.
    """
    matter = factories.MatterFactory(owner=specialist)

    response = _post_add(signed_in, matter, url=TOO_LONG)

    assert response.status_code == 400, (
        "a link one character past the column reached PostgreSQL: neither the "
        "form nor the service bounds it, so the route answers 500 where every "
        "other bad link on it answers 400"
    )
    assert not MatterEngagement.objects.filter(matter=matter).exists()


def test_correcting_a_link_to_one_too_long_is_refused_the_same_way(signed_in, specialist):
    """The correction route shares the form, so it shares the failure."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="Küsitlus liikmetele",
        url=SHORT,
        actor=specialist,
    )

    response = signed_in.post(
        reverse(
            "matters:update_engagement",
            kwargs={"pk": matter.pk, "engagement_id": engagement.pk},
        ),
        {
            "kind": EngagementKind.SURVEY.value,
            "title": "Küsitlus liikmetele",
            "url": TOO_LONG,
            "note": "",
            "occurred_on": "",
        },
    )

    assert response.status_code == 400
    engagement.refresh_from_db()
    assert engagement.url == SHORT


# -- the label is the host, and a host has no password in it ----------------
#
# `link_label` returns `urlsplit(self.url).netloc`, and a URL's netloc is its
# *authority*: `userinfo@host:port`. So a link pasted straight out of a
# provider's dashboard — those do sometimes carry basic-auth credentials —
# yields a label with a username and a password in it, and
# `link_search_terms` copies that same string into the projection's
# `alias_text`, where it becomes a matchable token.
#
# This stays inside the visibility boundary: the projection row carries the
# engagement's own override and the AUTH-003 child scope holds. What it breaks
# is the field's own promise — "A link's host, for a control that must not
# print a tracking URL … the rest is machinery" — and a credential is the
# worst kind of machinery to print and the worst kind to index.
#
# The property is asserted on the model and on the stored row rather than on a
# page, because the markup that renders `link_label`
# (`templates/matters/partials/engagement.html`) is included by no live
# template today. It is also, precisely, the markup a provider-link surface
# would revive.

SECRET = "P4ssw0rd-NAHTAV"  # noqa: S105 - the point of the test is that this must not be printed
CREDENTIALED = f"https://kampaania:{SECRET}@uudiskiri.example.com/loend/8842?token=UTM-9931"


@pytest.fixture
def credentialed_engagement(db, specialist):
    matter = factories.MatterFactory(owner=specialist, title="Avalik teema")
    return add_engagement(
        matter=matter,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title="Uudiskirja saatmine liikmetele",
        url=CREDENTIALED,
        actor=specialist,
    )


def test_the_link_label_is_the_host_and_not_the_authority(credentialed_engagement):
    engagement = credentialed_engagement

    assert engagement.link_label == "uudiskiri.example.com", (
        "the label is `urlsplit().netloc`, which is the authority and brings "
        f"the credentials with it: {engagement.link_label!r}"
    )


def test_no_search_term_carries_the_credentials(credentialed_engagement):
    engagement = credentialed_engagement

    for term in engagement.link_search_terms:
        assert SECRET not in term, (
            f"{term!r} puts a password in the search projection, where it is a "
            "matchable token for everybody who may read the engagement"
        )
    assert "uudiskiri.example.com" in engagement.link_search_terms, (
        "the vendor host is what the column exists to make findable"
    )


def test_the_projection_row_carries_no_credentials(credentialed_engagement):
    """What a fix has to reach: the stored row, not only the property.

    An engagement is indexed from `post_save` (app/search/signals.py), so the
    row exists without anything here asking for it — which is also why a
    label computed at render time would not be enough.
    """
    from app.search.models import SearchDocument

    rows = SearchDocument.objects.filter(engagement=credentialed_engagement)
    assert rows.exists()
    for row in rows:
        assert SECRET not in row.alias_text
        assert SECRET not in row.title
        assert SECRET not in row.body_text
