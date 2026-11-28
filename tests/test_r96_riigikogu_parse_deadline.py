"""r96 integration: a Riigikogu file's parse stays inside the stage's time.

SEC-008 (docs/adr/0230) sends every untrusted file to the parser boundary with a
batch wall of 20 minutes; SEC-010 (docs/adr/0231) gives the nightly Riigikogu step
a 98-minute budget around its own 90-minute limit. Without a seam one slow scan near
the end of the stage would carry the step past its budget and be killed by the
supervisor, and a parse stopped for the stage's lack of time would be stored as an
unreadable letter. `app.riigikogu.documents` narrows the parse to the time left and
turns a stop by that narrowed wall into `client.OutOfTime` («the rest next night»).
"""

from __future__ import annotations

import pytest

from app.documents.extraction import isolation
from app.documents.extraction.errors import ExtractionFailed
from app.riigikogu import client, documents
from app.riigikogu.enums import TextStatus

PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


class Asked:
    """`parse_isolated` as the boundary: records what it was asked, answers ``answer``."""

    def __init__(self, answer: BaseException | None = None) -> None:
        self.walls: list[float | None] = []
        self.answer = answer

    def __call__(self, parser, source, *, budget=isolation.BATCH, wall_seconds=None):
        self.walls.append(wall_seconds)
        if self.answer is not None:
            raise self.answer
        raise ExtractionFailed("unreadable_pdf", "synthetic")


@pytest.fixture
def boundary(monkeypatch):
    asked = Asked()
    monkeypatch.setattr(isolation, "parse_isolated", asked)
    return asked


def test_outside_a_stage_the_boundarys_own_budget_applies(boundary):
    text = documents.read(PDF, "kiri.pdf")
    assert boundary.walls == [None]
    assert text.status == TextStatus.UNREADABLE  # the file's own failure, as before


def test_inside_a_stage_the_parse_gets_the_time_left_less_the_margin(boundary):
    with client.time_limit(client.clock() + 300):
        documents.read(PDF, "kiri.pdf")
    (wall,) = boundary.walls
    assert wall is not None
    assert 300 - documents.PARSE_MARGIN - 5 < wall <= 300 - documents.PARSE_MARGIN


def test_with_no_time_left_the_file_is_not_parsed_and_the_stage_stops(boundary):
    with client.time_limit(client.clock() + 5), pytest.raises(client.OutOfTime):
        documents.read(PDF, "kiri.pdf")
    assert boundary.walls == []


def test_a_parse_stopped_by_the_stages_time_is_out_of_time_not_unreadable(boundary):
    boundary.answer = ExtractionFailed(isolation.TIMEOUT, "synthetic")
    with client.time_limit(client.clock() + 300), pytest.raises(client.OutOfTime):
        documents.read(PDF, "kiri.pdf")


def test_a_parse_stopped_by_the_files_own_budget_stays_unreadable(boundary):
    # No stage limit: the 20-minute batch wall was the file's, so it is the file's failure.
    boundary.answer = ExtractionFailed(isolation.TIMEOUT, "synthetic")
    text = documents.read(PDF, "kiri.pdf")
    assert text.status == TextStatus.UNREADABLE and text.why == "ExtractionFailed"


def test_the_boundary_narrows_its_wall_and_never_widens_it(monkeypatch, settings):
    settings.DOCUMENT_PARSER_ISOLATION = isolation.SUBPROCESS
    seen: list[isolation.ChildLimits] = []

    class Stop(Exception):
        pass

    def run_child(*, header, payload, limits, scratch_root, protect_parent):
        seen.append(limits)
        assert header["limits"] == limits.as_dict()  # what the child (or service) is told
        raise Stop

    monkeypatch.setattr(isolation, "run_child", run_child)
    source = isolation.SourceFile(content=PDF, filename="kiri.pdf", mime_type="application/pdf")
    batch = isolation.limits_for(isolation.BATCH)
    for wall in (90.0, batch.wall_seconds * 10, None):
        with pytest.raises(Stop):
            isolation.parse_isolated(object(), source, wall_seconds=wall)
    narrowed, widened, default = seen
    assert narrowed.wall_seconds == 90.0 and narrowed.cpu_seconds == 90
    assert narrowed.memory_bytes == batch.memory_bytes
    assert widened == batch and default == batch
