"""Two pilot findings about what the UI *says*, neither of which moves a rule.

**F-01 — the RESTRICTED wording was false.** Three surfaces explained `Piiratud`
and all three said a restricted Teema is seen only by its owner, its
collaborators and the department head. That stopped being true at docs/adr/0042,
which made the confidentiality boundary the application rather than the Matter:
every lawyer in the department reads department-wide. A person marking
confidential member feedback as `Piiratud` was therefore being promised a
narrower audience than they were getting, which is the one direction this copy
must never be wrong in.

The authorization probes in `tests/test_department_wide_lawyer_access.py` pass
and are untouched. Nothing here changes who may read anything; the tests below
tie the sentence to the rule so the two cannot drift again.

**F-03 — duplicate first names made assignment ambiguous.** Two `Sandra` rows in
a Vastutaja picker are two identical answers to a question with one right
answer. The compact first-name UI is kept, and only the names that actually
collide *within the list being offered* grow.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings
from django.template import Context, Template
from django.urls import reverse

from app.accounts.enums import UserRole
from app.accounts.naming import disambiguated_names, name_among
from app.core.authorization import ROLES_WITH_RESTRICTED_ACCESS
from app.core.enums import Visibility
from app.core.visibility_help import RESTRICTED_VISIBILITY_HELP
from app.matters.forms import IncomingIntakeForm, MatterEditForm
from tests import factories

pytestmark = pytest.mark.django_db

ROOT = Path(settings.BASE_DIR)


# ---------------------------------------------------------------------------
# F-01 — one sentence, and it is true
# ---------------------------------------------------------------------------


def test_the_wording_names_the_population_the_authorization_actually_uses():
    """The rule is `SPECIALIST` + `DEPARTMENT_HEAD`, department-wide.

    Asserted against `ROLES_WITH_RESTRICTED_ACCESS` rather than against a string
    somebody typed, so widening or narrowing the rule without revisiting the
    sentence fails here instead of misleading a lawyer.
    """
    assert ROLES_WITH_RESTRICTED_ACCESS == frozenset(
        {UserRole.SPECIALIST.value, UserRole.DEPARTMENT_HEAD.value}
    )
    # The whole legal team, said in the department's own words …
    assert "kõik osakonna juristid" in RESTRICTED_VISIBILITY_HELP
    assert "osakonnajuht" in RESTRICTED_VISIBILITY_HELP
    # … and never the claim that put the pilot wrong.
    assert "ainult" not in RESTRICTED_VISIBILITY_HELP


def test_the_wording_still_says_where_the_boundary_is():
    assert "Väljapoole osakonda" in RESTRICTED_VISIBILITY_HELP
    assert "pärivad sama piirangu" in RESTRICTED_VISIBILITY_HELP


@pytest.mark.parametrize(
    "form", [MatterEditForm, IncomingIntakeForm], ids=["muuda-teemat", "saabunud"]
)
def test_every_visibility_control_carries_the_shared_wording(form):
    assert form().fields["visibility"].help_text == RESTRICTED_VISIBILITY_HELP


def test_the_matter_page_explains_restricted_visibility_in_the_shared_words(
    signed_in, restricted_matter
):
    """The banner and the header's Nähtavus menu, on one page, in one sentence."""
    html = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": restricted_matter.pk})
    ).content.decode()
    assert html.count(RESTRICTED_VISIBILITY_HELP) == 2


def test_the_edit_page_explains_it_too(signed_in, normal_matter):
    html = signed_in.get(
        reverse("matters:matter_edit", kwargs={"pk": normal_matter.pk})
    ).content.decode()
    assert RESTRICTED_VISIBILITY_HELP in html


def test_the_intake_form_explains_it_where_a_restricted_letter_is_first_filed(signed_in):
    """It had no explanation at all, on the one page the choice cannot be undone
    without the record having been department-wide in between."""
    html = signed_in.get(reverse("matters:intake")).content.decode()
    assert RESTRICTED_VISIBILITY_HELP in html


#: Sources a hand-written sentence about the restricted audience could hide in.
_SOURCES = [
    *(ROOT / "templates").rglob("*.html"),
    *(ROOT / "app").rglob("*.py"),
]

#: The one file allowed to contain the sentence, and the test that asserts it.
_ALLOWED = {
    ROOT / "app" / "core" / "visibility_help.py",
}


def test_no_surface_writes_its_own_sentence_about_who_sees_a_restricted_teema():
    """Three separately maintained sentences is how they drifted apart.

    Any line that both mentions `piiratud` and says who *sees* something is a
    fourth copy in the making. The wording lives in one module and reaches the
    templates through `{% restricted_visibility_help %}` and the forms through
    `help_text`.
    """
    offenders: list[str] = []
    for path in _SOURCES:
        if path in _ALLOWED or "migrations" in path.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            lowered = line.lower()
            if "piiratud" in lowered and ("näevad" in lowered or "näeb" in lowered):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert not offenders, "a second explanation of restricted visibility:\n" + "\n".join(offenders)


def test_the_shared_wording_is_reachable_from_a_template():
    rendered = Template("{% load people %}{% restricted_visibility_help %}").render(Context({}))
    assert rendered == RESTRICTED_VISIBILITY_HELP


def test_nothing_here_widened_who_may_read_a_restricted_matter(client, restricted_matter):
    """The copy changed; the boundary did not."""
    from app.matters.models import Matter

    reader = factories.ReaderFactory()
    assert not Matter.objects.visible_to(reader).filter(pk=restricted_matter.pk).exists()

    lawyer = factories.UserFactory()
    assert Matter.objects.visible_to(lawyer).filter(pk=restricted_matter.pk).exists()

    administrator = factories.AdministratorFactory()
    assert not Matter.objects.visible_to(administrator).filter(pk=restricted_matter.pk).exists()

    assert restricted_matter.visibility == Visibility.RESTRICTED


# ---------------------------------------------------------------------------
# F-03 — a name is only lengthened when it is genuinely ambiguous
# ---------------------------------------------------------------------------


def _person(display_name: str, upn: str, **kwargs):
    return factories.UserFactory(display_name=display_name, upn=upn, **kwargs)


def test_unique_first_names_stay_first_names():
    people = [
        _person("Sandra Tamm", "sandra.tamm@example.invalid"),
        _person("Martin Saar", "martin.saar@example.invalid"),
        _person("Ireen Mets", "ireen.mets@example.invalid"),
    ]
    labels = disambiguated_names(people)
    assert [labels[person.pk] for person in people] == ["Sandra", "Martin", "Ireen"]


def test_only_the_colliding_names_grow():
    sandra_tamm = _person("Sandra Tamm", "sandra.tamm@example.invalid")
    sandra_kask = _person("Sandra Kask", "sandra.kask@example.invalid")
    martin = _person("Martin Saar", "martin.saar@example.invalid")

    labels = disambiguated_names([sandra_tamm, sandra_kask, martin])

    assert labels[sandra_tamm.pk] == "Sandra Tamm"
    assert labels[sandra_kask.pk] == "Sandra Kask"
    # Martin's name was never in doubt and does not grow because somebody
    # else's was.
    assert labels[martin.pk] == "Martin"


def test_identical_display_names_fall_back_to_a_readable_account_name():
    """Not a UUID. A colleague can tell two mailboxes apart; they cannot tell
    two rows of hex apart."""
    first = _person("Sandra Tamm", "sandra.tamm@example.invalid")
    second = _person("Sandra Tamm", "s.tamm@example.invalid")

    labels = disambiguated_names([first, second])

    assert labels[first.pk] == "Sandra Tamm (sandra.tamm)"
    assert labels[second.pk] == "Sandra Tamm (s.tamm)"
    assert str(first.pk) not in labels[first.pk]


def test_the_same_person_listed_twice_is_not_a_collision():
    sandra = _person("Sandra Tamm", "sandra.tamm@example.invalid")
    labels = disambiguated_names([sandra, sandra])
    assert labels[sandra.pk] == "Sandra"


def test_the_population_decides_and_nothing_global_changes():
    """One Sandra in the list is `Sandra`, even though another exists."""
    sandra_tamm = _person("Sandra Tamm", "sandra.tamm@example.invalid")
    _person("Sandra Kask", "sandra.kask@example.invalid")

    assert name_among(sandra_tamm, [sandra_tamm]) == "Sandra"


def test_a_full_name_surface_is_never_shortened():
    """`Vali kasutaja` asks who you are; first names are a smaller answer."""
    people = [
        _person("Sandra Tamm", "sandra.tamm@example.invalid"),
        _person("Martin Saar", "martin.saar@example.invalid"),
    ]
    labels = disambiguated_names(people, start_full=True)
    assert sorted(labels.values()) == ["Martin Saar", "Sandra Tamm"]


# ---------------------------------------------------------------------------
# … and the value that is submitted is still the immutable identifier
# ---------------------------------------------------------------------------


def test_the_owner_picker_labels_two_sandras_apart(signed_in, normal_matter):
    sandra_tamm = _person("Sandra Tamm", "sandra.tamm@example.invalid")
    sandra_kask = _person("Sandra Kask", "sandra.kask@example.invalid")

    form = MatterEditForm(matter=normal_matter, viewer=None)
    labels = {
        str(value): str(label) for value, label in form.fields["owner"].choices if value != ""
    }

    assert labels[str(sandra_tamm.pk)] == "Sandra Tamm"
    assert labels[str(sandra_kask.pk)] == "Sandra Kask"
    # The submitted value is the id, and it always was.
    assert str(sandra_tamm.pk) in labels


def test_a_picker_pointed_at_a_second_population_renames_for_that_one():
    """The labels are the field's, so they have to be rebuilt with its queryset."""
    from app.accounts.models import User
    from app.matters.forms import UserChoiceField

    sandra_tamm = _person("Sandra Tamm", "sandra.tamm@example.invalid")
    sandra_kask = _person("Sandra Kask", "sandra.kask@example.invalid")

    field = UserChoiceField(queryset=User.objects.filter(pk__in=[sandra_tamm.pk, sandra_kask.pk]))
    assert field.label_from_instance(sandra_tamm) == "Sandra Tamm"

    field.queryset = User.objects.filter(pk=sandra_tamm.pk)
    assert field.label_from_instance(sandra_tamm) == "Sandra"


def test_assigning_by_the_submitted_id_reaches_the_person_the_label_named(
    signed_in, normal_matter, specialist
):
    sandra_tamm = _person("Sandra Tamm", "sandra.tamm@example.invalid")
    _person("Sandra Kask", "sandra.kask@example.invalid")

    response = signed_in.post(
        reverse("matters:assign_owner", kwargs={"pk": normal_matter.pk}),
        {"owner": str(sandra_tamm.pk), "next": reverse("matters:matter_list")},
    )

    assert response.status_code in (200, 302)
    normal_matter.refresh_from_db()
    assert normal_matter.owner_id == sandra_tamm.pk


def test_the_assignment_notice_goes_to_the_person_that_was_picked(
    signed_in, normal_matter, specialist
):
    from app.matters.models import MatterAssignmentNotice

    sandra_tamm = _person("Sandra Tamm", "sandra.tamm@example.invalid")
    sandra_kask = _person("Sandra Kask", "sandra.kask@example.invalid")

    signed_in.post(
        reverse("matters:assign_owner", kwargs={"pk": normal_matter.pk}),
        {"owner": str(sandra_kask.pk), "next": reverse("matters:matter_list")},
    )

    recipients = set(
        MatterAssignmentNotice.objects.filter(matter=normal_matter).values_list(
            "recipient_id", flat=True
        )
    )
    assert sandra_kask.pk in recipients
    assert sandra_tamm.pk not in recipients


def test_the_register_owner_filter_tells_two_sandras_apart(signed_in, normal_matter):
    sandra_tamm = _person("Sandra Tamm", "sandra.tamm@example.invalid")
    sandra_kask = _person("Sandra Kask", "sandra.kask@example.invalid")
    normal_matter.owner = sandra_tamm
    normal_matter.save(update_fields=["owner"])
    other = factories.MatterFactory(owner=sandra_kask)
    assert other.pk

    html = signed_in.get(reverse("matters:matter_list")).content.decode()

    assert "Sandra Tamm" in html
    assert "Sandra Kask" in html
