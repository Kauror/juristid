"""A record a reader may not know exists must not move anything they can see.

The question here is not whether an unauthorized reader can *open* a restricted
Matter or a restricted child record. Every id-addressed route already answers
that with the same 404 it gives a random UUID. The stronger question is whether
the reader can tell that the record **exists** — through a count, a page
number, a result order, an empty state that stops being empty, a badge, a
suggestion list, a statistics figure, a workload total.

So the whole file is one comparison, run over many surfaces:

    world A: a register the reader may see, in full
    world B: exactly the same register, plus one thing they may not know about

Every page the reader can reach is captured in A, the hidden record is added,
and the same page is captured in B. Only the CSRF token is normalized — it is
per-request by construction. Everything else is compared as rendered, because
the leaks this class of defect produces leave no string behind: a number moves,
a row shifts onto the next page, «vasteid ei leitud» becomes a result list.

Two hidden worlds are tried, because they exercise different boundaries:

* a RESTRICTED Matter — the Matter-level scope, ``Matter.objects.visible_to``;
* a NORMAL Matter carrying RESTRICTED children of every kind — the child-level
  scope, ``child_visibility_q``, which AUTH-003 and the 2026-09-09 audit both
  found broken somewhere the Matter-level scope was fine.

The adversary is READER and ADMINISTRATOR: the two roles that may sign in and
read NORMAL work but never RESTRICTED work. A SPECIALIST who is not on the file
is *authorized* by ADR 0042 and is not an adversary here.

The control test comes first. Capturing the same world twice must give the same
bytes, or a difference between A and B says nothing.
"""

from __future__ import annotations

import difflib
import re
import uuid
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.accounts.enums import UserRole
from app.core.enums import Visibility
from app.documents.models import Document
from app.intelligence.enums import FactStatus
from app.matters.enums import EngagementKind
from app.matters.models import MatterEngagement
from app.submissions.enums import SubmissionStatus
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics
from tests import factories
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

PDF = "application/pdf"

#: Tokens that can only come from the hidden record. Each search below asks for
#: one of them; none may return anything in either world.
HIDDEN_TITLE = "SALAJANE-TEEMA-9913"
HIDDEN_PHRASE = "SALAJANE-FRAAS-2261"
HIDDEN_ORGANISATION = "Salaamet SALA-ORG-7731"
HIDDEN_ACTION = "SALAJANE-SAMM-4471"
HIDDEN_REFERENCE_NUMBER = 9913

#: `Kaasamine` joined this list on 2026-09-12, when the record grew two external
#: provider addresses. It had been missing from `_children` — the visibility
#: itself held, but nothing here was proving it, and a child that now carries a
#: campaign host and a one-time token is exactly the kind whose *existence* is
#: worth an oracle. The hosts are distinctive because `link_search_terms` copies
#: them into `alias_text`, where they become matchable tokens; the tokens are
#: distinctive because nothing may ever index them.
HIDDEN_ENGAGEMENT = "SALAJANE-KAASAMINE-8842"
HIDDEN_SMAILY_HOST = "salakiri-7731.example"
HIDDEN_ALCHEMER_HOST = "salakysitlus-7731.example"
HIDDEN_SMAILY_URL = f"https://{HIDDEN_SMAILY_HOST}/c/8842?token=SALA-VOTI-ESIMENE"
HIDDEN_ALCHEMER_URL = f"https://{HIDDEN_ALCHEMER_HOST}/s3/8842?k=SALA-VOTI-TEINE"

#: A word the visible and the hidden records share, so a hidden row that was
#: counted or ranked would move the visible rows around it.
SHARED_WORD = "ühissõna"

#: Exactly one page of the register at its default page size, so a hidden row
#: that was counted would open a second page.
PAGE_SIZE = 12


# ---------------------------------------------------------------------------
# The visible world
# ---------------------------------------------------------------------------


@pytest.fixture
def world(db, capture_evidence, extract):
    """One page of ordinary open work, with every kind of child on the first file.

    The hidden organisation exists in this world too. The Organisation catalogue
    is shared reference data by design (2026-09-09 audit, P-1), so the body
    being *listed* is not what is under test — the body being *used* is.
    """
    today = timezone.localdate()
    owner = factories.UserFactory(display_name="Peeter Paas")
    colleague = factories.UserFactory(display_name="Kati Kask")
    ministry = factories.OrganisationFactory(name="Näidisministeerium")
    hidden_org = factories.OrganisationFactory(name=HIDDEN_ORGANISATION)

    matters = []
    for index in range(PAGE_SIZE):
        matter = factories.MatterFactory(
            title=f"Avalik teema {index + 1} {SHARED_WORD}",
            owner=owner if index % 2 == 0 else colleague,
            visibility=Visibility.NORMAL,
            addressee_organisation=ministry,
            reference_year=2099,
            reference_number=100 + index,
            received_date=today - timedelta(days=index),
            response_deadline=today + timedelta(days=index),
        )
        matter.source_organisations.set([ministry])
        matters.append(matter)

    first = matters[0]
    factories.EntryFactory(matter=first, author=owner, body=f"<p>Avalik märge {SHARED_WORD}.</p>")
    factories.NextActionFactory(
        matter=first,
        text="Avalik samm",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today + timedelta(days=3),
        date_precision=DatePrecision.EXACT,
        status=ActionStatus.OPEN,
        responsible=owner,
    )
    version = capture_evidence(
        first,
        corpus.text_pdf([f"Avalik arvamus {SHARED_WORD}"]),
        "avalik.pdf",
        PDF,
        title="Avalik arvamus",
    )
    extract(version)
    submission = factories.SubmissionFactory(
        matter=first,
        title="Avalik arvamus",
        status=SubmissionStatus.SENT,
        sent_at=timezone.now(),
        final_version=version,
    )
    submission.recipients.set([ministry])
    factories.ImportantDateFactory(matter=first, title="Avalik tähtaeg", date_value=today)
    factories.EffectiveDateFactory(matter=first, date_value=today + timedelta(days=30))
    factories.WorkVictoryFactory(matter=first, title="Avalik töövõit")
    # Undated on purpose: an engagement with a date becomes the Matter's
    # `Viimane tegevus`, and this one is here to make the chronology's link row
    # render in *both* worlds rather than to move any ordering.
    MatterEngagement.objects.create(
        matter=first,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title=f"Avalik kaasamine {SHARED_WORD}",
        smaily_url="https://avalik-kiri.example/c/1",
        alchemer_url="https://avalik-kysitlus.example/s3/1",
        created_by=owner,
    )

    return {
        "today": today,
        "owner": owner,
        "colleague": colleague,
        "ministry": ministry,
        "hidden_org": hidden_org,
        "matters": matters,
        "first": first,
        "host": matters[1],
        "document": version.document,
        "reader": factories.UserFactory(role=UserRole.READER, display_name="Lugeja Luts"),
        "administrator": factories.UserFactory(
            role=UserRole.ADMINISTRATOR, display_name="Haldur Hallik"
        ),
    }


# ---------------------------------------------------------------------------
# The hidden records
# ---------------------------------------------------------------------------


def _children(matter, world, capture_evidence, extract, *, override: str):
    """Every kind of child record, on ``matter``, with one visibility override."""
    today = world["today"]
    factories.EntryFactory(
        matter=matter,
        author=world["owner"],
        body=f"<p>{HIDDEN_PHRASE} {SHARED_WORD}.</p>",
        visibility_override=override,
    )
    factories.NextActionFactory(
        matter=matter,
        text=HIDDEN_ACTION,
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today,
        date_precision=DatePrecision.EXACT,
        status=ActionStatus.OPEN,
        responsible=world["owner"],
        visibility_override=override,
    )
    version = capture_evidence(
        matter,
        corpus.text_pdf([f"{HIDDEN_PHRASE} {SHARED_WORD}"]),
        "salajane.pdf",
        PDF,
        title=HIDDEN_PHRASE,
        visibility_override=override,
    )
    extract(version)
    submission = factories.SubmissionFactory(
        matter=matter,
        title=HIDDEN_PHRASE,
        status=SubmissionStatus.SENT,
        sent_at=timezone.now(),
        final_version=version,
        visibility_override=override,
    )
    submission.recipients.set([world["hidden_org"]])
    factories.ImportantDateFactory(
        matter=matter,
        title=HIDDEN_PHRASE,
        date_value=today,
        status=FactStatus.ACTIVE,
        visibility_override=override,
    )
    factories.EffectiveDateFactory(
        matter=matter, date_value=today + timedelta(days=10), visibility_override=override
    )
    factories.WorkVictoryFactory(matter=matter, title=HIDDEN_PHRASE, visibility_override=override)
    # Dated, unlike the visible one: an engagement with a date is a candidate
    # for `Viimane tegevus`, so a hidden one that was counted would move a
    # register row's date and its ordering. Both provider addresses carry a host
    # that appears nowhere else and a token that must reach no index.
    MatterEngagement.objects.create(
        matter=matter,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title=f"{HIDDEN_ENGAGEMENT} {SHARED_WORD}",
        url=f"https://{HIDDEN_SMAILY_HOST}/avalik/8842",
        smaily_url=HIDDEN_SMAILY_URL,
        alchemer_url=HIDDEN_ALCHEMER_URL,
        occurred_on=today,
        response_count=17,
        created_by=world["owner"],
        visibility_override=override,
    )
    return {"submission": submission, "document": version.document, "version": version}


def add_restricted_matter(world, capture_evidence, extract):
    """A RESTRICTED Matter the reader is not on, naming a body nobody else names."""
    today = world["today"]
    matter = factories.MatterFactory(
        title=f"{HIDDEN_TITLE} {SHARED_WORD}",
        owner=world["owner"],
        visibility=Visibility.RESTRICTED,
        addressee_organisation=world["hidden_org"],
        reference_year=2099,
        reference_number=HIDDEN_REFERENCE_NUMBER,
        received_date=today,
        response_deadline=today,
    )
    matter.source_organisations.set([world["hidden_org"]])
    matter.collaborators.set([world["colleague"]])
    return {"matter": matter, **_children(matter, world, capture_evidence, extract, override="")}


def add_restricted_children(world, capture_evidence, extract):
    """Every kind of child, RESTRICTED, under a visible Matter with no open step.

    The second file, not the first: a Matter may carry only one open action,
    and the first already has its visible one.
    """
    matter = world["host"]
    return {
        "matter": matter,
        **_children(matter, world, capture_evidence, extract, override=Visibility.RESTRICTED.value),
    }


HIDDEN_WORLDS = {
    "restricted-matter": add_restricted_matter,
    "restricted-children": add_restricted_children,
}


# ---------------------------------------------------------------------------
# The surfaces
# ---------------------------------------------------------------------------


def surfaces(world) -> list[str]:
    """Every GET a READER or ADMINISTRATOR can reach that reads the register."""
    today = world["today"]
    first = world["first"]
    host = world["host"]
    owner = world["owner"]
    hidden_org = world["hidden_org"]
    register = reverse("matters:matter_list")
    search = reverse("search:search")
    suggest = reverse("search:suggestions")
    urls = [
        reverse("core:home"),
        reverse("matters:department"),
        reverse("matters:my_work"),
        reverse("matters:inbox"),
        reverse("matters:person_work", kwargs={"pk": owner.pk}),
        reverse("matters:person_work", kwargs={"pk": world["colleague"].pk}),
        reverse("submissions:sent"),
        reverse("submissions:archive"),
        reverse("submissions:embedded_block") + f"?q={SHARED_WORD}",
        reverse("intelligence:important_dates"),
        reverse("intelligence:effective_dates"),
        reverse("intelligence:work_victories"),
        # The register, in every shape the URL can give it.
        register,
        register + "?olek=avatud",
        register + "?olek=avatud&leht=2",
        register + f"?kaupa={PAGE_SIZE}",
        register + f"?kaupa={PAGE_SIZE}&leht=2",
        register + "?kaupa=koik",
        register + "?jarjestus=tahtaeg",
        register + f"?q={SHARED_WORD}",
        register + f"?q={HIDDEN_TITLE}",
        register + f"?q={HIDDEN_PHRASE}",
        register + f"?q={HIDDEN_ENGAGEMENT}",
        register + f"?q={HIDDEN_SMAILY_HOST}",
        register + f"?vastutaja={owner.pk}",
        register + f"?asutus={hidden_org.pk}",
        register + f"?saatja={hidden_org.pk}",
        register + f"?adressaat={hidden_org.pk}",
        register + "?arvamus=saadetud",
        register + "?arvamus=koostamisel",
        register + "?tegevus=puudub",
        register + "?tegevus=on",
        register + "?toovoit=on",
        register + "?joustumine=on",
        register + "?too=hilinenud",
        register + "?too=tahtaeg-nadalal",
        register + "?too=tahtaeg-30",
        register + "?too=sekkumist",
        register + "?too=muutusteta-30",
        register
        + f"?too=tahtaeg-vahemik&too_alates={today.isoformat()}"
        + f"&too_kuni={(today + timedelta(days=30)).isoformat()}",
        register + f"?too=tahtaeg-30&too_vastutaja={owner.pk}",
        reverse("matters:organisation_choices") + "?vali=asutus&asutus_otsing=Sala",
        reverse("matters:organisation_choices") + "?vali=saatja&saatja_otsing=Sala",
        # The visible Matter and its child surfaces.
        reverse("matters:matter_detail", kwargs={"pk": first.pk}),
        reverse("matters:timeline_page", kwargs={"pk": first.pk}),
        reverse("matters:timeline_page", kwargs={"pk": first.pk}) + "?nihe=0",
        reverse("matters:matter_documents", kwargs={"pk": first.pk}),
        reverse("matters:matter_documents", kwargs={"pk": first.pk}) + f"?otsi={HIDDEN_PHRASE}",
        reverse("matters:matter_position", kwargs={"pk": first.pk}),
        reverse("related_materials:section", kwargs={"pk": first.pk}),
        reverse("documents:document_detail", kwargs={"pk": world["document"].pk}),
        # The Matter the restricted children are filed under.
        reverse("matters:matter_detail", kwargs={"pk": host.pk}),
        reverse("matters:timeline_page", kwargs={"pk": host.pk}),
        reverse("matters:matter_documents", kwargs={"pk": host.pk}),
        reverse("matters:matter_position", kwargs={"pk": host.pk}),
        reverse("related_materials:section", kwargs={"pk": host.pk}),
        # Statistika and its exports.
        reverse("reporting:overview"),
        reverse("reporting:matters"),
        reverse("reporting:activity"),
        reverse("reporting:historical"),
        reverse("reporting:quality"),
        reverse("reporting:submissions"),
        reverse("reporting:materials"),
        reverse("reporting:definitions"),
        *(
            reverse("reporting:export", kwargs={"slug": slug})
            for slug in ("teemad", "arvamused", "materjalid", "andmekvaliteet")
        ),
    ]
    for term in (
        HIDDEN_TITLE,
        HIDDEN_PHRASE,
        HIDDEN_ACTION,
        HIDDEN_ENGAGEMENT,
        HIDDEN_SMAILY_HOST,
        HIDDEN_ALCHEMER_HOST,
        "salakiri",
        "salakysitlus",
        "SALA-VOTI-ESIMENE",
        "SALA-VOTI-TEINE",
        "Salaamet",
        "SALA-ORG-7731",
        f"2099_{HIDDEN_REFERENCE_NUMBER}",
        SHARED_WORD,
        "Avalik",
        "salajane.pdf",
    ):
        urls.append(f"{search}?q={term}")
        urls.append(f"{suggest}?q={term}")
    return urls


def _normalize(body: bytes) -> str:
    text = body.decode()
    return re.sub(r'value="[A-Za-z0-9]{32,}"', "CSRF", text)


def _capture(client: Client, urls: list[str]) -> dict[str, tuple[int, str, str]]:
    captured = {}
    for url in urls:
        response = client.get(url, HTTP_HX_REQUEST="true" if "/seotud/" in url else "")
        raw = (
            b"".join(response.streaming_content)
            if getattr(response, "streaming", False)
            else response.content
        )
        captured[url] = (
            response.status_code,
            response.headers.get("Location", ""),
            _normalize(raw),
        )
    return captured


def _differences(before, after) -> str:
    lines = []
    for url, (status_a, location_a, body_a) in before.items():
        status_b, location_b, body_b = after[url]
        if (status_a, location_a, body_a) == (status_b, location_b, body_b):
            continue
        lines.append(f"\n=== {url}: {status_a} -> {status_b} {location_a or ''} {location_b or ''}")
        diff = difflib.unified_diff(body_a.splitlines(), body_b.splitlines(), lineterm="", n=1)
        lines.extend(list(diff)[2:40])
    return "\n".join(lines)


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


# ---------------------------------------------------------------------------
# Control: the harness itself is deterministic
# ---------------------------------------------------------------------------


def test_capturing_the_same_world_twice_is_identical(world):
    client = _client(world["reader"])
    urls = surfaces(world)

    first = _capture(client, urls)
    second = _capture(client, urls)

    assert _differences(first, second) == ""


# ---------------------------------------------------------------------------
# The oracle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("persona", ["reader", "administrator"])
@pytest.mark.parametrize("hidden", list(HIDDEN_WORLDS))
def test_a_hidden_record_changes_nothing_the_reader_receives(
    world, capture_evidence, extract, persona, hidden
):
    client = _client(world[persona])
    urls = surfaces(world)

    before = _capture(client, urls)
    HIDDEN_WORLDS[hidden](world, capture_evidence, extract)
    after = _capture(client, urls)

    for url, (_status, _location, body) in after.items():
        for token in (HIDDEN_TITLE, HIDDEN_PHRASE, HIDDEN_ACTION):
            if token in url:
                # The reader's own query, echoed back into the search box.
                continue
            assert token not in body, f"{token} reached {persona} on {url}"

    assert _differences(before, after) == ""


@pytest.mark.parametrize("hidden", list(HIDDEN_WORLDS))
def test_the_participant_still_sees_the_hidden_work(world, capture_evidence, extract, hidden):
    """The other half of the contract: scoping must not blind the file's owner."""
    client = _client(world["owner"])
    urls = surfaces(world)

    before = _capture(client, urls)
    HIDDEN_WORLDS[hidden](world, capture_evidence, extract)
    after = _capture(client, urls)

    assert _differences(before, after) != ""
    search = reverse("search:search")
    assert HIDDEN_PHRASE in after[f"{search}?q={HIDDEN_PHRASE}"][2]


# ---------------------------------------------------------------------------
# Crafted requests: a hidden id must look exactly like a nonexistent one
# ---------------------------------------------------------------------------


def _crafted_routes(hidden) -> list[tuple[str, dict[str, object]]]:
    matter = hidden["matter"]
    return [
        ("matters:matter_detail", {"pk": matter.pk}),
        ("matters:timeline_page", {"pk": matter.pk}),
        ("matters:matter_documents", {"pk": matter.pk}),
        ("matters:matter_position", {"pk": matter.pk}),
        ("matters:matter_edit", {"pk": matter.pk}),
        ("related_materials:section", {"pk": matter.pk}),
        ("related_materials:picker", {"pk": matter.pk}),
        ("documents:document_detail", {"pk": hidden["document"].pk}),
        ("documents:download", {"pk": hidden["version"].pk}),
        ("documents:open", {"pk": hidden["version"].pk}),
        ("documents:thumbnail", {"pk": hidden["version"].pk}),
        ("documents:add_version", {"pk": hidden["document"].pk}),
        ("submissions:attach_evidence", {"pk": hidden["submission"].pk}),
        ("submissions:mark_sent", {"pk": hidden["submission"].pk}),
    ]


@pytest.mark.parametrize("persona", ["reader", "administrator"])
@pytest.mark.parametrize("hidden", list(HIDDEN_WORLDS))
def test_a_hidden_id_is_indistinguishable_from_a_random_one(
    world, capture_evidence, extract, persona, hidden
):
    client = _client(world[persona])
    created = HIDDEN_WORLDS[hidden](world, capture_evidence, extract)
    routes = _crafted_routes(created)
    if hidden == "restricted-children":
        # The Matter itself is visible here; only its children are hidden.
        routes = [route for route in routes if route[0].startswith(("documents:", "submissions:"))]

    for name, kwargs in routes:
        real = client.get(reverse(name, kwargs=kwargs), HTTP_HX_REQUEST="true")
        fake = client.get(
            reverse(name, kwargs={key: uuid.uuid4() for key in kwargs}), HTTP_HX_REQUEST="true"
        )
        real_body = real.content if not getattr(real, "streaming", False) else b""
        fake_body = fake.content if not getattr(fake, "streaming", False) else b""
        assert real.status_code == fake.status_code, (
            f"{name}: {real.status_code} vs {fake.status_code}"
        )
        assert dict(real.headers) == dict(fake.headers), f"{name}: headers differ"
        assert _normalize(real_body) == _normalize(fake_body), f"{name}: bodies differ"
        for token in (HIDDEN_TITLE, HIDDEN_PHRASE, HIDDEN_ACTION, "salajane.pdf"):
            assert token.encode() not in real_body, f"{token} reached {persona} via {name}"

    # The hidden document is invisible at its source, under either Matter.
    assert Document.objects.filter(matter=created["matter"]).visible_to(world[persona]).count() == 0
