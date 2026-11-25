"""The landing page: a key from an allowlist, checked when saved and at every redirect.

docs/adr/0221, «Personal preferences». `/` and the persona choice open the
person's chosen page while they may still open it, Minu asjad otherwise; an
explicit `next` always wins; nothing stored or posted can become a redirect to
an address outside the allowlist.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.accounts import landing
from app.accounts.models import PersonalPreference
from app.register_watch.models import CandidateAudience
from tests import factories
from tests.admin_session import apply_admin_gate, choose, pass_gate

pytestmark = pytest.mark.django_db

SAVE = "accounts:save_landing_page"


def _home(client) -> str:
    response = client.get(reverse("core:home"))
    assert response.status_code == 302
    return response["Location"]


def test_the_allowlist_is_the_contracted_six_in_order():
    assert [(page.key, page.url_name) for page in landing.LANDING_PAGES] == [
        ("minu-asjad", "matters:my_work"),
        ("teemad", "matters:matter_list"),
        ("osakond", "matters:department"),
        ("statistika", "reporting:dashboard"),
        ("monitooring", "register_watch:candidates"),
        ("teavitused", "notifications:list"),
    ]


def test_without_a_choice_home_opens_minu_asjad(client):
    client.force_login(factories.UserFactory())
    assert _home(client) == reverse("matters:my_work")


@pytest.mark.parametrize(
    "key, url_name",
    [
        ("teemad", "matters:matter_list"),
        ("osakond", "matters:department"),
        ("statistika", "reporting:dashboard"),
        ("teavitused", "notifications:list"),
        ("minu-asjad", "matters:my_work"),
    ],
)
def test_a_saved_choice_is_where_home_opens(client, key, url_name):
    person = factories.UserFactory()
    client.force_login(person)

    response = client.post(reverse(SAVE), {"avaleht": key})

    assert response.status_code == 302 and response["Location"] == reverse("haldus:settings")
    assert _home(client) == reverse(url_name)
    assert client.get(reverse(url_name)).status_code == 200


def test_monitooring_is_offered_and_saved_for_the_head_only(client):
    head = factories.DepartmentHeadFactory()
    lawyer = factories.UserFactory()
    assert "monitooring" in [page.key for page in landing.choices(head)]
    assert "monitooring" not in [page.key for page in landing.choices(lawyer)]

    client.force_login(lawyer)
    refused = client.post(reverse(SAVE), {"avaleht": "monitooring"})
    assert refused.status_code == 302
    assert not PersonalPreference.objects.filter(user=lawyer).exists()
    assert _home(client) == reverse("matters:my_work")

    client.force_login(head)
    client.post(reverse(SAVE), {"avaleht": "monitooring"})
    assert _home(client) == reverse("register_watch:candidates")


def test_a_choice_no_longer_permitted_falls_back_at_the_redirect(client):
    """Saved while Monitooring was open to everyone; the head closed it again."""
    audience = CandidateAudience.objects.create(everyone=True)
    lawyer = factories.UserFactory()
    client.force_login(lawyer)
    client.post(reverse(SAVE), {"avaleht": "monitooring"})
    assert _home(client) == reverse("register_watch:candidates")

    audience.everyone = False
    audience.save()

    assert _home(client) == reverse("matters:my_work")
    # The choice is kept: it applies again if the page opens to them again.
    assert PersonalPreference.objects.get(user=lawyer).landing_page == "monitooring"


@pytest.mark.parametrize(
    "value",
    ["https://example.invalid/", "//example.invalid", "/haldus/", "matters:my_work", "", "x" * 50],
)
def test_nothing_but_an_allowlisted_key_is_saved(client, value):
    """No URL, no route name, no stray value: a 302 back with a message, nothing written."""
    person = factories.UserFactory()
    client.force_login(person)

    response = client.post(reverse(SAVE), {"avaleht": value})

    assert response.status_code == 302 and response["Location"] == reverse("haldus:settings")
    assert not PersonalPreference.objects.filter(user=person).exists()
    assert _home(client) == reverse("matters:my_work")


def test_a_tampered_stored_value_is_never_a_redirect(client):
    person = factories.UserFactory()
    PersonalPreference.objects.create(user=person, landing_page="//example.invalid")
    client.force_login(person)
    assert _home(client) == reverse("matters:my_work")


def test_the_choice_is_the_senders_own(client):
    a = factories.UserFactory()
    b = factories.UserFactory()
    PersonalPreference.objects.create(user=a, landing_page="teemad")
    client.force_login(b)

    client.post(reverse(SAVE), {"avaleht": "statistika", "user": str(a.pk), "user_id": str(a.pk)})

    assert PersonalPreference.objects.get(user=a).landing_page == "teemad"
    assert PersonalPreference.objects.get(user=b).landing_page == "statistika"


def test_saving_needs_a_signed_in_person_and_a_post(client):
    refused_anonymous = client.post(reverse(SAVE), {"avaleht": "teemad"})
    assert refused_anonymous.status_code == 302
    assert reverse(SAVE) not in refused_anonymous["Location"].split("?")[0]
    assert not PersonalPreference.objects.exists()

    client.force_login(factories.UserFactory())
    assert client.get(reverse(SAVE)).status_code == 405


def test_the_persona_choice_opens_the_landing_page_and_next_wins(client, settings):
    apply_admin_gate(settings)
    lawyer = factories.UserFactory()
    PersonalPreference.objects.create(user=lawyer, landing_page="teemad")
    pass_gate(client)

    chosen = choose(client, lawyer)
    assert chosen.status_code == 302 and chosen["Location"] == reverse("matters:matter_list")

    explicit = client.post(
        reverse("accounts:act_as") + "?next=" + reverse("reporting:dashboard"),
        {"user_id": str(lawyer.pk)},
    )
    assert explicit["Location"] == reverse("reporting:dashboard")


def test_each_persona_keeps_its_own_landing_page_behind_the_gate(client, settings):
    apply_admin_gate(settings)
    lawyer = factories.UserFactory()
    head = factories.DepartmentHeadFactory()
    PersonalPreference.objects.create(user=head, landing_page="osakond")
    pass_gate(client)

    assert choose(client, head)["Location"] == reverse("matters:department")
    assert choose(client, lawyer)["Location"] == reverse("matters:my_work")


def test_kuvamine_offers_only_the_pages_this_persona_may_open(client):
    client.force_login(factories.UserFactory())
    body = client.get(reverse("haldus:settings")).content.decode()

    assert f'action="{reverse(SAVE)}"' in body
    for key in ("minu-asjad", "teemad", "osakond", "statistika", "teavitused"):
        assert f'value="{key}"' in body
    assert 'value="monitooring"' not in body
    assert '<option value="minu-asjad" selected>' in body


def test_kuvamine_offers_four_text_sizes_with_tavaline_pressed_by_default(client):
    client.force_login(factories.UserFactory())
    body = client.get(reverse("haldus:settings")).content.decode()

    for value, label in (
        ("vaike", "Väike"),
        ("tavaline", "Tavaline"),
        ("suur", "Suur"),
        ("vaga-suur", "Väga suur"),
    ):
        assert f'data-text-size-choice="{value}"' in body
        assert f">{label}</button>" in body
    assert 'data-text-size-choice="tavaline" aria-pressed="true"' in body


def test_the_text_size_is_applied_before_any_stylesheet(client):
    """Pre-paint like the theme: the script is in <head>, not deferred, before the CSS."""
    client.force_login(factories.UserFactory())
    body = client.get(reverse("matters:my_work")).content.decode()
    head = body[: body.index("</head>")]

    script = head.index("js/text-size.js")
    assert script < head.index("css/tokens.css")
    tag = head[head.rindex("<script", 0, script) : head.index("</script>", script)]
    assert "defer" not in tag and "async" not in tag
    # Tavaline is the server's page: no attribute is rendered.
    assert "data-text-size=" not in body[: body.index("<body")]


def test_the_sizes_are_token_overrides_and_tavaline_has_none():
    from pathlib import Path

    from django.conf import settings

    tokens = (Path(settings.BASE_DIR) / "static" / "css" / "tokens.css").read_text(encoding="utf-8")
    for value in ("vaike", "suur", "vaga-suur"):
        assert f':root[data-text-size="{value}"]' in tokens
    assert 'data-text-size="tavaline"' not in tokens
