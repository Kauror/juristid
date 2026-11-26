"""The owner's text removals of 26.11.2026 (UX-002, UX-004, UX-005; BUG-003).

The owner asked for less text on three pages and named every sentence. Each
test here holds two things at once: the sentence is gone from the page, and
the number, classification or note it sat beside is still computed — the
removal is the page leaving words out, never the measurement changing.
"""

from __future__ import annotations

import pytest

from app.reporting import context as reporting_context
from app.reporting import services
from app.reporting import views as reporting_views
from app.reporting.metric_types import TimeBasis
from app.reporting.selectors import archive

pytestmark = pytest.mark.django_db

QUALITY = "/statistika/andmekvaliteet/"
CHANGES = "/osakond/muudatused/"

#: Every sentence UX-002 removes from Andmekvaliteet, verbatim from the brief.
QUALITY_REMOVED = (
    "Hetkeseis · kogu korpus",
    "Arhiivi tõendus ei ole kanooniline saadetud arvamus. Neid näitajaid ei liideta.",
    "Arvamuste arhiivi ei ole veel kataloogitud.",
    "mõõtmist lihtsalt ei ole",
    "Viis eri seisundit, mida ei liideta.",
    "allkirjaümbrik ei ole kumbki",
    "„Loetud teema loomisel“ tähendab",
    "see on teadlik valik, mitte tegemata töö",
    "Failid ise on alati avatavad teema juurest",
)


def _text(response) -> str:
    """The page with its whitespace folded, so a sentence split by a template
    line break is still found."""
    return " ".join(response.content.decode().split())


def test_andmekvaliteet_no_longer_prints_the_removed_sentences(client, specialist):
    client.force_login(specialist)

    response = client.get(QUALITY)

    assert response.status_code == 200
    body = _text(response)
    for sentence in QUALITY_REMOVED:
        assert sentence not in body, sentence
    # What stays: the sections, the extraction chart and its per-state notes.
    for kept in ("Näitajad", "Järjekorrad", "Ajaloo sidumine", "Teksti eraldamine"):
        assert kept in body, kept
    assert "Tõendiversioonide seisund" in body
    assert "Loetud teema loomisel" in body  # the state's own label
    assert "Märkus" in body


def test_the_archive_notes_are_still_carried_by_the_metric(rf, specialist):
    """Removed from Andmekvaliteet only: the result itself still says both."""
    request = rf.get(QUALITY)
    request.user = specialist
    context = reporting_context.from_request(request)

    cards = services.quality_page(context).cards
    noted = [card for card in cards if archive.NOT_A_SUBMISSION_NOTE in card.notes]
    assert noted, "an empty archive declines with both notes (archive._empty_result)"
    assert all(archive.EMPTY_NOTE in card.notes for card in noted)

    shown = reporting_views._without_notes(cards, reporting_views.QUALITY_PAGE_OMITTED_NOTES)
    assert [card.key for card in shown] == [card.key for card in cards]
    assert [card.value for card in shown] == [card.value for card in cards]
    assert [card.status for card in shown] == [card.status for card in cards]
    for card in shown:
        assert archive.NOT_A_SUBMISSION_NOTE not in card.notes
        assert archive.EMPTY_NOTE not in card.notes


def test_only_the_snapshot_corpus_basis_line_is_left_out(client, specialist):
    """A point-in-time metric that ignores the period used to read «Hetkeseis
    · kogu korpus»; that line goes, every other basis line on the page stays."""
    client.force_login(specialist)

    response = client.get(QUALITY)

    cards = list(response.context["cards"]) + list(response.context["extraction"])
    silent = [
        card
        for card in cards
        if card.definition.time_basis == TimeBasis.POINT_IN_TIME
        and not card.definition.respects_period
    ]
    assert silent, "the page has cards whose basis line was «Hetkeseis · kogu korpus»"
    body = response.content.decode()
    assert body.count('class="metric__basis"') == len(cards) - len(silent)


def test_muudatused_no_longer_has_the_mida_see_ei_naita_panel(client, department_head):
    client.force_login(department_head)

    response = client.get(CHANGES)

    body = _text(response)
    for sentence in (
        "Mida see ei näita",
        "Ühtegi muudatust ei ole veel salvestatud.",
        "Imporditud teemasid perioodil",
        "Impordi- ja süsteemikirjeid, mida ei loeta",
        "Süsteemi automaatseid tegevuse muudatusi",
        "Avatud teemasid, mille hetkeseisu algus ei ole teada",
        "«Tähelepanu vajavad» ja «Tähtaja ületanud» muutust saab mõõta",
    ):
        assert sentence not in body, sentence
    assert "data-coverage" not in body
    # The rail keeps its other sections, so it is not an empty column.
    assert "data-snapshots" in body
    # The coverage figures the panel printed are still computed.
    coverage = response.context["page"].coverage
    for figure in ("imported_matters", "system_events", "automatic_steps", "stage_start_unknown"):
        assert isinstance(getattr(coverage, figure), int), figure


def test_the_kokku_row_has_one_cell_per_column(client, department_head, specialist):
    """BUG-003's markup half: the total row is as wide as the header, and every
    figure in it carries the numeric class its column's figures carry."""
    from app.matters.services import change_stage, create_matter
    from app.workflow.models import StageVocabulary

    matter = create_matter(title="Kokku rida", owner=specialist, actor=specialist, stage=None)
    change_stage(matter=matter, stage=StageVocabulary.objects.get(key="idea"), actor=specialist)
    client.force_login(department_head)

    body = client.get(CHANGES).content.decode()

    table = body.split("data-people", 1)[1].split("</table>", 1)[0]
    head = table.split("<thead>", 1)[1].split("</thead>", 1)[0]
    foot = table.split("<tfoot>", 1)[1].split("</tfoot>", 1)[0]
    assert head.count("<th ") == 6
    assert foot.count("<th ") + foot.count("<td ") == 6
    assert foot.count('<td class="chtable__num">') == 5
