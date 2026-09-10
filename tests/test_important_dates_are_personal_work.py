"""An `Oluline tähtaeg` is personal upcoming work, asserted on the real page.

The department-wide *Olulised tähtajad* reading page is retired. Nothing about
the fact moved with it: the record still lives on its Matter, it is still
created, corrected and cancelled from the Matter page, and it is still the
Matter *owner's* work — which is where the read model already put it
(docs/adr/0071).

That last claim is the one worth testing rather than assuming, and testing
through the surface rather than through the read model. `app/matters/work_items`
has its own tests and they pass whether or not `/minu-asjad/` renders what they
describe; what the brief asked for is proof that the page a lawyer actually
opens shows these deadlines, in the right chronological band, to the right
person and to nobody else.

The assignment rule, stated once:

    an active MatterImportantDate  ->  responsible = Matter.owner  ->  Minu asjad

There is no second `responsible` column and this change adds none. A deadline
follows the Matter, so a handover moves it with nothing edited and nothing
duplicated, and an unassigned Matter's deadline is nobody's personal work.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.intelligence.services import add_important_date, cancel_important_date
from app.matters.services import assign_matter, create_matter
from tests import factories

pytestmark = pytest.mark.django_db

MY_WORK = reverse("matters:my_work")


@pytest.fixture
def today():
    return timezone.localdate()


def _matter(owner, title="Tähtajaga teema", **extra):
    return create_matter(title=title, owner=owner, reference_year=2026, actor=owner, **extra)


def _deadline(matter, title, when, actor):
    return add_important_date(
        matter=matter, title=title, date_value=when, period_end=when, actor=actor
    )


def items_on(response) -> list[str]:
    """Every dated work item the page put in a band, whatever the render cap.

    Read off `work.bands` rather than scraped out of the HTML: a band renders a
    preview and keeps the rest behind a disclosure, so a body search would
    report a real item as absent purely because it sat eleventh.
    """
    found: list[str] = []
    for band in response.context["work"].bands:
        found.extend(item.text for item in [*band.preview, *band.rest])
    return found


def band_holding(response, text: str) -> str:
    for band in response.context["work"].bands:
        if text in [item.text for item in [*band.preview, *band.rest]]:
            return band.label
    raise AssertionError(f"{text!r} is in no band on the page")


# ---------------------------------------------------------------------------
# A. it is on the page at all
# ---------------------------------------------------------------------------


def test_my_own_matters_deadline_is_on_my_page(client, specialist, today):
    matter = _matter(specialist)
    _deadline(matter, "Kooskõlastusringi lõpp", today + timedelta(days=3), specialist)
    client.force_login(specialist)

    assert "Kooskõlastusringi lõpp" in items_on(client.get(MY_WORK))


def test_it_reaches_the_rendered_page_and_not_only_the_context(client, specialist, today):
    """The band's preview is what a reader sees, so one deadline is asserted in
    the markup as well — a context-only check would hold on a page that
    rendered nothing at all."""
    matter = _matter(specialist)
    _deadline(matter, "Kooskõlastusringi lõpp", today + timedelta(days=3), specialist)
    client.force_login(specialist)

    assert "Kooskõlastusringi lõpp" in client.get(MY_WORK).content.decode()


@pytest.mark.parametrize(
    "offset,expected",
    [(-7, "Üle tähtaja"), (20, "Järgmised 30 päeva")],
)
def test_it_lands_in_the_chronological_band_its_date_belongs_to(
    client, specialist, today, offset, expected
):
    """A passed milestone is late work and says so; a future one waits its turn.

    The two bands either side of «Sel nädalal» are chosen deliberately: this
    week's boundary moves with the weekday the suite runs on, and a test whose
    expected band depends on what day it is would fail on Fridays.
    """
    matter = _matter(specialist)
    _deadline(matter, "Kooskõlastusringi lõpp", today + timedelta(days=offset), specialist)
    client.force_login(specialist)

    assert band_holding(client.get(MY_WORK), "Kooskõlastusringi lõpp") == expected


# ---------------------------------------------------------------------------
# B. whose work it is
# ---------------------------------------------------------------------------


def test_a_colleagues_deadline_is_not_in_my_personal_queue(
    client, specialist, other_specialist, today
):
    """Visible to me as a Matter, and still not *my* work.

    The Matter is NORMAL, so this is not an authorization test — it is the
    responsibility rule. A page that showed every deadline everybody in the
    department is watching would be the department-wide list this change
    retired, rebuilt inside the personal one.
    """
    theirs = _matter(other_specialist, title="Kolleegi teema")
    _deadline(theirs, "Kolleegi tähtaeg", today + timedelta(days=3), other_specialist)
    client.force_login(specialist)

    assert "Kolleegi tähtaeg" not in items_on(client.get(MY_WORK))


def test_an_unassigned_matters_deadline_is_nobodys_personal_work(client, specialist, today):
    """It is *vastutajata* on Osakond, which is the honest place for work
    nobody has been given — never quietly somebody else's."""
    orphan = create_matter(title="Jaotamata teema", reference_year=2026, actor=specialist)
    _deadline(orphan, "Jaotamata tähtaeg", today + timedelta(days=3), specialist)
    client.force_login(specialist)

    assert "Jaotamata tähtaeg" not in items_on(client.get(MY_WORK))


def test_the_deadline_follows_the_matter_to_its_new_owner(
    client, specialist, other_specialist, today
):
    """A handover moves it with nothing edited and nothing duplicated.

    Both pages are read after the reassignment, so «it arrived» and «it left»
    are one assertion rather than two that could both be true of a copy.
    """
    matter = _matter(specialist)
    _deadline(matter, "Rändav tähtaeg", today + timedelta(days=3), specialist)
    assign_matter(matter=matter, owner=other_specialist, actor=specialist)

    client.force_login(specialist)
    assert "Rändav tähtaeg" not in items_on(client.get(MY_WORK))

    client.force_login(other_specialist)
    assert "Rändav tähtaeg" in items_on(client.get(MY_WORK))


def test_no_second_responsible_column_was_introduced(specialist, today):
    """The rule is «responsible = Matter.owner», read live.

    Asserted as an absence on the model, because a stored copy is exactly what
    would make a handover stop working — silently, and only for deadlines
    recorded before it.
    """
    from app.intelligence.models import MatterImportantDate

    fields = {field.name for field in MatterImportantDate._meta.get_fields()}
    assert "responsible" not in fields
    assert "owner" not in fields
    assert "assigned_to" not in fields


# ---------------------------------------------------------------------------
# C. what is not work
# ---------------------------------------------------------------------------


def test_a_cancelled_deadline_is_not_active_work(client, specialist, today):
    """Kept and marked on the Matter, and off the personal queue.

    An expectation that was called off is part of the file's history — quietly
    deleting it is how a reader concludes nobody ever recorded anything — but
    it is not something anybody still has to do (Stage-2G brief 8, 33).
    """
    matter = _matter(specialist)
    record = _deadline(matter, "Ärajäänud ring", today + timedelta(days=3), specialist)
    cancel_important_date(record=record, actor=specialist)
    client.force_login(specialist)

    assert "Ärajäänud ring" not in items_on(client.get(MY_WORK))
    # And it is still on the Matter, which is the half that must not have
    # happened.
    assert matter.important_dates.filter(pk=record.pk).exists()


def test_a_restricted_matter_leaks_nothing_into_a_non_participants_queue(
    client, reader, specialist, today
):
    """`visible_to` decides what may be seen; `owner` decides whose it is.

    A ``READER`` rather than a second specialist, because since docs/adr/0042 a
    specialist reads RESTRICTED work by role and would prove nothing here.
    """
    hidden = _matter(specialist, title="Salajane teema", visibility=Visibility.RESTRICTED)
    _deadline(hidden, "Salajane tähtaeg", today + timedelta(days=3), specialist)
    client.force_login(reader)

    response = client.get(MY_WORK)
    assert "Salajane tähtaeg" not in items_on(response)
    assert "Salajane tähtaeg" not in response.content.decode()


# ---------------------------------------------------------------------------
# D. it is still its own kind of work
# ---------------------------------------------------------------------------


def test_the_three_kinds_share_the_list_and_stay_different_facts(client, specialist, today):
    """`Järgmiseks`, `Arvamuse tähtaeg` and `Oluline tähtaeg` are three
    commitments, and a chronological list of work says so.

    One Matter legitimately produces more than one row. What must not happen is
    the three collapsing into one kind — «mida ma teen järgmisena» and «mis
    juhtub maailmas» are different questions and the page answers both.
    """
    from app.matters import work_items as wi

    matter = _matter(specialist, response_deadline=today + timedelta(days=5))
    _deadline(matter, "Ülevõtmise tähtaeg", today + timedelta(days=3), specialist)
    client.force_login(specialist)

    meanings = {
        item.meaning
        for band in client.get(MY_WORK).context["work"].bands
        for item in [*band.preview, *band.rest]
    }
    assert wi.MEANING_IMPORTANT in meanings
    assert wi.MEANING_RESPONSE in meanings


def test_a_commencement_is_not_anybodys_deadline(client, specialist, today):
    """An act coming into force is a fact about the world, not an instruction.

    Retiring the Jõustuvad aktid page does not make commencements personal
    work, and this asserts the distinction that was right before the change is
    still right after it (02-EKRAANID §D, 03-BACKEND §6).
    """
    matter = _matter(specialist)
    factories.EffectiveDateFactory(
        matter=matter,
        date_value=today + timedelta(days=3),
        period_end=today + timedelta(days=3),
        description="põhiosa jõustub",
    )
    client.force_login(specialist)

    response = client.get(MY_WORK)
    assert "põhiosa jõustub" not in items_on(response)
    assert "põhiosa jõustub" not in response.content.decode()
