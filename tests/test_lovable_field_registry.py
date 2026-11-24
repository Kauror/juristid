"""HR-10: the Lovable field descriptions every page and tool reads.

Golden values: each map below is the exact value at r91 (284b1d67), written
out by hand so a change to the shared registry that alters what a page, an
export, the importer or an MCP tool sees fails here first. The importer's and
the engine's column maps are engine input, so equality here is required.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from app.legacy_import import lovable_observation, lovable_pilot, lovable_review, lovable_sync
from app.legacy_import import lovable_sync_labels as labels

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: `LOVABLE_FIELD_LABELS`, in its order (Haldus «Vaata erinevusi» sorts by it).
GOLDEN_LABELS = [
    ("*", "Teema"),
    ("reference", "Teema tunnus"),
    ("title", "Pealkiri"),
    ("owner", "Vastutaja"),
    ("former_owner", "Endine vastutaja"),
    ("next_action_text", "Järgmine tegevus (sõnastus)"),
    ("next_action_date", "Järgmise tegevuse kuupäev"),
    ("onenote_link", "OneNote'i link"),
    ("stage", "Hetkeseis"),
    ("opinion_deadline", "Arvamuse tähtaeg"),
    ("sent", "Välja"),
    ("sent_date", "Välja saatmise kuupäev"),
    ("addressee", "Kellele"),
    ("koda_initiative", "Koja ettepanek"),
    ("legal_act", "Õigusakt"),
    ("received", "Sisse"),
    ("commencements", "Jõustumised"),
    ("operational_scope", "Operatiivne ulatus"),
    ("continuation", "Jätkuteema (menetlusetappide seos)"),
    ("new_topic", "Uus Lovable'i teema"),
    ("commencements_managed", "Jõustumisi hallatakse"),
    ("responsible_changes", "Vastutaja vahetused"),
    ("stage_links", "Menetlusetappide seosed"),
    ("topic_links", "Seotud teemad"),
]

#: `LOVABLE_SOURCE_COLUMNS`: Lovable column → canonical field.
GOLDEN_SOURCE_COLUMNS = {
    "teema": "title",
    "vastutaja": "owner",
    "uus_vastutaja": "owner",
    "jargmiseks": "next_action_text",
    "tegevuse_tahtaeg": "next_action_date",
    "onenote_link": "onenote_link",
    "hetkeseis": "stage",
    "arvamuse_tahtaeg": "opinion_deadline",
    "valja": "sent",
    "valja_kuupaev": "sent_date",
    "kellele": "addressee",
    "koja_ettepanek": "koda_initiative",
    "oigusakt": "legal_act",
    "sisse": "received",
    "operatiivne_ulatus": "operational_scope",
    "nr": "reference",
    "joustumised": "commencements",
    "joustumised_hallatud": "commencements_managed",
    "menetlusetapid": "stage_links",
    "seosed": "topic_links",
}

#: `lovable_pilot.HELD_FIELD_COLUMNS` (= `lovable_sync.IMPORT_HELD_COLUMNS`):
#: every source column but `operatiivne_ulatus` and the observation-only ones.
GOLDEN_HELD_FIELD_COLUMNS = {
    "jargmiseks": "next_action_text",
    "tegevuse_tahtaeg": "next_action_date",
    "hetkeseis": "stage",
    "arvamuse_tahtaeg": "opinion_deadline",
    "valja": "sent",
    "valja_kuupaev": "sent_date",
    "kellele": "addressee",
    "oigusakt": "legal_act",
    "sisse": "received",
    "koja_ettepanek": "koda_initiative",
    "teema": "title",
    "vastutaja": "owner",
    "uus_vastutaja": "owner",
    "joustumised": "commencements",
    "onenote_link": "onenote_link",
}

#: `lovable_pilot.REBASE_HELD_COLUMNS`, tuples in order (the validator's
#: message joins them with «or»).
GOLDEN_REBASE_HELD_COLUMNS = {
    "title": ("teema",),
    "owner": ("vastutaja", "uus_vastutaja"),
    "next_action": ("jargmiseks", "tegevuse_tahtaeg"),
    "stage": ("hetkeseis",),
    "open_request": ("arvamuse_tahtaeg",),
    "commencement_add": ("joustumised",),
}

#: `lovable_review.FIELD_ORDER`: Uuendused → Lovable, the xlsx exports and
#: the MCP tools' ``field`` choices.
GOLDEN_FIELD_ORDER = (
    "title",
    "owner",
    "stage",
    "next_action_text",
    "next_action_date",
    "opinion_deadline",
    "received",
    "sent",
    "sent_date",
    "addressee",
    "legal_act",
    "koda_initiative",
    "operational_scope",
    "commencements",
    "onenote_link",
)


def test_labels_are_unchanged_in_content_and_order():
    assert list(labels.LOVABLE_FIELD_LABELS.items()) == GOLDEN_LABELS


def test_source_columns_are_unchanged():
    assert labels.LOVABLE_SOURCE_COLUMNS == GOLDEN_SOURCE_COLUMNS
    # The only order a reader relies on: a field's own columns, in turn
    # (`lovable_observability` lists them for the ``?väli=`` filter).
    for field_name in set(GOLDEN_SOURCE_COLUMNS.values()):
        assert [c for c, f in labels.LOVABLE_SOURCE_COLUMNS.items() if f == field_name] == [
            c for c, f in GOLDEN_SOURCE_COLUMNS.items() if f == field_name
        ]


def test_import_held_columns_are_unchanged_and_shared_with_the_engine():
    assert lovable_pilot.HELD_FIELD_COLUMNS == GOLDEN_HELD_FIELD_COLUMNS
    assert lovable_sync.IMPORT_HELD_COLUMNS is lovable_pilot.HELD_FIELD_COLUMNS


def test_rebase_held_columns_are_unchanged():
    assert lovable_pilot.REBASE_HELD_COLUMNS == GOLDEN_REBASE_HELD_COLUMNS


def test_review_field_order_is_unchanged():
    assert lovable_review.FIELD_ORDER == GOLDEN_FIELD_ORDER


def test_mcp_field_choices_are_unchanged():
    # The schema module and the readers build their choices from the same tuple.
    text = (ROOT / "app" / "mcp_server" / "schemas.py").read_text(encoding="utf-8")
    assert '"field": tuple(lovable_review.FIELD_ORDER)' in text


def test_uuendused_field_filter_choices_are_unchanged():
    from app.core.views import _lovable_field_choices

    assert _lovable_field_choices() == [
        (k, v) for k, v in GOLDEN_LABELS if k not in ("*", "reference")
    ]


def test_every_synchronised_field_has_a_label():
    named = (
        set(lovable_sync.FIELDS)
        | set(lovable_sync.RESOLVABLE_FIELDS)
        | set(lovable_sync.SOURCE_FIELDS)
        | set(lovable_observation.OBSERVE_FIELDS)
        | set(lovable_sync.FIELD_POLICY)
        | set(lovable_review.FIELD_ORDER)
    )
    # FIELD_POLICY's two whole-topic entries are events, not fields (a page
    # shows them as «Teema», the ``*`` field).
    named -= {"source_missing", "identifier_changed"}
    assert named <= set(labels.LOVABLE_FIELD_LABELS)


class _Probe(dict):
    """A source row that records which columns a reader asks for."""

    def __init__(self) -> None:
        super().__init__()
        self.read: list[str] = []

    def get(self, key, default=None):  # type: ignore[override]
        self.read.append(key)
        return default

    def __getitem__(self, key):
        self.read.append(key)
        raise KeyError(key)


@pytest.mark.parametrize("field_name", sorted(lovable_sync.SOURCE_FIELDS))
def test_each_engine_extractor_reads_exactly_its_columns(field_name):
    """The engine's extractors keep their own column names (no rule change);
    each reads exactly the Lovable columns the shared map gives its field."""
    row = _Probe()
    lovable_sync.SOURCE_FIELDS[field_name]({"row": row, "commencements": []})
    expected = {c for c, f in GOLDEN_SOURCE_COLUMNS.items() if f == field_name}
    if field_name == "commencements":
        # Read off the bundle's child table (`topic_commencement`), not the row.
        assert row.read == []
    else:
        assert set(row.read) == expected


def test_shared_labels_module_imports_nothing_from_the_app():
    """`lovable_pilot`, the engine and the pages all import it: an app import
    here would be an import cycle."""
    tree = ast.parse(
        (ROOT / "app" / "legacy_import" / "lovable_sync_labels.py").read_text(encoding="utf-8")
    )
    imported = [
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    ]
    assert not [name for name in imported if name and name.startswith("app")]


def test_registry_flags_match_the_engine_and_the_observation():
    spec = labels.LOVABLE_FIELD
    assert {s.name for s in labels.LOVABLE_FIELDS if s.observed_only} == set(
        lovable_observation.OBSERVE_FIELDS
    )
    # The scope is compared, but no import decision holds it (stated, not implied).
    assert spec["operational_scope"].columns == ("operatiivne_ulatus",)
    assert not spec["operational_scope"].import_holdable
    assert labels.lovable_columns("next_action_text", "next_action_date") == (
        "jargmiseks",
        "tegevuse_tahtaeg",
    )
    assert len({s.name for s in labels.LOVABLE_FIELDS}) == len(labels.LOVABLE_FIELDS)
