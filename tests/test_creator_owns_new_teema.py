"""`Uus teema` opens with the person filing it as Vastutaja (owner's R1, 2026-10-09).

Server-side, on the unbound form: the chip is rendered chosen, so the page says
so the moment it opens and works without scripting (docs/adr/0150 §1). What is
pinned:

* the default is **whoever is signed in** — two different people each see
  themselves, never a fixed person;
* a colleague may be chosen instead, by somebody holding `work.assign`, and that
  choice survives a refused save;
* without `work.assign` the only owner on offer is oneself, and a crafted
  colleague is refused, exactly as before;
* somebody who is not a department worker (an administrator) is not made the
  owner of what they file.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from app.accounts.enums import UserRole
from app.matters.forms import MatterCreateForm
from app.matters.models import Matter
from tests import factories

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")


def checked_owner(body: str) -> list[str]:
    """The `owner` values the page renders chosen."""
    return re.findall(
        r'<input[^>]*name="owner"[^>]*value="([^"]+)"[^>]*checked', body
    ) + re.findall(r'<input[^>]*checked[^>]*name="owner"[^>]*value="([^"]+)"', body)


def test_two_different_people_each_open_the_page_as_its_owner(client):
    first = factories.UserFactory()
    second = factories.UserFactory()

    for person in (first, second):
        client.force_login(person)
        body = client.get(CREATE).content.decode()
        assert checked_owner(body) == [str(person.pk)], person
        client.logout()


def test_the_default_is_on_the_form_not_in_a_script(specialist):
    form = MatterCreateForm(viewer=specialist)
    assert form.initial["owner"] == specialist.pk
    assert str(form["owner"].value()) == str(specialist.pk)


def test_saving_as_opened_makes_the_creator_the_owner(signed_in, specialist):
    body = signed_in.get(CREATE).content.decode()
    owner = checked_owner(body)[0]

    signed_in.post(CREATE, {"title": "Avati ja salvestati", "owner": owner})

    assert Matter.objects.get(title="Avati ja salvestati").owner == specialist


def test_a_colleague_may_be_chosen_instead(signed_in, specialist):
    colleague = factories.UserFactory()

    signed_in.post(CREATE, {"title": "Kolleegile", "owner": str(colleague.pk)})

    assert Matter.objects.get(title="Kolleegile").owner == colleague


def test_a_refused_save_keeps_the_colleague_chosen(signed_in, specialist):
    colleague = factories.UserFactory()

    response = signed_in.post(CREATE, {"title": "", "owner": str(colleague.pk)})

    assert response.status_code == 400
    form = response.context["form"]
    assert str(form["owner"].value()) == str(colleague.pk)
    assert checked_owner(response.content.decode()) == [str(colleague.pk)]
    assert not Matter.objects.filter(owner=colleague).exists()


def test_a_bound_form_never_falls_back_to_the_creator(specialist):
    """The default is for an empty page only; what was posted is what is shown."""
    colleague = factories.UserFactory()
    form = MatterCreateForm({"title": "", "owner": str(colleague.pk)}, viewer=specialist)
    assert str(form["owner"].value()) == str(colleague.pk)

    unanswered = MatterCreateForm({"title": "Midagi"}, viewer=specialist)
    assert unanswered["owner"].value() in (None, "")


def test_without_work_assign_the_default_is_oneself_and_a_colleague_is_refused(client):
    no_assign = factories.UserFactory(
        role=UserRole.SPECIALIST, capability_overrides={"work.assign": "deny"}
    )
    colleague = factories.UserFactory()
    client.force_login(no_assign)

    body = client.get(CREATE).content.decode()
    assert checked_owner(body) == [str(no_assign.pk)]
    assert f'value="{colleague.pk}"' not in body

    response = client.post(CREATE, {"title": "Võõrale", "owner": str(colleague.pk)})
    assert response.status_code == 400
    assert "owner" in response.context["form"].errors
    assert not Matter.objects.filter(title="Võõrale").exists()

    client.post(CREATE, {"title": "Endale", "owner": str(no_assign.pk)})
    assert Matter.objects.get(title="Endale").owner == no_assign


def test_the_department_head_is_defaulted_too(client, department_head):
    client.force_login(department_head)
    body = client.get(CREATE).content.decode()
    assert checked_owner(body) == [str(department_head.pk)]


def test_somebody_who_is_not_a_department_worker_is_not_made_owner(administrator):
    form = MatterCreateForm(viewer=administrator)
    assert "owner" not in form.initial
    assert form["owner"].value() is None
