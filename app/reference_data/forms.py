"""Forms for Haldus → Klassifikaatorid.

Shape only: every rule — duplicates, the reviewed baseline, stale pages — is in
`app.organisations.administration` and `app.taxonomy.administration`, and a
refusal from there is added to the form as the field's error.
"""

from __future__ import annotations

from typing import Any

from django import forms

from app.organisations.forms import OrganisationQuickCreateForm
from app.organisations.models import AliasType
from app.taxonomy.models import Tag

_INPUT = {"class": "field__input", "autocomplete": "off"}


class RevisionForm(forms.Form):
    """The version of the row the page was drawn from (optimistic concurrency)."""

    revision = forms.CharField(widget=forms.HiddenInput, required=False)

    def revision_token(self) -> str | None:
        # An empty token is a page that did not say which version it held. Such
        # a post is treated as stale-unknown rather than as «no opinion»: every
        # page here renders one, so its absence is a crafted request.
        return self.cleaned_data.get("revision") or ""


class OrganisationCreateForm(OrganisationQuickCreateForm):
    """The quick-create's three fields; the catalogue page refuses a duplicate."""


class OrganisationRenameForm(RevisionForm):
    name = forms.CharField(label="Õige nimi", max_length=300, widget=forms.TextInput(attrs=_INPUT))
    keep_old_name = forms.BooleanField(
        label="Jäta senine nimi ajalooliseks nimekujuks",
        required=False,
        initial=True,
        help_text="Eemalda linnuke ainult siis, kui senine nimi oli lihtsalt kirjaviga.",
    )


class OrganisationAliasForm(RevisionForm):
    alias = forms.CharField(label="Nimekuju", max_length=300, widget=forms.TextInput(attrs=_INPUT))
    alias_type = forms.ChoiceField(
        label="Liik",
        choices=[
            (value, label)
            for value, label in AliasType.choices
            # A historical name is recorded by correcting the name, so that the
            # catalogue says which name came first.
            if value != AliasType.HISTORICAL_NAME
        ],
        initial=AliasType.SYNONYM,
        widget=forms.Select(attrs={"class": "field__input"}),
    )


class TagCreateForm(forms.Form):
    name = forms.CharField(label="Nimi", max_length=200, widget=forms.TextInput(attrs=_INPUT))
    key = forms.CharField(
        label="Võti",
        max_length=64,
        help_text="Püsiv tunnus aruandluses, näiteks halduskoormus. Hiljem seda ei muudeta.",
        widget=forms.TextInput(attrs=_INPUT),
    )
    definition = forms.CharField(
        label="Tähendus",
        required=False,
        help_text="Millal seda silti kasutada ja millal mitte.",
        widget=forms.Textarea(attrs={"class": "field__input", "rows": 3}),
    )


class TagEditForm(RevisionForm):
    name = forms.CharField(label="Nimi", max_length=200, widget=forms.TextInput(attrs=_INPUT))
    definition = forms.CharField(
        label="Tähendus",
        required=False,
        widget=forms.Textarea(attrs={"class": "field__input", "rows": 3}),
    )
    keep_old_name = forms.BooleanField(
        label="Jäta senine nimi nimekujuks",
        required=False,
        initial=True,
        help_text="Nii leitakse varem selle nime all sildistatud kirjed ka vana sõnaga.",
    )


class TagAliasForm(RevisionForm):
    alias = forms.CharField(label="Nimekuju", max_length=200, widget=forms.TextInput(attrs=_INPUT))


class TagMergeForm(RevisionForm):
    into = forms.ModelChoiceField(
        label="Liida sildiga",
        queryset=Tag.objects.none(),
        empty_label="Vali silt",
        widget=forms.Select(attrs={"class": "field__input"}),
    )

    def __init__(self, *args: Any, tag: Tag, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        field: Any = self.fields["into"]
        field.queryset = (
            Tag.objects.filter(is_active=True, merged_into__isnull=True)
            .exclude(pk=tag.pk)
            .order_by("name_et")
        )
