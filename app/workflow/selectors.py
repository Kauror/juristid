"""Reading the `Hetkeseis` vocabulary out of the database.

Deliberately *not* in ``app.workflow.vocabulary``. That module is the frozen
label mapping the offline register inspector needs on a machine with no
PostgreSQL, and importing a model into it would put an ORM at the top of a
DB-free import path — the regression this repository has re-run before every
apply since Stage 2H.

What lives here are the questions the *product* asks: which stages may somebody
choose today, which stages may somebody choose **on this Matter**, and what does
each one mean. Every answer comes from the `StageVocabulary` rows a migration
seeded, and each has exactly one definition so that the control offering a stage
and the tooltip explaining it cannot disagree (``workflow/0004``,
``workflow/0006``, Uus teema redesign §8).

The second question is not the first one. A stage a Matter already holds stays
choosable on that Matter after the stage is retired, because retiring reference
data must not be able to delete a fact somebody recorded (docs/adr/0032
§Amendment).
"""

from __future__ import annotations

from django.db.models import Q, QuerySet

from app.workflow.models import StageVocabulary


def selectable_stages() -> QuerySet[StageVocabulary]:
    """Every Hetkeseis a person may attach to a Matter today, in reviewed order.

    ``is_active`` is the whole rule for **new** work, exactly as it is for
    Valdkonnad: a retired stage keeps its row, its Matters and its place in the
    statistics, and simply stops being offered for new work
    (app/taxonomy/vocabulary.py).

    It is not the whole rule for a Matter that already holds one. That is
    :func:`stages_including`, and a surface editing an existing Matter must call
    it instead — see there for why.
    """
    return StageVocabulary.objects.filter(is_active=True).order_by("sort_order", "label_et")


def stages_including(stage: StageVocabulary | None) -> QuerySet[StageVocabulary]:
    """The offered vocabulary **plus** the stage one Matter already holds.

    Retiring a stage has to be safe for the files already standing in it, and
    `is_active` alone was not. Every stage control on an existing Matter — the
    edit page, the header's inline select, the field form behind it — was
    narrowed to the active rows, so a Matter left holding a since-retired stage
    was not offered it, was refused when the value was posted back, and — the
    field being optional — had it silently replaced with NULL by an edit that
    only meant to correct the title. A vocabulary flag is not supposed to be
    able to destroy a recorded fact (docs/adr/0032 §Amendment).

    So the held stage joins the population, and only the held stage: exactly the
    one this Matter already carries, never "anybody inactive". A crafted POST
    naming a *different* retired stage is still refused, because that row is not
    in this queryset either — and `Uus teema` goes on reading
    :func:`selectable_stages`, because a new Matter has nothing to preserve.

    The same shape as `assignable_including` and as `MatterEditForm`'s retired
    Valdkond and Õigusakt handling, for the same stated reason: validation
    accepts what the record carries; the chooser offers what may be chosen
    today (app/accounts/selectors.py, Teema redesign §7.2).
    """
    offered = Q(is_active=True)
    if stage is not None:
        offered |= Q(pk=stage.pk)
    return StageVocabulary.objects.filter(offered).order_by("sort_order", "label_et")


def stage_help_texts(stages: QuerySet[StageVocabulary] | None = None) -> dict[str, str]:
    """``{stage id as text: explanation}`` for every offered stage.

    Keyed by the *string* form of the primary key, because that is what a
    rendered radio carries and what a template comparison sees. A stage with no
    explanation is left out rather than mapped to an empty string, so a caller
    can ask "is there help for this one" without also asking whether the answer
    is blank — which is what decides whether a chip gets the affordance at all.

    One query, handed to the template by the form. A tag that looked each stage
    up as it rendered would put eleven queries on a page whose whole argument is
    that it loads in one.

    Over `selectable_stages` by default, and over whatever a caller offers
    instead — which is how the edit surfaces keep the department's sentence on a
    retired stage they are still showing. An explanation that vanished the
    moment a stage was retired would leave exactly the chip a reader is least
    likely to recognise as the one with nothing explaining it.
    """
    offered = selectable_stages() if stages is None else stages
    return {
        str(stage.pk): stage.help_text
        for stage in offered.only("id", "help_text")
        if stage.help_text.strip()
    }
