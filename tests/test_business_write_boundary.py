"""Who may execute a native business write — every mutating route, every actor.

AUTH-002 was not one bug. The policy was right and had been right for a long
time: `may_write_business_content` says SPECIALIST and DEPARTMENT_HEAD, and says
that technical administration is not business authorship. What was missing was
that fifteen mutating HTTP routes never asked it. A READER could change a
Matter's owner, set a Järgmiseks, register incoming material, upload evidence
and mark a Chamber opinion sent — not through a loophole in the rule, but past
a door with no lock on it.

So the fix is a boundary, and the test for a boundary is not a handful of cases.
It is a matrix, and a way of knowing the matrix is complete.

**The matrix.** `WRITE_ROUTES` below names every class-A mutation with a payload
that would really change something and a probe that reports whether it did.
Every forbidden actor is fired at every one of them, and the assertion is not
only the status code — a refusal that returned 404 and wrote anyway would pass a
status assertion and fail the product. Each case checks the response, the state,
and the absence of a `ChangeEvent` claiming the thing happened.

**Completeness.** `test_every_mutating_route_is_classified` walks Django's own
URL resolver, finds every POST-capable route in the application, and fails if
one is neither in the matrix nor in an explicitly classified exemption. A new
mutating view added later cannot quietly arrive unguarded: it has to be either
gated or argued for, in this file, by whoever adds it.

What this file deliberately does not test: whether an actor may *see* a Matter
(`visible_to`, tests/test_authorization.py), and whether a person may *receive*
work (`app.accounts.selectors`, tests/test_work_assignment_eligibility.py).
Three separate questions; this one is only the first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import pytest
from django.urls import get_resolver, reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.documents.enums import DocumentRole
from app.matters.models import Matter
from app.related_materials.models import (
    MatterBackgroundMaterial,
    MatterRelation,
    RelatedSuggestionDismissal,
)
from app.submissions.enums import SubmissionStatus
from app.workflow.enums import ActionKind, ActionStatus, DateSemantics
from tests import factories

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# The actors
# ---------------------------------------------------------------------------


def _reader():
    return factories.ReaderFactory()


def _administrator():
    return factories.AdministratorFactory()


def _inactive_specialist():
    return factories.UserFactory(is_active=False)


def _staff_without_business_role():
    """`is_staff` is a Django flag, not a business role."""
    return factories.ReaderFactory(is_staff=True)


def _superuser_without_business_role():
    """Neither is `is_superuser`. The rule reads `role`, and only `role`."""
    return factories.AdministratorFactory(is_superuser=True, is_staff=True)


#: Everybody who must be refused, and the answer they must get.
#:
#: 404 is the business-write refusal. The inactive specialist is 302 and that is
#: not a weaker answer, it is an earlier one: Django's authentication backend
#: will not give an inactive account a session at all, so the request never
#: reaches the gate and is sent to sign in. Written out per actor rather than
#: accepted as "either code", because "refused somehow" is exactly the assertion
#: that would keep passing if one of these actors started slipping through.
FORBIDDEN: dict[str, tuple[Any, int]] = {
    "READER": (_reader, 404),
    "ADMINISTRATOR": (_administrator, 404),
    "staff without a business role": (_staff_without_business_role, 404),
    "superuser without a business role": (_superuser_without_business_role, 404),
    "inactive SPECIALIST": (_inactive_specialist, 302),
}

ALLOWED = {
    "SPECIALIST": lambda: factories.UserFactory(),
    "DEPARTMENT_HEAD": lambda: factories.DepartmentHeadFactory(),
}


# ---------------------------------------------------------------------------
# The routes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WriteRoute:
    """One native mutation, and how to tell whether it happened."""

    #: The URL name, which is also how the completeness guard matches it.
    name: str
    #: What a person would call this, for a readable failure.
    label: str
    #: Builds ``(kwargs, payload)`` from the fixture world.
    request: Any
    #: Returns a comparable snapshot of what this route would change.
    probe: Any
    #: Extra files, if the route takes an upload.
    files: Any = None
    #: Event types this route would write, so a refusal can be checked for them.
    events: tuple[str, ...] = field(default_factory=tuple)

    def url(self, world: dict) -> str:
        kwargs, _ = self.request(world)
        return reverse(self.name, kwargs=kwargs)

    def payload(self, world: dict) -> dict:
        _, data = self.request(world)
        return data


def _matter(world) -> Matter:
    return world["matter"]


WRITE_ROUTES: tuple[WriteRoute, ...] = (
    WriteRoute(
        name="matters:matter_create",
        label="Uus teema",
        request=lambda w: ({}, {"title": "Loata loodud teema"}),
        probe=lambda w: Matter.objects.filter(title="Loata loodud teema").count(),
    ),
    WriteRoute(
        name="matters:intake",
        label="Saabunud materjali registreerimine",
        request=lambda w: ({}, {"title": "Loata saabunud teema"}),
        probe=lambda w: Matter.objects.filter(title="Loata saabunud teema").count(),
    ),
    WriteRoute(
        name="matters:update_field",
        label="Vastutaja muutmine",
        request=lambda w: (
            {"pk": w["matter"].pk, "field": "owner"},
            {"owner": str(w["other_specialist"].pk)},
        ),
        probe=lambda w: (
            _matter(w).__class__.objects.values_list("owner_id", flat=True).get(pk=w["matter"].pk)
        ),
    ),
    WriteRoute(
        name="matters:update_field",
        label="Hetkeseisu muutmine",
        request=lambda w: (
            {"pk": w["matter"].pk, "field": "stage"},
            {"stage": str(w["stage"].pk)},
        ),
        probe=lambda w: Matter.objects.values_list("stage_id", flat=True).get(pk=w["matter"].pk),
    ),
    WriteRoute(
        name="matters:update_field",
        label="Nähtavuse muutmine",
        request=lambda w: (
            {"pk": w["matter"].pk, "field": "visibility"},
            {"visibility": Visibility.RESTRICTED},
        ),
        probe=lambda w: Matter.objects.values_list("visibility", flat=True).get(pk=w["matter"].pk),
    ),
    WriteRoute(
        name="matters:set_data_class",
        label="Andmeklassi muutmine",
        request=lambda w: ({"pk": w["matter"].pk}, {"data_class": "TEST"}),
        probe=lambda w: Matter.objects.values_list("data_class", flat=True).get(pk=w["matter"].pk),
    ),
    WriteRoute(
        name="matters:set_action",
        label="Järgmiseks määramine",
        request=lambda w: (
            {"pk": w["matter"].pk},
            {
                "text": "Loata määratud tegevus",
                "kind": ActionKind.DO,
                "target_date": (timezone.localdate() + timedelta(days=7)).strftime("%d.%m.%Y"),
            },
        ),
        probe=lambda w: w["matter"].next_actions.count(),
    ),
    WriteRoute(
        name="matters:complete_action",
        label="Järgmiseks lõpetamine",
        request=lambda w: ({"pk": w["matter"].pk, "action_id": w["action"].pk}, {}),
        probe=lambda w: (
            w["action"].__class__.objects.values_list("status", flat=True).get(pk=w["action"].pk)
        ),
    ),
    WriteRoute(
        name="matters:review_action",
        label="Järgmiseks ülevaatamine",
        request=lambda w: (
            {"pk": w["monitored"].pk, "action_id": w["review_action"].pk},
            {"next_review_date": (timezone.localdate() + timedelta(days=14)).strftime("%d.%m.%Y")},
        ),
        probe=lambda w: (
            w["review_action"]
            .__class__.objects.values_list("target_date", flat=True)
            .get(pk=w["review_action"].pk)
        ),
    ),
    WriteRoute(
        name="matters:assign_owner",
        label="Vastutaja määramine registrist",
        request=lambda w: ({"pk": w["unowned"].pk}, {"owner": str(w["author"].pk)}),
        probe=lambda w: (
            w["unowned"]
            .__class__.objects.values_list("owner_id", flat=True)
            .get(pk=w["unowned"].pk)
        ),
    ),
    WriteRoute(
        name="matters:complete_work_item",
        label="Järgmiseks lõpetamine Minu tööl",
        # No `pk`: the route is keyed on the action, and the Matter is resolved
        # from it through the same gate.
        request=lambda w: ({"action_id": w["review_action"].pk}, {}),
        probe=lambda w: (
            w["review_action"]
            .__class__.objects.values_list("status", flat=True)
            .get(pk=w["review_action"].pk)
        ),
    ),
    WriteRoute(
        name="matters:defer_action",
        label="Järgmiseks edasilükkamine",
        request=lambda w: ({"pk": w["matter"].pk, "action_id": w["action"].pk}, {"paevad": "7"}),
        probe=lambda w: (
            w["action"]
            .__class__.objects.values_list("target_date", flat=True)
            .get(pk=w["action"].pk)
        ),
    ),
    WriteRoute(
        name="matters:compose",
        label="Sissekande lisamine",
        request=lambda w: ({"pk": w["matter"].pk}, {"body": "<p>Loata sissekanne.</p>"}),
        probe=lambda w: w["matter"].entries.count(),
    ),
    WriteRoute(
        name="matters:save_note",
        label="Isikliku märkme salvestamine",
        request=lambda w: ({"pk": w["matter"].pk}, {"note-body": "Loata märge"}),
        probe=lambda w: w["matter"].personal_notes.count(),
    ),
    WriteRoute(
        name="matters:reopen",
        label="Teema taasavamine",
        request=lambda w: ({"pk": w["closed"].pk}, {}),
        probe=lambda w: Matter.objects.values_list("is_open", flat=True).get(pk=w["closed"].pk),
    ),
    WriteRoute(
        name="matters:close",
        label="Teema sulgemine",
        request=lambda w: ({"pk": w["matter"].pk}, {"disposition": "COMPLETED"}),
        probe=lambda w: Matter.objects.values_list("is_open", flat=True).get(pk=w["matter"].pk),
    ),
    WriteRoute(
        name="matters:update_position",
        label="Koja seisukoha salvestamine",
        request=lambda w: ({"pk": w["matter"].pk}, {"position_summary": "Loata seisukoht"}),
        probe=lambda w: Matter.objects.values_list("position_summary", flat=True).get(
            pk=w["matter"].pk
        ),
    ),
    WriteRoute(
        name="matters:update_summary",
        label="Lühikokkuvõtte salvestamine",
        request=lambda w: ({"pk": w["matter"].pk}, {"brief_summary": "Loata kokkuvõte"}),
        probe=lambda w: Matter.objects.values_list("brief_summary", flat=True).get(
            pk=w["matter"].pk
        ),
    ),
    WriteRoute(
        name="matters:add_engagement",
        label="Kaasamise lisamine",
        request=lambda w: (
            {"pk": w["matter"].pk},
            {"kind": "WEB_CALL", "title": "Loata kaasamine"},
        ),
        probe=lambda w: w["matter"].engagements.count(),
    ),
    WriteRoute(
        name="intelligence:add_important_date",
        label="Olulise tähtaja lisamine",
        request=lambda w: (
            {"matter_id": w["matter"].pk},
            {"title": "Loata tähtaeg", "precision": "YEAR", "year": "2030"},
        ),
        probe=lambda w: w["matter"].important_dates.count(),
    ),
    WriteRoute(
        name="intelligence:add_effective_date",
        label="Jõustumise lisamine",
        request=lambda w: (
            {"matter_id": w["matter"].pk},
            {"kind": "KNOWN_DATE", "precision": "YEAR", "year": "2030"},
        ),
        probe=lambda w: w["matter"].effective_dates.count(),
    ),
    WriteRoute(
        name="intelligence:add_work_victory",
        label="Töövõidu lisamine",
        request=lambda w: (
            {"matter_id": w["matter"].pk},
            {"title": "Loata töövõit", "precision": "YEAR", "year": "2030"},
        ),
        probe=lambda w: w["matter"].work_victories.count(),
    ),
    WriteRoute(
        name="submissions:create",
        label="Arvamuse loomine",
        request=lambda w: (
            {"matter_id": w["matter"].pk},
            {"title": "Loata arvamus", "kind": "FORMAL_OPINION"},
        ),
        probe=lambda w: w["matter"].submissions.count(),
    ),
    WriteRoute(
        name="submissions:mark_sent",
        label="Arvamuse saadetuks märkimine",
        request=lambda w: ({"pk": w["submission"].pk}, {"channel": "EIS"}),
        probe=lambda w: (
            w["submission"]
            .__class__.objects.values_list("status", flat=True)
            .get(pk=w["submission"].pk)
        ),
    ),
    WriteRoute(
        name="submissions:withdraw",
        label="Arvamuse tagasivõtmine",
        request=lambda w: ({"pk": w["sent_submission"].pk}, {"reason": "Loata"}),
        probe=lambda w: (
            w["sent_submission"]
            .__class__.objects.values_list("status", flat=True)
            .get(pk=w["sent_submission"].pk)
        ),
    ),
    WriteRoute(
        name="documents:upload_evidence",
        label="Tõendi üleslaadimine",
        request=lambda w: (
            {"matter_id": w["matter"].pk},
            {"role": DocumentRole.INCOMING_AUTHORITY},
        ),
        files=lambda: {"upload": _pdf("loata-toend.pdf")},
        probe=lambda w: w["matter"].documents.count(),
    ),
    WriteRoute(
        name="matters:add_working_document",
        label="Töödokumendi lisamine",
        request=lambda w: (
            {"pk": w["matter"].pk},
            {"title": "Loata töödokument", "web_url": "https://example.invalid/a"},
        ),
        # A working document is a `Document` with a role, not its own table.
        probe=lambda w: w["matter"].documents.filter(role=DocumentRole.WORKING_DOCUMENT).count(),
    ),
    WriteRoute(
        name="organisations:quick_create",
        label="Asutuse kiirlisamine",
        request=lambda w: ({}, {"name": "Loata Ministeerium"}),
        probe=lambda w: (
            w["organisation"].__class__.objects.filter(name="Loata Ministeerium").count()
        ),
    ),
    # Seotud materjalid (docs/adr/0061). Six decisions, one gate.
    WriteRoute(
        name="related_materials:link",
        label="Seotud teema lisamine",
        request=lambda w: ({"pk": w["matter"].pk}, {"teema": str(w["monitored"].pk)}),
        probe=lambda w: MatterRelation.objects.count(),
        events=(ChangeEventType.MATTER_RELATION_ADDED,),
    ),
    WriteRoute(
        name="related_materials:unlink",
        label="Seotud teema eemaldamine",
        request=lambda w: ({"pk": w["matter"].pk}, {"teema": str(w["monitored"].pk)}),
        probe=lambda w: MatterRelation.objects.count(),
        events=(ChangeEventType.MATTER_RELATION_REMOVED,),
    ),
    WriteRoute(
        name="related_materials:add_background",
        label="Taustmaterjali lisamine",
        request=lambda w: (
            {"pk": w["monitored"].pk},
            {"liik": "arvamus", "kandidaat": str(w["sent_submission"].pk)},
        ),
        probe=lambda w: MatterBackgroundMaterial.objects.count(),
        events=(ChangeEventType.BACKGROUND_MATERIAL_ADDED,),
    ),
    WriteRoute(
        name="related_materials:remove_background",
        label="Taustmaterjali eemaldamine",
        request=lambda w: (
            {"pk": w["monitored"].pk},
            {"liik": "arvamus", "kandidaat": str(w["sent_submission"].pk)},
        ),
        probe=lambda w: MatterBackgroundMaterial.objects.count(),
        events=(ChangeEventType.BACKGROUND_MATERIAL_REMOVED,),
    ),
    WriteRoute(
        name="related_materials:dismiss",
        label="Soovituse peitmine",
        request=lambda w: (
            {"pk": w["matter"].pk},
            {"liik": "teema", "kandidaat": str(w["monitored"].pk)},
        ),
        probe=lambda w: RelatedSuggestionDismissal.objects.count(),
    ),
    WriteRoute(
        name="related_materials:restore",
        label="Soovituse taastamine",
        request=lambda w: (
            {"pk": w["matter"].pk},
            {"liik": "teema", "kandidaat": str(w["monitored"].pk)},
        ),
        probe=lambda w: RelatedSuggestionDismissal.objects.count(),
    ),
)


def _pdf(filename: str):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(filename, b"%PDF-1.4\nsyntethic", content_type="application/pdf")


# ---------------------------------------------------------------------------
# The world every route is fired at
# ---------------------------------------------------------------------------


@pytest.fixture
def world(db):
    from app.documents.services import add_evidence_version
    from app.matters.services import close_matter, create_matter

    author = factories.UserFactory()
    # Explicit references in a range the allocator will not reach: `create_matter`
    # below allocates from 2026_1 upwards and the factory's own sequence starts
    # there too, so the two collide on the uniqueness constraint.
    matter = factories.MatterFactory(
        owner=author, title="Tavaline avatud teema", reference_year=2099, reference_number=901
    )
    other = factories.UserFactory()

    # Dated, because the database refuses a DO/DEADLINE without one — the same
    # constraint that keeps a "deadline" with no date out of the register.
    action = factories.NextActionFactory(
        matter=matter,
        responsible=author,
        target_date=timezone.localdate() + timedelta(days=3),
    )
    # Its own Matter: only one action may be open on a Matter at a time, and
    # both of these have to be open for their routes to be reachable at all.
    monitored = factories.MatterFactory(
        owner=author, title="Jälgitav teema", reference_year=2099, reference_number=902
    )
    review_action = factories.NextActionFactory(
        matter=monitored,
        responsible=author,
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=timezone.localdate(),
    )

    closed = create_matter(title="Suletud teema", actor=author, owner=author)
    close_matter(matter=closed, actor=author, disposition="COMPLETED")

    submission = factories.SubmissionFactory(matter=matter, title="Mustand")
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

    # Its own Matter, and unowned: the register's «Määra ▾» exists only on a row
    # that has nobody, and firing at an owned one would prove nothing.
    unowned = factories.MatterFactory(
        owner=None, title="Vastutajata teema", reference_year=2099, reference_number=911
    )

    return {
        "matter": matter,
        "unowned": unowned,
        "closed": closed,
        "author": author,
        "other_specialist": other,
        "action": action,
        "monitored": monitored,
        "review_action": review_action,
        "submission": submission,
        "sent_submission": sent_submission,
        "organisation": factories.OrganisationFactory(),
        "stage": factories.StageFactory(),
    }


def _post(client, route: WriteRoute, world: dict):
    data = dict(route.payload(world))
    if route.files is not None:
        data.update(route.files())
    return client.post(route.url(world), data)


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", WRITE_ROUTES, ids=lambda r: f"{r.name}:{r.label}")
@pytest.mark.parametrize("actor_name", sorted(FORBIDDEN))
def test_a_forbidden_actor_cannot_execute_a_business_write(client, world, route, actor_name):
    """Refused, and nothing moved.

    The state assertion is the one that matters. A route that answered 404 and
    wrote anyway would satisfy a status check and fail the product, and that is
    exactly the shape a decorator applied to the wrong line would produce.
    """
    factory, expected = FORBIDDEN[actor_name]
    actor = factory()
    client.force_login(actor)
    before = route.probe(world)

    response = _post(client, route, world)

    assert response.status_code == expected, (
        f"{actor_name} was not refused by {route.name} ({route.label}): {response.status_code}"
    )
    assert route.probe(world) == before, f"{actor_name} changed state through {route.name}"


@pytest.mark.parametrize("route", WRITE_ROUTES, ids=lambda r: f"{r.name}:{r.label}")
@pytest.mark.parametrize("actor_name", sorted(FORBIDDEN))
def test_a_refused_write_records_no_business_history(client, world, route, actor_name):
    """No `ChangeEvent` may claim a refused action happened.

    An audit trail that records work nobody was allowed to do is worse than one
    that records nothing: somebody reading it later cannot tell the difference
    between a change and an attempt.
    """
    factory, _ = FORBIDDEN[actor_name]
    actor = factory()
    client.force_login(actor)
    before = ChangeEvent.objects.count()

    _post(client, route, world)

    assert ChangeEvent.objects.count() == before
    assert not ChangeEvent.objects.filter(actor=actor).exists()


@pytest.mark.parametrize("route", WRITE_ROUTES, ids=lambda r: f"{r.name}:{r.label}")
@pytest.mark.parametrize("actor_name", sorted(ALLOWED))
def test_an_authorized_actor_is_not_refused_by_the_write_gate(client, world, route, actor_name):
    """The other half: the boundary must not have closed the product.

    One route per test, on a fresh world. Fired in a loop against a shared one
    they poison each other — restricting a Matter's visibility, or closing it,
    changes what the *next* route is allowed to touch, and the 404 that follows
    is `visible_to` doing its job rather than the write gate doing the wrong
    one. Two different refusals wearing the same status code is precisely the
    confusion this file exists to keep apart (§4).

    Asserted as "not refused by *this* gate" rather than "succeeded", because a
    route may still answer 400 for a payload this matrix did not tailor to it.
    404 is the one answer that would mean the actor was turned away for who they
    are, and no business writer may ever see it here.
    """
    actor = ALLOWED[actor_name]()
    client.force_login(actor)

    response = _post(client, route, world)

    assert response.status_code != 404, f"{actor_name} was refused by {route.name} ({route.label})"


def test_an_anonymous_write_is_sent_to_sign_in_rather_than_hidden(client, world):
    """`login_required` stays outermost.

    A 404 here would be wrong in the other direction: somebody whose session has
    simply expired should be asked to sign in, not told the application has no
    such page.
    """
    route = WRITE_ROUTES[2]

    response = _post(client, route, world)

    assert response.status_code == 302
    assert "/konto/" in response["Location"]


def test_a_non_writer_learns_nothing_from_the_verb_they_use(client, world):
    """The same 404 for GET as for POST.

    `@business_write_required` sits outside `@require_http_methods` so a reader
    probing a POST-only route gets the same answer as for anything else that
    does not exist. A 405 would confirm the endpoint.
    """
    client.force_login(_reader())
    url = reverse("matters:set_action", kwargs={"pk": world["matter"].pk})

    assert client.get(url).status_code == 404
    assert client.post(url, {}).status_code == 404


# ---------------------------------------------------------------------------
# Break-glass reads more; it does not write
# ---------------------------------------------------------------------------


def test_break_glass_does_not_turn_a_technical_actor_into_an_author(client, world):
    """Seeing restricted material and authoring business data are different.

    A break-glass grant exists so somebody can *read* what an incident requires.
    If it also handed out authorship, the audited emergency read would become an
    unaudited promotion (master specification 5.2).
    """
    from app.accounts.models import BreakGlassGrant

    administrator = _administrator()
    restricted = factories.MatterFactory(
        owner=world["author"],
        visibility=Visibility.RESTRICTED,
        title="Piiratud teema",
        reference_year=2099,
        reference_number=903,
    )
    BreakGlassGrant.objects.create(
        user=administrator,
        granted_by=world["author"],
        reason="Intsidendi uurimine",
        starts_at=timezone.now() - timedelta(minutes=5),
        expires_at=timezone.now() + timedelta(hours=2),
    )
    client.force_login(administrator)

    # The grant is real: the restricted Matter is now readable.
    assert (
        client.get(reverse("matters:matter_detail", kwargs={"pk": restricted.pk})).status_code
        == 200
    )

    # And it authorizes nothing at all on the write side.
    response = client.post(
        reverse("matters:update_field", kwargs={"pk": restricted.pk, "field": "owner"}),
        {"owner": str(administrator.pk)},
    )

    assert response.status_code == 404
    restricted.refresh_from_db()
    assert restricted.owner_id == world["author"].pk


# ---------------------------------------------------------------------------
# Completeness — no unexplained POST write
# ---------------------------------------------------------------------------

#: Mutating routes deliberately outside the business-write boundary, each with
#: the reason it is outside. Anything not here and not in `WRITE_ROUTES` fails
#: the guard below, which is what stops a future mutation arriving unguarded.
CLASSIFIED_ELSEWHERE: dict[str, str] = {
    # C — a person's own private data, keyed on `request.user` and reachable by
    # nobody else. Not business content and not subject to the business-write
    # role: a READER may keep their own notes, and there is no signature
    # anywhere in the call chain that could write somebody else's row
    # (app/matters/person_work.py, tests/test_person_workspace.py).
    "matters:save_scratchpad": "C: the signed-in person's own notepad",
    # C — the recipient's own read receipt on «Uus asi». Not business content:
    # nothing about the Matter changes, and the only row it can touch is one
    # whose `recipient` is `request.user` — the lookup carries that, so there is
    # no id in the URL that reaches somebody else's queue. A READER who has been
    # handed a file may acknowledge having seen it, exactly as they may keep
    # their own notes; the Matter behind it still goes through
    # `get_visible_matter` (app/matters/views.py, docs/adr/0051,
    # tests/test_new_assignment_notices.py).
    "matters:open_assignment_notice": "C: the recipient's own read receipt",
    # D — authentication and session control. Not business content, and two of
    # them must work for somebody who has no role at all yet.
    "accounts:dev_login": "D: synthetic sign-in, development only",
    "accounts:sign_out": "D: ends a session",
    "accounts:shared_gate": "D: shared-gate password",
    "accounts:act_as": "D: persona selection; its own eligibility rules",
    # B — stricter than business write, and already stricter. DEPARTMENT_HEAD is
    # a subset of the business writers, so `may_review_work_victory` implies the
    # business-write rule and cannot be satisfied by a READER or ADMINISTRATOR.
    "intelligence:confirm_work_victory": "B: department head only",
    "intelligence:reject_work_victory": "B: department head only",
    # E — migration and reconciliation tooling, governed by their own explicit
    # administrator/reviewer checks rather than by business authorship.
    "legacy_import:opinion_archive_link": "E: archive reconciliation, reviewer-gated",
    "legacy_import:review_decide": "E: historical review queue, administrator-gated",
    "legacy_import:opinion_decide": "E: opinion review queue, administrator-gated",
    # A — covered by the matrix through a sibling route on the same view or the
    # same guard, and awkward to fire blind (they need a specific child object
    # or an upload bound to one).
    "matters:matter_edit": "A: gated; exercised by tests/test_matters.py",
    "matters:update_engagement": "A: gated; sibling of matters:add_engagement",
    "intelligence:edit_important_date": "A: gated; sibling of add_important_date",
    "intelligence:cancel_important_date": "A: gated; sibling of add_important_date",
    "intelligence:edit_effective_date": "A: gated; sibling of add_effective_date",
    "intelligence:cancel_effective_date": "A: gated; sibling of add_effective_date",
    "intelligence:edit_work_victory": "A: gated; sibling of add_work_victory",
    "submissions:attach_evidence": "A: gated; needs a bound evidence version",
    "documents:add_version": "A: gated; needs an existing document",
}


def _post_capable_routes() -> dict[str, str]:
    """Every application URL whose view can be reached with POST."""
    import inspect

    found: dict[str, str] = {}

    def walk(resolver, namespace=""):
        for pattern in resolver.url_patterns:
            if hasattr(pattern, "url_patterns"):
                walk(pattern, pattern.namespace or namespace)
                continue
            callback = pattern.callback
            module = getattr(inspect.unwrap(callback), "__module__", "")
            if not module.startswith("app."):
                continue
            try:
                source = inspect.getsource(inspect.unwrap(callback))
            except (OSError, TypeError):  # pragma: no cover - defensive
                continue
            if not re.search(r'"POST"|\brequest\.POST\b|\brequest\.FILES\b', source):
                continue
            if pattern.name:
                found[f"{namespace}:{pattern.name}" if namespace else pattern.name] = module
        return found

    return walk(get_resolver())


def test_every_mutating_route_is_classified():
    """No POST-capable application route may be unaccounted for.

    This is the assertion that makes the rest of the file a boundary rather than
    a list. A new mutating view is either in `WRITE_ROUTES` — and therefore
    fired at by every forbidden actor — or named in `CLASSIFIED_ELSEWHERE` with
    the reason it sits outside. There is no third option that passes.
    """
    covered = {route.name for route in WRITE_ROUTES} | set(CLASSIFIED_ELSEWHERE)

    unexplained = {
        name: module for name, module in _post_capable_routes().items() if name not in covered
    }

    assert not unexplained, (
        "these routes accept POST and are neither in the business-write matrix "
        f"nor classified out of it: {unexplained}"
    )


def test_the_classification_list_does_not_rot():
    """A name in the exemption list that no longer routes anywhere is a lie."""
    live = set(_post_capable_routes())

    stale = sorted(name for name in CLASSIFIED_ELSEWHERE if name not in live)

    assert not stale, f"classified routes that no longer exist: {stale}"


# ---------------------------------------------------------------------------
# The reader is still a reader
# ---------------------------------------------------------------------------


def test_a_reader_can_still_read_everything_they_could_before(client, world):
    """AUTH-002 must not be closed by locking the role out of the application.

    READER is a real role with a real job: read the register, open a file,
    search, look at the dashboard. If this PR had made those 404 too it would
    have "fixed" the finding by deleting the feature.
    """
    client.force_login(_reader())

    for name, kwargs in (
        ("matters:matter_list", {}),
        ("matters:department", {}),
        ("matters:matter_detail", {"pk": world["matter"].pk}),
        ("matters:matter_documents", {"pk": world["matter"].pk}),
        ("matters:matter_position", {"pk": world["matter"].pk}),
        ("search:search", {}),
        ("intelligence:important_dates", {}),
        ("reporting:overview", {}),
    ):
        response = client.get(reverse(name, kwargs=kwargs))
        assert response.status_code == 200, f"{name} refused a reader: {response.status_code}"


def test_an_administrator_keeps_its_technical_reach(client, world):
    """ADMINISTRATOR is narrowed on authorship, not on administration.

    The role exists to run the system. This change says only that running the
    system is not the same as writing the department's legal-policy record.
    """
    administrator = _administrator()
    client.force_login(administrator)

    assert client.get(reverse("matters:matter_list")).status_code == 200
    assert administrator.is_staff is True


# ---------------------------------------------------------------------------
# The stricter action keeps its stricter rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("actor_factory", "expected"),
    [
        (lambda: factories.DepartmentHeadFactory(), 302),
        (lambda: factories.UserFactory(), 403),
        (_administrator, 403),
        (_reader, 403),
    ],
    ids=["DEPARTMENT_HEAD", "SPECIALIST", "ADMINISTRATOR", "READER"],
)
def test_confirming_a_work_victory_stays_department_head_only(
    client, world, actor_factory, expected
):
    """Class B, unchanged.

    `may_review_work_victory` already refuses everybody outside the head role,
    and because DEPARTMENT_HEAD is a subset of the business writers it cannot be
    satisfied by an actor this PR would otherwise have had to stop. It keeps its
    403: a specialist who may write and may see the record is being told about a
    decision boundary inside their own work, which is a different message from
    "this route does not exist for you".
    """
    record = factories.WorkVictoryFactory(matter=world["matter"])
    client.force_login(actor_factory())

    response = client.post(
        reverse(
            "intelligence:confirm_work_victory",
            kwargs={"matter_id": world["matter"].pk, "pk": record.pk},
        )
    )

    assert response.status_code == expected
    record.refresh_from_db()
    if expected != 302:
        assert record.status != "CONFIRMED"


def test_a_specialist_may_still_do_ordinary_next_action_work(client, world):
    """The positive case for the family AUTH-002 was reproduced against."""
    client.force_login(world["author"])

    response = client.post(
        reverse("matters:set_action", kwargs={"pk": world["matter"].pk}),
        {
            "text": "Koosta arvamus",
            "kind": ActionKind.DO,
            "target_date": (timezone.localdate() + timedelta(days=7)).strftime("%d.%m.%Y"),
        },
    )

    assert response.status_code == 200
    assert (
        world["matter"]
        .next_actions.filter(text="Koosta arvamus", status=ActionStatus.OPEN)
        .exists()
    )
