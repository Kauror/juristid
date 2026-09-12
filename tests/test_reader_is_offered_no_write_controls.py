"""A reader is shown the record, and no control the server would refuse them.

ADR 0037 put `update_field` and `save_note` behind the business-write boundary
and made the refusal a 404. The header band and the rail kept offering a
READER the same disclosures a writer gets — `Vastutaja`, `Valdkond` and
`Hetkeseis` as inline editors, and the `Märkmed` box with its autosave. A
reader who picked a name from the owner list fired `data-autosubmit` at a route
that answered 404, which htmx does not swap: the select showed the choice, the
record never took it, and nothing on the page said so. The note box was worse,
because it autosaves into the same 404 and the words are gone on the next load.

The values stay readable. What is withheld is the control that cannot do what
it offers — the same rule the visibility menu, the date editors and every rail
editor already followed with `{% if can_write %}`.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.matters.services import assign_matter, change_stage

pytestmark = pytest.mark.django_db


def _detail(client, matter) -> str:
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


def _editor_routes(matter) -> list[str]:
    return [
        reverse("matters:update_field", kwargs={"pk": matter.pk, "field": field})
        for field in ("owner", "policy_areas", "stage")
    ]


def test_a_reader_is_offered_no_inline_editor_and_no_note_box(
    client, reader, normal_matter, specialist, stage
):
    assign_matter(matter=normal_matter, owner=specialist, actor=specialist)
    change_stage(matter=normal_matter, stage=stage, actor=specialist)
    client.force_login(reader)

    html = _detail(client, normal_matter)

    for route in _editor_routes(normal_matter):
        assert route not in html, f"a reader is offered {route}"
    assert reverse("matters:save_note", kwargs={"pk": normal_matter.pk}) not in html
    assert 'class="railnote"' not in html
    # The facts themselves are still read.
    assert specialist.get_short_name() in html
    assert stage.label_et in html


def test_a_writer_still_gets_the_editors_and_the_note_box(client, specialist, normal_matter, stage):
    client.force_login(specialist)

    html = _detail(client, normal_matter)

    for route in _editor_routes(normal_matter):
        assert route in html, f"a writer lost {route}"
    assert reverse("matters:save_note", kwargs={"pk": normal_matter.pk}) in html


def test_dokumendid_shares_the_header_and_offers_a_reader_no_editor(client, reader, normal_matter):
    client.force_login(reader)

    response = client.get(reverse("matters:matter_documents", kwargs={"pk": normal_matter.pk}))

    assert response.status_code == 200
    html = response.content.decode()
    for route in _editor_routes(normal_matter):
        assert route not in html
