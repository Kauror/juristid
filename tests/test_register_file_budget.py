"""«Lisa märkena» spends a bounded time on the register (HR-04).

The press used to fetch the listing and up to five files with 25 s per socket
wait and no total limit, inside a web request, so a slow ministry server could
hold a worker past gunicorn's 120 s and lose the lawyer's note. These tests
replace the network and the clock: no real time passes and nothing leaves the
machine.
"""

from __future__ import annotations

import json
import urllib.error
from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.urls import reverse

from app.documents.models import DocumentLink, DocumentVersion
from app.matters.models import MatterProceduralDevelopment
from app.register_watch import register_files, watch
from app.register_watch.models import FindingStatus, WatchFinding, WatchSource, WatchTarget
from app.register_watch.register_files import Fetched
from app.register_watch.sources import http
from app.register_watch.sources.http import FetchError
from tests import factories

PDF = b"%PDF-1.4 register file"
KEY = "0b4f2c1e-1111-4222-8333-444455556666"
LISTING = f"https://api.riigikogu.ee/api/documents/{KEY}"


class _Clock:
    """A clock only the fakes move: ``sleep`` and every simulated network wait."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class _Socket:
    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def settimeout(self, seconds: float) -> None:
        self.timeouts.append(seconds)


class _Slow:
    """An answer that costs ``per_read`` clock seconds per read, in ``chunks``."""

    def __init__(self, clock: _Clock, chunks: list[bytes], per_read: float) -> None:
        self.clock = clock
        self.chunks = list(chunks)
        self.per_read = per_read
        self.reads = 0
        self.sock = _Socket()
        self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=self.sock))
        self.open_timeout: float | None = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size: int = -1) -> bytes:
        self.reads += 1
        if not self.chunks:
            return b""
        timeout = self.sock.timeouts[-1] if self.sock.timeouts else self.open_timeout
        if timeout is not None and self.per_read > timeout:
            self.clock.now += timeout  # the socket gives up, as a real one would
            raise TimeoutError("timed out")
        self.clock.now += self.per_read
        return self.chunks.pop(0)


class _Network:
    """URL → answer; records each request and the socket timeout it was opened with."""

    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.routes: dict[str, object] = {}
        self.opened: list[tuple[str, float]] = []

    def open(self, request, timeout=None):
        url = request.full_url
        self.opened.append((url, timeout))
        answer = self.routes[url]
        if callable(answer):
            answer = answer()
        if isinstance(answer, BaseException):
            raise answer
        answer.open_timeout = timeout
        return answer


@pytest.fixture
def net(monkeypatch, settings):
    settings.REGISTER_WATCH_FETCH = True
    clock = _Clock()
    fake_time = SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep)
    monkeypatch.setattr(http, "time", fake_time)
    monkeypatch.setattr(register_files, "time", fake_time)
    monkeypatch.setattr(http, "_last_call", {})
    network = _Network(clock)
    monkeypatch.setattr(http, "_OPENER", network)
    monkeypatch.setattr(http, "_FORM_OPENER", network)
    return SimpleNamespace(clock=clock, network=network)


def _listing(net, names: list[str]) -> list[str]:
    """The Riigikogu lists ``names`` as public files; returns each file's URL."""
    files = [
        {"uuid": f"f{i}", "fileName": name, "accessRestrictionType": "PUBLIC"}
        for i, name in enumerate(names)
    ]
    net.network.routes[LISTING] = lambda: _Slow(
        net.clock, [json.dumps({"files": files}).encode()], per_read=0.5
    )
    return [f"https://api.riigikogu.ee/api/files/f{i}/download" for i in range(len(names))]


# -- the door: a deadline bounds the spacing, the socket and the reading ---------------


def test_without_a_deadline_the_door_reads_as_before(net):
    answer = _Slow(net.clock, [PDF], per_read=0)
    net.network.routes["https://adr.envir.ee/x"] = answer
    assert http.get_bytes("adr.envir.ee", "/x") == PDF
    assert net.network.opened == [("https://adr.envir.ee/x", http.TIMEOUT_SECONDS)]
    assert answer.reads == 1 and answer.sock.timeouts == []


def test_the_socket_timeout_shrinks_to_what_is_left(net):
    answer = _Slow(net.clock, [PDF], per_read=2)
    net.network.routes["https://adr.envir.ee/x"] = answer
    deadline = net.clock.now + 10

    assert http.get_bytes("adr.envir.ee", "/x", deadline=deadline) == PDF

    assert net.network.opened[0][1] == 10
    # Each piece is read with no more time than is left: 10, then 8 after the first.
    assert answer.sock.timeouts == [10, 8]


def test_a_server_that_drips_is_cut_off_at_the_deadline(net):
    endless = _Slow(net.clock, [b"x" * 1024] * 10_000, per_read=7)
    net.network.routes["https://adr.envir.ee/x"] = endless
    start = net.clock.now

    with pytest.raises(FetchError):
        http.get_bytes("adr.envir.ee", "/x", deadline=start + 30)

    # Pieces at 0, 7, 14, 21; the fifth is given the 2 s left and times out at 30.
    assert endless.reads == 5
    assert endless.sock.timeouts == [25, 23, 16, 9, 2]
    assert net.clock.now - start == 30


def test_spacing_that_would_pass_the_deadline_is_not_slept(net):
    http._last_call["api.riigikogu.ee"] = net.clock.now
    with pytest.raises(FetchError):
        http.get_bytes("api.riigikogu.ee", "/x", deadline=net.clock.now + 1)
    assert net.clock.slept == [] and net.network.opened == []


def test_a_passed_deadline_opens_nothing(net):
    with pytest.raises(FetchError):
        http.get_json("adr.envir.ee", "/x.json", deadline=net.clock.now)
    assert net.network.opened == []


def test_the_deadline_keeps_the_allowlist_and_the_size_cap(net):
    with pytest.raises(FetchError, match="keelatud"):
        http.get_bytes("example.com", "/x", deadline=net.clock.now + 30)
    net.network.routes["https://adr.envir.ee/big"] = _Slow(
        net.clock, [b"x" * 600, b"x" * 600], per_read=0
    )
    with pytest.raises(FetchError, match="liiga suur"):
        http.get_bytes("adr.envir.ee", "/big", max_bytes=1000, deadline=net.clock.now + 30)


# -- the press: one budget for the listing and every file -----------------------------


def test_the_budget_runs_out_and_the_rest_keep_their_links(net):
    urls = _listing(net, ["a.pdf", "b.pdf", "c.pdf", "d.pdf"])
    for url in urls:
        net.network.routes[url] = lambda: _Slow(net.clock, [PDF], per_read=15)
    start = net.clock.now

    fetched = register_files.fetch(WatchSource.RIIGIKOGU, f"text:{KEY}", budget=40)

    assert [one.name for one in fetched.uploads] == ["a.pdf", "b.pdf"]
    assert fetched.missed == ["c.pdf", "d.pdf"]
    assert fetched.missed_links == [
        {"name": "c.pdf", "url": urls[2]},
        {"name": "d.pdf", "url": urls[3]},
    ]
    # d.pdf was never asked for: the time was gone.
    assert urls[3] not in [url for url, _ in net.network.opened]
    assert net.clock.now - start <= 40


def test_one_slow_file_does_not_cost_the_others(net):
    urls = _listing(net, ["a.pdf", "aeglane.pdf", "c.pdf"])
    net.network.routes[urls[0]] = lambda: _Slow(net.clock, [PDF], per_read=0)
    net.network.routes[urls[1]] = TimeoutError("timed out")
    net.network.routes[urls[2]] = urllib.error.HTTPError(urls[2], 503, "x", {}, None)

    fetched = register_files.fetch(WatchSource.RIIGIKOGU, f"text:{KEY}")

    assert [one.name for one in fetched.uploads] == ["a.pdf"]
    assert fetched.missed == ["aeglane.pdf", "c.pdf"]
    assert [one["url"] for one in fetched.missed_links] == urls[1:]
    assert not fetched.unreachable


def test_a_listing_slower_than_the_budget_is_unreachable(net):
    net.network.routes[LISTING] = lambda: _Slow(net.clock, [b"{"] * 1000, per_read=10)
    fetched = register_files.fetch(WatchSource.RIIGIKOGU, f"text:{KEY}", budget=40)
    assert fetched.unreachable and fetched.uploads == []


def test_more_files_than_the_limit_are_missed_with_links(net):
    names = [f"f{i}.pdf" for i in range(register_files.MAX_FILES + 2)]
    urls = _listing(net, names)
    for url in urls:
        net.network.routes[url] = lambda: _Slow(net.clock, [PDF], per_read=0)

    fetched = register_files.fetch(WatchSource.RIIGIKOGU, f"text:{KEY}")

    assert len(fetched.uploads) == register_files.MAX_FILES
    assert [one["url"] for one in fetched.missed_links] == urls[register_files.MAX_FILES :]


# -- the view: the note survives, links stay, nothing twice, no lock while fetching ----


def _finding(matter, files=None):
    target = WatchTarget.objects.create(
        matter=matter,
        source=WatchSource.KLIM_REGISTER,
        identifier="Jäätmeseaduse",
        label="Jäätmeseaduse",
        origin="PHRASE",
    )
    return WatchFinding.objects.create(
        target=target,
        matter=matter,
        external_id="doc:b89d",
        title="Toiduliidu tagasiside",
        url="https://adr.envir.ee/et/document.html?id=b89d",
        detail="1-4/26/2491-15",
        files=files,
    )


def _press(client, finding, **extra):
    return client.post(
        reverse("register_watch:decide", kwargs={"pk": finding.pk}),
        {"otsus": "lisa", "markus": "Vaata punkti 3.", "tagasi": "teema", **extra},
        follow=True,
    )


def _names(development):
    documents = DocumentLink.objects.filter(procedural_development=development).values(
        "document_id"
    )
    return sorted(
        DocumentVersion.objects.filter(document_id__in=documents).values_list(
            "original_filename", flat=True
        )
    )


@pytest.mark.django_db
def test_a_partial_fetch_saves_the_note_with_the_missing_links(client, specialist, monkeypatch):
    matter = factories.MatterFactory(owner=specialist, title="Jäätmeseaduse muutmise eelnõu")
    finding = _finding(matter)
    missed = {"name": "lisa.pdf", "url": "https://adr.envir.ee/api/v1/documents/b89d/file"}
    monkeypatch.setattr(
        register_files,
        "fetch",
        lambda source, external_id: Fetched(
            uploads=[SimpleUploadedFile("kiri.pdf", PDF, content_type="application/pdf")],
            missed=["lisa.pdf"],
            missed_links=[missed],
        ),
    )
    client.force_login(specialist)

    response = _press(
        client,
        finding,
        failid=[SimpleUploadedFile("minu.pdf", PDF, content_type="application/pdf")],
    )

    development = MatterProceduralDevelopment.objects.get(matter=matter)
    assert development.note.startswith("Vaata punkti 3.\n\n")
    assert development.note.endswith(
        "Registri failid, mida ei saanud lisada:\n"
        "lisa.pdf: https://adr.envir.ee/api/v1/documents/b89d/file"
    )
    assert _names(development) == ["kiri.pdf", "minu.pdf"]
    assert "Neid faile ei saanud lisada (link on märkes): lisa.pdf." in response.content.decode()
    finding.refresh_from_db()
    assert finding.status == FindingStatus.ADDED


@pytest.mark.django_db
def test_an_unreachable_register_leaves_the_nightly_links_in_the_note(
    client, specialist, monkeypatch
):
    matter = factories.MatterFactory(owner=specialist, title="Jäätmeseaduse muutmise eelnõu")
    finding = _finding(
        matter,
        files=[{"name": "kiri.pdf", "url": "https://adr.envir.ee/api/v1/documents/b89d/file"}],
    )
    monkeypatch.setattr(
        register_files, "fetch", lambda source, external_id: Fetched(unreachable=True)
    )
    client.force_login(specialist)

    _press(client, finding)

    note = MatterProceduralDevelopment.objects.get(matter=matter).note
    assert "kiri.pdf: https://adr.envir.ee/api/v1/documents/b89d/file" in note


@pytest.mark.django_db
def test_a_slow_register_through_the_real_door_still_saves_the_note(
    client, specialist, net, monkeypatch
):
    """End to end on the fake network: the listing answers, the file never finishes."""
    matter = factories.MatterFactory(owner=specialist, title="Jäätmeseaduse muutmise eelnõu")
    finding = _finding(matter)
    net.network.routes["https://adr.envir.ee/api/v1/documents/b89d.json"] = lambda: _Slow(
        net.clock, [json.dumps({"file": "kiri.pdf"}).encode()], per_read=1
    )
    net.network.routes["https://adr.envir.ee/api/v1/documents/b89d/file"] = lambda: _Slow(
        net.clock, [b"%PDF"] + [b"x"] * 100_000, per_read=5
    )
    start = net.clock.now
    client.force_login(specialist)

    _press(client, finding)

    assert net.clock.now - start <= register_files.FETCH_BUDGET_SECONDS + http.TIMEOUT_SECONDS
    development = MatterProceduralDevelopment.objects.get(matter=matter)
    assert "kiri.pdf: https://adr.envir.ee/api/v1/documents/b89d/file" in development.note
    assert _names(development) == []


@pytest.mark.django_db
def test_no_row_lock_or_transaction_is_open_while_fetching(client, specialist, monkeypatch):
    matter = factories.MatterFactory(owner=specialist, title="Jäätmeseaduse muutmise eelnõu")
    finding = _finding(matter)
    events: list[str] = []
    depth_outside = len(connection.atomic_blocks)

    def fetch(source, external_id):
        events.append(f"fetch depth={len(connection.atomic_blocks) - depth_outside}")
        return Fetched()

    real_add = watch.add_to_teema

    def add_to_teema(*args, **kwargs):
        events.append("lock")
        return real_add(*args, **kwargs)

    monkeypatch.setattr(register_files, "fetch", fetch)
    monkeypatch.setattr(watch, "add_to_teema", add_to_teema)
    client.force_login(specialist)

    _press(client, finding)

    assert events == ["fetch depth=0", "lock"]
    assert MatterProceduralDevelopment.objects.filter(matter=matter).count() == 1


@pytest.mark.django_db
def test_a_double_submit_records_one_note(client, specialist, monkeypatch):
    matter = factories.MatterFactory(owner=specialist, title="Jäätmeseaduse muutmise eelnõu")
    finding = _finding(matter)
    calls: list[str] = []

    def fetch(source, external_id):
        calls.append(external_id)
        return Fetched(uploads=[SimpleUploadedFile("kiri.pdf", PDF)])

    monkeypatch.setattr(register_files, "fetch", fetch)
    client.force_login(specialist)

    _press(client, finding)
    again = client.post(
        reverse("register_watch:decide", kwargs={"pk": finding.pk}),
        {"otsus": "lisa", "tagasi": "teema"},
    )

    assert again.status_code == 404
    assert calls == ["doc:b89d"]  # the retry fetched nothing
    development = MatterProceduralDevelopment.objects.get(matter=matter)
    assert _names(development) == ["kiri.pdf"]


@pytest.mark.django_db
def test_a_press_that_loses_the_race_during_its_fetch_writes_nothing(
    client, specialist, monkeypatch
):
    """Two presses in flight: the other one saves while this one is still fetching."""
    matter = factories.MatterFactory(owner=specialist, title="Jäätmeseaduse muutmise eelnõu")
    finding = _finding(matter)

    def fetch(source, external_id):
        watch.add_to_teema(
            WatchFinding.objects.get(pk=finding.pk), actor=specialist, comment="Esimene"
        )
        return Fetched(uploads=[SimpleUploadedFile("kiri.pdf", PDF)])

    monkeypatch.setattr(register_files, "fetch", fetch)
    client.force_login(specialist)

    response = _press(client, finding)

    assert watch.ALREADY_DECIDED in response.content.decode()
    development = MatterProceduralDevelopment.objects.get(matter=matter)
    assert development.note.startswith("Esimene")
    assert _names(development) == []
