"""Deleting one Teema never removes a record another Teema holds (ENG-042).

`MatterBackgroundMaterial` names two Matters without being a pair model: Y
cites X's sent opinion as background, so the row's `matter` is Y and its
`submission` is X's. The ownership walk reaches it from X's `Submission`
under ``CASCADE`` — correctly, since the row cannot outlive the opinion — and
`_straddling_rows` then skipped every `Matter`-valued key. Deleting X deleted
Y's background row, with no event on Y and nothing said to the person pressing
the button.

The Matter-key exemption now belongs to the two models that exist to name a
pair, `MatterRelation` and `RelatedSuggestionDismissal` (docs/adr/0096); any
other row whose Matter is not the one being deleted refuses the deletion, in the
same generic sentence every straddling row gets.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.matters.deletion import (
    BLOCKED_BY_STRADDLING_ROW,
    MATTER_PAIR_MODELS,
    REFUSAL_TEXT,
    delete_matter,
    plan_matter_deletion,
)
from app.matters.models import Matter
from app.related_materials import services as related
from app.related_materials.models import (
    MatterBackgroundMaterial,
    MatterRelation,
    RelatedSuggestionDismissal,
)
from tests.test_related_materials import _matter, _sent_opinion

pytestmark = pytest.mark.django_db

CITING_TITLE = "Teine teema, mis tugineb sellele arvamusele"


@pytest.fixture
def cited(specialist):
    """X, whose sent opinion Y cites as background."""
    source = _matter(specialist, "Allikteema", number=4201)
    citing = _matter(specialist, CITING_TITLE, number=4202)
    opinion = _sent_opinion(source, "Koja arvamus, mida tsiteeritakse")
    related.add_background_submission(matter=citing, submission=opinion, actor=specialist)
    return source, citing, opinion


def test_a_background_citation_from_another_matter_refuses_the_deletion(cited, specialist):
    source, citing, _opinion = cited

    plan = plan_matter_deletion(source)
    straddling = [b for b in plan.blockers if b.code == BLOCKED_BY_STRADDLING_ROW]
    assert [b.label for b in straddling] == ["related_materials.MatterBackgroundMaterial.matter"]
    assert straddling[0].count == 1

    with pytest.raises(DomainError):
        delete_matter(matter=source, actor=specialist)

    assert MatterBackgroundMaterial.objects.filter(matter=citing).count() == 1
    assert Matter.objects.filter(pk=source.pk).exists()


def test_the_refusal_names_neither_the_other_matter_nor_its_title(signed_in, cited):
    source, citing, _opinion = cited

    response = signed_in.post(
        reverse("matters:matter_delete", kwargs={"pk": source.pk}), follow=True
    )
    body = response.content.decode()

    assert REFUSAL_TEXT[BLOCKED_BY_STRADDLING_ROW] in body
    assert CITING_TITLE not in body
    assert citing.display_reference not in body
    assert str(citing.pk) not in body
    assert Matter.objects.filter(pk=source.pk).exists()


def test_the_refusal_is_the_same_when_the_citing_matter_is_hidden(specialist):
    """The reader of X cannot see Y. The sentence does not change, so it tells
    them nothing about Y beyond what every straddling row says."""
    source = _matter(specialist, "Allikteema", number=4203)
    hidden = _matter(specialist, CITING_TITLE, number=4204, visibility=Visibility.RESTRICTED)
    opinion = _sent_opinion(source, "Arvamus")
    related.add_background_submission(matter=hidden, submission=opinion, actor=specialist)

    plan = plan_matter_deletion(source)
    assert {b.message for b in plan.blockers} == {REFUSAL_TEXT[BLOCKED_BY_STRADDLING_ROW]}
    assert all(CITING_TITLE not in b.detail for b in plan.blockers)


def test_once_the_citation_is_removed_the_deletion_goes_ahead(cited, specialist):
    source, citing, opinion = cited
    related.remove_background_material(matter=citing, submission=opinion, actor=specialist)

    delete_matter(matter=source, actor=specialist)

    assert not Matter.objects.filter(pk=source.pk).exists()
    assert Matter.objects.filter(pk=citing.pk).exists()


def test_a_relation_and_a_dismissal_between_two_matters_still_go_with_either(specialist):
    """The two pair models keep the exemption, and only they do."""
    assert MATTER_PAIR_MODELS == {
        MatterRelation._meta.label,
        RelatedSuggestionDismissal._meta.label,
    }
    first = _matter(specialist, "Esimene", number=4205)
    second = _matter(specialist, "Teine", number=4206)
    related.link_related_matters(matter=first, other=second, actor=specialist)
    related.dismiss_related_suggestion(matter=second, candidate_matter=first, actor=specialist)
    related.dismiss_related_suggestion(matter=first, candidate_matter=second, actor=specialist)

    assert not plan_matter_deletion(first).is_blocked
    delete_matter(matter=first, actor=specialist)

    assert MatterRelation.objects.count() == 0
    assert RelatedSuggestionDismissal.objects.count() == 0
    assert Matter.objects.filter(pk=second.pk).exists()
    assert ChangeEvent.objects.filter(
        matter=first, event_type=ChangeEventType.MATTER_DELETED
    ).exists()


def test_a_matter_citing_its_own_opinion_is_not_possible_and_its_own_rows_do_not_block(
    specialist,
):
    """A Matter's own background rows are owned, not straddling."""
    matter = _matter(specialist, "Oma taust", number=4207)
    other = _matter(specialist, "Teise teema arvamus", number=4208)
    opinion = _sent_opinion(other, "Teise arvamus")
    related.add_background_submission(matter=matter, submission=opinion, actor=specialist)

    assert not plan_matter_deletion(matter).is_blocked
    delete_matter(matter=matter, actor=specialist)
    assert MatterBackgroundMaterial.objects.count() == 0
    assert Matter.objects.filter(pk=other.pk).exists()
