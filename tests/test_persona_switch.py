"""Selecting a persona: the page, the endpoint, and what neither may widen.

`tests/test_shared_gate.py` proves the mode works — a password opens the door, a
persona changes whose work is shown, every change is audited. This suite proves
the *boundary* around that, which is a different question and the one the
redesign turned into a security property rather than a presentation one:

**The endpoint is the boundary, not the list.** Everybody behind the shared door
can post to the switch endpoint. A row hidden in HTML is hidden from a reader
and from nobody else, so every exclusion is asserted against a crafted POST as
well as against the rendered page.

**Switching never widens what is visible.** The persona decides authorization;
authorization decides content. A switch that could carry a restricted title
across would make the shared password a way to read confidential member material
by trying names, which is exactly the shape docs/adr/0016 exists to refuse.

**Navigation follows the role, not a name.** Osakonna töö is the department
head's surface because of the role they hold, and Minu töö needs a "minu" to be
about (docs/adr/0034).
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.accounts import shared_gate
from app.accounts.enums import AuthMode, UserRole
from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.core.enums import Visibility
from tests import factories

PASSWORD = "seda-parooli-ei-ole-kusagil-mujal"  # noqa: S105

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def gate_mode(settings):
    settings.AUTH_MODE = AuthMode.SHARED_GATE
    settings.SHARED_GATE_PASSWORD = PASSWORD
    settings.SHARED_GATE_MAX_ATTEMPTS = 5
    settings.SHARED_GATE_LOCKOUT_SECONDS = 300
    settings.SHARED_GATE_MAX_LOCKOUT_SECONDS = 3600
    settings.DEV_LOGIN_ENABLED = False
    settings.LOGIN_URL = "accounts:choose_persona"
    return settings


@pytest.fixture
def behind_the_gate(client):
    response = client.post(reverse("accounts:shared_gate"), {"password": PASSWORD})
    assert response.status_code == 302
    return client


@pytest.fixture
def department():
    """One of each role, plus the two accounts that must never be offered."""
    return {
        "specialist": factories.UserFactory(
            role=UserRole.SPECIALIST, display_name="Ireen Näidisjurist"
        ),
        "colleague": factories.UserFactory(
            role=UserRole.SPECIALIST, display_name="Sandra Näidisjurist"
        ),
        "head": factories.DepartmentHeadFactory(display_name="Marko Näidisjuht"),
        "admin": factories.UserFactory(
            role=UserRole.ADMINISTRATOR, is_staff=True, display_name="Adminkonto Tehniline"
        ),
        "reader": factories.ReaderFactory(display_name="Lugejakonto Näidisvaataja"),
    }


def _act_as(client, value: str, **extra):
    return client.post(reverse("accounts:act_as"), {"user_id": value, **extra})


def _current(response):
    user = response.wsgi_request.user
    return user if user.is_authenticated else None


# -- the page ---------------------------------------------------------------


def test_the_page_offers_the_department_and_nobody_else(behind_the_gate, department):
    page = behind_the_gate.get(reverse("accounts:choose_persona")).content.decode()

    assert department["specialist"].get_short_name() in page
    assert department["head"].get_short_name() in page
    assert department["admin"].get_short_name() not in page
    assert department["reader"].get_short_name() not in page


def test_the_page_counts_the_people_it_actually_shows(behind_the_gate, department):
    """The section label is derived, not written.

    A hard-coded "4" is a number that is right until somebody joins, and a
    number nobody re-checks after that (Vali kasutaja brief 9).
    """
    page = behind_the_gate.get(reverse("accounts:choose_persona")).content.decode()

    assert "Kasutajad · 3" in page

    factories.UserFactory(role=UserRole.SPECIALIST, display_name="Ann Näidisjurist")
    page = behind_the_gate.get(reverse("accounts:choose_persona")).content.decode()
    assert "Kasutajad · 4" in page


def test_the_page_marks_the_selected_person_and_offers_the_rest(behind_the_gate, department):
    _act_as(behind_the_gate, str(department["specialist"].pk))
    page = behind_the_gate.get(reverse("accounts:choose_persona")).content.decode()

    assert "Praegu valitud" in page
    assert f'value="{department["specialist"].pk}"' not in page
    assert f'value="{department["colleague"].pk}"' in page


def test_the_page_says_who_is_selected_in_its_header(behind_the_gate, department):
    _act_as(behind_the_gate, str(department["head"].pk))
    page = behind_the_gate.get(reverse("accounts:choose_persona")).content.decode()

    assert "Praegu:" in page
    assert department["head"].get_short_name() in page


def test_the_page_renders_the_no_persona_state(behind_the_gate, department):
    page = behind_the_gate.get(reverse("accounts:choose_persona")).content.decode()

    assert "Ilma kasutajata" in page
    assert "Praegu valitud" in page


def test_the_page_carries_none_of_the_copy_the_redesign_removed(behind_the_gate, department):
    """Roles, counts, addresses and the amber warning are gone (brief 8, 9).

    Asserted rather than looked at, because each of them came back once already
    as "one more useful thing on the row".
    """
    page = behind_the_gate.get(reverse("accounts:choose_persona")).content.decode()

    assert "banner--restricted" not in page
    assert department["specialist"].upn not in page
    assert str(UserRole.SPECIALIST.label) not in page
    assert str(UserRole.DEPARTMENT_HEAD.label) not in page


def test_the_page_renders_inside_the_normal_shell(behind_the_gate, department):
    """Not a floating card. The bar, the search and the switcher are all there."""
    page = behind_the_gate.get(reverse("accounts:choose_persona")).content.decode()

    assert 'class="topbar"' in page
    assert 'id="global-search"' in page
    assert 'id="persona-pill"' in page


def test_the_page_never_mutates_through_a_link(behind_the_gate, department):
    """Every choice is a POST. A GET that changes session state is one a
    browser is free to prefetch, and one a crawler follows (brief 20)."""
    page = behind_the_gate.get(reverse("accounts:choose_persona")).content.decode()

    assert f'href="{reverse("accounts:act_as")}"' not in page
    assert page.count('method="post"') >= 3
    assert "csrfmiddlewaretoken" in page


# -- the endpoint: what it accepts -----------------------------------------


def test_a_specialist_may_be_selected(behind_the_gate, department):
    response = _act_as(behind_the_gate, str(department["specialist"].pk))

    assert response.status_code == 302
    assert _current(response) == department["specialist"]


def test_a_department_head_may_be_selected(behind_the_gate, department):
    response = _act_as(behind_the_gate, str(department["head"].pk))

    assert _current(response) == department["head"]


def test_a_valid_next_returns_to_the_page_somebody_was_reading(behind_the_gate, department):
    response = _act_as(behind_the_gate, str(department["specialist"].pk), next="/teemad/")

    assert response.status_code == 302
    assert response["Location"] == "/teemad/"


def test_an_external_next_is_refused_and_falls_back(behind_the_gate, department):
    response = _act_as(
        behind_the_gate,
        str(department["specialist"].pk),
        next="https://mujal.invalid/koguja",
    )

    assert "mujal.invalid" not in response["Location"]


def test_no_persona_also_returns_to_the_page_somebody_was_reading(behind_the_gate, department):
    _act_as(behind_the_gate, str(department["specialist"].pk))
    response = _act_as(behind_the_gate, "", next="/ulevaade/")

    assert response["Location"] == "/ulevaade/"
    assert _current(response) is None
    assert shared_gate.has_passed(response.wsgi_request)


def test_no_persona_refuses_an_external_next(behind_the_gate, department):
    _act_as(behind_the_gate, str(department["specialist"].pk))
    response = _act_as(behind_the_gate, "", next="https://mujal.invalid/koguja")

    assert "mujal.invalid" not in response["Location"]


# -- the endpoint: what it refuses -----------------------------------------


@pytest.mark.parametrize(
    "who",
    ["admin", "reader"],
)
def test_a_crafted_post_cannot_select_a_non_department_account(behind_the_gate, department, who):
    """The list is narrowed in the endpoint, not in the template.

    Everybody behind the shared door can reach this URL, so hiding a row in HTML
    hides it from a reader and from nobody else (brief 5).
    """
    _act_as(behind_the_gate, str(department["specialist"].pk))

    response = _act_as(behind_the_gate, str(department[who].pk))

    assert response.status_code == 302
    assert _current(response) == department["specialist"]


def test_a_crafted_post_cannot_select_a_superuser(behind_the_gate, department):
    owner = factories.UserFactory(role=UserRole.DEPARTMENT_HEAD, is_superuser=True, is_staff=True)
    _act_as(behind_the_gate, str(department["specialist"].pk))

    response = _act_as(behind_the_gate, str(owner.pk))

    assert _current(response) == department["specialist"]


def test_a_crafted_post_cannot_select_a_technical_staff_account(behind_the_gate, department):
    staff = factories.UserFactory(role=UserRole.SPECIALIST, is_staff=True)
    _act_as(behind_the_gate, str(department["specialist"].pk))

    response = _act_as(behind_the_gate, str(staff.pk))

    assert _current(response) == department["specialist"]


def test_a_crafted_post_cannot_select_an_inactive_person(behind_the_gate, department):
    former = factories.UserFactory(role=UserRole.SPECIALIST)
    type(former).objects.filter(pk=former.pk).update(is_active=False)
    _act_as(behind_the_gate, str(department["specialist"].pk))

    response = _act_as(behind_the_gate, str(former.pk))

    assert _current(response) == department["specialist"]


@pytest.mark.parametrize(
    "raw", ["not-a-uuid", "00000000-0000-0000-0000-000000000000", "1; DROP TABLE"]
)
def test_a_malformed_identifier_is_refused(behind_the_gate, department, raw):
    _act_as(behind_the_gate, str(department["specialist"].pk))

    response = _act_as(behind_the_gate, raw)

    assert _current(response) == department["specialist"]


def test_a_refused_switch_writes_no_persona_audit_event(behind_the_gate, department):
    """A refusal is not a persona change, and must not read as one later."""
    _act_as(behind_the_gate, str(department["specialist"].pk))
    before = SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.PERSONA_SELECTED
    ).count()

    _act_as(behind_the_gate, str(department["admin"].pk))

    assert (
        SecurityAuditEvent.objects.filter(event_type=SecurityEventType.PERSONA_SELECTED).count()
        == before
    )


def test_the_switch_endpoint_only_answers_a_post(behind_the_gate, department):
    response = behind_the_gate.get(reverse("accounts:act_as"))

    assert response.status_code == 405


# -- a persona that was selected before the rule narrowed ------------------


def test_a_session_already_acting_as_an_excluded_account_is_dropped(behind_the_gate, department):
    """Closing the endpoint does not close the sessions that beat it there.

    A persona chosen this morning under the old rule would otherwise go on
    being an administrator persona until the gate aged out twelve hours later,
    which is most of a working day (docs/adr/0034).

    `force_login` here stands in for exactly that: a session carrying an
    account the current rule refuses. It cannot be produced through `act_as`
    any more, which is the point.
    """
    behind_the_gate.force_login(department["admin"])

    response = behind_the_gate.get("/ulevaade/")

    assert response.status_code == 200
    assert not response.wsgi_request.user.is_authenticated
    # And the door stays open: nobody is asked for the password again because
    # of a change they did not make.
    assert shared_gate.has_passed(response.wsgi_request)


def test_dropping_an_ineligible_persona_is_recorded_with_its_reason(behind_the_gate, department):
    behind_the_gate.force_login(department["admin"])
    behind_the_gate.get("/ulevaade/")

    event = SecurityAuditEvent.objects.filter(event_type=SecurityEventType.PERSONA_SELECTED).latest(
        "occurred_at"
    )
    assert event.detail["previous_persona"] == str(department["admin"].pk)
    assert event.detail["chosen_persona"] is None
    assert event.detail["reason"] == "persona_no_longer_eligible"


def test_a_session_acting_as_a_candidate_is_left_alone(behind_the_gate, department):
    """The guard above must not log everybody out on every request."""
    _act_as(behind_the_gate, str(department["specialist"].pk))

    first = behind_the_gate.get("/ulevaade/")
    second = behind_the_gate.get("/teemad/")

    assert first.wsgi_request.user == department["specialist"]
    assert second.wsgi_request.user == department["specialist"]
    assert (
        SecurityAuditEvent.objects.filter(event_type=SecurityEventType.PERSONA_SELECTED).count()
        == 1
    )


def test_a_stale_technical_persona_cannot_reach_the_department_surface(behind_the_gate, department):
    """The reason the drop matters, rather than the fact that it happens.

    A technical account that also carries the department-head role is the one
    combination where the old rule and the new one disagree about something
    with consequences: under the old rule it was selectable, and it opens
    Osakonna töö. The session is refused the surface because the persona is
    gone, not because the route happened to check something else.
    """
    privileged = factories.UserFactory(
        role=UserRole.DEPARTMENT_HEAD, is_staff=True, display_name="Tehniline Juhtkonto"
    )
    behind_the_gate.force_login(privileged)

    response = behind_the_gate.get(reverse("matters:department_work"))

    assert response.status_code in (302, 404)
    assert not response.wsgi_request.user.is_authenticated


# -- audit ------------------------------------------------------------------


def test_a_successful_switch_uses_the_existing_audit_event(behind_the_gate, department):
    _act_as(behind_the_gate, str(department["specialist"].pk))
    _act_as(behind_the_gate, str(department["head"].pk))

    events = list(
        SecurityAuditEvent.objects.filter(event_type=SecurityEventType.PERSONA_SELECTED).order_by(
            "occurred_at"
        )
    )
    assert len(events) == 2
    assert events[1].detail["previous_persona"] == str(department["specialist"].pk)
    assert events[1].detail["chosen_persona"] == str(department["head"].pk)
    assert events[1].detail["authenticated_via"] == "SHARED_GATE"


def test_choosing_nobody_is_also_a_recorded_persona_change(behind_the_gate, department):
    _act_as(behind_the_gate, str(department["specialist"].pk))
    _act_as(behind_the_gate, "")

    event = (
        SecurityAuditEvent.objects.filter(event_type=SecurityEventType.PERSONA_SELECTED)
        .order_by("occurred_at")
        .last()
    )
    assert event.detail["previous_persona"] == str(department["specialist"].pk)
    assert event.detail["chosen_persona"] is None
    assert event.detail["authenticated_via"] == "SHARED_GATE"


# -- visibility never widens ------------------------------------------------


@pytest.fixture
def restricted_world(department):
    """One ordinary Matter and one restricted one, with a title worth hiding.

    The restricted Matter is owned by `colleague` — not by the head — on
    purpose. Owned by the head it would be visible to them twice over, by role
    *and* by participation, and a test that cannot tell those apart proves
    neither. As it stands the four cases the brief names are each distinct:
    `colleague` is entitled by participation, `head` by role, `specialist` by
    neither, and a session with no persona by nothing at all.
    """
    factories.MatterFactory(
        owner=department["specialist"],
        title="Avalik eelnõu kõigile",
        visibility=Visibility.NORMAL,
        is_open=True,
    )
    factories.MatterFactory(
        owner=department["colleague"],
        title="Konfidentsiaalne liikmete tagasiside",
        visibility=Visibility.RESTRICTED,
        is_open=True,
    )
    return department


def test_the_owning_specialist_persona_does_see_their_restricted_matter(
    behind_the_gate, restricted_world
):
    """Participation entitles, and switching to it is how you get there.

    Without this the negative cases below would pass just as well if the
    restricted Matter were invisible to everybody, which would prove that
    nothing works rather than that authorization does.
    """
    _act_as(behind_the_gate, str(restricted_world["colleague"].pk))

    page = behind_the_gate.get("/teemad/?olek=koik").content.decode()

    assert "Konfidentsiaalne liikmete tagasiside" in page


@pytest.mark.parametrize("path", ["/ulevaade/", "/teemad/?olek=koik"])
def test_a_specialist_persona_receives_the_restricted_title_too(
    behind_the_gate, restricted_world, path
):
    """This asserted `not in` until docs/adr/0042.

    A persona is answered by authorization, and a lawyer's authorization is now
    the department. `READER` cannot stand in here — `PERSONA_ROLES` is the two
    lawyer roles, so a reader is not offerable as a persona at all. The viewer
    who still may not see it is a session with no persona, and that is asserted
    directly below.
    """
    _act_as(behind_the_gate, str(restricted_world["specialist"].pk))

    page = behind_the_gate.get(path).content.decode()

    assert "Avalik eelnõu kõigile" in page
    assert "Konfidentsiaalne liikmete tagasiside" in page


def test_the_department_head_persona_sees_what_that_role_entitles(
    behind_the_gate, restricted_world
):
    """By role alone: the head owns none of this and sees it anyway."""
    _act_as(behind_the_gate, str(restricted_world["head"].pk))

    page = behind_the_gate.get("/teemad/?olek=koik").content.decode()

    assert "Konfidentsiaalne liikmete tagasiside" in page


def test_switching_away_from_the_head_takes_the_restricted_sight_with_it(
    behind_the_gate, restricted_world
):
    """The dangerous shape: read as the head, switch, keep the content.

    Nothing caches a rendered page or a scope across the switch, and this is the
    assertion that says so — in the order somebody would actually try it.
    """
    _act_as(behind_the_gate, str(restricted_world["head"].pk))
    assert "Konfidentsiaalne" in behind_the_gate.get("/teemad/?olek=koik").content.decode()

    # Away from every persona, since both lawyer roles now read the department
    # and switching between them could no longer show anything being dropped.
    _act_as(behind_the_gate, "")

    assert "Konfidentsiaalne" not in behind_the_gate.get("/teemad/?olek=koik").content.decode()


def test_no_persona_does_not_inherit_the_previous_persona_restricted_access(
    behind_the_gate, restricted_world
):
    _act_as(behind_the_gate, str(restricted_world["head"].pk))
    _act_as(behind_the_gate, "")

    page = behind_the_gate.get("/ulevaade/").content.decode()

    assert "Avalik eelnõu kõigile" in page
    assert "Konfidentsiaalne liikmete tagasiside" not in page


def test_the_shared_password_alone_grants_nothing_restricted(behind_the_gate, restricted_world):
    page = behind_the_gate.get("/ulevaade/").content.decode()

    assert "Konfidentsiaalne liikmete tagasiside" not in page


# -- navigation follows the role -------------------------------------------


def _nav(client) -> str:
    return client.get("/ulevaade/").content.decode()


def test_a_specialist_gets_minu_too_and_not_the_department_surface(behind_the_gate, department):
    _act_as(behind_the_gate, str(department["specialist"].pk))
    page = _nav(behind_the_gate)

    assert reverse("matters:my_work") in page
    assert reverse("matters:department_work") not in page


def test_a_department_head_gets_both(behind_the_gate, department):
    _act_as(behind_the_gate, str(department["head"].pk))
    page = _nav(behind_the_gate)

    assert reverse("matters:my_work") in page
    assert reverse("matters:department_work") in page


def test_no_persona_gets_neither_and_keeps_ulevaade(behind_the_gate, department):
    """Minu töö is not a surface without a "minu" (brief 23).

    Ülevaade is, and stays: it is the department's own dashboard and the reason
    somebody past the door has anything to read before choosing a name.
    """
    page = _nav(behind_the_gate)

    assert reverse("matters:my_work") not in page
    assert reverse("matters:department_work") not in page
    assert behind_the_gate.get("/ulevaade/").status_code == 200


def test_minu_too_without_a_persona_invents_nobody(behind_the_gate, department):
    """Direct navigation is refused the way this application always refuses it.

    `login_required` sends a reader with no persona to the persona page. What
    matters is the second assertion: no personal queue is rendered for somebody
    who has not said whose it is.
    """
    response = behind_the_gate.get(reverse("matters:my_work"))

    assert response.status_code == 302
    assert reverse("accounts:choose_persona") in response["Location"]


def test_the_department_surface_refuses_a_specialist_at_the_route(behind_the_gate, department):
    """Hiding the link is presentation; the 404 is the boundary."""
    _act_as(behind_the_gate, str(department["specialist"].pk))

    assert behind_the_gate.get(reverse("matters:department_work")).status_code == 404


def test_the_department_surface_refuses_a_session_with_no_persona(behind_the_gate, department):
    """Knowing the shared password is not being the department head."""
    assert behind_the_gate.get(reverse("matters:department_work")).status_code in (302, 404)


def test_an_administrator_cannot_reach_the_department_surface_by_becoming_one(
    behind_the_gate, department
):
    """The whole chain, end to end: an administrator cannot be selected, so
    there is no persona through which the technical account reads the
    department head's surface."""
    response = _act_as(behind_the_gate, str(department["admin"].pk))

    assert _current(response) is None
    assert behind_the_gate.get(reverse("matters:department_work")).status_code in (302, 404)


# -- the top-bar switcher ---------------------------------------------------


def test_the_pill_is_on_every_ordinary_page(behind_the_gate, department):
    _act_as(behind_the_gate, str(department["specialist"].pk))

    for path in ["/ulevaade/", "/teemad/", "/minu-too/"]:
        page = behind_the_gate.get(path).content.decode()
        assert 'id="persona-pill"' in page, path
        assert 'id="persona-menu"' in page, path


def test_the_popover_offers_the_same_population_as_the_page(behind_the_gate, department):
    _act_as(behind_the_gate, str(department["specialist"].pk))
    page = behind_the_gate.get("/ulevaade/").content.decode()

    assert department["colleague"].get_short_name() in page
    assert department["head"].get_short_name() in page
    assert department["admin"].get_short_name() not in page
    assert department["reader"].get_short_name() not in page


def test_the_popover_carries_the_current_page_as_its_return_target(behind_the_gate, department):
    _act_as(behind_the_gate, str(department["specialist"].pk))
    page = behind_the_gate.get("/teemad/?olek=koik").content.decode()

    assert 'name="next" value="/teemad/?olek=koik"' in page


def test_the_popover_names_the_selected_person_on_the_pill(behind_the_gate, department):
    _act_as(behind_the_gate, str(department["head"].pk))
    page = behind_the_gate.get("/ulevaade/").content.decode()

    assert department["head"].get_short_name() in page
    assert "personapill--none" not in page


def test_with_no_persona_the_pill_says_so(behind_the_gate, department):
    page = behind_the_gate.get("/ulevaade/").content.decode()

    assert "personapill--none" in page
    assert "Ilma kasutajata" in page


def test_the_popover_footer_is_the_one_place_the_caveat_survives(behind_the_gate, department):
    page = behind_the_gate.get("/ulevaade/").content.decode()

    assert "Valik ei ole autentimine" in page
    assert page.count("Valik ei ole autentimine") == 1


def test_the_pill_declares_the_popover_it_controls(behind_the_gate, department):
    page = behind_the_gate.get("/ulevaade/").content.decode()

    assert 'aria-controls="persona-menu"' in page
    assert 'aria-expanded="false"' in page
    assert 'aria-haspopup="true"' in page


def test_the_switcher_is_absent_where_there_is_no_persona_to_switch(client, settings):
    """No gate, no persona, no pill. The other two modes are not this one:
    `none` signs in synthetically and `cloudflare_access` names an individual,
    and neither has a list of people somebody may become."""
    settings.AUTH_MODE = AuthMode.NONE
    settings.LOGIN_URL = "accounts:dev_login"
    settings.DEV_LOGIN_ENABLED = True
    person = factories.UserFactory(role=UserRole.SPECIALIST, is_synthetic=True)
    client.force_login(person)

    page = client.get("/ulevaade/").content.decode()

    assert 'id="persona-pill"' not in page
    assert reverse("matters:my_work") in page
