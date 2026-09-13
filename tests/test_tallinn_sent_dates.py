"""A submission's business date is the Tallinn day, not the UTC one.

`Submission.sent_at` is an aware timestamp, and PostgreSQL hands it back in UTC.
Three application boundaries used to derive a *date* from it with plain
``.date()``, which reads whatever day it was in UTC — not the day the lawyer who
sent it would name.

The gap is three hours in summer and two in winter, so it only bites late in the
evening. An opinion sent at 01:30 on 13 September Tallinn time is 22:30 on the
12th in UTC, and it appeared on the related-materials card, in the background
list and in the Arvamused export dated 12 September: a day that contradicts the
timestamp shown two lines above it on the submission's own page, where the
template's ``|date`` filter had always localised correctly.

Every test below uses that same crossing timestamp on purpose. A test written
at midday would pass against both the defect and the fix and prove nothing.

The fix is `timezone.localdate`, which is the idiom this codebase already uses
everywhere it needs today's date, and which reads the business timezone from
``settings.TIME_ZONE`` (``Europe/Tallinn``) rather than from an offset written
into the call site. Nothing about how `sent_at` is stored changed.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from django.urls import reverse
from django.utils import timezone

from app.documents.services import add_evidence_version
from app.matters.models import Matter
from app.related_materials import engine, services
from app.related_materials.selectors import related_materials_for
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from tests import factories

pytestmark = pytest.mark.django_db


#: 01:30 on 13 September in Tallinn, stated in UTC exactly as PostgreSQL holds
#: it. Europe/Tallinn is UTC+3 in September, so every correct reading of this
#: moment is the 13th and every UTC reading of it is the 12th.
SENT_AT_UTC = dt.datetime(2026, 9, 12, 22, 30, tzinfo=dt.UTC)
TALLINN_DAY = dt.date(2026, 9, 13)
UTC_DAY = dt.date(2026, 9, 12)


def test_the_fixture_really_crosses_the_boundary():
    """The premise, pinned: without this the three tests below prove nothing."""
    assert SENT_AT_UTC.date() == UTC_DAY
    assert timezone.localdate(SENT_AT_UTC) == TALLINN_DAY
    assert timezone.get_current_timezone_name() == "Europe/Tallinn"


def _matter(owner: Any, title: str, *, number: int, year: int = 2026) -> Matter:
    return factories.MatterFactory(
        owner=owner, title=title, reference_year=year, reference_number=number
    )


def _sent_opinion(matter: Matter, title: str, *, sent: dt.datetime) -> Submission:
    """A canonical, sent Submission with the evidence the constraint requires."""
    document = factories.DocumentFactory(matter=matter)
    version = add_evidence_version(
        document=document,
        content=b"%PDF-1.4\n" + title.encode("utf-8"),
        original_filename="arvamus.pdf",
        mime_type="application/pdf",
    )
    return factories.SubmissionFactory(
        matter=matter,
        title=title,
        status=SubmissionStatus.SENT,
        sent_at=sent,
        final_version=version,
    )


# ---------------------------------------------------------------------------
# Seotud materjalid — the suggested opinion
# ---------------------------------------------------------------------------


def test_a_suggested_opinion_carries_its_tallinn_date(specialist):
    source = _matter(specialist, "Pakendiseaduse muutmine 2024", number=871, year=2024)
    current = _matter(specialist, "Pakendiseaduse muutmine 2026", number=872)
    _sent_opinion(source, "Koja arvamus pakendiseaduse muutmise kohta", sent=SENT_AT_UTC)

    (item,) = engine.suggestions_for(current, specialist).materials

    assert item.kind == engine.KIND_SUBMISSION
    assert item.date == TALLINN_DAY
    assert item.date != UTC_DAY


# ---------------------------------------------------------------------------
# Seotud materjalid — the confirmed background selection
# ---------------------------------------------------------------------------


def test_a_background_opinion_carries_its_tallinn_date(specialist):
    source = _matter(specialist, "Pakendiseaduse muutmine 2024", number=873, year=2024)
    current = _matter(specialist, "Pakendiseaduse muutmine 2026", number=874)
    opinion = _sent_opinion(source, "Koja arvamus pakendiseaduse muutmise kohta", sent=SENT_AT_UTC)
    services.add_background_submission(matter=current, submission=opinion, actor=specialist)

    (item,) = related_materials_for(current, specialist).background

    assert item.kind == "SUBMISSION"
    assert item.date == TALLINN_DAY
    assert item.date != UTC_DAY


# ---------------------------------------------------------------------------
# Statistika — the Arvamused export
# ---------------------------------------------------------------------------


def test_the_submissions_export_writes_the_tallinn_date(client, department_head):
    matter = _matter(department_head, "Käibemaksuseaduse muutmine", number=875)
    _sent_opinion(matter, "Koja arvamus käibemaksuseaduse kohta", sent=SENT_AT_UTC)
    client.force_login(department_head)

    response = client.get(
        reverse("reporting:export", kwargs={"slug": "arvamused"}), {"periood": "koik"}
    )
    assert response.status_code == 200
    body = b"".join(response.streaming_content).decode("utf-8-sig")

    header, *rows = body.strip().splitlines()
    assert header.split(";")[0] == "saadetud"
    (row,) = [line for line in rows if "käibemaksuseaduse" in line]
    assert row.split(";")[0] == TALLINN_DAY.isoformat()
    assert row.split(";")[0] != UTC_DAY.isoformat()


# ---------------------------------------------------------------------------
# And the defect cannot come back by the door it came in
# ---------------------------------------------------------------------------


def test_no_application_code_derives_a_date_from_sent_at_directly():
    """`submission.sent_at.date()` is the exact spelling that was wrong.

    A grep rather than a behavioural assertion, because the next call site to
    want a sent date will be written by somebody who has not read this module,
    and ``.date()`` is the obvious thing to reach for. `timezone.localdate` is
    the idiom; this is what says so at the moment the wrong one is added.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    offenders = [
        f"{path.relative_to(root.parent)}:{number}"
        for path in sorted(root.rglob("*.py"))
        if "migrations" not in path.parts
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "sent_at.date()" in line
    ]

    assert offenders == []
