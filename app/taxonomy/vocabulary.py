"""The one place that answers "which Valdkonnad may somebody choose today".

Before this module the answer was assembled independently in the create form,
the register filter and the reporting filters, and the three agreed only because
nobody had changed one of them lately. A vocabulary that is defined three times
is a vocabulary that drifts — a label offered on Uus teema but missing from the
register filter is a Matter nobody can find again — so there is now one
function, and every surface that offers a choice calls it (Teema redesign §7.1).

**Active is the whole rule.** ``PolicyArea.is_active`` is what separates the
current working vocabulary from the areas the department has stopped filing
under. A retired area keeps its row, its relations and its place on the Matters
already classified with it; it simply stops being offered for new work. Nothing
here deletes, renames or reassigns anything.

**Order is the reviewed order.** ``sort_order`` carries the sequence the
department gave, and that is what people see. It deliberately replaces the
usage-frequency ordering the nine-area list used: with twenty-three labels a
stable order can be learned, a list that rearranges itself under the reader
cannot — and an order derived from how often each area is used was also, in the
end, a channel through which the *existence* of restricted work could be
inferred from a checkbox list (Stage-2E.1 brief 19 solved the disclosure by
scoping the count; this removes the derivation).
"""

from __future__ import annotations

from django.db.models import QuerySet

from app.taxonomy.models import PolicyArea


def selectable_policy_areas() -> QuerySet[PolicyArea]:
    """Every Valdkond a person may attach to a Matter today, in reviewed order.

    A queryset rather than a list, so a caller that needs `.count()`, a `values`
    projection or a form's `queryset=` gets one without a round trip it does not
    need.
    """
    return PolicyArea.objects.filter(is_active=True).order_by("sort_order", "name_et")


def policy_area_choices() -> list[tuple[str, str]]:
    """``(key, label)`` pairs for a filter that addresses areas by key.

    The register's Valdkond filter lives in the query string and has always used
    the stable key, so a bookmarked filter survives a rename.
    """
    return [(area.key, area.name_et) for area in selectable_policy_areas()]
