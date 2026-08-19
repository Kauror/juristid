from __future__ import annotations

from typing import Any

from django import forms

from app.matters.forms import set_choices
from app.organisations.models import Organisation
from app.submissions.enums import SubmissionKind

SELECT_WIDGET = forms.Select(attrs={"class": "field__input"})


class SubmissionCreateForm(forms.Form):
    title = forms.CharField(
        label="Pealkiri",
        max_length=400,
        widget=forms.TextInput(
            attrs={"class": "field__input", "placeholder": "Näiteks: Koja arvamus eelnõule"}
        ),
    )
    kind = forms.ChoiceField(
        label="Liik",
        choices=SubmissionKind.choices,
        initial=SubmissionKind.FORMAL_OPINION,
        widget=SELECT_WIDGET,
    )
    recipients = forms.ModelMultipleChoiceField(
        label="Adressaadid",
        queryset=Organisation.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "field__input", "size": "4"}),
    )
    joint_submitters = forms.ModelMultipleChoiceField(
        label="Kaasesitajad",
        queryset=Organisation.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "field__input", "size": "3"}),
        help_text="Ühispöördumise puhul teised esitajad.",
    )
    channel = forms.CharField(
        label="Kanal",
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={"class": "field__input", "placeholder": "EIS, e-post…"}),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        organisations = Organisation.objects.order_by("name")
        set_choices(self, "recipients", organisations)
        set_choices(self, "joint_submitters", organisations)


class FinalEvidenceForm(forms.Form):
    """Attach the exact binary that is being sent.

    Either a new upload or an evidence version already captured in this Matter.
    Both paths end at the same immutable DocumentVersion.
    """

    upload = forms.FileField(label="Lõplik fail", required=False)
    existing_version = forms.CharField(label="Olemasolev tõend", required=False)

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if not cleaned.get("upload") and not cleaned.get("existing_version"):
            raise forms.ValidationError("Vali fail või olemasolev tõend.")
        return cleaned


class MarkSentForm(forms.Form):
    channel = forms.CharField(
        label="Kanal", max_length=200, required=False, widget=forms.TextInput()
    )
    reference = forms.CharField(
        label="Viide", max_length=200, required=False, widget=forms.TextInput()
    )
