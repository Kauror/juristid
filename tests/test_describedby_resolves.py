"""Every `aria-describedby` in a refusal or an on-demand editor resolves in that response.

Django's `BoundField` puts `aria-describedby="<auto_id>_error"` on a widget
whose field has errors and `<auto_id>_helptext` on one whose field declares help
text, whatever the template prints. These forms are rendered field by field, by
hand, so a template that prints the error or the help sentence into an element
without that id leaves the control pointing at nothing: a screen reader follows
the pointer, finds no element, and reads the box with no description at all
(ENG-092).

`tests/test_form_help_accessibility.py` holds the same invariant over first
renders of whole pages. That half was already clean; the defect lived where a
page is not rendered in full — a POST the form refuses, which re-renders the
Teema view with the errors in place, and the fragments a `Muuda` button fetches
into a chronology row. Those are what this module fires at.

**Resolved inside the same response**, and exactly once. An htmx swap puts the
fragment into a page that happens to contain other things, but nothing promises
the id a fragment points at will be among them — the description has to arrive
with the control. Two elements answering to one id is the same defect read from
the other end: the browser picks one and nobody chose which.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from html.parser import HTMLParser
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.core.dates import format_estonian_date
from app.submissions.enums import SubmissionStatus
from app.taxonomy.models import LegalInstrumentType
from app.workflow.models import StageVocabulary
from tests import factories

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


class _Ids(HTMLParser):
    """Every id an element claims, and every token an `aria-describedby` asks for."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: Counter[str] = Counter()
        self.wanted: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name == "id" and value:
                self.ids[value] += 1
            elif name == "aria-describedby" and value:
                self.wanted.extend(value.split())

    handle_startendtag = handle_starttag


def dangling_descriptions(html: str) -> list[str]:
    """The `aria-describedby` tokens that do not name exactly one element here."""
    parser = _Ids()
    parser.feed(html)
    parser.close()
    return [
        f"{token} (x{parser.ids[token]})"
        for token in dict.fromkeys(parser.wanted)
        if parser.ids[token] != 1
    ]


# ---------------------------------------------------------------------------
# The world: one of every record a correction editor opens on
# ---------------------------------------------------------------------------


@pytest.fixture
def world(normal_matter, specialist) -> dict[str, Any]:
    from app.documents.services import add_evidence_version
    from app.matters.enums import EngagementKind
    from app.matters.services import (
        add_engagement,
        change_stage,
        plan_website_overview,
        publish_website_overview,
        record_external_position,
        record_procedural_development,
        record_procedural_link,
    )

    matter = normal_matter
    organisation = factories.OrganisationFactory()
    # A procedure, so `Muuda kulgu` has phases to draw, and a `Hetkeseis` on one
    # of them, so one phase is protected and carries its reason as help text.
    matter.legal_instruments.set([LegalInstrumentType.objects.get(key="seadus")])
    change_stage(
        matter=matter, stage=StageVocabulary.objects.get(key="consultation"), actor=specialist
    )

    # An open step, because `+ Järgmine tegevus` is drawn beside one and nowhere
    # else: without it a refused `set_action` has no panel to put its errors in.
    factories.NextActionFactory(
        matter=matter,
        responsible=specialist,
        target_date=timezone.localdate() + timedelta(days=3),
    )
    entry = factories.EntryFactory(matter=matter, author=specialist, body="<p>Algne.</p>")
    engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="Sünteetiline kaasamine",
        url="https://example.org/kaasamine",
        feedback_deadline=timezone.localdate() + timedelta(days=7),
        actor=specialist,
    )
    # Filed as history: the one kind of round `Ootan tagasisidet` is offered on
    # since docs/adr/0132 — every round a person records is already open.
    quiet_engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="Ootuseta kaasamine",
        lifecycle_tracked=False,
        actor=specialist,
    )
    external_position = record_external_position(
        matter=matter,
        organisation=organisation,
        url="https://example.org/seisukoht",
        actor=specialist,
    )
    development = record_procedural_development(
        matter=matter, title="Sünteetiline areng", note="", actor=specialist
    )
    procedural_link = record_procedural_link(
        matter=matter, kind="EIS", url="https://eelnoud.valitsus.ee/toimik", actor=specialist
    )
    published_overview = publish_website_overview(
        overview=plan_website_overview(matter=matter, actor=specialist),
        url="https://koda.ee/uudised/sunteetiline",
        published_on=timezone.localdate(),
        actor=specialist,
    )
    document = factories.DocumentFactory(matter=matter)
    version = add_evidence_version(
        document=document,
        content=b"%PDF-1.4\nlopp",
        original_filename="lopp.pdf",
        mime_type="application/pdf",
    )
    sent_submission = factories.SubmissionFactory(
        matter=matter,
        title="Saadetud",
        status=SubmissionStatus.SENT,
        sent_at=timezone.now(),
        final_version=version,
    )
    return {
        "matter": matter,
        "organisation": organisation,
        "entry": entry,
        "engagement": engagement,
        "quiet_engagement": quiet_engagement,
        "external_position": external_position,
        "development": development,
        "procedural_link": procedural_link,
        "published_overview": published_overview,
        "sent_submission": sent_submission,
    }


# ---------------------------------------------------------------------------
# The surfaces
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Surface:
    label: str
    route: str
    kwargs: Callable[[dict[str, Any]], dict[str, Any]]
    #: The refused POST; `None` for a surface that is only ever fetched.
    refusal: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    #: Whether a GET opens an editor fragment on this route.
    editor: bool = True
    #: Files riding with the refused POST.
    files: Callable[[], dict[str, Any]] | None = None
    #: Whether the refusal must carry at least one `…_error` description, so a
    #: payload the form happened to accept cannot pass this case vacuously.
    describes_an_error: bool = True

    @property
    def slug(self) -> str:
        return self.route.split(":", 1)[1]


def _on_matter(w: dict[str, Any]) -> dict[str, Any]:
    return {"pk": w["matter"].pk}


NOT_A_DATE = "ei ole kuupäev"


def _empty_upload(name: str = "attachments") -> Callable[[], dict[str, Any]]:
    """An empty file, which an upload field refuses before any service runs."""
    return lambda: {name: SimpleUploadedFile("tuhi.pdf", b"")}


NOT_A_URL = "ei ole aadress"

SURFACES: tuple[Surface, ...] = (
    # -- `+ Lisa` panels on the Teema page: a refusal re-renders the view ------
    Surface(
        "tähtaeg",
        "matters:add_important_date",
        _on_matter,
        lambda w: {"deadline_precision": "EXACT", "deadline_date": NOT_A_DATE},
        editor=False,
        files=_empty_upload(),
    ),
    Surface(
        "jõustumine",
        "matters:add_effective_date",
        _on_matter,
        lambda w: {"effective_on": NOT_A_DATE},
        editor=False,
        files=_empty_upload(),
    ),
    Surface(
        "töövõit",
        "matters:add_work_victory",
        _on_matter,
        lambda w: {"victory_date": NOT_A_DATE},
        editor=False,
        files=_empty_upload(),
    ),
    # `+ Lisa · Tavaline` (`add_note`) has no panel since 2026-10-07; its
    # place in the family is `Arvamuse tähtaeg`.
    Surface(
        "arvamuse-tähtaeg",
        "matters:add_response_deadline",
        _on_matter,
        lambda w: {"response_deadline_date": NOT_A_DATE},
        editor=False,
    ),
    Surface(
        "kaasamine",
        "matters:add_engagement_compact",
        _on_matter,
        lambda w: {"occurred_on": NOT_A_DATE, "smaily_url": NOT_A_URL},
        editor=False,
        files=_empty_upload(),
    ),
    Surface(
        "väline seisukoht",
        "matters:add_external_position",
        _on_matter,
        lambda w: {"position_precision": "EXACT", "url": NOT_A_URL},
        editor=False,
        files=_empty_upload(),
    ),
    Surface(
        "meile saadetud tagasiside",
        "matters:add_received_feedback",
        _on_matter,
        lambda w: {"position_precision": "EXACT", "url": NOT_A_URL},
        editor=False,
    ),
    Surface(
        "koja arvamus",
        "matters:add_koda_opinion",
        _on_matter,
        lambda w: {"sent_on": NOT_A_DATE},
        editor=False,
        files=_empty_upload("upload"),
    ),
    Surface(
        "ülevaade / uudis",
        "matters:add_website_overview",
        _on_matter,
        lambda w: {"url": NOT_A_URL, "published_on": NOT_A_DATE},
        editor=False,
    ),
    Surface(
        "järgmine tegevus",
        "matters:set_action",
        _on_matter,
        lambda w: {"text": "", "kind": "DO", "target_date": NOT_A_DATE},
        editor=False,
    ),
    # -- `Saabunud`, whose refusal re-renders the intake page -----------------
    Surface(
        "saabunud",
        "matters:intake",
        lambda w: {},
        lambda w: {"title": "", "received_date": NOT_A_DATE, "response_deadline": NOT_A_DATE},
        editor=False,
    ),
    # -- on-demand editors in `Teema käik`, and their refusals ----------------
    Surface(
        "kaasamise parandus",
        "matters:update_engagement",
        lambda w: {"pk": w["matter"].pk, "engagement_id": w["engagement"].pk},
        lambda w: {
            "title": "",
            "url": NOT_A_URL,
            "occurred_on": NOT_A_DATE,
            "feedback_deadline": NOT_A_DATE,
            "revision": "",
        },
    ),
    Surface(
        "kaasamise lõpetamine",
        "matters:complete_engagement_feedback",
        lambda w: {"pk": w["matter"].pk, "engagement_id": w["engagement"].pk},
        lambda w: {"revision": ""},
        editor=False,
        # An empty file, which the upload field refuses before the service runs.
        files=_empty_upload(),
    ),
    Surface(
        "tagasiside ootus",
        "matters:open_engagement_wait",
        lambda w: {"pk": w["matter"].pk, "engagement_id": w["quiet_engagement"].pk},
        lambda w: {"feedback_deadline": NOT_A_DATE, "revision": ""},
        editor=False,
    ),
    Surface(
        "koja arvamuse parandus",
        "matters:update_sent_opinion",
        lambda w: {"pk": w["matter"].pk, "submission_id": w["sent_submission"].pk},
        lambda w: {"sent_on": NOT_A_DATE, "summary": "", "revision": ""},
    ),
    Surface(
        "sissekande parandus",
        "matters:edit_entry",
        lambda w: {"pk": w["matter"].pk, "entry_id": w["entry"].pk},
        lambda w: {"body": "", "revision": ""},
    ),
    Surface(
        "kodulehe-ülevaate lingi parandus",
        "matters:correct_website_overview",
        lambda w: {"pk": w["matter"].pk, "overview_id": w["published_overview"].pk},
        lambda w: {"url": NOT_A_URL, "published_on": NOT_A_DATE, "revision": ""},
    ),
    Surface(
        "välise seisukoha parandus",
        "matters:update_external_position",
        lambda w: {"pk": w["matter"].pk, "position_id": w["external_position"].pk},
        lambda w: {
            "organisation": "",
            "url": NOT_A_URL,
            "position_precision": "EXACT",
            "position_date": NOT_A_DATE,
            "revision": "",
        },
    ),
    Surface(
        "menetluse arengu parandus",
        "matters:update_development",
        lambda w: {"pk": w["matter"].pk, "development_id": w["development"].pk},
        lambda w: {
            "title": "",
            "occurred_on": NOT_A_DATE,
            "areng_precision": "EXACT",
            "revision": "",
        },
    ),
    Surface(
        "menetluse lingi parandus",
        "matters:correct_procedural_link",
        lambda w: {"pk": w["matter"].pk, "link_id": w["procedural_link"].pk},
        lambda w: {"kind": "", "url": NOT_A_URL, "label": "", "revision": ""},
        # POST only: the correction form is drawn inside the page itself.
        editor=False,
    ),
    Surface(
        "menetluse kulg",
        "matters:timeline_steps",
        _on_matter,
        # Filled below from the panel itself: every date box, in reverse order,
        # which `TimelineStepsForm.clean` refuses as a roadmap running backwards.
        None,
    ),
)


def _backwards_roadmap(html: str) -> dict[str, str]:
    """Every `…__date` box on the panel, dated so the phases run backwards."""

    class _Dates(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.names: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            name = dict(attrs).get("name") or ""
            if tag == "input" and name.endswith("__date"):
                self.names.append(name)

    parser = _Dates()
    parser.feed(html)
    today = timezone.localdate()
    return {
        name: format_estonian_date(today + timedelta(days=400 - 30 * index))
        for index, name in enumerate(parser.names)
    }


def _assert_resolves(response: Any, where: str) -> None:
    html = response.content.decode()
    dangling = dangling_descriptions(html)
    assert not dangling, (
        f"{where}: aria-describedby names ids this response does not hold exactly once: "
        f"{', '.join(dangling)}"
    )


# ---------------------------------------------------------------------------
# The tests
# ---------------------------------------------------------------------------


def test_the_teema_page_first_render_resolves_every_description(signed_in, world):
    """The control: the page these fragments live in, rendered whole."""
    response = signed_in.get(reverse("matters:matter_detail", kwargs=_on_matter(world)))
    assert response.status_code == 200
    _assert_resolves(response, "teema page")


@pytest.mark.parametrize("surface", [s for s in SURFACES if s.editor], ids=lambda s: s.slug)
def test_an_on_demand_editor_resolves_every_description(signed_in, world, surface):
    url = reverse(surface.route, kwargs=surface.kwargs(world))
    response = signed_in.get(url, **HTMX)
    assert response.status_code == 200, f"{surface.label}: GET answered {response.status_code}"
    _assert_resolves(response, f"{surface.label} (GET)")


@pytest.mark.parametrize("surface", SURFACES, ids=lambda s: s.slug)
def test_a_refusal_resolves_every_description(signed_in, world, surface):
    url = reverse(surface.route, kwargs=surface.kwargs(world))
    if surface.refusal is not None:
        payload = surface.refusal(world)
    else:
        payload = _backwards_roadmap(signed_in.get(url, **HTMX).content.decode())
        assert payload, f"{surface.label}: the panel offered no date box to refuse"
    if surface.files is not None:
        payload = {**payload, **surface.files()}
    response = signed_in.post(url, payload, **HTMX)
    assert response.status_code == 400, (
        f"{surface.label}: expected the form to refuse, got {response.status_code}"
    )
    if surface.describes_an_error:
        parser = _Ids()
        parser.feed(response.content.decode())
        assert any(token.endswith("_error") for token in parser.wanted), (
            f"{surface.label}: the refusal describes no control by its error, so this "
            f"case proves nothing about error ids"
        )
    _assert_resolves(response, f"{surface.label} (refused POST)")


# ---------------------------------------------------------------------------
# The same rule outside the Teema workspace: the fact forms, the quick-create
# institution form and the opinion metadata page render their own errors.
# ---------------------------------------------------------------------------


def test_a_refused_fact_form_resolves_every_description(signed_in, normal_matter):
    from django.urls import reverse

    response = signed_in.post(
        reverse("intelligence:add_important_date", kwargs={"matter_id": normal_matter.pk}),
        {"title": "", "precision": "QUARTER", "quarter": "2"},
    )
    assert response.status_code == 400
    assert "_error" in response.content.decode()
    _assert_resolves(response, "intelligence:add_important_date refusal")


def test_the_error_templates_outside_the_workspace_carry_their_ids():
    """Read off the templates: every rendered field error names the id Django
    points `aria-describedby` at, so a refusal on these pages cannot dangle."""
    from pathlib import Path

    from django.conf import settings

    for relative in (
        "intelligence/partials/field.html",
        "intelligence/partials/fact_fields.html",
        "organisations/partials/quick_create_form.html",
        "submissions/metadata.html",
    ):
        text = (Path(settings.BASE_DIR) / "templates" / relative).read_text(encoding="utf-8")
        for match in re.finditer(r'<span class="field__error"[^>]*>', text):
            assert "_error" in match.group(0), f"{relative}: {match.group(0)}"
