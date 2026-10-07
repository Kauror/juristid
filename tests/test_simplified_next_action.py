"""The simplified Teema next-action workflow (ADR 0052).

The composer stopped asking a lawyer to classify their own work. Three
questions remain — *mida tegid või mis juhtus*, *järgmiseks*, *millal* — and
this module is the contract that keeps them three questions rather than five.

What is asserted here, and why each of them is a thing somebody could quietly
undo:

* the two text boxes stay two facts, neither derived from the other;
* a native step is stored `DO` / `DEADLINE` / `EXACT` and that is invisible;
* the classification vocabulary is not merely hidden — the form has no field
  that carries it, so a crafted POST cannot reintroduce it;
* every historical `WAIT` and `MONITOR` is untouched, unlabelled and still
  completable;
* `✓ Tehtud` completes, and completes *only* — no entry is invented;
* superseding a step is still not the same as completing it.

The domain underneath has its own suites and is unchanged. What is tested here
is that this surface reaches it, and that it stopped asking for things.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.dates import format_estonian_date
from app.matters.models import Entry
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics, Disposition
from app.workflow.models import NextAction
from app.workflow.services import current_next_action, set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

#: Words this workflow retired from the Teema surface. Checked as a set on
#: every rendering assertion below, because removing one of the four and
#: leaving the other three is exactly the half-migration this module exists to
#: catch.
RETIRED_WORDS = ("TEEN", "OOTAN", "JÄLGIN", "Ei muuda")

#: And the date vocabulary that went with them, as the row used to shout it.
#: Checked inside the `Järgmiseks` row rather than over the whole page:
#: `Oluline tähtaeg` is a different concept, is still offered, and is still
#: spelled with the same word.
RETIRED_DATE_WORDS = ("TÄHTAEG", "VAATAN ÜLE", "OODATAV", "ÜLEVAATUS MÖÖDAS")


def _jargmiseks_row(body: str) -> str:
    """`PRAEGUNE TEGEVUS` alone, so an assertion about it cannot be answered by
    something else on the page.

    The `Järgmiseks` row it used to cut out is superseded by the zone that
    states the current task and takes the answer that finishes it
    (docs/adr/0075 §3). Everything this file asserts about that surface — the
    sentence, the date, the lateness, and the retired vocabulary staying
    retired — is asked of the zone instead.
    """
    start = body.index('id="praegune-tegevus"')
    return " ".join(body[start : body.index('id="lisa-teemale"', start)].split())


def _detail(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _set_step(client, matter, *, text="", target_date="", **extra):
    """`+ Järgmine tegevus` / `Muuda` — `matters:set_action`, the step's own door.

    This file posted to the superseded composer, which wrote the description
    and the step in one save; it was retired with ENG-050A2 (docs/adr/0075).
    """
    payload = {"text": text, "target_date": target_date, **extra}
    return client.post(
        reverse("matters:set_action", kwargs={"pk": matter.pk}),
        payload,
        headers={"HX-Request": "true"},
    )


def _marge(client, matter, title):
    """`+ Märge` — something happened, and the open step is not part of it."""
    return client.post(
        reverse("matters:add_note", kwargs={"pk": matter.pk}),
        {"title": title, "occurred_on": format_estonian_date(timezone.localdate())},
        headers={"HX-Request": "true"},
    )


def _in_a_week() -> str:
    return format_estonian_date(timezone.localdate() + timedelta(days=7))


# ---------------------------------------------------------------------------
# 1-3. The state matrix: what each combination of the two boxes writes
# ---------------------------------------------------------------------------


def test_a_body_alone_writes_an_entry_and_leaves_the_current_step_alone(
    signed_in, normal_matter, specialist
):
    """A. The ordinary save. This used to be spelled «Ei muuda»."""
    existing = set_next_action(
        matter=normal_matter,
        text="Helistada Kliimaministeeriumisse",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() + timedelta(days=3),
        actor=specialist,
    )

    response = _marge(signed_in, normal_matter, "Ministeerium lubas uue versiooni.")
    assert response.status_code == 200, response.content.decode()[:2000]

    existing.refresh_from_db()
    assert existing.status == ActionStatus.OPEN
    assert existing.text == "Helistada Kliimaministeeriumisse"
    assert current_next_action(normal_matter) == existing


def test_a_next_action_alone_writes_no_empty_entry(signed_in, normal_matter):
    """B. A supported case in its own right, and the one the old form refused.

    Under the previous contract the next step's wording *was* the entry body,
    so recording only what happens next was impossible: it always dragged an
    entry along saying the same sentence.
    """
    response = _set_step(
        signed_in, normal_matter, text="Vaadata uus eelnõu versioon üle", target_date=_in_a_week()
    )
    assert response.status_code == 200, response.content.decode()[:2000]

    assert not Entry.objects.filter(matter=normal_matter).exists()
    action = current_next_action(normal_matter)
    assert action is not None
    assert action.text == "Vaadata uus eelnõu versioon üle"


# ---------------------------------------------------------------------------
# 4-5. The two refusals, each on the control that is empty
# ---------------------------------------------------------------------------


def test_a_next_action_without_a_date_is_saved_and_never_filed_for_today(signed_in, normal_matter):
    """D, reversed by docs/adr/0106. The sentence is the step; the day is extra.

    This was «refused on the date», on the reasoning that a deadline with no
    date cannot be planned against. What it refused was a complete instruction,
    and the only way past it was to type a day nobody had chosen — so the entry
    and the step are both written now, and the date stays `NULL` rather than
    becoming today.
    """
    response = _set_step(signed_in, normal_matter, text="Vaadata uus versioon üle")
    assert response.status_code == 200, response.content.decode()[:2000]

    action = NextAction.objects.get(matter=normal_matter)
    assert action.text == "Vaadata uus versioon üle"
    assert action.target_date is None
    assert action.kind == ActionKind.DO
    assert action.date_semantics == DateSemantics.DEADLINE
    assert action.status == ActionStatus.OPEN
    # Nothing was invented.
    assert action.target_date != timezone.localdate()


# ---------------------------------------------------------------------------
# 6-8. The other composer paths still save on their own
# ---------------------------------------------------------------------------


def test_closing_the_matter_still_works_with_neither_box(signed_in, normal_matter, specialist):
    """F, part three. Closure is its own path — a `Hetkeseis` since docs/adr/0131 §11.

    The old composer's closure answer is refused now; a `+ Märge` choosing
    «Jõustunud» closes the file and ends the step exactly as a closure did.
    """
    from app.workflow.models import StageVocabulary

    set_next_action(
        matter=normal_matter,
        text="Saata kiri",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() + timedelta(days=2),
        actor=specialist,
    )

    response = signed_in.post(
        reverse("matters:add_note", kwargs={"pk": normal_matter.pk}),
        {
            "title": "Menetlus lõppes.",
            "stage": str(StageVocabulary.objects.get(key="in_force").pk),
        },
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200, response.content.decode()[:2000]

    normal_matter.refresh_from_db()
    assert not normal_matter.is_open
    assert normal_matter.disposition == Disposition.COMPLETED
    assert current_next_action(normal_matter) is None


# ---------------------------------------------------------------------------
# 9-10. Superseding, completing, and the difference between them
# ---------------------------------------------------------------------------


def test_a_new_step_supersedes_the_open_one_rather_than_completing_it(
    signed_in, normal_matter, specialist
):
    """9 + ADR 0052 §8. The distinction survives the simpler UI.

    A lawyer who replaces a step did not necessarily do the old one. Marking it
    `COMPLETED` because a replacement arrived would put work in the history
    that nobody did.
    """
    first = set_next_action(
        matter=normal_matter,
        text="Saata kiri",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() + timedelta(days=2),
        actor=specialist,
    )

    _set_step(
        signed_in,
        normal_matter,
        text="Helistada ministeeriumile",
        target_date=_in_a_week(),
        action_id=str(first.pk),
    )

    first.refresh_from_db()
    assert first.status == ActionStatus.SUPERSEDED
    assert first.status != ActionStatus.COMPLETED
    current = current_next_action(normal_matter)
    assert current is not None
    assert current.text == "Helistada ministeeriumile"
    assert first.replaced_by == current
    assert not ChangeEvent.objects.filter(
        matter=normal_matter, event_type=ChangeEventType.NEXT_ACTION_COMPLETED
    ).exists()


def test_completion_is_the_result_being_saved_and_swaps_the_whole_column(
    signed_in, normal_matter, specialist
):
    """ADR 0052 §8 kept `✓ Tehtud` off `#teema-vaade` so an open composer was
    not thrown away by pressing it. docs/adr/0075 §3 removes the problem rather
    than working around it: there is no second control, the result and the
    completion are one save, and the response may therefore legitimately
    re-render the whole column — there is nothing left underneath to discard.
    """
    set_next_action(
        matter=normal_matter,
        text="Saata kiri",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() + timedelta(days=1),
        actor=specialist,
    )
    body = _detail(signed_in, normal_matter)
    row = _jargmiseks_row(body)

    assert "/praegune/" in row, "the zone no longer offers the completion route"
    assert 'hx-target="#teema-vaade"' in row
    # The two-save shape is gone: no completion route that writes no result.
    # `✓ Tehtud` is the disclosure around that one form (docs/adr/0133 §4),
    # never a button posting somewhere of its own.
    assert "/valmis/" not in row
    assert "Tehtud</button>" not in row


# ---------------------------------------------------------------------------
# 11-12. Historical WAIT and MONITOR: stored, unlabelled, completable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "semantics", "retired_label"),
    [
        (ActionKind.WAIT, DateSemantics.EXPECTED_AROUND, "OOTAN"),
        (ActionKind.MONITOR, DateSemantics.REVIEW_ON, "JÄLGIN"),
    ],
)
def test_a_historical_action_keeps_its_kind_and_never_shows_it(
    signed_in, normal_matter, specialist, kind, semantics, retired_label
):
    action = set_next_action(
        matter=normal_matter,
        text="Ootan ministeeriumi vastust",
        kind=kind,
        date_semantics=semantics,
        target_date=timezone.localdate() + timedelta(days=5),
        actor=specialist,
    )

    body = _detail(signed_in, normal_matter)
    flat = " ".join(body.split())

    # Rendered, honestly: the sentence and the date.
    assert "Ootan ministeeriumi vastust" in flat
    assert action.display_date in flat

    # Classified, silently.
    action.refresh_from_db()
    assert action.kind == kind
    assert action.date_semantics == semantics
    assert retired_label not in flat
    assert f"modechip--{kind.lower()}" not in body

    row = _jargmiseks_row(body)
    for word in RETIRED_DATE_WORDS:
        assert word not in row, f"the row still names the date «{word}»"


@pytest.mark.parametrize("kind", [ActionKind.WAIT, ActionKind.MONITOR])
def test_a_historical_action_can_still_be_completed_from_the_teema_page(
    signed_in, normal_matter, specialist, kind
):
    action = set_next_action(
        matter=normal_matter,
        text="Jälgida eelnõu menetlust",
        kind=kind,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=timezone.localdate() + timedelta(days=5),
        actor=specialist,
    )

    # `PRAEGUNE TEGEVUS` → `Salvesta`, the one completion door (ENG-050A retired
    # `matters:complete_action`, which this used to post to).
    response = signed_in.post(
        reverse("matters:complete_current_action", kwargs={"pk": normal_matter.pk}),
        {"action_id": str(action.pk), "body": "Eelnõu menetlus on lõppenud."},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200

    action.refresh_from_db()
    assert action.status == ActionStatus.COMPLETED
    assert ChangeEvent.objects.filter(
        matter=normal_matter,
        event_type=ChangeEventType.NEXT_ACTION_COMPLETED,
        object_id=action.pk,
    ).exists()
    # Completing it did not rewrite what it was.
    assert action.kind == kind
    assert action.date_semantics == DateSemantics.REVIEW_ON


def test_an_undated_historical_action_is_shown_without_a_date(signed_in, normal_matter, specialist):
    set_next_action(
        matter=normal_matter,
        text="Jälgida menetlust",
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=None,
        actor=specialist,
    )
    body = _detail(signed_in, normal_matter)
    assert "Jälgida menetlust" in body
    assert "uxnext__date" not in body


def test_an_approximate_historical_action_keeps_its_period_wording(
    signed_in, normal_matter, specialist
):
    """The composer only writes EXACT. What is already stored is not rewritten."""
    action = set_next_action(
        matter=normal_matter,
        text="Jälgida sügisest menetlust",
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=timezone.localdate().replace(month=9, day=1),
        date_precision=DatePrecision.MONTH,
        actor=specialist,
    )
    body = _detail(signed_in, normal_matter)
    assert action.display_date in body
    assert action.date_precision == DatePrecision.MONTH


# ---------------------------------------------------------------------------
# The form's own shape: what it stores, and what it refuses to accept
# ---------------------------------------------------------------------------


def test_a_native_step_is_stored_do_deadline_exact(signed_in, normal_matter):
    target = timezone.localdate() + timedelta(days=7)
    _set_step(
        signed_in,
        normal_matter,
        text="Vaadata uus versioon üle",
        target_date=format_estonian_date(target),
    )

    action = current_next_action(normal_matter)
    assert action is not None
    assert action.kind == ActionKind.DO
    assert action.date_semantics == DateSemantics.DEADLINE
    assert action.date_precision == DatePrecision.EXACT
    assert action.target_date == target


def test_a_crafted_post_cannot_choose_a_kind_or_a_date_meaning(signed_in, normal_matter):
    # A precision is a real answer on this form (`periods=True`); a kind and a
    # date meaning are not, and a crafted one binds to nothing (ADR 0054).
    _set_step(
        signed_in,
        normal_matter,
        text="Kontrollida, kas ministeerium vastas",
        target_date=_in_a_week(),
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.EXPECTED_AROUND,
        next_kind=ActionKind.WAIT,
        next_date_semantics=DateSemantics.EXPECTED_AROUND,
    )

    action = current_next_action(normal_matter)
    assert action is not None
    assert action.kind == ActionKind.DO
    assert action.date_semantics == DateSemantics.DEADLINE


def test_the_next_action_text_is_stored_exactly_as_typed(signed_in, normal_matter):
    _set_step(
        signed_in,
        normal_matter,
        text="  Kontrollida, kas ministeerium vastas  ",
        target_date=_in_a_week(),
    )
    action = current_next_action(normal_matter)
    assert action is not None
    assert action.text == "Kontrollida, kas ministeerium vastas"


# ---------------------------------------------------------------------------
# What the two surfaces render
# ---------------------------------------------------------------------------


def test_the_workspace_asks_its_questions_and_no_classification(signed_in, normal_matter):
    # An open step, because `Muuda` — the next-step form — is drawn beside a
    # task and is its only host since `+ Järgmine tegevus` left the launcher
    # (docs/adr/0097 §8.2).
    set_next_action(
        matter=normal_matter,
        text="Koosta arvamus",
        target_date=timezone.localdate() + timedelta(days=7),
        actor=normal_matter.owner,
    )
    body = _detail(signed_in, normal_matter)
    flat = " ".join(body.split())

    # `+ Märge` asks `Tegevus` since docs/adr/0124; `Millal?` is `Muuda`'s.
    assert "Tegevus" in flat
    assert "Mida on vaja teha?" in flat
    assert "Millal?" in flat
    assert 'name="text"' in body

    for word in RETIRED_WORDS:
        assert word not in flat, f"the workspace still offers «{word}»"
    assert 'name="next_kind"' not in body
    assert 'name="next_date_semantics"' not in body
    assert "Mida kuupäev" not in flat
    assert "Täpsemalt…" not in flat


def test_the_questions_stopped_asking_for_both_at_once(signed_in, normal_matter):
    # An open step, because `Muuda` — the next-step form — is drawn beside a
    # task and is its only host since `+ Järgmine tegevus` left the launcher
    # (docs/adr/0097 §8.2).
    set_next_action(
        matter=normal_matter,
        text="Koosta arvamus",
        target_date=timezone.localdate() + timedelta(days=7),
        actor=normal_matter.owner,
    )
    body = _detail(signed_in, normal_matter)
    assert "Kirjelda, mis tegid ja mida teed edasi" not in body
    # The composer's one box asked for both at once, in one sentence. What is
    # left is one *activity* per save — `Tegevus` is either what was done or
    # what will be, and its day says which (docs/adr/0075 §2, docs/adr/0124) —
    # and editing the open step is its own form beside it.
    assert "Tegevus" in body
    assert "Kirjuta, mida tegid või mis on järgmine tegevus" in body
    assert "Mida on vaja teha?" in body


def test_the_current_step_shows_its_text_its_date_and_tehtud(signed_in, normal_matter, specialist):
    """State A of the approved design, asserted on the rendered page."""
    target = timezone.localdate() + timedelta(days=22)
    action = set_next_action(
        matter=normal_matter,
        text="Vaadata uus eelnõu versioon üle",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=target,
        actor=specialist,
    )

    body = _detail(signed_in, normal_matter)
    flat = " ".join(body.split())

    assert "Vaadata uus eelnõu versioon üle" in flat
    assert action.display_date in flat
    # **Not a bare «Tehtud».** State A is the task, its date, and the box that
    # finishes it by recording what was done (docs/adr/0075 §3) — behind the
    # `✓ Tehtud` disclosure since docs/adr/0133 §4, which opens it and
    # completes nothing on its own.
    assert "Mida tegid?" in flat
    assert "Tehtud</button>" not in flat
    for word in RETIRED_WORDS:
        assert word not in flat

    row = _jargmiseks_row(body)
    for word in RETIRED_DATE_WORDS:
        assert word not in row


def test_an_overdue_step_still_reads_as_late(signed_in, normal_matter, specialist):
    """Without the word «TÄHTAEG» in front of it."""
    set_next_action(
        matter=normal_matter,
        text="Saata kiri",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() - timedelta(days=6),
        actor=specialist,
    )
    body = _detail(signed_in, normal_matter)
    flat = " ".join(body.split())

    assert "curact--overdue" in body
    assert "curact__date--overdue" in body
    assert "6 p" in flat
    assert "TÄHTAEG MÖÖDAS" not in flat


def test_the_empty_state_stays_quiet(signed_in, normal_matter):
    body = _detail(signed_in, normal_matter)
    assert "Järgmine samm on määramata" in body
    # And nothing else. «Määra allpool ↓» pointed at a composer that now asks
    # `Järgmiseks` by name one row below it (ADR 0052 §13).
    assert "Määra allpool" not in body


# ---------------------------------------------------------------------------
# Sildid: retired from the page, untouched in the database
# ---------------------------------------------------------------------------


def test_sildid_are_gone_from_the_teema_detail_page(signed_in, normal_matter):
    tag = factories.TagFactory(name_et="Käibemaks")
    normal_matter.tags.add(tag)

    body = _detail(signed_in, normal_matter)
    assert "Sildid" not in body
    assert "Silte ei ole." not in body
    assert "Käibemaks" not in body


def test_reading_the_page_does_not_touch_stored_tags(signed_in, normal_matter):
    """A UI retirement, not a data migration (ADR 0052 §10)."""
    tag = factories.TagFactory(name_et="Käibemaks")
    normal_matter.tags.add(tag)

    _detail(signed_in, normal_matter)

    normal_matter.refresh_from_db()
    assert list(normal_matter.tags.all()) == [tag]


def test_muu_valdkond_left_the_teema_rail_without_leaving_the_record(signed_in, normal_matter):
    """UI retirement, not a data change.

    The approved target's `Teema andmed` was four rows — `Teemaviide`,
    `Menetlusliik`, `Saatja`, `Kellele` — and every one of them answered a
    question somebody asks mid-sentence. `Muu valdkond` is a correction to how
    the file was classified, which is `Muuda teemat` work
    (TEEMA_TARGET_SPEC §G.1, docs/adr/0074 §17).

    Two of the four have since gone, for a different reason than `Muu
    valdkond`: the two Teema forms stopped asking about `Menetlusliik` and the
    Matter-level `Kellele`, and a read-only rail row is the easiest place for a
    withdrawn question to survive its own removal (docs/adr/0097 §3, §4).

    The columns, the values and the audit rows are all untouched — which is
    what this test is about, and is as true of the two that left as of the one
    it was written for.
    """
    normal_matter.policy_area_other = "Riigihanked ja ehitus"
    normal_matter.save(update_fields=["policy_area_other"])

    body = _detail(signed_in, normal_matter)
    rail = body[body.index('id="teema-andmed"') :]
    rail = rail[: rail.index("</aside>")]

    assert "Muu valdkond" not in rail
    assert "Andmeklass" not in rail
    assert "Märgi testandmeteks" not in rail
    assert "Saabus" not in rail
    assert "Saatja" in rail
    # `Teemaviide` left the rail in the owner's compact round (2026-10-07).
    for gone in ("Teemaviide", "Menetlusliik", "Kellele"):
        assert gone not in rail
    # Stored, and unchanged by any of that.
    normal_matter.refresh_from_db()
    assert normal_matter.policy_area_other == "Riigihanked ja ehitus"

    normal_matter.refresh_from_db()
    assert normal_matter.policy_area_other == "Riigihanked ja ehitus"


def test_muu_valdkond_is_still_editable_in_place(signed_in, normal_matter):
    response = signed_in.post(
        reverse(
            "matters:update_field",
            kwargs={"pk": normal_matter.pk, "field": "policy_area_other"},
        ),
        {"policy_area_other": "Riigihanked"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200, response.content.decode()[:1000]
    normal_matter.refresh_from_db()
    assert normal_matter.policy_area_other == "Riigihanked"


# ---------------------------------------------------------------------------
# Authorization is unchanged
# ---------------------------------------------------------------------------


def test_a_reader_sees_the_step_but_not_tehtud_and_not_the_composer(
    client, reader, normal_matter, specialist
):
    set_next_action(
        matter=normal_matter,
        text="Vaadata uus versioon üle",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() + timedelta(days=3),
        actor=specialist,
    )
    client.force_login(reader)
    body = _detail(client, normal_matter)

    assert "Vaadata uus versioon üle" in body
    assert "Tehtud" not in body
    assert 'name="next_text"' not in body
    assert 'id="teema-koostaja"' not in body
