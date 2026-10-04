"""A completion record is never less restricted than the action it completes (docs/adr/0138).

`PRAEGUNE TEGEVUS` — `complete_current_action` — writes an `Entry` saying what
was done for the one open `NextAction` the form named, and completes that
action, in one operation. The `Entry` *is* the completion record of that action,
so an action restricted below its Matter has its completion `Entry` restricted
with it. Before this, the `Entry` was created with no override and inherited the
Matter: a reader who could not see the task could read, in `Teema käik` and in
search, the account of how it was done.

Asserted through the real operation, never by building the rows by hand:

* **restricted action** — the `Entry` is `RESTRICTED`; a reader who may see the
  Matter but not restricted records finds it on no surface (its own
  `visible_to`, `Teema käik`, the page, search, counts), and the specialist does;
* **with a file** — this rule and docs/adr/0137 compose: the `Entry` is
  restricted by this one, and its file by 0137 through the `Entry`;
* **the controls** — an unrestricted action's `Entry` and file stay ordinary; a
  restricted Matter needs no redundant override;
* **copied, not joined** — relaxing the action later leaves the `Entry`
  restricted, and an `Entry` written before an action was restricted is not
  rewritten;
* **unchanged contracts** — a stale action still refuses the whole save, and a
  `Tööplaan`-linked action gets the same rule.
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from app.core.enums import Visibility
from app.documents.models import Document
from app.matters.models import Entry
from app.matters.timeline import matter_timeline
from app.matters.workspace import STALE_ACTION_REFUSAL, complete_current_action
from app.search.services import result_count
from app.workflow import plan as work_plan
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction
from app.workflow.services import set_next_action_for_new_work
from tests import factories
from tests.refusals import refused

pytestmark = pytest.mark.django_db

#: Distinctive words, so search answers for this test's rows only.
BODY_WORD = "Salastatudtöö"
FILE_WORD = "Salastatudfail"


def _body(extra: str = "") -> str:
    return f"<p>{BODY_WORD} — lugesin eelnõu läbi{extra}.</p>"


def _pdf() -> SimpleUploadedFile:
    name = f"{FILE_WORD} lisa.pdf"
    return SimpleUploadedFile(name, f"%PDF-1.4 {name}".encode(), content_type="application/pdf")


#: The restricted action's own words, which no reader surface may print either.
ACTION_TEXT = "Salastatudsamm vaata eelnõu üle"


def _action(matter, actor, *, restricted: bool, text: str = ACTION_TEXT) -> NextAction:
    action = set_next_action_for_new_work(matter=matter, text=text, actor=actor)
    if restricted:
        # No panel offers a restriction; the shell, the admin and importers
        # write one — this is that row.
        NextAction.objects.filter(pk=action.pk).update(visibility_override=Visibility.RESTRICTED)
        action.refresh_from_db()
    return action


def _complete(matter, actor, action, *, uploads=(), body: str | None = None):
    return complete_current_action(
        matter=matter,
        author=actor,
        action_id=action.pk,
        body=body or _body(),
        uploads=list(uploads),
    )


def _timeline_entries(matter, user) -> set:
    page, _ = matter_timeline(matter=matter, user=user)
    return {item.entry.pk for item in page if item.entry is not None}


def _teema(client, user, matter) -> str:
    client.force_login(user)
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _dokumendid(client, user, matter) -> str:
    client.force_login(user)
    return client.get(
        reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    ).content.decode()


@pytest.fixture
def matter(specialist):
    return factories.MatterFactory(owner=specialist)


# ---------------------------------------------------------------------------
# A. A restricted action, no file
# ---------------------------------------------------------------------------


def test_a_restricted_actions_completion_entry_is_restricted(client, matter, specialist, reader):
    action = _action(matter, specialist, restricted=True)
    readers_entries_before = Entry.objects.visible_to(reader).filter(matter=matter).count()
    assert not NextAction.objects.visible_to(reader).filter(pk=action.pk).exists()

    entry = _complete(matter, specialist, action).entry

    assert matter.visibility == Visibility.NORMAL
    assert entry.visibility_override == Visibility.RESTRICTED

    # Not on any surface for a reader who may not see the action.
    assert not Entry.objects.visible_to(reader).filter(pk=entry.pk).exists()
    assert entry.pk not in _timeline_entries(matter, reader)
    readers_page = _teema(client, reader, matter)
    assert BODY_WORD not in readers_page
    assert "Salastatudsamm" not in readers_page
    assert result_count(query=BODY_WORD, user=reader) == 0
    assert Entry.objects.visible_to(reader).filter(matter=matter).count() == (
        readers_entries_before
    )

    # Still there for somebody who may see it.
    assert Entry.objects.visible_to(specialist).filter(pk=entry.pk).exists()
    assert entry.pk in _timeline_entries(matter, specialist)
    assert BODY_WORD in _teema(client, specialist, matter)
    assert result_count(query=BODY_WORD, user=specialist) >= 1

    action.refresh_from_db()
    assert action.status == ActionStatus.COMPLETED


# ---------------------------------------------------------------------------
# B. With a file: this rule and docs/adr/0137 compose
# ---------------------------------------------------------------------------


def test_a_restricted_actions_completion_file_is_restricted_through_the_entry(
    client, matter, specialist, reader
):
    action = _action(matter, specialist, restricted=True)
    readers_files_before = Document.objects.visible_to(reader).filter(matter=matter).count()

    result = _complete(matter, specialist, action, uploads=[_pdf()])
    (document,) = result.documents

    assert result.entry.visibility_override == Visibility.RESTRICTED
    assert document.visibility_override == Visibility.RESTRICTED

    readers_page = _teema(client, reader, matter)
    readers_files = _dokumendid(client, reader, matter)
    assert BODY_WORD not in readers_page
    assert document.title not in readers_page
    assert document.title not in readers_files
    assert Document.objects.visible_to(reader).filter(matter=matter).count() == (
        readers_files_before
    )
    assert result_count(query=BODY_WORD, user=reader) == 0
    assert result_count(query=FILE_WORD, user=reader) == 0

    assert BODY_WORD in _teema(client, specialist, matter)
    assert document.title in _dokumendid(client, specialist, matter)
    assert result_count(query=BODY_WORD, user=specialist) >= 1
    assert result_count(query=FILE_WORD, user=specialist) >= 1


# ---------------------------------------------------------------------------
# C, D. The controls
# ---------------------------------------------------------------------------


def test_an_unrestricted_actions_completion_stays_ordinary(client, matter, specialist, reader):
    action = _action(matter, specialist, restricted=False)

    result = _complete(matter, specialist, action, uploads=[_pdf()])
    (document,) = result.documents

    assert result.entry.visibility_override == ""
    assert document.visibility_override == ""
    assert result.entry.pk in _timeline_entries(matter, reader)
    assert BODY_WORD in _teema(client, reader, matter)
    assert document.title in _dokumendid(client, reader, matter)
    assert result_count(query=BODY_WORD, user=reader) >= 1


def test_a_restricted_matter_needs_no_redundant_override(specialist):
    matter = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    action = _action(matter, specialist, restricted=False)

    entry = _complete(matter, specialist, action).entry

    assert entry.visibility_override == ""
    assert entry.effective_visibility == Visibility.RESTRICTED


# ---------------------------------------------------------------------------
# E, F. Copied at creation, never joined and never rewritten
# ---------------------------------------------------------------------------


def test_relaxing_the_action_later_leaves_the_entry_restricted(matter, specialist, reader):
    action = _action(matter, specialist, restricted=True)
    entry = _complete(matter, specialist, action).entry

    NextAction.objects.filter(pk=action.pk).update(visibility_override="")

    entry.refresh_from_db()
    assert entry.visibility_override == Visibility.RESTRICTED
    assert not Entry.objects.visible_to(reader).filter(pk=entry.pk).exists()


def test_an_existing_entry_is_not_rewritten_when_its_action_is_restricted_later(
    matter, specialist, reader
):
    action = _action(matter, specialist, restricted=False)
    entry = _complete(matter, specialist, action).entry

    NextAction.objects.filter(pk=action.pk).update(visibility_override=Visibility.RESTRICTED)

    entry.refresh_from_db()
    assert entry.visibility_override == ""
    assert Entry.objects.visible_to(reader).filter(pk=entry.pk).exists()


# ---------------------------------------------------------------------------
# G. The stale-action contract is unchanged
# ---------------------------------------------------------------------------


def test_a_stale_restricted_action_refuses_the_whole_save(matter, specialist):
    stale = _action(matter, specialist, restricted=True, text="Vana samm")
    _complete(matter, specialist, stale, body=_body(" esimest korda"))
    current = _action(matter, specialist, restricted=False, text="Uus samm")
    entries_before = Entry.objects.filter(matter=matter).count()
    files_before = Document.objects.filter(matter=matter).count()

    # Refused because the named step is no longer the current one — not merely
    # because something refused.
    with refused(STALE_ACTION_REFUSAL):
        _complete(matter, specialist, stale, uploads=[_pdf()], body=_body(" teist korda"))

    assert Entry.objects.filter(matter=matter).count() == entries_before
    assert Document.objects.filter(matter=matter).count() == files_before
    current.refresh_from_db()
    assert current.status == ActionStatus.OPEN


# ---------------------------------------------------------------------------
# H. A Tööplaan-linked action gets the same rule
# ---------------------------------------------------------------------------


def test_a_restricted_plan_step_actions_completion_is_restricted(matter, specialist, reader):
    work_plan.seed_standard_plan(matter=matter, actor=specialist)
    step = work_plan.plan_steps_of(matter)[0]
    action = work_plan.activate_plan_step(matter=matter, step=step, actor=specialist)
    NextAction.objects.filter(pk=action.pk).update(visibility_override=Visibility.RESTRICTED)
    action.refresh_from_db()
    assert action.plan_step_id == step.pk

    entry = _complete(matter, specialist, action).entry

    assert entry.visibility_override == Visibility.RESTRICTED
    assert not Entry.objects.visible_to(reader).filter(pk=entry.pk).exists()
    assert result_count(query=BODY_WORD, user=reader) == 0
    step.refresh_from_db()
    assert step.state == "COMPLETED"


# ---------------------------------------------------------------------------
# The next step chosen in the same save is new work, not the completion record
# ---------------------------------------------------------------------------


def test_the_next_step_chosen_in_the_same_save_keeps_the_ordinary_creation_rule(matter, specialist):
    """Not decided here (docs/adr/0138 §4): no restriction is carried to future work.

    The next step is written by `set_next_action_for_new_work` exactly as it is
    from any other door, with no override. Whether new work should inherit the
    restriction of the work it follows is a separate product decision; this
    pins today's answer so a change to it is a visible one.
    """
    action = _action(matter, specialist, restricted=True)

    result = complete_current_action(
        matter=matter,
        author=specialist,
        action_id=action.pk,
        body=_body(),
        next_text="Saada kommentaarid ministeeriumile",
    )

    assert result.entry.visibility_override == Visibility.RESTRICTED
    assert result.action is not None
    assert result.action.visibility_override == ""


@pytest.mark.parametrize(
    ("override", "expected"),
    [("", ""), (Visibility.NORMAL.value, ""), (Visibility.RESTRICTED.value, "RESTRICTED")],
    ids=["empty", "normal", "restricted"],
)
def test_the_override_a_completion_entry_is_created_with(override, expected):
    from app.matters.workspace import completion_visibility_override

    assert completion_visibility_override(NextAction(visibility_override=override)) == expected
