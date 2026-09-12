"""A count of one is not «1 teemat».

Estonian takes the nominative singular after one and the partitive after
everything else, nought included. Every count on every surface was rendered as
`N` followed by the partitive, which is right twice and wrong once: the register
header said «1 teemat», Osakond said «1 avatud teemat», the documents tab said
«1 faili» and the chronology said «1 kirjet». A native reader meets it on the
first screen (adversarial QA 2026-09-12, QA-12).

The rule lives in one filter, so the tests of the rule are unit tests of the
filter and the tests of the surfaces only check that each one asks it.
"""

from __future__ import annotations

import pytest
from django.template import Context, Template
from django.urls import reverse

from app.core.templatetags.counts import counted
from tests import factories

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "number,expected",
    [
        (0, "0 teemat"),
        (1, "1 teema"),
        (2, "2 teemat"),
        (11, "11 teemat"),
        (21, "21 teemat"),
        (101, "101 teemat"),
    ],
)
def test_only_exactly_one_takes_the_nominative(number, expected):
    """Nought is partitive, and so is every number above one — 21 included.

    Estonian does not follow the Slavic pattern where a number ending in one
    reverts to the singular, so `21 teemat` is right and `21 teema` would be a
    new mistake in place of the old one.
    """
    assert counted(number, "teema,teemat") == expected


def test_a_string_of_digits_counts_as_its_number():
    """A template hands whatever the context holds; `1` and `"1"` are one count."""
    assert counted("1", "fail,faili") == "1 fail"


def test_something_that_is_not_a_number_keeps_the_old_behaviour():
    """A count that cannot be read is a template bug, and must look like one
    rather than disappear into a blank or raise inside a rendered page."""
    assert counted(None, "kirje,kirjet") == "None kirjet"


def test_the_filter_is_loadable_by_the_name_the_templates_use():
    rendered = Template('{% load counts %}{{ n|counted:"fail,faili" }}').render(Context({"n": 1}))
    assert rendered == "1 fail"


# ---------------------------------------------------------------------------
# The surfaces QA named
# ---------------------------------------------------------------------------


def test_the_register_header_says_one_teema(signed_in, specialist):
    factories.MatterFactory(owner=specialist)

    body = signed_in.get(reverse("matters:matter_list")).content.decode()

    assert "1 teema<" in body
    assert "1 teemat" not in body


def test_the_register_header_still_says_two_teemat(signed_in, specialist):
    """The other half, so the fix cannot be «always nominative»."""
    factories.MatterFactory(owner=specialist)
    factories.MatterFactory(owner=specialist)

    body = signed_in.get(reverse("matters:matter_list")).content.decode()

    assert "2 teemat" in body


def test_the_department_header_says_one_avatud_teema(signed_in, specialist):
    factories.MatterFactory(owner=specialist, is_open=True)

    body = signed_in.get(reverse("matters:department")).content.decode()

    assert "1 avatud teema<" in body
    assert "1 avatud teemat" not in body


def test_the_documents_tab_says_one_fail(signed_in, specialist, capture_evidence):
    from tests import synthetic_corpus as corpus

    matter = factories.MatterFactory(owner=specialist)
    capture_evidence(matter, corpus.text_pdf(["Saatekiri"]), "kiri.pdf", "application/pdf")

    body = signed_in.get(
        reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    ).content.decode()

    assert "1 fail" in body
    assert "1 faili" not in body


def test_the_chronology_says_one_kirje(signed_in, specialist):
    from app.matters.services import add_entry

    matter = factories.MatterFactory(owner=specialist)
    add_entry(matter=matter, author=specialist, body="<p>Helistasin ministeeriumisse.</p>")

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()

    assert "1 kirje<" in body
    assert "1 kirjet" not in body
