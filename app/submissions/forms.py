from __future__ import annotations

from datetime import date
from typing import Any, cast

from django import forms
from django.utils import timezone
from django.utils.formats import date_format

from app.core.widgets import EstonianDateField, EstonianDateInput
from app.matters.forms import set_choices
from app.matters.models import MatterWebsiteOverview
from app.organisations.models import Organisation
from app.submissions.enums import SubmissionKind
from app.submissions.links import selectable_tags, selectable_website_overviews
from app.taxonomy.models import Tag

SELECT_WIDGET = forms.Select(attrs={"class": "field__input"})

#: Form prefixes for the two opinion forms on Dokumendid.
#:
#: That page renders three forms carrying a `title` — the SharePoint working
#: reference, a new opinion, and registering a send — and three `id="id_title"`
#: on one page make every `<label for>` ambiguous, for a screen reader and for a
#: browser test alike. The same reason `app/matters/views.py` prefixes the
#: private note: the composer's own field is called `body` too.
#:
#: The working-document form keeps the bare names, because it was there first
#: and its field names are what its route already accepts.
CREATE_PREFIX = "arvamus"
REGISTER_PREFIX = "saadetud"


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

    `sent_on` is **required** and is a **day**. A person recording that an
    opinion went out on the 12th knows the day and not the hour, so the date is
    stored as aware midnight with `SentAtPrecision.DATE` and the UI never reads
    that anchor back as «00:00» (`app/submissions/enums.py`).

    It used to be optional, and blank meant *now*. That is the right reading for
    `Märgi saadetuks`, where pressing the button is the send — and the wrong one
    here, where the whole act is recording a send that already happened. Blank
    produced a canonical SENT Submission stamped with today, and the outbound
    register and the process timeline then reported `Arvamus välja <today>`
    about a letter whose date nobody had supplied (R2-01). There is no answer
    this form can infer, so it asks.

    `recipients` is required for the same reason and is required *here only*:
    `SubmissionCreateForm` opens a draft, where "who this goes to" is a question
    still being worked out. A registered send is a statement that Koda wrote to
    somebody, and a send with no addressee is not a fact anybody can check.
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
    #: `EstonianDateField`, never a native `type="date"`. A native control takes
    #: its format from the *browser's* locale rather than the page's, so a
    #: US-English Chrome shows `mm/dd/yyyy` on an Estonian form and reads
    #: `7.9.2026` as the 9th of July — on the one field in this form whose value
    #: becomes the date Koda claims to have written to a ministry
    #: (app/core/widgets.py).
    sent_on = EstonianDateField(
        label="Saadetud",
        required=True,
        widget=EstonianDateInput(),
        help_text="Kuupäev, mil arvamus välja saadeti.",
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
        # Narrowed from the parent rather than redeclared, so the queryset,
        # widget and label `SubmissionCreateForm.__init__` sets up still apply.
        recipients = self.fields["recipients"]
        recipients.required = True
        recipients.label = "Adressaadid"
        recipients.help_text = "Kellele arvamus saadeti. Vähemalt üks."
        # `cast` rather than a runtime check: the field is declared on this class
        # three lines up, and a `TypedChoiceField` it demonstrably is.
        cast(forms.ChoiceField, self.fields["document"]).choices = [
            (str(document.pk), _document_label(document)) for document in documents or []
        ]
        self.order_fields(self.field_order)

    def clean_sent_on(self) -> date | None:
        """A send is never in the future. The record says what happened.

        Unchanged by the required-field correction above: a date that is present
        and in the future is still a different refusal from one that is absent,
        and both still refuse.
        """
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


class WebsiteOverviewChoiceField(forms.ModelMultipleChoiceField):
    """`Ülevaade / uudis` as a chip a person can actually tell apart.

    The model has **no title** — deliberately, since a plan does not have one yet
    and inventing one would put a name nobody chose beside the real page
    (docs/adr/0081 §1). So `__str__` is `«Avaldatud: <matter id>»`, which is right
    for a log line and useless in a list where several rows differ only in which
    page they point at.

    The label is therefore built from the facts the row really carries: its state,
    the day it states, and — for a published row — its address. Nothing is
    invented: a plan reads `Plaanis`, with the day it was **recorded** named as
    such rather than printed where a publication date would be, which is the one
    thing docs/adr/0089 §10 forbids.

    The address is plain text in a label and not an anchor. ADR 0085 §2 keeps a
    raw URL out of a *link's* text because a look-alike address in link position
    is believed; here it is the only thing that distinguishes two published
    write-ups on one file, and it is not offered as something to click.
    """

    def label_from_instance(self, obj: Any) -> str:
        from app.matters.enums import WebsiteOverviewStatus

        state = str(obj.get_status_display())
        if obj.status == WebsiteOverviewStatus.PUBLISHED:
            parts = [state, obj.chronology_date]
            if obj.url:
                parts.append(obj.url if len(obj.url) <= 70 else obj.url[:69] + "…")
            return " · ".join(parts)
        # `lisatud`, named for what it is. A `Plaanis` row carries neither an
        # address nor a date, and three plans on one file must still be told
        # apart — so the day somebody recorded the plan is printed under its own
        # word rather than in the position a publication date occupies.
        recorded = date_format(timezone.localtime(obj.created_at), "j.n.Y")
        return f"{state} · lisatud {recorded}"


class SubmissionMetadataForm(forms.Form):
    """`Arvamuse märksõnad ja seosed` — the two facts docs/adr/0093 decided.

    **It edits exactly these two and nothing else.** No title, no `Liik`, no
    recipient, no date, no status and no file: a metadata surface that could also
    re-address a sent letter would be a second opinion editor, which is what
    ADR 0061 retired. The send workflow is untouched and this form cannot reach it.

    **Neither field is required.** Zero keywords and zero linked write-ups is the
    ordinary state of every opinion in the register and stays the ordinary state
    after this ships — nothing is backfilled and nothing is proposed.

    **Neither field is ever pre-ticked from somewhere else.** The Matter's own
    `Sildid` are not offered here as though they belonged to the letter, no
    overview is selected because its address resembles the title or its date sits
    near the send, and the `initial` this form is opened with is read from the
    Submission's own assignments and from nothing else (docs/adr/0093 §1, §2).

    Both are chip checkbox groups, the control `Muuda teemat` already uses for
    `Sildid` and `Valdkonnad`, so a person who has learned one classification
    control has learned this one.
    """

    use_required_attribute = False

    tags = forms.ModelMultipleChoiceField(
        label="Märksõnad",
        # Replaced in `__init__` with `selectable_tags`, and empty here for the
        # reason every other picker in this product is: a field-level queryset is
        # evaluated at import time and would be the wrong answer by the time
        # anybody opened the form.
        queryset=Tag.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "chip__input"}),
        help_text="Mida see kiri käsitles. Teema sildid on eraldi ja neid siit ei muudeta.",
    )
    website_overviews = WebsiteOverviewChoiceField(
        label="Seotud ülevaated / uudised",
        queryset=MatterWebsiteOverview.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "chip__input"}),
        help_text="Sama teema ülevaated ja uudised, mis seda arvamust kajastavad.",
    )

    def __init__(
        self,
        *args: Any,
        submission: Any,
        viewer: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.submission = submission
        # Both querysets come from `app.submissions.links`, which is also what the
        # service resolves a POST against. One definition of «what may be chosen»,
        # so a candidate the page never offered cannot be accepted and one it did
        # offer cannot be refused (docs/adr/0093 §4).
        cast(Any, self.fields["tags"]).queryset = selectable_tags(submission)
        cast(Any, self.fields["website_overviews"]).queryset = selectable_website_overviews(
            submission, viewer=viewer
        )


class SentOpinionEditForm(forms.Form):
    """`Muuda` on a recorded `Koja arvamus`, from its own chronology row.

    `Arvamus välja` was the one row on `Teema käik` with no correction control
    at all — `Meile saadetud tagasiside`, `Teiste arvamus`, `Kaasamine`,
    `Märge` and `Ülevaade / uudis` all carry one — on the record where a wrong
    date or a wrong recipient matters most (QA-023).

    **Four questions, and they are the four that were asked at capture.** The
    day, the `Liik`, the `Kokkuvõte` and who the letter was addressed to: what
    `Registreeri saatmine` asked, minus the two things a correction may not
    touch. `Saadetud fail` is absent because the evidence is immutable and a
    letter whose text was wrong is a different letter; `Pealkiri` is absent
    because the chronology does not read it and `Arvamuse märksõnad ja seosed`
    is where a letter's own metadata is edited (docs/adr/0093).

    **`Teadmiseks` is absent and is not cleared.** `set_recipients` replaces the
    whole set, so a form that asked only about addressees and handed back an
    empty second list would quietly drop every copied-in committee. The service
    reads the current ones and passes them through unchanged
    (`correct_sent_opinion`, `_for_information_of`).

    ``revision`` is the version the form was filled from, carried through the
    round trip so the service can refuse a save whose record has moved on — the
    same hidden field every other correction on the Teema page carries, and for
    the same reason (QA-002).
    """

    use_required_attribute = False

    #: A day, and never a native `type="date"`, for `RegisterSentOpinionForm`'s
    #: reason: a native control takes its format from the *browser's* locale, so
    #: a US-English Chrome reads `7.9.2026` as the 9th of July — on the one
    #: field whose value is the date Koda claims to have written to a ministry.
    sent_on = EstonianDateField(
        label="Saadetud",
        required=True,
        widget=EstonianDateInput(),
        help_text="Kuupäev, mil arvamus välja saadeti.",
    )
    kind = forms.ChoiceField(
        label="Liik",
        choices=SubmissionKind.choices,
        widget=forms.Select(attrs={"class": "field__input"}),
    )
    summary = forms.CharField(
        label="Kokkuvõte",
        required=False,
        widget=forms.Textarea(attrs={"class": "field__input", "rows": "3"}),
        help_text="Mida kiri ütles. Seda loeb teema käik.",
    )
    recipients = forms.ModelMultipleChoiceField(
        label="Adressaadid",
        queryset=Organisation.objects.none(),
        required=True,
        widget=forms.SelectMultiple(attrs={"class": "field__input", "size": "4"}),
        help_text="Kellele kiri formaalselt saadeti. Teadmiseks-saajad jäävad muutmata.",
    )
    revision = forms.CharField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args: Any, organisations: Any = None, record: Any = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.record = record
        # The **queryset**, not only the rendered choices: that is what
        # validates a posted id, so a crafted POST naming an organisation
        # outside the catalogue is refused by the field itself.
        set_choices(self, "recipients", organisations)
        if record is not None:
            # A chronology may show several sent opinions, and Django would give
            # every one of these controls the same `id` — enough to make a
            # `<label for>` reach the wrong box. The same per-record derivation
            # `ExternalPositionEditForm` uses.
            self.auto_id = f"id_koja_arvamus_{record.pk}_%s"
