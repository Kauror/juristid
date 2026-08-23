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
from django.utils import timezone

from app.accounts.models import User
from app.core.authorization import scoped_count
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.matters.entry_enums import EntryKind
from app.matters.enums import EngagementKind, MatterDataClass
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
    """Show what a colleague is called, not "Name (upn@example)".

    ``User.__str__`` includes the UPN because that is what makes a user
    unambiguous in the admin and in logs. In a list of half a dozen colleagues
    it is noise, and so is the surname: this department addresses each other by
    first name, and a row of chips reading *Ireen · Ann · Marko · Sandra* is
    read at a glance where full names are read one at a time.

    ``get_short_name`` rather than a split written here, because the User model
    already owns what a person is called informally and two copies of that rule
    are two places for it to drift. Falls back to the UPN for an account with
    no display name at all, exactly as before.
    """

    def label_from_instance(self, obj: Any) -> str:
        return obj.get_short_name() or obj.upn


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


def policy_areas_by_usage(viewer: Any) -> list[PolicyArea]:
    """Active policy areas, most-used first.

    Ordered by how often the department actually files under them, because a
    list ordered by an admin's `sort_order` puts Keskkond below something used
    twice in 2013 and makes people hunt.

    Counted only over Matters the *viewer* may read. An area's popularity is
    derived from records, and deriving it from records somebody cannot see would
    let the order of a checkbox list disclose that restricted work exists
    (Stage-2E.1 brief 19).
    """
    from app.matters.models import Matter

    usage = (
        Matter.objects.visible_to(viewer)
        .filter(policy_areas__isnull=False)
        # `order_by()` with no arguments, before the grouping. `Matter.Meta`
        # sets a default ordering, and Django adds ordering columns to the
        # GROUP BY — which silently turns one row per area into one row per
        # (area, created_at) and makes every count 1. It looks like the usage
        # data is missing rather than like a bug.
        .order_by()
        .values("policy_areas")
        # Distinct *Matters* per area. `Count("policy_areas")` counted the join
        # rows the visibility predicate produces, so an area used on files with
        # several collaborators floated to the top of the list for a specialist
        # and stayed put for the department head (app/core/authorization.py).
        .annotate(total=scoped_count())
    )
    counts = {row["policy_areas"]: row["total"] for row in usage}
    areas = list(PolicyArea.objects.filter(is_active=True))
    areas.sort(key=lambda area: (-counts.get(area.pk, 0), area.sort_order, area.name_et))
    return areas


def organisations_by_usage(viewer: Any, *, limit: int = 10) -> list[Organisation]:
    """The senders this department actually hears from, most frequent first.

    Same authorization reasoning as the policy areas, and the same refusal to
    hard-code: which ministry is most active changes with the government, and a
    list written into the source would be wrong within a year.
    """
    from app.matters.models import Matter

    usage = (
        Matter.objects.visible_to(viewer)
        .filter(source_organisations__isnull=False)
        # Cleared first, then re-ordered by the aggregate. The default ordering
        # would otherwise join the GROUP BY and give every organisation a count
        # of one (see `policy_areas_by_usage`).
        .order_by()
        .values("source_organisations")
        # Distinct *Matters* per organisation, which is what makes the plural
        # relation count correctly in both directions. A Matter sent by two
        # bodies contributes one to each of them, and a Matter with three
        # collaborators still contributes one — the sender join and the
        # visibility join both fan out, and `Count("source_organisations")`
        # would have counted the rows either of them produced.
        .annotate(total=scoped_count())
        .order_by("-total")[:limit]
    )
    ranking = {row["source_organisations"]: index for index, row in enumerate(usage)}
    if not ranking:
        return list(Organisation.objects.order_by("name")[:limit])
    found = Organisation.objects.filter(pk__in=ranking)
    return sorted(found, key=lambda organisation: ranking[organisation.pk])


class MatterCreateForm(forms.Form):
    """Creating a Teema requires a title and nothing else.

    Everything else is optional and disclosed under a details panel. Demanding
    metadata at capture time is precisely what makes people keep a spreadsheet
    open instead (master specification 3.8, 9.1).

    What changed in Stage 2E.1 is *which* optional fields are in front of you.
    A new matter arrives as a title, a file, a person, a sender and a date, and
    those now use visible controls rather than five dropdowns — for a department
    of four, a select is a click to find out what the options even are. The
    procedural metadata that used to sit beside them is still here, one
    disclosure down (brief 14, 15).
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
        # Radios, not checkboxes, and the styling makes them look like chips.
        # `Matter.owner` is one person; a control that lets you tick two would be
        # promising something the model cannot keep (brief 16).
        widget=forms.RadioSelect(attrs={"class": "choicecard__input"}),
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
    source_organisations = forms.ModelMultipleChoiceField(
        label="Saatja",
        queryset=Organisation.objects.none(),
        required=False,
        # Checkboxes, because a Matter really can arrive from several bodies at
        # once. This was radios while the model held one sender, and the control
        # was right for the model it had; both moved together (Agent-E brief 28).
        widget=forms.CheckboxSelectMultiple(attrs={"class": "checkitem__input"}),
        help_text="Kellelt teema tuli. Saatjaid võib olla mitu.",
    )
    #: The long tail. Shown only when the reader asks for it, and validated
    #: against the same queryset, so this is a second way to pick existing
    #: organisations rather than a way to invent one.
    #:
    #: A plain multiple select rather than a search widget: the reference table
    #: is small enough that a sized list is usable, and a JS dependency added
    #: for one disclosure is a dependency the whole product then carries
    #: (brief 30).
    source_organisations_other = forms.ModelMultipleChoiceField(
        label="Muu saatja",
        queryset=Organisation.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "field__input", "size": "8"}),
    )
    addressee_organisation = forms.ModelChoiceField(
        label="Adressaat",
        queryset=Organisation.objects.none(),
        required=False,
        widget=SELECT_WIDGET,
        help_text="Kellele Koda vastab. Eraldi fakt saatjast.",
    )
    received_date = forms.DateField(
        label="Saabus",
        required=False,
        widget=DATE_WIDGET,
        # Today, because that is when nearly everything arrives. `initial` only
        # ever fills an *unbound* form, so a POSTed value always wins and
        # nothing here can overwrite what somebody typed (brief 18).
        initial=timezone.localdate,
    )
    response_deadline = forms.DateField(
        label="Arvamuse tähtaeg", required=False, widget=DATE_WIDGET
    )
    policy_areas = forms.ModelMultipleChoiceField(
        label="Valdkonnad",
        queryset=PolicyArea.objects.none(),
        required=False,
        # Checkboxes because a Matter really can belong to several areas, and a
        # multi-select hides that behind a modifier key nobody uses (brief 19).
        widget=forms.CheckboxSelectMultiple(attrs={"class": "checkitem__input"}),
    )
    policy_area_other_selected = forms.BooleanField(
        label="Muu",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "checkitem__input"}),
    )
    policy_area_other = forms.CharField(
        label="Muu valdkond",
        max_length=400,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "field__input", "placeholder": "Millisesse valdkonda see kuulub?"}
        ),
        help_text="Vabatekst. Siit ei teki uut valdkonda ega silti.",
    )
    #: One checkbox, unticked, rather than a REAL/TEST select.
    #:
    #: Real work is the overwhelmingly normal case, and a required dropdown on
    #: every creation would put a decision in front of somebody who has none to
    #: make — the shape of control people learn to click past without reading.
    #: The presentation is a boolean; the *stored* value is still the two-value
    #: class, resolved by the `data_class` property below (Agent-C brief 15, 16).
    is_test_data = forms.BooleanField(
        label="Testandmed",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "checkitem__input"}),
        help_text="Arenduseks loodud teema; ei kuulu päris aruandlusse.",
    )

    #: `Nähtavus` is deliberately absent from this form.
    #:
    #: Restricting a Matter is a rare, deliberate act, and putting it on the
    #: creation screen made it a field to skim past. The model, the enum, the
    #: authorization and every existing restricted record are untouched; what is
    #: gone is the control. New Matters are NORMAL, decided server-side rather
    #: than inferred from a field somebody could omit (brief 21).

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}

        # The two sender controls are two ways into one set, so the canonical
        # answer is their union. Nothing is privileged for having come from the
        # frequent list, and an organisation ticked in both places appears once
        # (Agent-E brief 31).
        senders: dict[Any, Organisation] = {}
        for source in ("source_organisations", "source_organisations_other"):
            for organisation in cleaned.get(source) or []:
                senders.setdefault(organisation.pk, organisation)
        cleaned["source_organisations"] = sorted(senders.values(), key=lambda o: o.name)

        # Free text belongs to the checkbox that reveals it. Unticking "Muu"
        # and leaving the box full must not quietly save the text.
        if not cleaned.get("policy_area_other_selected"):
            cleaned["policy_area_other"] = ""
        cleaned["policy_area_other"] = (cleaned.get("policy_area_other") or "").strip()

        if cleaned.get("policy_area_other_selected") and not cleaned["policy_area_other"]:
            self.add_error("policy_area_other", "Kirjuta, millise valdkonnaga on tegemist.")

        return cleaned

    @property
    def data_class(self) -> str:
        """What the checkbox means in the vocabulary the model stores.

        The form parses and the service writes: this hands the service a value
        from `MatterDataClass`, and nothing here touches a model field
        (the convention this module opens with, Agent-C brief 16).
        """
        if self.cleaned_data.get("is_test_data"):
            return MatterDataClass.TEST
        return MatterDataClass.REAL

    def __init__(self, *args: Any, viewer: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.viewer = viewer

        set_choices(self, "owner", active_users())
        set_choices(self, "stage", active_stages())
        set_choices(self, "addressee_organisation", Organisation.objects.order_by("name"))

        # Every organisation is a *valid* sender; only the frequent ones are
        # offered as chips. Validation therefore runs against the full set —
        # narrowing it to the visible ten would reject a correct answer given
        # through the search control.
        everything = Organisation.objects.order_by("name")
        set_choices(self, "source_organisations", everything)
        set_choices(self, "source_organisations_other", everything)

        set_choices(
            self,
            "policy_areas",
            PolicyArea.objects.filter(is_active=True).order_by("sort_order", "name_et"),
        )

        # Ordering is a presentation concern, so it is applied to the rendered
        # choices rather than to the validating queryset.
        if viewer is not None:
            # `fields[...]` is typed as the base Field, which has no `choices`.
            # These two are ChoiceFields by construction a few lines above.
            areas = cast(Any, self.fields["policy_areas"])
            senders = cast(Any, self.fields["source_organisations"])
            areas.choices = [(area.pk, area.name_et) for area in policy_areas_by_usage(viewer)]
            self.frequent_senders = organisations_by_usage(viewer)
            senders.choices = [
                (organisation.pk, organisation.name) for organisation in self.frequent_senders
            ]
        else:
            self.frequent_senders = []


class NextActionForm(forms.Form):
    """`Järgmiseks`, including what its date actually means.

    `use_required_attribute` is off, and that is not cosmetic. On Uus teema this
    form is rendered inside a *closed* `<details>`, and setting a next action is
    optional — the view only validates it when somebody typed something. With
    the HTML `required` attribute present, the browser refuses to submit a form
    containing an invalid control it cannot scroll to, reports nothing, and the
    "Loo teema" button silently does nothing. Creating a Matter without a next
    action was impossible in a browser and fine in every test that posted
    directly (Stage-2E.1 brief 26).

    The server-side requirement is unchanged: `text` is still required, and a
    partially filled next action is still refused.
    """

    use_required_attribute = False

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
    # Plural, and a multiple field even though the surface it posts from is a
    # checkbox list: an inline edit of the sender set replaces the whole set, so
    # an empty POST is how somebody clears it rather than a validation error
    # (Agent-E brief 34).
    source_organisations = forms.ModelMultipleChoiceField(
        queryset=Organisation.objects.none(), required=False
    )
    addressee_organisation = forms.ModelChoiceField(
        queryset=Organisation.objects.none(), required=False
    )
    received_date = forms.DateField(required=False)
    response_deadline = forms.DateField(required=False)
    visibility = forms.ChoiceField(choices=Visibility.choices, required=False)
    # Editable after creation like every other fact on the record. Blank is a
    # legitimate value here — it is how somebody clears a note that turned out
    # to belong under a real PolicyArea after all (Stage-2E.1 brief 20).
    policy_area_other = forms.CharField(max_length=400, required=False)
    # `required=False` like every other field on this form: one POST carries
    # one field, so demanding this one would refuse every other inline edit.
    # An absent or empty value is still refused — by the service, which knows
    # no blank data class — rather than being defaulted to REAL, because a
    # malformed POST must not quietly reclassify a development record as
    # business data.
    data_class = forms.ChoiceField(choices=MatterDataClass.choices, required=False)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        set_choices(self, "owner", active_users())
        set_choices(self, "stage", active_stages())
        set_choices(self, "source_organisations", Organisation.objects.order_by("name"))
        set_choices(self, "addressee_organisation", Organisation.objects.order_by("name"))


class EngagementForm(forms.Form):
    """`Kaasamine` — five fields, four of them optional.

    Required: the channel and a human-readable title. Everything else is
    optional because the commonest real record is incomplete: a mailing with no
    durable link, a consultation somebody is entering months later without the
    exact date to hand. A form that demanded them would simply not be used
    (Agent-F brief 39).
    """

    kind = forms.ChoiceField(
        label="Liik",
        choices=EngagementKind.choices,
        initial=EngagementKind.WEB_CALL,
        widget=SELECT_WIDGET,
    )
    title = forms.CharField(
        label="Pealkiri",
        max_length=500,
        widget=forms.TextInput(
            attrs={"class": "field__input", "placeholder": "Näiteks: Liikmete tagasiside küsimine"}
        ),
    )
    url = forms.CharField(
        label="Link",
        required=False,
        widget=forms.TextInput(attrs={"class": "field__input", "placeholder": "https://…"}),
        help_text="Vabatahtlik. Kampaanial ei pruugi püsivat avalikku aadressi olla.",
    )
    #: No `initial`. The record may be about a consultation from 2019, and a
    #: date box pre-filled with today is answered by pressing save (brief 38).
    occurred_on = forms.DateField(label="Kuupäev", required=False, widget=DATE_WIDGET)
    note = forms.CharField(
        label="Märkus",
        required=False,
        widget=forms.Textarea(attrs={"class": "field__input", "rows": "2"}),
    )

    def clean_url(self) -> str:
        """The same rule the service enforces, reported where somebody typed it.

        Duplicated deliberately: the service is what guarantees the invariant
        for an importer or a shell, and this is what turns a refusal into a
        message beside the field instead of a 400 page.
        """
        from app.matters.services import normalize_engagement_url

        try:
            return normalize_engagement_url(self.cleaned_data.get("url"))
        except DomainError as error:
            raise forms.ValidationError(str(error)) from error

    def clean_title(self) -> str:
        title = (self.cleaned_data.get("title") or "").strip()
        if not title:
            raise forms.ValidationError("Kaasamisel peab olema pealkiri.")
        return title


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
    source_organisations = forms.ModelMultipleChoiceField(
        label="Saatja",
        queryset=Organisation.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "field__input", "size": "6"}),
        help_text="Saatjaid võib olla mitu.",
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
        set_choices(self, "source_organisations", Organisation.objects.order_by("name"))
        set_choices(
            self,
            "stage",
            StageVocabulary.objects.filter(is_active=True).order_by("sort_order"),
        )
