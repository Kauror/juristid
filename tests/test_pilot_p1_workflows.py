"""The three P1 defects the pilot QA found on the Teema surface.

Written as reproductions first. Each one states the thing a lawyer actually did
and the thing the application did back, so the fix is judged against the
behaviour rather than against the implementation that produced it.

**F-02 — closure data could be silently discarded.** The closing section was
gated on a second confirmation, `Lõpeta see teema`. Filling in the disposition,
the sent opinion, its date, its recipients and the work victory and leaving that
one box unticked produced a successful save that wrote an ordinary Entry and
dropped every closure answer on the floor: no Submission, no Document, no
closure, and no message saying so.

**F-04 — `Lükka edasi` destroyed unsaved composer content.** Deferring swapped
the whole Teema column, which is the Järgmiseks row *and the open composer under
it*. `✓ Tehtud` had already been fixed for exactly this (ADR 0052 §8); the defer
control had not.

**F-05 — `Lükka edasi` counted from today.** A step due 30.09 deferred by a day
became 01.09 — a date in the past — because the delta was added to today rather
than to the date on the step.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.workflow.enums import (
    ActionKind,
    DatePrecision,
    DateSemantics,
)
from app.workflow.services import set_next_action_for_new_work

pytestmark = pytest.mark.django_db


def _pdf(name: str = "Koja_arvamus.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf")


# ---------------------------------------------------------------------------
# F-02 — closure data may never be accepted and then ignored
# ---------------------------------------------------------------------------


def test_the_lopeta_teema_route_is_gone(signed_in, normal_matter):
    """`+ Lõpeta teema` and its endpoint went together (docs/adr/0131 §11)."""
    response = signed_in.post(
        f"/teemad/{normal_matter.pk}/lisa/lopeta/",
        {"disposition": "COMPLETED", "closing_words": "Menetlus lõppes."},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 404
    normal_matter.refresh_from_db()
    assert normal_matter.is_open


# ---------------------------------------------------------------------------
# F-04 / F-05 — `Lükka edasi` is gone, route and all (ENG-050A)
# ---------------------------------------------------------------------------


def _action(matter, actor, *, days: int, kind=ActionKind.DO, semantics=DateSemantics.DEADLINE):
    return set_next_action_for_new_work(
        matter=matter,
        text="Vaadata uus eelnõu versioon üle",
        kind=kind,
        date_semantics=semantics,
        target_date=timezone.localdate() + timedelta(days=days),
        date_precision=DatePrecision.EXACT,
        actor=actor,
    )


def test_the_current_action_zone_no_longer_carries_the_defer_control(
    signed_in, normal_matter, specialist
):
    """`PRAEGUNE TEGEVUS` is the text, the date, `Mida tegid?` and `Muuda`.

    «Lükka edasi» was a second disclosure holding four POST buttons and a date
    box, inside the one row on the page that has to be readable at a glance
    (TEEMA_TARGET_SPEC §C.1, docs/adr/0074 §20). `✓ Tehtud` went in the round
    after it, for a stronger reason: completing a task without recording what
    was done is half of one act (docs/adr/0075 §3). The defer route and its
    day-counting went with ENG-050A: a plan moves through `Muuda` and a step
    that waits through `Vaatasin üle`.
    """
    _action(normal_matter, specialist, days=30)
    html = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})
    ).content.decode()

    zone = html.split('id="praegune-tegevus"')[1].split('id="lisa-teemale"')[0]
    assert "Lükka edasi" not in html
    # `✓ Tehtud` is back since docs/adr/0133 §4 — as the disclosure that opens
    # `Mida tegid?`, never as a completion with nothing recorded.
    assert "Tehtud</button>" not in zone
    assert "Mida tegid?" in zone
    assert "Muuda" in zone
