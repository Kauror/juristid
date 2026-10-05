"""Whether a form field must be filled in — the one answer every label reads.

`Field.required` is the ordinary declaration. `marks_required` is for a field
declared `required=False` whose emptiness the form's `clean` refuses all the
same, with a sentence of its own rather than Django's (`Mida tegid?` is one).
Both are read by `{% field_label %}` (app/core/templatetags/form_labels.py,
docs/adr/0140 §6).
"""

from __future__ import annotations

from typing import Any


def marks_required[F](field: F) -> F:
    """Declare that ``field`` must be filled in although it is `required=False`.

    Django copies a form's fields per instance with `deepcopy`, which keeps the
    attribute.
    """
    field.marks_required = True  # type: ignore[attr-defined]
    return field


def is_required(field_or_bound: Any) -> bool:
    """Whether the label of this field (or bound field) carries the required marker."""
    field = getattr(field_or_bound, "field", field_or_bound)
    return bool(getattr(field, "required", False) or getattr(field, "marks_required", False))
