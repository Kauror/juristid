"""What the three register-watch model callers send and do with each kind of answer (HR-08).

`ai.judge` (a Teema's title-search findings, ADR 0217), `candidates_ai.judge_unjudged`
(new drafts, ADR 0218) and `agenda.judge` (the Government's agenda, ADR 0219) each
build one request, call the model, account for tokens, check the stop reason and
read a JSON answer. These tests pin, for each caller and each kind of answer:

* the request, byte for byte (model, system, prompt, max_tokens, format, betas);
* the client the caller builds itself (timeout and retries);
* what it returns, writes, counts on ``Usage`` and logs;
* for the agenda only, ``ai_attempts`` and ``judged_at`` (audit HR-02).

They describe today's behaviour, drift included (the sweep logs neither a cut-off
nor an unreadable answer; the watch does not log an unreadable one), so a shared
transport helper can be shown to change nothing. No external model is called.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.enums import Visibility
from app.matters.enums import MatterDataClass
from app.register_watch import agenda, ai, candidates, candidates_ai
from app.register_watch.models import (
    AgendaItem,
    CandidateItem,
    GovernmentAgenda,
    TopicCandidate,
    WatchFinding,
    WatchSource,
    WatchTarget,
)
from tests import factories

pytestmark = pytest.mark.django_db

TEXT_MODEL = "served-model-1"
LONG = "x" * 600


# -- the fake model ----------------------------------------------------------------


class Overloaded(Exception):
    status_code = 529


class APITimeoutError(Exception):
    """Named like the SDK's timeout; carries no status."""


class FakeClient:
    """Records each request and answers with ``reply(request)`` or raises ``fail``."""

    def __init__(self, reply: Callable[[dict], Any] | None = None, fail: Exception | None = None):
        self.reply = reply
        self.fail = fail
        self.requests: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **request: Any) -> Any:
        self.requests.append(request)
        if self.fail is not None:
            raise self.fail
        assert self.reply is not None
        return self.reply(request)


def response(
    text: str | None = None,
    *,
    stop: str = "end_turn",
    content: list | None = None,
    usage: Any = None,
    model: str | None = None,
) -> Any:
    answer = SimpleNamespace(
        stop_reason=stop,
        usage=usage if usage is not None else SimpleNamespace(input_tokens=10, output_tokens=5),
        content=content if content is not None else [SimpleNamespace(type="text", text=text)],
    )
    if model is not None:
        answer.model = model
    return answer


# -- the three callers -------------------------------------------------------------


@dataclass
class Case:
    """One caller's world: what it judges, how its answers are keyed, what it wrote."""

    run: Callable[[Any, Any], int]
    usage: Any
    keys: list[Any]
    names: dict[str, str]
    observe: Callable[[], list[tuple]]
    agenda: GovernmentAgenda | None = None


def _topics() -> dict[str, Any]:
    """One topic the model may name, and two it may never see (`outbound`)."""
    sendable = factories.MatterFactory(title="Avatud teema")
    restricted = factories.MatterFactory(title="Piiratud teema", visibility=Visibility.RESTRICTED)
    test = factories.MatterFactory(title="Testteema", data_class=MatterDataClass.TEST)
    return {"sendable": sendable, "restricted": restricted, "test": test}


def _watch_case(matter: Any = None) -> Case:
    matter = matter or factories.MatterFactory(title="Jälgitav teema")
    target = WatchTarget.objects.create(
        matter=matter,
        source=WatchSource.HTM_REGISTER,
        identifier="973 SE",
        label="973 SE",
        origin="TITLE",
    )
    findings = [
        WatchFinding.objects.create(
            target=target,
            matter=matter,
            external_id=f"doc:{n}",
            title=f"Dokument {n} (973 SE)",
            occurred_on=dt.date(2026, 11, n),
            url=f"https://dok.hm.ee/et/document.html?id={n}",
            detail=f"8-1/26/{n} · Riigikogu",
        )
        for n in (1, 2)
    ]
    stamps = [f.updated_at for f in findings]

    def observe() -> list[tuple]:
        rows = []
        for finding, stamp in zip(findings, stamps, strict=True):
            finding.refresh_from_db()
            rows.append((finding.relevance, finding.ai_note, finding.updated_at != stamp))
        return rows

    return Case(
        run=lambda usage, client: ai.judge(matter, findings, usage, client),
        usage=ai.Usage(),
        keys=[str(f.pk) for f in findings],
        names={str(f.pk): f"<F{n}>" for n, f in enumerate(findings, start=1)},
        observe=observe,
    )


def _related_names(topics: dict[str, Any]) -> dict[str, str]:
    return {str(m.pk): f"<T:{name}>" for name, m in topics.items()}


DRAFTS = ("Äriseadustiku muutmise seaduse eelnõu", "Pakendiseaduse muutmise seaduse eelnõu")


def _sweep_case() -> Case:
    topics = _topics()
    chosen = []
    for n, title in enumerate(DRAFTS, start=1):
        candidate = TopicCandidate.objects.create(
            title=title, match_key=candidates.match_key(title)
        )
        CandidateItem.objects.create(
            candidate=candidate,
            source=WatchSource.ADR_MKM,
            external_id=f"adr:mkm:{n}",
            title=title,
            occurred_on=dt.date(2026, 11, 20),
            url=f"https://adr.rik.ee/mkm/dokument/{n}",
            detail=f"2-3/{n} · Sissetulev kiri",
        )
        chosen.append(candidate)
    stamps = [c.updated_at for c in chosen]

    def observe() -> list[tuple]:
        rows = []
        for candidate, stamp in zip(chosen, stamps, strict=True):
            candidate.refresh_from_db()
            related = _related_names(topics).get(str(candidate.related_matter_id))
            rows.append(
                (candidate.strength, candidate.reason, related, candidate.updated_at != stamp)
            )
        return rows

    return Case(
        run=lambda usage, client: candidates_ai.judge_unjudged(usage, client),
        usage=candidates_ai.Usage(),
        keys=[str(c.pk) for c in chosen],
        names={
            **{str(c.pk): f"<C{n}>" for n, c in enumerate(chosen, start=1)},
            **_related_names(topics),
        },
        observe=observe,
    )


def _agenda_case() -> Case:
    topics = _topics()
    session = GovernmentAgenda.objects.create(
        url="https://valitsus.ee/uudised/x",
        title="Valitsuse 19.11.26 istungi kommenteeritud päevakord",
        session_date=dt.date(2026, 11, 19),
    )
    items = [
        AgendaItem.objects.create(
            agenda=session,
            number=n,
            title=f"Punkt {n}",
            presenter="Minister",
            kind="Eelnõu",
            text=f"Selgitus {n}",
        )
        for n in (1, 2)
    ]
    stamps = [i.updated_at for i in items]

    def observe() -> list[tuple]:
        rows = []
        for item, stamp in zip(items, stamps, strict=True):
            item.refresh_from_db()
            related = _related_names(topics).get(str(item.related_matter_id))
            rows.append((item.strength, item.reason, related, item.updated_at != stamp))
        session.refresh_from_db()
        rows.append(("agenda", session.judged_at is not None, session.ai_attempts))
        return rows

    return Case(
        run=lambda usage, client: agenda.judge(session, usage, client),
        usage=candidates_ai.Usage(),
        keys=[1, 2],
        names=_related_names(topics),
        observe=observe,
        agenda=session,
    )


CASES = {"watch": _watch_case, "sweep": _sweep_case, "agenda": _agenda_case}
VALUE = {"watch": "verdict", "sweep": "strength", "agenda": "strength"}
TEXT = {"watch": "note", "sweep": "reason", "agenda": "reason"}
KEY = {"watch": "id", "sweep": "id", "agenda": "number"}
GOOD = {"watch": ("RELEVANT", "UNRELATED"), "sweep": ("STRONG", "NONE")}
GOOD["agenda"] = GOOD["sweep"]


@pytest.fixture(autouse=True)
def models_on(settings):
    settings.REGISTER_WATCH_AI = True
    settings.REGISTER_SWEEP_AI = True
    settings.REGISTER_WATCH_AI_MODEL = "watch-model"
    settings.REGISTER_SWEEP_AI_MODEL = "sweep-model"


def _row(kind: str, key: Any, value: str, text: str, related: str | None = None) -> dict:
    row = {KEY[kind]: key, VALUE[kind]: value, TEXT[kind]: text}
    if kind != "watch":
        row["related"] = related
    return row


def _text(rows: list[dict]) -> str:
    return json.dumps({"items": rows}, ensure_ascii=False)


def _logs(caplog: pytest.LogCaptureFixture) -> list[tuple[str, str]]:
    return [(r.levelname, r.getMessage()) for r in caplog.records if r.name == "app.register_watch"]


def _usage(case: Case) -> dict:
    return asdict(case.usage)


def _base_usage(kind: str, **changes: Any) -> dict:
    usage = asdict(ai.Usage() if kind == "watch" else candidates_ai.Usage())
    if kind == "sweep":
        usage["model"] = "sweep-model"  # judge_unjudged seeds it from the setting
    usage.update(changes)
    return usage


# -- the request -------------------------------------------------------------------

EXPECTED_PROMPT = {
    "watch": (
        'Tracked topic:\n{"title": "Jälgitav teema", "searched_for": ["973 SE"]}\n\n'
        "Documents found:\n[\n"
        ' {\n  "id": "<F1>",\n  "title": "Dokument 1 (973 SE)",\n  "date": "2026-11-01",\n'
        '  "register": "Haridus- ja Teadusministeeriumi dokumendiregister",\n'
        '  "number_and_sender": "8-1/26/1 · Riigikogu"\n },\n'
        ' {\n  "id": "<F2>",\n  "title": "Dokument 2 (973 SE)",\n  "date": "2026-11-02",\n'
        '  "register": "Haridus- ja Teadusministeeriumi dokumendiregister",\n'
        '  "number_and_sender": "8-1/26/2 · Riigikogu"\n }\n]'
    ),
    "sweep": (
        'The Chamber\'s open topics:\n[{"id": "<T:sendable>", "title": "Avatud teema"}]\n\n'
        "New drafts:\n[\n"
        ' {\n  "id": "<C1>",\n  "entries": [\n   {\n'
        '    "title": "Äriseadustiku muutmise seaduse eelnõu",\n    "date": "2026-11-20",\n'
        '    "register": "Majandus- ja Kommunikatsiooniministeeriumi dokumendiregister",\n'
        '    "number_and_sender": "2-3/1 · Sissetulev kiri"\n   }\n  ]\n },\n'
        ' {\n  "id": "<C2>",\n  "entries": [\n   {\n'
        '    "title": "Pakendiseaduse muutmise seaduse eelnõu",\n    "date": "2026-11-20",\n'
        '    "register": "Majandus- ja Kommunikatsiooniministeeriumi dokumendiregister",\n'
        '    "number_and_sender": "2-3/2 · Sissetulev kiri"\n   }\n  ]\n }\n]'
    ),
    "agenda": (
        'The Chamber\'s open topics:\n[{"id": "<T:sendable>", "title": "Avatud teema"}]\n\n'
        "The Chamber's recent outgoing letters (titles):\n[]\n\n"
        "Agenda: Valitsuse 19.11.26 istungi kommenteeritud päevakord\n[\n"
        ' {\n  "number": 1,\n  "title": "Punkt 1",\n  "presenter": "Minister",\n'
        '  "kind": "Eelnõu",\n  "text": "Selgitus 1"\n },\n'
        ' {\n  "number": 2,\n  "title": "Punkt 2",\n  "presenter": "Minister",\n'
        '  "kind": "Eelnõu",\n  "text": "Selgitus 2"\n }\n]'
    ),
}
EXPECTED_REQUEST = {
    "watch": {"model": "watch-model", "max_tokens": 4000, "system": ai.SYSTEM, "schema": ai.SCHEMA},
    "sweep": {
        "model": "sweep-model",
        "max_tokens": 6000,
        "system": candidates_ai.SYSTEM,
        "schema": candidates_ai.SCHEMA,
    },
    "agenda": {
        "model": "sweep-model",
        "max_tokens": 8000,
        "system": agenda.SYSTEM,
        "schema": agenda.SCHEMA,
    },
}


def _normalised(text: str, names: dict[str, str]) -> str:
    for raw, name in names.items():
        text = text.replace(raw, name)
    return text


@pytest.mark.parametrize("kind", CASES)
def test_the_request_each_caller_builds(kind):
    case = CASES[kind]()
    client = FakeClient(lambda request: response(_text([])))

    case.run(case.usage, client)

    assert len(client.requests) == 1
    request = dict(client.requests[0])
    prompt = request.pop("messages")
    assert len(prompt) == 1 and set(prompt[0]) == {"role", "content"}
    assert prompt[0]["role"] == "user"
    assert _normalised(prompt[0]["content"], case.names) == EXPECTED_PROMPT[kind]
    expected = EXPECTED_REQUEST[kind]
    assert request == {
        "model": expected["model"],
        "max_tokens": expected["max_tokens"],
        "system": expected["system"],
        "output_config": {
            "effort": "low",
            "format": {"type": "json_schema", "schema": expected["schema"]},
        },
        "betas": ["server-side-fallback-2026-07-01"],
        "fallbacks": "default",
    }


@pytest.mark.parametrize("kind", CASES)
def test_the_default_model_when_no_setting_names_one(kind, settings):
    del settings.REGISTER_WATCH_AI_MODEL
    del settings.REGISTER_SWEEP_AI_MODEL
    case = CASES[kind]()
    client = FakeClient(lambda request: response(_text([])))

    case.run(case.usage, client)

    assert client.requests[0]["model"] == "claude-opus-5-5"


# -- the client a caller builds itself ---------------------------------------------


@pytest.mark.parametrize(
    ("kind", "timeout"), [("watch", 120.0), ("sweep", 180.0), ("agenda", 180.0)]
)
def test_the_client_a_caller_builds(kind, timeout, monkeypatch):
    built: list[dict] = []
    fake = FakeClient(lambda request: response(_text([])))

    def anthropic_client(**kwargs: Any) -> FakeClient:
        built.append(kwargs)
        return fake

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=anthropic_client))
    case = CASES[kind]()

    case.run(case.usage, None)

    assert built == [{"timeout": timeout, "max_retries": 2}]
    assert len(fake.requests) == 1


@pytest.mark.parametrize("kind", CASES)
def test_no_sdk_is_a_failed_request(kind, monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "anthropic", None)  # `import anthropic` raises ImportError
    case = CASES[kind]()
    caplog.set_level(logging.INFO, logger="app.register_watch")

    assert case.run(case.usage, None) == 0

    assert _usage(case) == _base_usage(kind, failures=1, last_failure="ModuleNotFoundError")
    assert _logs(caplog) == REQUEST_LOG[kind]("ModuleNotFoundError")


# -- readable answers --------------------------------------------------------------


@pytest.mark.parametrize("kind", CASES)
def test_a_complete_answer(kind, caplog):
    case = CASES[kind]()
    caplog.set_level(logging.INFO, logger="app.register_watch")
    first, second = GOOD[kind]
    sendable = next((raw for raw, name in case.names.items() if name == "<T:sendable>"), None)
    client = FakeClient(
        lambda request: response(
            _text(
                [
                    _row(kind, case.keys[0], first, "  esimene rida  ", sendable),
                    _row(kind, case.keys[1], second, LONG),
                ]
            ),
            model=TEXT_MODEL,
        )
    )

    assert case.run(case.usage, client) == 2

    linked = "<T:sendable>"
    if kind == "watch":
        assert case.observe() == [(first, "esimene rida", True), (second, "x" * 500, True)]
    else:
        expected = [(first, "esimene rida", linked, True), (second, "x" * 500, None, True)]
        if kind == "agenda":
            expected.append(("agenda", True, 1))
        assert case.observe() == expected
    assert _usage(case) == _base_usage(
        kind, calls=1, input_tokens=10, output_tokens=5, model=TEXT_MODEL, judged=2
    )
    assert _logs(caplog) == []


@pytest.mark.parametrize("kind", CASES)
def test_a_partial_answer_with_an_unusable_value_and_an_unoffered_teema(kind, caplog):
    case = CASES[kind]()
    caplog.set_level(logging.INFO, logger="app.register_watch")
    first, _ = GOOD[kind]
    hidden = next((raw for raw, name in case.names.items() if name == "<T:restricted>"), None)
    client = FakeClient(
        lambda request: response(
            _text(
                [
                    _row(kind, case.keys[0], first, "rida", hidden),
                    _row(kind, case.keys[1], "MAYBE", "ei"),
                    _row(kind, "999" if kind != "agenda" else 999, first, "võõras"),
                ]
            )
        )
    )

    assert case.run(case.usage, client) == 1

    if kind == "watch":
        assert case.observe() == [(first, "rida", True), ("", "", False)]
        logs: list = []
    else:
        expected = [(first, "rida", None, True), ("", "", None, False)]
        logs = []
        if kind == "agenda":
            expected.append(("agenda", True, 1))
            logs = [("INFO", "government agenda 2026-11-19: 1 of 2 item(s) left unjudged")]
        assert case.observe() == expected
    model = "sweep-model" if kind == "sweep" else ""
    assert _usage(case) == _base_usage(
        kind, calls=1, input_tokens=10, output_tokens=5, model=model, judged=1
    )
    assert _logs(caplog) == logs


@pytest.mark.parametrize("kind", CASES)
def test_a_readable_answer_with_no_usable_verdict(kind, caplog):
    case = CASES[kind]()
    caplog.set_level(logging.INFO, logger="app.register_watch")
    client = FakeClient(
        lambda request: response(
            _text([_row(kind, case.keys[0], "MAYBE", "ei"), _row(kind, case.keys[1], "", "ei")])
        )
    )

    assert case.run(case.usage, client) == 0

    model = "sweep-model" if kind == "sweep" else ""
    tokens = {"calls": 1, "input_tokens": 10, "output_tokens": 5, "model": model}
    if kind == "agenda":
        reason = "vastuses polnud ühtki kasutatavat hinnangut"
        assert case.observe()[-1] == ("agenda", False, 1)
        assert _usage(case) == _base_usage(kind, **tokens, failures=1, last_failure=reason)
        assert _logs(caplog) == agenda_log(reason)
    else:
        assert _usage(case) == _base_usage(kind, **tokens)
        assert _logs(caplog) == []
    assert all(row[-1] is False for row in case.observe() if row[0] != "agenda")


# -- answers that give nothing -----------------------------------------------------


def agenda_log(reason: str) -> list[tuple[str, str]]:
    return [("WARNING", f"government agenda 2026-11-19: model attempt failed: {reason}")]


REQUEST_LOG: dict[str, Callable[[str], list]] = {
    "watch": lambda r: [("WARNING", f"register watch: model call failed: {r}")],
    "sweep": lambda r: [("WARNING", f"register sweep: model call failed: {r}")],
    "agenda": agenda_log,
}
STOP_LOG: dict[str, Callable[[str], list]] = {
    "watch": lambda stop: [("WARNING", f"register watch: model stopped with {stop}")],
    "sweep": lambda stop: [],  # drift: the sweep does not log a cut-off answer
    "agenda": lambda stop: agenda_log(f"stop_reason {stop}"),
}
UNREADABLE_LOG: dict[str, list] = {
    "watch": [],  # drift: neither logs an unreadable answer
    "sweep": [],
    "agenda": agenda_log("vastus ei olnud loetav"),
}


def _nothing_written(case: Case, kind: str, *, attempts: int) -> None:
    rows = case.observe()
    if kind == "agenda":
        assert rows.pop() == ("agenda", False, attempts)
    blank = ("", "", False) if kind == "watch" else ("", "", None, False)
    assert rows == [blank, blank]


@pytest.mark.parametrize("kind", CASES)
@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (RuntimeError("sk-ant-secret is never shown"), "RuntimeError"),
        (Overloaded("busy"), "Overloaded 529"),
        (APITimeoutError("timed out"), "APITimeoutError"),
    ],
)
def test_a_failed_request(kind, error, reason, caplog):
    case = CASES[kind]()
    caplog.set_level(logging.INFO, logger="app.register_watch")

    assert case.run(case.usage, FakeClient(fail=error)) == 0

    _nothing_written(case, kind, attempts=1)
    assert _usage(case) == _base_usage(kind, failures=1, last_failure=reason)
    assert _logs(caplog) == REQUEST_LOG[kind](reason)


@pytest.mark.parametrize("kind", CASES)
def test_a_prompt_that_cannot_be_built_is_a_failed_request(kind, monkeypatch, caplog):
    module = {"watch": ai, "sweep": candidates_ai, "agenda": agenda}[kind]

    def broken(*args: Any, **kwargs: Any) -> str:
        raise ValueError("prompt")

    monkeypatch.setattr(module, "_prompt", broken)
    case = CASES[kind]()
    caplog.set_level(logging.INFO, logger="app.register_watch")
    client = FakeClient(lambda request: response(_text([])))

    assert case.run(case.usage, client) == 0

    assert client.requests == []
    _nothing_written(case, kind, attempts=1)
    assert _usage(case) == _base_usage(kind, failures=1, last_failure="ValueError")
    assert _logs(caplog) == REQUEST_LOG[kind]("ValueError")


@pytest.mark.parametrize("kind", CASES)
@pytest.mark.parametrize("stop", ["max_tokens", "refusal", "pause_turn"])
def test_an_incomplete_answer(kind, stop, caplog):
    case = CASES[kind]()
    caplog.set_level(logging.INFO, logger="app.register_watch")
    first, second = GOOD[kind]
    rows = [_row(kind, case.keys[0], first, "a"), _row(kind, case.keys[1], second, "b")]
    client = FakeClient(lambda request: response(_text(rows), stop=stop, model=TEXT_MODEL))

    assert case.run(case.usage, client) == 0

    _nothing_written(case, kind, attempts=1)
    assert _usage(case) == _base_usage(
        kind,
        calls=1,
        input_tokens=10,
        output_tokens=5,
        model=TEXT_MODEL,
        failures=1,
        last_failure=f"stop_reason {stop}",
    )
    assert _logs(caplog) == STOP_LOG[kind](stop)


UNREADABLE = {
    "malformed json": lambda kind, keys: response("not json {"),
    "no items": lambda kind, keys: response("{}"),
    "items not a list": lambda kind, keys: response('{"items": 5}'),
    "items of text": lambda kind, keys: response('{"items": ["x"]}'),
    "a row without its key": lambda kind, keys: response(
        _text([{VALUE[kind]: GOOD[kind][0], TEXT[kind]: "a"}])
    ),
    "no content": lambda kind, keys: response(content=[]),
    "no text block": lambda kind, keys: response(content=[SimpleNamespace(type="thinking")]),
}


@pytest.mark.parametrize("kind", CASES)
@pytest.mark.parametrize("shape", UNREADABLE)
def test_an_unreadable_answer(kind, shape, caplog):
    case = CASES[kind]()
    caplog.set_level(logging.INFO, logger="app.register_watch")
    client = FakeClient(lambda request: UNREADABLE[shape](kind, case.keys))

    assert case.run(case.usage, client) == 0

    _nothing_written(case, kind, attempts=1)
    model = "sweep-model" if kind == "sweep" else ""
    assert _usage(case) == _base_usage(
        kind,
        calls=1,
        input_tokens=10,
        output_tokens=5,
        model=model,
        failures=1,
        last_failure="vastus ei olnud loetav",
    )
    assert _logs(caplog) == UNREADABLE_LOG[kind]


def test_an_agenda_number_that_is_not_a_number_is_unreadable(caplog):
    case = _agenda_case()
    caplog.set_level(logging.INFO, logger="app.register_watch")
    client = FakeClient(
        lambda request: response(_text([{"number": "x", "strength": "STRONG", "reason": "a"}]))
    )

    assert case.run(case.usage, client) == 0

    _nothing_written(case, "agenda", attempts=1)
    assert case.usage.last_failure == "vastus ei olnud loetav"
    assert _logs(caplog) == agenda_log("vastus ei olnud loetav")


# -- token accounting --------------------------------------------------------------


@pytest.mark.parametrize("kind", CASES)
@pytest.mark.parametrize(
    "tokens",
    [SimpleNamespace(input_tokens=None, output_tokens=None), SimpleNamespace()],
    ids=["none", "missing"],
)
def test_missing_token_counts_count_as_zero(kind, tokens):
    case = CASES[kind]()
    client = FakeClient(lambda request: response(_text([]), usage=tokens))

    case.run(case.usage, client)

    assert (case.usage.calls, case.usage.input_tokens, case.usage.output_tokens) == (1, 0, 0)


@pytest.mark.parametrize("kind", CASES)
def test_tokens_add_up_over_requests_and_the_served_model_wins(kind):
    case = CASES[kind]()
    case.usage.calls, case.usage.input_tokens, case.usage.output_tokens = 2, 100, 50
    case.usage.model = "seeded"
    client = FakeClient(lambda request: response(_text([]), model=TEXT_MODEL))

    case.run(case.usage, client)

    assert (case.usage.calls, case.usage.input_tokens, case.usage.output_tokens) == (3, 110, 55)
    assert case.usage.model == TEXT_MODEL


def test_the_watch_sends_batches_of_25_and_the_sweep_of_20(monkeypatch):
    monkeypatch.setattr(ai, "BATCH", 1)
    watch = _watch_case()
    watch_client = FakeClient(lambda request: response(_text([])))
    watch.run(watch.usage, watch_client)

    monkeypatch.setattr(candidates_ai, "BATCH", 1)
    sweep = _sweep_case()
    sweep_client = FakeClient(lambda request: response(_text([])))
    sweep.run(sweep.usage, sweep_client)

    assert (len(watch_client.requests), watch.usage.calls) == (2, 2)
    assert (len(sweep_client.requests), sweep.usage.calls) == (2, 2)


def test_a_long_failure_reason_is_cut_to_120_characters():
    case = _watch_case()
    error_type = type("E" * 130, (Exception,), {})

    case.run(case.usage, FakeClient(fail=error_type()))

    assert case.usage.last_failure == "E" * 120 and case.usage.failures == 1


# -- the outbound rule -------------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [{"visibility": Visibility.RESTRICTED}, {"data_class": MatterDataClass.TEST}],
    ids=["restricted", "test"],
)
def test_the_watch_never_sends_a_teema_the_outbound_rule_refuses(extra):
    case = _watch_case(factories.MatterFactory(title="Jälgitav teema", **extra))
    client = FakeClient(lambda request: response(_text([])))

    assert case.run(case.usage, client) == 0

    assert client.requests == []
    assert _usage(case) == asdict(ai.Usage())


@pytest.mark.parametrize("kind", CASES)
def test_a_model_that_is_off_is_never_asked(kind, settings):
    settings.REGISTER_WATCH_AI = False
    settings.REGISTER_SWEEP_AI = False
    case = CASES[kind]()
    client = FakeClient(lambda request: response(_text([])))

    assert case.run(case.usage, client) == 0

    assert client.requests == []
