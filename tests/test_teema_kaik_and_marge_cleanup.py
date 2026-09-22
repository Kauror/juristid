"""One list, a compact row, and a `Märge` that takes whatever the lawyer has.

docs/adr/0105, in three parts, each asserted where it is decided:

1. **`Teema käik` is one chronological list.** The phase headings and
   `Etapiga sidumata` are gone from the page. The ordering half of this claim is
   `tests/test_matter_process_phases.py`'s, which owns the eight worked examples
   and the rail they are now read off; what is here is the *markup* — that no
   heading reaches the document and that the classes that drew one are unused;
2. **a milestone row is compact.** The headline, the day and the controls that
   correct it are one line, which is `.uxtl__head`;
3. **`+ Märge` saves whatever there is.** A comment, a file, a stage, a next
   step, or any combination — and a press carrying none of them is refused with
   one sentence naming all four.

`tests/test_website_overviews.py` and `tests/test_overview_news_publication.py`
own §3, the `Ülevaade / uudis` row's address. `tests/test_lawyer_workflow_package.py`
owns what a titleless `MatterProceduralDevelopment` means once it is stored, and
`tests/test_procedural_development_correction.py` owns clearing a title on one.
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.matters.models import MatterProceduralDevelopment
from app.matters.services import DEVELOPMENT_NEEDS_SOMETHING
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction
from tests import factories

pytestmark = pytest.mark.django_db


def _teema(matter) -> str:
    return reverse("matters:matter_detail", kwargs={"pk": matter.pk})


def _add_note(matter) -> str:
    return reverse("matters:add_note", kwargs={"pk": matter.pk})


def _history(body: str) -> str:
    """Everything from `Teema käik`'s own element to the end of the page."""
    return body[body.index('id="ajajoon"') :]


def _pdf(name: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"%PDF-1.4 synthetic evidence", content_type="application/pdf")


def _inside_head(html: str) -> list[str]:
    """Every `id` and every button word that sits **inside** a `.uxtl__head`.

    Parsed rather than sliced. The claim is about nesting — the chips are children
    of the headline's own element, not a block after it — and a string window
    cannot tell the two apart: the markup reads the same either way and only the
    tree differs, which is the identical trap `row_remove.html` documents for a
    `<details>` inside a `<p>`.
    """
    from html.parser import HTMLParser

    class Reader(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.depth = 0
            self.open_tags: list[tuple[str, bool]] = []
            self.found: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            values = dict(attrs)
            classes = (values.get("class") or "").split()
            opens = "uxtl__head" in classes
            if opens:
                self.depth += 1
            if self.depth and values.get("id"):
                self.found.append(str(values["id"]))
            if tag not in ("br", "img", "input", "hr", "meta", "link"):
                self.open_tags.append((tag, opens))

        def handle_endtag(self, tag: str) -> None:
            while self.open_tags:
                name, opens = self.open_tags.pop()
                if opens:
                    self.depth -= 1
                if name == tag:
                    break

        def handle_data(self, data: str) -> None:
            if self.depth and data.strip():
                self.found.append(data.strip())

    reader = Reader()
    reader.feed(html)
    return reader.found


def _today() -> str:
    today = timezone.localdate()
    return f"{today.day}.{today.month}.{today.year}"


# ---------------------------------------------------------------------------
# §1 — one list, and no headings in the document
# ---------------------------------------------------------------------------


def test_no_phase_markup_reaches_the_chronology(signed_in, specialist, organisation):
    """The classes that drew a phase heading are not in the page, on any file.

    Asserted on the markup rather than on the words, because the words are what a
    rename would move: `uxtl__phase`, `uxtl__phasename`, `uxtl__phasenow`,
    `uxtl__phasetoggle` and `uxtl__phasenote` were the whole heading, and a
    template still emitting any of them would be drawing a section whose
    stylesheet has been deleted.
    """
    from app.matters.workspace import add_procedural_development

    matter = factories.MatterFactory(owner=specialist)
    add_procedural_development(
        matter=matter,
        author=specialist,
        title="Ministeerium saatis uue versiooni",
        occurred_on=timezone.localdate(),
    )

    history = _history(signed_in.get(_teema(matter)).content.decode())

    for retired in (
        "uxtl__phase",
        "uxtl__phasename",
        "uxtl__phasenow",
        "uxtl__phasetoggle",
        "uxtl__phasenote",
    ):
        assert retired not in history, retired
    assert "Etapiga sidumata" not in history
    assert "jätkub" not in history


def test_no_stylesheet_still_styles_a_phase_heading():
    """The other half: the rules went with the markup.

    `tests/test_ui_contract.py` refuses a class no stylesheet defines; this refuses
    the reverse, which that guard does not catch — a block of dead rules for
    elements nothing renders. Read off the files rather than through Django,
    because this is a claim about the repository.
    """
    import pathlib

    for name in ("static/css/app.css", "static/css/ux.css", "static/js/ux.js"):
        text = pathlib.Path(name).read_text(encoding="utf-8")
        assert "uxtl__phase" not in text, name


# ---------------------------------------------------------------------------
# §2 — the compact row
# ---------------------------------------------------------------------------


def test_a_marge_row_carries_its_controls_on_the_headline_row(signed_in, specialist):
    """`Muuda`, `+ Lisa fail` and `Kustuta` sit inside the headline's own element.

    The structural claim behind «2–3 lines, not 4–5»: the chip row used to be a
    block of its own under the record, so an ordinary row spent a whole line on
    one quiet word. `.uxtl__head` holds the headline paragraph and the chips as
    siblings, and it is a `<div>` rather than a `<p>` because the tree builder
    closes an open `p` at `Kustuta`'s `<details>`.
    """
    from app.matters.workspace import add_procedural_development

    matter = factories.MatterFactory(owner=specialist)
    record = add_procedural_development(
        matter=matter,
        author=specialist,
        title="Ministeerium saatis uue versiooni",
        occurred_on=timezone.localdate(),
    ).record

    inside = _inside_head(_history(signed_in.get(_teema(matter)).content.decode()))

    assert f"menetluse-areng-{record.pk}-pealkiri" in inside
    assert "Muuda" in inside
    assert "+ Lisa fail" in inside
    assert "Kustuta" in inside


def test_an_arvamus_valja_row_carries_muuda_on_the_headline_row(
    signed_in, specialist, organisation, evidence_root
):
    """The row the owner's round named, in the shape it asked for.

    `Arvamus välja · 22.9.2026 · Muuda`, then the sub-line, then the letter —
    rather than a third line holding `Muuda` between them.
    """
    from app.matters.workspace import add_matter_koda_opinion

    matter = factories.MatterFactory(owner=specialist)
    submission = add_matter_koda_opinion(
        matter=matter,
        author=specialist,
        upload=_pdf("arvamus.pdf"),
        recipients=[organisation],
        sent_on=timezone.localdate(),
        title="Koja arvamus",
    ).record

    inside = _inside_head(_history(signed_in.get(_teema(matter)).content.decode()))

    assert f"koja-arvamus-{submission.pk}-pealkiri" in inside
    assert "Muuda" in inside


# ---------------------------------------------------------------------------
# §4 — `+ Märge` saves whatever there is
# ---------------------------------------------------------------------------


def test_the_panel_marks_every_control_optional(signed_in, specialist, stage):
    """Read off the panel, because that is where somebody decides what to answer."""
    body = signed_in.get(_teema(factories.MatterFactory(owner=specialist))).content.decode()
    zone = body[body.index('id="lisa-teemale"') :]
    panel = zone[zone.index('id="marge-tavaline"') : zone.index('id="marge-tahtaeg"')]

    assert "Mis juhtus?" in panel
    # The sentence box now says so, which is the visible half of §4.
    head = panel[panel.index("Mis juhtus?") :]
    assert "valikuline" in head[:200]


def test_a_marge_saves_with_only_a_comment(signed_in, specialist, stage):
    """The ordinary note, and nothing else answered — including no date."""
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(_add_note(matter), {"title": "Rääkisin ministeeriumiga"})

    assert response.status_code == 200
    record = MatterProceduralDevelopment.objects.get(matter=matter)
    assert record.title == "Rääkisin ministeeriumiga"
    assert record.occurred_on is None


def test_a_marge_saves_with_only_a_file(signed_in, specialist, stage, evidence_root):
    """The paper that just arrived is what happened, and the row says `Märge`."""
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        _add_note(matter),
        {"occurred_on": _today(), "attachments": [_pdf("ministeeriumi_kiri.pdf")]},
    )

    assert response.status_code == 200
    record = MatterProceduralDevelopment.objects.get(matter=matter)
    assert record.title == ""
    history = _history(signed_in.get(_teema(matter)).content.decode())
    assert "ministeeriumi_kiri.pdf" in history
    assert ">Märge<" in history


def test_a_marge_saves_with_only_a_state_change(signed_in, specialist, stage):
    """The file moved, and that is the whole content of the save."""
    matter = factories.MatterFactory(owner=specialist, stage=None)

    response = signed_in.post(_add_note(matter), {"stage": str(stage.pk)})

    assert response.status_code == 200
    matter.refresh_from_db()
    assert matter.stage_id == stage.pk
    record = MatterProceduralDevelopment.objects.get(matter=matter)
    assert record.title == ""
    assert record.occurred_on is None


def test_a_marge_saves_with_only_a_next_task(signed_in, specialist, stage):
    """«Vaatan uue versiooni üle, 25.09», and nothing said about what happened."""
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        _add_note(matter),
        {"next_text": "Vaatan uue versiooni üle", "next_date": "25.09.2026"},
    )

    assert response.status_code == 200
    action = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert action.text == "Vaatan uue versiooni üle"
    assert MatterProceduralDevelopment.objects.get(matter=matter).title == ""


def test_a_marge_saves_all_four_together(signed_in, specialist, stage, evidence_root):
    """The combination, in one transaction and one operation."""
    matter = factories.MatterFactory(owner=specialist, stage=None)

    response = signed_in.post(
        _add_note(matter),
        {
            "title": "Ministeerium saatis uue eelnõu versiooni",
            "occurred_on": _today(),
            "stage": str(stage.pk),
            "next_text": "Vaatan uue versiooni üle",
            "next_date": "25.09.2026",
            "attachments": [_pdf("eelnou_v2.pdf")],
        },
    )

    assert response.status_code == 200
    record = MatterProceduralDevelopment.objects.get(matter=matter)
    assert record.title == "Ministeerium saatis uue eelnõu versiooni"
    matter.refresh_from_db()
    assert matter.stage_id == stage.pk
    assert NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).exists()
    history = _history(signed_in.get(_teema(matter)).content.decode())
    assert "eelnou_v2.pdf" in history


def test_a_marge_carrying_nothing_is_refused_with_one_sentence(signed_in, specialist, stage):
    """No sentence, no file, no stage, no step — and no «this field is required».

    The refusal names the four ways to answer, because whichever box the form
    checked first would be the wrong one to point at.
    """
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(_add_note(matter), {"occurred_on": _today()})

    assert response.status_code == 400
    body = response.content.decode()
    assert DEVELOPMENT_NEEDS_SOMETHING in body
    assert "See lahter on nõutav" not in body
    assert not MatterProceduralDevelopment.objects.filter(matter=matter).exists()


def test_a_next_step_with_no_day_saves(signed_in, specialist, stage):
    """docs/adr/0106 reverses the one refusal this round kept.

    The reasoning for keeping it was that a dateless step appears in nobody's
    `Tähtajad` and in nobody's `Minu asjad`. The first is true and correct — a
    list of dates is not where an undated step belongs. The second was wrong:
    `my_work.undated_items` has rendered a `Kuupäevata` block since the page was
    built, and simply had only WAIT and MONITOR rows to put in it.

    `tests/test_undated_next_actions.py` owns the whole new contract; this holds
    the `+ Märge` half of it, on the route this file is about.
    """
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(_add_note(matter), {"next_text": "Vaatan uue versiooni üle"})

    assert response.status_code == 200
    action = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert action.text == "Vaatan uue versiooni üle"
    assert action.target_date is None
    assert MatterProceduralDevelopment.objects.filter(matter=matter).exists()


def test_a_date_with_no_next_step_is_refused_on_the_sentence(signed_in, specialist, stage):
    """Unchanged, and the other half of the same pair."""
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        _add_note(matter), {"title": "Midagi juhtus", "next_date": "25.09.2026"}
    )

    assert response.status_code == 400
    assert "Kirjuta järgmine tegevus." in response.content.decode()


def test_a_titleless_marge_is_correctable_into_one(signed_in, specialist, stage):
    """And back: `Muuda` opens on the record and takes a sentence for it.

    The round trip that makes a titleless save safe — somebody who files the paper
    first and describes it afterwards has a route, and it is the row's own `Muuda`.
    """
    matter = factories.MatterFactory(owner=specialist)
    signed_in.post(_add_note(matter), {"stage": str(stage.pk)})
    record = MatterProceduralDevelopment.objects.get(matter=matter)

    url = reverse(
        "matters:update_development",
        kwargs={"pk": matter.pk, "development_id": record.pk},
    )
    response = signed_in.post(
        url,
        {
            "title": "Eelnõu saadeti Riigikokku",
            "occurred_on": _today(),
            "note": "",
            "revision": record.revision_token,
        },
    )

    assert response.status_code == 200
    record.refresh_from_db()
    assert record.title == "Eelnõu saadeti Riigikokku"
