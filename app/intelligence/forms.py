"""Forms parse and validate. They never write.

Every one of these hands cleaned values to a function in
``app.intelligence.services``; nothing here touches a model field, so the audit
trail and the constraints cannot be bypassed by adding another view
(master specification 12.4).

The interesting part is the period control. A lawyer knows "II kvartal 2027",
not "1 April 2027", and asking them to type an anchor date would be asking them
to invent a day. So the form asks for the precision first and then only for the
parts that precision needs, and ``app.workflow.dates`` turns the answer into the
anchor and period end the database stores. The user never sees or types the
anchor (Stage-2G brief 7, 49, 51).
"""

from __future__ import annotations

from datetime import date
from typing import Any, cast

from django import forms

from app.intelligence.enums import EffectiveDateKind
from app.workflow.dates import MAX_YEAR, MIN_YEAR, InvalidPeriod, bounds_for
from app.workflow.enums import ESTONIAN_MONTHS, ROMAN_QUARTERS, DatePrecision

TEXT_WIDGET = forms.TextInput(attrs={"class": "field__input"})
SELECT_WIDGET = forms.Select(attrs={"class": "field__input field__input--compact"})
DATE_WIDGET = forms.DateInput(attrs={"type": "date", "class": "field__input"})

#: The precisions a person may choose. ``INFERRED`` is deliberately absent: it
#: records that a value was derived from free text by an importer, which is not
#: something anybody types into a form (app/workflow/enums.py).
#:
#: The wording is "Kvartali täpsusega" rather than "Kvartal" on purpose. The
#: control below has a *field* labelled "Kvartal" — the one that asks which
#: quarter — and two controls sharing a label is a form where a screen reader,
#: and a browser test, cannot say which one is meant.
PRECISION_CHOICES: tuple[tuple[str, str], ...] = (
    (DatePrecision.EXACT.value, "Täpne kuupäev"),
    (DatePrecision.MONTH.value, "Kuu täpsusega"),
    (DatePrecision.QUARTER.value, "Kvartali täpsusega"),
    (DatePrecision.HALF_YEAR.value, "Poolaasta täpsusega"),
    (DatePrecision.YEAR.value, "Aasta täpsusega"),
)

#: The same list plus "no period at all", for a work victory whose timing is
#: genuinely unknown. Unknown is data; it is not a field somebody forgot
#: (Stage-2G brief 21, 69).
NO_PERIOD = ""
VICTORY_PRECISION_CHOICES: tuple[tuple[str, str], ...] = (
    *PRECISION_CHOICES,
    (NO_PERIOD, "Teadmata periood"),
)

MONTH_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (str(number), name.capitalize()) for number, name in enumerate(ESTONIAN_MONTHS, start=1)
)
QUARTER_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (str(number), f"{numeral} kvartal") for number, numeral in enumerate(ROMAN_QUARTERS, start=1)
)
HALF_CHOICES: tuple[tuple[str, str], ...] = (
    ("1", "I poolaasta"),
    ("2", "II poolaasta"),
)


class PeriodForm(forms.Form):
    """The shared precision control.

    Subclasses declare their own business fields and call
    :meth:`cleaned_period`. Keeping the parsing here is what stops a quarter
    entered on the Matter page and a quarter entered anywhere else from
    normalising to two different anchors.
    """

    #: Overridden by the work-victory form, which also offers "no period".
    precision_choices = PRECISION_CHOICES
    #: Whether a period must be given at all.
    period_required = True

    precision = forms.ChoiceField(
        label="Täpsus",
        choices=PRECISION_CHOICES,
        initial=DatePrecision.EXACT,
        widget=forms.RadioSelect(attrs={"class": "precision__radio"}),
    )
    exact_date = forms.DateField(label="Kuupäev", required=False, widget=DATE_WIDGET)
    month = forms.ChoiceField(
        label="Kuu", choices=(("", "—"), *MONTH_CHOICES), required=False, widget=SELECT_WIDGET
    )
    quarter = forms.ChoiceField(
        label="Kvartal", choices=(("", "—"), *QUARTER_CHOICES), required=False, widget=SELECT_WIDGET
    )
    half = forms.ChoiceField(
        label="Poolaasta", choices=(("", "—"), *HALF_CHOICES), required=False, widget=SELECT_WIDGET
    )
    year = forms.IntegerField(
        label="Aasta",
        required=False,
        min_value=MIN_YEAR,
        max_value=MAX_YEAR,
        widget=forms.NumberInput(attrs={"class": "field__input field__input--compact"}),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Per instance, so a subclass can widen the vocabulary without the
        # class-level field being shared between forms.
        precision_field = cast(forms.ChoiceField, self.fields["precision"])
        precision_field.choices = list(self.precision_choices)
        if not self.period_required:
            precision_field.required = False

    def _int(self, name: str) -> int | None:
        value = self.cleaned_data.get(name)
        return int(value) if value not in (None, "") else None

    def cleaned_period(self) -> tuple[date | None, date | None, str]:
        """The anchor, the period end and the precision, or three empties.

        Raises nothing: a bad combination is reported through ``add_error`` on
        the field that is actually missing, so the page says *which* box to
        fill rather than "invalid input" (Stage-2G brief 50).
        """
        precision = self.cleaned_data.get("precision") or NO_PERIOD
        if precision == NO_PERIOD:
            return None, None, DatePrecision.YEAR.value

        field_for_precision = {
            DatePrecision.EXACT.value: "exact_date",
            DatePrecision.MONTH.value: "month",
            DatePrecision.QUARTER.value: "quarter",
            DatePrecision.HALF_YEAR.value: "half",
            DatePrecision.YEAR.value: "year",
        }
        try:
            start, end = bounds_for(
                precision,
                exact_date=self.cleaned_data.get("exact_date"),
                year=self._int("year"),
                month=self._int("month"),
                quarter=self._int("quarter"),
                half=self._int("half"),
            )
        except InvalidPeriod as error:
            self.add_error(field_for_precision.get(precision, "precision"), str(error))
            return None, None, precision
        return start, end, precision

    @staticmethod
    def initial_for(value: date | None, precision: str) -> dict[str, Any]:
        """Reopen a stored period in the control that produced it.

        The stored anchor is never shown as a date: a QUARTER record comes back
        as the quarter and the year, which is what somebody chose.
        """
        if value is None:
            return {"precision": NO_PERIOD}
        if precision == DatePrecision.YEAR:
            return {"precision": precision, "year": value.year}
        if precision == DatePrecision.HALF_YEAR:
            return {
                "precision": precision,
                "half": "1" if value.month <= 6 else "2",
                "year": value.year,
            }
        if precision == DatePrecision.QUARTER:
            return {
                "precision": precision,
                "quarter": str((value.month - 1) // 3 + 1),
                "year": value.year,
            }
        if precision == DatePrecision.MONTH:
            return {"precision": precision, "month": str(value.month), "year": value.year}
        return {"precision": DatePrecision.EXACT.value, "exact_date": value}


class ImportantDateForm(PeriodForm):
    """`Oluline tähtaeg` — a description and when it is expected."""

    title = forms.CharField(
        label="Mis on oodata",
        max_length=2000,
        widget=forms.TextInput(
            attrs={
                "class": "field__input field__input--prominent",
                "placeholder": "Näiteks: eelnõu kooskõlastusring",
            }
        ),
    )
    note = forms.CharField(
        label="Märkus",
        required=False,
        widget=forms.Textarea(attrs={"class": "field__input", "rows": "2"}),
    )

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        start, end, precision = self.cleaned_period()
        cleaned["date_value"] = start
        cleaned["period_end"] = end
        cleaned["date_precision"] = precision
        return cleaned

    def as_service_kwargs(self) -> dict[str, Any]:
        return {
            "title": self.cleaned_data["title"],
            "date_value": self.cleaned_data["date_value"],
            "period_end": self.cleaned_data["period_end"],
            "date_precision": self.cleaned_data["date_precision"],
            "note": self.cleaned_data.get("note") or "",
        }

    @classmethod
    def from_record(cls, record: Any) -> ImportantDateForm:
        return cls(
            initial={
                "title": record.title,
                "note": record.note,
                **cls.initial_for(record.date_value, record.date_precision),
            }
        )


class EffectiveDateForm(PeriodForm):
    """`Jõustumine` — what comes into force, and what is known about when."""

    period_required = False

    kind = forms.ChoiceField(
        label="Jõustumine",
        choices=EffectiveDateKind.choices,
        initial=EffectiveDateKind.KNOWN_DATE,
        widget=forms.RadioSelect(attrs={"class": "precision__radio"}),
    )
    description = forms.CharField(
        label="Mis jõustub",
        required=False,
        max_length=2000,
        widget=forms.TextInput(
            attrs={"class": "field__input", "placeholder": "Näiteks: põhiosa või osad sätted"}
        ),
    )
    source_url = forms.URLField(
        label="Ametlik allikas",
        required=False,
        assume_scheme="https",
        max_length=1000,
        widget=forms.URLInput(attrs={"class": "field__input", "placeholder": "https://…"}),
    )
    note = forms.CharField(
        label="Märkus",
        required=False,
        widget=forms.Textarea(attrs={"class": "field__input", "rows": "2"}),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # The precision control is only meaningful for a known date, so it is
        # not required on its own; `clean` decides.
        self.fields["precision"].required = False

    def _period_was_filled(self) -> bool:
        return any(
            self.cleaned_data.get(name) not in (None, "")
            for name in ("exact_date", "month", "quarter", "half", "year")
        )

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        kind = cleaned.get("kind")

        if kind == EffectiveDateKind.KNOWN_DATE:
            if not cleaned.get("precision"):
                cleaned["precision"] = DatePrecision.EXACT.value
                self.cleaned_data["precision"] = DatePrecision.EXACT.value
            start, end, precision = self.cleaned_period()
            if start is None and not self.errors:
                self.add_error("exact_date", "Teadaoleva jõustumise puhul on kuupäev kohustuslik.")
            cleaned["date_value"] = start
            cleaned["period_end"] = end
            cleaned["date_precision"] = precision
            return cleaned

        # A kind that says the date is not known must not carry one. Refused
        # rather than silently dropped: quietly discarding what somebody typed
        # is how a date they believed they had recorded disappears
        # (Stage-2G brief 50).
        if self._period_was_filled():
            self.add_error("kind", "Kuupäeva saab määrata ainult teadaoleva jõustumise puhul.")
        cleaned["date_value"] = None
        cleaned["period_end"] = None
        cleaned["date_precision"] = DatePrecision.EXACT.value
        return cleaned

    def as_service_kwargs(self) -> dict[str, Any]:
        return {
            "kind": self.cleaned_data["kind"],
            "date_value": self.cleaned_data["date_value"],
            "period_end": self.cleaned_data["period_end"],
            "date_precision": self.cleaned_data["date_precision"],
            "description": self.cleaned_data.get("description") or "",
            "source_url": self.cleaned_data.get("source_url") or "",
            "note": self.cleaned_data.get("note") or "",
        }

    @classmethod
    def from_record(cls, record: Any) -> EffectiveDateForm:
        return cls(
            initial={
                "kind": record.kind,
                "description": record.description,
                "source_url": record.source_url,
                "note": record.note,
                **cls.initial_for(record.date_value, record.date_precision),
            }
        )


class WorkVictoryForm(PeriodForm):
    """A `Töövõit` as somebody describes it: the words, the period, the source.

    The form carries no status. Which state the row is created in belongs to
    the service that receives it — confirmed when a person adds one from the
    Matter page, a candidate when a machine or an import proposes one — and a
    status field here would let a request choose (app/intelligence/services.py).
    """

    precision_choices = VICTORY_PRECISION_CHOICES
    period_required = False

    title = forms.CharField(
        label="Töövõit",
        max_length=2000,
        widget=forms.TextInput(
            attrs={
                "class": "field__input field__input--prominent",
                "placeholder": "Näiteks: Koja ettepanek võeti eelnõusse üle",
            }
        ),
    )
    detail = forms.CharField(
        label="Selgitus",
        required=False,
        widget=forms.Textarea(attrs={"class": "field__input", "rows": "3"}),
    )
    source_url = forms.URLField(
        label="Viide",
        required=False,
        assume_scheme="https",
        max_length=1000,
        widget=forms.URLInput(attrs={"class": "field__input", "placeholder": "https://…"}),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["precision"].initial = DatePrecision.YEAR
        self.fields["precision"].label = "Periood"

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        start, end, precision = self.cleaned_period()
        cleaned["period_date"] = start
        cleaned["period_end"] = end
        cleaned["date_precision"] = precision
        return cleaned

    def as_service_kwargs(self) -> dict[str, Any]:
        return {
            "title": self.cleaned_data["title"],
            "detail": self.cleaned_data.get("detail") or "",
            "period_date": self.cleaned_data["period_date"],
            "period_end": self.cleaned_data["period_end"],
            "date_precision": self.cleaned_data["date_precision"],
            "source_url": self.cleaned_data.get("source_url") or "",
        }

    @classmethod
    def from_record(cls, record: Any) -> WorkVictoryForm:
        return cls(
            initial={
                "title": record.title,
                "detail": record.detail,
                "source_url": record.source_url,
                **cls.initial_for(record.period_date, record.date_precision),
            }
        )


class ReasonForm(forms.Form):
    """The one field a cancellation or a rejection may carry.

    Optional on purpose. Requiring an explanation is how a status that should
    have been set stays unset (master specification 3.8).
    """

    reason = forms.CharField(
        label="Selgitus",
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={"class": "field__input", "rows": "2"}),
    )
