"""What closing a Teema no longer asks, and the canonical rules it never relaxed.

`Lõpeta teema` and the closing composer are gone: a Matter ends through a
terminal `Hetkeseis` (docs/adr/0131 §10–11, `tests/test_stage_episodes.py`), and
the composer that once carried a closure, its opinion, a victory and a
commencement in one save was retired with ENG-050A2 (docs/adr/0075, amended).
Each of those is its own operation now — `+ Koja arvamus`, `+ Töövõit`,
`+ Jõustumine` — tested where it lives; the recipient rules the closing save
proved are stated on `resolve_recipients` in `tests/test_recipient_resolution.py`.

What stays here is what closing never changed: the page asks none of the retired
questions, the successor disposition is not offered and the domain still refuses
a successor on the wrong one, and a SENT opinion always carries its exact final
evidence.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from app.matters.forms import CLOSURE_CHOICES
from app.submissions.enums import SubmissionStatus
from app.workflow.enums import Disposition
from tests import factories
from tests.refusals import refused

pytestmark = pytest.mark.django_db


REMOVED_FIELDS = [
    "closure_reason",
    # The `Lõpeta see teema` confirmation. Answering the closing section is the
    # request to close; a seventh control gating the six above it is where the
    # pilot lost a whole closure without being told (pilot QA F-02,
    # tests/test_pilot_p1_workflows.py).
    "close_matter",
    "successor",
    "final_version",
    "final_title",
    "final_channel",
    "final_reference",
    "victory_title",
    "victory_detail",
    # Retired by the approved target: closing a file is not a claim that an
    # opinion was sent, and a win is recorded by `+ Töövõit` whether or not the
    # file is closing (docs/adr/0074 §10).
    "final_file",
    "final_sent_on",
    "final_recipients",
    "final_recipient_names",
    "work_victory",
    "victory_effective_on",
]


def _composer_html(client, matter) -> str:
    url = reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    return client.get(url).content.decode()


@pytest.mark.parametrize("name", REMOVED_FIELDS)
def test_the_closing_section_no_longer_renders_a_retired_field(signed_in, normal_matter, name):
    assert f'name="{name}"' not in _composer_html(signed_in, normal_matter)


def test_the_disposition_that_needs_a_successor_is_not_offered():
    """`Jätkub teise teema all` is the one closure reason whose truth depends on
    a second record, and this workflow does not ask for one. It is withdrawn
    from the offer rather than posted with a null successor (§3)."""
    offered = {value for value, _label in CLOSURE_CHOICES}

    assert Disposition.SUPERSEDED.value not in offered
    assert Disposition.COMPLETED.value in offered
    # And the domain capability is untouched, ready for an operation that asks.
    assert hasattr(factories.MatterFactory.build(), "superseded_by")


def test_the_domain_still_refuses_a_successor_on_the_wrong_disposition(normal_matter, specialist):
    """Withdrawing a choice from one form must not have relaxed the rule."""
    from app.matters.services import close_matter

    successor = factories.MatterFactory(owner=specialist)
    with refused("Järglase saab määrata ainult siis, kui töö jätkub teise teema all."):
        close_matter(
            matter=normal_matter,
            disposition=Disposition.COMPLETED,
            successor=successor,
            actor=specialist,
        )


def test_a_sent_opinion_always_carries_its_exact_final_evidence(
    normal_matter, specialist, organisation
):
    """The invariant the old form's four questions were protecting, stated where
    it is actually enforced.

    `mark_submission_sent` refuses a submission with no final version, so there
    is no path — form, importer or shell — that marks an opinion sent without the
    bytes that went out. The retired «Lae saadetud fail» refusal was a *form*
    rule layered on top of this one; removing the form did not remove this."""
    from app.submissions.services import create_submission, mark_submission_sent

    submission = create_submission(
        matter=normal_matter,
        title=normal_matter.title,
        actor=specialist,
        recipients=[organisation],
    )
    assert submission.final_version is None

    with refused("Saadetud arvamus vajab täpset lõplikku tõendit. Lisa või vali saadetud fail."):
        mark_submission_sent(submission=submission, actor=specialist, sent_at=timezone.now())

    submission.refresh_from_db()
    assert submission.status != SubmissionStatus.SENT
