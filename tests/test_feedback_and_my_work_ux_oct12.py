"""The owner's UX review of 2026-10-12, held so it cannot quietly come back.

KAASAMINE · LISA TAGASISIDE (docs/adr/0142 amendment of 2026-10-12)
* A — the form asks `Kaasamine`, `Saadud tagasiside`, `Kuupäev`, the files and
  `Salvesta`, in that order, and no `Märkus` (docs/adr/0170 since added the
  organisation and the provenance before them, and named the box `Seisukoht`);
* the field is gone from `FeedbackForm` (`Lisa tagasiside`) only — the sibling correction
  form still asks it, and the column is still stored;
* a crafted `lawyer_note=` reaches nothing, and the reply text is not merged
  with it;
* a historical note keeps rendering under its own label, and stays correctable.

MINU ASJAD (docs/adr/0149 amendment of 2026-10-12)
* B — «arvamust sellel nädalal» and «arvamust kokku», with «arvamus …» for one;
  the old captions are gone; values, keys, links and order are unchanged
  (reworded again by the UX package of 2026-10-28: «arvamust tähtajaga sel
  nädalal» and «arvamust koostamisel», tests/test_seis_figure_help.py);
* C — «teemat menetluses» («teema menetluses» for one) on the same link to the
  same open-Matter list, with an explanation in a button *beside* the link —
  never inside it. Since the UX package of 2026-10-28 every Minu asjad and
  Osakond figure carries one; Saabunud's and Statistika's strips still do not.

The browser half (hover, focus, tap, Escape, the viewport) is in
e2e/test_my_work_in_progress_help.py.
"""

from __future__ import annotations

import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.matters.enums import EngagementKind, ExternalPositionProvenance
from app.matters.forms import ExternalPositionEditForm, FeedbackForm
from app.matters.models import MatterEngagement, MatterExternalPosition
from app.matters.my_work import build_my_work, opinion_work
from app.matters.register_filters import register_population
from app.matters.seis_help import IN_PROGRESS_HELP
from app.matters.services import add_engagement, close_matter, record_external_position
from app.matters.timeline import LAWYER_NOTE_LABEL
from app.matters.work_items import open_matters
from app.workflow.enums import Disposition
from app.workflow.reference_stages import REFERENCE_STAGES
from app.workflow.stage_flow import TERMINAL_STAGE_KEYS
from tests import factories
from tests.test_my_work_opinion_kpis import FRIDAY, MONDAY, drafted, listed, params_of

pytestmark = pytest.mark.django_db


def _round(matter, actor, title: str) -> MatterEngagement:
    return add_engagement(
        matter=matter,
        kind=EngagementKind.OTHER,
        title=title,
        occurred_on=timezone.localdate(),
        actor=actor,
    )


def _detail(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _reply_form(body: str) -> str:
    """The `Lisa tagasiside` form's own markup, and nothing around it."""
    reply = body[body.index('id="kaasamine-tagasiside"') :]
    return reply[: reply.index("</form>")]


def _reply(client, matter, data):
    return client.post(
        reverse("matters:add_engagement_reply", kwargs={"pk": matter.pk}),
        data,
        headers={"HX-Request": "true"},
    )


# ---------------------------------------------------------------------------
# A — `+ Kaasamine · Lisa tagasiside` asks no `Märkus`
# ---------------------------------------------------------------------------


def test_the_reply_form_asks_its_questions_in_order_and_no_markus(
    signed_in, normal_matter, specialist
):
    """No `Märkus`, one writing box, and the reviewed order kept.

    docs/adr/0170 made this the one form for every position, so it asks more
    than the four questions the owner reviewed on 2026-10-12 — the
    organisation, how the position reached Koda, and its link — and the box
    `Saadud tagasiside` is `Seisukoht` again, because it now holds published
    views too. The 2026-10-12 decisions stand: no note, one textarea, and the
    round, the text, the day, the files and the save in that order.

    How the position reached Koda is asked *before* the organisation since the
    2026-10-17 audit (JUX-005; docs/adr/0170, amendment of 2026-10-18): the
    answer reveals `Liige` at the end of the organisation row, so Tab after
    «Saadeti Kojale» has to reach it.
    """
    _round(normal_matter, specialist, "Liikmete küsitlus")

    reply = _reply_form(_detail(signed_in, normal_matter))

    assert 'name="lawyer_note"' not in reply
    assert "Märkus" not in reply
    # One writing box, and it is `Seisukoht`.
    assert reply.count("<textarea") == 1
    assert re.search(r'<textarea[^>]*name="summary"', reply)
    assert "Seisukoht" in reply
    order = [
        reply.index('name="provenance"'),
        reply.index('name="organisation'),
        reply.index('name="engagement"'),
        reply.index('name="summary"'),
        reply.index('name="stated_on"'),
        reply.index('name="attachments"'),
        reply.index(">Salvesta</button>"),
    ]
    assert order == sorted(order), order


def test_only_the_reply_form_lost_the_field():
    """Removed from `Lisa tagasiside`; still asked where a note is corrected."""
    assert "lawyer_note" not in FeedbackForm.base_fields
    assert {"engagement", "summary", "stated_on", "attachments"} <= set(FeedbackForm.base_fields)
    assert "lawyer_note" in ExternalPositionEditForm.base_fields
    # The sibling capture panels (`CompactExternalPositionForm` and its two
    # subclasses) were deleted by docs/adr/0170: `Lisa tagasiside` is now the
    # only creation form, and it declares no note. `Muuda` above still asks it.
    assert {field.name for field in MatterExternalPosition._meta.get_fields()} >= {"lawyer_note"}


def test_a_crafted_note_is_not_stored_and_not_merged_into_the_reply(
    signed_in, normal_matter, specialist
):
    round_ = _round(normal_matter, specialist, "Liikmete küsitlus")

    response = _reply(
        signed_in,
        normal_matter,
        {
            "engagement": str(round_.pk),
            "summary": "Kaks liiget toetavad.",
            "stated_on": "3.10.2026",
            "lawyer_note": "Varjatud märkus",
            "attachments": SimpleUploadedFile(
                "vastus.pdf", b"%PDF-1.4\nvastus", content_type="application/pdf"
            ),
        },
    )

    assert response.status_code == 200
    (position,) = MatterExternalPosition.objects.filter(matter=normal_matter)
    assert position.lawyer_note == ""
    assert position.summary == "Kaks liiget toetavad."
    assert position.engagement_id == round_.pk
    assert position.provenance == ExternalPositionProvenance.RECEIVED
    assert position.stated_on.isoformat() == "2026-10-03"
    assert "Varjatud märkus" not in response.content.decode()
    # The chosen wait is still completed in the same save.
    round_.refresh_from_db()
    assert not round_.has_open_feedback_wait


def test_a_note_alone_is_still_refused_as_an_empty_reply(signed_in, normal_matter, specialist):
    """The refusal is unchanged: a note was never content, and now is not even read."""
    round_ = _round(normal_matter, specialist, "Liikmete küsitlus")

    response = _reply(
        signed_in, normal_matter, {"engagement": str(round_.pk), "lawyer_note": "Ainult märkus"}
    )

    assert response.status_code == 400
    assert not MatterExternalPosition.objects.filter(matter=normal_matter).exists()
    round_.refresh_from_db()
    assert round_.has_open_feedback_wait


def test_a_historical_note_survives_renders_and_stays_correctable(
    signed_in, normal_matter, specialist
):
    old_round = _round(normal_matter, specialist, "Möödunud küsitlus")
    historical = record_external_position(
        matter=normal_matter,
        organisation=None,
        provenance=ExternalPositionProvenance.RECEIVED.value,
        summary="Liikmed vastasid telefonis.",
        lawyer_note="Vana juristi märkus, alles.",
        engagement=old_round,
        actor=specialist,
    )
    new_round = _round(normal_matter, specialist, "Uus küsitlus")

    response = _reply(
        signed_in,
        normal_matter,
        {"engagement": str(new_round.pk), "summary": "Uus vastus.", "lawyer_note": "x"},
    )
    assert response.status_code == 200

    historical.refresh_from_db()
    assert historical.lawyer_note == "Vana juristi märkus, alles."
    assert historical.summary == "Liikmed vastasid telefonis."
    # Read where it always was: its own line, under its own label, on its row.
    body = _detail(signed_in, normal_matter)
    assert LAWYER_NOTE_LABEL in body
    assert "Vana juristi märkus, alles." in body
    # And `Muuda` still opens on it.
    edit = signed_in.get(
        reverse(
            "matters:update_external_position",
            kwargs={"pk": normal_matter.pk, "position_id": historical.pk},
        ),
        headers={"HX-Request": "true"},
    ).content.decode()
    assert re.search(r'<textarea[^>]*name="lawyer_note"[^>]*>\s*Vana juristi märkus, alles\.', edit)


# ---------------------------------------------------------------------------
# B — the two opinion captions, shortened; nothing else moves
# ---------------------------------------------------------------------------


def _strip(work) -> dict:
    return {figure.key: figure for figure in work.seis}


@pytest.mark.parametrize(
    ("count", "week", "total"),
    [
        (1, "arvamus tähtajaga sel nädalal", "arvamus koostamisel"),
        (2, "arvamust tähtajaga sel nädalal", "arvamust koostamisel"),
        (3, "arvamust tähtajaga sel nädalal", "arvamust koostamisel"),
    ],
)
def test_the_opinion_captions_are_short_and_agree_with_their_number(specialist, count, week, total):
    for _ in range(count):
        drafted(specialist, deadline=MONDAY)

    work = build_my_work(specialist, today=FRIDAY)
    figures = _strip(work)
    expected = opinion_work(specialist, specialist, FRIDAY)

    assert (figures["opinions_week"].value, figures["opinions_drafting"].value) == (count, count)
    assert (expected.due_this_week, expected.in_preparation) == (count, count)
    assert figures["opinions_week"].caption == week
    assert figures["opinions_drafting"].caption == total
    # The links are the populations they were, and open exactly what is counted.
    assert figures["opinions_week"].url == expected.due_url
    assert figures["opinions_drafting"].url == expected.preparation_url
    assert params_of(expected.preparation_url)["arvamus"] == "koostamisel"
    assert len(listed(specialist, figures["opinions_week"].url)) == count
    assert len(listed(specialist, figures["opinions_drafting"].url)) == count


def test_the_order_tone_and_keys_of_the_strip_are_unchanged(specialist):
    work = build_my_work(specialist, today=FRIDAY)

    assert [figure.key for figure in work.seis] == [
        "open",
        "overdue",
        "week",
        "no_action",
        "opinions_week",
        "opinions_drafting",
    ]
    assert [figure.tone for figure in work.seis] == ["", "danger", "warning", "warning", "", ""]


def test_the_page_says_the_new_captions_and_never_the_old_ones(client, specialist):
    drafted(specialist, deadline=timezone.localdate())
    drafted(specialist, deadline=None)
    client.force_login(specialist)

    body = client.get(reverse("matters:my_work")).content.decode()

    assert '<span class="seis__caption">arvamus tähtajaga sel nädalal</span>' in body
    assert '<span class="seis__caption">arvamust koostamisel</span>' in body
    for old in (
        "arvamust koostada sel nädalal",
        "arvamust koostamisel kokku",
        "avatud teemat",
        "arvamus sellel nädalal",
        "arvamust kokku",
    ):
        assert old not in body, old


# ---------------------------------------------------------------------------
# C — «teemat menetluses», the same link, an explanation beside it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("count", "caption"), [(1, "teema menetluses"), (4, "teemat menetluses")])
def test_the_first_figure_is_the_same_open_matter_list(specialist, count, caption):
    mine = [factories.MatterFactory(owner=specialist, is_open=True) for _ in range(count)]
    # Not counted: closed, somebody else's, and register-only rows.
    close_matter(
        matter=factories.MatterFactory(owner=specialist),
        disposition=Disposition.COMPLETED,
        actor=specialist,
    )
    factories.MatterFactory(is_open=True)

    figure = _strip(build_my_work(specialist, today=FRIDAY))["open"]

    assert figure.value == count
    assert figure.caption == caption
    assert figure.url == (
        f"{reverse('matters:matter_list')}?olek=avatud&liik=FULL&vastutaja={specialist.pk}"
    )
    assert figure.help is IN_PROGRESS_HELP
    counted = set(open_matters(specialist).filter(owner=specialist).values_list("pk", flat=True))
    assert counted == {matter.pk for matter in mine}
    assert (
        set(register_population(specialist, params_of(figure.url)).values_list("pk", flat=True))
        == counted
    )


def test_a_matter_with_no_hetkeseis_is_counted_and_the_explanation_says_so(specialist):
    """Why the owner's text gained «hetkeseis on veel määramata»."""
    factories.MatterFactory(owner=specialist, is_open=True, stage=None)

    figure = _strip(build_my_work(specialist, today=FRIDAY))["open"]

    assert figure.value == 1
    assert "hetkeseis on veel määramata" in " ".join(IN_PROGRESS_HELP.paragraphs)


def test_the_explanation_names_every_current_stage_in_the_right_sentence():
    """The stage list is the vocabulary's, so a renamed stage fails here, not in a review."""
    in_progress, closed = IN_PROGRESS_HELP.paragraphs[1], IN_PROGRESS_HELP.paragraphs[2]
    for stage in REFERENCE_STAGES:
        sentence = closed if stage.key in TERMINAL_STAGE_KEYS else in_progress
        assert stage.label_et.lower() in sentence.lower(), stage.label_et
    assert IN_PROGRESS_HELP.title == "Menetluses olev teema"
    assert IN_PROGRESS_HELP.paragraphs[0].startswith("Menetluses olev teema on teema, millega")


def test_the_explanation_is_a_neighbour_of_the_link_never_inside_it(client, specialist):
    factories.MatterFactory(owner=specialist, is_open=True)
    client.force_login(specialist)

    body = client.get(reverse("matters:my_work")).content.decode()

    # Every figure drawn is explained (UX package 2026-10-28); the first is this one.
    assert body.count("data-seishelp") == body.count('class="seis__figure"') >= 1
    item = body[body.index('<span class="seis__item" data-seishelp>') :]
    item = item[: item.index('<span class="seis__tip"')]
    link = item[item.index('<a class="seis__figure"') : item.index("</a>") + len("</a>")]
    # The figure is still exactly its link…
    assert '<span class="seis__caption">teema menetluses</span>' in link
    assert "<button" not in link and "seis__info" not in link
    # …and the button follows it, describing itself by the explanation.
    button = re.search(r"<button[^>]*class=\"seis__info\"[^>]*>", item)
    assert button, item
    tag = button.group(0)
    assert 'type="button"' in tag
    assert 'aria-label="Selgitus: Menetluses olev teema"' in tag
    assert 'aria-describedby="seis-selgitus-menetluses"' in tag
    assert 'aria-expanded="false"' in tag
    tip = body[body.index('id="seis-selgitus-menetluses"') - 60 :]
    assert 'role="tooltip"' in tip[:120]
    for paragraph in IN_PROGRESS_HELP.paragraphs:
        assert paragraph in body
    # No nested interactive markup anywhere in the strip.
    strip = body[body.index('<div class="seis') :]
    strip = strip[: strip.index('<span class="seis__tip"')]
    assert not re.search(r"<a [^>]*>(?:(?!</a>).)*<(?:a|button)\b", strip, re.S)


def test_a_colleagues_desk_carries_the_same_figure(client, department_head, specialist):
    factories.MatterFactory(owner=specialist, is_open=True)
    factories.MatterFactory(owner=specialist, is_open=True)
    client.force_login(department_head)

    body = client.get(reverse("matters:person_work", kwargs={"pk": specialist.pk})).content.decode()

    assert '<span class="seis__caption">teemat menetluses</span>' in body
    assert body.count("data-seishelp") == body.count('class="seis__figure"')
    assert 'id="seis-selgitus-menetluses"' in body


@pytest.mark.parametrize(
    ("route", "who"),
    [
        ("matters:inbox", "specialist"),
        ("reporting:overview", "department_head"),
    ],
)
def test_no_other_strip_grows_the_explanation(client, request, route, who):
    """The Seis component is shared; Saabunud's and Statistika's strips explain nothing.

    Osakond left this list in the UX package of 2026-10-28: every figure there
    carries its own explanation now (tests/test_seis_figure_help.py).
    """
    person = request.getfixturevalue(who)
    factories.MatterFactory(owner=person, is_open=True)
    client.force_login(person)

    response = client.get(reverse(route))

    assert response.status_code == 200
    body = response.content.decode()
    assert 'class="seis' in body
    assert "data-seishelp" not in body
    assert "seis__info" not in body
    assert "Menetluses olev teema" not in body
