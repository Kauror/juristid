"""Where the two halves of this release meet.

PR #64 rebuilt `Uus teema`. PR #63 made every number on `Ülevaade` open exactly
the list it counted. Each proved itself thoroughly and separately, and the seam
between them was proved by neither: a Matter *created through the redesigned
form* has to join the populations the redesigned drill-downs count, and the
count-equals-list promise has to survive its arrival.

That seam is the only thing this file is about. It deliberately does not
re-assert what either round already owns — the create form's field semantics are
`tests/test_uus_teema_redesign.py`'s, and the drill-down harness is
`tests/test_overview_drilldowns.py`'s, which is why the harness is imported here
rather than rebuilt. What is new is the order of events: create first, then ask
the overview whether it still adds up.

The last three tests are boundary re-checks at the seam, not new coverage.
Creation through this form must still produce evidence rather than a canonical
opinion, must still keep a private note private, and must still refuse a reader
— and it is a release integrating two rounds that is the moment to ask, because
each of those is a rule one round could satisfy while the other quietly
undermined it.
"""

from __future__ import annotations

import io
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.accounts.enums import UserRole
from app.documents.models import DocumentVersion
from app.matters import overview as ov
from app.matters.models import Matter, MatterPersonalNote
from app.submissions.models import Submission
from app.taxonomy.models import PolicyArea
from app.workflow.enums import ActionKind
from app.workflow.models import NextAction
from tests import factories
from tests.test_overview_drilldowns import list_claims, register_claims, shown_total

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")

TITLE = "Integratsioonikatse eelnõu"
NOTE = "Ainult minu märkus, mitte kellegi teise oma."
SUMMARY = "Muudaks teavitamiskohustust väikeettevõtetele."


def upload(name: str, content: bytes, mime: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, io.BytesIO(content).read(), content_type=mime)


@pytest.fixture
def stage():
    return factories.StageFactory(label_et="Kooskõlastusringil")


@pytest.fixture
def created(signed_in, specialist, stage):
    """One Matter, made the way the redesigned page makes one.

    Through the view rather than through `create_matter`, because the claim
    being tested is about the form: a Matter built by calling the service
    directly would prove the service and skip every decision the redesign made
    about what the page posts.
    """
    area = PolicyArea.objects.filter(is_active=True).order_by("sort_order").first()
    tomorrow = timezone.localdate() + timedelta(days=1)
    response = signed_in.post(
        CREATE,
        {
            "title": TITLE,
            "brief_summary": SUMMARY,
            "notes": NOTE,
            "owner": specialist.pk,
            "policy_areas": [area.pk],
            "stage": stage.pk,
            "files": upload("kaaskiri.pdf", b"%PDF-1.4 integratsioon", "application/pdf"),
            "next-text": "Loen eelnou labi",
            "next-kind": ActionKind.DO,
            "next-date_semantics": "",
            "next-target_date": tomorrow.strftime("%d.%m.%Y"),
        },
    )
    assert response.status_code in (302, 303), response.status_code
    return Matter.objects.get(title=TITLE)


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------


def test_the_form_produces_the_whole_record_in_one_go(created, specialist, stage):
    """The canonical services, and no second implementation of any of them."""
    assert created.owner == specialist
    assert created.brief_summary == SUMMARY
    assert created.stage == stage
    assert created.policy_areas.exists()

    assert DocumentVersion.objects.filter(document__matter=created).count() == 1
    assert NextAction.objects.filter(matter=created).exists()
    assert MatterPersonalNote.objects.filter(matter=created, author=specialist).exists()


def test_the_new_matter_reaches_the_overview_it_was_created_from(created, specialist):
    """Created on one page, counted on the other. The whole point of the seam."""
    page = ov.build_overview(specialist, scope=ov.SCOPE_DEPARTMENT)
    open_figure = next(figure for figure in page.figures if figure.key == "open")
    assert open_figure.count >= 1

    titles = set(
        Matter.objects.visible_to(specialist)
        .filter(closed_at__isnull=True)
        .values_list("title", flat=True)
    )
    assert TITLE in titles


@pytest.mark.parametrize("scope", [ov.SCOPE_DEPARTMENT, ov.SCOPE_TEAM, ov.SCOPE_AREAS])
def test_count_and_list_still_agree_once_a_matter_arrives_this_way(
    signed_in, specialist, created, scope
):
    """#63's promise, re-asked with #64's Matter in the population.

    Every number Ülevaade prints is walked and its destination opened through
    the real view, exactly as the drill-down suite does — the difference is only
    that one of the rows being counted was posted by the redesigned form. A
    figure that counted this Matter and a list that lost it would be invisible
    to either round on its own.
    """
    page = ov.build_overview(specialist, scope=scope)
    claims = list_claims(page)
    assert claims, f"{scope} rendered no list destinations at all"

    for claim in claims:
        assert shown_total(signed_in, claim.url) == claim.count, claim


def test_the_new_matter_is_reachable_from_at_least_one_number(
    signed_in, specialist, created, stage
):
    """Not merely counted somewhere: findable by clicking something."""
    page = ov.build_overview(specialist, scope=ov.SCOPE_DEPARTMENT)
    reached = set()
    for claim in register_claims(page):
        response = signed_in.get(claim.url)
        reached.update(matter.title for matter in response.context["page"].object_list)
    assert TITLE in reached


# ---------------------------------------------------------------------------
# Boundaries, re-checked where the two rounds meet
# ---------------------------------------------------------------------------


def test_an_uploaded_file_is_evidence_and_never_a_canonical_opinion(created):
    """A file attached while creating is a Document, and the register of Koda's
    outbound opinions stays a thing somebody does deliberately, later."""
    assert DocumentVersion.objects.filter(document__matter=created).exists()
    assert not Submission.objects.filter(matter=created).exists()


def test_the_private_note_stays_out_of_the_shared_record(
    client, created, other_specialist, specialist
):
    note = MatterPersonalNote.objects.get(matter=created)
    assert note.author == specialist

    client.force_login(other_specialist)
    body = client.get(reverse("matters:matter_detail", kwargs={"pk": created.pk})).content.decode()
    assert NOTE not in body


def test_a_reader_still_cannot_create_a_matter(client):
    reader = factories.UserFactory(role=UserRole.READER)
    client.force_login(reader)

    before = Matter.objects.count()
    response = client.post(CREATE, {"title": "Lugeja integratsiooniteema"})

    assert response.status_code in (403, 302)
    assert Matter.objects.count() == before
    assert not Matter.objects.filter(title="Lugeja integratsiooniteema").exists()
