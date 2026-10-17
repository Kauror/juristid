"""`Lisa tagasiside` drafts, `Tehtud` and the stated provenance across saves.

Two defects of the 2026-10-17 audit, each found only in a running page:

* **JQA-002** — a `Seisukoht` typed on a Teema with no round was carried
  across `Alusta kaasamist` as the old, round-less form. `Tehtud` on the new
  wait then found no `Seotud kaasamine` to choose, and the reply was stored
  standalone while the wait stayed open. The panel's draft now belongs to the
  set of rounds it offered (`data-draft-state`), so a draft written against
  other choices is told as «Salvestamata sisu» instead of carried; and
  `Tehtud` never acts on a form that cannot name its round — it falls back to
  the server's own `?ava=tagasiside&kaasamine=…`.
* **JQA-004** — after a refused save with a round linked, the re-rendered form
  marked «Saadeti Kojale» as implied by the round even when the person had
  chosen it, so clearing the round unchecked it and dropped the `Liige` tick.
  The implied marker now travels with the post (docs/adr/0170 §3: cleared with
  the round «unless the person chose it»).

What is stored is read from the server's own database, because the defect was
that the page looked right while the row was wrong.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    REPOSITORY_ROOT,
    SAADETI_KOJALE,
    SANDRA,
    create_matter,
    feedback_form,
    open_add_panel,
    open_feedback_form,
    sign_in,
    unique_title,
    wait_for_htmx,
)

pytestmark = pytest.mark.e2e

#: The rounds and positions of the Matter `E2E_MATTER`, as one JSON line.
_STORED_SCRIPT = (
    "import json, os;"
    "from app.matters.models import MatterEngagement as E, MatterExternalPosition as P;"
    "m = os.environ['E2E_MATTER'];"
    "print(json.dumps({"
    "'rounds': {str(r.pk): r.feedback_closed_at is not None"
    " for r in E.objects.filter(matter_id=m)},"
    "'positions': [{'provenance': p.provenance,"
    " 'engagement': str(p.engagement_id) if p.engagement_id else None,"
    " 'member': p.source_is_member, 'summary': p.summary}"
    " for p in P.objects.filter(matter_id=m)]}))"
)


def _stored(page) -> dict:
    """What the server's database holds for the Teema open in ``page``."""
    matter_id = re.search(r"/teemad/([0-9a-f-]{36})/", page.url).group(1)
    result = subprocess.run(  # noqa: S603
        [sys.executable, "manage.py", "shell", "-c", _STORED_SCRIPT],
        cwd=REPOSITORY_ROOT,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings", "E2E_MATTER": matter_id},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, "\n".join(["stdout:", result.stdout, "stderr:", result.stderr])
    return json.loads(result.stdout.strip().splitlines()[-1])


def _day(days: int) -> str:
    day = date.today() + timedelta(days=days)
    return f"{day.day:02d}.{day.month:02d}.{day.year}"


def _start_round(page, *, deadline: str = "") -> None:
    """`+ Kaasamine · Alusta kaasamist`, saved — with a wait's deadline if given."""
    open_add_panel(page, "kaasamine-alusta")
    panel = page.locator("#kaasamine-alusta")
    panel.locator("[name=audience]").fill("Liikmed (e-kiri)")
    if deadline:
        panel.locator("[name=feedback_deadline]").fill(deadline)
    with page.expect_response(lambda r: "/lisa/kaasamine/" in r.url) as answer:
        panel.locator("button[type=submit]").click()
    assert answer.value.status == 200
    wait_for_htmx(page)


def _save_feedback(page) -> int:
    with page.expect_response(lambda r: "/lisa/kaasamise-tagasiside/" in r.url) as answer:
        feedback_form(page).get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)
    return answer.value.status


def _received(form):
    return form.locator(f'input[name="provenance"][value="{SAADETI_KOJALE}"]')


def _state_received(form) -> None:
    form.locator(f'label.chip:has(input[name="provenance"][value="{SAADETI_KOJALE}"])').click()


@pytest.fixture
def teema(page, base_url):
    sign_in(page, base_url, SANDRA)
    return create_matter(page, base_url, unique_title("Tagasiside mustand"))


def test_tehtud_after_the_first_round_links_the_reply_and_ends_the_wait(page, teema):
    """JQA-002, the audit's exact sequence."""
    typed = "Liikme vastus kaasamisele."
    # 1. A Seisukoht typed on a Teema with no round, not saved.
    open_add_panel(page, "kaasamine-tagasiside")
    feedback_form(page).locator("[name=summary]").fill(typed)

    # 2. The first round, with a wait.
    _start_round(page, deadline=_day(14))
    told = page.locator("section.unsaved").all_text_contents()

    # 3. «Tehtud» on the new wait.
    opener = page.locator("#praegune-ootused [data-open-feedback]")
    expect(opener).to_have_count(1)
    round_id = opener.get_attribute("data-open-feedback")
    opener.click()
    page.wait_for_load_state()
    wait_for_htmx(page)
    form = feedback_form(page)
    linked = page.evaluate(
        "() => { const s = document.querySelector("
        "'#kaasamine-tagasiside select[name=engagement]'); return s ? s.value : null; }"
    )

    # 4. The person writes (or pastes back) the reply and saves.
    form.locator("[name=summary]").fill(typed)
    if not _received(form).is_checked():
        _state_received(form)
    assert _save_feedback(page) == 200

    stored = _stored(page)
    assert [(p["provenance"], p["engagement"]) for p in stored["positions"]] == [
        (SAADETI_KOJALE, round_id)
    ]
    assert stored["rounds"] == {round_id: True}, "the reply finishes the round's wait"
    assert linked == round_id, "«Tehtud» opened the form with its round chosen"
    # The round-less draft was not carried silently: its text was told.
    assert any(typed in text for text in told), told
    expect(page.locator("#praegune-ootused [data-feedback-wait]")).to_have_count(0)


def test_a_draft_survives_an_unrelated_save_while_the_rounds_stay_the_same(page, teema):
    """The control: the round choices did not change, so the draft is carried."""
    _start_round(page)
    typed = "Pooleli liikme vastus."
    form = open_feedback_form(page, SAADETI_KOJALE)
    form.locator("[name=summary]").fill(typed)

    open_add_panel(page, "marge-toovoit")
    page.fill("#id_victory_change", "Ministeerium muutis sõnastust")
    with page.expect_response(lambda r: "/lisa/toovoit/" in r.url) as answer:
        page.locator("#marge-toovoit button[type=submit]").click()
    assert answer.value.status == 200
    wait_for_htmx(page)

    expect(page.locator("section.unsaved")).to_have_count(0)
    form = feedback_form(page)
    expect(form.locator("[name=summary]")).to_have_value(typed)
    expect(_received(form)).to_be_checked()

    # Still a working form: `Tehtud` links its round and the save ends the wait.
    round_id = page.locator("#praegune-ootused [data-open-feedback]").get_attribute(
        "data-open-feedback"
    )
    page.locator("#praegune-ootused [data-open-feedback]").click()
    expect(form.locator("[name=engagement]")).to_have_value(round_id)
    assert _save_feedback(page) == 200

    stored = _stored(page)
    assert [(p["engagement"], p["summary"]) for p in stored["positions"]] == [(round_id, typed)]
    assert stored["rounds"] == {round_id: True}


@pytest.mark.parametrize("stale", ["no-round-field", "round-not-offered"])
def test_tehtud_on_a_form_that_cannot_name_its_round_asks_the_server(page, teema, stale):
    """A form that cannot link the round is never treated as linked.

    The draft state keeps such a form from being carried; this is the guard
    behind it. A page that still holds one is sent to the server's own
    `?ava=tagasiside&kaasamine=…`, which opens the form with that round chosen.
    """
    _start_round(page, deadline=_day(7))
    open_add_panel(page, "kaasamine-tagasiside")
    script = {
        "no-round-field": "() => document.querySelector("
        "'#kaasamine-tagasiside select[name=engagement]').closest('label').remove()",
        "round-not-offered": "() => document.querySelectorAll("
        "'#kaasamine-tagasiside select[name=engagement] option')"
        ".forEach(o => { if (o.value) o.remove(); })",
    }[stale]
    page.evaluate(script)

    opener = page.locator("#praegune-ootused [data-open-feedback]")
    round_id = opener.get_attribute("data-open-feedback")
    opener.click()
    page.wait_for_url(re.compile(rf"ava=tagasiside&kaasamine={round_id}"))
    page.wait_for_load_state("domcontentloaded")

    form = feedback_form(page)
    expect(form.locator("[name=engagement]")).to_have_value(round_id)
    expect(_received(form)).to_be_checked()


def test_a_stated_saadeti_kojale_and_liige_outlive_a_refusal_and_a_cleared_round(page, teema):
    """JQA-004: the person's own answer is not undone with the round."""
    _start_round(page)
    form = open_feedback_form(page, SAADETI_KOJALE)
    form.locator("[data-feedback-member] label.chip").click()
    form.locator("[name=engagement]").select_option(index=1)
    form.locator("[name=summary]").fill("Liikme tagasiside, ringist lahti.")
    form.locator("[name=url]").fill("pole link")
    assert _save_feedback(page) == 400

    form = feedback_form(page)
    form.locator("[name=engagement]").select_option(value="")
    form.locator("[name=url]").fill("")
    kept = {
        "received": _received(form).is_checked(),
        "member": form.locator('input[name="source_is_member"]').is_checked(),
    }
    assert _save_feedback(page) == 200

    stored = _stored(page)
    assert [(p["provenance"], p["engagement"], p["member"]) for p in stored["positions"]] == [
        (SAADETI_KOJALE, None, True)
    ]
    assert kept == {"received": True, "member": True}


def test_a_saadeti_kojale_implied_by_the_round_is_still_cleared_after_a_refusal(page, teema):
    """The other half of docs/adr/0170 §3: an answer the round implied goes with it."""
    _start_round(page)
    open_add_panel(page, "kaasamine-tagasiside")
    form = feedback_form(page)
    form.locator("[name=engagement]").select_option(index=1)
    expect(_received(form)).to_be_checked()
    form.locator("[name=summary]").fill("Vastus ringile.")
    form.locator("[name=url]").fill("pole link")
    assert _save_feedback(page) == 400

    form = feedback_form(page)
    expect(_received(form)).to_be_checked()
    form.locator("[name=engagement]").select_option(value="")
    expect(_received(form)).not_to_be_checked()
    expect(form.locator("[data-feedback-member]")).to_be_hidden()
