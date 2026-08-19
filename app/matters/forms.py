"""Forms parse and validate input. They never change state.

Every form here hands its cleaned values to a service function in
``app.matters.services`` or ``app.workflow.services``. Nothing in this module
writes a model field, so the audit trail and the invariants cannot be bypassed
by adding another view (master specification 12.4, 23.4).
"""

from __future__ import annotations

from typing import Any, cast

from django import forms
from django.db.models import QuerySet

from app.accounts.models import User
from app.core.enums import Visibility
from app.matters.entry_enums import EntryKind
from app.organisations.models import Organisation
from app.taxonomy.models import PolicyArea, Tag
from app.workflow.enums import (
    ActionKind,
    DatePrecision,
    DateSemantics,
    Disposition,
    Track,
)
from app.workflow.models import StageVocabulary


class UserChoiceField(forms.ModelChoiceField):
    """Show a colleague's name, not "Name (upn@example)".

    ``User.__str__`` includes the UPN because that is what makes a user
    unambiguous in the admin and in logs. In a dropdown of half a dozen
    colleagues it is noise.
    """

    def label_from_instance(self, obj: Any) -> str:
        return obj.display_name or obj.upn


def set_choices(form: forms.Form, name: str, queryset: QuerySet) -> None:
    """Point a choice field at its queryset.

    Deferred to __init__ rather than declared on the class so the query runs per
    request instead of at import time, which is what keeps reference data fresh
    and migrations importable.
    """
    field = cast(forms.ModelChoiceField, form.fields[name])
    field.queryset = queryset


DATE_WIDGET = forms.DateInput(attrs={"type": "date", "class": "field__input"})
TEXT_WIDGET = forms.TextInput(attrs={"class": "field__input"})
SELECT_WIDGET = forms.Select(attrs={"class": "field__input"})


def active_users() -> Any:
    return User.objects.filter(is_active=True).order_by("display_name")


def active_stages() -> Any:
    return StageVocabulary.objects.filter(is_active=True).order_by("sort_order", "label_et")


class MatterCreateForm(forms.Form):
    """Creating a Teema requires a title and nothing else.

    Everything else is optional and disclosed under a details panel. Demanding
    metadata at capture time is precisely what makes people keep a spreadsheet
    open instead (master specification 3.8, 9.1).
    """

    title = forms.CharField(
        label="Pealkiri",
        max_length=1000,
        widget=forms.TextInput(
            attrs={
                "class": "field__input field__input--prominent",
                "autofocus": "autofocus",
                "placeholder": "Näiteks: Pakendiseaduse muutmise eelnõu",
            }
        ),
    )
    owner = UserChoiceField(
        label="Vastutaja",
        queryset=User.objects.none(),
        required=False,
        widget=SELECT_WIDGET,
    )
    stage = forms.ModelChoiceField(
        label="Hetkeseis",
        queryset=StageVocabulary.objects.none(),
        required=False,
        widget=SELECT_WIDGET,
    )
    track = forms.ChoiceField(
        label="Menetlusliik",
        choices=[("", "—"), *Track.choices],
        required=False,
        widget=SELECT_WIDGET,
    )
    source_organisation = forms.ModelChoiceField(
        label="Algataja või saatja",
        queryset=Organisation.objects.none(),
        required=False,
        widget=SELECT_WIDGET,
        help_text="Kellelt teema tuli.",
    )
    addressee_organisation = forms.ModelChoiceField(
        label="Adressaat",
        queryset=Organisation.objects.none(),
        required=False,
        widget=SELECT_WIDGET,
        help_text="Kellele Koda vastab. Eraldi fakt saatjast.",
    )
    received_date = forms.DateField(label="Saabus", required=False, widget=DATE_WIDGET)
    response_deadline = forms.DateField(
        label="Arvamuse tähtaeg", required=False, widget=DATE_WIDGET
    )
    policy_areas = forms.ModelMultipleChoiceField(
        label="Valdkonnad",
        queryset=PolicyArea.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "field__input", "size": "4"}),
    )
    visibility = forms.ChoiceField(
        label="Nähtavus",
        choices=Visibility.choices,
        initial=Visibility.NORMAL,
        # Not required, so a Matter really can be created from a title alone
        # (specification 3.8). On screen the select is always present and
        # preselected; this is about the form not refusing a bare POST.
        required=False,
        widget=SELECT_WIDGET,
        help_text="Piiratud teemat näevad ainult vastutaja, kaastöötajad ja osakonnajuht.",
    )

    def clean_visibility(self) -> str:
        return self.cleaned_data.get("visibility") or Visibility.NORMAL

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        set_choices(self, "owner", active_users())
        set_choices(self, "stage", active_stages())
        set_choices(self, "source_organisation", Organisation.objects.order_by("name"))
        set_choices(self, "addressee_organisation", Organisation.objects.order_by("name"))
        set_choices(
            self,
            "policy_areas",
            PolicyArea.objects.filter(is_active=True).order_by("sort_order", "name_et"),
        )


class NextActionForm(forms.Form):
    """`Järgmiseks`, including what its date actually means."""

    text = forms.CharField(
        label="Järgmiseks",
        max_length=2000,
        widget=forms.TextInput(
            attrs={"class": "field__input", "placeholder": "Mida teed, ootad või jälgid?"}
        ),
    )
    kind = forms.ChoiceField(
        label="Liik", choices=ActionKind.choices, initial=ActionKind.DO, widget=SELECT_WIDGET
    )
    date_semantics = forms.ChoiceField(
        label="Kuupäeva tähendus",
        choices=DateSemantics.choices,
        initial=DateSemantics.DEADLINE,
        widget=SELECT_WIDGET,
    )
    target_date = forms.DateField(label="Kuupäev", required=False, widget=DATE_WIDGET)
    responsible = UserChoiceField(
        label="Vastutaja", queryset=User.objects.none(), required=False, widget=SELECT_WIDGET
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        set_choices(self, "responsible", active_users())

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        kind = cleaned.get("kind")
        semantics = cleaned.get("date_semantics")
        target = cleaned.get("target_date")

        # A deadline with no date cannot be overdue, cannot be planned against
        # and cannot be reported on. It is the one combination worth refusing.
        if kind == ActionKind.DO and semantics == DateSemantics.DEADLINE and target is None:
            self.add_error("target_date", "Tähtajaline tegevus vajab kuupäeva.")
        return cleaned

    def as_service_kwargs(self) -> dict[str, Any]:
        return {
            "text": self.cleaned_data["text"],
            "kind": self.cleaned_data["kind"],
            "date_semantics": self.cleaned_data["date_semantics"],
            "target_date": self.cleaned_data.get("target_date"),
            "date_precision": self.cleaned_data.get("date_precision") or DatePrecision.EXACT,
            "responsible": self.cleaned_data.get("responsible"),
        }


class ComposerForm(forms.Form):
    """The unified composer: one entry, optionally one new `Järgmiseks`.

    Both halves are optional individually and at least one is required, so the
    same box serves "just note this down" and "note this down and here is what
    happens next" without the user choosing a mode first.
    """

    body = forms.CharField(
        label="Sissekanne",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "composer__body",
                "rows": "4",
                "placeholder": "Kirjuta sissekanne…",
                "data-richtext": "true",
            }
        ),
    )
    kind = forms.ChoiceField(
        label="Liik", choices=EntryKind.choices, initial=EntryKind.NOTE, widget=SELECT_WIDGET
    )
    occurred_at = forms.DateTimeField(
        label="Toimus",
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "class": "field__input"}),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"],
    )
    organisation = forms.ModelChoiceField(
        label="Asutus", queryset=Organisation.objects.none(), required=False, widget=SELECT_WIDGET
    )
    attachment = forms.FileField(
        label="Manus",
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "field__input"}),
        help_text="Salvestatakse muutumatu tõendina teema dokumentide alla.",
    )

    update_next_action = forms.BooleanField(
        label="Muuda ka Järgmiseks", required=False, widget=forms.CheckboxInput()
    )
    next_text = forms.CharField(
        label="Järgmiseks",
        required=False,
        max_length=2000,
        widget=forms.TextInput(
            attrs={"class": "field__input", "placeholder": "Mida teed, ootad või jälgid?"}
        ),
    )
    next_kind = forms.ChoiceField(
        label="Liik",
        choices=ActionKind.choices,
        initial=ActionKind.DO,
        required=False,
        widget=SELECT_WIDGET,
    )
    next_date_semantics = forms.ChoiceField(
        label="Kuupäeva tähendus",
        choices=DateSemantics.choices,
        initial=DateSemantics.DEADLINE,
        required=False,
        widget=SELECT_WIDGET,
    )
    next_target_date = forms.DateField(label="Kuupäev", required=False, widget=DATE_WIDGET)
    next_date_precision = forms.ChoiceField(
        label="Täpsus",
        choices=DatePrecision.choices,
        initial=DatePrecision.EXACT,
        required=False,
        widget=SELECT_WIDGET,
        help_text="Ligikaudse aja jaoks vali kuu või kvartal.",
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        set_choices(self, "organisation", Organisation.objects.order_by("name"))

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        body = (cleaned.get("body") or "").strip()
        wants_action = cleaned.get("update_next_action")

        if not body and not wants_action:
            raise forms.ValidationError("Kirjuta sissekanne või muuda Järgmiseks.")

        if wants_action:
            if not (cleaned.get("next_text") or "").strip():
                self.add_error("next_text", "Järgmiseks vajab teksti.")
            if (
                cleaned.get("next_kind") == ActionKind.DO
                and cleaned.get("next_date_semantics") == DateSemantics.DEADLINE
                and cleaned.get("next_target_date") is None
            ):
                self.add_error("next_target_date", "Tähtajaline tegevus vajab kuupäeva.")
        return cleaned

    def next_action_kwargs(self) -> dict[str, Any] | None:
        if not self.cleaned_data.get("update_next_action"):
            return None
        return {
            "text": self.cleaned_data["next_text"],
            "kind": self.cleaned_data["next_kind"] or ActionKind.DO,
            "date_semantics": self.cleaned_data["next_date_semantics"] or DateSemantics.DEADLINE,
            "target_date": self.cleaned_data.get("next_target_date"),
            "date_precision": self.cleaned_data.get("next_date_precision") or DatePrecision.EXACT,
        }


class MatterFieldForm(forms.Form):
    """Inline edits from the Matter header.

    One small form per field rather than one large Edit Matter page: changing
    an owner should not mean re-submitting every other value on the record.
    """

    owner = UserChoiceField(queryset=User.objects.none(), required=False)
    stage = forms.ModelChoiceField(queryset=StageVocabulary.objects.none(), required=False)
    track = forms.ChoiceField(choices=[("", "—"), *Track.choices], required=False)
    source_organisation = forms.ModelChoiceField(
        queryset=Organisation.objects.none(), required=False
    )
    addressee_organisation = forms.ModelChoiceField(
        queryset=Organisation.objects.none(), required=False
    )
    received_date = forms.DateField(required=False)
    response_deadline = forms.DateField(required=False)
    visibility = forms.ChoiceField(choices=Visibility.choices, required=False)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        set_choices(self, "owner", active_users())
        set_choices(self, "stage", active_stages())
        set_choices(self, "source_organisation", Organisation.objects.order_by("name"))
        set_choices(self, "addressee_organisation", Organisation.objects.order_by("name"))


class PositionForm(forms.Form):
    position_summary = forms.CharField(
        label="Koja seisukoht",
        required=False,
        widget=forms.Textarea(attrs={"class": "field__input", "rows": "5"}),
    )
    rationale_summary = forms.CharField(
        label="Põhjendus",
        required=False,
        widget=forms.Textarea(attrs={"class": "field__input", "rows": "5"}),
    )


class CloseMatterForm(forms.Form):
    disposition = forms.ChoiceField(
        label="Lõpetamise põhjus", choices=Disposition.choices, widget=SELECT_WIDGET
    )
    reason = forms.CharField(
        label="Selgitus",
        required=False,
        widget=forms.Textarea(attrs={"class": "field__input", "rows": "3"}),
    )


class TagAssignmentForm(forms.Form):
    tag = forms.ModelChoiceField(label="Silt", queryset=Tag.objects.none(), widget=SELECT_WIDGET)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        set_choices(self, "tag", Tag.objects.filter(is_active=True).order_by("name_et"))


class IncomingIntakeForm(forms.Form):
    """Filing material that has just arrived.

    The file-first counterpart to `Uus teema`. Only the files are required:
    everything else can be filled in once the material is safely captured, and
    demanding a stage or an owner at the moment a ministry's PDF lands is how
    people go back to saving attachments on a desktop.

    Deliberately absent: any default Hetkeseis. A file arriving means something
    was received, not that the external process has reached a particular stage.
    """

    title = forms.CharField(
        label="Pealkiri",
        max_length=1000,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "field__input field__input--prominent",
                "placeholder": "Jäta tühjaks, et kasutada esimese faili nime",
            }
        ),
    )
    source_organisation = forms.ModelChoiceField(
        label="Saatja",
        queryset=Organisation.objects.none(),
        required=False,
        widget=SELECT_WIDGET,
    )
    received_date = forms.DateField(label="Saabus", required=False, widget=DATE_WIDGET)
    response_deadline = forms.DateField(
        label="Arvamuse tähtaeg", required=False, widget=DATE_WIDGET
    )
    owner = UserChoiceField(
        label="Vastutaja",
        queryset=User.objects.none(),
        required=False,
        widget=SELECT_WIDGET,
    )
    stage = forms.ModelChoiceField(
        label="Hetkeseis",
        queryset=StageVocabulary.objects.none(),
        required=False,
        widget=SELECT_WIDGET,
        help_text="Vabatahtlik. Faili saabumine ei ütle, kus väline menetlus on.",
    )
    track = forms.ChoiceField(
        label="Menetlusliik",
        choices=[("", "—"), *Track.choices],
        required=False,
        widget=SELECT_WIDGET,
    )
    visibility = forms.ChoiceField(
        label="Nähtavus",
        choices=Visibility.choices,
        initial=Visibility.NORMAL,
        widget=SELECT_WIDGET,
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        set_choices(self, "owner", User.objects.filter(is_active=True).order_by("display_name"))
        set_choices(self, "source_organisation", Organisation.objects.order_by("name"))
        set_choices(
            self,
            "stage",
            StageVocabulary.objects.filter(is_active=True).order_by("sort_order"),
        )
