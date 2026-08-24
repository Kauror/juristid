"""Forms parse and validate input. They never change state.

Every form here hands its cleaned values to a service function in
``app.matters.services`` or ``app.workflow.services``. Nothing in this module
writes a model field, so the audit trail and the invariants cannot be bypassed
by adding another view (master specification 12.4, 23.4).
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, cast

from django import forms
from django.db.models import QuerySet
from django.utils import timezone

from app.accounts.models import User
from app.core.authorization import scoped_count
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.core.richtext import plain_text
from app.core.widgets import EstonianDateField, EstonianDateInput
from app.documents.enums import DocumentRole
from app.documents.models import Document, DocumentVersion
from app.matters.entry_enums import EntryKind
from app.matters.enums import EngagementKind, MatterDataClass
from app.matters.models import Matter
from app.organisations.models import Organisation
from app.taxonomy.models import PolicyArea, Tag
from app.taxonomy.vocabulary import selectable_policy_areas
from app.workflow.dates import MAX_YEAR, MIN_YEAR, InvalidPeriod, bounds_for
from app.workflow.enums import (
    ESTONIAN_MONTHS,
    ROMAN_QUARTERS,
    ActionKind,
    DatePrecision,
    DateSemantics,
    Disposition,
    Track,
    default_date_semantics,
)
from app.workflow.models import StageVocabulary


def _entry_moment(value: date | None) -> datetime | None:
    """When an entry happened, given the day somebody chose.

    Today means *now*. The box is pre-filled with today, so leaving it alone is
    the ordinary case, and turning that into midnight would stamp 00:00 on
    something written at half past two — a small untruth on every routine save.
    Passing ``None`` lets `add_entry` record the actual moment.

    Any other day is that day, at its start. Somebody writing up Friday's
    meeting on Monday knows the day and not the hour, and the chronology sorts
    by day with a deterministic tie-break behind it (app/matters/models.py).
    """
    if value is None or value == timezone.localdate():
        return None
    return _as_datetime(value)


def _as_datetime(value: date | None) -> datetime | None:
    """A chosen day, as the aware midnight the submission stores.

    `Submission.sent_at` is a moment, and a person recording that an opinion
    went out on the 12th knows the day and not the hour. Midnight in the
    department's own timezone is the honest reading of that day; using
    `timezone.now()` instead would silently stamp today onto a letter sent last
    month.
    """
    if value is None:
        return None
    return timezone.make_aware(datetime.combine(value, time.min))


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


#: One date control for the whole application. `type="text"`, because a native
#: date input renders in the *browser's* locale and showed `mm/dd/yyyy` on an
#: otherwise Estonian form (app/core/widgets.py).
DATE_WIDGET = EstonianDateInput()
TEXT_WIDGET = forms.TextInput(attrs={"class": "field__input"})
SELECT_WIDGET = forms.Select(attrs={"class": "field__input"})


def active_users() -> Any:
    return User.objects.filter(is_active=True).order_by("display_name")


def active_stages() -> Any:
    return StageVocabulary.objects.filter(is_active=True).order_by("sort_order", "label_et")


def offered_policy_areas() -> list[PolicyArea]:
    """The Valdkonnad a person may choose, in the department's reviewed order.

    One line, because the decision is not this function's to make: the governed
    vocabulary is `app.taxonomy.vocabulary.selectable_policy_areas` and every
    surface that offers a choice reads it, so Uus teema, the Teema header, the
    register filter and the reporting filters cannot drift apart
    (Teema redesign §7.1).

    It replaces an ordering by usage frequency. That existed because nine broad
    headings sorted by an admin's `sort_order` made people hunt; with the
    twenty-three working labels the department itself sequenced, a stable order
    is learnable and a self-rearranging one is not.
    """
    return list(selectable_policy_areas())


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
        #
        # Deliberately without `blank=True`, unlike `stage` below, and therefore
        # with no *Määramata* chip: a chosen owner cannot be un-chosen on this
        # form without reloading it. That is how the control has behaved since
        # Stage 2E.1 and is left alone here rather than redesigned in a round
        # about other things — but it is the same gap `stage` had, so if anybody
        # is asked to fix it, this is the line (Agent-UI brief 5.1).
        widget=forms.RadioSelect(attrs={"class": "choicecard__input"}),
    )
    #: Radios, rendered as chips. Both fields hold exactly one value, and a
    #: control that let you tick two would promise something the model cannot
    #: keep — the same rule that keeps Vastutaja radios and Valdkonnad
    #: checkboxes (brief 16, Agent-UI brief 5.1).
    #:
    #: Visible rather than collapsed because eleven stages and seven tracks fit
    #: on two lines each, and for a department of four a select is a click spent
    #: finding out what the options are. If either vocabulary grows past what
    #: reads at a glance, a select is the better control again and this should
    #: go back to one.
    stage = forms.ModelChoiceField(
        label="Hetkeseis",
        queryset=StageVocabulary.objects.none(),
        required=False,
        # The blank option is named rather than left as Django's row of dashes:
        # "not decided yet" is a real answer here and should read like one.
        empty_label="Määramata",
        # `blank=True` is what makes that label survive. Django drops the empty
        # choice entirely for a `ModelChoiceField` rendered as radios unless it
        # is set (django/forms/models.py, `ModelChoiceField.__init__`) — so
        # without it the row had no Määramata chip at all, and a stage picked by
        # mistake could not be unpicked. Caught by CI, not by reading.
        blank=True,
        widget=forms.RadioSelect(attrs={"class": "choicecard__input"}),
    )
    track = forms.ChoiceField(
        label="Menetlusliik",
        choices=[("", "Määramata"), *Track.choices],
        required=False,
        widget=forms.RadioSelect(attrs={"class": "choicecard__input"}),
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
    #: Two things changed here. The rendered choices now *exclude* the frequent
    #: chips above, because a disclosure headed "Muu saatja" that reopened the
    #: same ten bodies read as a second, contradictory sender control — the
    #: screenshot complaint this addresses. And it is checkboxes rather than an
    #: eight-row multiple select, so ticking two does not depend on knowing to
    #: hold Ctrl (Agent-UI brief 6.1).
    #:
    #: The queryset stays the whole catalogue. Validation must accept an
    #: organisation this reader's frequent list happens to contain, or a POST
    #: from a colleague with a different history would be refused as invalid.
    source_organisations_other = forms.ModelMultipleChoiceField(
        label="Muu saatja",
        queryset=Organisation.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "checkitem__input"}),
    )
    addressee_organisation = forms.ModelChoiceField(
        label="Adressaat",
        queryset=Organisation.objects.none(),
        required=False,
        widget=SELECT_WIDGET,
        help_text="Kellele Koda vastab. Eraldi fakt saatjast.",
    )
    received_date = EstonianDateField(
        label="Saabus",
        required=False,
        widget=DATE_WIDGET,
        # Today, because that is when nearly everything arrives. `initial` only
        # ever fills an *unbound* form, so a POSTed value always wins and
        # nothing here can overwrite what somebody typed (brief 18).
        initial=timezone.localdate,
    )
    response_deadline = EstonianDateField(
        label="Arvamuse tähtaeg",
        required=False,
        widget=DATE_WIDGET,
        initial=timezone.localdate,
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

        set_choices(self, "policy_areas", selectable_policy_areas())

        # Ordering is a presentation concern, so it is applied to the rendered
        # choices rather than to the validating queryset.
        if viewer is not None:
            # `fields[...]` is typed as the base Field, which has no `choices`.
            # These two are ChoiceFields by construction a few lines above.
            senders = cast(Any, self.fields["source_organisations"])
            self.frequent_senders = organisations_by_usage(viewer)
            senders.choices = [
                (organisation.pk, organisation.name) for organisation in self.frequent_senders
            ]
            # The disclosure holds what the chips do not. Offering the same ten
            # bodies twice is what made "Muu / lisa saatja" read as a second
            # sender control that contradicted the first — and it is why nobody
            # could find the body that genuinely was not on the list.
            frequent = {organisation.pk for organisation in self.frequent_senders}
            rest = cast(Any, self.fields["source_organisations_other"])
            rest.choices = [
                (organisation.pk, organisation.name)
                for organisation in Organisation.objects.order_by("name")
                if organisation.pk not in frequent
            ]
        else:
            self.frequent_senders = []


class MatterEditForm(forms.Form):
    """`Muuda teemat` — the whole record on one page.

    The redesign replaced the edit page with inline controls in the header and
    the rail, on the argument that changing an owner should not mean
    re-submitting every other value. That argument still holds for changing one
    field, and the inline controls stay.

    It does not hold for the case hands-on QA found: correcting a Matter that
    was filed wrongly. Then somebody is looking at five wrong facts at once, and
    clicking five separate controls in two different regions of the page — each
    with its own save, each re-rendering something — is not five small edits. It
    is one job the page refused to admit was one job. This form is that job:
    read the record, fix it, save once.

    **Only the fields a person may decide.** Deliberately absent: the Matter's
    reference and register identity, its origin, the imported source reference,
    every provenance and audit field, and the data class. Some of those are
    immutable facts about where the record came from; the rest have their own
    deliberate surface. A field is not editable here merely because the column
    exists (Teema QA §2.2).

    **Nothing here writes.** Each value goes to the named service that already
    owns it — `set_matter_title`, `set_brief_summary`, `assign_matter`,
    `set_policy_areas`, `set_organisations`, `set_matter_dates`, `set_tags` and
    the rest — so one page cannot become a second way to change a Matter that
    the audit trail does not know about (this module's opening rule).
    """

    title = forms.CharField(
        label="Pealkiri",
        max_length=1000,
        widget=forms.TextInput(attrs={"class": "field__input field__input--prominent"}),
    )
    brief_summary = forms.CharField(
        label="Lühikokkuvõte",
        required=False,
        widget=forms.Textarea(attrs={"class": "field__input", "rows": "3"}),
        help_text="Mida see teema puudutatud ettevõtete jaoks tähendab.",
    )
    owner = UserChoiceField(
        label="Vastutaja",
        queryset=User.objects.none(),
        required=False,
        widget=SELECT_WIDGET,
        # Named, and offered: unlike Uus teema, an edit page must be able to
        # take an owner *off* a Matter. That is the whole reason somebody opens
        # it — to correct what is on the record (Agent-UI brief 5.1).
        empty_label="Määramata",
    )
    stage = forms.ModelChoiceField(
        label="Hetkeseis",
        queryset=StageVocabulary.objects.none(),
        required=False,
        empty_label="Määramata",
        widget=SELECT_WIDGET,
    )
    track = forms.ChoiceField(
        label="Menetlusliik",
        choices=[("", "Määramata"), *Track.choices],
        required=False,
        widget=SELECT_WIDGET,
    )
    policy_areas = forms.ModelMultipleChoiceField(
        label="Valdkonnad",
        queryset=PolicyArea.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "checkitem__input"}),
    )
    policy_area_other = forms.CharField(
        label="Muu valdkond",
        max_length=400,
        required=False,
        widget=TEXT_WIDGET,
        help_text="Vabatekst. Siit ei teki uut valdkonda ega silti.",
    )
    source_organisations = forms.ModelMultipleChoiceField(
        label="Kellelt",
        queryset=Organisation.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "field__input", "size": "8"}),
        help_text="Kellelt teema tuli. Saatjaid võib olla mitu.",
    )
    addressee_organisation = forms.ModelChoiceField(
        label="Kellele",
        queryset=Organisation.objects.none(),
        required=False,
        empty_label="Määramata",
        widget=SELECT_WIDGET,
        help_text="Kellele Koda vastab. Eraldi fakt saatjast.",
    )
    #: No `initial=timezone.localdate` on either date, unlike every other date
    #: box in the product. This form is always opened on a Matter that already
    #: exists and its `initial` dict carries that Matter's real values, so a
    #: field-level default would only ever apply where a date is genuinely
    #: empty — and there, pre-filling today would invent a fact nobody stated
    #: (Teema QA §5.2).
    received_date = EstonianDateField(label="Saabus", required=False, widget=DATE_WIDGET)
    response_deadline = EstonianDateField(
        label="Arvamuse tähtaeg", required=False, widget=DATE_WIDGET
    )
    tags = forms.ModelMultipleChoiceField(
        label="Sildid",
        queryset=Tag.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "checkitem__input"}),
    )
    visibility = forms.ChoiceField(
        label="Nähtavus",
        choices=Visibility.choices,
        required=False,
        widget=SELECT_WIDGET,
        help_text="Piiratud teemat näevad ainult vastutaja ja osalejad.",
    )

    def __init__(self, *args: Any, matter: Matter | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.matter = matter

        set_choices(self, "owner", active_users())
        set_choices(self, "stage", active_stages())
        organisations = Organisation.objects.order_by("name")
        set_choices(self, "source_organisations", organisations)
        set_choices(self, "addressee_organisation", organisations)
        set_choices(self, "tags", Tag.objects.filter(is_active=True).order_by("name_et"))

        # Validation accepts the whole vocabulary; only the *offered* list is
        # narrowed. A Matter filed years ago under a since-retired area keeps
        # it, and correcting this Matter's title must not silently drop its
        # filing — which is exactly what a queryset limited to the current 23
        # would do on save (Teema redesign §7.2).
        set_choices(self, "policy_areas", PolicyArea.objects.all())
        offered = list(offered_policy_areas())
        if matter is not None:
            known = {area.pk for area in offered}
            offered += [area for area in matter.policy_areas.all() if area.pk not in known]
        areas = cast(Any, self.fields["policy_areas"])
        areas.choices = [(area.pk, area.name_et) for area in offered]
        #: The retired areas this Matter carries. The template says so rather
        #: than showing a ticked box that looks like every other one.
        self.retired_area_ids = {
            area.pk for area in offered if not getattr(area, "is_active", True)
        }

    def clean_title(self) -> str:
        value = (self.cleaned_data.get("title") or "").strip()
        if not value:
            raise forms.ValidationError("Teemal peab olema pealkiri.")
        return value

    def clean_visibility(self) -> str:
        # Blank is not a value here. An unrecognised POST must not quietly
        # un-restrict a Matter somebody deliberately restricted.
        value = self.cleaned_data.get("visibility") or ""
        if not value and self.matter is not None:
            return self.matter.visibility
        return value or Visibility.NORMAL


def edit_initial(matter: Matter) -> dict[str, Any]:
    """The Matter's current values, in the shape `MatterEditForm` reads."""
    return {
        "title": matter.title,
        "brief_summary": matter.brief_summary,
        "owner": matter.owner_id,
        "stage": matter.stage_id,
        "track": matter.track,
        "policy_areas": [area.pk for area in matter.policy_areas.all()],
        "policy_area_other": matter.policy_area_other,
        "source_organisations": [
            organisation.pk for organisation in matter.source_organisations.all()
        ],
        "addressee_organisation": matter.addressee_organisation_id,
        "received_date": matter.received_date,
        "response_deadline": matter.response_deadline,
        "tags": [tag.pk for tag in matter.tags.all()],
        "visibility": matter.visibility,
    }


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
        # "Järgmiseks" is what the column is called; it is not a question a
        # lawyer can answer without being told what belongs in it.
        label="Mida järgmisena teed või ootad?",
        max_length=2000,
        widget=forms.TextInput(
            attrs={
                "class": "field__input",
                "placeholder": "Näiteks: uurin 7. septembril ministeeriumilt kohtumise kohta",
            }
        ),
    )
    #: Radios rather than a select, and the enum's own labels: Teen, Ootan,
    #: Jälgin already read as Estonian a lawyer uses. What the template adds is
    #: a line under each saying what it means, which is where the distinction
    #: between "I have to do something" and "I am waiting on someone" actually
    #: gets made. The stored values are unchanged — DO, WAIT, MONITOR
    #: (master specification 11.2).
    kind = forms.ChoiceField(
        label="Mis laadi samm see on?",
        choices=ActionKind.choices,
        initial=ActionKind.DO,
        widget=forms.RadioSelect(attrs={"class": "choicecard__input"}),
    )
    #: Optional, and normally derived. "Kuupäeva tähendus" is a question about
    #: the data model, and a required dropdown asking it was in front of every
    #: lawyer setting a next step.
    #:
    #: It is *not* deleted, because the model genuinely permits more than one
    #: meaning per kind and the register's parser uses that — a DO whose source
    #: names a vague month is DO + EXPECTED_AROUND, not a deadline. Left alone
    #: it derives from the kind; the explicit choice is one disclosure away
    #: (app/workflow/enums.py, Agent-UI brief 9.4).
    date_semantics = forms.ChoiceField(
        label="Mida kuupäev täpselt tähendab",
        choices=DateSemantics.choices,
        required=False,
        widget=SELECT_WIDGET,
    )
    #: Today, because that is the answer nearly every time and nobody should have
    #: to type it. `initial` only ever fills an *unbound* form, so a POSTed value
    #: always wins, a validation error keeps what was typed, and a date somebody
    #: deliberately cleared stays cleared (Teema QA §5.2).
    target_date = EstonianDateField(
        label="Kuupäev", required=False, widget=DATE_WIDGET, initial=timezone.localdate
    )
    responsible = UserChoiceField(
        label="Kes selle eest vastutab?",
        queryset=User.objects.none(),
        required=False,
        widget=SELECT_WIDGET,
        # The service already falls back to the Matter's owner. Saying so turns
        # a blank select from "I must choose again" into "this is already
        # right" (app/workflow/services.py, `set_next_action`).
        help_text="Tühjaks jättes vastutab teema vastutaja.",
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        set_choices(self, "responsible", active_users())

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        kind = cleaned.get("kind")
        # Derived here rather than in the template, so a POST from anywhere —
        # a browser with the disclosure closed, a test, an integration — stores
        # the same canonical value.
        semantics = cleaned.get("date_semantics") or default_date_semantics(kind or "")
        cleaned["date_semantics"] = semantics
        target = cleaned.get("target_date")

        # A deadline with no date cannot be overdue, cannot be planned against
        # and cannot be reported on. It is the one combination worth refusing.
        if kind == ActionKind.DO and semantics == DateSemantics.DEADLINE and target is None:
            self.add_error("target_date", "Tähtajaline tegevus vajab kuupäeva.")
        return cleaned

    def as_service_kwargs(self, *, default_responsible: Any = None) -> dict[str, Any]:
        """What ``set_next_action`` needs.

        ``default_responsible`` is for the one caller that has an owner the
        service cannot see yet: Uus teema chooses the Matter's owner on the same
        form as the next action, and the Matter does not exist when this is
        read. Everywhere else the service's own fallback to ``matter.owner``
        already does this, and passing nothing keeps that behaviour exactly.
        """
        return {
            "text": self.cleaned_data["text"],
            "kind": self.cleaned_data["kind"],
            "date_semantics": self.cleaned_data["date_semantics"],
            "target_date": self.cleaned_data.get("target_date"),
            "date_precision": self.cleaned_data.get("date_precision") or DatePrecision.EXACT,
            "responsible": self.cleaned_data.get("responsible") or default_responsible,
        }


#: The Valdkonnad-free part of the composer's period control, shared by the
#: next step and the important deadline. Both ask the same question — how
#: exactly is this date known — and both answer it with `app.workflow.dates`,
#: so a quarter typed into either normalises to the same anchor.
COMPOSER_PRECISION_CHOICES: tuple[tuple[str, str], ...] = (
    (DatePrecision.EXACT.value, "Täpne kuupäev"),
    (DatePrecision.MONTH.value, "Kuu täpsusega"),
    (DatePrecision.QUARTER.value, "Kvartali täpsusega"),
    (DatePrecision.HALF_YEAR.value, "Poolaasta täpsusega"),
    (DatePrecision.YEAR.value, "Aasta täpsusega"),
)

MONTH_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (str(number), name.capitalize()) for number, name in enumerate(ESTONIAN_MONTHS, start=1)
)
QUARTER_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (str(number), f"{numeral} kvartal") for number, numeral in enumerate(ROMAN_QUARTERS, start=1)
)
HALF_CHOICES: tuple[tuple[str, str], ...] = (("1", "I poolaasta"), ("2", "II poolaasta"))

#: What a person may choose when recording a new `Kaasamine`.
#:
#: Three, not four. `WEB_CALL` is still a valid stored value and every
#: historical row carrying it still reads correctly — nothing is renamed and no
#: record is rewritten — but it is not offered for new work, because the
#: department settled on these three words and a fourth option nobody picks is
#: a fourth way for two people to file the same thing differently
#: (Teema redesign §14).
ENGAGEMENT_CHOICES: tuple[tuple[str, str], ...] = (
    (EngagementKind.SURVEY.value, "Küsitlus"),
    (EngagementKind.EMAIL_CAMPAIGN.value, "Otsepostitus"),
    (EngagementKind.OTHER.value, "Muu"),
)

#: The closure reasons the composer offers, in the order they are read.
#:
#: A subset of `Disposition`, chosen so that every option is a sentence
#: somebody would actually say about a finished file. `DUPLICATE` is deliberately
#: absent: merging two records is data management and belongs to whoever is
#: cleaning up, not to the lawyer finishing the work (Teema redesign §15.1).
CLOSURE_CHOICES: tuple[tuple[str, str], ...] = (
    (Disposition.COMPLETED.value, "Jõustus või töö lõppes"),
    (Disposition.INITIATIVE_WITHDRAWN.value, "Eelnõu või algatus lõpetati"),
    (Disposition.RESPONSE_COMPLETE.value, "Vastus esitatud ja järeltegevus tehtud"),
    (Disposition.MONITORING_STOPPED.value, "Koda ei tegele edasi"),
    (Disposition.NO_POSITION_FORMED.value, "Seisukohta ei kujundatud"),
    (Disposition.SUPERSEDED.value, "Jätkub teise teema all"),
    (Disposition.OTHER.value, "Muu"),
)


def _period_anchor(form: forms.Form, prefix: str) -> tuple[date | None, date | None, str]:
    """Turn one prefixed precision group into an anchor, an end and a precision.

    The composer carries two of these groups at once — the next step's date and
    an important deadline's — so they cannot each be a `PeriodForm`. What they
    share instead is `app.workflow.dates.bounds_for`, which is the thing that
    actually matters: a quarter entered here and a quarter entered on the
    Olulised tähtajad form must produce the same stored anchor, or the same
    period would sort into two places (Stage-2G brief 49).
    """

    def value(name: str) -> int | None:
        raw = form.cleaned_data.get(f"{prefix}_{name}")
        return int(raw) if raw not in (None, "") else None

    precision = form.cleaned_data.get(f"{prefix}_precision") or DatePrecision.EXACT.value
    exact = form.cleaned_data.get(f"{prefix}_date")
    if precision == DatePrecision.EXACT.value and exact is None:
        return None, None, precision

    field_for_precision = {
        DatePrecision.EXACT.value: f"{prefix}_date",
        DatePrecision.MONTH.value: f"{prefix}_month",
        DatePrecision.QUARTER.value: f"{prefix}_quarter",
        DatePrecision.HALF_YEAR.value: f"{prefix}_half",
        DatePrecision.YEAR.value: f"{prefix}_year",
    }
    try:
        start, end = bounds_for(
            precision,
            exact_date=exact,
            year=value("year"),
            month=value("month"),
            quarter=value("quarter"),
            half=value("half"),
        )
    except InvalidPeriod as error:
        form.add_error(field_for_precision.get(precision, f"{prefix}_date"), str(error))
        return None, None, precision
    return start, end, precision


def _precision_fields(prefix: str, *, date_label: str) -> dict[str, forms.Field]:
    """One precision group, named for the thing it dates.

    Built rather than declared because the composer needs two identical groups
    under different prefixes, and copying twenty lines is how the second copy
    stops matching the first.
    """
    return {
        #: **No `initial`**, unlike almost every other date box in the product,
        #: and the exception is deliberate.
        #:
        #: A TEEN with no date is the one combination the domain refuses — a
        #: deadline that cannot be met, missed or planned against. Pre-filling
        #: today turns that refusal into a silent assertion that the work is due
        #: today, which is not what the person said and is worse than being
        #: asked. The browser lane caught it: the save that used to be refused
        #: started succeeding with a date nobody chose
        #: (e2e/test_lawyer_workflow.py).
        #:
        #: The same control serves `Oluline tähtaeg`, where today is rarely the
        #: answer either, and where the person may mean a quarter rather than a
        #: day. One control, one rule.
        f"{prefix}_date": EstonianDateField(
            label=date_label, required=False, widget=EstonianDateInput()
        ),
        f"{prefix}_precision": forms.ChoiceField(
            label="Täpsus",
            choices=COMPOSER_PRECISION_CHOICES,
            initial=DatePrecision.EXACT.value,
            required=False,
            widget=forms.RadioSelect(attrs={"class": "precision__radio"}),
        ),
        f"{prefix}_month": forms.ChoiceField(
            label="Kuu", choices=(("", "—"), *MONTH_CHOICES), required=False, widget=SELECT_WIDGET
        ),
        f"{prefix}_quarter": forms.ChoiceField(
            label="Kvartal",
            choices=(("", "—"), *QUARTER_CHOICES),
            required=False,
            widget=SELECT_WIDGET,
        ),
        f"{prefix}_half": forms.ChoiceField(
            label="Poolaasta",
            choices=(("", "—"), *HALF_CHOICES),
            required=False,
            widget=SELECT_WIDGET,
        ),
        f"{prefix}_year": forms.IntegerField(
            label="Aasta",
            required=False,
            min_value=MIN_YEAR,
            max_value=MAX_YEAR,
            widget=forms.NumberInput(attrs={"class": "field__input field__input--compact"}),
        ),
    }


class ComposerForm(forms.Form):
    """`TEGEVUSE KIRJELDUS` — one box, and everything else on demand.

    The redesign's central claim is that recording professional work is *one*
    act. A lawyer comes back from a meeting having agreed a deadline, promised
    an opinion and been handed a PDF; the system should take that in one
    sentence and one save, not as four forms on three screens.

    So this form is one required-ish textarea and four optional groups, each
    hidden behind a quiet control until somebody wants it: an attachment, an
    important deadline, the next step, and closing the file.

    **No consultation here.** `Kaasamine` has one entry point and it is the
    section below, which shows what is already on the file while you add to it.
    Two controls for one act is how the same consultation gets recorded twice.
    `compose_update` still accepts `engagement=`; this form no longer sends it.

    **There is no separate next-step text field, and that is the point.**
    "Kirjelda, mis tegid ja mida teed edasi" already contains the wording; a
    second box asking for it again is the duplicate data entry the whole
    product exists to remove. Choosing TEEN, OOTAN or JÄLGIN turns the
    description into the next step's text, verbatim — no NLP, no sentence
    splitting, no summarisation. What somebody wrote is what the register says
    (Teema redesign §9.1).

    **A mode is what decides whether a next step is written**, rather than a
    checkbox beside one. With no mode chosen, the save is an entry and nothing
    else.
    """

    use_required_attribute = False

    body = forms.CharField(
        label="Tegevuse kirjeldus",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "composer__body",
                "rows": "3",
                "placeholder": "Kirjelda, mis tegid ja mida teed edasi…",
                "data-richtext": "true",
            }
        ),
    )
    #: Kept, and kept quiet. The entry kind is a real distinction in the
    #: chronology — a meeting is not a note — but it is not a question worth
    #: asking before somebody has written anything, so it defaults to Märkus
    #: and lives inside the attachment/meta disclosure.
    kind = forms.ChoiceField(
        label="Liik",
        choices=EntryKind.choices,
        initial=EntryKind.NOTE,
        required=False,
        widget=SELECT_WIDGET,
    )
    #: An Estonian date box, like every other date in this application.
    #:
    #: It was a native `datetime-local`, which renders in the *browser's*
    #: locale: a lawyer on a US-English Windows saw `mm/dd/yyyy` on an
    #: otherwise Estonian form, with no way to know it would read 7.9.2026 as
    #: the 9th of July. That is the whole class of defect `app/core/dates.py`
    #: exists to prevent, and this control had been missed by it.
    #:
    #: A day rather than a minute. Somebody writing up Friday's meeting on
    #: Monday knows which day it was and does not know the hour, and the
    #: chronology sorts by day — `add_entry` stamps the current moment when
    #: this is left empty, which is the ordinary case.
    occurred_on = EstonianDateField(
        label="Toimus", required=False, widget=DATE_WIDGET, initial=timezone.localdate
    )
    organisation = forms.ModelChoiceField(
        label="Asutus", queryset=Organisation.objects.none(), required=False, widget=SELECT_WIDGET
    )

    # -- + Manus -----------------------------------------------------------
    attachment = forms.FileField(
        label="Manus",
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "field__input"}),
    )
    #: Asked with the file, never after it. A document filed as "Muu" because
    #: the form asked too late is a document nobody finds again
    #: (Teema redesign §23.5).
    attachment_role = forms.ChoiceField(
        label="Roll",
        choices=DocumentRole.choices,
        initial=DocumentRole.OTHER,
        required=False,
        widget=SELECT_WIDGET,
    )

    # -- MIS EDASI? --------------------------------------------------------
    #: Blank is a real answer: it means the next step is unchanged.
    next_kind = forms.ChoiceField(
        label="Mis edasi?",
        choices=(("", "Ei muuda"), *ActionKind.choices),
        required=False,
        widget=forms.RadioSelect(attrs={"class": "modechip__input"}),
    )
    #: The stored meaning, behind a disclosure and never required. It derives
    #: from the chosen mode when left alone, and the model genuinely permits
    #: pairs the derivation does not produce — the register's own parser records
    #: a DO whose source names a vague month as an expectation, not a deadline
    #: (app/workflow/enums.py, Teema redesign §9.3).
    next_date_semantics = forms.ChoiceField(
        label="Mida kuupäev täpselt tähendab",
        choices=(("", "Tuleneb valikust"), *DateSemantics.choices),
        required=False,
        widget=SELECT_WIDGET,
    )

    # -- + Oluline tähtaeg -------------------------------------------------
    deadline_title = forms.CharField(
        label="Mis on oodata",
        required=False,
        max_length=2000,
        widget=forms.TextInput(
            attrs={"class": "field__input", "placeholder": "Näiteks: eelnõu kooskõlastusring"}
        ),
    )

    # -- + Lõpeta teema ----------------------------------------------------
    close_matter = forms.BooleanField(label="Lõpeta teema", required=False)
    disposition = forms.ChoiceField(
        label="Põhjus",
        choices=CLOSURE_CHOICES,
        required=False,
        widget=SELECT_WIDGET,
    )
    closure_reason = forms.CharField(
        label="Tulemus",
        required=False,
        widget=forms.Textarea(
            attrs={"class": "field__input", "rows": "2", "placeholder": "Mis lõpuks juhtus?"}
        ),
    )
    successor = forms.ModelChoiceField(
        label="Järglane",
        queryset=Matter.objects.none(),
        required=False,
        widget=SELECT_WIDGET,
    )
    #: The final opinion is a canonical Submission or it is nothing. This picks
    #: the exact evidence that went out from the files already on the Matter;
    #: a submission cannot be marked sent without one (Teema redesign §17).
    final_version = forms.ModelChoiceField(
        label="Lõpparvamuse fail",
        queryset=DocumentVersion.objects.none(),
        required=False,
        widget=SELECT_WIDGET,
    )
    final_title = forms.CharField(
        label="Lõpparvamuse pealkiri",
        required=False,
        max_length=400,
        widget=forms.TextInput(attrs={"class": "field__input"}),
    )
    #: No `initial`, for the same reason the period control has none: `clean`
    #: reads this field's emptiness. A title, a recipient or a date with no
    #: chosen file is refused as an opinion claimed without its evidence — so a
    #: default here refuses every ordinary closure that is not also recording a
    #: sent opinion, which is most of them (Teema redesign §17, §20).
    final_sent_on = EstonianDateField(
        label="Saatmise kuupäev", required=False, widget=EstonianDateInput()
    )
    final_recipients = forms.ModelMultipleChoiceField(
        label="Saaja",
        queryset=Organisation.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "checkitem__input"}),
    )
    final_channel = forms.CharField(
        label="Kanal",
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={"class": "field__input", "placeholder": "Näiteks: EIS"}),
    )
    final_reference = forms.CharField(
        label="Viide",
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={"class": "field__input", "placeholder": "Toimiku number"}),
    )
    victory_title = forms.CharField(
        label="Töövõit",
        required=False,
        max_length=2000,
        widget=forms.TextInput(
            attrs={"class": "field__input", "placeholder": "Mida Koda saavutas?"}
        ),
    )
    victory_detail = forms.CharField(
        label="Töövõidu selgitus",
        required=False,
        widget=forms.Textarea(attrs={"class": "field__input", "rows": "2"}),
    )

    def __init__(self, *args: Any, matter: Any = None, viewer: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for prefix, label in (("next", "Kuupäev"), ("deadline", "Kuupäev")):
            self.fields.update(_precision_fields(prefix, date_label=label))
        set_choices(self, "organisation", Organisation.objects.order_by("name"))
        set_choices(self, "final_recipients", Organisation.objects.order_by("name"))
        self.matter = matter
        if matter is None:
            return

        # Both querysets go through `visible_to`, and that is a security
        # boundary rather than a convenience.
        #
        # A crafted POST naming a Matter this person may not see would tell them
        # a restricted file with that id exists — the same disclosure
        # `get_visible_matter` returns 404 to avoid. And binding a document they
        # may not read as a submission's final evidence would then print its
        # filename, its size and its SHA-256 to everybody who can see the
        # submission: exactly the defect fixed once already in
        # `app.submissions.views.attach_evidence`, which is why the version
        # queryset filters through `Document.objects.visible_to` rather than on
        # `document__matter` alone. A child override only ever restricts
        # further, so the Matter-only filter is not the same question.
        set_choices(self, "successor", Matter.objects.visible_to(viewer).exclude(pk=matter.pk))
        set_choices(
            self,
            "final_version",
            DocumentVersion.objects.filter(
                document__in=Document.objects.visible_to(viewer).filter(matter=matter)
            ).select_related("document"),
        )

    # -- validation --------------------------------------------------------

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        body = (cleaned.get("body") or "").strip()
        mode = cleaned.get("next_kind") or ""
        wants_deadline = bool((cleaned.get("deadline_title") or "").strip())
        wants_closure = bool(cleaned.get("close_matter"))
        has_file = bool(cleaned.get("attachment"))

        if not (body or mode or wants_deadline or wants_closure or has_file):
            raise forms.ValidationError("Kirjelda tegevust või vali, mida veel salvestada.")

        self._clean_next_action(cleaned, body=body, mode=mode)
        self._clean_deadline(cleaned, wanted=wants_deadline)
        self._clean_closure(cleaned, wanted=wants_closure)
        return cleaned

    def _clean_next_action(self, cleaned: dict[str, Any], *, body: str, mode: str) -> None:
        if not mode:
            cleaned["next_action_kwargs"] = None
            return

        # The wording comes from the description, exactly as it was typed. The
        # entry stores sanitised HTML and a next action is a plain sentence, so
        # the tags come off and nothing else does.
        text = plain_text(body).strip()
        if not text:
            self.add_error(
                "body",
                "Kirjelda, mida teed või ootad — sellest saab järgmise sammu sõnastus.",
            )
            return

        anchor, _end, precision = _period_anchor(self, "next")
        semantics = cleaned.get("next_date_semantics") or default_date_semantics(mode)
        if mode == ActionKind.DO and semantics == DateSemantics.DEADLINE and anchor is None:
            self.add_error("next_date", "Tähtajaline tegevus vajab kuupäeva.")
            return

        cleaned["next_action_kwargs"] = {
            "text": text[:2000],
            "kind": mode,
            "date_semantics": semantics,
            "target_date": anchor,
            "date_precision": precision,
        }

    def _clean_deadline(self, cleaned: dict[str, Any], *, wanted: bool) -> None:
        if not wanted:
            cleaned["important_date_kwargs"] = None
            return
        anchor, end, precision = _period_anchor(self, "deadline")
        if anchor is None or end is None:
            self.add_error("deadline_date", "Oluline tähtaeg vajab kuupäeva või perioodi.")
            return
        cleaned["important_date_kwargs"] = {
            "title": (cleaned.get("deadline_title") or "").strip(),
            "date_value": anchor,
            "period_end": end,
            "date_precision": precision,
        }

    def _clean_closure(self, cleaned: dict[str, Any], *, wanted: bool) -> None:
        if not wanted:
            cleaned["closure_kwargs"] = None
            return

        disposition = cleaned.get("disposition") or ""
        if not disposition:
            self.add_error("disposition", "Vali, miks teema lõpeb.")
            return

        successor = cleaned.get("successor")
        if disposition == Disposition.SUPERSEDED and successor is None:
            self.add_error("successor", "Vali teema, mille all töö jätkub.")
            return
        if disposition != Disposition.SUPERSEDED and successor is not None:
            self.add_error(
                "successor",
                "Järglase saab määrata ainult siis, kui töö jätkub teise teema all.",
            )
            return

        closure: dict[str, Any] = {
            "disposition": disposition,
            "reason": (cleaned.get("closure_reason") or "").strip(),
            "successor": successor,
        }

        version = cleaned.get("final_version")
        recipients = list(cleaned.get("final_recipients") or [])
        title = (cleaned.get("final_title") or "").strip()
        sent_on = cleaned.get("final_sent_on")
        # An opinion is claimed only when somebody chose the file that went out.
        # Everything else about it is then required, because a sent submission
        # with no recipient and no date is a claim the record cannot support —
        # and a PDF on its own is not a sent opinion (Teema redesign §17, §20).
        if version is not None:
            if not title:
                self.add_error("final_title", "Lõpparvamus vajab pealkirja.")
            if not recipients:
                self.add_error("final_recipients", "Märgi, kellele arvamus saadeti.")
            if sent_on is None:
                self.add_error("final_sent_on", "Märgi, millal arvamus saadeti.")
            if not self.errors:
                closure["final_opinion"] = {
                    "title": title,
                    "final_version": version,
                    "recipients": recipients,
                    "sent_at": _as_datetime(sent_on),
                    "channel": (cleaned.get("final_channel") or "").strip(),
                    "reference": (cleaned.get("final_reference") or "").strip(),
                }
        elif title or recipients or sent_on:
            self.add_error(
                "final_version",
                "Vali saadetud fail — ilma täpse tõendita ei saa arvamust saadetuks märkida.",
            )

        victory_title = (cleaned.get("victory_title") or "").strip()
        if victory_title:
            closure["work_victory"] = {
                "title": victory_title,
                "detail": (cleaned.get("victory_detail") or "").strip(),
            }

        cleaned["closure_kwargs"] = closure

    # -- what the service is called with -----------------------------------

    def as_service_kwargs(self) -> dict[str, Any]:
        """Everything :func:`app.matters.services.compose_update` needs."""
        return {
            "body": self.cleaned_data.get("body") or "",
            "kind": self.cleaned_data.get("kind") or EntryKind.NOTE,
            "occurred_at": _entry_moment(self.cleaned_data.get("occurred_on")),
            "organisation": self.cleaned_data.get("organisation"),
            "attachment": self.cleaned_data.get("attachment"),
            "attachment_role": self.cleaned_data.get("attachment_role") or DocumentRole.OTHER,
            "next_action": self.cleaned_data.get("next_action_kwargs"),
            "important_date": self.cleaned_data.get("important_date_kwargs"),
            "closure": self.cleaned_data.get("closure_kwargs"),
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
    # Estonian-reading, like every other date box. These post from the header's
    # inline edits, which submitted ISO from a native control and now submit
    # `7.9.2026` from a text one; ISO stays accepted so nothing that already
    # posts it breaks (app/core/dates.py).
    received_date = EstonianDateField(required=False)
    response_deadline = EstonianDateField(required=False)
    visibility = forms.ChoiceField(choices=Visibility.choices, required=False)
    # Editable after creation like every other fact on the record. Blank is a
    # legitimate value here — it is how somebody clears a note that turned out
    # to belong under a real PolicyArea after all (Stage-2E.1 brief 20).
    policy_area_other = forms.CharField(max_length=400, required=False)
    # The governed vocabulary, edited in the header where the value is shown.
    # A multiple field for the same reason `source_organisations` is one: the
    # control replaces the whole set, and an untouched checkbox posts nothing.
    policy_areas = forms.ModelMultipleChoiceField(
        queryset=PolicyArea.objects.none(), required=False
    )
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
        # The offered vocabulary *plus* whatever this Matter already carries.
        # Validation would otherwise refuse a save that merely left a retired
        # area ticked, which would make correcting one field on an old Matter
        # impossible without silently dropping its filing (Teema redesign §7.2).
        set_choices(self, "policy_areas", PolicyArea.objects.all())


class EngagementForm(forms.Form):
    """`Kaasamine` — five fields, four of them optional.

    Required: the channel and a human-readable title. Everything else is
    optional because the commonest real record is incomplete: a mailing with no
    durable link, a consultation somebody is entering months later without the
    exact date to hand. A form that demanded them would simply not be used
    (Agent-F brief 39).
    """

    #: The three approved options, not the whole enum. `WEB_CALL` stays a valid
    #: stored value and every historical row carrying it still reads correctly;
    #: it is simply not offered for new work (Teema redesign §14).
    kind = forms.ChoiceField(
        label="Liik",
        choices=ENGAGEMENT_CHOICES,
        initial=EngagementKind.SURVEY.value,
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
    #: Today. The original argument against it was that a record may be about a
    #: consultation from 2019 and a pre-filled box is answered by pressing save
    #: (Agent-F brief 38). Hands-on QA settled it the other way: the overwhelming
    #: case is recording something that just happened, and re-typing today's date
    #: every time is the friction people actually complained about. Backdating is
    #: one edit; typing today is every time.
    occurred_on = EstonianDateField(
        label="Kuupäev", required=False, widget=DATE_WIDGET, initial=timezone.localdate
    )
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


class BriefSummaryForm(forms.Form):
    """`Lühikokkuvõte`, edited where it is read.

    One field, no heading, no page. Blank is a legitimate submission: a summary
    somebody wrote before understanding the file is one they may want to remove
    rather than replace (Teema redesign §6, §26.1).
    """

    brief_summary = forms.CharField(
        label="Lühikokkuvõte",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "field__input inlineedit__area",
                "rows": "3",
                "placeholder": "Mida see teema ettevõtjatele tähendab? (2–3 lauset)",
            }
        ),
    )


class PersonalNoteForm(forms.Form):
    """`Märkmed` — the private scratch pad, autosaved.

    Plain text and nothing else: no sanitiser, no rich text, no formatting
    toolbar. It is never rendered as HTML, never indexed, never exported, and
    never shown to anybody but its author (Teema redesign §22.4).
    """

    body = forms.CharField(
        label="Märkmed",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "railnote__area",
                "rows": "6",
                "placeholder": "Vabad märkmed…",
            }
        ),
    )


class WorkingDocumentForm(forms.Form):
    """A living SharePoint file, referenced and never captured.

    Deliberately not an upload. What this records is *where the working file
    lives*, which is the opposite of evidence: the bytes keep changing, nobody
    checksums them, and presenting the link as proof of what Koda sent is the
    one confusion the documents workspace exists to prevent
    (master specification 8.6; Teema redesign §23.3).
    """

    title = forms.CharField(
        label="Nimi",
        max_length=400,
        widget=forms.TextInput(
            attrs={"class": "field__input", "placeholder": "Näiteks: Arvamuse töödokument.docx"}
        ),
    )
    web_url = forms.CharField(
        label="SharePointi aadress",
        max_length=1000,
        widget=forms.TextInput(
            attrs={"class": "field__input", "placeholder": "https://…sharepoint.com/…"}
        ),
    )
    site_path = forms.CharField(
        label="Asukoht",
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "field__input", "placeholder": "Näiteks: Õigusosakond / KMS 2026"}
        ),
        help_text="Valikuline. Aitab lugejal aru saada, kus fail SharePointis asub.",
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
    received_date = EstonianDateField(
        label="Saabus", required=False, widget=DATE_WIDGET, initial=timezone.localdate
    )
    response_deadline = EstonianDateField(
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
