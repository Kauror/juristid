"""Forms parse and validate input. They never change state.

Every form here hands its cleaned values to a service function in
``app.matters.services`` or ``app.workflow.services``. Nothing in this module
writes a model field, so the audit trail and the invariants cannot be bypassed
by adding another view (master specification 12.4, 23.4).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
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
from app.matters.entry_enums import EntryKind
from app.matters.enums import (
    COMPOSER_ENGAGEMENT_KINDS,
    EngagementKind,
    MatterDataClass,
)
from app.matters.models import Matter
from app.organisations.models import Organisation, OrganisationAlias
from app.taxonomy.legal_instruments import OTHER_LEGAL_INSTRUMENT_KEY
from app.taxonomy.models import LegalInstrumentType, PolicyArea, Tag
from app.taxonomy.vocabulary import (
    selectable_legal_instrument_types,
    selectable_policy_areas,
)
from app.workflow.dates import MAX_YEAR, MIN_YEAR, InvalidPeriod, bounds_for
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
      alone records that the instrument was none of sixteen listed kinds and
      says nothing about which, which is less than the blank field it replaced.
    """
    chosen = list(cleaned.get("legal_instruments") or [])
    other_selected = any(item.key == OTHER_LEGAL_INSTRUMENT_KEY for item in chosen)
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
    value is `Muu`, one saying whether its box renders open.
    """

    fields: dict[str, forms.Field]
    errors: Any

    #: Set by `offer_legal_instruments`. Declared here so a form that somehow
    #: never called it renders a plain chip row instead of raising.
    _other_instrument_value: str = ""

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
        other = next((item for item in offered if item.key == OTHER_LEGAL_INSTRUMENT_KEY), None)
        self._other_instrument_value = "" if other is None else str(other.pk)

    @property
    def other_instrument_value(self) -> str:
        """The rendered value of the `Muu` chip, as the template sees it.

        `Muu` is a real vocabulary row here rather than a checkbox beside one —
        see docs/adr/0070 §8 for why — so the template cannot tell it apart by
        field name the way the Valdkonnad block does, and compares against this.

        Empty when the offered vocabulary carries no `Muu` row. That is a real
        state, not an error: a database seeded before this vocabulary existed
        renders a plain chip row and no reveal.
        """
        return self._other_instrument_value

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
        other = self.other_instrument_value
        if not other:
            return False
        raw = cast(Any, self)["legal_instruments"].value()
        if raw is None:
            return False
        values = raw if isinstance(raw, (list, tuple)) else [raw]
        return any(str(item) == other for item in values)


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
    `_named_senders` gives below: a bound form's data is a `QueryDict` from a
    real POST and an ordinary dict from a caller constructing one, and only the
    widget knows how to read both — and how to honour a form prefix.
    """
    field = form.fields[name]
    return field.widget.value_from_datadict(form.data, form.files, form.add_prefix(name))


def _named_senders(form: Any) -> list[Organisation]:
    """The organisations this bound form's two sender controls name, as rows.

    Read from `form.data` rather than from `cleaned_data`, because this runs in
    `__init__` — before validation, and on forms that will never be validated at
    all. Two consequences worth stating:

    * an identifier that is not a real Organisation matches nothing, and one
      that is not even a UUID is discarded before it reaches the queryset — so a
      malformed or hostile POST answers with the ordinary refusal rather than a
      500;
    * this survives a *refused* save, which is the case both of its callers
      exist for. A form that comes back with errors comes back with its senders
      ticked, and everything the page derives from them has to come back too.

    Authorization needs no separate thought here: every Organisation is a valid
    counterparty for every reader, and these are the ones this reader just
    chose. Nothing about a restricted Matter can reach this — the input is the
    request, not the register (task §10, §18).
    """
    if not form.is_bound:
        return []

    chosen: list[Any] = []
    for name in ("source_organisations", "source_organisations_other"):
        # The widget's own reader, not `form.data.getlist`. A bound form's data
        # is a QueryDict from a real POST and an ordinary dict from a caller
        # constructing one, and only the first has `getlist` — so reaching for
        # it directly is a read that works in production and silently does
        # nothing anywhere else. This is the accessor Django's own field
        # validation uses, and it honours the form prefix too.
        raw = _raw_value(form, name)
        if raw is None:
            continue
        chosen.extend(raw if isinstance(raw, (list, tuple)) else [raw])

    # Parsed here rather than handed to the queryset. `Organisation.pk` is a
    # UUID column, and a value that is not one makes `pk__in` *raise* — which in
    # `__init__` is a 500 on a page whose whole job is to answer a bad POST with
    # a form and an error message. A caller probing this endpoint learns nothing
    # and gets the ordinary refusal; the field's own validation still rejects
    # the value a moment later.
    identifiers: list[uuid.UUID] = []
    for value in chosen:
        try:
            identifiers.append(uuid.UUID(str(value)))
        except (ValueError, AttributeError, TypeError):
            continue

    if not identifiers:
        return []

    # One query for however many were named. Ordered by name so that several
    # senders are read deterministically rather than in whatever order the
    # browser happened to serialise the checkboxes.
    return list(Organisation.objects.filter(pk__in=identifiers).order_by("name"))


def _promote_named_senders(
    senders: list[Organisation], shortlist: list[Organisation]
) -> list[Organisation]:
    """The addressee shortlist, with this form's chosen senders moved to the front.

    Ranking only. It returns a different order of the same catalogue; it selects
    nothing, writes nothing and creates nothing.

    **What this is for changed, and it is no longer decoration.** It used to be
    the whole of the sender→addressee relationship: the sender was offered first
    and deliberately never chosen. Since `Uus teema` began *answering* Adressaat
    with the sender (docs/adr/0069) the ordering carries a different weight — the
    body that has just become the default addressee has to be one of the chips,
    because the chips are what the collapsed disclosure shows on the ordinary
    visit. A default sitting in the long tail would be an answer the person
    could only see by opening two disclosures to look for it.
    """
    seen = {organisation.pk for organisation in senders}
    return [*senders, *(item for item in shortlist if item.pk not in seen)]


def _default_addressee(form: Any, senders: list[Organisation]) -> tuple[str, str] | None:
    """Who this form answers, when nobody has said — as a field name and a value.

    The ordinary case is that a file arrived from X and is answered to X, so
    `Uus teema` fills Adressaat from Saatja rather than asking the same question
    twice. This is the server's statement of that rule, and it is the
    authoritative one: the browser mirrors it live so the page reads correctly
    while somebody is still filling it in, but with scripting off a POST naming
    one sender and no addressee still saves a Matter answered to that sender
    (docs/adr/0069, task §10).

    Three conditions, and each is a refusal to guess:

    1. **Nothing may already answer Adressaat.** A chosen chip or a typed name
       is the person's own answer and outranks anything derived here.
    2. **`addressee_is_manual` must not be set.** That hidden field is how the
       browser says «this person answered Adressaat themselves», and it is what
       makes deliberately choosing «Määramata» beside a sender possible. With no
       scripting it is simply absent, which is why the rule above still holds
       for the case it exists for.
    3. **Exactly one sender must be named.** Two ticked bodies, or one ticked
       and one typed, is a Matter that arrived from two places and no
       unambiguous body to answer — so Adressaat is left unanswered rather than
       guessed. The browser has more to go on than a POST does (it watched which
       sender was chosen first) and keeps that seed; the server has only a set,
       so the deterministic reading of a set of two is "no default" (§9).

    A typed sender defaults the *typed* addressee, because a body being named
    for the first time has no primary key until `Loo teema` runs. Both then
    resolve through `resolve_organisation_name` inside one transaction, so the
    same spelling becomes one `Organisation` row used on both relations rather
    than two rows (`app.matters.services.resolve_addressee`, §5).
    """
    if not form.is_bound:
        return None
    if (
        _raw_value(form, "addressee_organisation")
        or (_raw_value(form, "addressee_name") or "").strip()
    ):
        return None
    if _raw_value(form, "addressee_is_manual"):
        return None

    typed = clean_typed_organisation_name(_raw_value(form, "sender_name"))
    if len(senders) + (1 if typed else 0) != 1:
        return None
    if senders:
        return ("addressee_organisation", str(senders[0].pk))
    return ("addressee_name", typed)


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
    #: `Õigusakt`, directly after `Menetlusliik` and answered independently of
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
    #: Still one organisation, and still a radio, because `Matter` holds one
    #: addressee. The approved design draws it as a multi-select mirroring what
    #: ADR 0025 did for senders — a file can be answered to a ministry and a
    #: committee at once — and offers the single-value chip group as the version
    #: to ship if the migration is not wanted yet. That is what this is: the
    #: layout is the design's, the cardinality is the model's, and the schema
    #: change is left as a decision rather than made as a side effect of a form
    #: redesign (Uus teema redesign §5, ADR 0032).
    #:
    #: What is new is that it arrives *answered* on the ordinary journey. A file
    #: that came from X is normally answered to X, so a single named sender
    #: fills this field and the person overrides it only when the ordinary case
    #: does not hold (`_default_addressee`, docs/adr/0069).
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
        widget=OrganisationRadioSelect(attrs={"class": "chip__input"}),
    )
    addressee_name = addressee_name_field()
    #: «This person answered Adressaat themselves», said by the browser.
    #:
    #: Since a named sender fills Adressaat by default, the two states the form
    #: has to tell apart are *nobody has answered yet* and *somebody answered
    #: «Määramata»* — and both post an empty `addressee_organisation`. Without
    #: this the second is unreachable: the server would re-derive the sender and
    #: silently overwrite a deliberate answer, which is the one thing the
    #: default is not allowed to do (task §6).
    #:
    #: Deliberately a hidden field rather than an inference. Display order,
    #: chip position and «which value is currently checked» are all things the
    #: promotion legitimately changes, so none of them can carry this fact.
    #:
    #: Absent with scripting off, which is correct rather than a gap: a browser
    #: that cannot set it also cannot have offered the person the default to
    #: reject, so the server's own rule is the whole behaviour there (§10).
    addressee_is_manual = forms.BooleanField(
        required=False,
        widget=forms.HiddenInput(attrs={"data-addressee-manual": ""}),
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

    def clean_addressee_name(self) -> str:
        return clean_typed_organisation_name(self.cleaned_data.get("addressee_name"))

    def clean_sender_name(self) -> str:
        return clean_typed_organisation_name(self.cleaned_data.get("sender_name"))

    @property
    def addressee_summary(self) -> str:
        """The addressee this form currently holds, as a name to read.

        What the collapsed Adressaat disclosure says after the word itself:
        «Adressaat · Kliimaministeerium», or «Adressaat» when nothing is
        answered. A disclosure that hid whether a question had been answered
        would make the person open it every time to find out (task §12).

        Read off the *rendered choices* rather than by fetching the row. The
        catalogue is already on the page as `(pk, name)` pairs, so the label is
        there to be found and asking the database for a name the template is
        about to print anyway would be a query per render.

        `Määramata` is deliberately not a summary. It is the same state as an
        unanswered field — that is what `blank=True` makes it — and printing it
        would dress "no answer" up as one.
        """
        typed = (_raw_value(self, "addressee_name") or "").strip() if self.is_bound else ""
        if typed:
            return typed
        chosen = str(_raw_value(self, "addressee_organisation") or "") if self.is_bound else ""
        if not chosen:
            return ""
        # `fields[...]` is typed as the base Field, which has no `choices`. This
        # one is a ModelChoiceField by construction.
        for value, label in cast(Any, self.fields["addressee_organisation"]).choices:
            if str(value) == chosen:
                return str(label)
        return ""

    @property
    def addressee_disclosure_open(self) -> bool:
        """Whether the Adressaat disclosure renders open.

        Only for an error on Adressaat itself. A refusal whose message is inside
        a closed accordion is a form that appears to have refused for no reason,
        and «avage see, et näha miks» is not something a page may ask
        (task §11 C).

        Deliberately *not* opened by an answer being present — including the one
        the sender just filled in. A default that made another section unfold
        would be the page reacting to itself, and the summary already says what
        the answer is (§4, §12).
        """
        return bool(self.errors.get("addressee_organisation") or self.errors.get("addressee_name"))

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

        # Read once, used twice: the default below decides from it, and the
        # addressee shortlist is ordered by it. A second read would be a second
        # query for an answer that cannot have changed inside `__init__`.
        self.named_senders = _named_senders(self)

        # Saatja answers Adressaat, and it does so *here* rather than in
        # `clean()`.
        #
        # The value has two readers and they must agree. `clean()` would satisfy
        # the save and leave the re-render of a refused form showing an
        # unanswered Adressaat beside a sender — so a browser with scripting off
        # would be told the field was empty and then find the Matter answered
        # anyway. Writing the derived value into the bound data instead means
        # the rendered radio, `cleaned_data` and the saved Matter are one fact
        # with one origin, and the person can see the default and override it
        # (docs/adr/0069, task §10, §11).
        #
        # `self.data` is a `QueryDict` from a real POST and an ordinary dict
        # from a caller constructing one; `copy()` is the one call both answer
        # with something mutable, and the copy is this form's alone.
        self.addressee_default: tuple[str, str] | None = _default_addressee(
            self, self.named_senders
        )
        if self.addressee_default is not None:
            field, value = self.addressee_default
            # `Form.data` is annotated as a read-only mapping because most forms
            # only ever read it. A `QueryDict` copy is mutable and a plain dict's
            # is a plain dict, so the write below is real on both; the cast says
            # so rather than widening the annotation for every other form.
            data = cast(Any, self.data).copy()
            data[self.add_prefix(field)] = value
            self.data = data

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
            # Adressaat kept the disclosure and reads better for it, so Saatja
            # now matches it: chips first, the whole catalogue and its search
            # one click away, `Uus saatja` outside where it answers "the body I
            # need is not here" without anything having to be opened
            # (task §9, matter_create.html).
            self.sender_tail_count = len(tail)

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
            #
            # **The senders chosen on this form come first.** Replying to
            # whoever wrote to you is the ordinary case, and Saatja and
            # Adressaat are two questions about one catalogue of institutions —
            # so a body ticked as the sender is the single best guess for the
            # addressee, ahead of any historical ranking (docs/adr/0063,
            # task §12).
            #
            # Ordering only — the *answer* is written above, once, and this
            # cannot disagree with it because both read `self.named_senders`.
            # What this guarantees is that the answer is reachable: the chips
            # are what the collapsed Adressaat disclosure summarises and what
            # opening it shows first, so a default left in the long tail would
            # be an answer nobody could see.
            #
            # This is the *server's* half, and it is the half that works on a
            # bound form — a refused save re-renders with the senders that were
            # ticked, and they must not fall back down the page underneath the
            # historical shortlist. Ticking a chip with the form still open is
            # the browser's half (static/js/app.js `bindAddresseeDefault`).
            self.frequent_addressees = _promote_named_senders(
                self.named_senders, addressees_by_usage(viewer)
            )
            shortlist = {organisation.pk for organisation in self.frequent_addressees}
            tail = [
                organisation
                for organisation in Organisation.objects.order_by("name")
                if organisation.pk not in shortlist
            ]
            self.addressee_offered = [*self.frequent_addressees, *tail]
            self.addressee_split = 1 + len(self.frequent_addressees)
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

            # The recorded spellings, onto the controls that carry them.
            #
            # Read once for the whole catalogue and handed to all three choice
            # fields, because the picker searches one pool of institutions
            # through two questions and «MKM» has to find the same ministry
            # whichever of them is being answered (docs/adr/0073, task §13).
            spellings = organisation_alias_terms()
            for field_name in (
                "source_organisations",
                "source_organisations_other",
                "addressee_organisation",
            ):
                cast(Any, self.fields[field_name].widget).alias_terms = spellings
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
    source_organisations = forms.ModelMultipleChoiceField(
        label="Kellelt",
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
    #: There is no precision group behind it either. The approximate-period
    #: control genuinely earns its place on `Oluline tähtaeg`, where a
    #: consultation really does end "in the autumn"; a lawyer's own working day
    #: is a day (ADR 0052 §4).
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
        elif cleaned.get("target_date") is None:
            self.add_error("target_date", "Vali järgmise tegevuse kuupäev.")
        return cleaned

    def as_service_kwargs(self, *, default_responsible: Any = None) -> dict[str, Any]:
        """What ``set_next_action`` needs.

        The kind, the date meaning and the precision are the canonical
        compatibility values, written here and never read from the POST. They
        are honest rather than merely convenient: on this surface the date is
        the day the work gets done, and a lawyer who has to remember to chase a
        ministry writes that as an action — «Kontrollida, kas ministeerium
        vastas» — rather than as a workflow classification (ADR 0052 §1, §3).

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
            "target_date": self.cleaned_data.get("target_date"),
            "date_precision": DatePrecision.EXACT,
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
#:
#: The labels are `EngagementKind`'s, never written out again here. This tuple
#: decides which kinds are offered; what each is called is the enum's answer, so
#: this form cannot call `EMAIL_CAMPAIGN` «Otsepostitus» while `+ Kaasamine`
#: calls it «Kirjade voor» and the chronology calls it something else again
#: (post-QA R2-06).
ENGAGEMENT_CHOICES: tuple[tuple[str, str], ...] = (
    (EngagementKind.SURVEY.value, EngagementKind.SURVEY.label),
    (EngagementKind.EMAIL_CAMPAIGN.value, EngagementKind.EMAIL_CAMPAIGN.label),
    (EngagementKind.OTHER.value, EngagementKind.OTHER.label),
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
    # **One date box and a precision, when that is all the surface offers.**
    #
    # The approved Teema target's `+ Oluline tähtaeg` is a single `Kuupäev` plus
    # `Täpne päev` / `Kuu` / `Kvartal`: somebody picks the day they were told
    # about and says how precisely it was meant. The month, quarter, half and
    # year selects are still here, still posted by the surfaces that have them,
    # and still take precedence — this only fills in what a compact panel cannot
    # ask for, from the date it did ask for. `bounds_for` normalises either way,
    # which is what keeps a quarter entered here and a quarter entered on
    # `Olulised tähtajad` the same stored anchor (docs/adr/0074 §11).
    derived_year = value("year")
    derived_month = value("month")
    derived_quarter = value("quarter")
    derived_half = value("half")
    if exact is not None:
        if derived_year is None:
            derived_year = exact.year
        if derived_month is None:
            derived_month = exact.month
        if derived_quarter is None:
            derived_quarter = (exact.month - 1) // 3 + 1
        if derived_half is None:
            derived_half = 1 if exact.month <= 6 else 2
    if precision != DatePrecision.EXACT.value and exact is not None:
        # The refusal has to land on a control the reader can see. On the target
        # panel that is the date box, whatever precision the chips say.
        field_for_precision = dict.fromkeys(field_for_precision, f"{prefix}_date")
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
        self.fields.update(_precision_fields("deadline", date_label="Kuupäev"))
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
        """`Täpne päev` / `Kuu` / `Kvartal` for `+ Oluline tähtaeg`.

        Three of the five stored precisions. `HALF_YEAR` and `YEAR` are real and
        stay readable on the records that carry them; they are simply not worth
        a chip on a panel whose whole point is that it fits on one row
        (TEEMA_TARGET_SPEC §C.4, docs/adr/0074 §11).
        """
        chosen = self._chosen("deadline_precision", DatePrecision.EXACT.value)
        labels = (
            (DatePrecision.EXACT.value, "Täpne päev"),
            (DatePrecision.MONTH.value, "Kuu"),
            (DatePrecision.QUARTER.value, "Kvartal"),
        )
        return [
            {"value": value, "label": label, "selected": value == chosen} for value, label in labels
        ]

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
        wants_victory = bool((cleaned.get("victory_change") or "").strip())
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
        """`+ Töövõit` — one sentence, and no second question.

        No period. `MatterWorkVictory.period_date` is a *reporting* period, and
        borrowing today's date for it because the panel happens to be open would
        file a win into a reporting year nobody chose. The record is created
        undated, which the domain already supports and the reporting surfaces
        already count separately (Stage-2G brief 13, docs/adr/0074 §8).
        """
        if not wanted:
            cleaned["work_victory_kwargs"] = None
            return
        cleaned["work_victory_kwargs"] = {
            "title": (cleaned.get("victory_change") or "").strip()[:2000],
            "detail": "",
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
            # The day the work is being recorded. The target deliberately does
            # not ask for an engagement date, and the application's convention
            # for «this happened as part of the work I am writing down now» is
            # today in Europe/Tallinn — the same clock `add_entry` stamps with
            # (docs/adr/0074 §9).
            "occurred_on": timezone.localdate(),
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
        set_choices(self, "stage", active_stages())
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
    #: The same two provider pointers the workspace panel asks for, so a
    #: correction made through this form round-trips them rather than dropping
    #: what `+ Kaasamine` stored (docs/adr/0027, amended 2026-09-12).
    smaily_url = provider_link_field("Smaily link", "https://sendsmaily.net/…")
    alchemer_url = provider_link_field("Alchemer link", "https://survey.alchemer.eu/…")
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


class CompactEngagementForm(ChipChoices, forms.Form):
    """`+ Kaasamine` — the kind, who was engaged, how many answered, the replies.

    The business model is untouched this round. `Vastuseid` stays optional and
    blank still means *nobody counted* rather than *nobody answered*; no response
    rate is computed here or anywhere else, and the larger «Alustasin arvamuste
    küsimist» / «Arvamused saabusid» redesign is a separate product round
    (brief §16).

    **Two optional pointers, and they are the only thing this round adds.** One
    consultation routinely has a mailing *and* a questionnaire, so the single
    generic `url` made somebody drop one of the two addresses or keep it
    somewhere nobody can click. `Smaily link` and `Alchemer link` are where
    those live. Both are optional; neither makes an engagement valid on its own,
    because `Keda kaasati` is still what identifies the record
    (docs/adr/0027, amended 2026-09-12).

    ``kind`` validates against the whole stored vocabulary while the panel offers
    three chips, so a historical `WEB_CALL` row stays editable through every
    service that takes a kind.
    """

    use_required_attribute = False

    kind = forms.ChoiceField(
        label="Liik",
        choices=EngagementKind.choices,
        initial=COMPOSER_ENGAGEMENT_KINDS[0][0],
        required=False,
        widget=forms.HiddenInput(),
    )
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
    attachments = workspace_attachments("id_kaasamine_failid")

    @property
    def kind_chips(self) -> list[dict[str, Any]]:
        return self.chips("kind", COMPOSER_ENGAGEMENT_KINDS, COMPOSER_ENGAGEMENT_KINDS[0][0])

    def clean_audience(self) -> str:
        audience = (self.cleaned_data.get("audience") or "").strip()
        if not audience:
            raise forms.ValidationError("Kirjuta, keda kaasati.")
        return audience

    def clean_smaily_url(self) -> str:
        return clean_provider_link(self, "smaily_url")

    def clean_alchemer_url(self) -> str:
        return clean_provider_link(self, "alchemer_url")

    def clean_kind(self) -> str:
        return self.cleaned_data.get("kind") or COMPOSER_ENGAGEMENT_KINDS[0][0]


class CompactImportantDateForm(ChipChoices, forms.Form):
    """`+ Oluline tähtaeg` — a milestone somebody announced, and its letter.

    The canonical `MatterImportantDate` semantics, unchanged: one date box plus
    `Täpne päev` / `Kuu` / `Kvartal`, normalised through `app.workflow.dates`
    so a quarter recorded here is the same stored anchor as a quarter recorded
    on `Olulised tähtajad`. `HALF_YEAR` and `YEAR` remain stored precisions and
    still render on the rows that carry them (docs/adr/0074 §11, brief §17).

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
        self.fields.update(_precision_fields("deadline", date_label="Kuupäev"))

    @property
    def precision_chips(self) -> list[dict[str, Any]]:
        return self.chips(
            "deadline_precision",
            (
                (DatePrecision.EXACT.value, "Täpne päev"),
                (DatePrecision.MONTH.value, "Kuu"),
                (DatePrecision.QUARTER.value, "Kvartal"),
            ),
            DatePrecision.EXACT.value,
        )

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        title = (cleaned.get("deadline_title") or "").strip()
        if not title:
            self.add_error("deadline_title", "Kirjuta, mis tähtaeg see on.")
        anchor, end, precision = _period_anchor(self, "deadline")
        if anchor is None or end is None:
            if not self.has_error("deadline_date"):
                self.add_error("deadline_date", "Oluline tähtaeg vajab kuupäeva või perioodi.")
            return cleaned
        cleaned["important_date_kwargs"] = {
            "title": title,
            "date_value": anchor,
            "period_end": end,
            "date_precision": precision,
        }
        return cleaned


class CompactEffectiveDateForm(forms.Form):
    """`+ Jõustumine` — what commences and the day it does.

    A door onto the canonical `MatterEffectiveDate`, not a second commencement
    model and not an `Entry` pretending to be one. Both halves or neither,
    refused on whichever is missing: a commencement with no date is a sentence,
    and a date with nothing commencing on it is a number (brief §18).

    Stored at `EXACT`, because the panel asks for a day and takes a day. The
    approximate and general-order kinds the domain also carries keep their own
    surfaces and their own rows.
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
    effective_on = EstonianDateField(label="Jõustub", required=False, widget=EstonianDateInput())
    attachments = workspace_attachments("id_joustumine_failid")

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        title = (cleaned.get("effective_title") or "").strip()
        when = cleaned.get("effective_on")
        if not title:
            self.add_error("effective_title", "Kirjuta, mis jõustub.")
        if when is None and not self.has_error("effective_on"):
            self.add_error("effective_on", "Märgi, millal see jõustub.")
        if self.errors:
            return cleaned
        cleaned["effective_date_kwargs"] = {
            "description": title,
            "date_value": when,
            "date_precision": DatePrecision.EXACT.value,
        }
        return cleaned


class CompactWorkVictoryForm(forms.Form):
    """`+ Töövõit` — what changed, and the evidence that it did.

    One sentence and its files. A win closes nothing, completes nothing and is
    recorded on the day it happened rather than on the day the file finishes
    (brief §19).

    No period. `MatterWorkVictory.period_date` is a *reporting* period, and
    borrowing today's date for it because a panel happened to be open would file
    a win into a reporting year nobody chose.
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

    def clean_victory_change(self) -> str:
        value = (self.cleaned_data.get("victory_change") or "").strip()
        if not value:
            raise forms.ValidationError("Kirjuta, mis muutus.")
        return value[:2000]


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
