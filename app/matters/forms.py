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
from app.accounts.selectors import assignable_business_users, assignable_including
from app.core.authorization import scoped_count
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.core.richtext import plain_text
from app.core.widgets import DescribedRadioSelect, EstonianDateField, EstonianDateInput
from app.documents.enums import DocumentRole
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
from app.workflow.selectors import selectable_stages, stage_help_texts


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


def assignable_users() -> Any:
    """Who a person may be handed work on this form.

    One line, for the reason `active_stages` and `offered_policy_areas` below
    are one line: the population is `app.accounts.selectors`, and a form that
    assembled its own would drift from the rule the persona list already obeys.

    It replaces an `is_active=True` filter over the whole user table. Being able
    to sign in is not the same thing as doing the department's work — the
    administrator account could not be *become*, but could still be handed a
    file — and the two answers are now the one answer (docs/adr/0036).

    For a form editing a record that already names somebody, use
    `assignable_including(...)` instead: this list is about new work, and an
    edit page must not refuse the owner already on the Matter.
    """
    return assignable_business_users()


def active_stages() -> Any:
    """The offered Hetkeseis vocabulary, from the module that governs it.

    One line, for the reason `offered_policy_areas` below is one line: the list
    and its order are `app.workflow.selectors.selectable_stages`, and a form
    that assembled its own would drift from the tooltip that explains it
    (Uus teema redesign §8).
    """
    return selectable_stages()


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


def addressee_name_field() -> forms.CharField:
    """`Adressaat`'s typed half, on both Teema forms.

    Written once and built twice, because `Uus teema` and `Muuda teemat` must
    offer the same control: a person filing a Teema and a person correcting one
    are answering the same question, and two spellings of one field is two
    workflows to learn (§7).

    Its own field, deliberately, rather than a `name` attribute bolted onto the
    long tail's search box. That box is a *filter* over choices already on the
    page: somebody who types «Kliima», watches the list narrow to
    `Kliimaministeerium` and clicks it has answered the question, and a control
    that also posted the four letters left in the box would file the Teema
    against a new institution called «Kliima». One control per intention —
    filter, or name a body that is not here — is what makes the difference
    between them decidable on the server (§10).

    Nothing is created here. `clean_addressee_name` trims and length-caps the
    text; `app.matters.services.resolve_addressee` decides what it means, and
    does so inside the save's own transaction (§5, §6).
    """
    return forms.CharField(
        label="Uus adressaat",
        # `Organisation.name` is 300, so a name this field accepted and the
        # service could not store is not a state either of them can reach.
        max_length=300,
        required=False,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "class": "field__input field__input--compact",
                "autocomplete": "off",
                "placeholder": "Kirjuta asutuse nimi",
            }
        ),
    )


def clean_addressee_name(value: str | None) -> str:
    """Collapse the typed institution name. Resolve nothing.

    Whitespace is collapsed the way `app.core.text.normalize_for_matching`
    collapses it, so «Majandus-  ja   Kommunikatsiooniministeerium» and the
    canonical spelling reach the resolver as one string and reuse one row.

    Whether that string names an institution that already exists, a new one, or
    two at once is a question about stored state, and a form does not touch
    stored state (§5).
    """
    return " ".join((value or "").split())


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


def addressees_by_usage(viewer: Any, *, limit: int = 10) -> list[Organisation]:
    """The bodies this department actually answers to, most frequent first.

    The addressee counterpart of `organisations_by_usage`, and separate from it
    on purpose: who *sends* Koda a file and who Koda *answers* are two different
    facts, and one list standing for both would put the Riigikogu committee that
    never sends anything behind ten ministries that never receive anything.

    `scoped_count` for the same reason the sender list uses it: the visibility
    join fans out over collaborators, and `Count("id")` inside a `GROUP BY`
    would count join rows (app/core/authorization.py).
    """
    from app.matters.models import Matter

    usage = (
        Matter.objects.visible_to(viewer)
        .filter(addressee_organisation__isnull=False)
        .order_by()
        .values("addressee_organisation")
        .annotate(total=scoped_count())
        .order_by("-total")[:limit]
    )
    ranking = {row["addressee_organisation"]: index for index, row in enumerate(usage)}
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
    of four, a select is a click to find out what the options even are.

    What changed with the approved Uus teema design is that nothing is behind a
    disclosure any more, and that the form finally carries the two texts a
    lawyer writes while the file is still in front of them: `Lühikokkuvõte` and
    the private `Märkmed`. The height that bought it came from pairing rows and
    from the chip control, not from hiding fields — every field on the page
    before this round is still on it, still posting the same name and the same
    value (Uus teema redesign §2, §3).
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
    #: The plain-language answer to *what is this*, written where it is first
    #: known. `Matter.brief_summary` and nothing else: `position_summary` says
    #: what Koda thinks, `rationale_summary` says why, and the first `Entry`
    #: says what happened on a day. None of the three can be made to mean this
    #: without corrupting it (app/matters/models.py, Teema redesign §6).
    #:
    #: Optional, like everything but the title. A summary written before the
    #: file has been read is worse than none.
    brief_summary = forms.CharField(
        label="Millest teema räägib",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "field__input field__input--prose",
                "rows": "3",
                "placeholder": "Mida see eelnõu muudab ja keda puudutab?",
            }
        ),
    )
    #: The private scratch pad, on the capture screen because that is where the
    #: half-formed thought occurs. Written to `MatterPersonalNote`, which is
    #: scoped by author and read by nobody else — not a second Matter column
    #: (app/matters/models.py, Teema redesign §22.4).
    #:
    #: No placeholder, deliberately: a prompt in a box nobody else will ever
    #: read is the page telling somebody what to think about privately.
    notes = forms.CharField(
        label="Märkmed",
        required=False,
        widget=forms.Textarea(
            attrs={"class": "railnote__area railnote__area--create", "rows": "3"}
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
        widget=forms.RadioSelect(attrs={"class": "chip__input"}),
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
        # The one control on the page whose options need explaining. Which of
        # `Kooskõlastusringil` and `Valitsuses` a file is in depends on an event
        # that has or has not happened, and the department wrote a sentence per
        # stage saying which. The sentence is on the row; this is what points a
        # screen reader at the one belonging to *this* chip
        # (app/workflow/selectors.py, Uus teema redesign §8).
        widget=DescribedRadioSelect(attrs={"class": "chip__input"}),
    )
    track = forms.ChoiceField(
        label="Menetlusliik",
        choices=[("", "Määramata"), *Track.choices],
        required=False,
        widget=forms.RadioSelect(attrs={"class": "chip__input"}),
    )
    source_organisations = forms.ModelMultipleChoiceField(
        label="Saatja",
        queryset=Organisation.objects.none(),
        required=False,
        # Checkboxes, because a Matter really can arrive from several bodies at
        # once. This was radios while the model held one sender, and the control
        # was right for the model it had; both moved together (Agent-E brief 28).
        widget=forms.CheckboxSelectMultiple(attrs={"class": "chip__input"}),
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
        widget=forms.CheckboxSelectMultiple(attrs={"class": "chip__input"}),
    )
    #: Still one organisation, and still a radio, because `Matter` holds one
    #: addressee. The approved design draws it as a multi-select mirroring what
    #: ADR 0025 did for senders — a file can be answered to a ministry and a
    #: committee at once — and offers the single-value chip group as the version
    #: to ship if the migration is not wanted yet. That is what this is: the
    #: layout is the design's, the cardinality is the model's, and the schema
    #: change is left as a decision rather than made as a side effect of a form
    #: redesign (Uus teema redesign §5, ADR 0032).
    addressee_organisation = forms.ModelChoiceField(
        label="Adressaat",
        queryset=Organisation.objects.none(),
        required=False,
        empty_label="Määramata",
        # `blank=True` is what keeps that label. Django drops the empty choice
        # for a `ModelChoiceField` rendered as radios unless it is set, and
        # without it an addressee picked by mistake could not be unpicked — the
        # defect CI caught on `stage` a round ago.
        blank=True,
        widget=forms.RadioSelect(attrs={"class": "chip__input"}),
    )
    addressee_name = addressee_name_field()
    received_date = EstonianDateField(
        label="Saabus",
        required=False,
        widget=DATE_WIDGET,
        # Today, because that is when nearly everything arrives. `initial` only
        # ever fills an *unbound* form, so a POSTed value always wins and
        # nothing here can overwrite what somebody typed (brief 18).
        initial=timezone.localdate,
    )
    #: Deliberately no `initial`, unlike Saabus directly above it.
    #:
    #: The two dates are not the same kind of fact. Saabus is an *observation* —
    #: the day the file arrived — and nearly everything arrives on the day it is
    #: entered, so today is a useful capture default and a wrong one is
    #: harmless. `Arvamuse tähtaeg` is a *commitment*, usually somebody else's:
    #: the day Koda's opinion is due. Defaulting it to today invents a
    #: commitment nobody stated, and since the field became work
    #: (app/matters/work_items.py) the invention is no longer inert — a Matter
    #: created and left alone would be due on its creation day and overdue the
    #: next morning, on every deadline surface in the product.
    #:
    #: The edit form and `IncomingIntakeForm` already read it this way
    #: (Teema QA §5.2); this is that decision applied to the one form that had
    #: been missed.
    response_deadline = EstonianDateField(
        label="Arvamuse tähtaeg",
        required=False,
        widget=DATE_WIDGET,
    )
    policy_areas = forms.ModelMultipleChoiceField(
        label="Valdkonnad",
        queryset=PolicyArea.objects.none(),
        required=False,
        # Checkboxes because a Matter really can belong to several areas, and a
        # multi-select hides that behind a modifier key nobody uses (brief 19).
        widget=forms.CheckboxSelectMultiple(attrs={"class": "chip__input"}),
    )
    policy_area_other_selected = forms.BooleanField(
        label="Muu",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "chip__input"}),
    )
    policy_area_other = forms.CharField(
        label="Muu valdkond",
        max_length=400,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "field__input", "placeholder": "Millisesse valdkonda see kuulub?"}
        ),
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
        # The one control on this page that is not a chip. Every chip answers
        # "which of these is it"; this answers "is this even real work", and a
        # pill in the row beside Valdkonnad would read as one more of them.
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

    def clean_addressee_name(self) -> str:
        return clean_addressee_name(self.cleaned_data.get("addressee_name"))

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

        # New work, and only new work: this form has no existing owner to
        # preserve, so the population is the current department workers with no
        # union (app/accounts/selectors.py).
        set_choices(self, "owner", assignable_users())
        set_choices(self, "stage", active_stages())
        set_choices(self, "addressee_organisation", Organisation.objects.order_by("name"))

        # The explanations the Hetkeseis chips carry, read once. Handed to the
        # widget so each radio can point at its own, and exposed on the form so
        # the template can render the bubble the radio points at — one mapping,
        # two consumers, no sentence written twice
        # (app/workflow/selectors.py, Uus teema redesign §8).
        self.stage_help = stage_help_texts()
        cast(Any, self.fields["stage"].widget).descriptions = self.stage_help

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
            # What the disclosure's own label says it holds. Counted here
            # because a template cannot take the length of a choice iterator,
            # and "Vali nimekirjast" with no number is a door with nothing
            # written on it.
            self.sender_tail_count = len(rest.choices)

            # Adressaat is one radio group rendered in two places: the bodies
            # this department answers most often as chips, the rest inside the
            # "Vali nimekirjast" disclosure. One group and one name, because it
            # holds one value — the senders need two *fields* only because a
            # checkbox group cannot be split without splitting the field.
            #
            # `addressee_offered` is the whole ordered list; `addressee_split`
            # is where the long tail starts, counting the named blank option
            # that Django puts first. The template slices on it rather than
            # comparing primary keys, which a template cannot do without a
            # filter written to help it.
            self.frequent_addressees = addressees_by_usage(viewer)
            shortlist = {organisation.pk for organisation in self.frequent_addressees}
            tail = [
                organisation
                for organisation in Organisation.objects.order_by("name")
                if organisation.pk not in shortlist
            ]
            self.addressee_offered = [*self.frequent_addressees, *tail]
            self.addressee_split: int | None = 1 + len(self.frequent_addressees)
            addressees = cast(Any, self.fields["addressee_organisation"])
            # The named blank option, restated. Assigning `choices` replaces
            # Django's iterator, and the iterator is what would otherwise have
            # put `empty_label` in front — so "Määramata" has to be written
            # here or an addressee chosen by mistake could not be unchosen.
            addressees.choices = [
                ("", addressees.empty_label),
                *((organisation.pk, organisation.name) for organisation in self.addressee_offered),
            ]
            self.addressee_tail_count = len(tail)
        else:
            self.frequent_senders = []
            self.sender_tail_count = 0
            self.frequent_addressees = []
            self.addressee_offered = []
            # No viewer means no usage to rank by, so there is no shortlist and
            # no long tail — and the template must render *everything* inline
            # rather than the one blank option. A split of zero would hide the
            # whole catalogue behind a disclosure that is not rendered either,
            # which is a form quietly offering one choice.
            self.addressee_split = None
            self.addressee_tail_count = 0


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
    #: Chips, not selects — the same controls `Uus teema` uses, because the two
    #: pages are one job seen twice and were drifting apart as two designs
    #: (02-EKRAANID §C). The cardinality is unchanged: radios where the model
    #: holds one value, checkboxes where it holds several (ADR 0025, ADR 0032).
    #:
    #: `blank=True` on every `ModelChoiceField` rendered as radios, because
    #: Django drops the empty choice for one unless it is set — and an edit page
    #: whose «Määramata» chip is missing is a page that cannot take a value
    #: *off* a record, which is the whole reason somebody opens it.
    owner = UserChoiceField(
        label="Vastutaja",
        queryset=User.objects.none(),
        required=False,
        empty_label="Määramata",
        blank=True,
        widget=forms.RadioSelect(attrs={"class": "chip__input"}),
    )
    stage = forms.ModelChoiceField(
        label="Hetkeseis",
        queryset=StageVocabulary.objects.none(),
        required=False,
        empty_label="Määramata",
        blank=True,
        widget=DescribedRadioSelect(attrs={"class": "chip__input"}),
    )
    track = forms.ChoiceField(
        label="Menetlusliik",
        choices=[("", "Määramata"), *Track.choices],
        required=False,
        widget=forms.RadioSelect(attrs={"class": "chip__input"}),
    )
    policy_areas = forms.ModelMultipleChoiceField(
        label="Valdkonnad",
        queryset=PolicyArea.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "chip__input"}),
    )
    policy_area_other = forms.CharField(
        label="Muu valdkond",
        max_length=400,
        required=False,
        widget=TEXT_WIDGET,
    )
    source_organisations = forms.ModelMultipleChoiceField(
        label="Kellelt",
        queryset=Organisation.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "chip__input"}),
    )
    #: The long tail, exactly as `Uus teema` splits it. Two fields rather than
    #: one because a checkbox group cannot be split without splitting the field;
    #: `clean` unions them back into one answer.
    source_organisations_other = forms.ModelMultipleChoiceField(
        label="Muu saatja",
        queryset=Organisation.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "chip__input"}),
    )
    addressee_organisation = forms.ModelChoiceField(
        label="Kellele",
        queryset=Organisation.objects.none(),
        required=False,
        empty_label="Määramata",
        blank=True,
        widget=forms.RadioSelect(attrs={"class": "chip__input"}),
    )
    addressee_name = addressee_name_field()
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
        widget=forms.CheckboxSelectMultiple(attrs={"class": "chip__input"}),
    )
    visibility = forms.ChoiceField(
        label="Nähtavus",
        choices=Visibility.choices,
        required=False,
        widget=forms.RadioSelect(attrs={"class": "chip__input"}),
        help_text="Piiratud teemat näevad ainult vastutaja ja osalejad.",
    )

    def __init__(
        self, *args: Any, matter: Matter | None = None, viewer: Any = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self.matter = matter
        self.viewer = viewer

        # The current workers *plus* whoever this Matter already names.
        #
        # Exactly the shape `policy_areas` uses a few lines down, and for the
        # same reason: a Matter owned by a departed colleague must survive
        # having its title corrected. Narrowing this to the current workers
        # alone would make the unchanged owner an invalid choice, and the field
        # being optional that would not even fail loudly — it would clear an
        # owner nobody asked to remove (app/accounts/selectors.py).
        set_choices(self, "owner", assignable_including(matter.owner if matter else None))
        set_choices(self, "stage", active_stages())

        # The same per-stage explanations `Uus teema` shows, on the same
        # control. One mapping, two forms: a sentence written twice is a
        # sentence that stops matching (app/workflow/selectors.py).
        self.stage_help = stage_help_texts()
        cast(Any, self.fields["stage"].widget).descriptions = self.stage_help

        # Every organisation is a *valid* answer; only the offered chips are
        # narrowed. Validation therefore runs against the whole catalogue —
        # narrowing it to the visible few would reject a correct answer given
        # through the search control, and on an *edit* page it would reject the
        # value the record already carries.
        organisations = Organisation.objects.order_by("name")
        set_choices(self, "source_organisations", organisations)
        set_choices(self, "source_organisations_other", organisations)
        set_choices(self, "addressee_organisation", organisations)
        set_choices(self, "tags", Tag.objects.filter(is_active=True).order_by("name_et"))

        # The frequent bodies as chips and the rest behind «Vali nimekirjast»,
        # the same split `Uus teema` uses — plus, always, whatever this Matter
        # already names, because an edit page that hides the current value in a
        # disclosure is an edit page that looks like it cleared it.
        current_senders = list(matter.source_organisations.all()) if matter else []
        frequent = list(organisations_by_usage(viewer)) if viewer is not None else []
        known = {organisation.pk for organisation in frequent}
        for organisation in current_senders:
            if organisation.pk not in known:
                frequent.append(organisation)
                known.add(organisation.pk)
        self.frequent_senders = frequent
        senders = cast(Any, self.fields["source_organisations"])
        senders.choices = [(item.pk, item.name) for item in frequent]
        rest = cast(Any, self.fields["source_organisations_other"])
        tail = [item for item in organisations if item.pk not in known]
        rest.choices = [(item.pk, item.name) for item in tail]
        self.sender_tail_count = len(tail)

        # Adressaat is one radio group rendered in two places. One group and one
        # name, because it holds one value — the senders need two *fields* only
        # because a checkbox group cannot be split without splitting the field.
        shortlist = list(addressees_by_usage(viewer)) if viewer is not None else []
        chosen = matter.addressee_organisation if matter else None
        offered_ids = {item.pk for item in shortlist}
        if chosen is not None and chosen.pk not in offered_ids:
            shortlist.append(chosen)
            offered_ids.add(chosen.pk)
        addressee_tail = [item for item in organisations if item.pk not in offered_ids]
        self.addressee_offered = [*shortlist, *addressee_tail]
        # Counting the named blank option Django puts first, which the template
        # slices on rather than comparing primary keys.
        self.addressee_split: int | None = 1 + len(shortlist)
        self.addressee_tail_count = len(addressee_tail)
        addressees = cast(Any, self.fields["addressee_organisation"])
        # Assigning `choices` replaces Django's iterator, and the iterator is
        # what would otherwise have put `empty_label` in front — so «Määramata»
        # has to be written here or an addressee chosen by mistake could not be
        # unchosen.
        addressees.choices = [
            ("", addressees.empty_label),
            *((item.pk, item.name) for item in self.addressee_offered),
        ]

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

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        # The two sender controls are two ways into one set, so the canonical
        # answer is their union. Nothing is privileged for having been on the
        # shortlist, and an organisation ticked in both places appears once.
        senders: dict[Any, Organisation] = {}
        for source in ("source_organisations", "source_organisations_other"):
            for organisation in cleaned.get(source) or []:
                senders.setdefault(organisation.pk, organisation)
        cleaned["source_organisations"] = sorted(senders.values(), key=lambda o: o.name)
        return cleaned

    def clean_addressee_name(self) -> str:
        return clean_addressee_name(self.cleaned_data.get("addressee_name"))

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


#: What the date on a next step means, in the words the form shows.
#:
#: The stored values are `DateSemantics` and are not touched. What differs is
#: the wording and the order: Tähtaeg · Oodatav aeg · Vaatan üle reads as the
#: three answers to "what is this date", in the order the three kinds produce
#: them, where the enum's own labels are ordered by nothing and call
#: EXPECTED_AROUND "Oodatav umbes" — right in a register cell, wrong as the
#: label of a box somebody is about to type a date into.
DATE_MEANING_CHOICES: tuple[tuple[str, str], ...] = (
    (DateSemantics.DEADLINE.value, "Tähtaeg"),
    (DateSemantics.EXPECTED_AROUND.value, "Oodatav aeg"),
    (DateSemantics.REVIEW_ON.value, "Vaatan üle"),
)


class NextActionForm(forms.Form):
    """`Järgmiseks`, including what its date actually means.

    `use_required_attribute` is off, and that is not cosmetic. Setting a next
    action is optional — the view only validates it when somebody typed
    something — and with the HTML `required` attribute present a browser refuses
    to submit a form containing an invalid control, reports nothing, and the
    "Loo teema" button silently does nothing. That is how it failed while this
    block sat inside a closed `<details>`; the block is on the page now, but an
    empty optional field would refuse the submit just the same
    (Stage-2E.1 brief 26).

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
    #: The same three mode chips as Minu töö, the register and the Teema
    #: composer, and the same shape rule: TEEN filled, OOTAN solid-outlined,
    #: JÄLGIN dashed. Shape carries the distinction, never colour alone, and a
    #: reader who has met the vocabulary on the other four surfaces should not
    #: have to relearn it here as a stack of described cards.
    #:
    #: The described cards are what this replaces. Their glosses — "Mul endal
    #: tuleb midagi teha" and the other two — made the distinction on a page
    #: where the vocabulary was new. Beside a date whose meaning is stated on
    #: the same row, they were three lines explaining what the row already says
    #: (Uus teema redesign §6).
    #:
    #: The stored values are unchanged: DO, WAIT, MONITOR
    #: (master specification 11.2).
    kind = forms.ChoiceField(
        label="Mis laadi samm see on?",
        choices=ActionKind.choices,
        initial=ActionKind.DO,
        widget=forms.RadioSelect(attrs={"class": "modechip__input"}),
    )
    #: Optional, and normally derived. "Kuupäeva tähendus" is a question about
    #: the data model, and a required dropdown asking it was in front of every
    #: lawyer setting a next step.
    #:
    #: It is *not* deleted, because the model genuinely permits more than one
    #: meaning per kind and the register's parser uses that — a DO whose source
    #: names a vague month is DO + EXPECTED_AROUND, not a deadline.
    #:
    #: No longer a nested disclosure, and no longer phrased as a question about
    #: the data model. The three meanings are chips on the row with the date, so
    #: the box states what it means instead of a swapped label stating it on the
    #: box's behalf. Left blank it still derives from the kind, which is what
    #: keeps a POST from anywhere — a script, a test, a browser with the chips
    #: untouched — storing the same canonical value
    #: (app/workflow/enums.py, Uus teema redesign §6).
    date_semantics = forms.ChoiceField(
        label="Mida kuupäev tähendab",
        # The design's wording for the same three stored values, in the order
        # the three kinds produce them. The enum is untouched: the register,
        # Minu töö and the Teema page keep the words they have.
        choices=DATE_MEANING_CHOICES,
        required=False,
        widget=forms.RadioSelect(attrs={"class": "chip__input"}),
    )
    #: Today, because that is the answer nearly every time and nobody should have
    #: to type it. `initial` only ever fills an *unbound* form, so a POSTed value
    #: always wins, a validation error keeps what was typed, and a date somebody
    #: deliberately cleared stays cleared (Teema QA §5.2).
    target_date = EstonianDateField(
        label="Kuupäev", required=False, widget=DATE_WIDGET, initial=timezone.localdate
    )
    #: Kept, and no longer rendered on Uus teema.
    #:
    #: The step inherits the Vastutaja chosen a few rows up, which the view
    #: hands to the service as a default; naming the same colleague twice on one
    #: form is a question whose answer is already on the screen. Re-assigning a
    #: step to somebody else is a real thing that happens — on the Teema page,
    #: where the step and the person are both in front of you
    #: (app/workflow/services.py `set_next_action`, Uus teema redesign §6).
    #:
    #: The field stays, because a POST that names somebody explicitly must
    #: still win over the default, and because deleting it would move that rule
    #: out of the form and into the view.
    responsible = UserChoiceField(
        label="Kes selle eest vastutab?",
        queryset=User.objects.none(),
        required=False,
        widget=SELECT_WIDGET,
        help_text="Tühjaks jättes vastutab teema vastutaja.",
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # No template renders this select — it is a field the POST may carry,
        # which is exactly why the population matters. A control hidden from the
        # page is not a control an endpoint refuses, and until this line the
        # endpoint accepted any signed-in account's identifier. New work only,
        # so no union: an existing step is superseded and replaced rather than
        # edited, and the person named on the replacement is a new assignment
        # (app/workflow/services.py `set_next_action_for_new_work`, ADR 0036).
        #
        # Leaving it blank still means *the Matter owner* — but only while that
        # owner is somebody the department still gives work to. The native
        # service refuses the rest rather than filing a new step in a departed
        # colleague's queue; the wording is in `responsible_for_new_work`, and
        # the reason it is enforced there rather than here is that the composer
        # reaches the same fallback without going through this form at all
        # (ADR 0036 §5).
        set_choices(self, "responsible", assignable_users())

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
#: What `Põhjus` offers when a Matter is closed from the composer.
#:
#: **`SUPERSEDED` is deliberately absent, and no domain capability was removed
#: to achieve that.** `Jätkub teise teema all` is the one disposition whose
#: truth depends on a second record — the `Järglane` it continues under — and
#: the simplified closing flow does not ask for one. Offering it anyway would
#: mean posting a null successor: a closure asserting a continuation that names
#: nothing, which is worse than not offering the choice.
#:
#: `Matter.superseded_by`, `close_matter(successor=…)` and
#: `Disposition.SUPERSEDED` are all untouched and still enforce their invariant,
#: ready for the dedicated "work continues under another Matter" operation that
#: would ask the question properly (Teema closing redesign §3).
CLOSURE_CHOICES: tuple[tuple[str, str], ...] = (
    (Disposition.COMPLETED.value, "Jõustus või töö lõppes"),
    (Disposition.INITIATIVE_WITHDRAWN.value, "Eelnõu või algatus lõpetati"),
    (Disposition.RESPONSE_COMPLETE.value, "Vastus esitatud ja järeltegevus tehtud"),
    (Disposition.MONITORING_STOPPED.value, "Koda ei tegele edasi"),
    (Disposition.NO_POSITION_FORMED.value, "Seisukohta ei kujundatud"),
    (Disposition.OTHER.value, "Muu"),
)

#: `Töövõit` is a decision, so it has no default. A Matter closed without
#: anybody answering would silently count as "no win", which is a claim the
#: person never made (Teema closing redesign §10).
WORK_VICTORY_CHOICES: tuple[tuple[str, str], ...] = (("JAH", "Jah"), ("EI", "Ei"))


class MultiTextInput(forms.TextInput):
    """One text box that may be submitted many times under one name.

    The `Muu` recipient control is a search box plus however many chips
    somebody has added, and every one of them posts as `final_recipient_names`.
    Django's default widget reads a single value, so seven typed parties would
    arrive as one.

    Reading `getlist` rather than replacing the control with a textarea keeps
    the no-JavaScript path honest: the visible box carries the same name, so
    typing one recipient and saving works with nothing bound to it.
    """

    def value_from_datadict(self, data: Any, files: Any, name: str) -> list[str]:
        if hasattr(data, "getlist"):
            return list(data.getlist(name))
        value = data.get(name)
        if value in (None, ""):
            return []
        return [value] if isinstance(value, str) else list(value)


class RecipientNamesField(forms.Field):
    """The typed half of the recipient set: names, cleaned of whitespace only.

    Deliberately not a `ModelMultipleChoiceField` and deliberately not resolved
    here. A form validates; the institutions these names mean are created by
    `app.organisations.services.resolve_recipients` inside the closure's own
    transaction, so a refused save leaves no rows behind (§7E, §17).
    """

    widget = MultiTextInput

    def clean(self, value: Any) -> list[str]:
        if value in (None, ""):
            return []
        raw = value if isinstance(value, list) else [value]
        return [cleaned for cleaned in (" ".join(str(item).split()) for item in raw) if cleaned]


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
    #: The closing section asks six things and nothing else: why, the file that
    #: went out, when it went out, who to, whether it was a win, and — only
    #: then — when the result commenced.
    #:
    #: **Everything it used to ask twice is gone.** `Tulemus` asked for the
    #: closing narrative a second time, immediately under the box that had just
    #: taken it; `Lõpparvamuse pealkiri` asked for the Matter's own title;
    #: `Mida Koda saavutas?` and `Töövõidu selgitus` asked for the narrative a
    #: third and fourth time. One save, one narrative — the composer body — and
    #: the canonical records derive their wording from it
    #: (Teema closing redesign §2, §5, §10, §12).
    close_matter = forms.BooleanField(label="Lõpeta teema", required=False)
    disposition = forms.ChoiceField(
        label="Põhjus",
        choices=CLOSURE_CHOICES,
        required=False,
        widget=SELECT_WIDGET,
    )
    #: The file that actually went out, uploaded here.
    #:
    #: It used to be a picker over versions already on the Matter, which is the
    #: wrong workflow: closing a file is the moment the lawyer has the sent PDF
    #: in front of them and has typically never uploaded it. Nothing about the
    #: canonical rule changed — a SENT `Submission` still needs the exact final
    #: evidence, still checked against the Matter's visibility. What changed is
    #: that the evidence is captured in the same save instead of in a visit
    #: beforehand (Teema closing redesign §4).
    final_file = forms.FileField(
        label="Lõpparvamus",
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "field__input"}),
    )
    #: No `initial`, like every other date box read for its emptiness: a send
    #: date with no file is an opinion claimed without its evidence, so a
    #: default would refuse every ordinary closure that is not also recording
    #: one (Teema QA §5).
    final_sent_on = EstonianDateField(
        label="Saatmise kuupäev", required=False, widget=EstonianDateInput()
    )
    #: The shortlist half of `Saaja`. The queryset stays the whole catalogue
    #: because the *rendered* shortlist is a convenience, not a permission —
    #: an institution reached through `Muu` posts its own name, and an
    #: institution somebody reached last week must still be tickable.
    final_recipients = forms.ModelMultipleChoiceField(
        label="Saaja",
        queryset=Organisation.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "checkitem__input"}),
    )
    #: `Muu` — the typed half, repeatable. Seven new recipients on one opinion
    #: is a real case and it must not cost seven page loads (§7B).
    final_recipient_names = RecipientNamesField(
        label="Muu",
        required=False,
        widget=MultiTextInput(
            attrs={
                "class": "field__input",
                "autocomplete": "off",
                "placeholder": "Otsi või kirjuta saaja nimi…",
            }
        ),
    )
    #: An explicit decision, taken when the file is closed. Radios rather than a
    #: checkbox, because a checkbox left alone cannot be told apart from a
    #: person who answered "no" (§10).
    work_victory = forms.ChoiceField(
        label="Töövõit",
        choices=WORK_VICTORY_CHOICES,
        required=False,
        widget=forms.RadioSelect(attrs={"class": "choiceset__radio"}),
    )
    #: `Jõustumise kuupäev` — when the result commenced, and **not** the work
    #: victory's business period. They are two facts and the domain has two
    #: models for them; storing a commencement in `MatterWorkVictory.period_date`
    #: because both happen to be dates would put a win in the reporting year of
    #: whenever the act took effect (§13).
    victory_effective_on = EstonianDateField(
        label="Jõustumise kuupäev", required=False, widget=EstonianDateInput()
    )

    def __init__(self, *args: Any, matter: Any = None, viewer: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for prefix, label in (("next", "Kuupäev"), ("deadline", "Kuupäev")):
            self.fields.update(_precision_fields(prefix, date_label=label))
        set_choices(self, "organisation", Organisation.objects.order_by("name"))
        set_choices(self, "final_recipients", Organisation.objects.order_by("name"))
        # Ticked in one click, because most letters go to bodies this
        # department writes to constantly — and computed rather than
        # hard-coded, because which ones those are changes with the government
        # (`addressees_by_usage`).
        self.recipient_shortlist = addressees_by_usage(viewer)
        self.matter = matter

        # **Nothing here binds an existing record the viewer might not be able
        # to read.** The old form offered a `Järglane` picker over Matters and a
        # final-evidence picker over `DocumentVersion`, and both had to go
        # through `visible_to`: a crafted POST naming a restricted Matter would
        # have told the sender that a file with that id exists, and binding a
        # document they may not read as a submission's final evidence would
        # print its filename, size and SHA-256 to everybody who can see the
        # submission.
        #
        # Neither picker exists now. The final opinion is a file this person is
        # uploading, so it is created on *this* Matter and inherits its
        # visibility — the whole class of disclosure the two querysets guarded
        # against has no surface left in this form (Teema closing redesign §18).

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
        """The closing half: six answers, and none of them asked twice.

        The narrative is the composer body and only the composer body. It
        becomes the stored closure reason, and — when somebody says this was a
        win — the work victory's wording too. So `Töövõit = Jah` with an empty
        box is refused *on the box*, rather than inventing a description for a
        record the department reports on (Teema closing redesign §2, §12).
        """
        if not wanted:
            cleaned["closure_kwargs"] = None
            return

        disposition = cleaned.get("disposition") or ""
        if not disposition:
            self.add_error("disposition", "Vali, miks teema lõpeb.")

        # The plain-text form of what was typed above. `disposition_reason` is
        # a sentence in a banner, not markup, and the entry keeps the rich text.
        narrative = plain_text(cleaned.get("body") or "").strip()

        upload = cleaned.get("final_file")
        chosen = list(cleaned.get("final_recipients") or [])
        typed = self._clean_recipient_names(cleaned.get("final_recipient_names") or [], chosen)
        sent_on = cleaned.get("final_sent_on")

        # An opinion is claimed only when somebody chose the file that went out.
        # Everything else about it is then required, because a sent submission
        # with no recipient and no date is a claim the record cannot support —
        # and a PDF on its own is not a sent opinion (§21).
        if upload is not None:
            if sent_on is None:
                self.add_error("final_sent_on", "Märgi, millal arvamus saadeti.")
            if not chosen and not typed:
                self.add_error("final_recipients", "Märgi, kellele arvamus saadeti.")
        elif chosen or typed or sent_on is not None:
            self.add_error(
                "final_file",
                "Lae saadetud fail — ilma täpse tõendita ei saa arvamust saadetuks märkida.",
            )

        victory = cleaned.get("work_victory") or ""
        if not victory:
            self.add_error("work_victory", "Märgi, kas teemast sai töövõit.")
        elif victory == "JAH":
            if not narrative:
                self.add_error(
                    "body",
                    "Kirjelda, mida Koda saavutas — sellest saab töövõidu sõnastus.",
                )
            if cleaned.get("victory_effective_on") is None:
                self.add_error("victory_effective_on", "Märgi, millal tulemus jõustus.")

        # Assembled only once nothing was refused. A half-built closure handed
        # to the service would be a partial save waiting to happen, and the
        # whole point of one transaction is that there is no such state (§24).
        if self.errors:
            return

        closure: dict[str, Any] = {"disposition": disposition, "reason": narrative}
        if upload is not None:
            closure["final_opinion"] = {
                "upload": upload,
                "recipients": chosen,
                "recipient_names": typed,
                "sent_at": _as_datetime(sent_on),
            }
        if victory == "JAH":
            closure["work_victory"] = {"title": narrative[:2000], "detail": ""}
            effective_on = cleaned["victory_effective_on"]
            # `Jõustumise kuupäev` is a commencement, so it goes where the
            # domain keeps commencements. `MatterWorkVictory.period_date` is a
            # reporting period and stays empty here rather than being borrowed
            # because it happens to be the only other date on the record (§13).
            closure["effective_date"] = {
                "date_value": effective_on,
                "period_end": effective_on,
            }
        cleaned["closure_kwargs"] = closure

    def _clean_recipient_names(self, typed: list[str], chosen: list[Any]) -> list[str]:
        """The typed recipients that are still worth resolving.

        Two names are dropped here rather than in the service: one that already
        names a ticked institution, and one that repeats an earlier line. Both
        would be collapsed again by `resolve_recipients` and by the unique
        recipient-per-submission constraint — dropping them at the form is what
        keeps the count somebody sees equal to the count that is stored (§7F).

        Genuine ambiguity is refused instead. Two institutions spelled the same
        way is a question only a person can answer, and both silently picking
        one and silently creating a third are wrong answers (§7D).
        """
        from app.core.text import normalize_for_matching
        from app.organisations.services import find_matches

        seen = {normalize_for_matching(organisation.name) for organisation in chosen}
        keep: list[str] = []
        for name in typed:
            matches = find_matches(name)
            if len(matches) > 1:
                self.add_error(
                    "final_recipient_names",
                    f"«{name}» sobib mitme organisatsiooniga — vali nimekirjast.",
                )
                continue
            # An existing name — canonical or an alias somebody recorded —
            # identifies that institution, so the duplicate check compares
            # institutions rather than spellings.
            key = normalize_for_matching(matches[0].name if matches else name)
            if key in seen:
                continue
            seen.add(key)
            keep.append(name)
        return keep

    @property
    def shortlist_recipients(self) -> list[dict[str, Any]]:
        """The tickable half of `Saaja`: who this department writes to most.

        Rendered from an explicit list rather than by iterating the field,
        because the field's queryset is deliberately wider than the shortlist.
        The queryset is what a POST is validated against — an institution
        somebody reached through `Muu` last week must still be tickable if a
        later form is built with it — while this is only what is worth showing
        without asking (§7A).
        """
        submitted = (
            set(self.data.getlist("final_recipients"))
            if self.is_bound and hasattr(self.data, "getlist")
            else set()
        )
        return [
            {
                "value": str(organisation.pk),
                "label": organisation.name,
                "checked": str(organisation.pk) in submitted,
            }
            for organisation in getattr(self, "recipient_shortlist", [])
        ]

    @property
    def typed_recipients(self) -> list[str]:
        """What the `Muu` chips should show again after a refused save.

        Read from the raw data rather than from `cleaned_data`, because the
        save that most needs its chips back is the one that did not validate.
        """
        if not self.is_bound or not hasattr(self.data, "getlist"):
            return []
        seen: set[str] = set()
        names: list[str] = []
        for raw in self.data.getlist("final_recipient_names"):
            name = " ".join((raw or "").split())
            if name and name.casefold() not in seen:
                seen.add(name.casefold())
                names.append(name)
        return names

    @property
    def recipient_catalogue(self) -> list[str]:
        """Every spelling the `Muu` box can complete against.

        Canonical names and recorded aliases both, so typing `MKM` finds the
        ministry somebody decided it means. It is a catalogue rather than a
        choice list: a name that is not in it is a new institution, created on
        save (§7C).
        """
        from app.organisations.models import OrganisationAlias

        names = list(Organisation.objects.order_by("name").values_list("name", flat=True))
        aliases = list(OrganisationAlias.objects.order_by("alias").values_list("alias", flat=True))
        known = {name.casefold() for name in names}
        return names + [alias for alias in aliases if alias.casefold() not in known]

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

    def __init__(self, *args: Any, matter: Matter | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # `matter` is what makes the inline owner control survive a Matter whose
        # owner is no longer a department worker: the current workers plus that
        # one person, so re-submitting the value already on the record is a
        # save and naming any *other* non-assignable account is a refusal
        # (app/accounts/selectors.py, docs/adr/0036).
        set_choices(self, "owner", assignable_including(matter.owner if matter else None))
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

    Deliberately absent: Hetkeseis and Menetlusliik. A file arriving means
    something was received, not that the external process has reached a
    particular stage — and the v2 form says so in its own footer: the rest of
    the record is filled in on the Matter page (02-EKRAANID §F).

    **Nähtavus stays.** It is not "the rest of the record": filing a restricted
    letter as NORMAL and correcting it afterwards means it was department-wide
    in between, and this round does not widen access anywhere
    (docs/design-v2-compatibility.md, DS-15).
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
    brief_summary = forms.CharField(
        label="Lühikokkuvõte",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "field__input",
                "rows": "3",
                "placeholder": "Mida see teema ettevõtjatele tähendab? (2–3 lauset)",
            }
        ),
    )
    #: Chips, and the long tail behind «Vali nimekirjast», exactly as `Uus
    #: teema` does it. Two fields rather than one because a checkbox group
    #: cannot be split without splitting the field; `clean` unions them back
    #: into one answer, so nothing is privileged for having been on the
    #: shortlist (ADR 0025).
    source_organisations = forms.ModelMultipleChoiceField(
        label="Saatja või algataja",
        queryset=Organisation.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "chip__input"}),
    )
    source_organisations_other = forms.ModelMultipleChoiceField(
        label="Muu saatja",
        queryset=Organisation.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "chip__input"}),
    )
    handover_note = forms.CharField(
        label="Märkmed vastutajale",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "field__input",
                "rows": "2",
                "placeholder": "Mida vastutaja peaks teadma — kellega rääkida, mis on siin oluline",
            }
        ),
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
        # The blank option is named rather than left as Django's row of dashes,
        # and `blank=True` is what makes that name survive: Django drops the
        # empty choice for a `ModelChoiceField` rendered as radios unless it is
        # set, and without it there is no «Määramata» chip and an owner picked
        # by mistake cannot be unpicked.
        empty_label="Määramata",
        blank=True,
        widget=forms.RadioSelect(attrs={"class": "chip__input"}),
    )
    visibility = forms.ChoiceField(
        label="Nähtavus",
        choices=Visibility.choices,
        initial=Visibility.NORMAL,
        widget=SELECT_WIDGET,
    )

    def __init__(self, *args: Any, viewer: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # A Matter that does not exist yet, so there is nothing to preserve:
        # the current department workers and nobody else. Intake is the second
        # way into a Matter and must not be the way around `Uus teema`'s rule
        # (app/accounts/selectors.py).
        set_choices(self, "owner", assignable_users())

        # Every organisation is a *valid* sender; only the frequent ones are
        # offered as chips. Validation therefore runs against the full set —
        # narrowing it to the visible few would reject a correct answer given
        # through the search control.
        everything = Organisation.objects.order_by("name")
        set_choices(self, "source_organisations", everything)
        set_choices(self, "source_organisations_other", everything)

        self.frequent_senders: list[Organisation] = []
        self.sender_tail_count = 0
        if viewer is not None:
            senders = cast(Any, self.fields["source_organisations"])
            self.frequent_senders = list(organisations_by_usage(viewer))
            senders.choices = [
                (organisation.pk, organisation.name) for organisation in self.frequent_senders
            ]
            frequent = {organisation.pk for organisation in self.frequent_senders}
            rest = cast(Any, self.fields["source_organisations_other"])
            rest.choices = [
                (organisation.pk, organisation.name)
                for organisation in everything
                if organisation.pk not in frequent
            ]
            self.sender_tail_count = len(rest.choices)

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        # The two sender controls are two ways into one set, so the canonical
        # answer is their union — the same rule `Uus teema` applies.
        senders: dict[Any, Organisation] = {}
        for source in ("source_organisations", "source_organisations_other"):
            for organisation in cleaned.get(source) or []:
                senders.setdefault(organisation.pk, organisation)
        cleaned["source_organisations"] = sorted(senders.values(), key=lambda o: o.name)
        return cleaned
