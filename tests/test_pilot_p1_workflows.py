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

from app.documents.enums import DocumentRole
from app.documents.models import Document
from app.matters.models import Entry
from app.workflow.enums import (
    ActionKind,
    DatePrecision,
    DateSemantics,
    Disposition,
)
from app.workflow.models import NextAction
from app.workflow.services import set_next_action_for_new_work

pytestmark = pytest.mark.django_db


def _pdf(name: str = "Koja_arvamus.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf")


def _compose(client, matter, **fields):
    """One composer save, carrying the fields every POST from the page carries."""
    payload = {
        "body": "",
        "kind": "NOTE",
        "attachment_role": DocumentRole.OTHER,
        "next_text": "",
        "next_date": "",
        "deadline_title": "",
        "deadline_date": "",
        "deadline_precision": DatePrecision.EXACT,
    }
    payload.update(fields)
    return client.post(
        reverse("matters:compose", kwargs={"pk": matter.pk}),
        payload,
        headers={"HX-Request": "true"},
    )


# ---------------------------------------------------------------------------
# F-02 — closure data may never be accepted and then ignored
# ---------------------------------------------------------------------------


def test_closure_answers_are_never_accepted_and_then_dropped(signed_in, normal_matter):
    """F-02, after docs/adr/0131 §11: a closure posted to the old composer is refused.

    F-02 was that a filled-in closing section could return 200, write an ordinary
    Entry and silently discard the rest. Closing is a `Hetkeseis` now, and the
    superseded composer refuses its closure answers outright — visibly, and with
    nothing written — rather than either closing through a second door or
    dropping them.
    """
    response = _compose(
        signed_in,
        normal_matter,
        body="Teema on lõppenud.",
        disposition=Disposition.COMPLETED,
        closing_words="Seadus jõustus 1. jaanuaril.",
    )
    assert response.status_code == 400
    assert "Teema lõpetatakse hetkeseisuga" in response.content.decode()
    normal_matter.refresh_from_db()
    assert normal_matter.is_open
    assert not Entry.objects.filter(matter=normal_matter).exists()


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


def test_the_old_confirmation_box_is_gone_from_the_form_and_the_page(signed_in, normal_matter):
    """A redundant second confirmation is a place for answers to get lost."""
    from app.matters.forms import ComposerForm

    assert "close_matter" not in ComposerForm().fields
    html = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})
    ).content.decode()
    assert 'name="close_matter"' not in html


def test_a_partial_closure_refuses_the_whole_save(signed_in, normal_matter):
    """Nothing at all is written when the closing half does not hold together —
    not the entry above it, and not the next step beside it."""
    response = _compose(
        signed_in,
        normal_matter,
        body="Midagi juhtus.",
        next_text="Vaadata versioon üle",
        next_date="20.10.2026",
        closing_words="Menetlus lõppes.",
    )

    assert response.status_code == 400
    normal_matter.refresh_from_db()
    assert normal_matter.is_open
    assert not Entry.objects.filter(matter=normal_matter).exists()
    assert not NextAction.objects.filter(matter=normal_matter).exists()


def test_a_rejected_upload_leaves_nothing_behind(signed_in, normal_matter):
    """The same atomic refusal when it is the evidence that is refused.

    Through the composer's own file control, which is the evidence path the
    approved target has: the closing panel no longer takes an upload, and the
    canonical rules that governed that one govern this one
    (app/documents/services.py)."""
    bad = SimpleUploadedFile("arvamus.exe", b"MZ not a pdf", content_type="application/pdf")
    response = _compose(signed_in, normal_matter, body="Sain faili.", attachment=bad)

    assert response.status_code == 400
    normal_matter.refresh_from_db()
    assert normal_matter.is_open
    assert not Entry.objects.filter(matter=normal_matter).exists()
    assert not Document.objects.filter(matter=normal_matter).exists()


def test_an_ordinary_save_that_touches_no_closing_field_still_works(signed_in, normal_matter):
    """Outcome A. The composer is a capture surface first."""
    response = _compose(signed_in, normal_matter, body="Helistasin ministeeriumisse.")

    assert response.status_code == 200
    normal_matter.refresh_from_db()
    assert normal_matter.is_open
    assert Entry.objects.filter(matter=normal_matter).count() == 1


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
