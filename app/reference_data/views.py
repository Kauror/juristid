"""Haldus → Klassifikaatorid (docs/adr/0187).

**One gate in front of every route:** `reference_actor`, which is the request's
own account when it holds `reference.manage`. Anybody else — anonymous, the
shared gate without a persona, a READER, a specialist without the grant, a
technical administrator without it — gets the same 404, so the pages do not
confirm that they exist (the reasoning of docs/adr/0037 and 0145 §12).

Views are thin: each change is one call into `app.organisations.administration`
or `app.taxonomy.administration`, which own the rules, the locks, the stale-page
refusal and the audit row. A refusal comes back as the form's error with a 400,
and a success as a redirect with a message — the post/redirect/get every other
form in the product uses.

**Usage counts are business information.** How many Teemad carry a tag is shown
to somebody who may read Teemad; a technical administrator holding the
capability manages the words without being told how the department's work is
distributed across them (docs/adr/0181).
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from app.core.authorization import may_read_business_content
from app.core.errors import DomainError
from app.haldus.sections import visible_sections
from app.organisations import administration as catalogue
from app.organisations.duplicates import identity_report
from app.organisations.models import AliasType, Organisation, OrganisationAlias
from app.reference_data import forms
from app.reference_data.access import reference_actor
from app.reference_data.inventory import BY_KEY, CONTROLLED, REVIEWED_CHANGE_ONLY, VOCABULARIES
from app.taxonomy import administration as vocabulary
from app.taxonomy.models import Tag, TagAlias

NOT_HERE = "Lehte ei leitud."
SECTION = "klassifikaatorid"


def reference_data_required[R: HttpResponseBase](
    view: Callable[..., R],
) -> Callable[..., R | HttpResponseBase]:
    """The capability first, then sign-in; every refusal past sign-in is a 404.

    The actor is asked before `login_required`, because a verified
    administrator under the shared gate may have chosen no persona at all — a
    technical administrator has no persona to choose — and must not be sent to
    the persona list to reach a page their own session already opens
    (docs/adr/0186). Anybody else without an actor meets the ordinary sign-in.
    """

    uncached = never_cache(view)
    signed_in = login_required(uncached)

    @functools.wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> R | HttpResponseBase:
        if reference_actor(request) is not None:
            return uncached(request, *args, **kwargs)
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            # Never reaches the view: `login_required` redirects first.
            return signed_in(request, *args, **kwargs)
        raise Http404(NOT_HERE)

    return wrapper


def _render(request: HttpRequest, template: str, context: dict[str, Any], status: int = 200):
    return render(
        request,
        template,
        {
            "haldus_active": SECTION,
            "haldus_sections": visible_sections(request),
            "shows_usage": may_read_business_content(request.user),
            **context,
        },
        status=status,
    )


# -- overview ----------------------------------------------------------------------------


@reference_data_required
@require_GET
def index(request: HttpRequest) -> HttpResponse:
    return _render(request, "reference_data/index.html", {"vocabularies": VOCABULARIES})


@reference_data_required
@require_GET
def controlled_vocabulary(request: HttpRequest, key: str) -> HttpResponse:
    """One controlled vocabulary, read-only, with how many records carry each value."""
    entry = BY_KEY.get(key)
    if key not in CONTROLLED or entry is None or entry.rows is None:
        raise Http404(NOT_HERE)
    return _render(
        request,
        "reference_data/vocabulary.html",
        {"vocabulary": entry, "rows": list(entry.rows()), "explanation": REVIEWED_CHANGE_ONLY},
    )


# -- organisations ---------------------------------------------------------------------


def _organisations_page(
    request: HttpRequest, form: forms.OrganisationCreateForm, *, query: str = "", status: int = 200
) -> HttpResponse:
    """The catalogue list with its create form — the GET page and a refused create alike.

    A refused «Lisa organisatsioon» shows the same rows the list does: an empty
    table under the error would read as an empty catalogue.
    """
    rows = list(catalogue.search_organisations(query, limit=100).prefetch_related("aliases"))
    return _render(
        request,
        "reference_data/organisations.html",
        {"query": query, "rows": rows, "form": form},
        status=status,
    )


@reference_data_required
@require_GET
def organisations(request: HttpRequest) -> HttpResponse:
    query = (request.GET.get("q") or "").strip()[:200]
    return _organisations_page(request, forms.OrganisationCreateForm(), query=query)


@reference_data_required
@require_GET
def organisation_duplicates(request: HttpRequest) -> HttpResponse:
    """The identity report `check_organisation_duplicates` prints, read-only."""
    return _render(
        request, "reference_data/organisation_duplicates.html", {"report": identity_report()}
    )


@reference_data_required
@require_POST
def organisation_create(request: HttpRequest) -> HttpResponse:
    form = forms.OrganisationCreateForm(request.POST)
    if form.is_valid():
        try:
            created = catalogue.create_organisation(
                actor=reference_actor(request),
                name=form.cleaned_data["name"],
                organisation_type=form.cleaned_data["organisation_type"],
                registry_code=form.cleaned_data["registry_code"],
            )
        except DomainError as error:
            form.add_error("name", str(error))
        else:
            messages.success(request, f"Lisatud: {created.name}.")
            return redirect("reference_data:organisation", pk=created.pk)
    return _organisations_page(request, form, status=400)


def _organisation_context(organisation: Organisation, **forms_in: Any) -> dict[str, Any]:
    revision = catalogue.organisation_revision(organisation)
    aliases = list(organisation.aliases.all())
    reviewed_key = catalogue.reviewed_entry_key(organisation)
    return {
        "organisation": organisation,
        "revision": revision,
        "aliases": [
            {
                "row": alias,
                "removable": alias.alias_type != AliasType.HISTORICAL_NAME
                and not catalogue.is_reviewed_alias(alias),
            }
            for alias in aliases
        ],
        "is_reviewed": bool(reviewed_key),
        "references": catalogue.organisation_references(organisation),
        "continuity": {
            "predecessor": organisation.predecessor,
            "successors": list(organisation.successors.all()),
        },
        "rename_form": forms_in.get("rename_form")
        or forms.OrganisationRenameForm(initial={"name": organisation.name, "revision": revision}),
        "alias_form": forms_in.get("alias_form")
        or forms.OrganisationAliasForm(initial={"revision": revision}),
    }


def _organisation(pk: Any) -> Organisation:
    return get_object_or_404(
        Organisation.objects.select_related("predecessor").prefetch_related("aliases"), pk=pk
    )


@reference_data_required
@require_GET
def organisation(request: HttpRequest, pk: Any) -> HttpResponse:
    return _render(
        request, "reference_data/organisation.html", _organisation_context(_organisation(pk))
    )


@reference_data_required
@require_POST
def organisation_rename(request: HttpRequest, pk: Any) -> HttpResponse:
    current = _organisation(pk)
    form = forms.OrganisationRenameForm(request.POST)
    if form.is_valid():
        try:
            result = catalogue.rename_organisation(
                actor=reference_actor(request),
                organisation=current,
                name=form.cleaned_data["name"],
                expected_revision=form.revision_token(),
                keep_old_name=form.cleaned_data["keep_old_name"],
            )
        except DomainError as error:
            form.add_error("name", str(error))
        else:
            note = " Senine nimi jäi ajalooliseks nimekujuks." if result.historical_alias else ""
            messages.success(request, f"Nimi parandatud: {result.organisation.name}.{note}")
            return redirect("reference_data:organisation", pk=current.pk)
    return _render(
        request,
        "reference_data/organisation.html",
        _organisation_context(_organisation(pk), rename_form=form),
        status=400,
    )


@reference_data_required
@require_POST
def organisation_alias_add(request: HttpRequest, pk: Any) -> HttpResponse:
    current = _organisation(pk)
    form = forms.OrganisationAliasForm(request.POST)
    if form.is_valid():
        try:
            created = catalogue.add_organisation_alias(
                actor=reference_actor(request),
                organisation=current,
                alias=form.cleaned_data["alias"],
                alias_type=form.cleaned_data["alias_type"],
                expected_revision=form.revision_token(),
            )
        except DomainError as error:
            form.add_error("alias", str(error))
        else:
            messages.success(request, f"Nimekuju lisatud: {created.alias}.")
            return redirect("reference_data:organisation", pk=current.pk)
    return _render(
        request,
        "reference_data/organisation.html",
        _organisation_context(_organisation(pk), alias_form=form),
        status=400,
    )


@reference_data_required
@require_POST
def organisation_alias_remove(request: HttpRequest, pk: Any, alias_pk: Any) -> HttpResponse:
    alias = get_object_or_404(OrganisationAlias, pk=alias_pk, organisation_id=pk)
    form = forms.RevisionForm(request.POST)
    form.is_valid()
    try:
        catalogue.remove_organisation_alias(
            actor=reference_actor(request), alias=alias, expected_revision=form.revision_token()
        )
    except DomainError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Nimekuju eemaldatud: {alias.alias}.")
    return redirect("reference_data:organisation", pk=pk)


# -- tags ------------------------------------------------------------------------------


def _tags_page(
    request: HttpRequest, form: forms.TagCreateForm, *, query: str = "", status: int = 200
) -> HttpResponse:
    """The tag list with its create form — the GET page and a refused «Lisa silt» alike."""
    rows = list(vocabulary.search_tags(query).prefetch_related("aliases"))
    return _render(
        request,
        "reference_data/tags.html",
        {"query": query, "rows": rows, "form": form},
        status=status,
    )


@reference_data_required
@require_GET
def tags(request: HttpRequest) -> HttpResponse:
    query = (request.GET.get("q") or "").strip()[:200]
    return _tags_page(request, forms.TagCreateForm(), query=query)


@reference_data_required
@require_POST
def tag_create(request: HttpRequest) -> HttpResponse:
    form = forms.TagCreateForm(request.POST)
    if form.is_valid():
        try:
            created = vocabulary.create_tag(
                actor=reference_actor(request),
                key=form.cleaned_data["key"],
                name=form.cleaned_data["name"],
                definition=form.cleaned_data["definition"],
            )
        except DomainError as error:
            field = (
                "key" if str(error) in (vocabulary.KEY_INVALID, vocabulary.KEY_TAKEN) else "name"
            )
            form.add_error(field, str(error))
        else:
            messages.success(request, f"Silt lisatud: {created.name_et}.")
            return redirect("reference_data:tag", pk=created.pk)
    return _tags_page(request, form, status=400)


def _tag(pk: Any) -> Tag:
    return get_object_or_404(
        Tag.objects.select_related("merged_into").prefetch_related("aliases"), pk=pk
    )


def _tag_context(tag: Tag, **forms_in: Any) -> dict[str, Any]:
    revision = vocabulary.tag_revision(tag)
    return {
        "tag": tag,
        "revision": revision,
        "aliases": list(tag.aliases.all()),
        "usage": vocabulary.tag_usage(tag),
        "canonical": tag.canonical(),
        "merged_from": list(tag.merged_from.order_by("name_et")),
        "edit_form": forms_in.get("edit_form")
        or forms.TagEditForm(
            initial={"name": tag.name_et, "definition": tag.definition, "revision": revision}
        ),
        "alias_form": forms_in.get("alias_form")
        or forms.TagAliasForm(initial={"revision": revision}),
        "merge_form": forms_in.get("merge_form")
        or forms.TagMergeForm(tag=tag, initial={"revision": revision}),
    }


@reference_data_required
@require_GET
def tag(request: HttpRequest, pk: Any) -> HttpResponse:
    return _render(request, "reference_data/tag.html", _tag_context(_tag(pk)))


@reference_data_required
@require_POST
def tag_edit(request: HttpRequest, pk: Any) -> HttpResponse:
    current = _tag(pk)
    form = forms.TagEditForm(request.POST)
    if form.is_valid():
        try:
            vocabulary.update_tag(
                actor=reference_actor(request),
                tag=current,
                name=form.cleaned_data["name"],
                definition=form.cleaned_data["definition"],
                expected_revision=form.revision_token(),
                keep_old_name=form.cleaned_data["keep_old_name"],
            )
        except DomainError as error:
            form.add_error("name", str(error))
        else:
            messages.success(request, "Silt salvestatud.")
            return redirect("reference_data:tag", pk=current.pk)
    return _render(
        request, "reference_data/tag.html", _tag_context(_tag(pk), edit_form=form), status=400
    )


@reference_data_required
@require_POST
def tag_alias_add(request: HttpRequest, pk: Any) -> HttpResponse:
    current = _tag(pk)
    form = forms.TagAliasForm(request.POST)
    if form.is_valid():
        try:
            created = vocabulary.add_tag_alias(
                actor=reference_actor(request),
                tag=current,
                alias=form.cleaned_data["alias"],
                expected_revision=form.revision_token(),
            )
        except DomainError as error:
            form.add_error("alias", str(error))
        else:
            messages.success(request, f"Nimekuju lisatud: {created.alias}.")
            return redirect("reference_data:tag", pk=current.pk)
    return _render(
        request, "reference_data/tag.html", _tag_context(_tag(pk), alias_form=form), status=400
    )


@reference_data_required
@require_POST
def tag_alias_remove(request: HttpRequest, pk: Any, alias_pk: Any) -> HttpResponse:
    alias = get_object_or_404(TagAlias, pk=alias_pk, tag_id=pk)
    form = forms.RevisionForm(request.POST)
    form.is_valid()
    try:
        vocabulary.remove_tag_alias(
            actor=reference_actor(request), alias=alias, expected_revision=form.revision_token()
        )
    except DomainError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Nimekuju eemaldatud: {alias.alias}.")
    return redirect("reference_data:tag", pk=pk)


@reference_data_required
@require_http_methods(["GET", "POST"])
def tag_merge(request: HttpRequest, pk: Any) -> HttpResponse:
    """Liitmine — its own page and its own decision.

    A merge cannot be undone here, so choosing the target on the tag page only
    opens this page (GET). It names both tags and, for a reader of Teemad, how
    many records carry the retiring one; the button on it is the POST. The
    revision travels from the tag page through this one, so a tag changed in
    between is refused as stale rather than merged unseen — the
    `confirm_work_victory` convention.
    """
    current = _tag(pk)
    if request.method == "GET":
        chosen = forms.TagMergeForm(request.GET, tag=current)
        if not chosen.is_valid():
            return _render(
                request,
                "reference_data/tag.html",
                _tag_context(current, merge_form=chosen),
                status=400,
            )
        return _render(
            request,
            "reference_data/confirm_tag_merge.html",
            {
                "tag": current,
                "into": chosen.cleaned_data["into"],
                "revision": chosen.revision_token(),
                "usage": vocabulary.tag_usage(current),
            },
        )
    form = forms.TagMergeForm(request.POST, tag=current)
    if form.is_valid():
        try:
            vocabulary.merge_tag(
                actor=reference_actor(request),
                tag=current,
                into=form.cleaned_data["into"],
                expected_revision=form.revision_token(),
            )
        except DomainError as error:
            form.add_error("into", str(error))
        else:
            messages.success(
                request,
                f"«{current.name_et}» liideti sildiga «{form.cleaned_data['into'].name_et}».",
            )
            return redirect("reference_data:tag", pk=current.pk)
    return _render(
        request, "reference_data/tag.html", _tag_context(_tag(pk), merge_form=form), status=400
    )


@reference_data_required
@require_POST
def tag_status(request: HttpRequest, pk: Any) -> HttpResponse:
    """Aegunuks märkimine või taas kasutusele võtmine — one route, the verb in the form."""
    current = _tag(pk)
    form = forms.RevisionForm(request.POST)
    form.is_valid()
    action = request.POST.get("tegevus")
    service = {"aegunuks": vocabulary.deactivate_tag, "kasutusele": vocabulary.reactivate_tag}.get(
        action or ""
    )
    if service is None:
        messages.error(request, "Tundmatu toiming.")
        return redirect("reference_data:tag", pk=pk)
    try:
        service(
            actor=reference_actor(request), tag=current, expected_revision=form.revision_token()
        )
    except DomainError as error:
        messages.error(request, str(error))
    else:
        messages.success(
            request,
            "Silt märgiti aegunuks." if action == "aegunuks" else "Silt on taas kasutusel.",
        )
    return redirect("reference_data:tag", pk=pk)
