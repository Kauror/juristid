"""SEC-008: untrusted files are parsed behind a boundary (docs/adr/0230).

Three layers of tests, because the boundary has three kinds of claim:

* **The contract** (any platform): what crosses the boundary is validated
  JSON and nothing else; a child is given no secret; the limits are clamped;
  the system check holds the intake budget inside the stale-claim window.
* **The queue** (PostgreSQL): what a stopped or unreachable parser does to a
  staged file or a corpus version — settled with a named failure, or handed
  back untouched — and that the queue carries on and a Teema can still be
  created.
* **The kernel** (Linux only, skipped elsewhere): real children, real
  ``setrlimit``, real process groups — a hang, a spin, a native crash, an
  output flood, a fork that tries to outlive its parent, a memory hoard, and
  what a child can see of the parent's secrets — plus the parser service over
  a real Unix socket, and the sizing measurements the limits are proposed from.
"""

from __future__ import annotations

import ast
import json
import socket
import sys
import threading
import time
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse

from app.documents.enums import DerivativeKind, ExtractionState, LocatorKind, TextSource
from app.documents.extraction import isolation
from app.documents.extraction.base import (
    DerivativePayload,
    Fragment,
    ParsedAttachment,
    ParseResult,
    SourceFile,
    registry,
)
from app.documents.extraction.errors import ExtractionFailed, ExtractionNotApplicable
from app.documents.extraction.isolation_checks import check_parser_isolation
from tests import synthetic_corpus as corpus

LINUX = sys.platform.startswith("linux")
linux_only = pytest.mark.skipif(
    not LINUX, reason="setrlimit, process groups and wait4 are Linux semantics"
)

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EML = "message/rfc822"
MSG = "application/vnd.ms-outlook"
TEXT = "text/plain"
HOOK = "juristid-parser-test-hook-"


def limits(**overrides) -> isolation.ChildLimits:
    values = {
        "wall_seconds": 60.0,
        "cpu_seconds": 60,
        "memory_bytes": 2 * 1024**3,
        "file_bytes": 64 * 1024**2,
        "output_bytes": 64 * 1024**2,
        "open_files": 256,
    }
    values.update(overrides)
    return isolation.ChildLimits(**values)


def sample_result() -> ParseResult:
    return ParseResult(
        derivatives=(
            DerivativePayload(
                kind=DerivativeKind.EXTRACTED_TEXT,
                fragments=(
                    Fragment(
                        text="Õiguskantsleri märgukiri",
                        locator_kind=LocatorKind.PAGE,
                        locator={"page": 1},
                        locator_label="lk 1",
                        text_source=TextSource.NATIVE,
                    ),
                ),
                metadata={"pages": 1},
            ),
            DerivativePayload(
                kind=DerivativeKind.THUMBNAIL,
                binary=b"\x89PNG\r\n",
                binary_extension="png",
                binary_mime_type="image/png",
            ),
        ),
        attachments=(ParsedAttachment(content=b"%PDF-1.4", filename="lisa.pdf", mime_type=PDF),),
        note="Kõik loetud.",
    )


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


def test_a_result_survives_the_wire_unchanged():
    result = sample_result()
    wire = json.loads(json.dumps(isolation.encode_result(result, text_only=False)))
    assert isolation.decode_result(wire) == result


def test_an_intake_answer_carries_text_and_never_bytes():
    wire = isolation.encode_result(sample_result(), text_only=True)
    assert [item["kind"] for item in wire["derivatives"]] == [DerivativeKind.EXTRACTED_TEXT]
    assert wire["attachments"] == []
    assert all(item["binary"] is None for item in wire["derivatives"])


@pytest.mark.parametrize(
    "tamper",
    [
        lambda wire: wire["derivatives"][0].update(kind="ARBITRARY"),
        lambda wire: wire["derivatives"][0]["fragments"][0].update(text=["not", "text"]),
        lambda wire: wire["derivatives"][1].update(binary="***not base64***"),
        lambda wire: wire["attachments"][0].update(inline="yes"),
        lambda wire: wire.update(derivatives={"a": 1}),
        lambda wire: wire["derivatives"][0].update(metadata=["not", "an", "object"]),
    ],
)
def test_an_off_shape_answer_is_refused_not_repaired(tamper):
    wire = json.loads(json.dumps(isolation.encode_result(sample_result(), text_only=False)))
    tamper(wire)
    with pytest.raises((ValueError, TypeError)):
        isolation.decode_result(wire)


def test_a_child_answering_for_another_parser_is_a_protocol_failure():
    parser = registry.for_mime_type(PDF)
    run = isolation.ChildRun(
        {"status": "done", "parser": "somebody-else", "result": {"derivatives": []}}
    )
    with pytest.raises(ExtractionFailed) as refused:
        isolation.interpret(parser, run, limits())
    assert refused.value.code == isolation.PROTOCOL


def test_the_boundarys_verdicts_keep_the_parsers_own_meaning():
    parser = registry.for_mime_type(PDF)
    failed = isolation.ChildRun({"status": "failed", "code": "page_limit", "detail": "Liiga pikk."})
    with pytest.raises(ExtractionFailed) as refused:
        isolation.interpret(parser, failed, limits())
    assert (refused.value.code, refused.value.detail) == ("page_limit", "Liiga pikk.")

    declined = isolation.ChildRun({"status": "not_applicable", "code": "x", "detail": "Ei."})
    with pytest.raises(ExtractionNotApplicable, match=r"Ei."):
        isolation.interpret(parser, declined, limits())

    crashed = isolation.ChildRun({"status": "error", "type": "ZeroDivisionError"})
    with pytest.raises(ExtractionFailed) as refused:
        isolation.interpret(parser, crashed, limits())
    assert refused.value.code == "parser_error"
    assert "ZeroDivisionError" in refused.value.detail


@pytest.mark.parametrize(
    "code",
    [
        isolation.TIMEOUT,
        isolation.CPU_LIMIT,
        isolation.MEMORY_LIMIT,
        isolation.FILE_LIMIT,
        isolation.OUTPUT_LIMIT,
        isolation.CRASHED,
        isolation.PROTOCOL,
    ],
)
def test_every_stop_has_a_sentence_that_says_the_original_is_kept(code):
    with pytest.raises(ExtractionFailed) as refused:
        isolation.interpret(
            registry.for_mime_type(PDF), isolation.ChildRun(None, failure_code=code), limits()
        )
    assert refused.value.code == code
    assert "Originaal on alles" in refused.value.detail


def test_a_childs_environment_carries_no_credential(monkeypatch, tmp_path):
    for name in (
        "POSTGRES_PASSWORD",
        "POSTGRES_USER",
        "DJANGO_SECRET_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "JURISTID_SHARED_GATE_PASSWORD",
        "DATABASE_URL",
    ):
        monkeypatch.setenv(name, "secret-value")
    environment = isolation.child_environment(tmp_path)
    assert set(environment) <= {
        "PATH",
        "LANG",
        "LC_ALL",
        "TESSDATA_PREFIX",
        "HOME",
        "TMPDIR",
        "PYTHONPATH",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONUNBUFFERED",
        "OMP_THREAD_LIMIT",
    }
    assert "secret-value" not in environment.values()


def test_only_parser_limits_travel_to_the_child(settings):
    settings.EXTRACTION_SOMETHING_SECRET_SHAPED = {"nested": "not a scalar"}
    chosen = isolation.extraction_settings()
    assert chosen
    assert all(name.startswith("EXTRACTION_") for name in chosen)
    assert "EXTRACTION_SOMETHING_SECRET_SHAPED" not in chosen
    assert "SECRET_KEY" not in chosen and "DATABASES" not in chosen


def test_a_callers_limits_never_exceed_the_services_ceiling():
    asked = limits(wall_seconds=9999, memory_bytes=10**12, output_bytes=10**12)
    clamped = asked.clamped(limits(wall_seconds=120, memory_bytes=1024**3, output_bytes=1024))
    assert (clamped.wall_seconds, clamped.memory_bytes, clamped.output_bytes) == (
        120,
        1024**3,
        1024,
    )
    with pytest.raises(ValueError, match="not a positive number"):
        isolation.ChildLimits.from_dict({**limits().as_dict(), "memory_bytes": -1})


def test_the_intake_budget_must_end_inside_the_stale_claim_window(settings):
    assert check_parser_isolation() == []
    settings.DOCUMENT_PARSER_INTAKE_WALL_SECONDS = 290
    errors = check_parser_isolation()
    assert [error.id for error in errors] == ["documents.E001"]
    assert "stale-claim window" in errors[0].msg


def test_the_intake_defaults_are_the_owner_decision_of_28_11_and_inside_the_claim_window(settings):
    """240 s wall + 30 s queue (+ 15 s hand-back) < the reader's 300 s claim, batch unchanged."""
    assert settings.DOCUMENT_PARSER_INTAKE_WALL_SECONDS == 240
    assert settings.DOCUMENT_PARSER_QUEUE_SECONDS == 30
    assert settings.DOCUMENT_PARSER_BATCH_WALL_SECONDS == 1200
    held = settings.DOCUMENT_PARSER_INTAKE_WALL_SECONDS + settings.DOCUMENT_PARSER_QUEUE_SECONDS + 15
    assert held == 285 < settings.INTAKE_READER_STALE_CLAIM_MINUTES * 60
    assert check_parser_isolation() == []


def test_a_240_second_parse_with_the_old_120_second_queue_would_outlive_its_claim(settings):
    """The lease is why the queue was cut: 240 + 120 + 15 is past 300 s, and refused."""
    settings.DOCUMENT_PARSER_QUEUE_SECONDS = 120
    errors = check_parser_isolation()
    assert [error.id for error in errors] == ["documents.E001"]
    assert "stale-claim window" in errors[0].msg


def test_service_mode_without_a_socket_is_a_configuration_error(settings):
    settings.DOCUMENT_PARSER_ISOLATION = "service"
    settings.DOCUMENT_PARSER_SOCKET = ""
    assert [error.id for error in check_parser_isolation()] == ["documents.E001"]


def test_an_unreachable_service_is_no_verdict_on_the_file(settings, tmp_path):
    settings.DOCUMENT_PARSER_ISOLATION = "service"
    settings.DOCUMENT_PARSER_SOCKET = str(tmp_path / "nobody-listens.sock")
    parser = registry.for_mime_type(TEXT)
    with pytest.raises(isolation.ParserUnavailable, match=r"did not answer|not available"):
        isolation.parse_isolated(parser, SourceFile(b"tere", "a.txt", TEXT), budget="intake")


def test_the_child_module_reaches_nothing_but_the_parsers():
    """The child must stay unable to name the database, the settings module or a model."""
    module = (
        Path(django_settings.BASE_DIR) / "app" / "documents" / "extraction" / "isolation_child.py"
    )
    tree = ast.parse(module.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    application = {name for name in imported if name.startswith(("app", "config"))}
    assert application <= {
        "app.documents.extraction",
        "app.documents.extraction.base",
        "app.documents.extraction.errors",
        "app.documents.extraction.isolation",
        "app.documents.extraction.limits",
    }, sorted(application)
    assert not any(name.startswith("config") for name in imported)


def test_the_service_never_loads_the_deployments_settings():
    module = (
        Path(django_settings.BASE_DIR) / "app" / "documents" / "extraction" / "isolation_server.py"
    )
    source = module.read_text(encoding="utf-8")
    assert "config.settings" not in source
    assert "DJANGO_SETTINGS_MODULE" not in source
    assert "manage.py" not in source.split('"""', 2)[2]


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------

STAGE = reverse("matters:intake_stage")


def _stage(client, *files):
    from tests.test_intake_reader import stage

    return stage(client, *files)


def _upload(name: str = "kaaskiri.pdf"):
    from tests.test_intake_reader import upload

    return upload(name)


def _unavailable(*args, **kwargs):
    raise isolation.ParserUnavailable("parser service did not answer: ConnectionRefusedError")


@pytest.mark.django_db
def test_a_reader_whose_parser_is_down_hands_the_file_back_untouched(
    signed_in, evidence_root, monkeypatch
):
    """Not a verdict on the file: PENDING again, attempts untouched, batch stops."""
    from app.matters import intake_extraction
    from app.matters.staging import MatterIntakeFile

    session = _stage(signed_in, _upload("üks.pdf"), _upload("kaks.pdf"))
    monkeypatch.setattr("app.documents.extraction.orchestrator.parse_isolated", _unavailable)

    reports = intake_extraction.drain(limit=10)

    assert [report.state for report in reports] == [intake_extraction.PARSER_UNAVAILABLE]
    rows = MatterIntakeFile.objects.filter(session=session).order_by("created_at")
    assert [row.extraction_state for row in rows] == [ExtractionState.PENDING] * 2
    assert all(row.extraction_claimed_at is None for row in rows)
    assert rows[0].extraction_attempts == 1
    assert not rows[0].text


@pytest.mark.django_db
def test_the_reader_waits_rather_than_spins_while_its_parser_is_down(
    signed_in, evidence_root, monkeypatch
):
    from app.matters.management.commands import run_intake_reader

    _stage(signed_in, _upload())
    monkeypatch.setattr("app.documents.extraction.orchestrator.parse_isolated", _unavailable)
    slept: list[float] = []

    class Stop(Exception):
        pass

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        raise Stop

    monkeypatch.setattr(run_intake_reader.time, "sleep", sleep)
    with pytest.raises(Stop):
        call_command("run_intake_reader", stdout=StringIO())
    assert slept == [run_intake_reader.PARSER_UNAVAILABLE_BACKOFF_SECONDS]


@pytest.mark.django_db
def test_a_file_the_boundary_stopped_is_settled_and_the_queue_moves_on(
    signed_in, evidence_root, monkeypatch
):
    """A hang is FAILED with its reason, once; the next file is still read."""
    from app.matters import intake_extraction
    from app.matters.staging import MatterIntakeFile

    session = _stage(signed_in, _upload("ripub.pdf"), _upload("korras.pdf"))
    real = isolation.parse_isolated

    def stopped_first(parser, source, *, budget):
        assert budget == isolation.INTAKE
        if source.filename == "ripub.pdf":
            raise ExtractionFailed(
                isolation.TIMEOUT, isolation.failure_note(isolation.TIMEOUT, limits())
            )
        return real(parser, source, budget=budget)

    monkeypatch.setattr("app.documents.extraction.orchestrator.parse_isolated", stopped_first)
    reports = intake_extraction.drain(limit=10)

    assert [report.error_code for report in reports] == [isolation.TIMEOUT, ""]
    hung = MatterIntakeFile.objects.get(session=session, original_filename="ripub.pdf")
    read = MatterIntakeFile.objects.get(session=session, original_filename="korras.pdf")
    assert hung.extraction_state == ExtractionState.FAILED
    assert "Originaal on alles" in hung.extraction_note
    assert hung.extraction_claimed_at is None
    assert read.extraction_state == ExtractionState.DONE
    # Settled, so nothing offers it again: no retry loop.
    assert intake_extraction.drain(limit=10) == []


@pytest.mark.django_db
def test_a_stale_pass_cannot_hand_back_a_file_somebody_else_now_owns(
    signed_in, evidence_root, monkeypatch
):
    from app.matters import intake_extraction
    from app.matters.staging import MatterIntakeFile

    session = _stage(signed_in, _upload())
    staged = MatterIntakeFile.objects.get(session=session)
    claimed = intake_extraction.claim_intake_file(staged.pk)
    # A second reader reclaims the row while the first is still waiting.
    intake_extraction.claim_intake_file(staged.pk, force=True)
    newer = MatterIntakeFile.objects.get(pk=staged.pk).extraction_claimed_at

    monkeypatch.setattr("app.documents.extraction.orchestrator.parse_isolated", _unavailable)
    report = intake_extraction.extract_intake_file(claimed)

    assert report.state == intake_extraction.CLAIM_LOST
    row = MatterIntakeFile.objects.get(pk=staged.pk)
    assert row.extraction_state == ExtractionState.PROCESSING
    assert row.extraction_claimed_at == newer


@pytest.mark.django_db
def test_a_teema_is_created_while_the_parser_is_down(signed_in, evidence_root, monkeypatch):
    """Parsing is never a precondition of `Loo teema` (docs/adr/0072, ADR 0230 §Failure)."""
    from app.documents.models import DocumentVersion
    from app.matters import intake_extraction

    session = _stage(signed_in, _upload("ootel.pdf"))
    monkeypatch.setattr("app.documents.extraction.orchestrator.parse_isolated", _unavailable)
    intake_extraction.drain(limit=10)

    response = signed_in.post(
        reverse("matters:matter_create"), {"title": "Lugeja on maas", "intake": str(session.pk)}
    )
    assert response.status_code == 302
    version = DocumentVersion.objects.get(original_filename="ootel.pdf")
    assert version.extraction_state == ExtractionState.PENDING


@pytest.mark.django_db
def test_a_corpus_version_is_handed_back_when_the_parser_is_down(
    normal_matter, capture_evidence, monkeypatch
):
    from app.documents.extraction import orchestrator
    from app.documents.models import DocumentDerivative

    version = capture_evidence(normal_matter, corpus.text_pdf(["Tekst"]), "a.pdf", PDF)
    claimed = orchestrator.claim_version(version.pk, force=True)
    monkeypatch.setattr(orchestrator, "parse_isolated", _unavailable)

    report = orchestrator.extract_document_version(claimed)

    assert report.state == orchestrator.PARSER_UNAVAILABLE
    version.refresh_from_db()
    assert version.extraction_state == ExtractionState.PENDING
    assert version.extraction_claimed_at is None
    assert not DocumentDerivative.objects.filter(version=version).exists()


def test_the_reader_refuses_to_parse_in_its_own_process_outside_dev_and_tests(settings):
    settings.DOCUMENT_PARSER_ISOLATION = "inprocess"
    settings.DEBUG = False
    settings.IS_TEST_SETTINGS = False
    with pytest.raises(CommandError, match="inprocess is refused"):
        call_command("run_intake_reader", "--once", stdout=StringIO())


# ---------------------------------------------------------------------------
# The kernel (Linux)
# ---------------------------------------------------------------------------


def _run(filename: str, content: bytes, mime: str, *, scratch: Path, **limit_overrides):
    header = {
        "op": "parse",
        "filename": filename,
        "mime_type": mime,
        "settings": {**isolation.extraction_settings(), "EXTRACTION_PARSER_TEST_HOOKS": True},
        "text_only": False,
    }
    return isolation.run_child(
        header=header, payload=content, limits=limits(**limit_overrides), scratch_root=scratch
    )


def _our_sleepers() -> list[int]:
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if cmdline[:2] == [b"sleep", b"600"]:
            found.append(int(entry.name))
    return found


@linux_only
def test_a_real_child_reads_a_pdf_exactly_as_the_parser_does(tmp_path):
    content = corpus.text_pdf(["Esimene lehekülg", "Teine lehekülg"])
    run = _run("a.pdf", content, PDF, scratch=tmp_path)
    parser = registry.for_mime_type(PDF)
    isolated = isolation.interpret(parser, run, limits())
    direct = parser.parse(SourceFile(content, "a.pdf", PDF))
    expected = isolation.decode_result(
        json.loads(json.dumps(isolation.encode_result(direct, text_only=False)))
    )
    assert isolated == expected
    assert run.usage.peak_rss_bytes > 0
    assert list(tmp_path.iterdir()) == []  # the job directory is gone


@linux_only
def test_a_hanging_parser_is_stopped_at_its_deadline(tmp_path):
    started = time.monotonic()
    run = _run(f"{HOOK}hang.txt", b"x", TEXT, scratch=tmp_path, wall_seconds=3)
    assert run.failure_code == isolation.TIMEOUT
    assert time.monotonic() - started < 15


@linux_only
def test_a_parser_burning_cpu_is_stopped_by_its_cpu_limit(tmp_path):
    run = _run(f"{HOOK}spin.txt", b"x", TEXT, scratch=tmp_path, wall_seconds=30, cpu_seconds=2)
    assert run.failure_code == isolation.CPU_LIMIT
    assert run.usage.cpu_seconds >= 2


@linux_only
def test_a_native_crash_is_a_named_failure_and_nothing_else(tmp_path):
    run = _run(f"{HOOK}crash.txt", b"x", TEXT, scratch=tmp_path)
    assert run.failure_code == isolation.CRASHED
    assert "SIGSEGV" in run.usage.exit_status


@linux_only
def test_a_flood_of_output_is_cut_off_at_the_cap(tmp_path):
    run = _run(f"{HOOK}flood.txt", b"x", TEXT, scratch=tmp_path, output_bytes=1024**2)
    assert run.failure_code == isolation.OUTPUT_LIMIT


@linux_only
def test_a_parser_tree_dies_with_its_parser(tmp_path):
    """A forked grandchild cannot outlive the deadline: the whole session is killed."""
    before = set(_our_sleepers())
    run = _run(f"{HOOK}fork-hang.txt", b"x", TEXT, scratch=tmp_path, wall_seconds=3)
    assert run.failure_code == isolation.TIMEOUT
    deadline = time.monotonic() + 5
    while set(_our_sleepers()) - before and time.monotonic() < deadline:
        time.sleep(0.1)
    assert set(_our_sleepers()) - before == set()


@linux_only
def test_a_memory_hoard_is_stopped_at_the_address_space_limit(tmp_path):
    ceiling = limits(memory_bytes=1024**3)
    run = _run(f"{HOOK}memory.txt", b"x", TEXT, scratch=tmp_path, memory_bytes=1024**3)
    with pytest.raises(ExtractionFailed) as refused:
        isolation.interpret(registry.for_mime_type(TEXT), run, ceiling)
    assert refused.value.code == isolation.MEMORY_LIMIT
    assert run.usage.peak_rss_bytes < 1100 * 1024**2


@linux_only
def test_a_child_finds_no_secret_in_its_environment_or_its_parents(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("POSTGRES_PASSWORD", "never-in-a-child")
    monkeypatch.setenv("DJANGO_SECRET_KEY", "never-in-a-child")
    header = {
        "op": "parse",
        "filename": f"{HOOK}secrets.txt",
        "mime_type": TEXT,
        "settings": {**isolation.extraction_settings(), "EXTRACTION_PARSER_TEST_HOOKS": True},
        "text_only": True,
    }
    run = isolation.run_child(
        header=header, payload=b"x", limits=limits(), scratch_root=tmp_path, protect_parent=True
    )
    found = json.loads(run.verdict["detail"])
    assert not {"POSTGRES_PASSWORD", "DJANGO_SECRET_KEY"} & set(found["environment"])
    assert found["parent_environ_readable"] is False
    # Recorded, not asserted: a subprocess cannot take the network away. That
    # is the service's job (network_mode: none), and the reason it exists.
    with capsys.disabled():
        print(f"\nSUBPROCESS-MODE child network reachable: {found['network']}")


@linux_only
def test_the_service_answers_over_its_socket_and_survives_a_bad_client(tmp_path, settings):
    from app.documents.extraction.isolation_server import Service, check

    path = tmp_path / "sock" / "parser.sock"
    service = Service(
        socket_path=path,
        scratch=tmp_path / "scratch",
        ceiling=limits(wall_seconds=30),
        max_input=16 * 1024**2,
    )
    service.bind()
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                connection, _ = service.listener.accept()
            except TimeoutError:
                continue
            with connection:
                service.handle(connection)
            service.sweep()

    worker = threading.Thread(target=loop, daemon=True)
    worker.start()
    try:
        assert check(path) == 0
        # A client that sends garbage and hangs up is refused, and the next is served.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as rogue:
            rogue.connect(str(path))
            rogue.sendall(b"\xff\xff\xff\xff not a header")
        settings.DOCUMENT_PARSER_ISOLATION = "service"
        settings.DOCUMENT_PARSER_SOCKET = str(path)
        parser = registry.for_mime_type(PDF)
        result = isolation.parse_isolated(
            parser, SourceFile(corpus.text_pdf(["Teenuse kaudu"]), "a.pdf", PDF), budget="intake"
        )
        assert "Teenuse kaudu" in "".join(
            fragment.text for payload in result.derivatives for fragment in payload.fragments
        )
        # Something a child left beside the socket is swept away.
        (path.parent / "left-behind").write_bytes(b"x" * 1024)
        service.sweep()
        assert sorted(p.name for p in path.parent.iterdir()) == ["parser.sock"]
    finally:
        stop.set()
        worker.join(timeout=10)
        service.listener.close()


@linux_only
def test_the_service_strips_a_callers_attempt_to_switch_test_hooks_on(tmp_path):
    from app.documents.extraction.isolation_server import Service

    service = Service(
        socket_path=tmp_path / "s" / "p.sock",
        scratch=tmp_path / "scratch",
        ceiling=limits(wall_seconds=5),
        max_input=1024,
    )
    left, right = socket.socketpair()
    header = {
        "op": "parse",
        "filename": f"{HOOK}hang.txt",
        "mime_type": TEXT,
        "settings": {**isolation.extraction_settings(), "EXTRACTION_PARSER_TEST_HOOKS": True},
        "text_only": True,
        "limits": limits().as_dict(),
    }
    with left, right:
        isolation.send_request(left, header, b"tavaline tekst")
        service.handle(right)
        response = isolation.receive_response(left, max_bytes=1024**2)
    # The hook never ran: the file was read as the text it is.
    assert response["failure_code"] == ""
    assert response["verdict"]["status"] == "done"


# -- sizing: what each fixture class actually costs -------------------------


def _large_docx(paragraphs: int = 4000) -> bytes:
    import io

    from docx import Document

    document = Document()
    for n in range(paragraphs):
        document.add_paragraph(f"Lõik {n}: " + "Seletuskirja tekst eelnõu kohta. " * 8)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _large_xlsx(rows: int = 20000) -> bytes:
    import io

    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for n in range(rows):
        sheet.append([f"Rida {n}", n, n * 1.5, "Märkus eelnõu kohta"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _sizing_fixtures() -> list[tuple[str, str, bytes]]:
    pages = [f"Lehekülg {n}: " + "Eelnõu seletuskiri. " * 120 for n in range(1, 401)]
    return [
        ("plain text", TEXT, corpus.memo_txt()),
        ("ordinary pdf (2 pages)", PDF, corpus.government_pdf()),
        ("large pdf (400 pages)", PDF, corpus.text_pdf(pages)),
        ("scanned pdf (OCR)", PDF, corpus.scanned_pdf()),
        ("scanned png (OCR)", "image/png", corpus.scanned_png()),
        ("docx", DOCX, corpus.draft_docx()),
        ("large docx (4000 par.)", DOCX, _large_docx()),
        ("xlsx", XLSX, corpus.annex_xlsx()),
        ("large xlsx (20000 rows)", XLSX, _large_xlsx()),
        ("eml with attachments", EML, corpus.consultation_eml()),
        ("msg with attachment", MSG, corpus.outlook_msg()),
        ("malformed pdf", PDF, corpus.corrupt_pdf()),
        ("malformed eml", EML, corpus.malformed_eml()),
        ("zip bomb as docx", DOCX, corpus.zip_bomb()),
    ]


@linux_only
def test_sizing_measurements_per_fixture_class(tmp_path, capsys):
    """Prints wall, CPU and peak RSS per class for docs/adr/0230 §Limits.

    Every class must also finish well inside the proposed limits, so the
    limits never stop a legitimate file of these shapes.
    """
    proposed = isolation.ChildLimits(
        wall_seconds=float(django_settings.DOCUMENT_PARSER_INTAKE_WALL_SECONDS),
        cpu_seconds=int(django_settings.DOCUMENT_PARSER_INTAKE_WALL_SECONDS),
        memory_bytes=int(django_settings.DOCUMENT_PARSER_MEMORY_BYTES),
        file_bytes=int(django_settings.DOCUMENT_PARSER_FILE_BYTES),
        output_bytes=int(django_settings.DOCUMENT_PARSER_BATCH_OUTPUT_BYTES),
    )
    rows = []
    for label, mime, content in _sizing_fixtures():
        header = {
            "op": "parse",
            "filename": "fixture",
            "mime_type": mime,
            "settings": isolation.extraction_settings(),
            "text_only": False,
        }
        run = isolation.run_child(
            header=header, payload=content, limits=proposed, scratch_root=tmp_path
        )
        status = run.failure_code or (run.verdict or {}).get("status", "?")
        if (run.verdict or {}).get("status") == "failed":
            status += f":{run.verdict.get('code')}"
        vm_peak = int((run.verdict or {}).get("vm_peak_bytes") or 0)
        hwm = int((run.verdict or {}).get("vm_hwm_bytes") or 0)
        rows.append(
            f"SIZING {label:<24} in={len(content) / 1024:>8.1f}KiB "
            f"wall={run.usage.seconds:>6.2f}s cpu={run.usage.cpu_seconds:>6.2f}s "
            f"rss_hwm={hwm / 1024**2:>7.1f}MiB "
            f"vm_peak={vm_peak / 1024**2:>7.1f}MiB {status}"
        )
        assert run.failure_code not in {
            isolation.TIMEOUT,
            isolation.CPU_LIMIT,
            isolation.MEMORY_LIMIT,
            isolation.OUTPUT_LIMIT,
            isolation.CRASHED,
        }, (label, run.failure_code)
    with capsys.disabled():
        print()
        print("\n".join(rows))
