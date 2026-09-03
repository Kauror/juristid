"""Where a link lands, and what the destination looks like when it gets there.

Two defects from the same family, both of them about the moment *after* a click.

`Määra` on Minu asjad, and `Märgi tehtuks` / `Muuda` / `Vaatasin üle…` in a work
row's menu, all pointed at `#jargmiseks`. Nothing in the product renders that id
— the row is `jargmiseks-rida` — so the browser found no target, arrival left
the reader at the top of a long Matter page, and the composer that writes a next
step stayed shut under a different heading. `Järgmise tegevuseta` was 79 for one
lawyer and 111 for the department, which made it the product's most repeated
request with a dead affordance (UX-003).

Arriving at the register had the mirror problem. `Täpsem otsing` opened itself
whenever a filter was active, which is precisely what a drill-down is, so
clicking a number to see the rows behind it produced 733 px of select boxes and
no visible result (UX-008).

The first test here is deliberately a *class* guard rather than four
assertions. Four links were wrong for one reason — the id was renamed and the
hrefs were not — and a test naming those four would not have caught the fifth.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.matters.services import close_matter, create_matter
from app.workflow.enums import ActionKind, DateSemantics
from app.workflow.services import set_next_action

pytestmark = pytest.mark.django_db

#: Any in-app href that names a fragment. Excludes the ones a template writes as
#: a bare `#` for a control JavaScript owns — there are none, and the second
#: test asserts that.
FRAGMENT_HREF = re.compile(r'href="([^"]*#[^"]+)"')


@pytest.fixture
def today():
    return timezone.localdate()


def _matter(owner, title="Näidisteema"):
    return create_matter(title=title, owner=owner, reference_year=2026)


def _with_action(owner, today, title="Tegevusega teema"):
    matter = _matter(owner, title=title)
    set_next_action(
        matter=matter,
        text="Esitan arvamuse",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today - timedelta(days=3),
        actor=owner,
    )
    return matter


def _body(client, url) -> str:
    response = client.get(url)
    assert response.status_code == 200, f"{url} answered {response.status_code}"
    return response.content.decode()


# ---------------------------------------------------------------------------
# Every fragment a page emits is a fragment the destination renders
# ---------------------------------------------------------------------------


def test_every_fragment_minu_asjad_emits_resolves_at_its_destination(client, specialist, today):
    """The whole class, not the four links that happened to be wrong.

    Both row shapes are on the page: one Matter carrying an overdue action, so
    the menu renders `Märgi tehtuks` / `Muuda`, and one with no action at all, so
    the `Järgmise tegevuseta` block renders `Määra`.
    """
    _with_action(specialist, today)
    _matter(specialist, title="Ilma tegevuseta")
    client.force_login(specialist)

    body = _body(client, reverse("matters:my_work"))
    hrefs = {match for match in FRAGMENT_HREF.findall(body) if "#" in match}
    assert hrefs, "the fixture should produce at least one fragment link"

    seen = set()
    for href in hrefs:
        path, _, fragment = href.partition("#")
        destination = body if path == "" else _body(client, path)
        assert f'id="{fragment}"' in destination, (
            f"{href} points at #{fragment}, which {path or 'this page'} does not render"
        )
        seen.add(fragment)

    # The two the next-step controls are supposed to use, and nothing named
    # `jargmiseks`, which never existed.
    assert "jargmiseks" not in seen
    assert {"teema-koostaja", "jargmiseks-rida"} <= seen


def test_no_control_is_a_bare_hash(client, specialist, today):
    """`href="#"` is a link that scrolls to the top and looks like an action."""
    _with_action(specialist, today)
    client.force_login(specialist)

    body = _body(client, reverse("matters:my_work"))

    assert 'href="#"' not in body


# ---------------------------------------------------------------------------
# The two destinations, and which control lives where
# ---------------------------------------------------------------------------


def test_the_row_menu_sends_completion_to_the_row_that_completes(client, specialist, today):
    """`Märgi tehtuks` and `Vaatasin üle…` name controls inside the row.

    «✓ Tehtud» and «Lükka edasi» are in `jargmiseks-rida`; the composer cannot
    complete an action. Sending those two to the composer would have been the
    same mistake in a tidier form.
    """
    matter = _with_action(specialist, today)
    client.force_login(specialist)

    body = _body(client, reverse("matters:my_work"))
    row = body[body.index("Märgi tehtuks") - 400 : body.index("Märgi tehtuks")]

    assert f"{matter.pk}/#jargmiseks-rida" in row or "#jargmiseks-rida" in row
    detail = _body(client, reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert "✓ Tehtud" in detail


def test_setting_a_next_step_sends_the_reader_to_the_composer(client, specialist, today):
    """`Määra` names the one place a next step is written.

    The `Järgmise tegevuseta` block's CTA specifically. `Aktiivsed teemad`
    carries a second control also labelled `Määra` — a decorative span inside a
    row-wide link to the Matter (`partials/portfolio_row.html`) — which is a
    different shape and is deliberately left alone by this wave.
    """
    _matter(specialist, title="Ilma tegevuseta")
    client.force_login(specialist)

    body = _body(client, reverse("matters:my_work"))
    marker = body.index("quietrow__cta")
    cta = body[marker : marker + 200]

    assert "#teema-koostaja" in cta
    assert ">Määra<" in cta


# ---------------------------------------------------------------------------
# The landing targets exist on every Matter page, which is what the fallback
# in static/js/ux.js relies on
# ---------------------------------------------------------------------------


def test_the_next_step_row_is_a_focusable_landing_target(client, specialist, today):
    matter = _with_action(specialist, today)
    client.force_login(specialist)

    detail = _body(client, reverse("matters:matter_detail", kwargs={"pk": matter.pk}))

    assert 'id="jargmiseks-rida" tabindex="-1"' in detail


@pytest.mark.parametrize("closed", [False, True])
def test_the_row_is_rendered_whether_or_not_the_composer_is(client, specialist, today, closed):
    """The fallback's premise, asserted rather than assumed.

    `overview.html` renders the composer only for a writer on an open Matter, so
    `#teema-koostaja` can legitimately be absent — on a closed Matter, or for a
    reader. Arrival falls back to the row, and that is only safe because the row
    is on every one of those pages.
    """
    matter = _with_action(specialist, today)
    if closed:
        close_matter(matter=matter, actor=specialist, disposition="COMPLETED")
    client.force_login(specialist)

    detail = _body(client, reverse("matters:matter_detail", kwargs={"pk": matter.pk}))

    assert 'id="jargmiseks-rida"' in detail
    if closed:
        assert 'id="teema-koostaja"' not in detail


def test_a_reader_gets_the_row_but_no_composer(client, reader, specialist, today):
    matter = _with_action(specialist, today)
    client.force_login(reader)

    detail = _body(client, reverse("matters:matter_detail", kwargs={"pk": matter.pk}))

    assert 'id="jargmiseks-rida"' in detail
    assert 'id="teema-koostaja"' not in detail


# ---------------------------------------------------------------------------
# UX-008 — the drill-down lands on rows, not on the filter form
# ---------------------------------------------------------------------------


def test_a_filtered_register_does_not_open_the_filter_panel(client, specialist, today):
    _with_action(specialist, today)
    client.force_login(specialist)

    body = _body(client, f"{reverse('matters:matter_list')}?too=hilinenud&olek=avatud")
    panel = body[body.index('id="tapsem-otsing"') : body.index('id="tapsem-otsing"') + 120]

    assert "open" not in panel


def test_the_plain_register_still_does_not_open_it_either(client, specialist):
    _matter(specialist)
    client.force_login(specialist)

    body = _body(client, reverse("matters:matter_list"))
    panel = body[body.index('id="tapsem-otsing"') : body.index('id="tapsem-otsing"') + 120]

    assert "open" not in panel


def test_the_active_filters_are_still_stated_as_chips(client, specialist, today):
    """Nothing is hidden by closing the panel — the chips are the better
    affordance and they are what must keep working."""
    _with_action(specialist, today)
    client.force_login(specialist)

    body = _body(client, f"{reverse('matters:matter_list')}?too=hilinenud&olek=avatud")

    assert "Tühjenda kõik" in body
    assert "Üle tähtaja" in body
