"""Haldus → Klassifikaatorid: the department's reference vocabulary, managed (docs/adr/0187).

No models of its own and no second copy of any vocabulary: the pages read and
write `app.organisations` and `app.taxonomy` through those apps' own services,
and show the controlled vocabularies — Valdkond, Õigusakt, Hetkeseis — read-only.
"""
