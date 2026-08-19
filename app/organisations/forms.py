"""Forms for the institution quick-create."""

from __future__ import annotations

from django import forms

from app.organisations.models import OrganisationType


class OrganisationQuickCreateForm(forms.Form):
    """The smallest form that can record a real institution.

    Name only. Type defaults to OTHER and the registry code is optional,
    because demanding metadata at the moment somebody is trying to file an
    incoming letter is how people stop using the field and start typing the
    ministry into the title instead.
    """

    name = forms.CharField(
        label="Nimi",
        max_length=300,
        widget=forms.TextInput(
            attrs={
                "class": "field__input",
                "placeholder": "Näiteks: Riigikogu majanduskomisjon",
                "autocomplete": "off",
            }
        ),
    )
    organisation_type = forms.ChoiceField(
        label="Tüüp",
        choices=OrganisationType.choices,
        initial=OrganisationType.OTHER,
        required=False,
        widget=forms.Select(attrs={"class": "field__input"}),
    )
    registry_code = forms.CharField(
        label="Registrikood",
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={"class": "field__input", "autocomplete": "off"}),
    )

    def clean_organisation_type(self) -> str:
        return self.cleaned_data.get("organisation_type") or OrganisationType.OTHER
