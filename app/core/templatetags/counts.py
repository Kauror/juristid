"""How a number and the thing it counts read together, in Estonian.

Every count on every surface was rendered as `N` followed by the partitive —
«3 teemat», «12 faili», «0 kirjet» — which is right for nought and for two
upwards and wrong for exactly one. The register header said «1 teemat», Osakond
said «1 avatud teemat», the documents tab said «1 faili» and the chronology said
«1 kirjet». A native reader meets the slip on the first screen, and it is the
kind of mistake that makes a careful application read as a translated one
(adversarial QA 2026-09-12, QA-12).

One filter rather than twenty `{% if count == 1 %}` branches, because the branch
is the same every time and a template that carries it is a template somebody
will forget to carry it in.

**Not a morphology engine.** Estonian declension is not derivable from a
nominative, and nothing here tries: the two forms are given at the call site,
where the person writing the sentence already knows both. What this owns is the
one rule that is genuinely uniform — *which* of the two a given number takes.
"""

from __future__ import annotations

from typing import Any

from django import template

register = template.Library()


@register.filter(name="counted")
def counted(value: Any, forms: str) -> str:
    """``{{ total|counted:"teema,teemat" }}`` — the number and the right form.

    ``forms`` is the nominative singular and the partitive, comma-separated, in
    that order. One is the nominative; everything else — nought included — is
    the partitive.

    A value that is not a number is returned with the partitive, which is what
    every one of these surfaces did before this existed: a count that cannot be
    read is a template bug, and swallowing it into a blank or an exception in the
    middle of a rendered page is not how it should surface.
    """
    singular, _, plural = forms.partition(",")
    singular, plural = singular.strip(), plural.strip()
    try:
        number = int(value)
    except (TypeError, ValueError):
        return f"{value} {plural or singular}"
    return f"{number} {singular if number == 1 else plural}"
