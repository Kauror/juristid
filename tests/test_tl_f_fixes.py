"""Agent F: the edges of the fixes for agent E's findings on PR #602.

E's probes (`test_tl_e_*`) reproduce each defect; these pin what the fixes must
*not* do besides — an ordinary note stays unlabelled, a past note is not
«Eesolev», the stop list does not swallow «lõpuks», a collapsed tab is still
a valid title. Synthetic data only.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from app.documents import title_proposals as tp
from app.legacy_import import historical_notes as hn
from app.matters.timeline import HISTORICAL_NOTE_ITEM
from tests.test_tl_b_timeline_notes import make_page, text
from tests.test_tl_d_title_proposals import _document, _plan, _row
from tests.test_tl_e_parser_probes import _t


@pytest.fixture
def _public_origin(settings):
    settings.JURISTID_PUBLIC_ORIGIN = "https://juristid.example.invalid"


# -- R2b stop list ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "Arvamuse esitamise lõpukuupäev 15.03.2024",
        "Kooskõlastusring lõppeb 15.03.2024",
        "Vastuse TÄHTPÄEV: 15.03.2024",
    ],
)
def test_every_case_form_of_a_due_date_label_is_stopped(line):
    assert hn.trailing_date(line) is None, line


def test_the_stop_list_is_a_word_stem_not_a_substring():
    # «lõpuks» (finally) is not «lõpp»: the heading stays a dated note.
    found = hn.trailing_date("Lõpuks võeti eelnõu vastu 15.03.2024")
    assert found is not None and found.display == "15.03.2024"


# -- parser robustness -----------------------------------------------------------------


def test_a_block_under_a_floating_block_is_page_text_not_lost():
    blocks = [
        _t(0, "12.03.2024 - esimene"),
        {**_t(1, "ujuv"), "outline": hn.FLOATING},
        {**_t(2, "ujuva laps"), "parent": 1},
    ]
    page = hn.parse_page(blocks)
    texts = [p for n in page.notes for p in n.paragraphs] + list(page.page_text)
    assert sorted(texts) == ["esimene", "ujuv", "ujuva laps"]


def test_a_well_formed_nested_page_reads_the_same_way_iteratively():
    blocks = [
        _t(0, "12.03.2024 - esimene"),
        _t(1, "laps", parent=0),
        _t(2, "lapselaps", parent=1),
        _t(3, "20.03.2024 - teine"),
        _t(4, "teise laps", parent=3),
    ]
    notes = hn.parse_page(blocks).notes
    assert [(n.date.display, n.ordinals, n.paragraphs) for n in notes] == [
        ("12.03.2024", (0, 1, 2), ("esimene", "laps", "lapselaps")),
        ("20.03.2024", (3, 4), ("teine", "teise laps")),
    ]


# -- the row's «Eesolev» -------------------------------------------------------------


@pytest.mark.django_db
def test_a_past_note_is_not_marked_eesolev(client, normal_matter):
    make_page([normal_matter], [text(0, "27.07.2026 - KLIM saatis näidiseelnõu")])
    client.force_login(normal_matter.owner)
    html = client.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}))
    row = re.search(
        r"<article[^>]*uxtl__item--historical[^>]*>.*?</article>", html.content.decode(), re.S
    )
    assert row is not None and "Eesolev" not in row.group(0)


# -- MCP get_matter_activity -----------------------------------------------------------


def _note_items(matter):
    from app.mcp_server import readers

    answer = readers.matter_activity(matter.owner, str(matter.pk))
    return [i for i in answer["items"] if i.get("item_type") == HISTORICAL_NOTE_ITEM]


@pytest.mark.django_db
@pytest.mark.usefixtures("_public_origin")
@pytest.mark.parametrize(
    ("line", "precision", "display"),
    [
        ("27.07.2026 - päeva märge", "EXACT", "27.07.2026"),
        ("07.2026 - kuu märge", "MONTH", "07.2026"),
        ("2025 - aasta märge", "YEAR", "2025"),
    ],
)
def test_mcp_activity_carries_the_notes_own_precision(normal_matter, line, precision, display):
    make_page([normal_matter], [text(0, line)])
    (item,) = _note_items(normal_matter)
    assert item["date"]["precision"] == precision
    assert item["date"]["display"] == display


@pytest.mark.django_db
@pytest.mark.usefixtures("_public_origin")
def test_mcp_activity_sends_every_paragraph_and_labels_only_next_actions(normal_matter):
    make_page(
        [normal_matter],
        [
            text(0, "27.07.2026 - KLIM saatis näidiseelnõu"),
            text(1, "teine lõik", depth=2, parent=0),
        ],
        title="Näidisleht",
    )
    (item,) = _note_items(normal_matter)
    assert item["text"].startswith("KLIM saatis näidiseelnõu\nteine lõik\n")
    assert "Näidisleht" in item["text"]
    assert hn.NEXT_ACTIONS_LABEL not in item["text"]
    assert item["text_truncated"] is False


# -- the title command -----------------------------------------------------------------


@pytest.mark.django_db
def test_a_refused_character_is_named_in_the_rows_detail(tmp_path, normal_matter, specialist):
    document = _document(normal_matter)
    path = _plan(tmp_path, [_row(document, proposed="Arvamus‮fdp.exe")])
    (result,) = tp.run_plan(path, actor_identifier=specialist.upn).results
    assert result.outcome == tp.REFUSED_BAD_PROPOSAL
    assert result.detail == f"{tp.PROPOSAL_FORBIDDEN_CHARACTER} U+202E"


@pytest.mark.django_db
def test_whitespace_the_service_collapses_is_still_a_valid_title(
    tmp_path, normal_matter, specialist
):
    document = _document(normal_matter)
    path = _plan(tmp_path, [_row(document, proposed="Näidis\tseletuskiri\n")])
    (result,) = tp.run_plan(path, actor_identifier=specialist.upn).results
    assert result.outcome == tp.WOULD_RENAME
