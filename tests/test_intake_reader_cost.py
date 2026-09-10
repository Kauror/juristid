"""What one intake costs, measured rather than assumed.

Two questions the brief asks for numbers on, and both of them are the kind
that only get worse quietly:

* **§20, latency.** This is interactive work — somebody is at a form watching
  «Loen faili…» — so «how long from staging to an answer» is a product figure
  and not an engineering curiosity.
* **§21, write amplification.** The production incident of 2026-09-10 was a
  storage one: every small write to the parity-protected array is a
  read-modify-write on a saturated USB disk, so *how many statements* one
  intake costs matters more here than it would anywhere else, and «polling
  writes nothing» is the property that decides whether a browser asking every
  1.2 s is free or expensive.

**These are ceilings, not benchmarks.** A timing assertion on CI is a flaky
test waiting to happen, so the bounds are generous — an order of magnitude
above what this machine measures — and what they catch is a change of *shape*:
a parse that moved into the request, a poll that started writing, a budget that
stopped bounding. The measured numbers live in the PR description; the numbers
here are the ones that must never be exceeded.
"""

from __future__ import annotations

import time
from io import StringIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.documents.enums import ExtractionState
from app.matters.staging import MatterIntakeFile
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

STAGE = reverse("matters:intake_stage")
STATUS = reverse("matters:intake_status")
PDF = "application/pdf"

LETTER = """Näidisministeerium

Eesti Kaubandus-Tööstuskoda

Pakendiseaduse muutmise seaduse eelnõu kooskõlastamiseks

Saadame kooskõlastamiseks pakendiseaduse muutmise seaduse eelnõu. Eelnõuga
muudetakse pakendite ja jäätmete käitlemise korda ning keskkonnatasu määrasid.
Palume esitada arvamus hiljemalt 18. septembriks 2026.
"""

#: Generous by an order of magnitude. What these catch is a parse that moved
#: into the request or an analysis that stopped being bounded, not a slow
#: afternoon on a shared runner.
STAGE_SECONDS = 5.0
READ_SECONDS = 15.0
POLL_SECONDS = 5.0

#: Statements one poll may cost. The panel reads staged rows and computes
#: suggestions from JSON already on them; the catalogues are two queries and
#: the vocabulary one. A poll that started writing, or that grew a query per
#: file, shows up here immediately.
POLL_QUERY_CEILING = 40


def stage(client, *files):
    response = client.post(STAGE, {"files": list(files)})
    assert response.status_code in (200, 400), response.status_code
    return response.context["intake_session"]


def upload(name: str = "kaaskiri.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, corpus.text_pdf([LETTER]), content_type=PDF)


# ---------------------------------------------------------------------------
# Latency
# ---------------------------------------------------------------------------


def test_staging_a_file_does_not_parse_it(signed_in, evidence_root):
    """§20, and the property the whole architecture rests on.

    The request that receives a file validates it, stores the bytes and
    returns. It must not open a PDF: a parse in a gunicorn worker is a worker
    that is not serving anybody, and on a 200-page scan it is a worker gone for
    minutes (docs/adr/0064).

    Asserted twice — the row is still PENDING, *and* the request was fast —
    because either alone can be satisfied by an accident.
    """
    started = time.monotonic()
    session = stage(signed_in, upload())
    elapsed = time.monotonic() - started

    staged = MatterIntakeFile.objects.get(session=session)
    assert staged.extraction_state == ExtractionState.PENDING
    assert not staged.text
    assert elapsed < STAGE_SECONDS, f"staging took {elapsed:.2f}s"


def test_a_whole_envelope_is_read_in_one_pass(signed_in, evidence_root):
    """§20 — upload to autofill, for the shape `Uus teema` actually receives.

    Four files is a covering letter, a draft, a memorandum and an annex. One
    `--once` run reads all four, because `BATCH` is a whole envelope: a person
    who drops four files must not watch them arrive one poll at a time.
    """
    session = stage(
        signed_in,
        upload("kaaskiri.pdf"),
        upload("eelnou.pdf"),
        upload("seletuskiri.pdf"),
        upload("lisa.pdf"),
    )

    started = time.monotonic()
    call_command("run_intake_reader", "--once", stdout=StringIO())
    elapsed = time.monotonic() - started

    states = set(
        MatterIntakeFile.objects.filter(session=session).values_list("extraction_state", flat=True)
    )
    assert states == {ExtractionState.DONE}
    assert elapsed < READ_SECONDS, f"reading four files took {elapsed:.2f}s"


def test_the_answer_is_on_the_page_as_soon_as_the_reader_has_it(signed_in, evidence_root):
    """§20 — the last hop, which is the one the person actually sees.

    The reader writes to PostgreSQL and the next poll renders it. Nothing has
    to be reloaded, nothing has to be pressed, and no second pass is needed to
    turn a finished read into a suggestion.
    """
    session = stage(signed_in, upload())
    call_command("run_intake_reader", "--once", stdout=StringIO())

    started = time.monotonic()
    response = signed_in.get(STATUS, {"intake": str(session.pk)})
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert response.context["intake_state"] == "ready"
    assert response.context["assisted"] is not None
    assert response.context["intake_prefill"], "the poll rendered no pre-fill at all"
    assert elapsed < POLL_SECONDS, f"the poll took {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def _writes(captured) -> list[str]:
    """The statements that change something, out of a captured query log."""
    return [
        query["sql"]
        for query in captured.captured_queries
        if query["sql"].strip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]


def test_the_page_stops_asking_once_the_answer_is_there(signed_in, evidence_root):
    """§21 — what bounds the *analysis* a poll costs, which writes do not.

    Polling writes nothing (below), but each poll on a session with a finished
    file does recompute the rule engine, and that is real CPU: measured at
    ~650 ms for one document at the per-document ceiling and ~1.6 s for a whole
    envelope at the intake ceiling. Three gunicorn workers and a 1.2 s poll
    would be a problem if it went on.

    It does not, and this is why: the browser only polls while the panel says
    `reading`, so the expensive polls are the ones during a partial read — and
    the reader takes a whole envelope in one turn (`BATCH`), so there are one
    or two of them. The state this asserts is the one that stops the loop
    (static/js/app.js).
    """
    session = stage(signed_in, upload("a.pdf"), upload("b.pdf"))
    reading = signed_in.get(STATUS, {"intake": str(session.pk)})
    assert reading.context["intake_state"] == "reading"

    call_command("run_intake_reader", "--once", stdout=StringIO())

    settled = signed_in.get(STATUS, {"intake": str(session.pk)})
    assert settled.context["intake_state"] == "ready"


def test_polling_writes_nothing_at_all(signed_in, evidence_root):
    """§21, §27.27 — the property that makes a 1.2 s poll free.

    A browser asks this route every 1.2 s for up to a minute. If the poll wrote
    even one row per call, a single person filling in a form would be fifty
    write transactions — on a volume whose fsync reached 155 seconds during the
    incident this round is about. It reads settled state and nothing else.

    Sessions are excluded: `SESSION_SAVE_EVERY_REQUEST` is False, so a plain
    GET writes no session row, and if that ever changed this test is where it
    would surface.
    """
    session = stage(signed_in, upload())
    call_command("run_intake_reader", "--once", stdout=StringIO())

    with CaptureQueriesContext(connection) as captured:
        for _ in range(5):
            assert signed_in.get(STATUS, {"intake": str(session.pk)}).status_code == 200

    assert _writes(captured) == [], _writes(captured)


def test_one_poll_costs_a_bounded_number_of_queries(signed_in, evidence_root):
    """§21 — and the cost does not grow with the envelope.

    Measured on one file and on four. The analyser's input is planned from a
    stored character count and reads the text of exactly what the budget
    admits, so the query *count* is flat however much material is staged
    (`app/matters/intake_suggestions/input.py`).
    """
    small = stage(signed_in, upload("üks.pdf"))
    call_command("run_intake_reader", "--once", stdout=StringIO())
    with CaptureQueriesContext(connection) as one_file:
        signed_in.get(STATUS, {"intake": str(small.pk)})

    large = stage(
        signed_in,
        upload("a.pdf"),
        upload("b.pdf"),
        upload("c.pdf"),
        upload("d.pdf"),
    )
    call_command("run_intake_reader", "--once", stdout=StringIO())
    with CaptureQueriesContext(connection) as four_files:
        signed_in.get(STATUS, {"intake": str(large.pk)})

    assert len(one_file.captured_queries) <= POLL_QUERY_CEILING, len(one_file.captured_queries)
    assert len(four_files.captured_queries) <= POLL_QUERY_CEILING, len(four_files.captured_queries)
    # Flat, not merely bounded: four files may cost a little more than one, but
    # not four times more, or the budget is bounding the reading and not the
    # loading.
    assert len(four_files.captured_queries) < 2 * len(one_file.captured_queries)


def test_reading_one_file_writes_one_row_and_publishes_nothing(signed_in, evidence_root):
    """§21 — the whole storage cost of the feature, counted.

    One staged file produces exactly one settling UPDATE. No derivative row, no
    fragment rows, no search projection, no attachment Document — none of which
    may exist before there is a Matter, and none of which is created afterwards
    either (docs/adr/0072).
    """
    stage(signed_in, upload())

    with CaptureQueriesContext(connection) as captured:
        call_command("run_intake_reader", "--once", stdout=StringIO())

    writes = _writes(captured)
    settling = [sql for sql in writes if "matters_matterintakefile" in sql.lower()]
    assert len(writes) == len(settling), [sql[:120] for sql in writes if sql not in settling]
    # Two: the claim, and the settle. The claim is what makes two readers safe.
    assert len(settling) == 2, [sql[:120] for sql in settling]


def test_the_reader_does_not_rewrite_text_it_has_already_settled(signed_in, evidence_root):
    """§21 — «avoid repeated full-text rewrites during polling», at the source.

    A second pass over a finished envelope must find nothing to do. Without
    this the queue and the worker would disagree about what is finished, which
    is the hot loop that logged thousands of lines a second on the first
    real-data deployment.
    """
    stage(signed_in, upload())
    call_command("run_intake_reader", "--once", stdout=StringIO())

    with CaptureQueriesContext(connection) as captured:
        call_command("run_intake_reader", "--once", stdout=StringIO())

    assert _writes(captured) == []
