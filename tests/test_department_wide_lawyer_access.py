"""The contract docs/adr/0042 records, asserted where it is enforced.

Every other authorization test in this suite says what somebody may *not* see.
This one exists because the decision that made those tests need rewriting was a
positive one, and a positive decision with no test of its own is a decision the
next refactor quietly reverses. If a future change narrows lawyer access back to
owners and collaborators, the suite should go red here rather than pass with a
smaller product.

Two lawyers, no relationship between them: A owns a RESTRICTED Matter, B is not
its owner, not a collaborator, and holds no break-glass grant.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.urls import reverse

from app.accounts.enums import UserRole
from app.core.authorization import (
    DEPARTMENT_VIEWER,
    may_review_work_victory,
    may_write_business_content,
    scope_for_user,
)
from app.core.enums import Visibility
from app.documents.enums import DocumentRole
from app.documents.models import Document, DocumentVersion
from app.documents.services import add_evidence_version, create_document
from app.matters.models import Entry, Matter
from app.matters.services import add_entry
from app.submissions.models import Submission
from app.submissions.services import create_submission, select_final_evidence
from app.workflow.models import NextAction
from app.workflow.services import set_next_action_for_new_work
from tests import factories

pytestmark = pytest.mark.django_db

RESTRICTED_TITLE = "Salajane liikmete tagasiside"
RESTRICTED_ENTRY = "Piiratud sissekanne"
RESTRICTED_FILE = "salajane-lisa.pdf"


def _world(owner, *, visibility: str):
    """One Matter of the given visibility, carrying one of everything."""
    matter = factories.MatterFactory(owner=owner, visibility=visibility, title=RESTRICTED_TITLE)
    entry = add_entry(matter=matter, body=RESTRICTED_ENTRY, author=owner)
    submission = create_submission(matter=matter, title="Piiratud arvamus", actor=owner)
    document = create_document(
        matter=matter,
        title="Piiratud tõend",
        role=DocumentRole.KODA_SUBMISSION_FINAL,
        created_by=owner,
        visibility_override="",
    )
    version = add_evidence_version(
        document=document,
        content=b"%PDF-1.4 salajane",
        original_filename=RESTRICTED_FILE,
        mime_type="application/pdf",
        uploaded_by=owner,
    )
    select_final_evidence(submission=submission, version=version, actor=owner)
    set_next_action_for_new_work(
        matter=matter,
        text="Piiratud samm",
        actor=owner,
        target_date=date.today() + timedelta(days=7),
    )
    return matter, entry, submission, document, version


@pytest.fixture
def restricted(specialist):
    return _world(specialist, visibility=Visibility.RESTRICTED)


# ---------------------------------------------------------------------------
# What a lawyer may read
# ---------------------------------------------------------------------------


def test_a_lawyer_reads_every_record_under_a_colleagues_restricted_matter(
    restricted, other_specialist
):
    """The whole matrix in one assertion set, because it moves together.

    These all derive from one scope. Splitting them into a test each would
    suggest they could disagree, and the point of the central chokepoint is that
    they cannot.
    """
    matter, entry, submission, document, version = restricted

    assert scope_for_user(other_specialist).sees_all_restricted
    assert Matter.objects.visible_to(other_specialist).filter(pk=matter.pk).exists()
    assert Entry.objects.visible_to(other_specialist).filter(pk=entry.pk).exists()
    assert Submission.objects.visible_to(other_specialist).filter(pk=submission.pk).exists()
    assert Document.objects.visible_to(other_specialist).filter(pk=document.pk).exists()
    assert NextAction.objects.visible_to(other_specialist).filter(matter=matter).exists()
    # Versions are reached through their Document, which is where the rule lives;
    # the download route is asserted directly in the next test.
    assert DocumentVersion.objects.filter(
        pk=version.pk, document__in=Document.objects.visible_to(other_specialist)
    ).exists()


def test_a_lawyer_opens_the_page_and_the_file(client, restricted, other_specialist):
    """Rendered surfaces and the direct object, which are different questions.

    A link the page does not draw is not a denial, and a file the page links to
    is not proof the download route agrees. Both are asked.
    """
    matter, _entry, _submission, _document, version = restricted
    client.force_login(other_specialist)

    detail = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert detail.status_code == 200
    body = detail.content.decode()
    assert RESTRICTED_TITLE in body
    assert RESTRICTED_ENTRY in body

    download = client.get(reverse("documents:download", kwargs={"pk": version.pk}))
    assert download.status_code == 200

    register = client.get(reverse("matters:matter_list")).content.decode()
    assert RESTRICTED_TITLE in register


def test_the_department_head_still_reads_it_too(restricted, department_head):
    """Unchanged by this decision, and asserted so a future narrowing is visible."""
    matter = restricted[0]
    assert Matter.objects.visible_to(department_head).filter(pk=matter.pk).exists()


# ---------------------------------------------------------------------------
# Parity: visibility is not what decides a lawyer's reach
# ---------------------------------------------------------------------------


def test_a_normal_and_a_restricted_matter_read_alike_for_a_lawyer(specialist, other_specialist):
    """The parity the decision actually asserts.

    Not "restricted is readable" in isolation — that could be true while some
    surface still treated it as a lesser record. The claim is that the two
    visibilities are indistinguishable from a lawyer's side.
    """
    normal = _world(specialist, visibility=Visibility.NORMAL)[0]
    restricted_matter = _world(specialist, visibility=Visibility.RESTRICTED)[0]

    for matter in (normal, restricted_matter):
        assert Matter.objects.visible_to(other_specialist).filter(pk=matter.pk).exists()
        assert Entry.objects.visible_to(other_specialist).filter(matter=matter).exists()
        assert Submission.objects.visible_to(other_specialist).filter(matter=matter).exists()
        assert Document.objects.visible_to(other_specialist).filter(matter=matter).exists()


def test_a_lawyer_writes_a_colleagues_restricted_matter_as_they_would_a_normal_one(
    client, specialist, other_specialist
):
    """AUTH-002's missing positive, and the parity that goes with it.

    `may_write_business_content` was always role-based, so what changed is the
    set of Matters a lawyer can reach rather than the rule applied to them. The
    two visibilities must therefore accept the same edit from the same person.
    """
    normal = _world(specialist, visibility=Visibility.NORMAL)[0]
    restricted_matter = _world(specialist, visibility=Visibility.RESTRICTED)[0]
    client.force_login(other_specialist)

    assert may_write_business_content(other_specialist)

    cases = ((normal, "Kolleeg nimetas avaliku"), (restricted_matter, "Kolleeg nimetas piiratu"))
    for matter, new_title in cases:
        opened = client.get(reverse("matters:matter_edit", kwargs={"pk": matter.pk}))
        assert opened.status_code == 200
        saved = client.post(
            reverse("matters:matter_edit", kwargs={"pk": matter.pk}), {"title": new_title}
        )
        assert saved.status_code in (200, 302), saved.status_code
        matter.refresh_from_db()
        assert matter.title == new_title


def test_a_lawyer_adds_an_entry_to_a_colleagues_restricted_matter(restricted, other_specialist):
    """One service path beyond the edit form, so the policy is not a view accident."""
    matter = restricted[0]
    entry = add_entry(matter=matter, body="Kolleegi märkus", author=other_specialist)
    assert Entry.objects.filter(pk=entry.pk, matter=matter).exists()


# ---------------------------------------------------------------------------
# Reach is not capability, and the boundary still exists
# ---------------------------------------------------------------------------


def test_reading_everything_does_not_make_a_specialist_the_department_head(
    specialist, department_head
):
    """The distinction the widening must not blur."""
    assert not may_review_work_victory(specialist)
    assert may_review_work_victory(department_head)


@pytest.mark.parametrize(
    "who",
    ["reader", "administrator", "department_viewer", "anonymous", "inactive", "unknown_role"],
)
def test_the_boundary_still_refuses_everybody_it_refused_before(
    restricted, who, reader, administrator
):
    """One place naming every actor the decision deliberately did not widen.

    Each of these has its own tests elsewhere; this is the summary that fails if
    somebody widens the role set again without thinking about who else is in it.
    """
    matter = restricted[0]
    actor = {
        "reader": reader,
        "administrator": administrator,
        "department_viewer": DEPARTMENT_VIEWER,
        "anonymous": None,
        "inactive": factories.UserFactory(is_active=False),
        "unknown_role": factories.UserFactory(role="TUNDMATU"),
    }[who]

    assert not Matter.objects.visible_to(actor).filter(pk=matter.pk).exists()
    assert not Document.objects.visible_to(actor).filter(matter=matter).exists()
    assert not may_write_business_content(actor)


def test_the_widened_role_set_is_exactly_the_two_lawyer_roles():
    """A guard on the decision itself, in the one place it is written down."""
    from app.core.authorization import ROLES_WITH_RESTRICTED_ACCESS

    assert ROLES_WITH_RESTRICTED_ACCESS == frozenset(
        {UserRole.SPECIALIST.value, UserRole.DEPARTMENT_HEAD.value}
    )
