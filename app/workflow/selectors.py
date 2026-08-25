"""Reading the `Hetkeseis` vocabulary out of the database.

Deliberately *not* in ``app.workflow.vocabulary``. That module is the frozen
label mapping the offline register inspector needs on a machine with no
PostgreSQL, and importing a model into it would put an ORM at the top of a
DB-free import path — the regression this repository has re-run before every
apply since Stage 2H.

What lives here is the pair of questions the *product* asks: which stages may
somebody choose today, and what does each one mean. Both answers come from the
`StageVocabulary` rows a migration seeded, and both have exactly one definition
so that the control offering a stage and the tooltip explaining it cannot
disagree (``workflow/0004``, ``workflow/0006``, Uus teema redesign §8).
"""

from __future__ import annotations

from django.db.models import QuerySet

from app.workflow.models import StageVocabulary


def selectable_stages() -> QuerySet[StageVocabulary]:
    """Every Hetkeseis a person may attach to a Matter today, in reviewed order.

    ``is_active`` is the whole rule, exactly as it is for Valdkonnad: a retired
    stage keeps its row, its Matters and its place in the statistics, and simply
    stops being offered for new work (app/taxonomy/vocabulary.py).
    """
    return StageVocabulary.objects.filter(is_active=True).order_by("sort_order", "label_et")


def stage_help_texts() -> dict[str, str]:
    """``{stage id as text: explanation}`` for every offered stage.

    Keyed by the *string* form of the primary key, because that is what a
    rendered radio carries and what a template comparison sees. A stage with no
    explanation is left out rather than mapped to an empty string, so a caller
    can ask "is there help for this one" without also asking whether the answer
    is blank — which is what decides whether a chip gets the affordance at all.

    One query, handed to the template by the form. A tag that looked each stage
    up as it rendered would put eleven queries on a page whose whole argument is
    that it loads in one.
    """
    return {
        str(stage.pk): stage.help_text
        for stage in selectable_stages().only("id", "help_text")
        if stage.help_text.strip()
    }
