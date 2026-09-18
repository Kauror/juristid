"""Forms parse and validate input. They never change state.

Every form here hands its cleaned values to a service function in
``app.matters.services`` or ``app.workflow.services``. Nothing in this module
writes a model field, so the audit trail and the invariants cannot be bypassed
by adding another view (master specification 12.4, 23.4).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, cast

from django import forms
from django.db.models import QuerySet
from django.utils import timezone
from django.utils.functional import cached_property

from app.accounts.models import User
from app.accounts.naming import disambiguated_names
from app.accounts.selectors import assignable_business_users, assignable_including
from app.core.authorization import scoped_count
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.core.richtext import plain_text
from app.core.visibility_help import RESTRICTED_VISIBILITY_HELP
from app.core.widgets import DescribedRadioSelect, EstonianDateField, EstonianDateInput
from app.documents.enums import DocumentRole
from app.documents.limits import WORKING_DOCUMENT_URL_MAX_LENGTH
from app.matters.entry_enums import EntryKind
from app.matters.enums import (
    COMPOSER_ENGAGEMENT_KINDS,
    EngagementKind,
    ExternalPositionProvenance,
    MatterDataClass,
    ProceduralLinkKind,
)
from app.matters.models import (
    DEVELOPMENT_TITLE_MAX_LENGTH,
    EXTERNAL_POSITION_LAWYER_NOTE_MAX_LENGTH,
    EXTERNAL_POSITION_SOURCE_LABEL_MAX_LENGTH,
    EXTERNAL_POSITION_SUMMARY_MAX_LENGTH,
    EXTERNAL_POSITION_URL_MAX_LENGTH,
    PROCEDURAL_LINK_LABEL_MAX_LENGTH,
    PROCEDURAL_LINK_URL_MAX_LENGTH,
    WEBSITE_OVERVIEW_URL_MAX_LENGTH,
    Matter,
)
from app.organisations.models import Organisation, OrganisationAlias
from app.taxonomy.legal_instruments import OTHER_LEGAL_INSTRUMENT_KEYS
from app.taxonomy.models import LegalInstrumentType, PolicyArea, Tag
from app.taxonomy.vocabulary import (
    selectable_legal_instrument_types,
    selectable_policy_areas,
)
from app.workflow.dates import (
    MAX_YEAR,
    MIN_YEAR,
    InvalidPeriod,
    bounds_for,
    format_at_precision,
    period_starts_after,
)
from app.workflow.enums import (
    ESTONIAN_MONTHS,
    ROMAN_QUARTERS,
    ActionKind,
    DatePrecision,
    DateSemantics,
    Disposition,
    Track,
)
from app.workflow.models import StageVocabulary
from app.workflow.selectors import selectable_stages, stage_help_texts, stages_including


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

    **Unless two of them are called the same thing.** Two `Sandra` rows in a
    Vastutaja picker are two identical answers to a question with one right
    answer, and picking the wrong one moves a file onto the wrong desk and sends
    the notice there too. So the label is computed over *this field's own
    queryset* — the people actually being offered — and only the names that
    genuinely collide grow (``app.accounts.naming``, pilot QA F-03).

    The value is the user's id throughout. Nothing here changes what is
    submitted, validated or stored; a label is a label.
    """

    #: The population the cached labels were computed from, held by identity.
    #:
    #: `ModelChoiceField.queryset` is a property whose setter stores
    #: `queryset.all()` — a *new* object each time it is assigned. So comparing
    #: identity invalidates the cache exactly when the field is pointed at a
    #: different population, and never otherwise. Overriding the setter itself
    #: would not work: Django binds the property to its own functions at class
    #: definition, so a subclass's `_set_queryset` is simply not called.
    _labels_for: Any = None
    _labels: dict[Any, str] | None = None

    def label_from_instance(self, obj: Any) -> str:
        queryset = self.queryset
        if self._labels is None or self._labels_for is not queryset:
            self._labels = disambiguated_names(list(queryset) if queryset is not None else [])
            self._labels_for = queryset
        return self._labels.get(obj.pk) or obj.get_short_name() or obj.upn


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


def provider_link_field(label: str, placeholder: str) -> forms.CharField:
    """One optional external pointer, validated exactly like `Kaasamine.url`.

    A `CharField` rather than a `URLField`: the scheme allow-list belongs to
    `normalize_engagement_url`, which is what the service enforces, and running
    Django's own validator first would answer a refused `javascript:` link with
    a different sentence depending on which layer caught it.
    """
    return forms.CharField(
        label=label,
        required=False,
        max_length=1000,
        widget=forms.TextInput(
            attrs={
                "class": "field__input field__input--compact",
                "inputmode": "url",
                "autocomplete": "off",
                "placeholder": placeholder,
            }
        ),
    )


def clean_provider_link(form: forms.Form, field: str) -> str:
    """The service's own rule, reported under the box somebody typed it in."""
    from app.matters.services import normalize_engagement_url

    try:
        return normalize_engagement_url(form.cleaned_data.get(field))
    except DomainError as error:
        raise forms.ValidationError(str(error)) from error


#: What a reply-by date typed before the round it belongs to is told.
#:
#: One string, because two forms ask the same question and a refusal worded
#: twice is a refusal that drifts (`CompactEngagementForm`, `EngagementForm`).
DEADLINE_BEFORE_ENGAGEMENT = "Tagasiside tähtaeg ei saa olla enne kaasamise kuupäeva."


def refuse_deadline_before_engagement(form: forms.Form, cleaned: dict[str, Any]) -> None:
    """The one relationship between the two engagement dates, and no other rule.

    A reply-by date *before* the day the round started is not a late
    consultation, it is a slip of the keyboard — nothing was ever asked to be
    answered before it was asked. Same day is fine («vastake tänaseks»), later
    is the normal case, and a deadline with no engagement date at all is
    accepted because a person who does not remember when they wrote may still
    remember what they asked for. A deadline already in the past is accepted
    too: a consultation recorded months late had its deadline months ago
    (docs/adr/0078 §3).

    Reported on `feedback_deadline`, because that is the box the person would
    correct: the engagement date is the anchor and the deadline is what is
    being placed against it.

    **Read from `occurred_on_value`, which is the resolved anchor rather than
    the day box.** Since docs/adr/0082 the engagement date may be a month, a
    quarter or a year, and those leave the day box empty — a rule reading it
    directly would simply stop firing for three of the four precisions.

    **And the anchor is the right end of the period to compare against.** The
    anchor is the period's *first* day, so an approximate round refuses only a
    deadline that falls before the whole period began — «kaasamine oktoobris,
    vastused 20. septembriks» — and accepts every day inside it. Comparing
    against the period's end would refuse «kaasamine oktoobris, vastused
    15. oktoobriks», which is not a typo but the commonest thing a consultation
    run over a month actually says.

    Shared by the `+ Kaasamine` panel and the correction form. The panel writes
    a new record and the correction form rewrites one, and the rule about what
    the two dates may say to each other is the same rule either way — a copy
    per form is how a record becomes correctable into a state it could never
    have been created in.
    """
    occurred_on = cleaned.get("occurred_on_value")
    feedback_deadline = cleaned.get("feedback_deadline")
    if occurred_on and feedback_deadline and feedback_deadline < occurred_on:
        form.add_error("feedback_deadline", DEADLINE_BEFORE_ENGAGEMENT)


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


def stages_including_held(matter: Matter | None) -> Any:
    """The offered Hetkeseis vocabulary **plus** the one this Matter holds.

    One line, for the reason `active_stages` above is one line: the rule is
    `app.workflow.selectors.stages_including`, and the two forms that edit an
    existing Matter must not each decide separately what a retired stage means.

    `active_stages` is what `Uus teema` uses and stays that way. This is the
    *edit* answer: a Matter holding a since-retired stage is offered it back and
    may keep it, exactly as it may keep a retired Valdkond or Õigusakt, and no
    other Matter gains it as a choice (docs/adr/0032 §Amendment).
    """
    return stages_including(matter.stage if matter else None)


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


def legal_instruments_field() -> forms.ModelMultipleChoiceField:
    """The `Õigusakt` control, defined once for the two forms that carry it.

    Checkboxes, because `Matter.legal_instruments` holds several — the control
    shape is a promise about the data, and shipping a single-value control over
    a many-valued field is the one thing the approved design forbids outright
    (OIGUSAKT_UUS_TEEMA_DESIGN §4, ADR 0025).

    The queryset is empty here and filled per form: `Uus teema` offers the
    active vocabulary, `Muuda teemat` validates against all of it so a Matter
    carrying a since-retired type does not lose it to an unrelated correction.
    """
    return forms.ModelMultipleChoiceField(
        label="Õigusakt",
        queryset=LegalInstrumentType.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "chip__input"}),
    )


def legal_instrument_other_field() -> forms.CharField:
    """The box `Muu` reveals. Label exactly «Õigusakti liik» (design §16).

    `required=False` at the field level and required by `clean` when `Muu` is
    among the chosen instruments — the same shape `policy_area_other` has,
    because "required, but only in one state" is a fact about the form and not
    about the field.
    """
    return forms.CharField(
        label="Õigusakti liik",
        max_length=400,
        required=False,
        widget=forms.TextInput(attrs={"class": "field__input field__input--compact"}),
    )


def clean_legal_instrument_answer(form: forms.Form, cleaned: dict[str, Any]) -> None:
    """Settle `Õigusakt` and the text beside it, for whichever form asks.

    One implementation, because `Uus teema` and `Muuda teemat` must agree about
    what `Muu` means: a rule that is true on the page somebody files from and
    false on the page they correct from is a rule people learn twice.

    Two things happen here and both are decisions rather than tidying:

    * **Free text belongs to the chip that reveals it.** Unticking `Muu` and
      leaving the box full must not quietly save the text — the same rule
      `policy_area_other` follows. Nothing "hidden" survives a save.
    * **`Muu` without the text is refused**, on the box that is empty. `Muu`
      alone records that the instrument was none of the listed kinds and says
      nothing about which, which is less than the blank field it replaced.

    **`Muu` is a set of rows, not one row.** The reviewed vocabulary splits it
    along the one axis it cares about — `Muu siseriiklik` and `Muu ELi
    dokument` — and version 1.0's `Muu` is still held by Matters filed before
    that. All three reveal the same box and are refused empty by the same rule,
    because the thing that makes the rule true is what the answer *records*,
    not which of the three said it (`OTHER_LEGAL_INSTRUMENT_KEYS`).
    """
    chosen = list(cleaned.get("legal_instruments") or [])
    other_selected = any(item.key in OTHER_LEGAL_INSTRUMENT_KEYS for item in chosen)
    cleaned["legal_instrument_other"] = (cleaned.get("legal_instrument_other") or "").strip()
    if not other_selected:
        cleaned["legal_instrument_other"] = ""
    elif not cleaned["legal_instrument_other"]:
        form.add_error("legal_instrument_other", "Kirjuta, millise õigusaktiga on tegemist.")


class LegalInstrumentChoicesMixin:
    """What the template needs to draw the row, on both forms that draw it.

    `Muu` is a real vocabulary row here rather than a checkbox beside one, so
    the template cannot tell it apart by field name the way the Valdkonnad block
    does. It compares against these instead — one property saying which rendered
    values are the `Muu` rows, one saying whether their box renders open.

    **Several rows, not one.** The reviewed vocabulary offers `Muu siseriiklik`
    and `Muu ELi dokument`, and a Matter filed before it may hold version 1.0's
    `Muu`; all three reveal the same free-text box
    (`OTHER_LEGAL_INSTRUMENT_KEYS`, docs/adr/0090 §3).
    """

    fields: dict[str, forms.Field]
    errors: Any

    #: Set by `offer_legal_instruments`. Declared here so a form that somehow
    #: never called it renders a plain chip row instead of raising.
    _other_instrument_values: tuple[str, ...] = ()

    def offer_legal_instruments(self, offered: Sequence[Any]) -> None:
        """Render these types, in this order, and remember which one is `Muu`.

        Both forms narrow the *rendered* list without narrowing the validating
        queryset — the edit form so a Matter carrying a since-retired type keeps
        it — so the choices are assigned rather than left to Django's iterator,
        and the `Muu` row's rendered value has to be captured while the objects
        are still in hand. Reading it back off `choices` afterwards would mean
        either a query per render or a dependence on Django keeping the
        model instance attached to the value it yields.
        """
        cast(Any, self.fields["legal_instruments"]).choices = [
            (item.pk, item.label_et) for item in offered
        ]
        self._other_instrument_values = tuple(
            str(item.pk) for item in offered if item.key in OTHER_LEGAL_INSTRUMENT_KEYS
        )

    @property
    def other_instrument_values(self) -> tuple[str, ...]:
        """The rendered values of the `Muu` chips, as the template sees them.

        `Muu` is a real vocabulary row here rather than a checkbox beside one —
        see docs/adr/0070 §8 for why — so the template cannot tell it apart by
        field name the way the Valdkonnad block does, and compares against this.

        Several, because the reviewed vocabulary splits the answer in two
        (`Muu siseriiklik`, `Muu ELi dokument`) and an older Matter may hold
        version 1.0's `Muu` besides. In *rendered* order, so the template's
        `in` test is over whatever this form is actually drawing.

        Empty when the offered vocabulary carries no such row. That is a real
        state, not an error: a database seeded before this vocabulary existed
        renders a plain chip row and no reveal.
        """
        return self._other_instrument_values

    @property
    def other_instrument_open(self) -> bool:
        """Whether the free-text box renders visible without any scripting.

        True when `Muu` is ticked in whatever this form is about to render —
        the POST on a refused save, the Matter's own values on an unbound edit.
        `BoundField.value()` is what answers both without the caller having to
        know which it is looking at.

        This is what makes the refusal in design §9 legible: the box comes back
        open, holding what was typed, with the error inside it. A reveal only
        JavaScript can open would hide a refusal behind a click (§16, §10).
        """
        others = set(self.other_instrument_values)
        if not others:
            return False
        raw = cast(Any, self)["legal_instruments"].value()
        if raw is None:
            return False
        values = raw if isinstance(raw, (list, tuple)) else [raw]
        return any(str(item) in others for item in values)


class OrganisationPickerChoicesMixin:
    """What `organisation_picker.html` needs, on both forms that draw it.

    `organisation_picker.html` renders one control for both counterparty
    questions, and each of them offers the same two things: a handful of
    institutions as visible chips, and the rest of the catalogue as entries the
    search can reach. The split differs underneath — Saatja is two checkbox
    *fields* because one group cannot be rendered in two places without becoming
    two, Adressaat is one radio group sliced at `addressee_split` because it
    holds one value — and neither difference belongs in a template that is
    supposed to be the same control twice.

    So the slicing is done here, in Python, and the partial receives two plain
    lists of subwidgets either way (task §23, docs/adr/0073).

    **Shared rather than copied, and that is the point of R2-12.** These four
    properties were `MatterCreateForm`'s alone while `Muuda teemat` rendered the
    older three-control shape — popular chips, `Vali nimekirjast`, `Uus saatja`,
    and no search at all over the catalogue. Same concept, same interaction: a
    person who learns one Organisation control filing a Teema must not find a
    different one on the page they correct it from. A second picker built for
    the edit page would be a second set of these rules to keep in step
    (post-QA R2-12).

    The forms remain two forms. What they now share is how the one control is
    *fed*, never what either of them writes: creating a Matter and correcting one
    are different transactions with different services and different rules about
    defaults, and §11 of the same brief turns on their staying that way.
    """

    fields: dict[str, forms.Field]

    #: Where the Adressaat radio group stops being chips and starts being tail.
    #: `None` on a form with no viewer — no usage to rank by means no shortlist,
    #: and everything is a chip.
    addressee_split: int | None = None

    @property
    def sender_chip_choices(self) -> list[Any]:
        """The senders offered without being asked."""
        return list(cast(Any, self)["source_organisations"])

    @property
    def sender_tail_choices(self) -> list[Any]:
        """Every other institution, searchable rather than on screen."""
        return list(cast(Any, self)["source_organisations_other"])

    @property
    def addressee_chip_choices(self) -> list[Any]:
        """«Määramata», the chosen senders, and the bodies most often answered."""
        offered = list(cast(Any, self)["addressee_organisation"])
        return offered if self.addressee_split is None else offered[: self.addressee_split]

    @property
    def addressee_tail_choices(self) -> list[Any]:
        if self.addressee_split is None:
            return []
        return list(cast(Any, self)["addressee_organisation"])[self.addressee_split :]


def _typed_organisation_field(label: str, *, hook: str = "") -> forms.CharField:
    """A box for naming an institution the catalogue may not hold yet.

    One definition, two counterparty fields. `Adressaat` has had this since the
    typed-addressee round; `Saatja` gained it when the rule that no institution
    may be created from a Teema form was withdrawn — see
    `resolve_source_organisations` for what that decision was and what replaced
    it. The two remain *different questions* about the same catalogue, so they
    are two fields with two labels and one implementation.

    ``hook`` names the control for the browser. `Uus teema` answers Adressaat
    with a sender somebody is typing before either exists, and the script needs
    to find the two boxes without depending on the auto-generated `id_` that a
    renamed field would change under it. It is an attribute and nothing more: no
    behaviour here reads it, and with scripting off the same default is reached
    on the server instead (`_default_addressee`, static/js/app.js
    `bindAddresseeDefault`).
    """
    attrs = {
        "class": "field__input field__input--compact",
        "autocomplete": "off",
        "placeholder": "Kirjuta asutuse nimi",
    }
    if hook:
        attrs[hook] = ""
    return forms.CharField(
        label=label,
        # `Organisation.name` is 300, so a name this field accepted and the
        # service could not store is not a state either of them can reach.
        max_length=300,
        required=False,
        strip=True,
        widget=forms.TextInput(attrs=attrs),
    )


def sender_name_field() -> forms.CharField:
    """`Saatja`'s typed half, on every surface that captures a sender.

    New, and it replaces a rule rather than a control: this field is what the
    sentence «Kui saatjat siin ei ole, tuleb asutus enne lisada asutuste alla»
    used to stand in for. Leaving a half-filled Teema, navigating to Asutused,
    creating a body and coming back to find it again is not a workflow anybody
    used; they filed the Teema with no sender.

    **It is no longer a visible box on `Uus teema`.** That surface asks one
    question — «Otsi või lisa asutus…» — and this field is what the `+` beside
    it writes into, so the distinction between *finding* an institution and
    *naming* one survives in the posted data without being two controls on the
    screen. `Muuda teemat` and `Saabunud` still render it as an ordinary text
    box, and so does the `<noscript>` fallback (docs/adr/0073, task §2, §22).

    What has not changed is the rule underneath, and it is the reason the field
    still exists at all: the search box posts nothing. Somebody who types
    «Kliima», watches the list narrow to `Kliimaministeerium` and chooses it has
    answered with a row; only pressing `+` says «this is a body you do not
    have», and only that writes here.

    Nothing is created here either way.
    `app.matters.services.resolve_source_organisations` decides what the typed
    name means, inside the save's own transaction.
    """
    return _typed_organisation_field("Uus saatja", hook="data-sender-name")


def addressee_name_field() -> forms.CharField:
    """`Adressaat`'s typed half, on both Teema forms.

    Written once and built twice, because `Uus teema` and `Muuda teemat` must
    offer the same control: a person filing a Teema and a person correcting one
    are answering the same question, and two spellings of one field is two
    workflows to learn (§7).

    Its own field, deliberately, rather than a `name` attribute bolted onto the
    search box. That box is a *filter* over the catalogue: somebody who types
    «Kliima», watches the list narrow to `Kliimaministeerium` and chooses it has
    answered the question, and a control that also posted the four letters left
    in the box would file the Teema against a new institution called «Kliima».
    One field per intention — found a body, or named one that is not here — is
    what makes the difference between them decidable on the server (§10).

    On `Uus teema` the two intentions are now expressed through one visible
    control: the search box, and the `+` attached to it that moves what was
    typed into this field. Two *fields*, one *control* — see
    `sender_name_field` and docs/adr/0073.

    Nothing is created here. `clean_typed_organisation_name` trims and
    length-caps the text; `app.matters.services.resolve_addressee` decides what
    it means, and does so inside the save's own transaction (§5, §6).
    """
    return _typed_organisation_field("Uus adressaat", hook="data-addressee-name")


def clean_typed_organisation_name(value: str | None) -> str:
    """Collapse a typed institution name. Resolve nothing.

    Whitespace is collapsed the way `app.core.text.normalize_for_matching`
    collapses it, so «Majandus-  ja   Kommunikatsiooniministeerium» and the
    canonical spelling reach the resolver as one string and reuse one row.

    Whether that string names an institution that already exists, a new one, or
    two at once is a question about stored state, and a form does not touch
    stored state (§5).
    """
    return " ".join((value or "").split())


#: How many bodies the sender control offers without being asked. Eight is what
#: fits the row at the widths this form is used at, and it is a *target* rather
#: than a maximum on relevance: see `organisations_by_usage`, which fills the
#: eight rather than offering however many happen to have sender history.
SENDER_SHORTLIST_SIZE = 8


def _usage_order(viewer: Any, field: str, limit: int) -> list[Any]:
    """Primary keys of the organisations most used in one direction, best first.

    Scoped by `visible_to`, which is the whole authorization story: a Matter
    this reader may not see contributes nothing to the order they are shown, so
    no ranking can disclose that a restricted file exists or who sent it.
    """
    from app.matters.models import Matter

    usage = (
        Matter.objects.visible_to(viewer)
        .filter(**{f"{field}__isnull": False})
        # Cleared first, then re-ordered by the aggregate. The default ordering
        # would otherwise join the GROUP BY and give every organisation a count
        # of one (see `policy_areas_by_usage`).
        .order_by()
        .values(field)
        # Distinct *Matters* per organisation, which is what makes the plural
        # relation count correctly in both directions. A Matter sent by two
        # bodies contributes one to each of them, and a Matter with three
        # collaborators still contributes one — the sender join and the
        # visibility join both fan out, and `Count(...)` would have counted the
        # rows either of them produced.
        .annotate(total=scoped_count())
        .order_by("-total")[:limit]
    )
    return [row[field] for row in usage]


def organisations_by_usage(
    viewer: Any, *, limit: int = SENDER_SHORTLIST_SIZE
) -> list[Organisation]:
    """The bodies to offer as senders without being asked, best first.

    Three sources, in descending order of how much they actually say about this
    reader's work, and the later ones exist only to *fill* what the earlier ones
    left empty:

    1. **used as a sender on Matters this reader can see**, most often first.
       The real signal, and the only one that is about senders at all.
    2. **used as an addressee on Matters this reader can see.** A weaker signal
       and deliberately a different fact — who writes to Koda and who Koda
       writes to are not the same list — but a ministry this department
       corresponds with is a better guess than the alphabet.
    3. **the catalogue, alphabetically.** Deterministic, so two readers with no
       history see the same eight and neither sees one.

    The layering is the fix for what this used to do. It ranked senders and
    stopped: a department whose new-system records happened to name one sender
    got a "quick choice" row holding exactly one chip, with every other body —
    including the nine it writes to every week — behind a disclosure. Falling
    back only when there was *nothing* at all meant the empty case was handled
    and the nearly-empty case, which is the one a young dataset is actually in,
    was not.

    This is presentation ordering and nothing else. Which bodies are valid
    senders is unchanged (all of them), what a sender *means* is unchanged, and
    no Matter's stored relations are read for anything but the count.

    Authorization is `visible_to` on both usage passes, so a restricted Matter
    cannot move a chip and cannot put a body on this row that the reader would
    otherwise have no reason to see there.
    """
    order: list[Any] = []
    seen: set[Any] = set()

    def take(pks: list[Any]) -> None:
        for pk in pks:
            if pk not in seen:
                seen.add(pk)
                order.append(pk)

    take(_usage_order(viewer, "source_organisations", limit))
    if len(order) < limit:
        # Asked only when the sender history did not fill the row, so a
        # department with eight active senders never pays for this query.
        take(_usage_order(viewer, "addressee_organisation", limit))

    order = order[:limit]
    # One query for the rows, whatever the two passes above found. Ranked in
    # Python rather than in SQL, because the order is the union's and not any
    # single query's.
    found = {
        organisation.pk: organisation for organisation in Organisation.objects.filter(pk__in=order)
    }
    shortlist = [found[pk] for pk in order if pk in found]

    if len(shortlist) < limit:
        shortlist.extend(
            Organisation.objects.exclude(pk__in=[item.pk for item in shortlist]).order_by("name")[
                : limit - len(shortlist)
            ]
        )
    return shortlist


def addressees_by_usage(viewer: Any, *, limit: int = 10) -> list[Organisation]:
    """The bodies this department actually answers to, most frequent first.

    The addressee counterpart of `organisations_by_usage`, and separate from it
    on purpose: who *sends* Koda a file and who Koda *answers* are two different
    facts, and one list standing for both would put the Riigikogu committee that
    never sends anything behind ten ministries that never receive anything.

    `scoped_count` for the same reason the sender list uses it: the visibility
    join fans out over collaborators, and `Count("id")` inside a `GROUP BY`
    would count join rows (app/core/authorization.py).

    Unlike the sender shortlist, this one is not topped up from the other
    direction — and it does not need to be. Adressaat renders the *whole*
    catalogue as one radio group, shortlist first and the rest behind it, so a
    short shortlist moves a body down the page rather than off it. The sender
    row had no such guarantee, which is why the layering lives there.
    """
    ranking = {
        pk: index for index, pk in enumerate(_usage_order(viewer, "addressee_organisation", limit))
    }
    if not ranking:
        return list(Organisation.objects.order_by("name")[:limit])
    found = Organisation.objects.filter(pk__in=ranking)
    return sorted(found, key=lambda organisation: ranking[organisation.pk])


class OrganisationSpellings:
    """A choice widget that writes each institution's other spellings onto it.

    The unified picker searches the catalogue in the browser, and «MKM» has to
    find `Majandus- ja Kommunikatsiooniministeerium` the way
    `app.organisations.services.find_matches` does — through a recorded alias,
    which is somebody's decision that the two name one body rather than a
    similarity score (`app/organisations/services.py` module docstring).

    Aliases are not in the rendered label, so without this they are simply not
    on the page and no amount of client-side cleverness can find them. They
    arrive as ``data-aliases`` on the option's own control, already normalised
    by `OrganisationAlias.save`, so the browser compares normalised text to
    normalised text and never re-implements the normaliser.

    **Presentation only, and deliberately so.** What a typed name *means* —
    reuse, create, or refuse as ambiguous — stays in
    `app.organisations.services.resolve_organisation_name`, inside the save's
    own transaction. This makes a spelling findable; it decides nothing
    (docs/adr/0073, task §21).
    """

    #: Normalised spellings per option value, joined by ``|``. Per form
    #: instance: Django deep-copies fields — and their widgets — in
    #: `BaseForm.__init__`, so assigning this never reaches another request.
    alias_terms: dict[str, str]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.alias_terms = {}

    def create_option(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        option = cast(dict[str, Any], super().create_option(*args, **kwargs))  # type: ignore[misc]
        # `value` is the second positional argument and may arrive either way.
        value = kwargs["value"] if "value" in kwargs else args[1]
        terms = self.alias_terms.get(str(value))
        if terms:
            option["attrs"]["data-aliases"] = terms
        return option


class OrganisationCheckboxSelect(OrganisationSpellings, forms.CheckboxSelectMultiple):
    """Saatja's chips, which are 0..N."""


class OrganisationRadioSelect(OrganisationSpellings, forms.RadioSelect):
    """Adressaat's chips, which are 0..1."""


def organisation_alias_terms() -> dict[str, str]:
    """Every institution's recorded spellings, by primary key, already normalised.

    One query for the whole catalogue rather than one per chip. The picker
    renders every institution — the shortlist visibly, the rest as searchable
    entries — so there is no narrower set to ask for, and `normalized_alias` is
    the column `find_matches` itself searches.

    Nothing here is scoped by viewer and nothing needs to be: an alias is
    reference data about an institution, the same class of fact as its name,
    and every name in this catalogue is already on the page. No Matter is read,
    so no restricted file can reach it (task §28).
    """
    terms: dict[str, list[str]] = {}
    rows = OrganisationAlias.objects.values_list("organisation_id", "normalized_alias")
    for organisation_id, normalized in rows:
        if normalized:
            terms.setdefault(str(organisation_id), []).append(normalized)
    return {key: "|".join(sorted(set(values))) for key, values in terms.items()}


def _raw_value(form: Any, name: str) -> Any:
    """What the request said about one field, before any validation ran.

    The widget's own reader rather than `form.data.get`, for the reason
    a bound form's data is a `QueryDict` from a real POST and an ordinary dict
    from a caller constructing one, and only the widget knows how to read both —
    and how to honour a form prefix.
    """
    field = form.fields[name]
    return field.widget.value_from_datadict(form.data, form.files, form.add_prefix(name))


class MatterCreateForm(LegalInstrumentChoicesMixin, OrganisationPickerChoicesMixin, forms.Form):
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
    #: Visible rather than collapsed because eleven stages fit on two lines, and
    #: for a department of four a select is a click spent finding out what the
    #: options are. If the vocabulary grows past what reads at a glance, a
    #: select is the better control again and this should go back to one.
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
    #: `Menetlusliik` is deliberately absent from this form, and deliberately
    #: not derived either.
    #:
    #: It asked the lawyer to classify the same file a second time. The reviewed
    #: `Õigusakt` vocabulary names *siseriiklik* or *ELi* in the label, so the
    #: distinction the lawyers wanted to keep is readable from the type a Matter
    #: already carries — but that is a reading, not a source. `Matter.track`
    #: says what kind of *procedure* a file is on; it has seven values rather
    #: than two, and no instrument type entails one. A `Seadus` transposing a
    #: directive is a domestic instrument on a `NATIONAL_TRANSPOSITION` track,
    #: so a rule writing `DOMESTIC` from `seadus` would be wrong about precisely
    #: the files the distinction exists for (docs/adr/0090 §4).
    #:
    #: `Matter.track` itself is untouched: the column, the seven values, the
    #: register filter, the reporting projection, the audit events and
    #: `StageVocabulary.applicable_tracks` all stand, and `Muuda teemat` and the
    #: Teema rail still offer the whole vocabulary to somebody correcting a
    #: file. It is answered where it is known, by a person.
    #: `Õigusakt`, directly after `Hetkeseis` and answered independently of
    #: it. Checkboxes rather than the radios above, and the asymmetry is the
    #: whole answer to "do not let these two read as one question split in two":
    #: the count beside the legend and the `×` on each chosen chip appear here
    #: and nowhere on Menetlusliik, so the two rows are visibly different kinds
    #: of control (OIGUSAKT_UUS_TEEMA_DESIGN §4, docs/adr/0070).
    legal_instruments = legal_instruments_field()
    legal_instrument_other = legal_instrument_other_field()
    source_organisations = forms.ModelMultipleChoiceField(
        label="Saatja",
        queryset=Organisation.objects.none(),
        required=False,
        # Checkboxes, because a Matter really can arrive from several bodies at
        # once. This was radios while the model held one sender, and the control
        # was right for the model it had; both moved together (Agent-E brief 28).
        widget=OrganisationCheckboxSelect(attrs={"class": "chip__input"}),
    )
    #: The rest of the catalogue, rendered beside the shortlist rather than
    #: behind a disclosure. The rendered choices exclude the chips above, so the
    #: same body is never offered twice.
    #:
    #: It stopped being a `<details>` when the sender control was reworked: a
    #: door reading «Vali nimekirjast (15)» is a door somebody has to guess is
    #: worth opening, and the search that narrows the catalogue was behind it.
    #: The search is on the page now and this is what it filters — bounded and
    #: scrollable, so a catalogue that grows does not become a wall
    #: (static/css/app.css `.chiplist`).
    #:
    #: The queryset stays the whole catalogue. Validation must accept an
    #: organisation this reader's shortlist happens to contain, or a POST from a
    #: colleague with a different history would be refused as invalid.
    source_organisations_other = forms.ModelMultipleChoiceField(
        label="Muu saatja",
        queryset=Organisation.objects.none(),
        required=False,
        widget=OrganisationCheckboxSelect(attrs={"class": "chip__input"}),
    )
    #: Saatja's typed half. The same contract Adressaat has had since the typed
    #: addressee round, on the field that was explicitly denied it — see
    #: `app.matters.services.resolve_source_organisations` for the decision that
    #: replaced «teema vormilt uut asutust ei teki».
    sender_name = sender_name_field()
    #: `Adressaat` is deliberately absent from this form, and so is everything
    #: that used to answer it.
    #:
    #: A file arriving is one counterparty question, not two. The lawyers
    #: reported being asked who to answer before anybody had decided to answer
    #: anything — and the field arrived pre-filled with the sender, which is the
    #: same body under a second label (docs/adr/0069, now superseded on this
    #: surface). `addressee_organisation`, `addressee_name` and the
    #: `addressee_is_manual` hidden field that made the default overridable are
    #: all gone from `Uus teema` together, because a default with nothing to
    #: default and a manual-override marker with nothing to override are the
    #: kind of remainder that looks like a feature (docs/adr/0090 §5).
    #:
    #: **`Adressaat` itself is untouched.** It is a different fact from Saatja
    #: and the two are never merged: Koda answers a ministry, the Riigikogu or
    #: an EU institution, and which of them is not derivable from who wrote in.
    #: The column, the relation, the audit events, the `update_field` endpoint,
    #: the Teema rail's `Kellele` row, `Muuda teemat`, the submission workflow
    #: and every Matter already carrying one are all as they were. What is gone
    #: is the question on the capture screen.
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

        clean_legal_instrument_answer(self, cleaned)

        return cleaned

    def clean_sender_name(self) -> str:
        return clean_typed_organisation_name(self.cleaned_data.get("sender_name"))

    @property
    def policy_area_summary(self) -> str:
        """The Valdkonnad this form currently holds, as labels to read.

        What the collapsed Valdkond disclosure says after the word itself:
        «Valdkonnad · Ehitus, Keskkond», or «Valdkonnad» when nothing is chosen.
        The whole argument for folding the vocabulary away is that a shut field
        is quieter than twenty-two chips; a shut field that also hid *the
        answer* would be quieter and worse, because then it has to be opened
        every time to find out (docs/adr/0091 §3).

        Read off the rendered choices rather than by fetching the rows, for the
        reason `addressee_summary` reads off its own: the catalogue is already
        on the page as `(pk, name)` pairs and a query per render buys nothing.

        `Muu` is included by name when it is ticked, because it *is* an answer
        here — it is the affordance that reveals the free-text box, and a
        summary reading «Valdkonnad» over a ticked `Muu` and a sentence of typed
        text would be wrong about the one state somebody has to come back to.

        Unbound — the ordinary first visit — is empty by construction: nothing
        is chosen, so there is nothing to say.
        """
        if not self.is_bound:
            return ""
        # `_raw_value` and not `form.data.getlist`: a `CheckboxSelectMultiple`
        # already knows how to read its own many-valued answer out of a
        # `QueryDict` or an ordinary dict, and asking the widget is what keeps
        # this working on a form a caller constructed by hand.
        chosen = {str(value) for value in (_raw_value(self, "policy_areas") or [])}
        # `fields[...]` is typed as the base Field, which has no `choices`. This
        # one is a ModelMultipleChoiceField by construction.
        names = [
            str(label)
            for value, label in cast(Any, self.fields["policy_areas"]).choices
            if str(value) in chosen
        ]
        if _raw_value(self, "policy_area_other_selected"):
            names.append(str(self.fields["policy_area_other_selected"].label))
        return ", ".join(names)

    @property
    def policy_area_disclosure_open(self) -> bool:
        """Whether the Valdkond disclosure renders open.

        Server-decided and server-rendered, so a browser with scripting off gets
        the same page: a refusal to read, or a `Muu` whose free-text box is
        inside the fold and has to be reachable.

        Deliberately *not* opened merely by an answer being present. A refused
        save that comes back with two areas ticked says so in the summary, and
        unfolding the vocabulary to prove it would undo the whole change on the
        one path where somebody is already being asked to fix something else
        (task §11 C).
        """
        if not self.is_bound:
            return False
        if self.errors.get("policy_areas") or self.errors.get("policy_area_other"):
            return True
        return bool(_raw_value(self, "policy_area_other_selected"))

    @property
    def data_class(self) -> str:
        """Ordinary `Uus teema` creates real work. There is no other answer.

        There used to be a «Testandmed» checkbox here and this read it. Both are
        gone, and the removal is the point rather than a side effect: the field
        existed so that somebody generating demonstration records could mark
        them, and it was standing on the one page a lawyer uses every day, where
        the only thing it could do was be ticked by mistake.

        **A forged POST cannot bring it back.** This is a constant, not a hidden
        input and not a default that a stray `is_test_data=on` could override —
        the form has no such field to bind, so the parameter is simply not part
        of the request as far as this form is concerned, and the value written
        is the same either way. A test asserts exactly that (task §16).

        Nothing downstream changed. `Matter.data_class` still exists, the enum
        still has both values, the historical TEST records still carry theirs,
        REAL/TEST reporting filters still split on it and the purge tooling
        still finds them. What no longer exists is a way to *create* TEST work
        from the ordinary capture path — which is where synthetic fixtures and
        the seeding commands write it directly, as they always have
        (app/matters/services.py `set_matter_data_class`, docs/adr/0067).
        """
        return MatterDataClass.REAL

    def __init__(self, *args: Any, viewer: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.viewer = viewer

        # Nothing reads the senders before validation any more. They were read
        # here so that Adressaat could be answered from them and its shortlist
        # ordered by them; `Uus teema` no longer asks the question, so the read,
        # the default and the hidden override marker went with it
        # (docs/adr/0090 §5).

        # New work, and only new work: this form has no existing owner to
        # preserve, so the population is the current department workers with no
        # union (app/accounts/selectors.py).
        set_choices(self, "owner", assignable_users())
        set_choices(self, "stage", active_stages())

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
        # The active vocabulary, in the department's reviewed order. New work is
        # filed under what is offered today; the edit form is the one that also
        # has to accept what a Matter already carries.
        set_choices(self, "legal_instruments", selectable_legal_instrument_types())
        self.offer_legal_instruments(list(selectable_legal_instrument_types()))

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
            # The rest of the catalogue, beside the chips rather than behind a
            # door. Offering the same bodies twice is what made "Muu / lisa
            # saatja" read as a second sender control that contradicted the
            # first, so the shortlist is excluded here.
            #
            frequent = {organisation.pk for organisation in self.frequent_senders}
            rest = cast(Any, self.fields["source_organisations_other"])
            tail = [
                organisation
                for organisation in Organisation.objects.order_by("name")
                if organisation.pk not in frequent
            ]
            rest.choices = [(organisation.pk, organisation.name) for organisation in tail]
            # `sender_tail_count` is back, and so is the door it labels.
            #
            # It was removed when the catalogue came out from behind the
            # disclosure and onto the page, on the argument that a door reading
            # «Vali nimekirjast (15)» is a door somebody has to guess is worth
            # opening. What that traded away was the shape of the row: a
            # permanent search box and a scrolling catalogue sat in the Saatja
            # column on every visit, including the overwhelming majority where
            # the answer was one of the chips already on screen.
            #
            # Adressaat kept the disclosure and read better for it, so Saatja
            # matches it: chips first, the whole catalogue and its search one
            # click away, `Uus saatja` outside where it answers "the body I need
            # is not here" without anything having to be opened
            # (task §9, matter_create.html). Adressaat itself is no longer on
            # this form; the shape it argued for is (docs/adr/0090 §5).
            self.sender_tail_count = len(tail)

            # The recorded spellings, onto the controls that carry them.
            #
            # Read once for the whole catalogue and handed to both sender
            # fields, because the picker searches one pool of institutions and
            # «MKM» has to find the same ministry from either of them
            # (docs/adr/0073, task §13).
            spellings = organisation_alias_terms()
            for field_name in ("source_organisations", "source_organisations_other"):
                cast(Any, self.fields[field_name].widget).alias_terms = spellings
        else:
            self.frequent_senders = []
            self.sender_tail_count = 0


class MatterEditForm(LegalInstrumentChoicesMixin, OrganisationPickerChoicesMixin, forms.Form):
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
    #: The same control `Uus teema` carries, because a canonical Matter fact
    #: that could only be answered at creation time would be a fact nobody could
    #: correct — and the two pages are one job seen twice (task §18).
    legal_instruments = legal_instruments_field()
    legal_instrument_other = legal_instrument_other_field()
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
    #: `OrganisationCheckboxSelect`, not a plain one: the widget is what writes
    #: each institution's recorded spellings onto its own control, and without
    #: them «MKM» finds nothing on this page while finding the ministry on `Uus
    #: teema`. One control means one search behaviour (post-QA R2-12).
    #:
    #: **`Saatja`, not `Kellelt`.** One fact had two names: `Uus teema` asked
    #: for `Saatja` and this page and the Teema rail answered with `Kellelt`, so
    #: «kes selle meile saatis?» read as two questions on three screens. The
    #: field, the relation, the widget and the search are unchanged; the word
    #: is now the one word (docs/adr/0090 §6).
    source_organisations = forms.ModelMultipleChoiceField(
        label="Saatja",
        queryset=Organisation.objects.none(),
        required=False,
        widget=OrganisationCheckboxSelect(attrs={"class": "chip__input"}),
    )
    #: The rest of the catalogue, exactly as `Uus teema` splits it. Two fields
    #: rather than one because a checkbox group cannot be split without
    #: splitting the field; `clean` unions them back into one answer.
    source_organisations_other = forms.ModelMultipleChoiceField(
        label="Muu saatja",
        queryset=Organisation.objects.none(),
        required=False,
        widget=OrganisationCheckboxSelect(attrs={"class": "chip__input"}),
    )
    #: And Saatja's typed half, so correcting a Teema offers what filing one
    #: does. A person who learns one sender workflow must not find a different
    #: one on the next screen (§2E).
    sender_name = sender_name_field()
    addressee_organisation = forms.ModelChoiceField(
        label="Kellele",
        queryset=Organisation.objects.none(),
        required=False,
        empty_label="Määramata",
        blank=True,
        widget=OrganisationRadioSelect(attrs={"class": "chip__input"}),
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
        # The one explanation, shared with the Teema header and the banner.
        # This line used to say "ainult vastutaja ja osalejad", which promised a
        # narrower audience than the application has given since docs/adr/0042
        # (pilot QA F-01, app/core/visibility_help.py).
        help_text=RESTRICTED_VISIBILITY_HELP,
    )

    def __init__(
        self,
        *args: Any,
        matter: Matter | None = None,
        viewer: Any = None,
        suggested_senders: Sequence[Organisation] = (),
        **kwargs: Any,
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

        # The offered vocabulary *plus* the stage this Matter already holds —
        # the shape `owner` uses one line up and `policy_areas` uses below, for
        # the same reason. `Hetkeseis` is optional, so narrowing this to the
        # active rows would not even fail loudly on a Matter left holding a
        # retired stage: the unchanged value would simply be refused, and a
        # title correction would clear a stage nobody asked to remove
        # (app/workflow/selectors.py, docs/adr/0032 §Amendment).
        offered_stages = stages_including_held(matter)
        set_choices(self, "stage", offered_stages)

        # The same per-stage explanations `Uus teema` shows, on the same
        # control. One mapping, two forms: a sentence written twice is a
        # sentence that stops matching (app/workflow/selectors.py).
        #
        # Read over the *offered* list rather than the active one, so the
        # retired chip below keeps the department's sentence about it.
        self.stage_help = stage_help_texts(offered_stages)
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
        # And whatever the document analysis proposes as a sender, so the
        # assisted review can show it as a chip beside the frequent ones rather
        # than leaving it inside the long tail where a pre-filled tick would be
        # invisible (app/matters/intake_suggestions). Offered, not chosen:
        # this list decides which chips render, never which are ticked.
        for organisation in (*current_senders, *suggested_senders):
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
        self.addressee_split = 1 + len(shortlist)
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

        # The recorded spellings, onto the controls that carry them.
        #
        # Read once for the whole catalogue and handed to all three choice
        # fields, exactly as `Uus teema` does it: the picker searches one pool
        # of institutions through two questions, and «MKM» has to find the same
        # ministry whichever of them is being answered — and on whichever of the
        # two pages is asking (docs/adr/0073 task §13, post-QA R2-12).
        spellings = organisation_alias_terms()
        for field_name in (
            "source_organisations",
            "source_organisations_other",
            "addressee_organisation",
        ):
            cast(Any, self.fields[field_name].widget).alias_terms = spellings

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

        # Õigusakt, the same way and for the same reason: validation accepts the
        # whole vocabulary so a Matter carrying a since-retired type does not
        # lose it to an unrelated correction, and the *offered* list is the
        # active one plus whatever this Matter already holds.
        set_choices(self, "legal_instruments", LegalInstrumentType.objects.all())
        instruments = list(selectable_legal_instrument_types())
        if matter is not None:
            seen = {item.pk for item in instruments}
            instruments += [item for item in matter.legal_instruments.all() if item.pk not in seen]
        self.offer_legal_instruments(instruments)
        #: The retired areas this Matter carries. The template says so rather
        #: than showing a ticked box that looks like every other one.
        self.retired_area_ids = {
            area.pk for area in offered if not getattr(area, "is_active", True)
        }
        #: And the same for `Hetkeseis`, which is one value rather than a set.
        #: A retired stage is offered back — that is the whole point — but it is
        #: not an ordinary chip, and a control that presented it as one would be
        #: inviting a second Matter's worth of new work into a stage the
        #: department has stopped using.
        #:
        #: Read off the Matter rather than by filtering the offered list, which
        #: would be a second query over the same eleven rows: `stages_including`
        #: adds exactly one row to the active vocabulary, so the only stage that
        #: can be retired *and* offered here is the one this Matter holds.
        #: A set of at most one, because that is the shape the template's `in`
        #: test takes for the areas beside it.
        held_stage = matter.stage if matter else None
        self.retired_stage_ids = (
            {held_stage.pk} if held_stage is not None and not held_stage.is_active else set()
        )

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
        # One rule about what `Muu` means, shared with `Uus teema`. A rule that
        # is true on the page somebody files from and false on the page they
        # correct from is a rule people have to learn twice.
        clean_legal_instrument_answer(self, cleaned)
        return cleaned

    def clean_addressee_name(self) -> str:
        return clean_typed_organisation_name(self.cleaned_data.get("addressee_name"))

    def clean_sender_name(self) -> str:
        return clean_typed_organisation_name(self.cleaned_data.get("sender_name"))

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
        "legal_instruments": [item.pk for item in matter.legal_instruments.all()],
        "legal_instrument_other": matter.legal_instrument_other,
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
    """`Järgmiseks` and `Millal?`, and nothing else asked about the next step.

    Two questions, in the words a lawyer already uses: what they will do, and
    the day they will do it. There is no action kind and no date meaning on
    this form, because a next step recorded natively is always `DO` /
    `DEADLINE` / `EXACT` — the date means *the day this gets done*, which is
    exactly what that combination already says (ADR 0052 §3).

    **The classification left the contract, rather than hiding in the
    template.** `kind` and `date_semantics` are not fields, so a crafted POST
    naming `WAIT` or `EXPECTED_AROUND` arrives as an unknown key and changes
    nothing. Hiding two inputs while still reading them would have left the
    page teaching a vocabulary the workflow retired *and* the endpoint
    accepting it.

    `ActionKind` and `DateSemantics` keep every value they had. Historical and
    imported `WAIT` and `MONITOR` rows are untouched, the register's parser
    still records the pairs its sources name, and `Minu töö`, the register and
    the timeline still say which kind a step is. This form simply stopped
    asking a person to pick one (ADR 0052 §1).

    `use_required_attribute` is off, and that is not cosmetic. Setting a next
    action is optional — the view binds this form only when somebody wrote
    something — and with the HTML `required` attribute present a browser
    refuses to submit a form containing an invalid control, reports nothing,
    and the "Loo teema" button silently does nothing (Stage-2E.1 brief 26).

    The server-side requirement is unchanged in substance: a partly filled next
    action is still refused, and it is refused on the half that is empty.
    """

    use_required_attribute = False

    #: What happens next, in the lawyer's own words, stored exactly as typed.
    #:
    #: The label is the column's own name. It used to be "Mida järgmisena teed
    #: või ootad?", a question that existed to explain `Järgmiseks` to a reader
    #: who had never met it — and which carried the retired classification back
    #: into the UI through the word *ootad*. `Järgmiseks` over a concrete
    #: placeholder says the same thing without naming a mode nobody chooses any
    #: more (ADR 0052 §2).
    #:
    #: Optional at the field, refused in `clean`. Whether a next action was
    #: requested at all is a question about the whole block, and answering it
    #: field by field is how "See lahter on nõutav." ends up under a control
    #: nobody touched.
    text = forms.CharField(
        label="Järgmiseks",
        required=False,
        max_length=2000,
        widget=forms.TextInput(
            attrs={
                "class": "field__input uxcomp__next",
                "placeholder": "Näiteks: Vaadata uus eelnõu versioon üle",
            }
        ),
    )
    #: **The day the work will be done**, and nothing more subtle than that.
    #:
    #: No `initial`. It used to default to today, on the reasoning that today
    #: is the answer nearly every time — but a blank new-Teema form silently
    #: holding today is a factual claim the person never made, and it turned
    #: "you forgot the date" into a date nobody chose. Deciding when somebody
    #: will do their own work is not the application's to decide (ADR 0052 §5).
    #:
    #: There **is** a precision group behind it, added in this round.
    #:
    #: ADR 0052 §4 deleted one, on the reasoning that «a lawyer's own working
    #: day is a day». That is right about the common case, and `Täpne päev` is
    #: still the default with the quick spans untouched in front of it. It is
    #: wrong about the case that brought the control back: a step that genuinely
    #: belongs *in October* left the person choosing between inventing the 1st
    #: and leaving the field empty, and the 1st is a claim they never made
    #: (docs/adr/0079 §1).
    #:
    #: The field keeps its name. Four quick spans write into `target_date` by
    #: name, and renaming it to `next_date` for tidiness would unhook them in a
    #: way that still looked right on the page.
    target_date = EstonianDateField(label="Millal?", required=False, widget=DATE_WIDGET)
    #: Kept, and still not rendered on Uus teema.
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
        #: The open step this save replaces, when there is one.
        #:
        #: Read for exactly one thing: whether it carries a precision this
        #: control cannot offer, so that editing its sentence does not rewrite
        #: *II poolaasta 2027* into a day (docs/adr/0079 §9). Uus teema passes
        #: nothing, because there is no step yet and no Matter either.
        self.current = kwargs.pop("current", None)
        #: Whether this host renders the `Täpsus` control.
        #:
        #: Default **off**, and the two Teema-page call sites turn it on. The
        #: rule is ADR 0052 §4's and it is why the retired group was deleted
        #: rather than hidden: a control the page does not have must not be
        #: reachable through a crafted POST either. Uus teema asks for a title,
        #: an owner and a first step on one screen and does not offer four
        #: precision chips inside that; adding the fields anyway would make
        #: `next-next_precision=MONTH` work on a form with no such control
        #: (docs/adr/0079 §1).
        self.periods = kwargs.pop("periods", False)
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
        # The precision group, minus the date box: `target_date` above is the
        # exact-day control and keeps its name for the quick spans' sake.
        if self.periods:
            kept = kept_precision_choice(
                getattr(self.current, "target_date", None),
                getattr(self.current, "date_precision", "") or "",
            )
            self.precision_choices = precision_choices(kept)
            group = _precision_fields("next", date_label="Millal?", kept=kept)
            # The date box is `target_date` above, which keeps its name for the
            # quick spans' sake.
            del group["next_date"]
            self.fields.update(group)

    @property
    def precision_chips(self) -> list[dict[str, Any]]:
        """The `Täpsus` radios, as the template renders every other chip row."""
        return _precision_chips(self, "next_precision")

    def clean(self) -> dict[str, Any]:
        """`Järgmiseks` + `Millal?`, together or not at all.

        The same three outcomes as the Teema composer, and deliberately the
        same wording: one half filled is refused **on the empty control**,
        because "vali kuupäev" pinned to the sentence box points at the wrong
        field (ADR 0052 §5).

        Nothing is defaulted. An undated step is never quietly filed for today,
        and a dated step with no sentence is never quietly discarded — somebody
        who pressed `Homme` and then wrote nothing did ask for a next action,
        and gets told which half is missing rather than getting a Teema with no
        step in it.

        This form is bound only when one of the two halves carries something,
        so both arriving empty means an empty POST to `set_action` rather than
        a title-only Teema. That is a refusal too, and it belongs on the
        sentence.
        """
        cleaned = super().clean() or {}
        text = (cleaned.get("text") or "").strip()

        if not text:
            self.add_error("text", "Kirjuta järgmine tegevus.")
            return cleaned

        anchor, precision = self._chosen_period()
        if anchor is None:
            # The refusal lands on whichever control the chosen precision asks
            # for — `_period_anchor` has already put one there when a month or a
            # year was malformed, and this is the remaining case: the chosen
            # precision's field was simply left alone.
            if not self.errors:
                self.add_error(
                    _precision_answer_field("next", precision)
                    if self.periods and precision in _PRECISION_FIELD_SUFFIX
                    else "target_date",
                    "Vali järgmise tegevuse kuupäev.",
                )
            return cleaned
        cleaned["next_anchor"] = anchor
        cleaned["next_precision_value"] = precision
        return cleaned

    def _chosen_period(self) -> tuple[date | None, str]:
        """The anchor and precision this save means, from what was chosen.

        Two answers, and the second one is the whole of docs/adr/0079 §9.

        **The «Muutmata» chip keeps the record's own values.** Nothing is
        recomputed and nothing was displayed: a step imported as *II poolaasta
        2027* has no `Poolaasta` control to read, so the honest way to keep it
        is to keep it — read the anchor off the record, not off a form that was
        never shown the period. Choosing one of the four offered chips instead
        replaces it, which is the only way it changes.

        The chip is only in `choices` when the record being replaced actually
        carries that precision, so this cannot be reached by a POST naming
        `HALF_YEAR` on a record that never had one.
        """
        if not self.periods:
            # A host with no `Täpsus` control states an exact day, which is what
            # this form always did (ADR 0052 §3).
            return self.cleaned_data.get("target_date"), DatePrecision.EXACT.value
        precision = self.cleaned_data.get("next_precision") or DatePrecision.EXACT.value
        if precision not in _OFFERED_PRECISIONS:
            kept = getattr(self.current, "target_date", None)
            return kept, precision
        anchor, _, precision = _period_anchor(self, "next", date_field="target_date")
        return anchor, precision

    def as_service_kwargs(self, *, default_responsible: Any = None) -> dict[str, Any]:
        """What ``set_next_action`` needs.

        The kind and the date meaning are the canonical compatibility values,
        written here and never read from the POST. They are honest rather than
        merely convenient: on this surface the date is the day the work gets
        done, and a lawyer who has to remember to chase a ministry writes that
        as an action — «Kontrollida, kas ministeerium vastas» — rather than as a
        workflow classification (ADR 0052 §1, §3).

        **The precision now comes from the person**, which is the half of §3
        docs/adr/0079 supersedes. The stored date is the anchor `bounds_for`
        computed — the first day of whatever period they named — and the
        precision travels with it so that nothing ever prints the anchor as a
        day somebody chose.

        ``default_responsible`` is for the one caller that has an owner the
        service cannot see yet: Uus teema chooses the Matter's owner on the same
        form as the next action, and the Matter does not exist when this is
        read. Everywhere else the service's own fallback to ``matter.owner``
        already does this, and passing nothing keeps that behaviour exactly.
        """
        return {
            "text": self.cleaned_data["text"].strip()[:2000],
            "kind": ActionKind.DO,
            "date_semantics": DateSemantics.DEADLINE,
            "target_date": self.cleaned_data.get("next_anchor"),
            "date_precision": self.cleaned_data.get("next_precision_value")
            or DatePrecision.EXACT.value,
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

#: What a person may choose when stating a date they are recording now.
#:
#: Four, and the labels are the four things somebody actually knows: a day, a
#: month, a quarter, a year. `DatePrecision` keeps six values and every one of
#: them still stores, renders and compares correctly — this tuple decides what
#: is *offered*, which is a different question (docs/adr/0079 §1).
#:
#: `HALF_YEAR` is left out because the department does not plan in halves; the
#: register's own vocabulary does, so the value stays and historical rows keep
#: reading correctly. `INFERRED` is left out because it is provenance — *this
#: day was read out of a sentence* — and is not something a person choosing a
#: precision can truthfully assert about their own answer (§7, §8).
OFFERED_PRECISION_CHOICES: tuple[tuple[str, str], ...] = (
    (DatePrecision.EXACT.value, "Täpne päev"),
    (DatePrecision.MONTH.value, "Kuu"),
    (DatePrecision.QUARTER.value, "Kvartal"),
    (DatePrecision.YEAR.value, "Aasta"),
)

#: The precisions whose answer is a single day, and which therefore need the
#: date box rather than a period select.
_DAY_PRECISIONS: tuple[str, ...] = (DatePrecision.EXACT.value, DatePrecision.INFERRED.value)

#: Which control a precision's own answer lives in, after the prefix. A refusal
#: has to land on the field the reader was asked to fill, not on the date box
#: they deliberately left alone.
_PRECISION_FIELD_SUFFIX: dict[str, str] = {
    DatePrecision.MONTH.value: "month",
    DatePrecision.QUARTER.value: "quarter",
    DatePrecision.HALF_YEAR.value: "half",
    DatePrecision.YEAR.value: "year",
}


def _precision_answer_field(prefix: str, precision: str) -> str:
    """The control whose emptiness is the reason a period could not be built."""
    suffix = _PRECISION_FIELD_SUFFIX.get(precision)
    return f"{prefix}_date" if suffix is None else f"{prefix}_{suffix}"


_OFFERED_PRECISIONS: frozenset[str] = frozenset(value for value, _ in OFFERED_PRECISION_CHOICES)


def precision_choices(kept: tuple[str, str] | None = None) -> tuple[tuple[str, str], ...]:
    """The four offered precisions, plus a record's own where it has one."""
    return OFFERED_PRECISION_CHOICES if kept is None else (kept, *OFFERED_PRECISION_CHOICES)


def _precision_chips(form: forms.Form, name: str) -> list[dict[str, Any]]:
    """One `Täpsus` control's radios, ready to render as chips.

    **Real radios, not buttons writing into a hidden input.** The hidden-input
    pattern this replaces could not state a precision at all with scripting off:
    the buttons did the choosing, so a reader without JavaScript was locked to
    whatever the hidden field already held. A radio group is the browser's own
    answer to «one of these», it arrives in the POST on its own, it carries
    arrow-key navigation and a focus ring for free, and the panel around it
    already uses exactly this pattern for the same reason (docs/adr/0078).

    The choices come from ``form.precision_choices`` — the same tuple that built
    the field — rather than from the field's own ``choices``, because a form
    carrying the «Muutmata» chip has five and every other form has four, and a
    control whose chips and whose validation read two different lists is a
    control that can offer something it then refuses.

    Read through the bound field when bound rather than from ``cleaned_data``:
    the save that most needs its chips back is the one that did not validate.
    """
    choices = getattr(form, "precision_choices", OFFERED_PRECISION_CHOICES)
    bound = form[name]
    chosen = str(bound.value() or choices[0][0])
    return [
        {
            "value": value,
            "label": label,
            "selected": value == chosen,
            "id": f"{bound.auto_id}_{value.lower()}",
            "name": bound.html_name,
        }
        for value, label in choices
    ]


def period_initial(
    prefix: str, value: date | None, precision: str, *, date_field: str | None = None
) -> dict[str, Any]:
    """One stored period, as the initial values its composer renders.

    The inverse of `_period_anchor`: given what the database holds, reopen the
    control on the answer somebody actually gave. An editor that opened on
    `Täpne päev` showing `01.10.2026` for a step recorded as *oktoober 2026*
    would invite a person to save the invented day back — which is the defect
    this round exists to remove, arriving through the edit path instead of the
    create one (docs/adr/0079 §3).
    """
    if value is None:
        return {}
    initial: dict[str, Any] = {f"{prefix}_precision": precision}
    if precision not in _OFFERED_PRECISIONS:
        # Only the «Muutmata» chip, selected. No control is filled in and none
        # is shown: the record's period is kept from the record itself, and
        # putting its anchor into a date box would be this product writing
        # `01.07.2027` on a screen for a fact that says *II poolaasta 2027*
        # (docs/adr/0079 §2, §9).
        return initial
    if precision in _DAY_PRECISIONS:
        initial[date_field or f"{prefix}_date"] = value
        return initial
    initial[f"{prefix}_year"] = value.year
    if precision == DatePrecision.MONTH.value:
        initial[f"{prefix}_month"] = str(value.month)
    elif precision == DatePrecision.QUARTER.value:
        initial[f"{prefix}_quarter"] = str((value.month - 1) // 3 + 1)
    return initial


def kept_precision_choice(value: date | None, precision: str) -> tuple[str, str] | None:
    """The extra chip a record carrying an unoffered precision earns.

    ``None`` for everything the control already offers, which is every record a
    person created through this product. A row imported as *II poolaasta 2027*,
    or one whose day the register's parser read out of a sentence, gets a fifth
    chip naming what it holds — selected, so that editing the sentence beside it
    keeps the date exactly as it was (docs/adr/0079 §9).

    The reader can still choose one of the four and replace it. What they cannot
    do is replace it *by accident*, which is what a control that silently
    reported `EXACT` for a half-year would arrange.
    """
    if value is None or precision in _OFFERED_PRECISIONS:
        return None
    return (precision, f"Muutmata: {format_at_precision(value, precision)}")


MONTH_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (str(number), name.capitalize()) for number, name in enumerate(ESTONIAN_MONTHS, start=1)
)
QUARTER_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (str(number), f"{numeral} kvartal") for number, numeral in enumerate(ROMAN_QUARTERS, start=1)
)
HALF_CHOICES: tuple[tuple[str, str], ...] = (("1", "I poolaasta"), ("2", "II poolaasta"))

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

#: `Kuidas lõppes` — the three outcomes the approved target offers, in its order
#: and with its words.
#:
#: Three chips over one stored vocabulary, not a new one. `Jõustus` is the
#: closure the register has always spelled «Lõpetatud või jõustunud»;
#: `Menetlus lõppes` is the draft or initiative being dropped upstream; and
#: `Loobuti` is Koda deciding to stop. `RESPONSE_COMPLETE`,
#: `NO_POSITION_FORMED`, `DUPLICATE`, `SUPERSEDED` and `OTHER` remain valid
#: stored dispositions with no chip — every one of them still reads, still
#: filters and still reports (docs/adr/0074 §10).
COMPOSER_CLOSURE_CHOICES: tuple[tuple[str, str], ...] = (
    (Disposition.COMPLETED.value, "Jõustus"),
    (Disposition.INITIATIVE_WITHDRAWN.value, "Menetlus lõppes"),
    (Disposition.MONITORING_STOPPED.value, "Loobuti"),
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


def _precision_controls(prefix: str, exact_name: str) -> dict[str, str]:
    """Which control in a precision group answers each precision.

    So that a refusal lands on the box the person actually typed into: ADR 0052
    §5's rule, and the reason «vali kuupäev» pinned to a sentence box points at
    the wrong field. `_period_anchor` reports a half-stated period through this
    map, and `ProceduralDevelopmentForm` reports a future one through the same
    one — two refusals about one control, aimed the same way.
    """
    return {
        DatePrecision.EXACT.value: exact_name,
        DatePrecision.INFERRED.value: exact_name,
        DatePrecision.MONTH.value: f"{prefix}_month",
        DatePrecision.QUARTER.value: f"{prefix}_quarter",
        DatePrecision.HALF_YEAR.value: f"{prefix}_half",
        DatePrecision.YEAR.value: f"{prefix}_year",
    }


def _period_anchor(
    form: forms.Form, prefix: str, *, date_field: str | None = None
) -> tuple[date | None, date | None, str]:
    """Turn one prefixed precision group into an anchor, an end and a precision.

    Four surfaces carry one of these groups — the next step, an important
    deadline, a commencement and a work victory — and the composer carries two
    at once, so they cannot each be a `PeriodForm`. What they share instead is
    `app.workflow.dates.bounds_for`, which is the thing that actually matters: a
    quarter entered on any of them must produce the same stored anchor, or the
    same period would sort into two places (Stage-2G brief 49).

    ``date_field`` names the exact-day control when it is not ``<prefix>_date``.
    `Järgmine tegevus` keeps its own `target_date`, because the quick spans
    (`Täna`, `Homme`, `+1 nädal`) write into that field by name and renaming it
    would silently unhook four controls that still looked right (ADR 0052 §4).

    **The person's own answer, and no other.** An earlier reading filled a
    missing month or quarter in from the day in the date box, because the
    compact `+ Oluline tähtaeg` panel offered no month select to fill in
    (docs/adr/0074 §11). Every surface now renders the control its chosen
    precision needs, so a blank one is a question the person did not answer, and
    guessing *III kvartal* from a day somebody typed and then stopped believing
    is exactly the invented certainty this round exists to remove
    (docs/adr/0079 §1).
    """
    exact_name = date_field or f"{prefix}_date"

    def value(name: str) -> int | None:
        raw = form.cleaned_data.get(f"{prefix}_{name}")
        return int(raw) if raw not in (None, "") else None

    precision = form.cleaned_data.get(f"{prefix}_precision") or DatePrecision.EXACT.value
    exact = form.cleaned_data.get(exact_name)
    if precision in _DAY_PRECISIONS and exact is None:
        return None, None, precision

    field_for_precision = _precision_controls(prefix, exact_name)
    derived_year = value("year")
    derived_month = value("month")
    derived_quarter = value("quarter")
    derived_half = value("half")
    try:
        start, end = bounds_for(
            precision,
            exact_date=exact,
            year=derived_year,
            month=derived_month,
            quarter=derived_quarter,
            half=derived_half,
        )
    except InvalidPeriod as error:
        form.add_error(field_for_precision.get(precision, exact_name), str(error))
        return None, None, precision
    return start, end, precision


def _precision_fields(
    prefix: str, *, date_label: str, kept: tuple[str, str] | None = None
) -> dict[str, forms.Field]:
    """One precision group, named for the thing it dates.

    Built rather than declared because four surfaces need the same group under
    four prefixes, and copying twenty lines is how the second copy stops
    matching the first.

    ``kept`` is the one-record exception of docs/adr/0079 §9: ``(value, label)``
    for a stored precision this control does not otherwise offer. A `Järgmiseks`
    imported as *II poolaasta 2027* must survive somebody fixing a typo in its
    sentence, and the honest way to do that is to show the reader what will be
    kept and let them replace it — not to carry the old value in a hidden input
    they cannot see, and certainly not to rewrite it to *juuli 2027* because
    another field on the form was edited.

    It is added **per instance, from the record being edited**, so the choice
    field refuses a crafted POST naming `HALF_YEAR` on a record that never had
    one.
    """
    choices = precision_choices(kept)
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
            choices=choices,
            initial=choices[0][0],
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


#: The prefix the two `Kaasamine` forms carry their precision group under.
#:
#: One name, because the panel that creates a consultation and the form that
#: corrects one must read the same POST keys — a person who states *oktoober
#: 2026* in `+ Kaasamine` and then opens `Muuda` has to find the same control
#: holding the same answer.
#: **`Tagasisidet ootame kuni` has no default any more**, which is why nothing
#: here computes one.
#:
#: docs/adr/0086 §2 opened the box on today + 7 and this module held the seven.
#: docs/adr/0091 §2 empties the box: recording that Koda asked somebody something
#: is a completed act, and a pre-filled reply-by date turned every such act into a
#: managed wait with a work item and a second act to end it — work the application
#: was assigning rather than work a lawyer had taken on (lawyer feedback 11).
#:
#: The spans beside the box are unchanged and are where the week now lives:
#: `FEEDBACK_DEADLINE_SPANS` in `app/matters/views.py` resolves `1 nädal`,
#: `2 nädalat` and `1 kuu` on the server in Europe/Tallinn, so asking for a wait
#: still costs one click. What went is the value nobody chose.


#: The prefix both `Väline seisukoht` forms carry their precision group under.
#:
#: One name, because the panel that records a position and the form that
#: corrects one must read the same POST keys — a person who states *oktoober
#: 2026* in `+ Väline seisukoht` and then opens `Muuda` has to find the same
#: control holding the same answer. `ENGAGEMENT_PREFIX` exists for the same
#: reason, and the two are deliberately different strings: both forms can be on
#: one page at once, and one prefix would be one POST key for two dates.
EXTERNAL_POSITION_PREFIX = "position"


def attach_external_position_precision(form: forms.Form, *, record: Any = None) -> None:
    """Give a `Väline seisukoht` form the shared `Täpsus` control, and its day box.

    `Seisukoha kuupäev` is a statement about *somebody else's* timetable, which
    is precisely the category docs/adr/0079 §11's exact-only list excludes — a
    ministry's paper remembered as «kevadel 2019» had an invented day or an
    empty field before this, and both are worse than the one the person has.

    **The existing composer, not a second one.** `_precision_fields` builds the
    group `+ Oluline tähtaeg`, `+ Jõustumine`, `+ Töövõit`, `Järgmine tegevus`
    and `+ Kaasamine` all carry, normalised through the same
    `app.workflow.dates.bounds_for` and rendered by the same
    `matters/partials/period_composer.html`. A quarter stated on a position is
    the same stored anchor as a quarter stated anywhere else, or the same period
    would sort into two places (Stage-2G brief 49, docs/adr/0079 §1).

    **The day box keeps the name `stated_on`**, exactly as the engagement's
    keeps `occurred_on` and `NextActionForm` keeps `target_date`: the view, the
    service and the tests read that name, and renaming a control for tidiness is
    how several things stop being wired up while still looking right.

    ``record`` is the position being corrected, when there is one. It earns the
    «Muutmata» chip of docs/adr/0079 §9 for a row carrying a precision the
    control does not otherwise offer — and, because the choices are built per
    instance, it is also what refuses a crafted `HALF_YEAR` on a record that
    never had one.
    """
    kept = kept_precision_choice(
        getattr(record, "stated_on", None),
        getattr(record, "stated_on_precision", "") or "",
    )
    form.precision_choices = precision_choices(kept)  # type: ignore[attr-defined]
    group = _precision_fields(EXTERNAL_POSITION_PREFIX, date_label="Seisukoha kuupäev", kept=kept)
    del group[f"{EXTERNAL_POSITION_PREFIX}_date"]
    form.fields.update(group)


def external_position_period(form: forms.Form) -> tuple[date | None, str]:
    """What a `Väline seisukoht` form's date control says: an anchor and a precision.

    The three answers `engagement_period` documents, for the same reasons: a
    period the person stated, **nothing at all** — which is «kuupäev teadmata»
    and is a fact the column has to be able to hold — or a period somebody began
    stating and did not finish, which `_period_anchor` has already refused on
    the control it belongs to.

    The precision returned beside a `None` anchor is always `EXACT`, which is
    what the column stores for a row with no date. Here the database says so as
    well (`matters_external_position_undated_is_exact`).
    """
    anchor, _end, precision = _period_anchor(form, EXTERNAL_POSITION_PREFIX, date_field="stated_on")
    if anchor is None:
        return None, DatePrecision.EXACT.value
    return anchor, precision


#: The prefix both `Menetluse areng` surfaces carry their precision group under.
#:
#: One name, because the panel that records a development and the form that
#: corrects one must read the same POST keys — a person who states *oktoober
#: 2026* in `+ Menetluse areng` and then opens `Muuda` has to find the same
#: control holding the same answer. Deliberately its own string: several of these
#: forms can be on one page at once, and a shared prefix would be one POST key
#: for two dates (`EXTERNAL_POSITION_PREFIX`, `ENGAGEMENT_PREFIX`).
DEVELOPMENT_PREFIX = "areng"


def attach_development_precision(form: forms.Form, *, record: Any = None) -> None:
    """Give a `Menetluse areng` form the shared `Täpsus` control, and its day box.

    `Kuupäev` here is a statement about **somebody else's** timetable — when a
    ministry sent a draft, when a government sat — which is precisely the
    category docs/adr/0079 §11's exact-only list excludes. A step learned about
    from a third party and remembered as «oktoobris» had an invented day or an
    empty field before this, and both are worse than the one the person has.

    **The existing composer, not a second one.** `_precision_fields` builds the
    group `+ Oluline tähtaeg`, `+ Jõustumine`, `+ Töövõit`, `Järgmine tegevus`,
    `+ Kaasamine` and `+ Teiste arvamus` all carry, normalised through the same
    `app.workflow.dates.bounds_for` and rendered by the same
    `matters/partials/period_composer.html`.

    **The day box keeps the name `occurred_on`**, exactly as the engagement's
    does: the view, the service and the tests read that name, and renaming a
    control for tidiness is how several things stop being wired up while still
    looking right.

    ``record`` is the development being corrected, when there is one. It earns the
    «Muutmata» chip of docs/adr/0079 §9 for a row carrying a precision the control
    does not otherwise offer — and, because the choices are built per instance, it
    is also what refuses a crafted `HALF_YEAR` on a record that never had one.
    """
    kept = kept_precision_choice(
        getattr(record, "occurred_on", None),
        getattr(record, "occurred_on_precision", "") or "",
    )
    form.precision_choices = precision_choices(kept)  # type: ignore[attr-defined]
    group = _precision_fields(DEVELOPMENT_PREFIX, date_label="Kuupäev", kept=kept)
    del group[f"{DEVELOPMENT_PREFIX}_date"]
    form.fields.update(group)


def development_period(form: forms.Form) -> tuple[date | None, str]:
    """What a `Menetluse areng` form's date control says: an anchor and a precision.

    The three answers `engagement_period` documents, for the same reasons: a
    period the person stated, **nothing at all** — which is «kuupäev teadmata»
    and is the fact that made this record a record rather than an `Entry` — or a
    period somebody began stating and did not finish, which `_period_anchor` has
    already refused on the control it belongs to.

    The precision returned beside a `None` anchor is always `EXACT`, which is what
    the column stores for a row with no date, and what the database enforces
    (`matters_development_undated_is_exact`).
    """
    anchor, _end, precision = _period_anchor(form, DEVELOPMENT_PREFIX, date_field="occurred_on")
    if anchor is None:
        return None, DatePrecision.EXACT.value
    return anchor, precision


def development_period_initial(record: Any) -> dict[str, Any]:
    """One stored `Kuupäev`, as the boxes its composer reopens on.

    `period_initial` under this form's prefix. For an approximate record it fills
    the year and the month or quarter and deliberately leaves the day box empty,
    so the anchor never reaches a screen as `01.10.2026` (docs/adr/0079 §2, §3).
    """
    return period_initial(
        DEVELOPMENT_PREFIX,
        getattr(record, "occurred_on", None),
        getattr(record, "occurred_on_precision", "") or "",
        date_field="occurred_on",
    )


def external_position_period_initial(record: Any) -> dict[str, Any]:
    """One stored `Seisukoha kuupäev`, as the boxes its composer reopens on.

    `period_initial` under this form's prefix. For an approximate record it
    fills the year and the month or quarter and deliberately leaves the day box
    empty, so the anchor never reaches a screen as `01.10.2026`
    (docs/adr/0079 §2).

    An explicit ``stated_on=None`` underneath, exactly as
    `engagement_period_initial` carries one and for the same reason.
    `+ Väline seisukoht`'s day box declares ``initial=timezone.localdate``, and
    a record with no date at all makes `period_initial` return ``{}`` — so
    without this line a form that inherited that default would open an undated
    position showing today, one `Salvesta` away from being saved as a date the
    other organisation never gave. `ExternalPositionEditForm` declares its own
    field without the default, and this says so a second time rather than
    trusting two declarations to stay apart (docs/adr/0084 §2).
    """
    return {
        "stated_on": None,
        **period_initial(
            EXTERNAL_POSITION_PREFIX,
            getattr(record, "stated_on", None),
            getattr(record, "stated_on_precision", "") or DatePrecision.EXACT.value,
            date_field="stated_on",
        ),
    }


@dataclass(frozen=True)
class OrganisationChoices:
    """One reading of the institution catalogue, for every control on one page.

    **Three controls on the Teema page ask the same question**, and until this
    existed each of them answered it with its own queries: the catalogue, two
    usage passes and the recorded spellings, four reads apiece. That was the
    right trade while `+ Väline seisukoht` was the only such panel — it is a
    closed panel most visits never open, so one extra read was cheap. It stopped
    being cheap the moment there were three of them: the Teema page went from 38
    queries to 49 and blew the budget `tests/test_teema_redesign.py` holds
    (docs/adr/0091 §3.5).

    So the reading is hoisted to the page. `workspace_forms` builds one of these
    and hands it to every control; each control still slices its own shortlist
    and tail out of it in Python, which is where that work always happened.

    **It is a reading, not a cache.** It is built per request and never stored,
    memoised or shared between them: the catalogue changes when somebody adds an
    institution, and a stale shortlist is a control offering a body that is not
    there. Nothing here is scoped by viewer except ``ranked``, which is — inside
    `_usage_order`, by `visible_to`, so no restricted Matter can move a chip.
    """

    #: Every institution, in name order. The validating queryset is separate and
    #: stays the whole catalogue, because every institution is a valid answer.
    catalogue: list[Any]
    #: The primary keys this reader's own Matters involve most, best first.
    #:
    #: Empty both for a caller with no viewer **and** for a reader whose visible
    #: Matters involve nobody yet — which is why :attr:`ranked_for_a_reader`
    #: exists rather than this being tested for truth. Those two states look the
    #: same here and are completely different on the page: the first has no
    #: shortlist at all and renders every institution as a chip, the second has a
    #: shortlist topped up alphabetically so two readers with no history see the
    #: same eight. Collapsing them put every catalogue behind the search on a
    #: fresh database (`test_every_institution_is_offered_even_when_it_is_not_a_chip`).
    ranked: list[Any]
    #: Whether this reading was made for somebody, as opposed to for a form with
    #: no viewer at all.
    ranked_for_a_reader: bool
    #: Recorded spellings by primary key, so «MKM» finds the ministry through an
    #: alias rather than through a similarity score.
    alias_terms: dict[str, str]

    def shortlist_and_tail(self) -> tuple[list[Any], list[Any]]:
        """The chips and the searchable rest, sliced out of one reading.

        The ranking `organisations_by_usage` produces, topped up alphabetically
        so two readers with no history see the same eight and neither sees none.
        Pure Python over :attr:`catalogue`; no query.

        **A reading with no reader has no shortlist**, and returns the whole
        catalogue as chips with an empty tail — which is what a form built without
        a viewer has always rendered. That is decided from
        :attr:`ranked_for_a_reader` and never from `ranked` being empty: a reader
        whose visible Matters involve nobody yet still gets the alphabetical eight.
        """
        if not self.ranked_for_a_reader:
            return list(self.catalogue), []
        by_pk = {organisation.pk: organisation for organisation in self.catalogue}
        shortlist = [by_pk[pk] for pk in self.ranked[:SENDER_SHORTLIST_SIZE] if pk in by_pk]
        if len(shortlist) < SENDER_SHORTLIST_SIZE:
            chosen = {organisation.pk for organisation in shortlist}
            shortlist.extend(
                organisation for organisation in self.catalogue if organisation.pk not in chosen
            )
            shortlist = shortlist[:SENDER_SHORTLIST_SIZE]
        chosen = {organisation.pk for organisation in shortlist}
        tail = [organisation for organisation in self.catalogue if organisation.pk not in chosen]
        return shortlist, tail


def read_organisation_choices(viewer: Any) -> OrganisationChoices:
    """Read the catalogue, the usage ranking and the spellings, once.

    Four queries at most, and the same four every control used to make for
    itself. The two usage passes are `organisations_by_usage`'s own, in its
    order and scoped by `visible_to` inside `_usage_order`; the second runs only
    when the sender history did not fill the row.
    """
    from app.organisations.models import Organisation

    catalogue = list(Organisation.objects.order_by("name"))
    ranked: list[Any] = []
    if viewer is not None:
        seen: set[Any] = set()
        for field_name in ("source_organisations", "addressee_organisation"):
            if len(ranked) >= SENDER_SHORTLIST_SIZE:
                break
            for pk in _usage_order(viewer, field_name, SENDER_SHORTLIST_SIZE):
                if pk not in seen:
                    seen.add(pk)
                    ranked.append(pk)
    return OrganisationChoices(
        catalogue=catalogue,
        ranked=ranked,
        ranked_for_a_reader=viewer is not None,
        alias_terms=organisation_alias_terms(),
    )


def attach_organisation_picker(
    form: forms.Form, *, viewer: Any, choices: OrganisationChoices | None = None
) -> None:
    """Point one single-answer organisation control at the shared catalogue.

    The compact `Väline seisukoht` panel asks the same question `Saatja` and
    `Adressaat` ask — *which institution?* — over the same one catalogue, so it
    is answered with the same control rather than a ninth way of naming a body
    (docs/adr/0063, docs/adr/0073). What this function does is exactly what
    `MatterCreateForm.__init__` does for `addressee_organisation`, and no more:

    * validation runs against the **whole** catalogue, because every institution
      is a valid answer and narrowing it to what is on screen would refuse a
      correct answer given through the search;
    * the *rendered* order is presentation — the bodies this reader's own
      Matters actually involve first, then the rest alphabetically — and it is
      scoped by `visible_to` inside `organisations_by_usage`, so no restricted
      Matter can move a chip;
    * `organisation_split` is where the shortlist ends and the searchable tail
      begins, computed here because a template cannot slice on a primary key;
    * the recorded spellings go onto the widget, so «MKM» finds
      `Majandus- ja Kommunikatsiooniministeerium` through a recorded alias
      rather than through a similarity score.

    **No blank option**, unlike `Adressaat`. «Määramata» is a real answer to
    *who was this addressed to* and is not a real answer to *whose position is
    this*: a position with no author is an anonymous claim on a professional
    file, so the refusal is `EXTERNAL_POSITION_NEEDS_ORGANISATION` rather than a
    chip.

    A form with no viewer gets the plain catalogue and no split, which is the
    same fallback `MatterCreateForm` takes: no usage to rank by means no
    shortlist, and everything is a chip.
    """
    from app.organisations.models import Organisation

    # **The catalogue is read once**, and both halves are sliced out of that one
    # reading in Python. `organisations_by_usage` is the shared shortlist helper
    # and is deliberately *not* called here: it re-reads the rows it ranked and
    # tops the row up with a second catalogue query, which is the right trade on
    # a page built around one Organisation question and the wrong one here.
    #
    # ``choices`` hoists the reading one level further, to the *page*. A caller
    # rendering three of these controls — which the Teema page now does — reads
    # the catalogue, the ranking and the spellings once and hands the result to
    # each of them; a caller rendering one passes nothing and reads it here, as
    # every caller used to (`read_organisation_choices`, docs/adr/0091 §3.5,
    # `tests/test_teema_redesign.py` holds the page's query budget).
    reading = choices if choices is not None else read_organisation_choices(viewer)
    set_choices(form, "organisation", Organisation.objects.order_by("name"))
    field = cast(Any, form.fields["organisation"])

    shortlist, tail = reading.shortlist_and_tail()
    if not reading.ranked_for_a_reader:
        # No *viewer* means no usage to rank by, so there is no shortlist and no
        # long tail, and the template renders everything inline — the same
        # fallback `MatterCreateForm` takes. A split of zero would hide the whole
        # catalogue behind a disclosure that is not rendered either.
        form.organisation_offered = shortlist  # type: ignore[attr-defined]
        form.organisation_split = None  # type: ignore[attr-defined]
    else:
        form.organisation_offered = [*shortlist, *tail]  # type: ignore[attr-defined]
        form.organisation_split = len(shortlist)  # type: ignore[attr-defined]

    field.choices = [
        (organisation.pk, organisation.name)
        for organisation in cast(Any, form).organisation_offered
    ]
    cast(Any, field.widget).alias_terms = reading.alias_terms


class InitialOpinionActionForm(forms.Form):
    """`Koostan arvamuse` on `Uus teema` — one date, and the first step it creates.

    **Deliberately its own form, with its own prefix and its own template
    partial.** The whole of this round's `Uus teema` change is one date box and
    one small hunk in `matter_create`, because `MatterCreateForm` and
    `matter_create.html` are being rewritten in parallel by the classification
    work: a field added to that class and that template would be a merge conflict
    in the two files most likely to move, over a question neither of them is about
    (docs/adr/0091 §1.4).

    **One field, because the sentence is not a question.** A normal incoming
    consultation begins the same way every time — the lawyer will write Koda's
    opinion by a day they already know — so the only thing this asks is the day.
    The step's wording is `OPINION_PREPARATION_TEXT`, the department's own words,
    and asking a lawyer to type them was the double entry lawyer feedback 9 is
    about (lawyer feedback 9).

    **No `initial`, and that is the rule rather than an omission.** «Saving the
    Matter automatically creates the next action» does not mean «invent the date».
    Not today, not seven days out, not the consultation deadline, not the end of
    the month and not the creation date: `Arvamuse tähtaeg` directly above it on
    the same page carries no default for exactly this reason, because a commitment
    nobody stated is a commitment nobody can be held to, and since the field became
    work it is not even inert — a Matter created and left alone would be due on its
    creation day and overdue the next morning (docs/adr/0078 §2, ADR 0052 §5).

    A blank box therefore creates nothing. Not an undated step either: `WAIT` and
    `MONITOR` may be dateless and this is neither, and turning a blank field into
    an open-ended commitment would be a promise nobody made (docs/adr/0091 §1.2).

    `use_required_attribute` is off for the reason `NextActionForm`'s is: this is
    optional, and with the HTML attribute present a browser would refuse to submit
    the whole `Uus teema` form and report nothing, so «Loo teema» would silently do
    nothing.
    """

    use_required_attribute = False

    prepare_by = EstonianDateField(
        label="Koostan arvamuse",
        required=False,
        widget=DATE_WIDGET,
        help_text="Mis kuupäevaks Koja arvamuse koostad. Jäta tühjaks, kui veel ei tea.",
    )

    @property
    def action_text(self) -> str:
        """The sentence the step will carry, for the page to show.

        Read from the service's own constant rather than written into the
        template, so that the words on the screen and the words in the record are
        one string. A person who is told «Koostan arvamuse» and finds «Arvamuse
        koostamine» on their Minu asjad has been shown a different product from the
        one that saved (`app.workflow.services.OPINION_PREPARATION_TEXT`).
        """
        from app.workflow.services import OPINION_PREPARATION_TEXT

        return OPINION_PREPARATION_TEXT


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

    **The next step has its own box, and the two questions are separate.**
    `body` is what happened; `next_text` is what happens next. Nothing is
    derived from the other, in either direction — no NLP, no sentence
    splitting, no summarisation, and no copying. A lawyer who writes
    "Ministeerium lubas uue versiooni nädala lõpuks" in one box and
    "Vaadata uus eelnõu versioon üle" in the other has stated two different
    facts, and the register stores two records (ADR 0052).

    This reverses the redesign's original claim that the description *is* the
    next step (ADR 0030 §4). Deriving one from the other only worked while the
    composer also asked the lawyer to classify the sentence, and that
    classification is what the approved workflow retires.

    **This form no longer asks for an action kind or a date meaning.** A next
    step recorded here is `DO` / `DEADLINE` / `EXACT` — an exact work date,
    meaning *when I will have done this*. `ActionKind` and `DateSemantics`
    remain valid stored concepts and every historical `WAIT` and `MONITOR` row
    is untouched; they are simply not a question this surface puts to a person.

    **A next step is requested by a non-empty `next_text` plus a date**, and by
    nothing else. Either one alone is refused on the control that is missing.
    """

    use_required_attribute = False

    body = forms.CharField(
        label="Mida tegid või mis juhtus?",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "composer__body",
                "rows": "3",
                "placeholder": "Kirjelda, mida tegid või mis juhtus…",
                "data-richtext": "true",
            }
        ),
    )
    # -- the file ----------------------------------------------------------
    #: `Lohista fail siia või vali arvutist` — always available, never behind a
    #: disclosure. `+ Manus` is gone: opening a panel to reach a file picker is a
    #: click spent on the commonest thing anybody does here after typing
    #: (TEEMA_TARGET_SPEC §C.3).
    #:
    #: **And nothing else is asked about it.** `Roll`, `Sissekande liik`,
    #: `Asutus` and `Toimus` were four questions standing between a lawyer and
    #: the PDF in front of them, and the approved target removes all four: a file
    #: dropped here is ordinary evidence, captured now, on an ordinary work
    #: entry. `compose_update` still takes every one of them as a parameter and
    #: the archive importer still supplies them — what changed is that this
    #: surface stopped asking a person to classify a file before they could
    #: attach it (docs/adr/0074 §6).
    #:
    #: Removed rather than hidden, which is the rule the retired next-step
    #: precision group already follows: a control the page does not have must not
    #: be reachable through a crafted POST either (ADR 0052 §4).
    attachment = forms.FileField(
        label="Manus",
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "field__input"}),
    )

    # -- JÄRGMISEKS --------------------------------------------------------
    #: What happens next, in the lawyer's own words, and stored exactly as
    #: typed. It is not derived from `body` and `body` is not derived from it:
    #: "what I did" and "what I will do" are two sentences, and a system that
    #: split one into the other would be inventing the register's content
    #: (ADR 0052 §2).
    #:
    #: Empty is the ordinary case. Most saves record what happened and leave
    #: the current step exactly where it was.
    next_text = forms.CharField(
        label="Järgmiseks",
        required=False,
        max_length=2000,
        widget=forms.TextInput(
            attrs={
                "class": "field__input uxcomp__next",
                "placeholder": "Näiteks: vaadata uus eelnõu versioon üle",
            }
        ),
    )
    #: **When the work will be done**, and nothing more subtle than that.
    #:
    #: No `initial`. A step with no date is refused rather than silently filed
    #: for today: pre-filling would turn the refusal into an assertion the
    #: person never made, and the browser lane has caught exactly that
    #: regression before (e2e/test_lawyer_workflow.py).
    #:
    #: There is no precision group behind it. The approximate-period machinery
    #: still exists and is still offered for `Oluline tähtaeg`, where a
    #: consultation genuinely ends "in the autumn" — but the day a lawyer will
    #: act on their own file is a day, and asking them how precisely they know
    #: their own plan is the classification this workflow retires
    #: (ADR 0052 §4).
    next_date = EstonianDateField(label="Millal?", required=False, widget=EstonianDateInput())

    # -- + Oluline tähtaeg -------------------------------------------------
    deadline_title = forms.CharField(
        label="Mis tähtaeg",
        required=False,
        max_length=2000,
        widget=forms.TextInput(
            attrs={
                "class": "field__input field__input--compact",
                "placeholder": "nt kooskõlastusringi lõpp",
            }
        ),
    )

    # -- + Jõustumine ------------------------------------------------------
    #: `Mis jõustub` and `Jõustub` — a composer door onto the existing
    #: `MatterEffectiveDate`, not a second commencement model and not an `Entry`
    #: pretending to be one. The panel is compact because a commencement a
    #: lawyer is writing down mid-work is a named thing on a named day; the
    #: approximate and general-order kinds the domain also stores keep their own
    #: surfaces and their own records (docs/adr/0074 §7).
    effective_title = forms.CharField(
        label="Mis jõustub",
        required=False,
        max_length=2000,
        widget=forms.TextInput(
            attrs={
                "class": "field__input field__input--compact",
                "placeholder": "nt pakendiseaduse muudatused",
            }
        ),
    )
    effective_on = EstonianDateField(label="Jõustub", required=False, widget=EstonianDateInput())

    # -- + Töövõit ---------------------------------------------------------
    #: `Mis muutus` — one box, and closing the Matter is not a precondition.
    #:
    #: A win is recorded when it happens. The closing section has its own
    #: victory question and always did; this is the other half of the same
    #: domain fact, for the far commoner case where the file stays open and
    #: something in it was won this week (docs/adr/0074 §8).
    victory_change = forms.CharField(
        label="Mis muutus",
        required=False,
        max_length=2000,
        widget=forms.TextInput(
            attrs={
                "class": "field__input field__input--compact",
                "placeholder": "nt üleminekuaeg väiketootjatele pikendati 2028. aastani",
            }
        ),
    )

    # -- + Kaasamine -------------------------------------------------------
    #: `Liik`, `Keda kaasati`, `Vastuseid` — three questions, which is what the
    #: approved target asks and all it asks.
    #:
    #: This reverses ADR 0031's «one entry point, and it is the standalone
    #: section». That decision was sound while the section existed: two controls
    #: for one act is how one consultation gets recorded twice. The section is
    #: gone now, so this is the one entry point rather than the second one
    #: (docs/adr/0074 §9).
    #:
    #: The kind field validates against the *whole* stored vocabulary while the
    #: page offers three chips. A historical `WEB_CALL` row must stay editable
    #: through every service that takes a kind, and a form that refused the value
    #: its own database holds would be the thing that breaks
    #: (app/matters/enums.py `COMPOSER_ENGAGEMENT_KINDS`).
    engagement_kind = forms.ChoiceField(
        label="Liik",
        choices=EngagementKind.choices,
        #: `Küsitlus`, because that is the chip the panel opens with.
        #:
        #: **Not merely a server-side fallback.** `_clean_engagement` defaults to
        #: the same value, so a POST that never touched the chips saves correctly
        #: either way — but the hidden input is what the chip script reads to
        #: decide which chip is selected, and an empty one made it *deselect* the
        #: chip the server had just rendered as chosen. A form whose first paint
        #: contradicts itself is the defect; the browser lane caught it
        #: (static/js/ux.js `bindChipGroups`, e2e/test_engagement.py).
        initial=COMPOSER_ENGAGEMENT_KINDS[0][0],
        required=False,
        widget=forms.HiddenInput(),
    )
    engagement_audience = forms.CharField(
        label="Keda kaasati",
        required=False,
        max_length=500,
        widget=forms.TextInput(
            attrs={
                "class": "field__input field__input--compact",
                "placeholder": "nt liikmed, kaubandusvaldkonna töögrupp",
            }
        ),
    )
    #: Optional, and empty means *not counted* rather than *nobody answered*.
    #: `min_value=0` because a consultation that genuinely drew no reply is a
    #: real answer somebody may want to record (docs/adr/0074 §5).
    engagement_responses = forms.IntegerField(
        label="Vastuseid",
        required=False,
        min_value=0,
        max_value=1_000_000,
        widget=forms.TextInput(
            attrs={
                "class": "field__input field__input--compact",
                "inputmode": "numeric",
                "autocomplete": "off",
                "placeholder": "14",
            }
        ),
    )
    #: The same two optional pointers `+ Kaasamine` grew, on the superseded
    #: composer as well — one act must not be recordable in two shapes that
    #: disagree about which facts it can hold (docs/adr/0027, amended
    #: 2026-09-12; docs/adr/0075 §11).
    engagement_smaily_url = provider_link_field("Smaily link", "https://sendsmaily.net/…")
    engagement_alchemer_url = provider_link_field("Alchemer link", "https://survey.alchemer.eu/…")

    # -- + Lõpeta teema ----------------------------------------------------
    #: **Two questions.** `Kuidas lõppes`, and an optional `Lõppsõna`.
    #:
    #: The closing section asked six things until this pass: why, the file that
    #: went out, when, to whom, whether it was a win, and when the result
    #: commenced. The approved target asks two, and the four that went are not a
    #: simplification of the closure — they are a correction of what closing
    #: *means*. **Closing a Matter is not a claim that an opinion was sent.**
    #: Koda closes files it never wrote to anybody about; it sends opinions on
    #: files that stay open for another year. Requiring the sent PDF in order to
    #: finish a file made the commonest closure impossible to record honestly,
    #: and made the rarer one — closing *because* the opinion went out — look
    #: like the only shape a closure has.
    #:
    #: **None of the canonical rules moved.** A `SENT` Submission still needs its
    #: exact final evidence, still goes through `mark_submission_sent`, and is
    #: still recorded from Dokumendid or from the composer's own file control. A
    #: `Töövõit` is still a confirmed `MatterWorkVictory` and is now recordable
    #: from `+ Töövõit` without closing anything. A commencement is still a
    #: `MatterEffectiveDate` and is now recordable from `+ Jõustumine`. What the
    #: closing panel stopped doing is asking for all three at the one moment
    #: they are least likely to all be true (docs/adr/0074 §10).
    #:
    #: `_apply_closure` still accepts `final_opinion`, `work_victory` and
    #: `effective_date` inside a closure payload; this form no longer sends them.
    disposition = forms.ChoiceField(
        label="Kuidas lõppes",
        choices=(("", "Vali põhjus…"), *CLOSURE_CHOICES),
        required=False,
        widget=forms.HiddenInput(),
    )
    #: `Lõppsõna` — what became of this, in the closer's own words, and optional
    #: because most closures have nothing to add that the chronology above does
    #: not already say.
    closing_words = forms.CharField(
        label="Lõppsõna",
        required=False,
        max_length=2000,
        widget=forms.Textarea(
            attrs={
                "class": "field__input field__input--compact",
                "rows": "2",
                "placeholder": "Mis sellest teemast lõpuks sai?",
            }
        ),
    )

    def __init__(self, *args: Any, matter: Any = None, viewer: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # One precision group, not two. `Oluline tähtaeg` is an external
        # milestone somebody announced and is often known only to a month or a
        # quarter; the next step is this lawyer's own working day. The `next`
        # group is gone rather than hidden, so a crafted POST cannot store an
        # approximate next step through a control the page no longer has
        # (ADR 0052 §4).
        self.precision_choices = precision_choices()
        self.fields.update(_precision_fields("deadline", date_label="Kuupäev"))
        self.fields.update(_precision_fields("victory", date_label="Millal"))
        self.matter = matter
        self.viewer = viewer

        # **Nothing here binds an existing record the viewer might not be able
        # to read**, and since the approved target there is nothing here that
        # could. The form once offered a `Järglane` picker over Matters and a
        # final-evidence picker over `DocumentVersion`, and both had to go
        # through `visible_to`, because a crafted POST naming a restricted record
        # would have confirmed that it exists. Both were retired with the old
        # closing flow; the sent-opinion recipient pickers went with the target's
        # two-question closure (Teema closing redesign §18, docs/adr/0074 §10).
        #
        # Every record this form now writes is created on *this* Matter and
        # inherits its visibility. There is no queryset left to scope.

    # -- what the panels offer --------------------------------------------

    @property
    def engagement_kind_chips(self) -> list[dict[str, Any]]:
        """`Küsitlus` / `Koosolek` / `Kirjade voor`, and which one is chosen.

        Rendered from an explicit list rather than by iterating the field,
        because the field validates against the whole stored vocabulary on
        purpose — the same reason `shortlist_recipients` was built this way. The
        first chip is selected on an unbound form, which is what the target
        shows (TEEMA_TARGET_SPEC §C.4).
        """
        chosen = self._chosen("engagement_kind", COMPOSER_ENGAGEMENT_KINDS[0][0])
        return [
            {"value": value, "label": label, "selected": value == chosen}
            for value, label in COMPOSER_ENGAGEMENT_KINDS
        ]

    @property
    def closure_chips(self) -> list[dict[str, Any]]:
        """`Jõustus` / `Menetlus lõppes` / `Loobuti`.

        Nothing is selected until somebody chooses, including on a first load:
        an unanswered `Kuidas lõppes` has to be representable, or the panel would
        post a closure from every ordinary save the moment it was opened — which
        is the defect the empty `Põhjus` option was added to fix (pilot QA F-02).
        """
        chosen = self._chosen("disposition", "")
        return [
            {"value": value, "label": label, "selected": value == chosen}
            for value, label in COMPOSER_CLOSURE_CHOICES
        ]

    @property
    def precision_chips(self) -> list[dict[str, Any]]:
        """`Täpne päev` / `Kuu` / `Kvartal` / `Aasta` for `+ Oluline tähtaeg`.

        The same four the Teema page offers, from the same partial. This panel
        showed three and derived the period from the day that was picked; the
        fourth is the one that made the derivation necessary, and both are gone
        together (docs/adr/0079 §1, superseding docs/adr/0074 §11).

        `HALF_YEAR` and `INFERRED` are real stored precisions and stay readable
        on the records that carry them. Neither is offered here, because this
        panel only ever creates (§7, §8).
        """
        return _precision_chips(self, "deadline_precision")

    @property
    def victory_precision_chips(self) -> list[dict[str, Any]]:
        """The same four, for `+ Töövõit`'s own period (docs/adr/0079 §10)."""
        return _precision_chips(self, "victory_precision")

    def _chosen(self, name: str, fallback: str) -> str:
        """What a chip group should show as selected, after a refused save too.

        Read from the raw data rather than from `cleaned_data`, for the same
        reason `typed_recipients` was: the save that most needs its chips back is
        the one that did not validate.
        """
        if self.is_bound:
            return str(self.data.get(name) or "")
        return fallback

    # -- validation --------------------------------------------------------

    #: The controls that only a closure has an answer for. Filling in any one of
    #: them is what asks for the Matter to be closed — see
    #: :meth:`closure_requested` (pilot QA F-02).
    #:
    #: Two, since the approved target's closing panel asks two questions. The
    #: five that went were the sent opinion and the work victory, and neither is
    #: a closure any more (docs/adr/0074 §10).
    CLOSURE_FIELDS: tuple[str, ...] = (
        "disposition",
        "closing_words",
    )

    @cached_property
    def closure_requested(self) -> bool:
        """Did this save answer anything only a closure is asked?

        **Read from what was submitted, not from `cleaned_data`.** A closing
        answer that fails its own field validation — an unreadable send date, a
        recipient id that is not in the catalogue — is exactly the save that
        must not fall through into an ordinary note, and by the time cleaning is
        done it is no longer in `cleaned_data`. The raw data is what the person
        actually did.

        Blanks do not count. Every text box on this form posts on every save
        whether or not anybody typed in it, so «submitted» has to mean «carries
        a value», and `Põhjus` is the reason it now has an empty option to
        carry.

        The template reads this too: a refused closure has to come back with the
        section open, or its errors are printed inside a panel nobody can see.
        """
        if not self.is_bound:
            return False
        for name in self.CLOSURE_FIELDS:
            if name in self.files:
                return True
            if hasattr(self.data, "getlist"):
                values = self.data.getlist(name)
            else:
                raw = self.data.get(name)
                values = raw if isinstance(raw, (list, tuple)) else [raw]
            if any(str(value).strip() for value in values if value is not None):
                return True
        return False

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        body = (cleaned.get("body") or "").strip()
        next_text = (cleaned.get("next_text") or "").strip()
        # A date on its own counts as *asking for* a next step. It is refused
        # below, on the box that is empty — but it has to reach that refusal,
        # rather than falling through to "you typed nothing at all", which is
        # not what happened and does not say where to look.
        wants_next = bool(next_text or cleaned.get("next_date") is not None)
        wants_deadline = bool((cleaned.get("deadline_title") or "").strip())
        # Each panel is wanted when its own *identifying* answer carries
        # something — the thing it names, not the date beside it. A date alone
        # is refused on the empty box below rather than falling through as «you
        # typed nothing at all», which is the rule `Järgmiseks` already follows.
        wants_effective = bool(
            (cleaned.get("effective_title") or "").strip()
            or cleaned.get("effective_on") is not None
        )
        # A period with no sentence counts as wanting the panel too, and is
        # then refused on the empty sentence — the rule every other panel here
        # already follows, applied to a control this one did not used to have.
        wants_victory = bool(
            (cleaned.get("victory_change") or "").strip()
            or cleaned.get("victory_date") is not None
            or (cleaned.get("victory_year") or "")
        )
        # A typed provider link is attempted work too. It used to count for
        # nothing: somebody who pasted a Smaily address and pressed Salvesta
        # was told «Kirjelda tegevust või vali, mida veel salvestada» and the
        # address went out with the response. Asking the panel «does this hold
        # anything at all» has to mean every box in it
        # (docs/adr/0027, amended 2026-09-12).
        wants_engagement = bool(
            (cleaned.get("engagement_audience") or "").strip()
            or cleaned.get("engagement_responses") is not None
            or (cleaned.get("engagement_smaily_url") or "").strip()
            or (cleaned.get("engagement_alchemer_url") or "").strip()
        )
        # Closure-specific input *is* closure intent. There is no second box to
        # tick and therefore no way to fill this section in, be told the save
        # succeeded, and find none of it stored (pilot QA F-02).
        wants_closure = self.closure_requested
        has_file = bool(cleaned.get("attachment"))

        if not (
            body
            or wants_next
            or wants_deadline
            or wants_effective
            or wants_victory
            or wants_engagement
            or wants_closure
            or has_file
        ):
            raise forms.ValidationError("Kirjelda tegevust või vali, mida veel salvestada.")

        self._clean_next_action(cleaned)
        self._clean_deadline(cleaned, wanted=wants_deadline)
        self._clean_effective(cleaned, wanted=wants_effective)
        self._clean_victory(cleaned, wanted=wants_victory)
        self._clean_engagement(cleaned, wanted=wants_engagement)
        self._clean_closure(cleaned, wanted=wants_closure)
        return cleaned

    def _clean_next_action(self, cleaned: dict[str, Any]) -> None:
        """`Järgmiseks` + `Millal?`, together or not at all.

        Two boxes, one record, and three outcomes. Both empty is the ordinary
        save: an entry is written and whatever step is already open stays open,
        untouched. Both filled writes the step. One filled is refused **on the
        empty control**, because "vali kuupäev" pinned to the sentence box is an
        error message pointing at the wrong field (ADR 0052 §5).

        Nothing is defaulted here. Quietly filing an undated step for today
        would be the application deciding when a lawyer is going to do their
        own work.
        """
        text = (cleaned.get("next_text") or "").strip()
        target_date = cleaned.get("next_date")

        if not text and target_date is None:
            cleaned["next_action_kwargs"] = None
            return

        if not text:
            self.add_error("next_text", "Kirjuta järgmine tegevus.")
            return
        if target_date is None:
            self.add_error("next_date", "Vali järgmise tegevuse kuupäev.")
            return

        # The canonical compatibility values, and internal to this surface.
        # They are not hidden inputs the page posts — a crafted POST cannot
        # name a different kind or a different date meaning, because this form
        # has no field that carries one. What the date means here is "the day
        # this gets done", which is exactly `DO` + `DEADLINE` + `EXACT`; a
        # lawyer who has to remember to chase somebody writes that as an
        # action — «Kontrollida, kas ministeerium vastas» — rather than as a
        # workflow classification (ADR 0052 §1, §3).
        cleaned["next_action_kwargs"] = {
            "text": text[:2000],
            "kind": ActionKind.DO,
            "date_semantics": DateSemantics.DEADLINE,
            "target_date": target_date,
            "date_precision": DatePrecision.EXACT,
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

    def _clean_effective(self, cleaned: dict[str, Any], *, wanted: bool) -> None:
        """`+ Jõustumine` — what commences, and the day it does.

        Both halves or neither, refused on whichever is missing, exactly as
        `Järgmiseks` and `Oluline tähtaeg` are. A commencement with no date is a
        sentence, and a date with nothing commencing on it is a number.

        Stored at `EXACT` precision because the panel asks for a day and takes a
        day. The approximate and general-order kinds `MatterEffectiveDate` also
        carries are unchanged on the rows that hold them; this compact path
        simply does not create new ones (docs/adr/0074 §7).
        """
        if not wanted:
            cleaned["effective_date_kwargs"] = None
            return
        title = (cleaned.get("effective_title") or "").strip()
        when = cleaned.get("effective_on")
        if not title:
            self.add_error("effective_title", "Kirjuta, mis jõustub.")
        if when is None:
            self.add_error("effective_on", "Märgi, millal see jõustub.")
        if self.errors:
            return
        cleaned["effective_date_kwargs"] = {
            "description": title,
            "date_value": when,
            "period_end": when,
            "date_precision": DatePrecision.EXACT.value,
        }

    def _clean_victory(self, cleaned: dict[str, Any], *, wanted: bool) -> None:
        """`+ Töövõit` — one sentence and the period it belongs to.

        **The period is asked for, and it is asked for here as well as on the
        Teema page.** This path used to create the record undated, on the sound
        reasoning that borrowing today's date would file a win into a reporting
        year nobody chose (Stage-2G brief 13, docs/adr/0074 §8) — but *not
        asking* is not the only alternative to guessing, and an undated win is
        invisible to `?toovoit=<aasta>` and to the reporting rail.

        So the person says when, at whatever precision they have. The rule that
        matters is unchanged and is now enforced rather than worked around:
        nothing is defaulted, and an existing undated row keeps no period
        (docs/adr/0079 §10).
        """
        if not wanted:
            cleaned["work_victory_kwargs"] = None
            return
        anchor, end, precision = _period_anchor(self, "victory")
        if anchor is None or end is None:
            if not self.errors:
                self.add_error(
                    _precision_answer_field("victory", precision),
                    "Märgi, millal see töövõit saavutati.",
                )
            return
        cleaned["work_victory_kwargs"] = {
            "title": (cleaned.get("victory_change") or "").strip()[:2000],
            "detail": "",
            "period_date": anchor,
            "period_end": end,
            "date_precision": precision,
        }

    def _clean_engagement(self, cleaned: dict[str, Any], *, wanted: bool) -> None:
        """`+ Kaasamine` — the kind, who was engaged, how many answered, and where.

        The two provider links are supplementary and never sufficient. A panel
        holding an address and no `Keda kaasati` is refused here rather than
        ignored: the link is what made this count as attempted work at all, so
        the answer owed to the person is a sentence under the audience box, not
        silence and a discarded URL.

        `Keda kaasati` is required because `MatterEngagement.title` is: the
        database refuses an empty one and so does `add_engagement`, and a
        refusal that arrives from a CHECK constraint halfway through a composer
        transaction is a 500 where a sentence would do.

        The kind falls back to the first chip rather than to `OTHER`. The panel
        shows `Küsitlus` selected on open, so a person who filled in the boxes
        and never touched the chips chose `Küsitlus` — storing `Muu` instead
        would contradict what they were looking at (TEEMA_TARGET_SPEC §C.4).
        """
        if not wanted:
            cleaned["engagement_kwargs"] = None
            return
        audience = (cleaned.get("engagement_audience") or "").strip()
        if not audience:
            self.add_error("engagement_audience", "Kirjuta, keda kaasati.")
            return
        kind = cleaned.get("engagement_kind") or COMPOSER_ENGAGEMENT_KINDS[0][0]
        cleaned["engagement_kwargs"] = {
            "kind": kind,
            "title": audience,
            "response_count": cleaned.get("engagement_responses"),
            "smaily_url": (cleaned.get("engagement_smaily_url") or "").strip(),
            "alchemer_url": (cleaned.get("engagement_alchemer_url") or "").strip(),
            # **Nothing, because this form has no box to ask with.**
            #
            # It used to be `timezone.localdate()`, on the reasoning of
            # docs/adr/0074 §9: the target asked for no engagement date, and
            # «this happened as part of the work I am writing down now» meant
            # today in Europe/Tallinn. docs/adr/0078 §2 withdrew that — a
            # consultation is routinely typed up days or months after it
            # happened, and filing it as today is a false fact written by the
            # server with no box on the screen anybody could have corrected.
            #
            # `+ Kaasamine` answered that by *asking*. This form cannot: it is
            # the superseded composer, kept alive for the browsers still
            # holding a page that posts to it (docs/adr/0075 §11), and adding a
            # date control to a surface nothing renders would be building a
            # question nobody can be shown. So it records that the date is not
            # known, which is what `MatterEngagement.occurred_on` has always
            # been able to mean and is the only truthful answer available here.
            # A surface that cannot ask must not answer.
            #
            # No `occurred_on_precision` either, for the same reason: an
            # unknown date has no precision, and `add_engagement` normalises a
            # `NULL` date to `EXACT` whatever a caller names
            # (`app/matters/services.py`, `_engagement_precision`).
            "occurred_on": None,
        }

    def _clean_closure(self, cleaned: dict[str, Any], *, wanted: bool) -> None:
        """`Kuidas lõppes`, and an optional `Lõppsõna`. Nothing else.

        **A closure is not a claim that an opinion was sent.** The old flow made
        the sent PDF a precondition of finishing a file, which is backwards: most
        closures are files Koda never wrote to anybody about, and the opinion
        that *did* go out is normally recorded months before the file closes.
        `mark_submission_sent` still refuses a submission without its exact final
        evidence; it is simply no longer this panel's business
        (docs/adr/0074 §10).

        The stored `reason` is `Lõppsõna` when somebody wrote one, and the
        composer body otherwise. The body has always been the closing narrative
        and still is on a save that closes and describes in one go; `Lõppsõna` is
        the box for a closure whose entry says something else, or nothing.
        """
        if not wanted:
            cleaned["closure_kwargs"] = None
            return

        disposition = cleaned.get("disposition") or ""
        if not disposition:
            self.add_error("disposition", "Vali, kuidas teema lõppes.")

        if self.errors:
            return

        # Plain text, not markup: `disposition_reason` is a sentence in a
        # banner, and the entry keeps the rich text it was written in.
        final_word = (cleaned.get("closing_words") or "").strip()
        narrative = final_word or plain_text(cleaned.get("body") or "").strip()
        cleaned["closure_kwargs"] = {"disposition": disposition, "reason": narrative}

    # -- what the service is called with -----------------------------------

    def as_service_kwargs(self) -> dict[str, Any]:
        """Everything :func:`app.matters.services.compose_update` needs.

        `kind`, `occurred_at`, `organisation` and `attachment_role` are the
        canonical defaults, written here and never read from the POST. The
        approved target does not ask a person to classify what they are writing
        or the file they are attaching, so this surface answers those questions
        the way it has always answered them for somebody who left the disclosure
        alone: an ordinary note, recorded now, and an ordinary attachment
        (docs/adr/0074 §6).
        """
        return {
            "body": self.cleaned_data.get("body") or "",
            "kind": EntryKind.NOTE,
            "occurred_at": None,
            "organisation": None,
            "attachment": self.cleaned_data.get("attachment"),
            "attachment_role": DocumentRole.OTHER,
            "next_action": self.cleaned_data.get("next_action_kwargs"),
            "important_date": self.cleaned_data.get("important_date_kwargs"),
            "effective_date": self.cleaned_data.get("effective_date_kwargs"),
            "work_victory": self.cleaned_data.get("work_victory_kwargs"),
            "engagement": self.cleaned_data.get("engagement_kwargs"),
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
    #: Saatja's typed half on the rail's own editor, so the fourth place a
    #: sender can be set is not the one place a body cannot be named
    #: (docs/adr/0063, `resolve_source_organisations`).
    sender_name = sender_name_field()
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
        # The offered vocabulary *plus* this Matter's own stage, for exactly the
        # reason the owner field is widened one line up: re-submitting the value
        # already on the record has to be a save, and naming any *other* retired
        # stage has to stay a refusal. Without it the header's Hetkeseis control
        # answers "Vigane väärtus." to the value it is displaying
        # (app/workflow/selectors.py, docs/adr/0032 §Amendment).
        set_choices(self, "stage", stages_including_held(matter))
        set_choices(self, "source_organisations", Organisation.objects.order_by("name"))
        set_choices(self, "addressee_organisation", Organisation.objects.order_by("name"))
        # The offered vocabulary *plus* whatever this Matter already carries.
        # Validation would otherwise refuse a save that merely left a retired
        # area ticked, which would make correcting one field on an old Matter
        # impossible without silently dropping its filing (Teema redesign §7.2).
        set_choices(self, "policy_areas", PolicyArea.objects.all())

    def clean_sender_name(self) -> str:
        return clean_typed_organisation_name(self.cleaned_data.get("sender_name"))


class EngagementForm(forms.Form):
    """`Kaasamine`, as it is **corrected** — every stored field, and its version.

    Required: a human-readable title. Everything else is optional because the
    commonest real record is incomplete: a mailing with no durable link, a
    consultation somebody is entering months later without the exact date to
    hand. A form that demanded them would simply not be used (Agent-F brief 39).

    **This is the correction contract, and it has to name every column a person
    can have filled in.** A correction form missing a field does not leave that
    field alone — it leaves the person with a record they can read on the
    chronology and cannot fix. The fields here are therefore the stored ones a
    person answers: `title`, `url`, the two provider links, `note`, both dates
    and `Saadud tagasiside`. `response_count` is deliberately not among them —
    the correction UI does not offer it, so the view never names it and
    `update_engagement`'s `_UNSET` leaves whatever is stored untouched rather
    than clearing it to «nobody counted».

    **`Liik` is not among them either, and that is this round's one deliberate
    subtraction.** The panel stopped asking which channel a round used, so the
    editor stops offering to change it: a correction form that could write a
    value the creating panel cannot is a record correctable into a shape it
    could never have been created in, which is the rule this form has always
    kept — read the other way round. Every stored kind is left exactly as it is,
    historical rows keep printing theirs, and nothing in the database refuses
    one (docs/adr/0086 §1).

    Creating a `Kaasamine` is `CompactEngagementForm` and the `+ Kaasamine`
    panel. The two forms stay separate — a creator may default a box and an
    editor may not — and share the one rule that relates the two dates
    (`refuse_deadline_before_engagement`).
    """

    title = forms.CharField(
        label="Keda kaasati",
        max_length=500,
        widget=forms.TextInput(
            attrs={
                "class": "field__input",
                "placeholder": "nt liikmed, kaubandusvaldkonna töögrupp",
            }
        ),
    )
    url = forms.CharField(
        label="Link",
        required=False,
        widget=forms.TextInput(attrs={"class": "field__input", "placeholder": "https://…"}),
        help_text="Vabatahtlik. Kampaanial ei pruugi püsivat avalikku aadressi olla.",
    )
    #: The same two provider pointers the workspace panel asks for, so a
    #: correction made through this form round-trips them rather than dropping
    #: what `+ Kaasamine` stored (docs/adr/0027, amended 2026-09-12).
    smaily_url = provider_link_field("Smaily link", "https://sendsmaily.net/…")
    alchemer_url = provider_link_field("Alchemer link", "https://survey.alchemer.eu/…")
    #: `Kaasamise kuupäev`, as an exact day — the one precision the simplified
    #: panel writes and therefore the one this editor offers (docs/adr/0086 §1).
    #:
    #: An emptied control stores `NULL`, which is «kuupäev teadmata» and is what
    #: the chronology then prints (`app/matters/timeline.py`,
    #: `ENGAGEMENT_DATE_UNKNOWN`). That is the whole point of it being
    #: correctable: a row the old panel stamped with today can now be told the
    #: truth, including the truth that nobody knows.
    #:
    #: **A record dated to a period opens with this box empty, and an empty box
    #: then means «leave the period alone».** The stored anchor of *oktoober
    #: 2025* is `2025-10-01`, which is not a day anybody named and must never be
    #: handed back in a date box (docs/adr/0079 §2) — and clearing the column
    #: because the box the anchor could not be shown in came back empty would
    #: destroy what somebody actually recorded. Typing a day replaces the period
    #: with that day; :attr:`clear_occurred_on` is how the period is removed on
    #: purpose (:meth:`clean`, docs/adr/0086 §1).
    #:
    #: **`initial` is today, and it belongs to the add route rather than to the
    #: editor.** `matters:add_engagement` still posts this form, and every date
    #: box the product opens for a *new* record starts on today. It reaches no
    #: correction, because `_engagement_edit_form` builds its `initial` from the
    #: stored record.
    occurred_on = EstonianDateField(
        label="Kaasamise kuupäev",
        required=False,
        widget=DATE_WIDGET,
        initial=timezone.localdate,
    )
    #: The one way to remove a date recorded as a month, a quarter or a year.
    #:
    #: Rendered **only** for a record that carries one (`shows_clear_occurred_on`),
    #: because for every other row the day box already does this job: emptying it
    #: clears the column. An approximate row cannot use that path — its box is
    #: empty to begin with, and «empty» there has to mean «unchanged» or the
    #: period would be lost by simply pressing `Salvesta`.
    #:
    #: A checkbox rather than a fifth precision chip. The question it asks is
    #: «remove this», not «at what precision is it known», and the control that
    #: asked the second question is exactly what this round retired
    #: (docs/adr/0086 §1).
    clear_occurred_on = forms.BooleanField(
        label="Kustuta salvestatud kuupäev",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "field__check"}),
    )
    #: `Tagasisidet ootame kuni`, and the box that opens or closes a waiting
    #: activity on this file.
    #:
    #: **No `initial` here, unlike the box beside it and unlike the creating
    #: panel.** A correction is filled from the stored value by
    #: `_engagement_edit_form`, which overrides this on every instance; a
    #: default on an *editor* would be a date nobody chose sitting one
    #: `Salvesta` away from being saved. Emptying it means the round is no
    #: longer waiting for anything, which also ends a wait somebody had already
    #: finished — the closure has nothing left to be a closure *of*
    #: (`update_engagement`, docs/adr/0086 §6).
    feedback_deadline = EstonianDateField(
        label="Tagasisidet ootame kuni", required=False, widget=DATE_WIDGET
    )
    #: `Saadud tagasiside / arvamused`, correctable like everything else.
    #:
    #: Editable after the wait is closed, and deliberately: a lawyer who typed
    #: the answers in a hurry has to be able to fix them, and the correction goes
    #: through `correct_engagement` with the Matter's lock, the revision token
    #: and its own audit row, exactly as every other field on this form does
    #: (docs/adr/0086 §6).
    feedback_received = forms.CharField(
        label="Saadud tagasiside / arvamused",
        required=False,
        widget=forms.Textarea(attrs={"class": "field__input", "rows": "3"}),
    )
    note = forms.CharField(
        label="Märkus",
        required=False,
        widget=forms.Textarea(attrs={"class": "field__input", "rows": "2"}),
    )
    #: The half of optimistic concurrency the browser owns, exactly as
    #: `EntryEditForm` carries it: the version the boxes were filled from,
    #: returned unchanged so `correct_engagement` can refuse a stale save
    #: rather than let it overwrite somebody else's correction.
    #:
    #: `required=False`, because an absent token must reach the service as an
    #: empty string and be refused *there* against a real row — a required
    #: field would answer a stale form with a field error that says nothing
    #: about what actually went wrong.
    revision = forms.CharField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args: Any, record: Any = None, **kwargs: Any) -> None:
        """``record`` is the engagement being corrected, when there is one.

        Passed by `_engagement_edit_form` and by nothing else: the add route
        posts this form with no record behind it, and a new record has no stored
        period to protect. It is what :meth:`clean` consults before deciding
        whether an empty day box means «unchanged» or «cleared», so a crafted
        POST arriving without it is held to the ordinary rule and can clear
        nothing it was not shown.
        """
        super().__init__(*args, **kwargs)
        self.record = record

    @property
    def shows_clear_occurred_on(self) -> bool:
        """Whether this instance renders `Kustuta salvestatud kuupäev`.

        Only for a record dated to a period. On every other row the day box
        already clears the column, and a second control saying the same thing is
        a second way to mean one act.
        """
        return bool(getattr(self.record, "has_approximate_date", False))

    @property
    def stored_period_display(self) -> str:
        """The period this record is dated to, for the sentence above the empty
        day box. Empty for every row the box itself can show."""
        return getattr(self.record, "display_date", "") if self.shows_clear_occurred_on else ""

    def clean(self) -> dict[str, Any]:
        """The date the save results in, then the one rule relating the two dates.

        Three answers, and the middle one is why this form takes a ``record``:

        * **a day in the box** — that day, at `EXACT`. This is how a period is
          corrected into the day somebody has since established.
        * **an empty box on a record dated to a period** — the stored anchor and
          the stored precision, unchanged. The box was empty when the form
          opened, because an anchor is not a day and may not be printed as one,
          so an empty box here is the person not touching the date rather than
          the person clearing it (docs/adr/0079 §2, docs/adr/0086 §1).
        * **an empty box anywhere else, or `Kustuta salvestatud kuupäev`** —
          `None` at `EXACT`, which is «kuupäev teadmata». An unknown date has no
          precision, and the service normalises it back to `EXACT` on every path
          besides (`app.matters.services._engagement_precision`).

        The order matters: the deadline rule compares against the *resolved*
        date, which for a preserved period is not in the day box at all
        (`refuse_deadline_before_engagement`).
        """
        cleaned = super().clean() or {}
        typed = cleaned.get("occurred_on")
        if typed is not None:
            cleaned["occurred_on_value"] = typed
            cleaned["occurred_on_precision"] = DatePrecision.EXACT.value
        elif self.shows_clear_occurred_on and not cleaned.get("clear_occurred_on"):
            cleaned["occurred_on_value"] = self.record.occurred_on
            cleaned["occurred_on_precision"] = self.record.occurred_on_precision
        else:
            cleaned["occurred_on_value"] = None
            cleaned["occurred_on_precision"] = DatePrecision.EXACT.value
        refuse_deadline_before_engagement(self, cleaned)
        return cleaned

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

    def clean_smaily_url(self) -> str:
        return clean_provider_link(self, "smaily_url")

    def clean_alchemer_url(self) -> str:
        return clean_provider_link(self, "alchemer_url")

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
    #: Which stored version this box was filled from.
    #:
    #: The half of optimistic concurrency the browser owns. A hidden field rather
    #: than a header, because it has to travel with the textarea it describes and
    #: an autosave posts the form — and `required=False` because a note that has
    #: never been saved has no version, which is a real state and not a missing
    #: value (docs/adr/0077, QA-09).
    revision = forms.CharField(required=False, widget=forms.HiddenInput())


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
    #: The same bound the service enforces, read from the same name.
    #:
    #: Stated here as well so an over-long address is a field error on the form
    #: the person is looking at rather than a message banner after the POST —
    #: and stated *only* as a bound. `link_working_document` is what actually
    #: decides, and it refuses rather than shortening; the number they share is
    #: in `app/documents/limits.py` so the two cannot drift apart again.
    web_url = forms.CharField(
        label="SharePointi aadress",
        max_length=WORKING_DOCUMENT_URL_MAX_LENGTH,
        error_messages={
            "max_length": (
                f"Viide on liiga pikk: lubatud on {WORKING_DOCUMENT_URL_MAX_LENGTH} märki."
            )
        },
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
    #: Saatja's typed half, here too. Saabunud is where a letter from a body
    #: nobody has filed before is most likely to land, so of the three capture
    #: surfaces this is the one that needed it most (§2E).
    sender_name = sender_name_field()
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
        # Intake is where a restricted letter is *first* filed — the template
        # says as much — and it was the one visibility control on the product
        # that explained nothing at all. Same sentence as everywhere else
        # (pilot QA F-01).
        help_text=RESTRICTED_VISIBILITY_HELP,
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
            tail = [organisation for organisation in everything if organisation.pk not in frequent]
            rest.choices = [(organisation.pk, organisation.name) for organisation in tail]
            self.sender_tail_count = len(tail)

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

    def clean_sender_name(self) -> str:
        return clean_typed_organisation_name(self.cleaned_data.get("sender_name"))


# ---------------------------------------------------------------------------
# The Teema workspace: one intention, one form, one save
# ---------------------------------------------------------------------------
#
# Eight small forms where there was one large one. Each asks only what its own
# operation needs, refuses on its own fields and posts to its own endpoint, so a
# save can no longer mean six things at once and there is no longer a single
# `Salvesta` whose meaning depends on which boxes happened to be filled in
# (docs/adr/0075 §2, brief §12, §28).
#
# They reuse the composer's own helpers rather than restating them: the chip
# vocabularies, `_period_anchor`, `EstonianDateField`. The composer itself is
# unchanged and still serves its endpoint (docs/adr/0075 §11).


class MultipleFileInput(forms.FileInput):
    """A file input that accepts more than one file.

    Django refuses `multiple` on the stock widget on purpose — the base field
    would silently keep the last file and drop the rest — so the opt-in is a
    subclass and the field below is what makes the list safe to clean. Not
    `ClearableFileInput`: the clear checkbox describes an existing stored value,
    and every control here is a fresh upload onto a record being created.
    """

    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """Every file the person chose, each validated like a single upload.

    The brief's requirement is that a note, a consultation, a commencement, a
    deadline and a win can each carry *several* files; the alternative — one
    control repeated, or one file per save — is the surface telling somebody to
    save five times for one act (brief §21).

    ``clean`` runs the ordinary ``FileField`` validation over each item, so the
    size and emptiness checks that guard a single upload guard all of them. The
    evidence-format and content-signature checks are ``read_upload``'s and run
    in the service, where a refusal unwinds the whole operation (brief §24).
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("widget", MultipleFileInput(attrs={"class": "visually-hidden"}))
        super().__init__(*args, **kwargs)

    def clean(self, data: Any, initial: Any = None) -> list[Any]:
        single = super().clean
        if isinstance(data, (list, tuple)):
            return [single(item, initial) for item in data if item not in (None, "", False)]
        if data in (None, "", False):
            return []
        return [single(data, initial)]


def require_written_body(raw: Any, message: str) -> str:
    """A rich-text box that a person actually wrote something into.

    ``.strip()`` is not enough here. The editor posts markup, so an untouched
    box arrives as `<p></p>` or `<br>` — non-empty as a string, empty as a
    sentence — and a form that accepted it would create an `Entry` with no
    content and then be refused by `add_entry` with a message pinned to nothing.
    Measured against the plain text, which is what a reader would see.
    """
    body = str(raw or "")
    if not plain_text(body).strip():
        raise forms.ValidationError(message)
    return body


def workspace_attachments(field_id: str) -> MultipleFileField:
    """`Lisa failid` — the same control on every operation that takes evidence.

    One factory rather than six declarations: the label is what a screen reader
    announces for a visually hidden input inside a drop zone, and six copies of
    it are six chances for one of them to say something else.

    **`field_id` is required, and it is not decoration.** Six of these render on
    one page and Django would give every one of them `id_attachments`, which is
    six duplicate ids in one document — invalid, and enough to make
    `getElementById` and a `<label for>` reach the wrong control. The browser
    lane caught the same collision on `id_body` between the current-action box
    and `+ Märge` (docs/adr/0075 §2).
    """
    return MultipleFileField(
        label="Lisa failid",
        required=False,
        widget=MultipleFileInput(attrs={"class": "visually-hidden", "id": field_id}),
    )


class ChipChoices:
    """Chip rendering for a bound-or-unbound hidden choice field.

    The chips write into a hidden input, which is the field that is submitted
    and validated, so the server sees one value however it was chosen and the
    form works with the chips ignored entirely. Read from ``self.data`` rather
    than ``cleaned_data`` for the reason the composer's own version gives: the
    save that most needs its chips back is the one that did not validate.
    """

    def chosen_chip(self, name: str, fallback: str) -> str:
        if getattr(self, "is_bound", False):
            return str(self.data.get(name) or "")  # type: ignore[attr-defined]
        return fallback

    def chips(
        self, name: str, options: Sequence[tuple[str, str]], fallback: str
    ) -> list[dict[str, Any]]:
        chosen = self.chosen_chip(name, fallback)
        return [
            {"value": value, "label": label, "selected": value == chosen}
            for value, label in options
        ]


class CompleteCurrentActionForm(forms.Form):
    """`PRAEGUNE TEGEVUS` — what I did, and the step it finishes.

    **`Mida tegid?` is required.** Completing a task means describing what was
    done about it: a step marked done with nothing said about it leaves the file
    saying only that somebody pressed a button. A file is supplementary and
    never a substitute — the form will not fabricate a body from a filename, the
    action's own text or the word "Tehtud" (brief §5).

    **`action_id` identifies the exact step the form was rendered against**, and
    it is the whole of the stale-tab protection. Hidden, not derived: if this
    form asked the service for "whatever is open", a tab showing a superseded
    step would complete its replacement — a task somebody else set, marked done
    by a person who never saw it. The service re-reads under a lock and refuses
    anything else (brief §6, app/matters/workspace.py).
    """

    use_required_attribute = False

    action_id = forms.UUIDField(widget=forms.HiddenInput())
    body = forms.CharField(
        label="Mida tegid?",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "composer__body",
                "rows": "3",
                "placeholder": "Kirjelda, mida sa selle ülesandega tegid…",
                "data-richtext": "true",
                # Explicit, because `+ Märge` asks its own question into its own
                # box and both fields are called `body`.
                "id": "id_praegune_body",
            }
        ),
    )
    attachments = workspace_attachments("id_praegune_failid")

    def clean_body(self) -> str:
        return require_written_body(self.cleaned_data.get("body"), "Kirjelda, mida tegid.")


class MatterNoteForm(forms.Form):
    """`+ Märge` — something happened, and it is not the current task finishing.

    One box and its files. Deliberately no `Järgmiseks` beside it: recording
    that the ministry rang must not silently complete, replace or create a step,
    and the surest way to guarantee that is a form with no field that could
    (brief §13).
    """

    use_required_attribute = False

    body = forms.CharField(
        label="Mis juhtus või mida tegid?",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "composer__body",
                "rows": "3",
                "placeholder": "Näiteks: ministeerium helistas, uus versioon tuleb reedel.",
                "data-richtext": "true",
                "id": "id_marge_body",
            }
        ),
    )
    attachments = workspace_attachments("id_marge_failid")

    def clean_body(self) -> str:
        return require_written_body(self.cleaned_data.get("body"), "Kirjelda, mis juhtus.")


class CompactEngagementForm(forms.Form):
    """`+ Kaasamine` — who was engaged, when, by when answers were asked for.

    **Four questions and a file box**, which is the whole of the simplified
    round (docs/adr/0086 §2):

    * `Keda kaasati` — required, and the one thing that identifies the record;
    * `Kaasamise kuupäev` — optional, visibly pre-filled with today, clearable;
    * `Saadud tagasiside / arvamused` — optional prose, for the round somebody
      is writing up after the answers already came in.

    `Vastuseid` and the two provider pointers stay, unchanged and optional. They
    cost a reader nothing when they are empty and they are the only place a
    mailing's address lives (docs/adr/0027, amended 2026-09-12).

    **This panel does not ask for a reply-by date at all**, which is where
    docs/adr/0086 §2 finally lands. That record put `Tagasisidet ootame kuni` on
    this form; docs/adr/0091 §2 emptied its default; using it on real files showed
    that neither went far enough. Recording that Koda asked somebody something is a
    *completed act*, and a question about a reply-by date in the middle of it is
    the complexity the department asked to have removed — an empty box is still a
    box that has to be read, understood and skipped, every time.

    The wait is unchanged and is not withdrawn: the column, the `WorkItem`, the
    overdue reading and `Lõpeta kaasamine` are all exactly as docs/adr/0086 built
    them. What moved is where one comes from — `Ootan tagasisidet` on the round's
    own chronology row, which is a decision with somebody's name on it rather than
    a field they were already filling in (lawyer feedback 11, docs/adr/0091 §2).

    **`Liik` is gone from this panel and no longer a `ChipChoices` question.**
    `Küsitlus` / `Koosolek` / `Kirjade voor` was a classification the department
    never read back: the chronology printed it, no surface filtered on it and no
    statistic counted it, so the panel's first control was a decision with no
    consequence — the failure ADR 0054 named for `NextAction.kind`. Every row
    written here is `EngagementKind.OTHER`, the value the column has always
    defaulted to, and the chronology prints no channel for it rather than
    printing «Muu». Historical rows keep the kind they were given and keep
    printing it (docs/adr/0086 §1, `app/matters/timeline.py`).

    **And `Täpsus` is gone with it.** `Kaasamise kuupäev` is an exact day here,
    which is what a round somebody is recording as it happens always has. What
    docs/adr/0082 bought — an engagement remembered as «oktoobris» — is kept
    where it was actually earned: existing approximate rows render and sort
    exactly as they did, and `EngagementForm` refuses to overwrite one with an
    empty day box (docs/adr/0086 §1).
    """

    use_required_attribute = False

    audience = forms.CharField(
        label="Keda kaasati",
        required=False,
        max_length=500,
        widget=forms.TextInput(
            attrs={
                "class": "field__input field__input--compact",
                "placeholder": "nt liikmed, kaubandusvaldkonna töögrupp",
            }
        ),
    )
    response_count = forms.IntegerField(
        label="Vastuseid",
        required=False,
        min_value=0,
        max_value=1_000_000,
        widget=forms.TextInput(
            attrs={
                "class": "field__input field__input--compact",
                "inputmode": "numeric",
                "autocomplete": "off",
                "placeholder": "14",
            }
        ),
    )
    smaily_url = provider_link_field("Smaily link", "https://sendsmaily.net/…")
    alchemer_url = provider_link_field("Alchemer link", "https://survey.alchemer.eu/…")
    #: **The date the panel never asked for**, and now an exact day.
    #:
    #: The view used to stamp `timezone.localdate()` on every row it wrote, so a
    #: consultation from March, recorded in September, was filed as having
    #: happened in September — a false fact, written behind the person's back,
    #: with no box on the screen to contradict it.
    #:
    #: Pre-filled with today because the overwhelming case is recording
    #: something that just happened, and re-typing today's date every time is
    #: friction people complain about. What matters is that the default is
    #: *visible*: it can be read, changed, and emptied. A cleared box stores
    #: `NULL` — «kuupäev teadmata» is a fact `MatterEngagement` has always been
    #: able to hold, and **nothing downstream puts today back**: the value the
    #: service receives is what this control says, and the service invents
    #: nothing (docs/adr/0078 §2, docs/adr/0086 §2).
    occurred_on = EstonianDateField(
        label="Kaasamise kuupäev",
        required=False,
        widget=EstonianDateInput(),
        initial=timezone.localdate,
    )
    #: `Saadud tagasiside / arvamused` — what came back, where no separate file
    #: exists.
    #:
    #: On the *creation* panel as well as on the completion form, because a
    #: round is routinely written up after it finished: somebody records the
    #: consultation and the answers in one save, and a field that only existed
    #: behind `Lõpeta kaasamine` would make them file an empty round and then
    #: immediately close it (docs/adr/0086 §5).
    #:
    #: Writing here does **not** close a wait. Recording what came back and
    #: deciding the round is over are two acts, and only the second is a
    #: decision somebody's name goes on.
    feedback_received = forms.CharField(
        label="Saadud tagasiside / arvamused",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "field__input field__input--compact",
                "rows": "3",
                "placeholder": "Mida vastati? Nt liikmed toetasid, kaubandus soovis pikemat aega.",
            }
        ),
    )
    attachments = workspace_attachments("id_kaasamine_failid")

    def clean_audience(self) -> str:
        audience = (self.cleaned_data.get("audience") or "").strip()
        if not audience:
            raise forms.ValidationError("Kirjuta, keda kaasati.")
        return audience

    def clean_smaily_url(self) -> str:
        return clean_provider_link(self, "smaily_url")

    def clean_alchemer_url(self) -> str:
        return clean_provider_link(self, "alchemer_url")

    def clean(self) -> dict[str, Any]:
        """The exact-day answer, then the one rule relating the two dates.

        `occurred_on_value` and `occurred_on_precision` are still the names the
        view and the service read, and they still travel together — the column
        pair is unchanged and every row written here is `EXACT`. What went is
        the control that could say anything else (docs/adr/0086 §1).
        """
        cleaned = super().clean() or {}
        cleaned["occurred_on_value"] = cleaned.get("occurred_on")
        cleaned["occurred_on_precision"] = DatePrecision.EXACT.value
        # No `refuse_deadline_before_engagement` here any more: this panel has no
        # reply-by box to relate to the engagement date. The rule is unchanged and
        # is kept by the two surfaces that still write one — `EngagementForm` and
        # `open_engagement_feedback_wait` (docs/adr/0091 §2).
        return cleaned


class EngagementWaitForm(forms.Form):
    """`Ootan tagasisidet` — one date, and the decision that opens a wait.

    The whole of the reply-by question, moved off `+ Kaasamine` and onto the
    round's own chronology row. Recording that Koda asked somebody something is a
    completed act; deciding that the file is *waiting* on an answer is a second
    act, and only the second one puts a row on somebody's desk
    (lawyer feedback 11, docs/adr/0091 §2).

    **The date is required here**, unlike the field this replaces. This form
    exists only to start a wait, so an empty box would be a save that does
    nothing — and the person who means «no wait» simply does not open the
    disclosure. Nothing about the wait itself changed: it is still one `WorkItem`,
    still read on `PRAEGUNE TEGEVUS`, still ended by `Lõpeta kaasamine`
    (docs/adr/0086 §3, §6).

    The three quick spans stay with it, so asking for the commonest wait is still
    one click. They write into the box beside them and store nothing of their own,
    which is what makes the control work with scripting off.

    ``revision`` is the version the row was rendered from, carried through the
    round trip so the service can refuse a save whose record has moved on — the
    same hidden field `EngagementFeedbackForm` carries, for the same reason.
    """

    use_required_attribute = False

    feedback_deadline = EstonianDateField(
        label="Tagasisidet ootame kuni",
        required=False,
        widget=EstonianDateInput(),
    )
    revision = forms.CharField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        #: Its own ids, because a chronology may hold several waiting rounds and
        #: each renders this form. Two of them sharing `id_feedback_deadline`
        #: would put duplicate ids in the document and make a `<label for>` reach
        #: the wrong row's box (docs/adr/0086 §6, `attach_feedback_form`).
        self.row_id = kwargs.pop("row_id", "")
        kwargs.setdefault("auto_id", f"id_ootus{self.row_id}_%s")
        super().__init__(*args, **kwargs)

    def clean_feedback_deadline(self) -> Any:
        from app.matters.services import ENGAGEMENT_FEEDBACK_NEEDS_A_DAY

        value = self.cleaned_data.get("feedback_deadline")
        if value is None:
            raise forms.ValidationError(ENGAGEMENT_FEEDBACK_NEEDS_A_DAY)
        return value


class EngagementFeedbackForm(forms.Form):
    """`Lõpeta kaasamine` — what came back, and the decision that the round is over.

    One textarea, the ordinary file control and a revision token. It is
    deliberately not `EngagementForm` with fewer fields: the two answer different
    questions, and a completion form carrying the audience, the links and both
    dates would invite somebody finishing a round to change what the round *was*
    (docs/adr/0086 §6).

    **Nothing is required.** Pressing the button with an empty box records that
    the wait is over and nothing came back, which is a real and common outcome —
    a completion that demanded prose would make «keegi ei vastanud» the one
    result a lawyer could not file. What the button promises is therefore the
    decision, not the prose.
    """

    #: Pre-filled from the record by the view, because a round may already carry
    #: feedback somebody typed when they created it: an empty box there would
    #: invite them to overwrite their own text with nothing.
    feedback_received = forms.CharField(
        label="Saadud tagasiside / arvamused",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "field__input field__input--compact",
                "rows": "3",
                "placeholder": "Mis tagasisidet saabus? Jäta tühjaks, kui vastuseid ei tulnud.",
            }
        ),
    )
    attachments = workspace_attachments("id_kaasamine_tagasiside_failid")
    #: The version the box was filled from. Same contract as `EngagementForm`'s,
    #: and `required=False` for the same reason: an absent token has to be
    #: refused by the service against a real row rather than by a field error
    #: that says nothing about what went wrong.
    revision = forms.CharField(required=False, widget=forms.HiddenInput())


class CompactImportantDateForm(forms.Form):
    """`+ Oluline tähtaeg` — a milestone somebody announced, and its letter.

    The canonical `MatterImportantDate` semantics, unchanged: `Täpne päev` /
    `Kuu` / `Kvartal` / `Aasta`, normalised through `app.workflow.dates` so a
    quarter recorded here is the same stored anchor as a quarter recorded on
    `Olulised tähtajad`. `HALF_YEAR` remains a stored precision and still
    renders on the rows that carry it (docs/adr/0074 §11, docs/adr/0079 §1).

    **`Aasta` was the missing fourth**, and its absence was the reason the
    panel had to guess: a consultation somebody was told about *«järgmisel
    aastal»* had no chip, so the person picked a day and the form derived a
    quarter from it. The control now asks for the year outright and the
    derivation is gone (`_period_anchor`).

    Creates no `NextAction`, then or ever: a date the file has to live with is
    not an instruction to a person.
    """

    use_required_attribute = False

    deadline_title = forms.CharField(
        label="Mis tähtaeg",
        required=False,
        max_length=2000,
        widget=forms.TextInput(
            attrs={
                "class": "field__input field__input--compact",
                "placeholder": "nt kooskõlastusringi lõpp",
            }
        ),
    )
    attachments = workspace_attachments("id_tahtaeg_failid")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.precision_choices = precision_choices()
        self.fields.update(_precision_fields("deadline", date_label="Kuupäev"))

    @property
    def precision_chips(self) -> list[dict[str, Any]]:
        return _precision_chips(self, "deadline_precision")

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        title = (cleaned.get("deadline_title") or "").strip()
        if not title:
            self.add_error("deadline_title", "Kirjuta, mis tähtaeg see on.")
        anchor, end, precision = _period_anchor(self, "deadline")
        if anchor is None or end is None:
            if not self.errors:
                self.add_error(
                    _precision_answer_field("deadline", precision),
                    "Oluline tähtaeg vajab kuupäeva või perioodi.",
                )
            return cleaned
        cleaned["important_date_kwargs"] = {
            "title": title,
            "date_value": anchor,
            "period_end": end,
            "date_precision": precision,
        }
        return cleaned


class CompactEffectiveDateForm(forms.Form):
    """`+ Jõustumine` — what commences and when.

    A door onto the canonical `MatterEffectiveDate`, not a second commencement
    model and not an `Entry` pretending to be one. Both halves or neither,
    refused on whichever is missing: a commencement with no date is a sentence,
    and a date with nothing commencing on it is a number (brief §18).

    **A commencement is frequently known to a month or a year**, which is why
    the model has carried `date_precision` and `period_end` since Stage 2G and
    why `EffectiveDateKind` has a *kuupäev täpsustamisel* value beside them.
    This panel took a day and stored `EXACT`; somebody told the act commences
    *«2027»* had to either name 1 January or record nothing. It now asks with
    the same four chips as everything else (docs/adr/0079 §1).

    The period is stored in full. A `QUARTER` row whose `period_end` equalled
    its anchor would claim, to every reader of the table, that a three-month
    commencement was over on its first day.
    """

    use_required_attribute = False

    effective_title = forms.CharField(
        label="Mis jõustub",
        required=False,
        max_length=2000,
        widget=forms.TextInput(
            attrs={
                "class": "field__input field__input--compact",
                "placeholder": "nt pakendiseaduse muudatused",
            }
        ),
    )
    attachments = workspace_attachments("id_joustumine_failid")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.precision_choices = precision_choices()
        group = _precision_fields("effective", date_label="Jõustub")
        # The date box keeps the name it has had since this panel existed. A
        # tidier `effective_date` would read better and would rename a control
        # that a browser test, a process-strip test and anybody's muscle memory
        # already know, for no behaviour at all.
        group["effective_on"] = group.pop("effective_date")
        self.fields.update(group)

    @property
    def precision_chips(self) -> list[dict[str, Any]]:
        return _precision_chips(self, "effective_precision")

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        title = (cleaned.get("effective_title") or "").strip()
        if not title:
            self.add_error("effective_title", "Kirjuta, mis jõustub.")
        anchor, end, precision = _period_anchor(self, "effective", date_field="effective_on")
        if anchor is None or end is None:
            if not self.errors:
                field = _precision_answer_field("effective", precision)
                self.add_error(
                    "effective_on" if field == "effective_date" else field,
                    "Märgi, millal see jõustub.",
                )
            return cleaned
        cleaned["effective_date_kwargs"] = {
            "description": title,
            "date_value": anchor,
            "period_end": end,
            "date_precision": precision,
        }
        return cleaned


class CompactWorkVictoryForm(forms.Form):
    """`+ Töövõit` — what changed, and the evidence that it did.

    One sentence, its period and its files. A win closes nothing, completes
    nothing and is recorded against the period it belongs to rather than the day
    the file finishes (brief §19).

    **The period is asked for, and nothing is invented when it is missing.**
    This panel used to send none at all, so every win recorded through it
    reached the database as `period_date = NULL` — invisible to
    `?toovoit=<aasta>`, absent from the reporting rail, and indistinguishable
    from an imported row whose period genuinely is unknown. The obvious repairs
    are both worse than the gap: today's date files a 2019 win in the year
    somebody typed it up, and the current year does the same thing less
    visibly. So the person says when, at whatever precision they have — most
    often `Aasta` (docs/adr/0079 §10, Stage-2G brief 22).

    The date box stays **blank**. `Täpne päev` is the chip that is selected
    first because it is the commonest answer elsewhere, and a pre-filled today
    underneath it would be a claim nobody made.

    Rows that already have no period keep none. There is no backfill and no
    migration: a win whose period was never recorded does not acquire one
    because this form learned to ask.
    """

    use_required_attribute = False

    victory_change = forms.CharField(
        label="Mis muutus",
        required=False,
        max_length=2000,
        widget=forms.TextInput(
            attrs={
                "class": "field__input field__input--compact",
                "placeholder": "nt üleminekuaeg väiketootjatele pikendati 2028. aastani",
            }
        ),
    )
    attachments = workspace_attachments("id_toovoit_failid")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.precision_choices = precision_choices()
        self.fields.update(_precision_fields("victory", date_label="Millal"))

    @property
    def precision_chips(self) -> list[dict[str, Any]]:
        return _precision_chips(self, "victory_precision")

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        change = (cleaned.get("victory_change") or "").strip()
        if not change:
            self.add_error("victory_change", "Kirjuta, mis muutus.")
        anchor, end, precision = _period_anchor(self, "victory")
        if anchor is None or end is None:
            if not self.errors:
                self.add_error(
                    _precision_answer_field("victory", precision),
                    "Märgi, millal see töövõit saavutati.",
                )
            return cleaned
        cleaned["work_victory_kwargs"] = {
            "title": change[:2000],
            "period_date": anchor,
            "period_end": end,
            "date_precision": precision,
        }
        return cleaned


class CompactWebsiteOverviewForm(forms.Form):
    """`+ Ülevaade / uudis` — a plan, or a page that is already up.

    docs/adr/0081 §1 shaped this panel as one button and no fields, on the
    reasoning that at the moment somebody decides a Matter should be written up
    there is no address and no publication date, so every field would be asking
    them to invent something the real page contradicts a week later. That
    reasoning is right about the case it describes and wrong about the one it
    did not: a lawyer frequently records the write-up **after** the page is
    already published, and the panel made them file a plan and then publish it
    from a second control to say so. Worse, what they met first was a panel with
    no fields at all and a button saying `Salvesta` — which reads as an unusable
    text box rather than as a complete form (docs/adr/0083).

    So the two questions are here, **optional and empty**, and the form answers
    in one of three ways:

    * neither filled — the plan, exactly as before;
    * an address, with or without a day — a page that already exists, recorded
      in one act;
    * a day and no address — a refusal naming the address, with what was typed
      still in the boxes. A date on its own is not a plan with a note attached
      and it is not a publication: it is a fact about a page nobody can open.

    **The address alone is enough**, and that is docs/adr/0089 §8 replacing
    docs/adr/0081 §2. Lawyer testing found the commonest real save to be an
    address pasted out of a search result or a mail, where the page plainly
    exists and its publication date is not known, not on the page and not worth
    a hunt. The old rule refused that save, and what people filed instead was
    today — a date nobody had checked, on the one column that is the person's
    own statement.

    **No `initial` on the date and no default written into it by anything.**
    Not by the form, not by the service, not by the model and — since
    docs/adr/0089 §8 — not by the browser either: the `data-publication-default`
    island docs/adr/0085 §3 introduced is withdrawn, because a date that appears
    in the box the instant somebody pastes a link is a date they accept without
    reading. An empty box means *unknown*, and unknown is now a thing this
    record can hold, so the honest default is nothing at all.

    A pre-filled date would also make «neither filled» unreachable — the
    property docs/adr/0083 §2 refused an `initial` to protect — so the two
    reasons now point the same way.

    Still no title, no description and no attachment, and **no kind selector**.
    The record's content is the address and, when it is known, the day; which of
    the two kinds of publication it is, is what the address says
    (docs/adr/0081 §2, docs/adr/0085 §1).
    """

    use_required_attribute = False

    #: The same `CharField`-not-`URLField` shape as `WebsiteOverviewLinkForm`,
    #: and for the same reason: the rule is `normalize_overview_news_url`'s, and
    #: letting Django's validator answer first would give a refused address a
    #: different sentence depending on which layer caught it.
    url = forms.CharField(
        label="Avaldatud ülevaate või uudise link",
        required=False,
        max_length=WEBSITE_OVERVIEW_URL_MAX_LENGTH,
        widget=forms.TextInput(
            attrs={
                "class": "field__input field__input--compact",
                "inputmode": "url",
                "autocomplete": "off",
                "placeholder": "https://…",
            }
        ),
    )
    #: Optional, empty, and with no default from anywhere — see the class
    #: docstring. `None` reaches the service as `None` and is stored as `NULL`,
    #: which means *the day is unknown* (docs/adr/0089 §8).
    published_on = EstonianDateField(
        label="Avaldamise kuupäev",
        required=False,
        widget=EstonianDateInput(),
        help_text="Kui kuupäev ei ole teada, jäta tühjaks.",
    )

    def clean(self) -> dict[str, Any]:
        """Nothing, an address, or a refusal naming the address that is missing.

        The address is validated through the service's own door so that a
        refused address is refused here in the words it is refused in
        everywhere, and under the box it was typed into.

        A date without an address is still refused, and that half has not
        changed: a publication date is a fact *about a page*, so one filed with
        nothing to open is a claim about nothing. The other half — an address
        without a date — is now an ordinary publication (docs/adr/0089 §8).
        """
        from app.matters.services import (
            WEBSITE_OVERVIEW_NEEDS_LINK,
            normalize_overview_news_url,
        )

        cleaned = super().clean() or {}
        raw_url = (cleaned.get("url") or "").strip()
        published_on = cleaned.get("published_on")

        url = ""
        if raw_url:
            try:
                url = normalize_overview_news_url(raw_url)
            except DomainError as error:
                self.add_error("url", str(error))
                return cleaned

        if published_on is not None and not url:
            self.add_error("url", WEBSITE_OVERVIEW_NEEDS_LINK)

        # What the view acts on. `None` is the plan; a pair is a publication,
        # whose second member may itself be `None` — an address recorded without
        # a known publication date.
        cleaned["publication"] = (url, published_on) if url else None
        return cleaned


class WebsiteOverviewLinkForm(forms.Form):
    """The address and the day, for a publication or for a correction to one.

    One form for both because they ask exactly the same two questions and must
    enforce exactly the same two rules — a second class would be a second place
    for the address rule to be written out, and the day it disagreed with the
    first would be the day a correction accepted an address a publication would
    have refused. *Which* of the two operations a POST is answering is decided
    by the route it arrived on and by the record's own state under a lock, never
    by the form (docs/adr/0081 §3).

    ``revision`` is the version the form was filled from, carried through the
    round trip so the service can refuse a save whose record has moved on. The
    same hidden field `EntryEditForm` carries, for the same reason.
    """

    use_required_attribute = False

    #: A `CharField` rather than a `URLField`, exactly as `provider_link_field`
    #: is one: the rule belongs to `normalize_overview_news_url`, which is what
    #: the service enforces, and running Django's own validator first would
    #: answer a refused address with a different sentence depending on which
    #: layer happened to catch it.
    url = forms.CharField(
        label="Avaldatud ülevaate või uudise link",
        required=False,
        max_length=WEBSITE_OVERVIEW_URL_MAX_LENGTH,
        widget=forms.TextInput(
            attrs={
                "class": "field__input field__input--compact",
                "inputmode": "url",
                "autocomplete": "off",
                "placeholder": "https://…",
            }
        ),
    )
    #: Optional, empty, and **no longer pre-filled with today**.
    #:
    #: docs/adr/0083 §2 gave this box `initial=timezone.localdate` on the
    #: reasoning that somebody who opens a publish form has chosen the published
    #: path, so today is a helpful suggestion they can change. Lawyer testing in
    #: September 2026 measured what that actually produces: a date is in the box
    #: before anybody has thought about it, it is already correct-looking, and
    #: it is accepted. The file then holds a publication date the application
    #: proposed and nobody verified — which is indistinguishable, afterwards,
    #: from one somebody knew (docs/adr/0089 §8).
    #:
    #: So the box opens empty, and empty means *unknown*. It is also the box a
    #: correction uses to **clear** a date that turned out to be a guess, which
    #: is the gesture the lawyer feedback asked for by name: the value goes to
    #: the service as `None`, is stored as `NULL`, and nothing puts today back.
    published_on = EstonianDateField(
        label="Avaldamise kuupäev",
        required=False,
        widget=EstonianDateInput(),
        help_text="Kui kuupäev ei ole teada, jäta tühjaks.",
    )
    revision = forms.CharField(required=False, widget=forms.HiddenInput())

    def clean_url(self) -> str:
        """The service's own rule, reported under the box somebody typed it in.

        Empty is refused *here* rather than left to the service, because this
        form is only ever used for an operation that requires an address — and a
        refusal that lands on the field is a refusal the person can see beside
        what they typed.
        """
        from app.matters.services import (
            WEBSITE_OVERVIEW_NEEDS_LINK,
            normalize_overview_news_url,
        )

        try:
            url = normalize_overview_news_url(self.cleaned_data.get("url"))
        except DomainError as error:
            raise forms.ValidationError(str(error)) from error
        if not url:
            raise forms.ValidationError(WEBSITE_OVERVIEW_NEEDS_LINK)
        return url

    # There is deliberately **no `clean_published_on`**. It used to refuse an
    # empty box with `WEBSITE_OVERVIEW_NEEDS_DATE`; since docs/adr/0089 §8 an
    # empty box is a valid answer meaning *the day is unknown*, on a publication
    # and on a correction alike. `EstonianDateField` still refuses a value that
    # is not a date, which is the only thing left to refuse here.


def _external_position_organisation_field() -> forms.ModelChoiceField:
    """`Organisatsioon` — one institution, from the one catalogue, required.

    The `Adressaat` control's shape with one difference, and the difference is
    the whole product rule: there is no named blank option, because «Määramata»
    is not an answer to *whose position is this* (`attach_organisation_picker`).

    ``required=False`` at field level and refused in `clean` instead, so the
    sentence a person reads is `EXTERNAL_POSITION_NEEDS_ORGANISATION` rather
    than Django's generic one — and so that a panel answered only through the
    typed box, which is the «this body is not in your catalogue» case, is not
    refused before the two halves have been read together.
    """
    from app.organisations.models import Organisation

    return forms.ModelChoiceField(
        label="Organisatsioon",
        queryset=Organisation.objects.none(),
        required=False,
        widget=OrganisationRadioSelect(attrs={"class": "chip__input"}),
    )


def _external_position_link_field() -> forms.CharField:
    """`Link` — where the position was published, if it was published anywhere.

    A `CharField` rather than a `URLField`, exactly as `provider_link_field` is
    one: the rule belongs to
    `app.matters.services.normalize_external_position_url`, which is what the
    service enforces, and letting Django's own validator answer first would give
    one refused address two different sentences depending on which layer caught
    it.
    """
    return forms.CharField(
        label="Link",
        required=False,
        max_length=EXTERNAL_POSITION_URL_MAX_LENGTH,
        widget=forms.TextInput(
            attrs={
                "class": "field__input field__input--compact",
                "inputmode": "url",
                "autocomplete": "off",
                "placeholder": "https://…",
            }
        ),
    )


def _external_position_summary_field() -> forms.CharField:
    """`Seisukoht` — what the other organisation actually said, in writing.

    Optional on its own and **one of the three answers to the source rule**: a
    member association's two-sentence reply, or what an official said on the
    telephone, is a complete record with no file and no published address
    behind it. It was `Selgitus` and a caption under a link before this, which
    is what made ordinary written feedback unrecordable (docs/adr/0084 §2, §3,
    amended 2026-09-16).

    Three rows rather than two, because the question changed: a caption under a
    link is one line, and a position somebody received is a short paragraph. It
    is still bounded at `EXTERNAL_POSITION_SUMMARY_MAX_LENGTH` — a box that
    invited pages would make this record a second, worse copy of the document
    that belongs beside it.
    """
    return forms.CharField(
        label="Seisukoht",
        required=False,
        max_length=EXTERNAL_POSITION_SUMMARY_MAX_LENGTH,
        widget=forms.Textarea(
            attrs={
                "class": "field__input field__input--compact",
                "rows": 3,
                "placeholder": "nt toetab eelnõu, kuid soovib pikemat üleminekuaega",
            }
        ),
    )


def _external_position_lawyer_note_field() -> forms.CharField:
    """`Juristi märkus` — this office's own reading of what somebody else said.

    Optional, bounded, and **structurally separate from `Seisukoht`** rather than
    a second paragraph inside it. Before this column a lawyer's assessment had
    two homes: appended to the position, where the file recorded the ministry as
    having said it, or a `Märge` that then said nothing about which position it
    was about. The first is the serious one — a professional record that
    attributes this office's criticism to the body being criticised is a record
    that lies (docs/adr/0091 §4).

    **It satisfies nothing.** The source rule counts the position, the address
    and the file; a save whose only content is this is refused exactly as an
    empty one is, because a record of Koda's opinion of something nobody can read
    is a record of nothing.
    """
    return forms.CharField(
        label="Juristi märkus",
        required=False,
        max_length=EXTERNAL_POSITION_LAWYER_NOTE_MAX_LENGTH,
        widget=forms.Textarea(
            attrs={
                "class": "field__input field__input--compact",
                "rows": 2,
                "placeholder": "nt nende põhjendus ei arvesta liikmete kulumõjuga",
            }
        ),
    )


def _external_position_source_label_field() -> forms.CharField:
    """`Allikas` — what to call a collection of answers with no single author.

    «Tööstusettevõtete küsitlus», «Liikmete kirjavastused». Offered on the
    received-feedback panel only, because it is the only kind of record that may
    carry one, and it exists so that a survey of 234 companies does not have to
    be filed under an invented organisation or under one arbitrary respondent
    standing for the rest (docs/adr/0091 §3.3).

    It is not a title and not a summary: it names *where the answers came from*,
    so the chronology row has an author to print. A record that needs more than a
    short name needs an organisation or a file.
    """
    return forms.CharField(
        label="Allikas",
        required=False,
        max_length=EXTERNAL_POSITION_SOURCE_LABEL_MAX_LENGTH,
        widget=forms.TextInput(
            attrs={
                "class": "field__input field__input--compact",
                "autocomplete": "off",
                "placeholder": "nt Tööstusettevõtete küsitlus",
            }
        ),
    )


def _external_position_engagement_field() -> forms.ModelChoiceField:
    """`Seotud kaasamine` — the round this position answered, where it answered one.

    Optional and empty by default, and the empty label says so in words rather
    than with a dash: the commonest external position is unsolicited, and a
    control whose blank option read «—» would look like a question somebody
    forgot to answer (docs/adr/0084 §4).

    The queryset is narrowed per instance, in `__init__`, to this Matter's
    engagements as *this reader* may see them. It is the field's own queryset
    and not only its rendered choices, because that is what validates a posted
    id: a crafted POST naming a consultation on another file, or one restricted
    below the Matter, is refused by the field before the service's own
    cross-Matter check ever runs (AUTH-003).
    """
    from app.matters.models import MatterEngagement

    return forms.ModelChoiceField(
        label="Seotud kaasamine",
        queryset=MatterEngagement.objects.none(),
        required=False,
        empty_label="Ei ole seotud kaasamisega",
        widget=SELECT_WIDGET,
    )


class ExternalPositionFieldsMixin:
    """What both `Väline seisukoht` forms share: the catalogue, the date, the source.

    Two forms, because recording a position and correcting one are different
    transactions with different services — and one set of questions, because a
    record must be correctable into exactly the shapes it could have been
    created in. A second spelling of the source rule is a second place for it to
    drift, and the day the two disagreed would be the day a correction accepted
    a position the panel would have refused (docs/adr/0084 §2).

    The source is **one of three** — the written `Seisukoht`, a public address,
    or an attached file — and the two forms differ only in where the third one
    is counted from: an upload control the person is holding, or the link table
    the record already carries (docs/adr/0084 §3, amended 2026-09-16).
    """

    fields: dict[str, forms.Field]
    cleaned_data: dict[str, Any]
    add_error: Any
    errors: Any
    is_bound: bool

    #: Whether this surface may ask for an `Allikas` — true on received feedback
    #: and false everywhere else.
    #:
    #: A class attribute rather than a check on a posted value, because the
    #: distinction is a property of the *panel*: `+ Teiste arvamus` has no such
    #: box, so a POST carrying `source_label` to it is a value that did not come
    #: off a page and the shared `clean` refuses the record as unauthored. The
    #: service refuses it again under the row lock, because a form is not a
    #: boundary (docs/adr/0091 §3.3).
    allows_source_label: bool = False

    #: Where the organisation radio group stops being chips and starts being
    #: searchable tail. `None` on a form with no viewer — no usage to rank by
    #: means no shortlist, and everything is a chip.
    organisation_split: int | None = None
    organisation_offered: list[Any] = []

    @property
    def organisation_chip_choices(self) -> list[Any]:
        offered = list(cast(Any, self)["organisation"])
        return offered if self.organisation_split is None else offered[: self.organisation_split]

    @property
    def organisation_tail_choices(self) -> list[Any]:
        if self.organisation_split is None:
            return []
        return list(cast(Any, self)["organisation"])[self.organisation_split :]

    @property
    def precision_chips(self) -> list[dict[str, Any]]:
        return _precision_chips(cast(Any, self), f"{EXTERNAL_POSITION_PREFIX}_precision")

    def clean_organisation_name(self) -> str:
        return clean_typed_organisation_name(self.cleaned_data.get("organisation_name"))

    def _clean_external_position(self, *, has_file: bool) -> dict[str, Any]:
        """The three rules the service will enforce, reported beside the boxes.

        Stated here as well as in `app.matters.services` and not *instead* of
        it. A form is what one browser was shown and a POST is what arrives, so
        the service is the boundary; this is where a person sees the refusal
        under the control that caused it, with everything they typed still in
        the other ones (docs/adr/0084 §3).
        """
        from app.matters.services import (
            EXTERNAL_POSITION_NEEDS_AUTHOR_OR_LABEL,
            EXTERNAL_POSITION_NEEDS_ORGANISATION,
            EXTERNAL_POSITION_NEEDS_SOURCE,
            normalize_external_position_url,
        )

        cleaned = self.cleaned_data

        # 1. Whose position it is. An organisation, from either half of the
        #    picker — or, on received feedback only, the `Allikas` naming the
        #    collection of answers it came from. Which of the two sentences a
        #    person reads depends on which answers are actually available to
        #    them, because «vali organisatsioon» on a survey of 234 companies is
        #    the refusal that made somebody invent one (docs/adr/0091 §3.3).
        named = bool(cleaned.get("organisation")) or bool(
            (cleaned.get("organisation_name") or "").strip()
        )
        label = (cleaned.get("source_label") or "").strip()
        cleaned["source_label"] = label
        if self.allows_source_label:
            if not named and not label:
                self.add_error("organisation_name", EXTERNAL_POSITION_NEEDS_AUTHOR_OR_LABEL)
        elif not named:
            self.add_error("organisation_name", EXTERNAL_POSITION_NEEDS_ORGANISATION)

        # The lawyer's own note, trimmed the way the service trims it — and
        # deliberately **not** read by rule 4 below. A record holding only this
        # office's comment about a position nobody can read is refused exactly as
        # an empty one is (docs/adr/0091 §4).
        cleaned["lawyer_note"] = (cleaned.get("lawyer_note") or "").strip()

        # 2. The address, through the service's own door, so a hostile scheme is
        #    refused here in the words it is refused in everywhere.
        raw_url = (cleaned.get("url") or "").strip()
        url = ""
        if raw_url:
            try:
                url = normalize_external_position_url(raw_url)
            except DomainError as error:
                self.add_error("url", str(error))
            else:
                cleaned["url"] = url

        # 3. The written position, trimmed the way the service trims it, so the
        #    two cannot disagree about whether three spaces are a position.
        summary = (cleaned.get("summary") or "").strip()
        cleaned["summary"] = summary

        # 4. A source — the text, the address, or the file. Reported on
        #    `summary` because that is the first of the three on the screen and
        #    the one a person most often meant to have filled; the other two are
        #    named in the sentence itself, which is how one refusal points at
        #    three controls without being printed three times. Suppressed while
        #    the address is already refused on its own field, so a hostile URL
        #    gets one sentence about what is wrong with it rather than two.
        if not summary and not url and not has_file and not self.errors.get("url"):
            self.add_error("summary", EXTERNAL_POSITION_NEEDS_SOURCE)

        # The date control's answer, resolved to an anchor and a precision.
        anchor, precision = external_position_period(cast(Any, self))
        cleaned["stated_on_value"] = anchor
        cleaned["stated_on_precision"] = precision
        return cleaned


class CompactExternalPositionForm(ExternalPositionFieldsMixin, forms.Form):
    """One panel behind two chips — `+ Meile saadetud tagasiside` and `+ Teiste arvamus`.

    Two professional facts with one shape, which is docs/adr/0091 §3's whole
    claim: an author, a source a colleague can open, an optional date at the
    precision it is known to, and — new in this round — an optional `Juristi
    märkus` that is never part of the source. Four models would have been four
    sets of validation for one set of rules, so the distinction is `provenance`
    and this class is instantiated twice with it fixed.

    **The provenance is a class attribute, not a field.** It is decided by which
    chip a person opened, so there is nothing for the browser to post and nothing
    a crafted POST can move: `+ Teiste arvamus` cannot be made to file received
    feedback by adding a parameter, because neither instance reads one. It is the
    reasoning `NextActionForm` gives for having no `kind` field, applied to a
    distinction the page genuinely does decide (ADR 0052 §1, docs/adr/0091 §3.5).

    **`Organisatsioon` is required for `Teiste arvamus` and not for received
    feedback**, and that asymmetry is the only place the authorship rule bends. A
    survey of 234 industrial companies that produced 58 answers has no single
    author; `Allikas` names the collection instead, and it is offered on that
    panel alone (docs/adr/0091 §3.3).

    Six controls, of which two are required together and four are not: the
    institution, a source — a link, a file, or both — and then the date, the
    explanation and the consultation it answered, each of which a real record
    frequently does not have.

    **The date opens on today, and clearing it is a real answer.** This panel
    argued itself out of a default once, on the ground that a position is filed
    some time after it was stated. What that produced in use was the opposite of
    an honest record: the commonest case is feedback that arrived this week and
    is being written up now, and a box that started empty made «kuupäev
    teadmata» the path of least resistance for exactly those. So the box carries
    `initial=timezone.localdate`, as `+ Kaasamine`'s does and for the reason
    docs/adr/0078 §2 allows one — the default is **visible** in the box before
    anything is saved, readable, changeable and clearable, which is a suggestion
    a person accepts rather than a stamp applied behind their back. An emptied
    box still stores `NULL` and still reads «Kuupäev teadmata», and
    `ExternalPositionEditForm` deliberately declares no such default: a
    correction that opened a recorded position showing today would be one
    `Salvesta` from a change nobody made (docs/adr/0084 §2, amended 2026-09-16).
    """

    use_required_attribute = False

    organisation = _external_position_organisation_field()
    organisation_name = _typed_organisation_field("Uus organisatsioon")
    url = _external_position_link_field()
    summary = _external_position_summary_field()
    lawyer_note = _external_position_lawyer_note_field()
    source_label = _external_position_source_label_field()
    engagement = _external_position_engagement_field()
    stated_on = EstonianDateField(
        label="Seisukoha kuupäev",
        required=False,
        widget=EstonianDateInput(),
        initial=timezone.localdate,
    )
    attachments = workspace_attachments("id_valine_seisukoht_failid")

    #: Which record this instance writes, decided by the chip rather than posted.
    provenance: str = ExternalPositionProvenance.DISCOVERED.value
    #: What every rendered id on this panel is prefixed with.
    #:
    #: Both panels are on one page, so one slug would put
    #: `id_valine_seisukoht_summary` in the document twice — and a `<label for>`
    #: pointing at whichever came first is a label that types into the wrong
    #: record. The file control's own id is reassigned from this in `__init__`
    #: for the same reason (`workspace_attachments`).
    panel_slug: str = "valine_seisukoht"

    def __init__(
        self,
        *args: Any,
        matter: Any = None,
        viewer: Any = None,
        choices: OrganisationChoices | None = None,
        engagements: list[Any] | None = None,
        **kwargs: Any,
    ) -> None:
        # **Its own `auto_id` per panel, and the field names are untouched.**
        # Eleven forms render on one Teema page; `+ Ülevaade / uudis` also calls
        # a field `url`, and since docs/adr/0091 §3.5 split this panel in two
        # both of its instances carry every field name twice. Django's default
        # `id_%s` would put `id_url` and `id_summary` in the document more than
        # once — invalid HTML, a `<label for>` reaching the wrong box, and
        # `getElementById` answering whichever came first. Prefixing the *ids*
        # fixes exactly that while leaving the POST keys alone, which is the
        # reasoning docs/adr/0065 gives for preferring `auto_id` over a form
        # `prefix` (`tests/test_teema_workspace.py`).
        kwargs.setdefault("auto_id", f"id_{self.panel_slug}_%s")
        super().__init__(*args, **kwargs)
        # `workspace_attachments` puts its id on the *widget*, which `auto_id`
        # does not touch — so the two panels would share `id_valine_seisukoht_failid`
        # however their other controls were named. Reassigned per instance rather
        # than by declaring the field twice, so there is still one factory and one
        # accessible name for the drop zone (docs/adr/0075 §2).
        cast(Any, self.fields["attachments"].widget).attrs["id"] = f"id_{self.panel_slug}_failid"
        if not self.allows_source_label:
            # A panel that may not ask does not render the box, and does not keep
            # a field a POST could fill either. `+ Teiste arvamus` has an author
            # by definition, and a free text box answering *whose position is
            # this* would be a ninth way of naming an institution beside the one
            # shared catalogue (docs/adr/0073, docs/adr/0091 §3.3).
            del self.fields["source_label"]
        # No `record`: this panel only ever creates. The «Muutmata» chip is a
        # correction affordance and there is nothing here to keep unchanged.
        attach_external_position_precision(self)
        # The page's one reading where a caller has it — two of these panels
        # render together, and reading the catalogue twice for them is half of
        # what blew the Teema page's query budget (docs/adr/0091 §3.5).
        attach_organisation_picker(self, viewer=viewer, choices=choices)
        set_external_position_engagements(
            self, matter=matter, viewer=viewer, engagements=engagements
        )

    def clean(self) -> dict[str, Any]:
        super().clean()
        return self._clean_external_position(
            has_file=bool(self.cleaned_data.get("attachments")),
        )


class ReceivedFeedbackForm(CompactExternalPositionForm):
    """`+ Meile saadetud tagasiside` — somebody gave this to Koda.

    A member company's e-mail, an association's written answer, a consultation
    response, the summary of a survey. The same seven controls as its sibling
    plus `Allikas`, and the one rule that differs: the organisation is optional,
    because an aggregate answer has no single author and the two things this
    panel used to force were an invented organisation called «234 ettevõtet» and
    one arbitrary respondent standing for the rest (lawyer feedback 12,
    docs/adr/0091 §3.3).

    A subclass rather than a flag on the caller, so that *which questions this
    panel asks* and *what it writes* are one decision in one place. The
    alternative — one form told at construction time which it is — puts the
    distinction in the hands of whoever writes the next call site, which is the
    reasoning `set_next_action_for_new_work` gives for being its own function
    rather than a `bypass=` parameter.
    """

    provenance = ExternalPositionProvenance.RECEIVED.value
    allows_source_label = True
    panel_slug = "tagasiside"


class OtherOpinionForm(CompactExternalPositionForm):
    """`+ Teiste arvamus` — Koda recorded somebody else's position from elsewhere.

    A ministry's opinion, another business organisation's position paper, a
    submission found in EIS, a public statement. Exactly the record docs/adr/0084
    built, under a name that says which of the two kinds it is: the organisation
    is required, there is no `Allikas`, and nothing else about it moved.
    """

    provenance = ExternalPositionProvenance.DISCOVERED.value
    allows_source_label = False
    panel_slug = "valine_seisukoht"


class ExternalPositionEditForm(ExternalPositionFieldsMixin, forms.Form):
    """`Muuda` on a recorded `Väline seisukoht`. The same questions, no files.

    **No upload control, deliberately.** Correcting the metadata of a position
    and adding a second piece of evidence to it are different acts with
    different audit trails, and a correction form that also captured bytes would
    make «what changed» unanswerable from one event. The files a position
    already carries are read on its chronology row through the ordinary
    `DocumentLink` projection and are not re-posted here — so a correction can
    never silently detach one either (docs/adr/0084 §8).

    That is also why the source rule reads the link table rather than a box: a
    position whose source is an attached document may have its address emptied
    and stay sourced, and so may one whose `Seisukoht` says what the ministry
    wrote — but a correction that empties the last of the three unsources the
    record and is refused. `app.matters.services.correct_external_position`
    decides that under the row lock; this form only needs to know whether to
    print the sentence.

    ``revision`` is the version the form was filled from, carried through the
    round trip so the service can refuse a save whose record has moved on. The
    same hidden field `EntryEditForm` and `WebsiteOverviewLinkForm` carry, for
    the same reason.
    """

    use_required_attribute = False

    organisation = _external_position_organisation_field()
    organisation_name = _typed_organisation_field("Uus organisatsioon")
    url = _external_position_link_field()
    summary = _external_position_summary_field()
    lawyer_note = _external_position_lawyer_note_field()
    source_label = _external_position_source_label_field()
    engagement = _external_position_engagement_field()
    stated_on = EstonianDateField(
        label="Seisukoha kuupäev",
        required=False,
        widget=EstonianDateInput(),
    )
    revision = forms.CharField(required=False, widget=forms.HiddenInput())

    def __init__(
        self,
        *args: Any,
        record: Any = None,
        viewer: Any = None,
        choices: OrganisationChoices | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.record = record
        # **What this record may be corrected into is what it already is.**
        #
        # `Allikas` is offered exactly when the row is received feedback, and
        # `provenance` is not on this form at all. A correction form that could
        # move it would let one press turn feedback a member sent us into an
        # opinion we found somewhere — a change to *how the file learned
        # something*, made silently, on a record whose whole point is provenance.
        # Correcting a mis-filing is recording the position again under the right
        # chip; there is no delete, so both rows stay, which is the honest history
        # of one (docs/adr/0091 §3.5).
        #
        # The rule is `EngagementForm`'s, read the other way round: an editor must
        # not be able to write a shape its creating surface cannot.
        self.allows_source_label = bool(record is not None and record.is_received)
        if not self.allows_source_label:
            del self.fields["source_label"]
        attach_external_position_precision(self, record=record)
        attach_organisation_picker(self, viewer=viewer, choices=choices)
        set_external_position_engagements(
            self, matter=getattr(record, "matter", None), viewer=viewer
        )

    @property
    def shows_source_label(self) -> bool:
        """Whether the template draws the `Allikas` box.

        A property rather than the template testing for the field, because a
        template that reached for a deleted field would render an empty label and
        no control — which reads as a box that failed to load rather than as a
        question this record is not asked.
        """
        return "source_label" in self.fields

    def clean(self) -> dict[str, Any]:
        super().clean()
        # Whether this record is *already* sourced by a document, read from the
        # link table rather than from a control. A correction that empties the
        # address of a position carrying a file is an ordinary correction; one
        # that empties the address of a position carrying nothing else is the
        # record being unsourced, and is refused.
        has_file = self.record is not None and self.record.document_links.exists()
        return self._clean_external_position(has_file=has_file)


def visible_engagements_of(matter: Any, viewer: Any) -> list[Any]:
    """This Matter's consultations, as this reader may see them, read once.

    Evaluated deliberately. Two `Seotud kaasamine` controls render on the Teema
    page since docs/adr/0091 §3 split the panel in two, and a lazy queryset handed
    to both is two identical reads at render time — which is half of what the
    query budget caught (`tests/test_matter_workflow.py`).

    Scoped by `visible_to`, which is the authorization half and is not weakened by
    being read early: what a crafted POST is validated against is the field's own
    queryset, set from this same filter (`set_external_position_engagements`).
    """
    from app.matters.models import MatterEngagement

    if matter is None:
        return []
    return list(
        MatterEngagement.objects.filter(matter=matter).visible_to(viewer).order_by("-created_at")
    )


def set_external_position_engagements(
    form: forms.Form, *, matter: Any, viewer: Any, engagements: list[Any] | None = None
) -> None:
    """Point `Seotud kaasamine` at this Matter's consultations, as this reader sees them.

    The field's **queryset**, not only its rendered choices: that is what
    validates a posted id, so a crafted POST naming a `Kaasamine` on another
    Matter — or one restricted below the Matter this reader may open — is
    refused by the field itself. The service's own cross-Matter check stays
    where it is, because a form is not a boundary (AUTH-003, docs/adr/0038).

    ``engagements`` is the page's one reading of that same set, where a caller has
    it. It fills the *rendered choices* only; the queryset above is untouched and
    is still what validates, so sharing the list costs nothing in authorization
    and saves one identical read per extra control. A caller passing nothing gets
    the lazy queryset it always got (`visible_engagements_of`, docs/adr/0091 §3.5).

    A form built without a Matter keeps the empty queryset the field declares,
    which is «no round to relate this to» and is the correct answer for a form
    that does not know which file it is on.
    """
    from app.matters.models import MatterEngagement

    field = cast(Any, form.fields["engagement"])
    if matter is None:
        return
    field.queryset = (
        MatterEngagement.objects.filter(matter=matter).visible_to(viewer).order_by("-created_at")
    )
    if engagements is not None:
        # The named blank option, restated. Assigning `choices` replaces Django's
        # iterator, and the iterator is what would otherwise have put
        # `empty_label` in front — so «Ei ole seotud» has to be written here or an
        # unrelated record would read as a question somebody forgot to answer
        # (`MatterCreateForm.__init__` states the same rule for `Adressaat`).
        field.choices = [
            ("", field.empty_label),
            *((engagement.pk, str(engagement)) for engagement in engagements),
        ]


class KodaOpinionForm(forms.Form):
    """`+ Koja arvamus` — the Chamber's own opinion went out, and here it is.

    **A second door onto `Submission`, never a second record of one.** Koda's own
    opinion has been `Submission` since the foundational schema — the canonical
    record of what was sent, to whom, when, with which exact bytes — and ADR 0061
    put the surface that manages it on `Dokumendid`. What the first lawyer test
    found is that a lawyer working a file on the Teema page could not see the
    step at all: sending the opinion is the centre of the workflow, and the way to
    record it was a filtered document list on another tab, behind a collapsed
    block, in two acts (upload the file, then register the send). So this panel
    asks the four questions that act has and posts to
    `register_sent_opinion_on_open_matter`, which is the same service the
    `Dokumendid` form already posts to. No `KodaOpinion` model, no second
    statistic, no second withdrawal path (lawyer feedback 13, docs/adr/0091 §6).

    **Four questions, and each of them is a fact a send really has.**

    `Kuupäev` — required, never in the future, and never inferred. Recording a
    send that already happened is not the same act as pressing send, and the
    blank that used to mean *now* produced `Arvamus välja <today>` about letters
    whose date nobody supplied (R2-01, ADR 0061's 2026-09-11 amendment).

    `Fail` — required, and exactly one. What was sent is the point; a Submission
    marked SENT with no final evidence is a claim the database itself refuses.
    The bytes go through the ordinary upload / `Document` / immutable
    `DocumentVersion` pipeline under `DocumentRole.KODA_SUBMISSION_FINAL`, and
    **uploading a file here is not what asserts the send** — pressing this button
    is, which is why the two are one transaction rather than one act
    (docs/adr/0091 §6.2).

    `Adressaadid` — required, at least one, and **not** defaulted from the
    Matter's sender. An opinion on the first draft goes to the ministry; one at
    second reading goes to a Riigikogu committee; one on a revised text may go to
    a third body. Assuming the original sender would put a false recipient on the
    canonical outbound record of a professional letter, which is the one thing a
    recipient column may not do (lawyer feedback 14, docs/adr/0091 §6.3).

    `Pealkiri` — optional, and the filename answers it when it is left blank.
    «Koja arvamus pakendiseaduse eelnõule» is worth typing and
    «arvamus_final_v3.docx» is not worth retyping, so a person who has already
    named the file has already answered this. Nothing is derived from the *file's
    contents* and nothing is generated.

    **No `Liik` and no `Kanal` here.** `SubmissionKind` keeps every value and
    `Dokumendid` keeps offering them; what this panel writes is
    `FORMAL_OPINION`, which is what «Koja arvamus» means. A channel is
    bookkeeping a lawyer can add on the submission's own surface, and asking for
    it here would put two optional text boxes in front of the four required
    answers — the friction this panel exists to remove.

    **Several per Matter is the ordinary case**, and nothing here is unique: an
    opinion on the VTK, one on the draft, one during Riigikogu proceedings and one
    on the revised text are four sends and four Submissions. There is no
    `Matter.final_opinion` and this panel does not invent one (ADR 0061,
    docs/adr/0091 §6.4).
    """

    use_required_attribute = False

    #: The exact bytes that went out. One file, because one `Submission` has one
    #: `final_version` — a panel that took several would have to ask which of
    #: them was the letter, and that is a question with no good place on it.
    #:
    #: A plain `FileField` rather than `workspace_attachments`: the other panels
    #: capture *supporting evidence for something*, where any number of files is
    #: ordinary. This is the thing itself.
    upload = forms.FileField(
        label="Saadetud fail",
        required=False,
        widget=forms.ClearableFileInput(
            attrs={"class": "visually-hidden", "id": "id_koja_arvamus_fail"}
        ),
    )
    title = forms.CharField(
        label="Pealkiri",
        required=False,
        max_length=400,
        widget=forms.TextInput(
            attrs={
                "class": "field__input field__input--compact",
                "placeholder": "nt Koja arvamus pakendiseaduse eelnõule",
            }
        ),
    )
    #: `Adressaadid` — who it was sent to, from the one shared catalogue.
    #:
    #: Checkboxes and a multiple field, because one letter really does go to two
    #: bodies at once — a ministry and the committee that asked for it — which is
    #: what `SubmissionRecipient` has always been able to hold.
    #:
    #: The queryset is the whole catalogue: every institution is a valid
    #: recipient, and narrowing it to a shortlist would refuse a correct answer
    #: given through the search control.
    recipients = forms.ModelMultipleChoiceField(
        label="Adressaadid",
        queryset=Organisation.objects.none(),
        required=False,
        widget=OrganisationCheckboxSelect(attrs={"class": "chip__input"}),
    )
    sent_on = EstonianDateField(
        label="Saatmise kuupäev",
        required=False,
        widget=EstonianDateInput(),
        #: Today, and visibly. An opinion is written up on the day it goes out
        #: far more often than not, and the default is in the box where it can be
        #: read and changed — which is the one shape docs/adr/0078 §2 allows a
        #: date default to take. What is refused is the *server* supplying one:
        #: an emptied box is a refusal naming the missing day, never a stamp
        #: (`clean_sent_on`, R2-01).
        initial=timezone.localdate,
    )

    def __init__(
        self,
        *args: Any,
        matter: Any = None,
        viewer: Any = None,
        choices: OrganisationChoices | None = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("auto_id", "id_koja_arvamus_%s")
        super().__init__(*args, **kwargs)
        self.matter = matter
        # Validation against the whole catalogue; the rendered order is the
        # **page's own** reading, which is the same usage ranking the `Saatja`
        # and `Adressaat` controls already use — so a ministry this reader writes
        # to weekly is a chip rather than a search.
        #
        # It shares `read_organisation_choices` with the two feedback panels
        # rather than calling `addressees_by_usage` for itself. That helper ranks
        # the *addressee* direction alone, which is marginally apter for a
        # recipient shortlist and costs two more queries on a page that is
        # already reading the same catalogue; the shared ranking is «bodies this
        # reader's own Matters involve», which is as good an answer to «who does
        # this office write to» and is already in hand (docs/adr/0091 §3.5).
        reading = choices if choices is not None else read_organisation_choices(viewer)
        set_choices(self, "recipients", Organisation.objects.order_by("name"))
        field = cast(Any, self.fields["recipients"])
        shortlist, tail = reading.shortlist_and_tail()
        self.recipients_offered = [*shortlist, *tail]
        field.choices = [
            (organisation.pk, organisation.name) for organisation in self.recipients_offered
        ]
        # The recorded spellings, so «MKM» finds the ministry through an alias
        # rather than through a similarity score. The panel has no search box of
        # its own, and the browser's in-page find reads these
        # (docs/adr/0073, `OrganisationSpellings`).
        cast(Any, field.widget).alias_terms = reading.alias_terms

    def clean_sent_on(self) -> Any:
        """A send is never in the future. The record says what happened.

        The same rule and deliberately the same sentence as
        `RegisterSentOpinionForm`'s, because it is the same act asked on a
        different page — two wordings for one refusal is two things to learn.
        """
        value = self.cleaned_data.get("sent_on")
        if value is not None and value > timezone.localdate():
            raise forms.ValidationError("Saatmise kuupäev ei saa olla tulevikus.")
        return value

    def clean(self) -> dict[str, Any]:
        """The three required answers, each refused on its own control.

        `required=False` on every field and the refusals here, for the reason
        `NextActionForm` gives: with the HTML `required` attribute present a
        browser refuses to submit and reports nothing, so the button silently
        does nothing. The server states which answer is missing instead, beside
        the box that is missing it.
        """
        cleaned = super().clean() or {}
        if not cleaned.get("upload"):
            self.add_error("upload", "Lisa fail, mis välja saadeti.")
        if cleaned.get("sent_on") is None and not self.errors.get("sent_on"):
            self.add_error("sent_on", "Märgi, mil kuupäeval arvamus välja saadeti.")
        if not cleaned.get("recipients"):
            self.add_error("recipients", "Vali vähemalt üks adressaat.")
        return cleaned


class ProceduralDevelopmentForm(forms.Form):
    """`+ Menetluse areng` — the procedure moved, and this is what it did.

    «12.10.2026 — Ministeerium saatis uue eelnõu versiooni», with the draft
    attached, the `Hetkeseis` it puts the file in, and the next thing the lawyer
    will do about it. One panel, one save, one transaction.

    **It writes a `MatterProceduralDevelopment`**, which is the canonical record
    docs/adr/0091 §5 settled on — beside `MatterEngagement` and
    `MatterExternalPosition`, through `workspace.add_procedural_development`.

    This docstring said «it writes an `Entry`, not a new model» for one round,
    and that was true for exactly that round. The Package D discovery retired the
    design: an incoming development cannot be projected truthfully from an
    `Entry`, because `Entry.occurred_at` is `NOT NULL` and a step learned about
    months later frequently has no day anybody could defend, because the lawyer's
    note has nowhere to go that is not the ministry's own sentence, and because a
    projection would have to parse a title out of prose. The sentence outlived
    the design it described, which on the feature whose renderer and whose date
    rule are both being corrected here is the wrong thing to leave standing
    (docs/adr/0091 §5.1, §5.2, docs/adr/0092 §3).

    **A development is something that has already happened.** A future date is
    refused — `clean` states the rule and `record_procedural_development`
    enforces it — because the product's forward-looking facts are `Järgmiseks`
    and `+ Oluline tähtaeg`, each with its own date, its own lateness and its own
    place on the page.

    **Three optional halves, and each of them is somebody's decision.**

    `Hetkeseis` is offered and never derived. Nothing reads the sentence and
    concludes that a file has reached the Riigikogu; a person chooses the stage
    or leaves it, and leaving it changes nothing. The vocabulary is
    `active_stages()` read through the canonical service, so a Package-A
    revision of the stage list arrives here without this form knowing about it
    (docs/adr/0091 §5.3, §8).

    `Järgmiseks` and `Millal?` are the same two questions `NextActionForm` asks,
    asked here because «the ministry sent a new draft» and «I will read it by
    Friday» are one thought — and requiring two saves for them is the friction
    that left files at a dead end after an opinion went out. Both or neither: a
    step with no date and a date with no step are each refused on the empty half,
    which is `NextActionForm`'s own rule and deliberately its own wording.

    **The date is optional and defaults visibly to today.** A development is
    written up when it is learned about, which is usually the day it happened, so
    the box opens holding today — in the box, readable, changeable and clearable,
    which is the one shape docs/adr/0078 §2 allows a date default to take. An
    emptied box stores `NULL` and the record reads «Kuupäev teadmata».

    That optionality is the whole reason `MatterProceduralDevelopment` exists
    rather than an `Entry`: `Entry.occurred_at` has been `NOT NULL` since the
    foundational schema, and a development learned about from a third party months
    later frequently has no day anybody could defend. An undated development is a
    development, recorded as one — not a `+ Märge`, which stamps the moment
    somebody typed it and claims nothing about when anything happened
    (docs/adr/0091 §5.1, §5.2).
    """

    use_required_attribute = False

    title = forms.CharField(
        label="Mis menetluses juhtus",
        required=False,
        max_length=DEVELOPMENT_TITLE_MAX_LENGTH,
        widget=forms.TextInput(
            attrs={
                "class": "field__input field__input--compact",
                "placeholder": "nt Ministeerium saatis uue eelnõu versiooni",
            }
        ),
    )
    #: `Juristi märkus` — what this office makes of the step, beside it and never
    #: inside it.
    #:
    #: «Uus versioon ei arvesta meie ettepanekut» is a professional judgement and
    #: «Ministeerium saatis uue versiooni» is a fact about the world. One box
    #: carrying both is a box whose meaning depends on who wrote the sentence —
    #: the same separation `+ Teiste arvamus` keeps (docs/adr/0091 §4, §5).
    note = forms.CharField(
        label="Juristi märkus",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "field__input field__input--compact",
                "rows": "2",
                "placeholder": "nt uus versioon ei arvesta meie ettepanekut",
            }
        ),
    )
    #: The day it happened, **optional**, at the precision it is known to.
    #:
    #: This is the field that retired the `Entry`-based design: `occurred_at` is
    #: `NOT NULL`, and a development learned about from a third party months later
    #: frequently has no day anybody could defend. The box opens on today because
    #: the common case is writing up something just learned, visibly and
    #: clearably — the one shape docs/adr/0078 §2 allows — and an emptied box
    #: stores `NULL`, which reads «Kuupäev teadmata» (docs/adr/0091 §5.2).
    occurred_on = EstonianDateField(
        label="Kuupäev",
        required=False,
        widget=EstonianDateInput(),
        initial=timezone.localdate,
    )
    #: `Hetkeseis`, optional, as a select rather than the create form's chip row.
    #:
    #: Eleven stages as chips is two lines of controls inside a panel that already
    #: holds five, and the chips exist on `Uus teema` because that page is built
    #: around them. Here the stage is the third question of five and most saves
    #: leave it alone, so it is the compact control — the same reasoning
    #: `+ Väline seisukoht` applies to `Seotud kaasamine`.
    stage = forms.ModelChoiceField(
        label="Uus hetkeseis",
        queryset=StageVocabulary.objects.none(),
        required=False,
        empty_label="Jätan muutmata",
        blank=True,
        widget=forms.Select(attrs={"class": "field__input field__input--compact"}),
    )
    next_text = forms.CharField(
        label="Järgmiseks",
        required=False,
        max_length=2000,
        widget=forms.TextInput(
            attrs={
                "class": "field__input field__input--compact",
                "placeholder": "nt Vaatan uue versiooni läbi",
            }
        ),
    )
    next_date = EstonianDateField(
        label="Millal?",
        required=False,
        widget=EstonianDateInput(),
    )
    attachments = workspace_attachments("id_menetluse_areng_failid")

    def __init__(self, *args: Any, record: Any = None, **kwargs: Any) -> None:
        kwargs.setdefault("auto_id", "id_menetluse_areng_%s")
        #: The record being corrected, when this form is an editor.
        #:
        #: Read for one thing: whether it carries a precision the day box cannot
        #: show, so that correcting the sentence of a development recorded as
        #: *oktoober 2026* does not rewrite it into a day (docs/adr/0079 §9).
        self.record = record
        super().__init__(*args, **kwargs)
        attach_development_precision(self, record=record)
        # The active vocabulary, in the department's reviewed order, read through
        # the canonical selector rather than from a list in this module. Package A
        # may revise what is active; this form inherits that without being edited
        # (`app/workflow/selectors.py`, docs/adr/0091 §8).
        set_choices(self, "stage", active_stages())

    @property
    def precision_chips(self) -> list[dict[str, Any]]:
        """The `Täpsus` radios, as the template renders every other chip row."""
        return _precision_chips(self, f"{DEVELOPMENT_PREFIX}_precision")

    def clean_title(self) -> str:
        from app.matters.services import DEVELOPMENT_NEEDS_TITLE

        title = (self.cleaned_data.get("title") or "").strip()
        if not title:
            raise forms.ValidationError(DEVELOPMENT_NEEDS_TITLE)
        return title

    def clean(self) -> dict[str, Any]:
        """The period, its refusal, and the next step's two halves together or not at all.

        **The date is not required**, unlike the round this panel shipped in: an
        emptied box stores `NULL` and reads «Kuupäev teadmata», which is a fact
        the file has to be able to hold about a step somebody learned of late
        (docs/adr/0091 §5.2).

        **And it may not be in the future.** A `Menetluse areng` records
        something that has happened; «Riigikogu esimene lugemine toimub 30.09» is
        a plan, and the product's forward-looking facts are `Järgmiseks` and
        `+ Oluline tähtaeg`. The refusal is the service's — one invariant, one
        sentence, and `record_procedural_development` is what actually enforces
        it — and it is repeated here so a person sees it beside the control they
        typed into rather than as a panel-level banner.

        **A period is refused only when the whole of it is still ahead.** The
        anchor of *september 2026* is 1 September, and rejecting it on 18
        September would refuse a step the lawyer is plainly describing as past —
        so the comparison is `period_starts_after`, and an approximate date is
        never resolved to a day to make the question easier (docs/adr/0079 §2).

        The refusal for a half-filled next step lands on the **empty** control,
        which is ADR 0052 §5's rule and its wording: «vali kuupäev» pinned to the
        sentence box points at the wrong field. The future-date refusal lands on
        the control the chosen precision is answered in, through the same map.
        """
        from app.matters.services import DEVELOPMENT_CANNOT_BE_FUTURE

        cleaned = super().clean() or {}
        anchor, precision = development_period(cast(Any, self))
        if period_starts_after(anchor, precision, day=timezone.localdate()):
            self.add_error(
                _precision_controls(DEVELOPMENT_PREFIX, "occurred_on").get(
                    precision, "occurred_on"
                ),
                DEVELOPMENT_CANNOT_BE_FUTURE,
            )
            anchor = None
        cleaned["occurred_on_value"] = anchor
        cleaned["occurred_on_precision"] = precision

        text = (cleaned.get("next_text") or "").strip()
        cleaned["next_text"] = text
        when = cleaned.get("next_date")
        if text and when is None:
            self.add_error("next_date", "Vali järgmise tegevuse kuupäev.")
        elif when is not None and not text:
            self.add_error("next_text", "Kirjuta järgmine tegevus.")
        return cleaned


class CompactClosureForm(ChipChoices, forms.Form):
    """`+ Lõpeta teema` — two questions, and nothing invented from them.

    The simplified closure of docs/adr/0074 §10, unchanged. Closing a Matter is
    not a claim that an opinion was sent: no final evidence, no send date, no
    recipients, no work-victory question. The six-question flow is not coming
    back, and the canonical rules behind each of those facts are untouched —
    `mark_submission_sent` still refuses a submission without its exact final
    evidence, and a `Töövõit` is still recorded from its own panel (brief §20).

    `Lõppsõna` is optional. The composer used to fall back to its body when it
    was blank; there is no shared body any more, so a closure with nothing to add
    stores an empty reason rather than borrowing a sentence from another
    operation (docs/adr/0075 §9).
    """

    use_required_attribute = False

    disposition = forms.ChoiceField(
        label="Kuidas lõppes",
        choices=(("", "Vali põhjus…"), *CLOSURE_CHOICES),
        required=False,
        widget=forms.HiddenInput(),
    )
    closing_words = forms.CharField(
        label="Lõppsõna",
        required=False,
        max_length=2000,
        widget=forms.Textarea(
            attrs={
                "class": "field__input field__input--compact",
                "rows": "2",
                "placeholder": "Mis sellest teemast lõpuks sai?",
            }
        ),
    )

    @property
    def closure_chips(self) -> list[dict[str, Any]]:
        return self.chips("disposition", COMPOSER_CLOSURE_CHOICES, "")

    def clean_disposition(self) -> str:
        disposition = self.cleaned_data.get("disposition") or ""
        if not disposition:
            raise forms.ValidationError("Vali, kuidas teema lõppes.")
        return disposition


class EntryEditForm(forms.Form):
    """`Muuda` — correcting the wording of a Sissekanne that is already filed.

    **The body and nothing else.** Not the author, not `Toimus`, not the kind,
    not the files and not the visibility: a correction says what happened was
    written down wrongly, and every one of those other fields would be saying
    that something *different* happened. A form with no field for them is the
    surest way to guarantee it
    (tests/test_entry_correction.py, `test_a_correction_changes_only_the_body`).

    **The box is filled from the stored value exactly, markup and all.** A note
    typed into the composer is stored as the plain sentence it was — the
    sanitiser wraps nothing that arrived unwrapped — so the ordinary correction
    shows a lawyer their own words and no tags. An entry pasted out of Word
    carries real structure: paragraphs, lists, a table. Showing that as plain
    text would be friendlier to look at and would destroy the structure on the
    next save, which is the one thing a *correction* may not do
    (app/core/richtext.py).

    `revision` is the version the box was filled from, carried through the
    round trip so `edit_entry` can refuse a stale save rather than let it
    overwrite somebody else's. `required=False`, because an absent token must
    reach the service as an empty string and be refused there against a real
    row — a required field would answer a stale form with a field error that
    says nothing about what actually went wrong.
    """

    use_required_attribute = False

    body = forms.CharField(
        label="Sissekande sisu",
        required=False,
        widget=forms.Textarea(
            attrs={
                # The ordinary field style rather than `composer__body`. The
                # composer's box is one line at rest and grows only inside
                # `.composer:focus-within`, which this is not in — it would open
                # forty pixels tall on an entry somebody wrote three paragraphs
                # of (static/css/app.css).
                "class": "field__input",
                "rows": "4",
                "data-richtext": "true",
            }
        ),
    )
    revision = forms.CharField(required=False, widget=forms.HiddenInput())

    def clean_body(self) -> str:
        return require_written_body(self.cleaned_data.get("body"), "Sissekanne vajab sisu.")


def _procedural_link_kind_field(*, initial: Any = None) -> forms.ChoiceField:
    """`Millise menetlusega on tegemist` — one of five, as chips.

    A radio group rather than a `<select>`, for the reason `Menetlusliik` and
    `Hetkeseis` are chip rows on `Uus teema`: for a vocabulary of five, a select
    is a click spent finding out what the options even are.

    ``required=False`` at field level and refused in the service instead, so the
    sentence a person reads is `PROCEDURAL_LINK_NEEDS_KIND` rather than Django's
    generic one — the shape `_external_position_organisation_field` uses for the
    same reason.
    """
    return forms.ChoiceField(
        label="Menetluse allikas",
        choices=ProceduralLinkKind.choices,
        required=False,
        initial=initial,
        widget=forms.RadioSelect(attrs={"class": "chip__input"}),
    )


def _procedural_link_url_field() -> forms.CharField:
    """`Link` — the address the proceeding actually lives at.

    A `CharField` rather than a `URLField`, exactly as every other public-link
    box on a Teema page is one: the rule belongs to
    `app.matters.services.normalize_procedural_link_url`, which is what the
    service enforces, and letting Django's own validator answer first would give
    one refused address two different sentences depending on which layer caught
    it.
    """
    return forms.CharField(
        label="Link",
        required=False,
        max_length=PROCEDURAL_LINK_URL_MAX_LENGTH,
        widget=forms.TextInput(
            attrs={
                "class": "field__input field__input--compact",
                "inputmode": "url",
                "autocomplete": "off",
                "placeholder": "https://…",
            }
        ),
    )


def _procedural_link_label_field() -> forms.CharField:
    """`Nimetus` — a few words naming *which* proceeding, where that is not obvious.

    Genuinely optional. A Matter with one EIS link needs nothing here, because
    the kind beside the address already says what it is; a Matter carrying three
    links from one ministry's register needs something, or the card reads as
    three identical rows.
    """
    return forms.CharField(
        label="Nimetus",
        required=False,
        max_length=PROCEDURAL_LINK_LABEL_MAX_LENGTH,
        widget=forms.TextInput(
            attrs={
                "class": "field__input field__input--compact",
                "autocomplete": "off",
                "placeholder": "Näiteks: Eelnõu 123 SE",
            }
        ),
    )


class ProceduralLinkFieldsMixin:
    """The three questions a `Menetluse link` asks, and the one door they go through.

    Shared by the panel that records one and the form that corrects one, because
    the two ask exactly the same three questions and must enforce exactly the
    same rules. A second class would be a second place for the address rule to
    be written out, and the day it disagreed with the first would be the day a
    correction accepted an address a recording would have refused
    (`WebsiteOverviewLinkForm` makes the identical argument).

    Each value is cleaned through the *service's* own normaliser so that a
    refusal reads in the words it reads in everywhere, and lands under the box
    it was typed into rather than at the top of the panel.
    """

    def clean_url(self) -> str:
        from app.matters.services import normalize_procedural_link_url

        try:
            return normalize_procedural_link_url(self.cleaned_data.get("url"))  # type: ignore[attr-defined]
        except DomainError as error:
            raise forms.ValidationError(str(error)) from error

    def clean_kind(self) -> str:
        from app.matters.services import normalize_procedural_link_kind

        try:
            return normalize_procedural_link_kind(self.cleaned_data.get("kind"))  # type: ignore[attr-defined]
        except DomainError as error:
            raise forms.ValidationError(str(error)) from error

    def clean_label(self) -> str:
        from app.matters.services import normalize_procedural_link_label

        try:
            return normalize_procedural_link_label(self.cleaned_data.get("label"))  # type: ignore[attr-defined]
        except DomainError as error:
            raise forms.ValidationError(str(error)) from error


class ProceduralLinkForm(ProceduralLinkFieldsMixin, forms.Form):
    """`+ Menetluse link` — where the official proceeding on this Matter lives.

    Three boxes, of which two are required and one is not: which kind of
    official source this is, the address, and optionally a few words naming the
    proceeding.

    **There is no date here and there is no status.** This record is not
    something that happened on a day — it is *where the file is happening* — so
    a date would be a column with nothing honest to put in it and a status would
    be a lifecycle nobody maintains (docs/adr/0089 §5, §11).

    **And there is nothing about fetching.** No «loe leht sisse», no «jälgi
    muudatusi», no preview: the lawyer gives the address and the application
    records the address (docs/adr/0089 §4).
    """

    use_required_attribute = False

    kind = _procedural_link_kind_field()
    url = _procedural_link_url_field()
    label = _procedural_link_label_field()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # **Its own `auto_id`, and the field names are untouched.** Ten forms
        # render on one Teema page and `+ Ülevaade / uudis` and `+ Väline
        # seisukoht` both call a field `url`, so Django's default `id_%s` would
        # put `id_url` in the document three times — invalid HTML, a
        # `<label for>` reaching the wrong box and `getElementById` answering
        # whichever came first. Prefixing the *ids* fixes exactly that while
        # leaving the POST keys alone (docs/adr/0065).
        kwargs.setdefault("auto_id", "id_menetluse_link_%s")
        super().__init__(*args, **kwargs)


class ProceduralLinkEditForm(ProceduralLinkFieldsMixin, forms.Form):
    """`Paranda` — the kind, the name or the address on a recorded link was wrong.

    The same three questions, filled from the row, plus the version they were
    filled from. There is deliberately no delete control and no «eemalda» field:
    a mistaken row is corrected, because what the file recorded and who recorded
    it is part of the file (docs/adr/0084 §8, docs/adr/0089 §6).

    ``revision`` is `required=False` for `EntryEditForm`'s reason: an absent
    token must reach the service as an empty string and be refused there against
    a real row, rather than answered by a field error that says nothing about
    what actually went wrong.
    """

    use_required_attribute = False

    kind = _procedural_link_kind_field()
    url = _procedural_link_url_field()
    label = _procedural_link_label_field()
    revision = forms.CharField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args: Any, link: Any = None, **kwargs: Any) -> None:
        """One correction form per row, with ids that name the row.

        Several of these render on one page — a Matter may carry four
        procedural links — so the `auto_id` carries the row's own primary key.
        Without it every disclosure on the card would contain `id_kind`, and a
        `<label for>` would reach the first one whichever row somebody opened.
        """
        if link is not None:
            kwargs.setdefault("auto_id", f"id_menetluse_link_{link.pk}_%s")
            kwargs.setdefault(
                "initial",
                {
                    "kind": link.kind,
                    "url": link.url,
                    "label": link.label,
                    "revision": link.revision_token,
                },
            )
        else:
            kwargs.setdefault("auto_id", "id_menetluse_link_muuda_%s")
        super().__init__(*args, **kwargs)


class ProceduralLinkCreateForm(ProceduralLinkFieldsMixin, forms.Form):
    """One `Menetluse link`, asked while the Teema is being created.

    The lawyer feedback that produced docs/adr/0089 asked for this by name:
    a Matter frequently arrives *from* a proceeding — an EIS notification, a
    ministry's covering letter naming its register — and the address is on
    screen at the moment the file is being opened. Making somebody create the
    Teema and then go and find that address again is how it ends up in a
    browser history instead of on the file.

    **One row, not a formset.** A Matter being created has one proceeding
    behind it in the overwhelming majority of cases, and the second and third
    addresses turn up later, as the file moves — which is what the Teema page's
    own `+ Menetluse link` is for. A repeating control here would put an empty
    table on a form whose whole design is that nothing on it is required
    (docs/adr/0089 §7).

    **Wholly optional, and silent when untouched.** All three boxes empty is the
    ordinary submit and writes nothing at all — no row, no event, no empty
    record. That is the same rule the private `Märkmed` box on this form
    follows: an empty answer creates no record saying somebody wrote nothing.

    **A `prefix`, not an `auto_id`.** This is the one procedural-link form that
    shares a `<form>` element with something else — `MatterCreateForm` and
    `NextActionForm` — and `NextActionForm` already uses `prefix="next"` there
    for exactly that reason. The prefix namespaces the POST keys as well as the
    ids, so `MatterCreateForm` cannot be changed in a way that silently collides
    with a field name here — which matters because the two forms are edited by
    different people at different times (docs/adr/0089 §13).
    """

    use_required_attribute = False

    kind = _procedural_link_kind_field(initial=ProceduralLinkKind.EIS.value)
    url = _procedural_link_url_field()
    label = _procedural_link_label_field()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Bound whatever happens, and **empty-permitted** — an untouched block is valid.

        The two halves are both load-bearing and they pull in opposite
        directions, which is why this is stated here rather than decided in the
        view.

        *Bound*, because the block has to come back holding what was typed into
        it when the save is refused for a reason somewhere else. Leaving it
        unbound on those attempts would be the easy way to stop it refusing
        anything, and it would silently empty the `Nimetus` box of somebody who
        had filled it.

        *Empty-permitted*, because a bound form validates, and this one has
        nothing to validate until somebody answers it. `empty_permitted` is
        Django's own name for exactly this — a sub-form that may legitimately
        be left alone — and with :meth:`has_changed` below it makes
        `full_clean` return with no errors and no `cleaned_data` on precisely
        the attempts where :attr:`wants_link` is false.

        The mixin's rules are untouched, which is the point: `ProceduralLinkForm`
        behind `+ Menetluse link` on a Teema page was opened deliberately, so an
        empty address there is an unfinished answer and stays refused. Only
        *this* form — the optional block nobody has to use — is a no-op when
        nobody used it (QA-01, docs/adr/0089 §13).
        """
        kwargs.setdefault("empty_permitted", True)
        super().__init__(*args, **kwargs)

    def has_changed(self) -> bool:
        """Did anybody answer this block? — :attr:`wants_link`, and nothing else.

        This is the hook `empty_permitted` consults, so it is where the one
        definition of «somebody used this block» has to be, rather than beside
        a second list of field checks that could drift away from it.

        Django's own answer would be `changed_data`, and it is the wrong one
        here: `kind` arrives with `EIS` selected, so a browser posts
        `menetlus-kind=EIS` on *every* save from this page while an omitted
        `menetlus-kind` — what a test client sends — reads as a change *away*
        from the initial. Both are noise about a chip nobody clicked, and both
        would put «Menetluse link vajab veebiaadressi.» under an address box
        nobody had typed in.
        """
        return self.wants_link

    @property
    def chosen_summary(self) -> str:
        """What the collapsed disclosure says after the word itself.

        «Menetluse link · EIS», or the kind and the lawyer's own name for it
        where they wrote one. The same affordance `policy_area_summary` gives
        `Valdkond`, for the same reason: a shut field is quieter than a row of
        chips and two boxes, and a shut field that also hid *the answer* would
        be quieter and worse, because then it has to be opened every time to
        find out (docs/adr/0088 §3).

        Empty until there is an address. The `EIS` chip arrives selected and
        means nothing on its own, so a summary reading «· EIS» on an untouched
        form would state an answer nobody had given — which is the shape of
        mistake this whole package is about.

        Read off the raw data rather than `cleaned_data`, because a refused save
        must still say what it is holding, and a refusal may be *why* there is
        no cleaned value.
        """
        if not self.is_bound:
            return ""
        url = (self.data.get(self.add_prefix("url")) or "").strip()
        if not url:
            return ""
        kind = (self.data.get(self.add_prefix("kind")) or "").strip()
        name = dict(ProceduralLinkKind.choices).get(kind, "")
        label = (self.data.get(self.add_prefix("label")) or "").strip()
        if name and label:
            return f"{name}: {label}"
        return label or str(name)

    @property
    def disclosure_open(self) -> bool:
        """Whether the block renders open.

        Server-decided and server-rendered, so a browser with scripting off gets
        the same page. Open on a refusal this block owns — the box somebody has
        to correct must be reachable — and closed otherwise, including on a
        refused save whose problem is somewhere else: the summary already says
        what is held, and unfolding it to prove that would undo the fold on the
        one path where somebody is already being asked to fix something else.
        `policy_area_disclosure_open` takes the same position, and this follows
        it deliberately (docs/adr/0088 §3).
        """
        if not self.is_bound:
            return False
        return bool(self.errors)

    @property
    def wants_link(self) -> bool:
        """Whether anybody actually answered this block.

        The address alone decides it. A `kind` on its own is the chip that
        arrives pre-selected and means nothing, and a `label` on its own is a
        name for a link that does not exist — neither is a request to record
        anything, and treating either as one would file a refusal at somebody
        who had simply not used this part of the form.

        Read from the **raw** data rather than from `cleaned_data`, because it
        is what decides whether there is any cleaning to do: :meth:`has_changed`
        asks it before `full_clean` runs, and the view asks the same property
        again before calling the service, so the page and the write agree by
        construction rather than by two lists of field checks matching.
        """
        if not self.is_bound:
            return False
        return bool((self.data.get(self.add_prefix("url")) or "").strip())

    def clean(self) -> dict[str, Any]:
        """Require the kind only once there is an address to classify.

        `kind` is `required=False` at field level so that the service's sentence
        is the one a person reads. This form is only ever validated when
        :attr:`wants_link` already said there is an address, so a missing kind
        here is a real omission and is refused on the chip row.
        """
        from app.matters.services import PROCEDURAL_LINK_NEEDS_KIND

        cleaned = super().clean() or {}
        if cleaned.get("url") and not cleaned.get("kind"):
            self.add_error("kind", PROCEDURAL_LINK_NEEDS_KIND)
        return cleaned
