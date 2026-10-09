"""`Seo olemasoleva teemaga` on `Uus teema` — a related Teema chosen by hand (R3).

The owner's brief of 2026-10-09: the suggestions under the form depend on a
threshold and on what has been typed, and a lawyer who already knows which
earlier file this one continues must be able to find it and link it anyway
(docs/adr/0150 §3). What is pinned here:

* **The search is the header search** — title, `Teemaviide` or keyword — with
  its boundary, usable with no suggestions at all and finding what the engine
  did not propose.
* **It writes nothing.** Choosing adds a hidden value to the form; the link is
  made by `matter_create`, through `link_related_matters`, in the transaction
  that creates the Teema — all of it or none of it.
* **Once, and only what may be seen.** A Teema chosen twice, or both chosen and
  ticked as a suggestion, is one relation; an unreadable or synthetic one
  refuses the whole save.
* **A refused save keeps the choice**, and the ticked suggestions too.
"""

from __future__ import annotations

import uuid

import pytest
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.matters.enums import MatterDataClass
from app.matters.models import Matter
from app.matters.views import SIMILAR_LINK_NOT_FOUND, _link_ticked_similar_matters
from app.related_materials import engine
from app.related_materials.models import MatterRelation
from app.related_materials.views import (
    LINK_ON_CREATE_CHOSEN_FIELD,
    LINK_ON_CREATE_FIELD,
    draft_picker_results,
)
from tests import factories
from tests.refusals import refused

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")
PICKER = reverse("related_materials:draft_picker")
DRAFT = reverse("related_materials:draft_suggestions")


def earlier(owner, title: str, number: int, **extra) -> Matter:
    return factories.MatterFactory(
        owner=owner, title=title, reference_year=2099, reference_number=number, **extra
    )


def relations_of(matter: Matter) -> list[Matter]:
    rows = MatterRelation.objects.filter(matter_a=matter) | MatterRelation.objects.filter(
        matter_b=matter
    )
    return [row.matter_b if row.matter_a_id == matter.pk else row.matter_a for row in rows]


def search(client, query: str, *chosen: Matter) -> str:
    params = {"q": query}
    response = client.get(
        PICKER, {**params, LINK_ON_CREATE_CHOSEN_FIELD: [str(item.pk) for item in chosen]}
    )
    assert response.status_code == 200
    return response.content.decode()


# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------


def test_a_teema_is_found_by_its_title(signed_in, specialist):
    target = earlier(specialist, "Metsanduse arengukava aastani 2030", 51)

    body = search(signed_in, "metsanduse")

    assert target.title in body
    assert f'data-related-pick="{target.pk}"' in body
    assert "2099_51" in body


def test_a_teema_is_found_by_its_reference(signed_in, specialist):
    target = earlier(specialist, "Pakendiaktsiisi seaduse muutmine", 52)

    body = search(signed_in, "2099_52")

    assert f'data-related-pick="{target.pk}"' in body


def test_a_teema_is_found_by_a_keyword_it_is_tagged_with(signed_in, specialist):
    target = earlier(specialist, "Ühe valdkonna ülevaade", 53)
    target.tags.set([factories.TagFactory(name_et="Ringmajandus")])
    target.save()

    body = search(signed_in, "ringmajandus")

    assert f'data-related-pick="{target.pk}"' in body


def test_the_search_finds_what_the_suggestions_did_not_propose(signed_in, specialist):
    """No threshold stands between a person and the file they know is related."""
    target = earlier(specialist, "Metsanduse arengukava aastani 2030", 54)
    profile = engine.build_draft_profile(
        title="Jäätmeseaduse muutmine",
        summary="",
        area_ids=[],
        instrument_ids=[],
        organisation_ids=[],
    )
    suggested = engine.suggestions_for_draft(profile, specialist) if profile else []
    assert target.pk not in {item.matter.pk for item in suggested}

    assert f'data-related-pick="{target.pk}"' in search(signed_in, "metsanduse")


def test_a_chosen_teema_is_not_offered_again(signed_in, specialist):
    target = earlier(specialist, "Metsanduse arengukava aastani 2030", 55)

    assert f'data-related-pick="{target.pk}"' not in search(signed_in, "metsanduse", target)


def test_a_closed_teema_can_be_found_and_says_so(signed_in, specialist):
    from app.matters.services import close_matter
    from app.workflow.enums import Disposition

    target = earlier(specialist, "Metsanduse vana arengukava", 56)
    close_matter(matter=target, disposition=Disposition.OTHER, actor=specialist)

    body = search(signed_in, "metsanduse")

    assert f'data-related-pick="{target.pk}"' in body
    assert "suletud" in body


def test_synthetic_test_work_is_never_offered(signed_in, specialist):
    target = earlier(
        specialist, "Metsanduse sünteetiline teema", 57, data_class=MatterDataClass.TEST
    )

    assert f'data-related-pick="{target.pk}"' not in search(signed_in, "metsanduse")


def test_a_restricted_teema_leaves_no_trace_for_somebody_who_may_not_read_it(specialist, reader):
    """The search's own boundary — the reader's scope before any ranking."""
    hidden = earlier(specialist, "Metsanduse piiratud kava", 58, visibility=Visibility.RESTRICTED)

    # A lawyer may read restricted work (docs/adr/0042), so it is found…
    assert hidden in draft_picker_results(specialist, "metsanduse", exclude=set())
    # …and somebody who may not read it finds no trace, by title or reference.
    assert hidden not in draft_picker_results(reader, "metsanduse", exclude=set())
    assert hidden not in draft_picker_results(reader, "2099_58", exclude=set())


def test_the_search_is_a_writers_control(client, reader):
    client.force_login(reader)
    response = client.get(PICKER, {"q": "metsanduse"})
    assert response.status_code in (403, 404)


def test_searching_writes_nothing(signed_in, specialist):
    earlier(specialist, "Metsanduse arengukava aastani 2030", 59)
    events = ChangeEvent.objects.count()
    relations = MatterRelation.objects.count()

    search(signed_in, "metsanduse")
    search(signed_in, "2099_59")

    assert ChangeEvent.objects.count() == events
    assert MatterRelation.objects.count() == relations


# ---------------------------------------------------------------------------
# Linking, with the Teema and never before it
# ---------------------------------------------------------------------------


def test_the_chosen_teema_is_linked_when_the_new_one_is_created(signed_in, specialist):
    target = earlier(specialist, "Metsanduse arengukava aastani 2030", 60)

    response = signed_in.post(
        CREATE, {"title": "Metsaseaduse muutmine", LINK_ON_CREATE_CHOSEN_FIELD: [str(target.pk)]}
    )

    assert response.status_code == 302
    created = Matter.objects.get(title="Metsaseaduse muutmine")
    assert relations_of(created) == [target]
    for side in (created, target):
        assert (
            ChangeEvent.objects.filter(
                matter=side, event_type=ChangeEventType.MATTER_RELATION_ADDED
            ).count()
            == 1
        )


def test_chosen_twice_and_ticked_as_a_suggestion_is_one_relation(signed_in, specialist):
    target = earlier(specialist, "Metsanduse arengukava aastani 2030", 61)

    signed_in.post(
        CREATE,
        {
            "title": "Kordused",
            LINK_ON_CREATE_FIELD: [str(target.pk)],
            LINK_ON_CREATE_CHOSEN_FIELD: [str(target.pk), str(target.pk)],
        },
    )

    created = Matter.objects.get(title="Kordused")
    assert (
        MatterRelation.objects.filter(matter_a__in=[created, target])
        .filter(matter_b__in=[created, target])
        .count()
        == 1
    )


def test_a_link_that_cannot_be_made_takes_the_teema_with_it(signed_in, specialist):
    target = earlier(specialist, "Metsanduse arengukava aastani 2030", 62)
    before = MatterRelation.objects.count()

    response = signed_in.post(
        CREATE,
        {
            "title": "Ei sünni",
            LINK_ON_CREATE_CHOSEN_FIELD: [str(target.pk), str(uuid.uuid4())],
        },
    )

    assert response.status_code in (400, 409)
    assert SIMILAR_LINK_NOT_FOUND in response.content.decode()
    assert not Matter.objects.filter(title="Ei sünni").exists()
    assert MatterRelation.objects.count() == before


def test_synthetic_work_is_refused_at_the_save_too(signed_in, specialist):
    synthetic = earlier(specialist, "Sünteetiline", 63, data_class=MatterDataClass.TEST)

    signed_in.post(
        CREATE, {"title": "Päris teema", LINK_ON_CREATE_CHOSEN_FIELD: [str(synthetic.pk)]}
    )

    assert not Matter.objects.filter(title="Päris teema").exists()


def test_a_teema_the_actor_may_not_read_is_never_linked(specialist, reader):
    hidden = earlier(specialist, "Piiratud", 64, visibility=Visibility.RESTRICTED)
    new = factories.MatterFactory(owner=specialist, title="Uus")

    with refused(SIMILAR_LINK_NOT_FOUND):
        _link_ticked_similar_matters(matter=new, ticked=[str(hidden.pk)], actor=reader)
    assert relations_of(new) == []


def test_the_new_teema_itself_is_never_a_target(specialist):
    new = factories.MatterFactory(owner=specialist, title="Iseendaga")
    _link_ticked_similar_matters(matter=new, ticked=[str(new.pk)], actor=specialist)
    assert relations_of(new) == []


# ---------------------------------------------------------------------------
# A refused save keeps the choice
# ---------------------------------------------------------------------------


def test_a_refused_save_comes_back_with_the_chosen_teema(signed_in, specialist):
    target = earlier(specialist, "Metsanduse arengukava aastani 2030", 65)

    response = signed_in.post(CREATE, {"title": "", LINK_ON_CREATE_CHOSEN_FIELD: [str(target.pk)]})

    assert response.status_code == 400
    assert response.context["related_chosen"] == [target]
    body = response.content.decode()
    assert f'name="{LINK_ON_CREATE_CHOSEN_FIELD}" value="{target.pk}"' in body
    assert target.title in body.split('id="seo-olemasoleva-teemaga"', 1)[1]
    assert relations_of(target) == []


def test_a_refused_save_keeps_the_ticked_suggestions_too(signed_in, specialist):
    target = earlier(specialist, "Metsanduse arengukava aastani 2030", 66)

    response = signed_in.post(CREATE, {"title": "", LINK_ON_CREATE_FIELD: [str(target.pk)]})

    assert response.status_code == 400
    region = response.content.decode().split('id="sarnased-teemad"', 1)[1].split("</div>", 1)[0]
    assert f'name="{LINK_ON_CREATE_FIELD}" value="{target.pk}"' in region


def test_a_refused_save_echoes_nothing_the_reader_may_not_see(client, specialist, reader):
    """Echoed values are re-resolved: a forged or unreadable id is dropped."""
    from django.test import RequestFactory

    from app.matters.views import _echoed_related_choices

    hidden = earlier(specialist, "Piiratud", 67, visibility=Visibility.RESTRICTED)
    request = RequestFactory().post(
        CREATE, {LINK_ON_CREATE_CHOSEN_FIELD: [str(hidden.pk), "ei-ole-uuid"]}
    )
    request.user = reader

    assert _echoed_related_choices(request) == {"related_chosen": [], "similar_ticked": []}


def test_a_ticked_suggestion_survives_an_answer_with_no_cards(signed_in, specialist):
    """The re-read after a refusal may find nothing to suggest; the tick stays."""
    target = earlier(specialist, "Metsanduse arengukava aastani 2030", 68)

    body = signed_in.post(
        DRAFT, {"title": "", LINK_ON_CREATE_FIELD: [str(target.pk)]}
    ).content.decode()

    assert f'name="{LINK_ON_CREATE_FIELD}" value="{target.pk}"' in body
    with pytest.raises(AssertionError):
        assert "draftsimilar" in body


def test_draft_picker_results_refuse_nothing_but_need_two_letters(specialist):
    earlier(specialist, "M", 69)
    assert draft_picker_results(specialist, "m", exclude=set()) == []


def test_a_malformed_value_refuses_by_name(specialist):
    new = factories.MatterFactory(owner=specialist, title="Halb väärtus")
    with refused(SIMILAR_LINK_NOT_FOUND):
        _link_ticked_similar_matters(matter=new, ticked=["pole-uuid"], actor=specialist)
