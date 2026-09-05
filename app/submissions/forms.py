from __future__ import annotations

from datetime import date
from typing import Any, cast

from django import forms
from django.utils import timezone

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
    for_information = forms.ModelMultipleChoiceField(
        label="Teadmiseks",
        queryset=Organisation.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "field__input", "size": "3"}),
        help_text="Saajad, kellele saadetakse koopia. Neid ei loeta adressaatideks.",
    )
    joint_submitters = forms.ModelMultipleChoiceField(
        label="Kaasesitajad",
        queryset=Organisation.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "field__input", "size": "3"}),
        help_text="Ühispöördumise puhul teised esitajad. Kinnitus märgitakse eraldi.",
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
        set_choices(self, "for_information", organisations)
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


class RegisterSentOpinionForm(SubmissionCreateForm):
    """«Registreeri saatmine» — a file already on the Matter went out.

    The same questions `SubmissionCreateForm` asks, plus the two facts that only
    exist once something has actually been sent: **which file** and **when**.
    Subclassed rather than copied so a field added to opinion creation is asked
    for here too, instead of being silently missing from the path most opinions
    will now take.

    `document` is a plain `ChoiceField` over identifiers the *view* resolved
    under the reader's own visibility scope, and the view resolves them again
    before anything is written. The choices are a usability gate, never the
    authorization one — a form's own vocabulary is submitted by the browser, and
    treating it as a permission check is how a crafted post binds a document
    somebody may not see (`app/submissions/views.py`).

    `sent_on` is optional and is a **day**. A person recording that an opinion
    went out on the 12th knows the day and not the hour, so a supplied date is
    stored as aware midnight with `SentAtPrecision.DATE` and the UI never reads
    that anchor back as «00:00». Left empty means *now*, which is a real moment
    and is stored as one (`app/submissions/enums.py`).
    """

    document = forms.ChoiceField(
        label="Saadetud fail",
        choices=(),
        widget=SELECT_WIDGET,
        help_text="Teema arvamused, millel ei ole veel kanoonilist saatmiskirjet.",
    )
    reference = forms.CharField(
        label="Viide",
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={"class": "field__input", "placeholder": "Kirja number…"}),
    )
    sent_on = forms.DateField(
        label="Saadetud",
        required=False,
        widget=forms.DateInput(attrs={"class": "field__input", "type": "date"}),
        help_text="Jäta tühjaks, kui arvamus läheb välja praegu.",
    )

    #: The order the panel renders in: what went out, what it was, who got it,
    #: then the two bookkeeping fields. `title` and `kind` come from the parent
    #: and keep their own labels.
    field_order = (
        "document",
        "title",
        "kind",
        "recipients",
        "for_information",
        "joint_submitters",
        "channel",
        "reference",
        "sent_on",
    )

    def __init__(self, *args: Any, documents: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # `cast` rather than a runtime check: the field is declared on this class
        # three lines up, and a `TypedChoiceField` it demonstrably is.
        cast(forms.ChoiceField, self.fields["document"]).choices = [
            (str(document.pk), _document_label(document)) for document in documents or []
        ]
        self.order_fields(self.field_order)

    def clean_sent_on(self) -> date | None:
        """A send is never in the future. The record says what happened."""
        value = self.cleaned_data.get("sent_on")
        if value is not None and value > timezone.localdate():
            raise forms.ValidationError("Saatmise kuupäev ei saa olla tulevikus.")
        return value


def _document_label(document: Any) -> str:
    """How one opinion file names itself in the select.

    The filename, because that is what a lawyer recognises — the stored title is
    frequently the submission's own wording and reads as a near-copy of the
    Teema. Falls back to the title for a document whose first version failed to
    save, which cannot be registered as sent anyway and is filtered out before
    it reaches here.
    """
    version = document.current_version
    return version.original_filename if version is not None else document.title
