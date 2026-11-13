"""The contract suite's cheap invariants (audit §D.4): no database, milliseconds.

* I-6 — the oracle is independent of the engine it checks.
* I-7 — the contract fixtures hold synthetic data only.
* LC-R1 (static part) — the synchronisation's transport can only read.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

from app.legacy_import import lovable_source
from app.legacy_import.lovable_source import HttpTransport, SourceConfig, SourceError

HERE = Path(__file__).resolve().parent
ORACLE_SIDE = ("oracle.py", "vocabulary.py")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name | ast.Attribute)
            and getattr(node.func, "id", getattr(node.func, "attr", ""))
            in ("__import__", "import_module")
        ):
            found.add("<dynamic import>")
    return found


@pytest.mark.parametrize("name", ORACLE_SIDE)
def test_the_oracle_imports_nothing_from_the_engine_or_the_domain(name):
    """I-6: the oracle (and the vocabulary it reads) import nothing from `app`.

    Contract: audit §D.2 — «It must NOT import `lovable_sync` or
    `lovable_reading`». Stricter on purpose: no `app.` module at all, so neither
    the engine's helpers nor a domain service can lend the oracle its answer;
    Juristid's rows are read through the model registry."""
    imported = _imports(HERE / name)
    assert "<dynamic import>" not in imported
    assert not {m for m in imported if m == "app" or m.startswith("app.")}, imported
    assert not {m for m in imported if "lovable_sync" in m or "lovable_reading" in m}


def test_the_import_scan_sees_a_forbidden_import(tmp_path):
    """I-6 has teeth: the scan reports an engine import in any of its forms."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "from app.legacy_import import lovable_sync\n"
        "import app.legacy_import.lovable_reading as r\n"
        "importlib.import_module('x')\n",
        encoding="utf-8",
    )
    found = _imports(probe)
    assert "app.legacy_import.lovable_sync" in found
    assert "app.legacy_import.lovable_reading" in found
    assert "<dynamic import>" in found


#: A topic number of a real register year (2000–2098 or 2100+): synthetic ones
#: use 1999 or 2099 only (audit §D.1 rule 3).
REAL_LOOKING_REFERENCE = re.compile(r"(?<![\d-])(?!1999_|2099_)((?:19|20|21)\d\d)_\d{1,4}(?!\d)")
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
SYNTHETIC_UUID_PREFIX = "00000000-0000-0000-"
#: Where a person's name is written: as a keyword argument or a dict key.
NAME_KEYS = frozenset({"vastutaja", "uus_vastutaja", "display_name", "owner_raw", "kellele_nimi"})
#: Where a year is written as a number.
YEAR_KEYS = frozenset({"year", "reference_year", "aasta"})
SYNTHETIC_YEARS = (1999, 2099)


def _contract_files() -> list[Path]:
    return sorted(p for p in HERE.glob("*.py") if p.name != Path(__file__).name)


def _text_of(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def violations(source: str) -> list[str]:
    """Everything in ``source`` that looks like real data (I-7)."""
    found: list[str] = []
    tree = ast.parse(source)

    def name(value: ast.AST, where: str) -> None:
        text = _text_of(value)
        if text and not text.endswith("Näidis"):
            found.append(f"{where}: name {text!r}")

    def year(value: ast.AST, where: str) -> None:
        if (
            isinstance(value, ast.Constant)
            and isinstance(value.value, int)
            and not isinstance(value.value, bool)
            and 1900 <= value.value <= 2200
            and value.value not in SYNTHETIC_YEARS
        ):
            found.append(f"{where}: year {value.value}")

    for node in ast.walk(tree):
        text = _text_of(node)
        if text is not None:
            if REAL_LOOKING_REFERENCE.search(text):
                found.append(f"reference in {text[:60]!r}")
            found += [
                f"uuid {u}" for u in UUID.findall(text) if not u.startswith(SYNTHETIC_UUID_PREFIX)
            ]
        if isinstance(node, ast.keyword) and node.arg in NAME_KEYS:
            name(node.value, node.arg)
        if isinstance(node, ast.keyword) and node.arg in YEAR_KEYS:
            year(node.value, node.arg)
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                label = _text_of(key) if key is not None else None
                if label in NAME_KEYS:
                    name(value, label)
                elif label in YEAR_KEYS:
                    year(value, label)
                elif label == "people" and isinstance(value, ast.Dict):
                    for person_key, person in zip(value.keys, value.values, strict=True):
                        if person_key is not None:
                            name(person_key, "people")
                        name(person, "people")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ref"
            and len(node.args) > 1
        ):
            year(node.args[1], "ref()")
    return found


@pytest.mark.parametrize("path", _contract_files(), ids=lambda p: p.name)
def test_contract_fixtures_hold_synthetic_data_only(path):
    """I-7: no real-looking topic number, no production UUID, no real name, no
    real register year — in strings, keyword arguments and dict literals alike.

    Contract: audit §D.1 rule 3 — topic numbers use the reserved years 1999 or
    2099; Lovable ids are the invented `00000000-0000-0000-…` form; every person
    a scenario names is invented and carries the «Näidis» surname."""
    assert violations(path.read_text(encoding="utf-8")) == []


def test_the_world_names_only_invented_people():
    """I-7: the world's people are invented (`world.PEOPLE`)."""
    from tests.lovable_contract import world

    assert world.PEOPLE and all(name.endswith(" Näidis") for name in world.PEOPLE)
    assert str(world.YEAR) in ("1999", "2099")
    assert world.source_id(world.ref(7)).startswith(SYNTHETIC_UUID_PREFIX)


@pytest.mark.parametrize(
    "source",
    [
        'x = "vt 2031_7"',
        'x = "Jätkub teemana 2031_12."',
        'x = "id 3f2b1c4d-1111-4222-8333-444455556666"',
        'lovable_row(r, vastutaja="Anna Kask")',
        'row = {"owner_raw": "Anna Kask"}',
        'package = {"people": {"Anna": "Anna Kask"}}',
        "create_matter(title=t, reference_year=2031)",
        'row = {"aasta": 2031}',
        "ref(5, 2031)",
    ],
)
def test_the_synthetic_data_guard_has_teeth(source):
    """I-7 catches a real-looking reference, a production-shaped UUID, a real
    name in a keyword or a dict, and a real register year (invented examples)."""
    assert violations(source)


def test_the_synthetic_data_guard_lets_synthetic_data_through():
    assert (
        violations(
            'x = "2099_12 ja 1999_3, kuupäev 2026-11-12"\n'
            'lovable_row(r, vastutaja="Ava Näidis", aasta=2099)\n'
            'p = {"people": {"Ava Näidis": "Ava Näidis"}}\n'
            "ref(5, 1999)\n"
            "dt.date(2026, 11, 12)\n"
        )
        == []
    )


# ---------------------------------------------------------------------------
# LC-R1, the static part
# ---------------------------------------------------------------------------


def test_the_transport_protocol_can_only_select():
    """LC-R1: the synchronisation's whole interface to Lovable is one read.

    Contract: approval 11.11 §2 «It read Lovable through GET requests only»;
    ADR 0192 §2 (owner rule 02.11: no write is ever sent, not even a probe)."""
    members = {
        name for name, _ in inspect.getmembers(lovable_source.Transport) if not name.startswith("_")
    }
    assert members == {"select"}


class _Recording:
    def __init__(self) -> None:
        self.requests: list[str] = []

    def __call__(self, request, timeout):  # pragma: no cover - must never be reached
        self.requests.append(request.get_method())
        raise AssertionError("a write-shaped request reached the network")


@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE"])
def test_the_http_transport_refuses_every_write_before_the_network(method):
    """LC-R1: POST (other than the sign-in), PATCH, PUT and DELETE are refused
    before a byte is sent (`test_lovable_source_security.py` is the unit; this
    pins the contract's list)."""
    opener = _Recording()
    transport = HttpTransport(
        SourceConfig(
            url="https://naidisprojekt.supabase.co",
            publishable_key="sb_publishable_naidis",
            email="e@x.invalid",
            password="pw-1",  # noqa: S106 - an invented test value
        ),
        opener=opener,
    )
    with pytest.raises(SourceError) as refused:
        transport._request(method, "/rest/v1/topic?id=eq.x", body={}, headers={})
    assert refused.value.code == "NOT_READ_ONLY"
    assert opener.requests == []
