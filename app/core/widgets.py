"""Shared form controls.

One entry so far, and it is here rather than in ``app/matters/forms.py`` because
five modules render a date and a sixth will: a date control defined next to the
Matter forms is a date control the intelligence forms copy rather than import,
and the copy is where ``mm/dd/yyyy`` came back last time.
"""

from __future__ import annotations

from typing import Any

from django import forms

from app.core.dates import (
    ESTONIAN_DATE_ERROR,
    ESTONIAN_DATE_INPUT_FORMATS,
    ESTONIAN_DATE_PLACEHOLDER,
    format_estonian_date,
)


class EstonianDateInput(forms.DateInput):
    """A date box that renders and reads Estonian, in every browser.

    ``type="text"``, deliberately. A native date input takes its format from the
    *browser's* locale rather than the page's, so a US-English Chrome showed
    ``mm/dd/yyyy`` on an Estonian form and read ``7.9.2026`` as the 9th of July.
    No server setting reaches inside that control; the only fix is not to use it
    (app/core/dates.py).

    ``data-datepicker`` is what the calendar in ``static/js/app.js`` binds to.
    Progressive enhancement only: with scripting off this is a text box that
    accepts the same three formats the field does, which is more than the native
    control offered a keyboard user anyway.

    ``inputmode="numeric"`` puts a phone or a tablet on the number pad without
    claiming the value is a number — ``type="number"`` would let a browser
    render spinners and strip the dots.
    """

    def __init__(self, attrs: dict[str, Any] | None = None) -> None:
        defaults: dict[str, Any] = {
            "type": "text",
            "class": "field__input dateinput",
            "inputmode": "numeric",
            "autocomplete": "off",
            "placeholder": ESTONIAN_DATE_PLACEHOLDER,
            "data-datepicker": "true",
        }
        defaults.update(attrs or {})
        super().__init__(attrs=defaults)

    def format_value(self, value: Any) -> str:
        """Render the stored date the one way this application writes dates.

        Overridden rather than passed as ``format=``: Django formats through
        ``strftime``, whose no-leading-zero directive differs between the
        platform this is developed on and the one it is deployed on.

        A *string* value is returned untouched. That is a redisplayed POST — the
        text somebody typed and the server refused — and replacing it with a
        blank box loses the input while the error message says to correct it.
        """
        if value in (None, ""):
            return ""
        if isinstance(value, str):
            return value
        return format_estonian_date(value)


class EstonianDateField(forms.DateField):
    """``forms.DateField`` that reads what an Estonian writes.

    ISO stays in the accepted set. Every link, bookmark and test written before
    this field existed carries ``2026-09-07``, and refusing them would turn a
    presentation change into a data-entry outage.
    """

    widget = EstonianDateInput

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("input_formats", list(ESTONIAN_DATE_INPUT_FORMATS))
        errors = {"invalid": ESTONIAN_DATE_ERROR}
        errors.update(kwargs.pop("error_messages", None) or {})
        kwargs["error_messages"] = errors
        super().__init__(**kwargs)


class DescribedRadioSelect(forms.RadioSelect):
    """Radios that point at the explanation rendered beside them.

    The `Hetkeseis` chips on `Uus teema` carry a tooltip saying from which event
    until which event a file sits in that stage. Sighted readers get it from
    hover or from focus; a screen reader gets it from `aria-describedby`, and
    nothing else on the page can supply that — the description belongs to *this
    radio*, not to the group.

    ``descriptions`` is a ``{value: text}`` mapping the form fills in, and the id
    is derived from the option's own id, so the template that renders the bubble
    and the attribute that points at it cannot disagree about the name. An
    option with no description is left exactly as it was, which is what keeps
    the named blank option ("Määramata") free of a dangling reference.
    """

    #: Set per form instance. Django deep-copies `base_fields` — widgets
    #: included — so assigning this in `__init__` cannot leak between requests.
    descriptions: dict[str, str]

    #: How the bubble's id is built from the option's. One rule, used by the
    #: widget and by the template.
    DESCRIPTION_SUFFIX = "-selgitus"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.descriptions = {}

    def create_option(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        option = super().create_option(*args, **kwargs)
        text = self.descriptions.get(str(option["value"]), "")
        option_id = option["attrs"].get("id")
        if text and option_id:
            option["attrs"]["aria-describedby"] = f"{option_id}{self.DESCRIPTION_SUFFIX}"
        return option
