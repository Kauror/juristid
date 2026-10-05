"""One rule for how a form label says whether its field must be filled in.

**Optional is the ordinary case and carries no marker; required carries a small
red `*`** (docs/adr/0140 §6). The word «valikuline» repeated beside most labels
made every form read as a list of exceptions, and the few fields a save cannot
do without were the ones nothing pointed at.

The answer is read off the field, never typed into a template
(`app.core.required_fields`): a template writes ``{% field_label form.title %}``
where it used to write ``{{ form.title.label }}``, and the marker follows the
form. A label whose text is not a form field's — a group heading over several
inputs — is required only when its template says so, with ``{% required_mark %}``.
"""

from __future__ import annotations

from typing import Any

from django import template
from django.utils.html import conditional_escape, format_html
from django.utils.safestring import SafeString, mark_safe

from app.core.required_fields import is_required

register = template.Library()

#: The marker: a small red `*` for the eye, and the word for a screen reader.
REQUIRED_MARKER = mark_safe(
    '<span class="req-mark" aria-hidden="true">*</span>'
    '<span class="visually-hidden"> (kohustuslik)</span>'
)


def required_marker() -> SafeString:
    return REQUIRED_MARKER


@register.simple_tag
def field_label(bound_field: Any) -> SafeString:
    """The field's label, with the required marker when the field must be filled in."""
    # Read as the template's own `{{ x.label }}` read it: a bound field, or a
    # plain mapping some partials pass in its place — which is never marked.
    if isinstance(bound_field, dict):
        return format_html("{}", bound_field.get("label", ""))
    label = conditional_escape(bound_field.label)
    if is_required(bound_field):
        return format_html("{}{}", label, required_marker())
    return format_html("{}", label)


@register.simple_tag
def required_mark() -> SafeString:
    """The marker alone, for a label whose text is not a form field's."""
    return required_marker()


@register.filter(name="is_required")
def is_required_filter(bound_field: Any) -> bool:
    """For a control with no visible label of its own — a file drop zone."""
    return is_required(bound_field)
