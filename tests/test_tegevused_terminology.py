"""One name for the Teema page's history, one for the button that adds work (owner, 2026-10-19).

* `Tegevused` is the section's name since docs/adr/0140 §7. `Kõik muudatused`
  still sent the reader to «Teema käik», and so did the removal and withdrawal
  confirmations, the document page, two refusals and the empty list. No text a
  reader meets may name «Teema käik» any more; code comments and stored audit
  history keep the old word where it explains history.
* `+ Lisa tegevus` is the button that adds an activity. The 19.10 release note
  sent a planned meeting to «Järgmine tegevus», which is a field of the
  completion form, not a button. The concept «järgmine tegevus» is untouched.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

from app.core.release_notes import load_release_notes
from app.documents import services as document_services
from app.submissions.forms import SentOpinionEditForm
from tests import factories

TEMPLATES = Path(settings.BASE_DIR) / "templates"
OLD_TERM = re.compile(r"teema käi(k|gu|gus|gust|ku)\b", re.IGNORECASE)
_COMMENT_BLOCK = re.compile(r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}", re.DOTALL)
_COMMENT_LINE = re.compile(r"{#.*?#}", re.DOTALL)


def _rendered_text(source: str) -> str:
    """A template with every Django comment taken out — what can reach a page."""
    return _COMMENT_LINE.sub("", _COMMENT_BLOCK.sub("", source))


def test_no_template_shows_the_old_name():
    offenders = [
        str(path.relative_to(TEMPLATES))
        for path in TEMPLATES.rglob("*.html")
        if OLD_TERM.search(_rendered_text(path.read_text(encoding="utf-8")))
    ]

    assert offenders == []


def test_the_refusals_and_help_name_tegevused():
    for text in (
        document_services.SENT_OPINION_EVIDENCE,
        document_services.DOCUMENT_IS_LAST_POSITION_SOURCE,
    ):
        assert not OLD_TERM.search(text)
        assert "Tegevused →" in text
    help_text = str(SentOpinionEditForm.base_fields["summary"].help_text)
    assert "«Tegevused»" in help_text


def test_release_notes_since_the_rename_use_the_current_names():
    """The rename reached production on 5.10; what was written later uses it."""
    from datetime import date

    for day in load_release_notes():
        if day.date <= date(2026, 10, 5):
            continue
        for line in day.changes:
            assert not OLD_TERM.search(line), (day.date, line)
            assert "«Järgmine tegevus» või «Oluline tähtaeg»" not in line, (day.date, line)
    # The entry of the release that renamed the button (19.10) names it. Pinned to
    # that day: a later entry describes its own release, not this one.
    renamed = next(day for day in load_release_notes() if day.date == date(2026, 10, 19))
    assert any("«+ Lisa tegevus»" in line for line in renamed.changes)


@pytest.mark.django_db
def test_koik_muudatused_points_at_tegevused(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    body = signed_in.get(
        reverse("matters:matter_changes", kwargs={"pk": matter.pk})
    ).content.decode()

    assert "<em>Tegevused</em>" in body
    assert not OLD_TERM.search(body)
