"""`Muu valdkond`: free text that stays free text.

The field exists because the governed PolicyArea list will never cover
everything, and because the alternative people reach for otherwise is inventing
a taxonomy row that nobody reviewed. Everything below pins the same boundary
from a different side: it is captured, shown, edited and searched — and it is
never counted, never promoted to a PolicyArea and never turned into a Tag
(Stage-2E.1 brief 20).
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.models import Matter
from app.matters.services import create_matter, set_policy_area_other
from app.taxonomy.models import PolicyArea, Tag
from tests import factories

pytestmark = pytest.mark.django_db


# -- capture -----------------------------------------------------------------


def test_the_service_trims_and_caps(specialist):
    matter = create_matter(title="Teema", actor=specialist, policy_area_other="  Kosmoseõigus  ")
    assert matter.policy_area_other == "Kosmoseõigus"

    long = create_matter(title="Pikk", actor=specialist, policy_area_other="x" * 900)
    assert len(long.policy_area_other) == 400


def test_nothing_becomes_taxonomy(specialist):
    """Not a PolicyArea, not a Tag, not a governed anything."""
    areas_before = PolicyArea.objects.count()
    tags_before = Tag.objects.count()

    matter = create_matter(title="Teema", actor=specialist, policy_area_other="Kosmoseõigus")

    assert PolicyArea.objects.count() == areas_before
    assert Tag.objects.count() == tags_before
    assert list(matter.policy_areas.all()) == []


def test_it_is_not_a_canonical_area_for_statistics(specialist):
    """`policy_areas` is what a statistic counts, and it stays empty.

    A Matter filed only under "Muu" must not appear in a per-area chart under
    a bar nobody governs.
    """
    area = factories.PolicyAreaFactory()
    counted = create_matter(title="Loetav", actor=specialist, policy_areas=[area])
    uncounted = create_matter(title="Muu all", actor=specialist, policy_area_other="Kosmoseõigus")

    by_area = Matter.objects.filter(policy_areas=area)
    assert list(by_area) == [counted]
    assert uncounted not in by_area


# -- later editing -----------------------------------------------------------


def test_it_can_be_changed_after_creation(specialist):
    matter = create_matter(title="Teema", actor=specialist, policy_area_other="Esialgne")
    set_policy_area_other(matter=matter, value="Parandatud", actor=specialist)

    matter.refresh_from_db()
    assert matter.policy_area_other == "Parandatud"


def test_it_can_be_cleared(specialist):
    """Common: a matter gains a real PolicyArea a week later."""
    matter = create_matter(title="Teema", actor=specialist, policy_area_other="Esialgne")
    set_policy_area_other(matter=matter, value="   ", actor=specialist)

    matter.refresh_from_db()
    assert matter.policy_area_other == ""


def test_a_change_is_audited_as_itself(specialist):
    """Its own event type. The timeline must not call it a position change."""
    matter = create_matter(title="Teema", actor=specialist)
    set_policy_area_other(matter=matter, value="Kosmoseõigus", actor=specialist)

    events = ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.MATTER_POLICY_AREA_OTHER_SET
    )
    assert events.count() == 1
    # The value itself is deliberately absent: an audit row quoting the text
    # would be a second, unmanaged copy of a field somebody may later clear.
    assert "Kosmoseõigus" not in str(events.first().payload)


def test_an_unchanged_value_writes_nothing(specialist):
    matter = create_matter(title="Teema", actor=specialist, policy_area_other="Sama")
    before = ChangeEvent.objects.filter(matter=matter).count()

    set_policy_area_other(matter=matter, value="Sama", actor=specialist)
    assert ChangeEvent.objects.filter(matter=matter).count() == before


def test_the_inline_edit_reaches_the_service(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    url = reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "policy_area_other"})

    response = signed_in.post(url, {"policy_area_other": "  Kosmoseõigus "})

    assert response.status_code == 200
    matter.refresh_from_db()
    assert matter.policy_area_other == "Kosmoseõigus"


def test_the_endpoint_still_writes_the_value(signed_in, specialist):
    """The route, the service and the audit row are untouched.

    **It answers with the header now, and that is the point of the mapping.**
    It used to answer with the rail, from the days when `Muu valdkond` had a
    control there; the target took that control out and the answer went on
    pointing at a fragment that rendered nothing of this value
    (docs/adr/0074 §17). Since R2-07 the value is read in the header's
    `Valdkond` slot, beside the canonical areas it qualifies — so that is the
    surface a save has to swap, or the write would succeed while the number on
    screen stayed as it was (`_FIELD_SURFACES`).
    """
    matter = factories.MatterFactory(owner=specialist)
    url = reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "policy_area_other"})

    body = signed_in.post(url, {"policy_area_other": "Kosmoseõigus"}).content.decode()

    assert 'id="teema-pais"' in body
    assert "Muu: Kosmoseõigus" in body
    matter.refresh_from_db()
    assert matter.policy_area_other == "Kosmoseõigus"


def test_a_matter_nobody_may_see_is_a_404_here_too(client, reader, restricted_matter):
    client.force_login(reader)
    url = reverse(
        "matters:update_field",
        kwargs={"pk": restricted_matter.pk, "field": "policy_area_other"},
    )
    assert client.post(url, {"policy_area_other": "Midagi"}).status_code == 404


# -- display -----------------------------------------------------------------


def test_the_value_reads_on_the_matter_page_and_the_form_label_does_not(signed_in, specialist):
    """Read here, edited on `Muuda teemat`.

    The target took `Muu valdkond` out of `Teema andmed` as an *editable row*,
    on the argument that correcting how a file was classified is `Muuda teemat`
    work rather than a fact a lawyer looks up mid-sentence
    (TEEMA_TARGET_SPEC §G.1, docs/adr/0074 §17). That argument was about the
    control and was read as being about the value, and the result was a Matter
    filed under `Muu` alone reading «Valdkond: Määramata» — the page denying
    that a question it had asked had been answered (post-QA R2-07).

    So the value reads in the `Valdkond` slot beside the canonical areas, and
    the form label «Muu valdkond» stays off this page: there is no box here to
    label, and the row says where the box is.
    """
    matter = factories.MatterFactory(owner=specialist, policy_area_other="Kosmoseõigus")
    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()

    assert "Muu: Kosmoseõigus" in body
    assert "Muu valdkond" not in body
    matter.refresh_from_db()
    assert matter.policy_area_other == "Kosmoseõigus"


def test_an_empty_value_shows_no_stale_label(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    matter.policy_areas.add(factories.PolicyAreaFactory(name_et="Keskkond"))

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    assert "Keskkond" in body
    # The label belongs to a value. With nothing recorded there is only the
    # control that would record one.
    assert body.count("Muu valdkond") <= 1
